package modelproxy

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"

	"orchara/platform/internal/config"
	"orchara/platform/internal/jcs"
)

// TransientError is a provider failure the server may retry (bounded by the
// caller's attempt ceiling). A provider that returns anything else is a
// deployment/permission/validation fact and must not be retried.
type TransientError struct {
	Reason string
}

func (e *TransientError) Error() string {
	if e.Reason == "" {
		return "the model provider failed transiently"
	}
	return "the model provider failed transiently: " + e.Reason
}

// IsTransient reports whether err is a retryable provider failure.
func IsTransient(err error) bool {
	var transient *TransientError
	return errors.As(err, &transient)
}

// Provider is the seam between the ledger and one model backend. It receives
// only what the server resolved for this job: the candidate row, its params and
// the fixture bytes. It never receives a worker-supplied endpoint, URL or
// credential.
type Provider interface {
	// Name is a stable label used in error messages and reports. It is never a
	// secret or a URL.
	Name() string
	// Available reports whether this provider can dispatch at all right now. It
	// is checked before a ledger attempt is reserved, so an unconfigured
	// provider leaves no row that could later be read as a charge.
	Available(candidate *Candidate) error
	// Call performs one physical attempt.
	Call(ctx context.Context, request CallRequest) (ProviderResponse, error)
}

// CallRequest is one dispatched attempt.
type CallRequest struct {
	Candidate     *Candidate
	Params        map[string]any
	Fixture       []byte
	FixtureSHA256 string
	Attempt       int
}

// ProviderResponse is a provider's answer. Output is the canonical bytes that
// are sealed as the call's result artifact; Data is the sanitized projection
// returned to the handler (it must never carry a credential).
type ProviderResponse struct {
	Data         map[string]any
	Output       []byte
	TokensIn     int64
	TokensOut    int64
	ChargedUnits int64 // 1e-8 currency units
}

// Providers chooses the provider for a candidate.
//
// The stub is only usable under a stub profile AND for a candidate explicitly
// registered with provider "stub"; a real candidate under a stub profile still
// takes the real path and therefore fails closed. That is what keeps stub output
// from being mistaken for a real model.
type Providers struct {
	profile   config.Profile
	endpoint  string
	secretDir string
	lookup    config.Lookup
	stub      *stubProvider
	real      *realProvider
}

// NewProviders builds the provider set. lookup resolves `env:NAME` references;
// nil means the process environment.
func NewProviders(
	profile config.Profile,
	endpoint, secretDir string,
	lookup config.Lookup,
) *Providers {
	if lookup == nil {
		lookup = config.FromEnv()
	}
	return &Providers{
		profile:   profile,
		endpoint:  strings.TrimSpace(endpoint),
		secretDir: secretDir,
		lookup:    lookup,
		stub:      &stubProvider{},
		real:      &realProvider{endpoint: strings.TrimSpace(endpoint), secretDir: secretDir, lookup: lookup},
	}
}

// SetStubServiceDelay installs the F13B/T114 capacity-stub stand-in service
// time. It is deliberately a no-op unless this provider set is the capacity-stub
// deployment: dev-stub keeps its existing (immediate) timing, and a real-model
// deployment can never delay a real provider with a stand-in knob.
//
// The delay is applied inside the stub provider, before it derives its answer;
// it is stand-in service time, not vendor latency.
func (p *Providers) SetStubServiceDelay(delay time.Duration) {
	if p == nil || p.stub == nil || delay <= 0 {
		return
	}
	if p.profile != config.ProfileCapacityStub {
		return
	}
	p.stub.serviceDelay = delay
}

// For returns the provider a candidate is allowed to use.
func (p *Providers) For(candidate *Candidate) (Provider, error) {
	if candidate == nil {
		return nil, ErrCandidateUnavailable
	}
	switch strings.ToLower(strings.TrimSpace(candidate.Provider)) {
	case "stub":
		if p.profile != config.ProfileDevStub && p.profile != config.ProfileCapacityStub {
			// A stub profile is the explicit registration of a fake model; a
			// real-model environment must never route a candidate here.
			return nil, ErrNotConfigured
		}
		return p.stub, nil
	default:
		return p.real, nil
	}
}

