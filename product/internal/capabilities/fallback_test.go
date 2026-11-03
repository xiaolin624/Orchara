package capabilities

import (
	"errors"
	"testing"

	"orchara/platform/internal/modelproxy"
)

func profileWithConfig(config map[string]any) *ModelProfile {
	return &ModelProfile{ID: "p", Config: config}
}

func chatConfig(overrides map[string]any) map[string]any {
	config := map[string]any{
		"kind":              "generation",
		"protocol":          "chat_compatible",
		"context_window":    float64(128000),
		"max_output_tokens": float64(4096),
	}
	for key, value := range overrides {
		config[key] = value
	}
	return config
}

func testParams(tools int, schema bool) *ModelParams {
	params := &ModelParams{
		Messages:        []map[string]any{{"role": "user", "content": "Summarize the sales file."}},
		MaxOutputTokens: 1024,
	}
	for index := 0; index < tools; index++ {
		params.Tools = append(params.Tools, modelTool{Name: "tool", Version: "v"})
	}
	if schema {
		params.ResponseSchema = map[string]any{"type": "object"}
	}
	return params
}

// The compatibility matrix is the T045 acceptance criterion "备用在用途/工具/
// Schema/预算兼容时才启用": every gate below must independently refuse.
func TestFallbackCompatibilityMatrix(t *testing.T) {
	primary := profileWithConfig(chatConfig(nil))
	cases := []struct {
		name     string
		fallback map[string]any
		params   *ModelParams
		want     string
	}{
		{"identical generation chat profile", chatConfig(nil), testParams(0, false), ""},
		{"kind mismatch", chatConfig(map[string]any{"kind": "embedding"}), testParams(0, false), CompatibilityKind},
		{"protocol mismatch", chatConfig(map[string]any{"protocol": "embedding_compatible"}), testParams(0, false), CompatibilityProtocol},
		{"undeclared protocol", map[string]any{"kind": "generation"}, testParams(0, false), CompatibilityProtocol},
		{"context window too small", chatConfig(map[string]any{"context_window": float64(16)}), testParams(0, false), CompatibilityContextWindow},
		{"missing context window", map[string]any{"kind": "generation", "protocol": "chat_compatible", "max_output_tokens": float64(4096)}, testParams(0, false), CompatibilityContextWindow},
		{"output cap too small", chatConfig(map[string]any{"max_output_tokens": float64(256)}), testParams(0, false), CompatibilityMaxOutput},
		{"missing output cap", map[string]any{"kind": "generation", "protocol": "chat_compatible", "context_window": float64(128000)}, testParams(0, false), CompatibilityMaxOutput},
		{"tool call on a profile that cannot call tools", chatConfig(map[string]any{"tools": false}), testParams(1, false), CompatibilityTools},
		{"structured output on a profile without it", chatConfig(map[string]any{"structured_output": false}), testParams(0, true), CompatibilityResponseSchema},
		{"tools allowed by default", chatConfig(nil), testParams(3, true), ""},
	}
	for _, entry := range cases {
		t.Run(entry.name, func(t *testing.T) {
			fallback := profileWithConfig(entry.fallback)
			if got := checkFallbackCompatibility(primary, fallback, entry.params); got != entry.want {
				t.Fatalf("compatibility = %q, want %q", got, entry.want)
			}
		})
	}
}

func TestFallbackCompatibilityRequiresGenerationPrimary(t *testing.T) {
	primary := profileWithConfig(chatConfig(map[string]any{"kind": "routing"}))
	fallback := profileWithConfig(chatConfig(map[string]any{"kind": "routing"}))
	if got := checkFallbackCompatibility(primary, fallback, testParams(0, false)); got != CompatibilityKind {
		t.Fatalf("a non-generation primary must not fall back, got %q", got)
	}
}

// Only availability faults may switch profiles; permission/schema/provider
// errors must stay terminal.
func TestFallbackReasonClassification(t *testing.T) {
	cases := []struct {
		name    string
		err     error
		want    string
		allowed bool
	}{
		{"generic transient", &modelproxy.TransientError{}, FallbackReasonAvailability, true},
		{"rate limited", &modelproxy.TransientError{Reason: "rate limited (429)"}, FallbackReasonRateLimited, true},
		{"quota", &modelproxy.TransientError{Reason: "monthly quota exhausted"}, FallbackReasonRateLimited, true},
		{"adapter unavailable", modelproxy.ErrAdapterUnavailable, FallbackReasonAdapterUnavailable, true},
		{"not configured", modelproxy.ErrNotConfigured, FallbackReasonAdapterUnavailable, true},
		{"permission", errors.New("permission denied"), "", false},
		{"schema", errors.New("response schema rejected"), "", false},
		{"tool arguments", errors.New("tool arguments invalid"), "", false},
	}
	for _, entry := range cases {
		t.Run(entry.name, func(t *testing.T) {
			reason, allowed := fallbackReasonFor(entry.err)
			if allowed != entry.allowed || reason != entry.want {
				t.Fatalf("(%q,%v), want (%q,%v)", reason, allowed, entry.want, entry.allowed)
			}
		})
	}
}

func TestFallbackProfileIDs(t *testing.T) {
	valid := "22222222-2222-4222-8222-222222222222"
	profile := profileWithConfig(map[string]any{
		"fallback_profile_version_ids": []any{valid, "not-a-uuid", 7},
	})
	ids := fallbackProfileIDs(profile)
	if len(ids) != 1 || ids[0] != valid {
		t.Fatalf("fallback ids = %v", ids)
	}
	if got := fallbackProfileIDs(profileWithConfig(map[string]any{})); got != nil {
		t.Fatalf("absent list = %v", got)
	}
	if got := fallbackProfileIDs(nil); got != nil {
		t.Fatalf("nil profile = %v", got)
	}
}

func TestRequiredTokensIncludesRequestedOutput(t *testing.T) {
	params := testParams(0, false)
	required := params.requiredTokens()
	if required < params.MaxOutputTokens {
		t.Fatalf("required tokens %d must include the %d requested output tokens", required, params.MaxOutputTokens)
	}
	if required == params.MaxOutputTokens {
		t.Fatal("required tokens must also count the input messages")
	}
}
