package capabilities

// F06/T045: compatible fallback after an availability fault.
//
// The rules this file implements are fixed by the T045 brief and are deliberately
// small, closed and unit-testable:
//
//   - The only fallback list is `ModelConfig.fallback_profile_version_ids` of the
//     primary profile version (the contract field modelregistry already
//     validates). It is read from the database row, never from worker params.
//   - A fallback is used only when it is *compatible*: same kind and protocol
//     (generation chat-compatible), enough context window for the request's
//     input plus requested output, enough `max_output_tokens`, able to run the
//     declared tools and accept the declared response schema, passed and
//     unrevoked, frozen into the run's manifest, and its conservative
//     reservation still fits the run budget. "Configured" is never enough.
//   - Only availability faults fall back. The closed reason vocabulary is
//     availability / rate_limited / adapter_unavailable. A permission,
//     authorisation, syntax, schema-validation or tool-argument error never
//     retries and never falls back.
//   - At most one physical attempt is spent on a fallback, after the primary's
//     bounded retries, so one operation never dispatches more than
//     maxPhysicalAttempts+fallbackPhysicalAttempts times.
//
// A profile may declare two optional capability flags in its stored config
// (`tools`, `structured_output`). They default to true for a chat-compatible
// generation profile: the contract's ModelConfig has no tool/schema field, so
// absence must not silently forbid the normal case. An explicit `false` is
// honoured and makes the fallback incompatible for a call that needs it — the
// fail-closed direction for the only information the platform actually has.

import (
	"encoding/json"
	"errors"
	"strings"

	"orchara/platform/internal/modelproxy"
	"orchara/platform/internal/uuid"
)

// Closed fallback reason vocabulary. It is stored in
// platform.operations.fallback_reason (migration 0027) and shown in the owner
// ledger; no provider text ever reaches it.
const (
	FallbackReasonAvailability       = "availability"
	FallbackReasonRateLimited        = "rate_limited"
	FallbackReasonAdapterUnavailable = "adapter_unavailable"
)

// fallbackPhysicalAttempts bounds the fallback's own physical attempts. One
// attempt is enough to answer "the compatible fallback worked or it did not"
// without a second retry loop the worker cannot see.
const fallbackPhysicalAttempts = 1

// fallbackFact is the persisted fact that this operation switched profiles.
type fallbackFact struct {
	ProfileVersionID string
	Reason           string
}

// fallbackReasonFor classifies a provider failure. It returns false for every
// failure that must not fall back (permission, syntax, schema, tool arguments,
// policy, unknown provider errors).
func fallbackReasonFor(err error) (string, bool) {
	var transient *modelproxy.TransientError
	if errors.As(err, &transient) {
		reason := strings.ToLower(transient.Reason)
		if strings.Contains(reason, "rate limit") || strings.Contains(reason, "rate_limit") ||
			strings.Contains(reason, "429") || strings.Contains(reason, "quota") {
			return FallbackReasonRateLimited, true
		}
		return FallbackReasonAvailability, true
	}
	if errors.Is(err, modelproxy.ErrAdapterUnavailable) ||
		errors.Is(err, modelproxy.ErrNotConfigured) {
		return FallbackReasonAdapterUnavailable, true
	}
	return "", false
}

// fallbackProfileIDs returns the declared fallback list of one profile config.
func fallbackProfileIDs(profile *ModelProfile) []string {
	if profile == nil || profile.Config == nil {
		return nil
	}
	raw, present := profile.Config["fallback_profile_version_ids"]
	if !present {
		return nil
	}
	items, ok := raw.([]any)
	if !ok {
		return nil
	}
	ids := make([]string, 0, len(items))
	for _, item := range items {
		value, ok := item.(string)
		if !ok {
			continue
		}
		value = strings.ToLower(strings.TrimSpace(value))
		if uuid.Valid(value) {
			ids = append(ids, value)
		}
	}
	return ids
}

// Compatibility rejection codes are internal (they explain a refusal in tests
// and code review); the owner ledger only ever shows the switch reason, never
// why another profile was skipped.
const (
	CompatibilityKind           = "kind"
	CompatibilityProtocol       = "protocol"
	CompatibilityContextWindow  = "context_window"
	CompatibilityMaxOutput      = "max_output_tokens"
	CompatibilityTools          = "tools"
	CompatibilityResponseSchema = "response_schema"
)

// checkFallbackCompatibility returns "" when fallback may serve this call, or
// the rejection code. It is a pure function over the two stored profiles and
// the validated params: no database, no provider, no clock.
func checkFallbackCompatibility(primary, fallback *ModelProfile, params *ModelParams) string {
	if primary == nil || fallback == nil || params == nil {
		return CompatibilityKind
	}
	primaryKind, primaryKindOK := profileStringConfig(primary, "kind")
	fallbackKind, fallbackKindOK := profileStringConfig(fallback, "kind")
	if !primaryKindOK || !fallbackKindOK || primaryKind != fallbackKind {
		return CompatibilityKind
	}
	primaryProtocol, primaryProtocolOK := profileStringConfig(primary, "protocol")
	fallbackProtocol, fallbackProtocolOK := profileStringConfig(fallback, "protocol")
	if !primaryProtocolOK || !fallbackProtocolOK ||
		primaryProtocol != "chat_compatible" || fallbackProtocol != primaryProtocol {
		return CompatibilityProtocol
	}
	if primaryKind != "generation" {
		// A routing/embedding/judge profile has no chat capability this batch
		// could fall back from or to.
		return CompatibilityKind
	}
	window, windowOK := profileIntConfig(fallback, "context_window")
	if !windowOK || window < params.requiredTokens() {
		return CompatibilityContextWindow
	}
	maxOutput, maxOutputOK := profileIntConfig(fallback, "max_output_tokens")
	if !maxOutputOK || maxOutput < params.MaxOutputTokens {
		return CompatibilityMaxOutput
	}
	if len(params.Tools) > 0 && profileDeclaresFalse(fallback, "tools") {
		return CompatibilityTools
	}
	if params.ResponseSchema != nil && profileDeclaresFalse(fallback, "structured_output") {
		return CompatibilityResponseSchema
	}
	return ""
}

// requiredTokens is the conservative input+output window a fallback must cover.
// Input is estimated from the canonical message bytes (one token per four
// bytes, the same conservative rule the pre-dispatch reservation uses).
func (params *ModelParams) requiredTokens() int64 {
	if params == nil {
		return 0
	}
	raw, err := json.Marshal(params.Messages)
	if err != nil {
		return params.MaxOutputTokens
	}
	return int64((len(raw)+3)/4) + params.MaxOutputTokens
}

func profileStringConfig(profile *ModelProfile, key string) (string, bool) {
	if profile == nil || profile.Config == nil {
		return "", false
	}
	value, ok := profile.Config[key].(string)
	if !ok {
		return "", false
	}
	value = strings.TrimSpace(value)
	return value, value != ""
}

func profileIntConfig(profile *ModelProfile, key string) (int64, bool) {
	if profile == nil || profile.Config == nil {
		return 0, false
	}
	value, present := profile.Config[key]
	if !present {
		return 0, false
	}
	return jsonInteger(value)
}

func profileDeclaresFalse(profile *ModelProfile, key string) bool {
	if profile == nil || profile.Config == nil {
		return false
	}
	value, present := profile.Config[key].(bool)
	return present && !value
}