// stubProvider is the deterministic development provider.
//
// Its output is derived from the actual fixture bytes it was handed: the
// reported fixture SHA-256 and byte count change when the file changes, and the
// compatibility verdict is driven by an explicit case marker in the fixture. It
// always labels itself `provider: "stub"`, so a caller can never mistake the
// answer for a real model's.
//
// serviceDelay is the F13B/T114 capacity-stub stand-in service time: a fixed
// delay installed only by the capacity-stub deployment (SetStubServiceDelay).
// A dev-stub deployment never sets it, so the existing deterministic timing is
// unchanged there.
type stubProvider struct {
	serviceDelay time.Duration
}

func (s *stubProvider) Name() string { return "stub" }

// Available: the stub is always dispatchable once the profile gate in For has
// accepted it.
func (s *stubProvider) Available(candidate *Candidate) error {
	if candidate == nil {
		return ErrCandidateUnavailable
	}
	return nil
}

func (s *stubProvider) Call(ctx context.Context, request CallRequest) (ProviderResponse, error) {
	if request.Candidate == nil {
		return ProviderResponse{}, ErrCandidateUnavailable
	}
	if s.serviceDelay > 0 {
		// Capacity-stub stand-in service time. It observes cancellation so a
		// cancelled run is not parked for the whole delay.
		timer := time.NewTimer(s.serviceDelay)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return ProviderResponse{}, ctx.Err()
		case <-timer.C:
		}
	}
	// F08/T079: a routing control call is a server-built prompt with
	// `task: "route"`; it is answered deterministically from the prompt bytes.
	// The agent/validation branches below are untouched.
	if task, ok := request.Params["task"].(string); ok && task == "route" {
		return stubRouteResponse(request)
	}
	// F06/T041 agent branch: a run-scoped capability call carries `messages`
	// (and `task: "agent"`); the job-scoped validation call carries a fixture.
	// The agent answer is derived deterministically from the canonical params,
	// never from a vendor and never from the wall clock, so a test can replay it.
	if isAgentCall(request.Params) {
		return stubAgentResponse(request)
	}
	fixture := request.Fixture
	if request.FixtureSHA256 != "" && request.FixtureSHA256 != fixtureSHA256Hex(fixture) {
		return ProviderResponse{}, fmt.Errorf("%w: fixture hash does not match the bytes", ErrNotConfigured)
	}

	checkName := "structured_output"
	passed := true
	reason := ""
	var document map[string]any
	if err := json.Unmarshal(fixture, &document); err != nil {
		passed = false
		reason = "fixture_invalid_json"
	} else {
		if caseName, ok := document["case"].(string); ok {
			switch caseName {
			case "structured_output_invalid":
				checkName, passed, reason = "structured_output", false, "structured_output"
			case "tool_call_invalid":
				checkName, passed, reason = "tool_call", false, "tool_call"
			}
		}
		if requested, ok := document["check"].(string); ok && requested != "" && requested != "structured_output" && requested != "tool_call" {
			// Unknown requested check: report it honestly instead of silently
			// passing a check the fixture asked for.
			checkName, passed, reason = requested, false, "unsupported_check"
		}
	}
	fixtureHash := fixtureSHA256Hex(fixture)
	data := map[string]any{
		"provider":       "stub",
		"stub":           true,
		"model_id":       request.Candidate.ModelID,
		"fixture_sha256": fixtureHash,
		"fixture_bytes":  len(fixture),
		"check": map[string]any{
			"name":   checkName,
			"passed": passed,
			"reason": reason,
		},
	}
	output, err := canonicalJSON(data)
	if err != nil {
		return ProviderResponse{}, fmt.Errorf("canonicalising the stub answer: %w", err)
	}
	tokensIn := int64((len(fixture) + 3) / 4)
	tokensOut := int64((len(output) + 3) / 4)
	return ProviderResponse{
		Data:         data,
		Output:       output,
		TokensIn:     tokensIn,
		TokensOut:    tokensOut,
		ChargedUnits: (tokensIn + tokensOut) * reservedUnitsPerInputToken,
	}, nil
}

// canonicalJSON renders a server-built value as RFC 8785 bytes. It marshals
// first because jcs.Canonicalize only accepts already-JSON-shaped values
// (float64, not int), and the bytes must be exactly what is hashed and sealed.
func canonicalJSON(value any) ([]byte, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	return jcs.CanonicalizeBytes(raw)
}

// isAgentCall distinguishes a run-scoped agent capability call from the
// job-scoped validation call: the agent call always carries messages.
func isAgentCall(params map[string]any) bool {
	if params == nil {
		return false
	}
	if task, ok := params["task"].(string); ok && task == "agent" {
		return true
	}
	_, hasMessages := params["messages"]
	return hasMessages
}

// stubAgentResponse is the deterministic F06 agent stub: same params, same
// answer; an explicit stub label; the documented stub_tool_calls hint passed
// through for the capability validator to accept or refuse; stub_transient_failures
// makes the retry/ledger path testable without a vendor.
func stubAgentResponse(request CallRequest) (ProviderResponse, error) {
	// T045: the model-id convention makes an availability fault reproducible
	// without a vendor. A primary registered with this model id always answers
	// with the same typed failure, so the bounded retry and compatible-fallback
	// path is exercised end to end; a compatible fallback registered under a
	// normal stub model id then succeeds.
	if fault := stubModelFault(request.Candidate.ModelID); fault != nil {
		return ProviderResponse{}, fault
	}
	if failures, ok := integerParam(request.Params["stub_transient_failures"]); ok &&
		request.Attempt <= int(failures) {
		return ProviderResponse{}, &TransientError{
			Reason: fmt.Sprintf("stub transient failure %d", request.Attempt),
		}
	}
	rawParams, err := json.Marshal(request.Params)
	if err != nil {
		return ProviderResponse{}, fmt.Errorf("encoding the stub agent params: %w", err)
	}
	canonical, err := jcs.CanonicalizeBytes(rawParams)
	if err != nil {
		return ProviderResponse{}, fmt.Errorf("canonicalising the stub agent params: %w", err)
	}
	sum := sha256.Sum256(canonical)
	digest := hex.EncodeToString(sum[:])
	text := "stub-agent:" + digest[:16]
	data := map[string]any{
		"provider":   "stub",
		"stub":       true,
		"model_id":   request.Candidate.ModelID,
		"text":       text,
		"tool_calls": []any{},
	}
	if hint, ok := request.Params["stub_tool_calls"]; ok {
		data["tool_calls"] = hint
	} else if calls := stubAgentAutoToolCalls(request.Params); calls != nil {
		// T043: a controlled agent whose node declares a tool must be able to
		// exercise the real grant/sandbox/result path end to end with the dev
		// stub. Absent an explicit hint, the stub plays a model that calls the
		// first declared tool on its first turn and answers once a tool result
		// message exists.
		data["tool_calls"] = calls
	}
	if schema, ok := request.Params["response_schema"].(map[string]any); ok {
		// F04A/T049: the sales template's explain node receives the python
		// node's validated summary in its user message. When the response schema
		// asks for a report, the deterministic stub copies those verified
		// numbers verbatim — it never recomputes or invents an amount — and the
		// totals object is the same summary. Amount truth stays with the python
		// tool; the model only explains.
		if structured := verifiedSalesReport(schema, request.Params); structured != nil {
			data["structured"] = structured
		} else if structured, ok := stubSchemaValue(schema, digest, 0).(map[string]any); ok {
			data["structured"] = structured
		}
	}
	tokensIn := int64((len(canonical) + 3) / 4)
	tokensOut := int64((len(text) + 3) / 4)
	if strings.HasPrefix(strings.ToLower(strings.TrimSpace(request.Candidate.ModelID)), "stub-usage-missing") {
		// F06/T046: a provider answer without a usage object. The capability
		// service keeps the conservative reservation (usage_missing) until a
		// maintenance reconciliation settles it; the stub is honest about not
		// knowing the cost instead of inventing one.
		output, err := canonicalJSON(data)
		if err != nil {
			return ProviderResponse{}, fmt.Errorf("canonicalising the stub agent answer: %w", err)
		}
		return ProviderResponse{Data: data, Output: output}, nil
	}
	data["usage"] = map[string]any{
		"input_tokens":  tokensIn,
		"output_tokens": tokensOut,
	}
	output, err := canonicalJSON(data)
	if err != nil {
		return ProviderResponse{}, fmt.Errorf("canonicalising the stub agent answer: %w", err)
	}
	return ProviderResponse{
		Data:         data,
		Output:       output,
		TokensIn:     tokensIn,
		TokensOut:    tokensOut,
		ChargedUnits: (tokensIn + tokensOut) * reservedUnitsPerInputToken,
	}, nil
}

// verifiedSalesReport builds the deterministic explain answer for the F04A/T049
// sales template.
//
// It is used only when the response schema declares a `report` property and the
// agent's user message contains the python node's validated summary object. The
// report sentence copies total/regions/order_count exactly; `totals` is the same
// object. A schema without `report` (T043's `{text}`) is unaffected, and a
// message without a summary falls through to the generic deterministic stub.
func verifiedSalesReport(schema map[string]any, params map[string]any) map[string]any {
	properties, ok := schema["properties"].(map[string]any)
	if !ok {
		return nil
	}
	if _, ok := properties["report"]; !ok {
		return nil
	}
	summary := verifiedSummaryFromMessages(params)
	if summary == nil {
		return nil
	}
	total, _ := summary["total"].(string)
	regions, _ := summary["regions"].(map[string]any)
	names := make([]string, 0, len(regions))
	for name := range regions {
		names = append(names, name)
	}
	sort.Strings(names)

	var report strings.Builder
	report.WriteString("销售报告：总额 ")
	report.WriteString(total)
	report.WriteString("，订单数 ")
	report.WriteString(strconv.Itoa(int(summary["order_count"].(float64))))
	report.WriteString("，区域：")
	for index, name := range names {
		if index > 0 {
			report.WriteString("，")
		}
		report.WriteString(name)
		report.WriteString(" ")
		report.WriteString(regions[name].(string))
	}
	report.WriteString("。以上数字全部来自已验证统计，报告未重新计算任何金额。")
	return map[string]any{"report": report.String(), "totals": summary}
}

// verifiedSummaryFromMessages finds the validated summary the executor placed in
// the last user message's inputs JSON. It scans messages from newest to oldest so
// a later tool turn can never shadow the original inputs, and it accepts only an
// object with the tool's exact total/regions/order_count shape.
func verifiedSummaryFromMessages(params map[string]any) map[string]any {
	messages, _ := params["messages"].([]any)
	for index := len(messages) - 1; index >= 0; index-- {
		message, ok := messages[index].(map[string]any)
		if !ok || message["role"] != "user" {
			continue
		}
		content, ok := message["content"].(string)
		if !ok {
			continue
		}
		var value any
		if json.Unmarshal([]byte(content), &value) != nil {
			continue
		}
		if summary := findSummaryObject(value); summary != nil {
			return summary
		}
	}
	return nil
}

// findSummaryObject walks a decoded inputs document (nested objects/arrays) for
// the validated summary shape.
func findSummaryObject(value any) map[string]any {
	switch typed := value.(type) {
	case map[string]any:
		if summary := summaryOf(typed); summary != nil {
			return summary
		}
		for _, child := range typed {
			if summary := findSummaryObject(child); summary != nil {
				return summary
			}
		}
	case []any:
		for _, child := range typed {
			if summary := findSummaryObject(child); summary != nil {
				return summary
			}
		}
	}
	return nil
}

// summaryOf reports whether object is the preset tool's summary document.
func summaryOf(object map[string]any) map[string]any {
	total, ok := object["total"].(string)
	if !ok || strings.TrimSpace(total) == "" {
		return nil
	}
	regions, ok := object["regions"].(map[string]any)
	if !ok {
		return nil
	}
	for name, value := range regions {
		amount, ok := value.(string)
		if !ok || strings.TrimSpace(name) == "" || strings.TrimSpace(amount) == "" {
			return nil
		}
	}
	count, ok := object["order_count"].(float64)
	if !ok || count < 0 || count != math.Trunc(count) {
		return nil
	}
	return object
}

// stubModelFault maps a registered stub model id to a deterministic failure.
// T045 uses this (instead of a params hint) so the primary and the fallback of
// one call can answer differently: the fault belongs to the candidate the
// server resolved, not to worker-supplied params. A normal model id (for
// example "stub-model") is unaffected.
//
//   - stub-unavailable*      bounded-transient availability fault
//   - stub-rate-limited*     bounded-transient rate-limit fault
//   - stub-adapter-down*     deployment availability fault (no adapter)
//   - stub-permission-denied* terminal provider refusal (never retried/fallen back)
//   - stub-schema-error*     terminal schema refusal (never retried/fallen back)
//   - stub-usage-missing*    success without a usage object (T046 reconciliation path)
func stubModelFault(modelID string) error {
	normalized := strings.ToLower(strings.TrimSpace(modelID))
	switch {
	case strings.HasPrefix(normalized, "stub-adapter-down"):
		return ErrAdapterUnavailable
	case strings.HasPrefix(normalized, "stub-rate-limited"):
		return &TransientError{Reason: "rate limited (429) by the stub provider"}
	case strings.HasPrefix(normalized, "stub-unavailable"):
		return &TransientError{Reason: "the stub provider is unavailable"}
	case strings.HasPrefix(normalized, "stub-permission-denied"):
		return fmt.Errorf("the stub provider refused the call: permission denied")
	case strings.HasPrefix(normalized, "stub-schema-error"):
		return fmt.Errorf("the stub provider rejected the response schema")
	default:
		return nil
	}
}

// stubAgentAutoToolCalls is the deterministic "model calls the tool first"
// script: when the call declares tools and the conversation has no tool result
// yet, emit one call for the first declared tool. The empty argument object is
// validated by the capability server against the real tool input_schema, so a
// tool that needs arguments refuses the call exactly like a real model mistake.
func stubAgentAutoToolCalls(params map[string]any) []any {
	tools, _ := params["tools"].([]any)
	if len(tools) == 0 {
		return nil
	}
	if messages, ok := params["messages"].([]any); ok {
		for _, raw := range messages {
			message, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			if role, _ := message["role"].(string); role == "tool" {
				return nil
			}
		}
	}
	first, _ := tools[0].(map[string]any)
	name, _ := first["name"].(string)
	if name == "" {
		return nil
	}
	return []any{map[string]any{
		"id": "stub-call-1", "name": name, "arguments": map[string]any{},
	}}
}

// stubSchemaValue builds a deterministic value that satisfies the restricted
// schema subset the capability validator understands. It only ever fills
// declared properties, so an additionalProperties:false schema still passes.
func stubSchemaValue(schema map[string]any, seed string, depth int) any {
	if depth > 16 || schema == nil {
		return nil
	}
	if enum, ok := schema["enum"].([]any); ok && len(enum) > 0 {
		return enum[0]
	}
	kind, _ := schema["type"].(string)
	switch kind {
	case "object":
		object := map[string]any{}
		if properties, ok := schema["properties"].(map[string]any); ok {
			for name, raw := range properties {
				child, ok := raw.(map[string]any)
				if !ok {
					continue
				}
				object[name] = stubSchemaValue(child, seed, depth+1)
			}
		}
		return object
	case "array":
		item, _ := schema["items"].(map[string]any)
		value := stubSchemaValue(item, seed, depth+1)
		if value == nil {
			return []any{}
		}
		return []any{value}
	case "string":
		if len(seed) > 16 {
			seed = seed[:16]
		}
		return seed
	case "number":
		return float64(1)
	case "integer":
		return float64(1)
	case "boolean":
		return true
	default:
		return nil
	}
}

func integerParam(raw any) (int64, bool) {
	switch value := raw.(type) {
	case float64:
		return int64(value), math.Trunc(value) == value
	case int:
		return int64(value), true
	case int64:
		return value, true
	case json.Number:
		integer, err := value.Int64()
		return integer, err == nil
	default:
		return 0, false
	}
}
