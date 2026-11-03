package capabilities

import (
	"errors"
	"testing"

	"orchara/platform/internal/modelproxy"
)

// The schema validator is the seam every tool-call argument and structured
// response crosses. These cases pin the supported subset and its refusal paths
// with hand-written expectations, not by re-running the validator.
func TestValidateValueSubset(t *testing.T) {
	objectSchema := map[string]any{
		"type":                 "object",
		"additionalProperties": false,
		"required":             []any{"city", "days"},
		"properties": map[string]any{
			"city": map[string]any{"type": "string", "enum": []any{"Berlin", "Paris"}},
			"days": map[string]any{"type": "integer"},
			"tags": map[string]any{
				"type":  "array",
				"items": map[string]any{"type": "string"},
			},
		},
	}

	if problems := ValidateValue(objectSchema, map[string]any{
		"city": "Berlin", "days": float64(2), "tags": []any{"a", "b"},
	}); len(problems) != 0 {
		t.Fatalf("valid object was refused: %+v", problems)
	}

	cases := []struct {
		name   string
		value  any
		field  string
		reason string
	}{
		{"missing required", map[string]any{"city": "Berlin"}, "/days", "is required"},
		{"wrong scalar type", map[string]any{"city": "Berlin", "days": "two"}, "/days", "must be of type integer"},
		{"unknown property", map[string]any{"city": "Berlin", "days": float64(1), "extra": true}, "/extra", "is not an allowed property"},
		{"enum violation", map[string]any{"city": "Rome", "days": float64(1)}, "/city", "must be one of the declared enum values"},
		{"array item type", map[string]any{"city": "Berlin", "days": float64(1), "tags": []any{"ok", 7}}, "/tags/1", "must be of type string"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			problems := ValidateValue(objectSchema, testCase.value)
			if len(problems) == 0 {
				t.Fatalf("%s was accepted", testCase.name)
			}
			found := false
			for _, problem := range problems {
				if problem.Field == testCase.field && problem.Message == testCase.reason {
					found = true
				}
			}
			if !found {
				t.Fatalf("expected %s %q, got %+v", testCase.field, testCase.reason, problems)
			}
		})
	}
}

// A fractional float is not an integer, and a JSON number 1.0 is.
func TestValidateValueIntegerSemantics(t *testing.T) {
	schema := map[string]any{"type": "integer"}
	if problems := ValidateValue(schema, float64(1.5)); len(problems) == 0 {
		t.Fatal("1.5 was accepted as an integer")
	}
	if problems := ValidateValue(schema, float64(1)); len(problems) != 0 {
		t.Fatalf("1 was refused as an integer: %+v", problems)
	}
}

func TestCanonicalParamsDigestOrderIndependent(t *testing.T) {
	left := map[string]any{"b": float64(2), "a": "same", "nested": map[string]any{"y": true, "x": "v"}}
	right := map[string]any{"nested": map[string]any{"x": "v", "y": true}, "a": "same", "b": float64(2)}
	_, leftDigest, err := canonicalParams(left)
	if err != nil {
		t.Fatal(err)
	}
	_, rightDigest, err := canonicalParams(right)
	if err != nil {
		t.Fatal(err)
	}
	if leftDigest != rightDigest {
		t.Fatalf("key order changed the digest: %s != %s", leftDigest, rightDigest)
	}
	_, changedDigest, err := canonicalParams(map[string]any{"a": "different"})
	if err != nil {
		t.Fatal(err)
	}
	if changedDigest == leftDigest {
		t.Fatal("different params produced the same digest")
	}
}

func TestValidateModelParamsVocabulary(t *testing.T) {
	profile := &ModelProfile{ID: "p", Config: map[string]any{"max_output_tokens": float64(128)}}
	tools := map[string]*ToolSpec{
		"11111111-1111-4111-8111-111111111111": {
			ID: "11111111-1111-4111-8111-111111111111", State: "ready", Approved: true,
			InputSchema: map[string]any{"type": "object"},
		},
	}
	params := map[string]any{
		"task":     "agent",
		"messages": []any{map[string]any{"role": "user", "content": "hi"}},
		"tools": []any{map[string]any{
			"name": "lookup", "capability_version": "11111111-1111-4111-8111-111111111111",
		}},
		"response_schema":   map[string]any{"type": "object", "properties": map[string]any{"answer": map[string]any{"type": "string"}}},
		"max_output_tokens": float64(64),
	}
	validated, failure := validateModelParams(params, profile, tools)
	if failure != nil {
		t.Fatalf("valid model params were refused: %v", failure)
	}
	if validated.MaxOutputTokens != 64 || len(validated.Tools) != 1 || validated.Tools[0].Name != "lookup" {
		t.Fatalf("unexpected validation result: %+v", validated)
	}

	refusals := []struct {
		name   string
		params map[string]any
		field  string
	}{
		{"unknown param", map[string]any{"messages": []any{map[string]any{"role": "user", "content": "x"}}, "temperature": float64(1)}, "params/temperature"},
		{"empty messages", map[string]any{"messages": []any{}}, "params/messages"},
		{"bad role", map[string]any{"messages": []any{map[string]any{"role": "root", "content": "x"}}}, "params/messages/0/role"},
		{"token cap", map[string]any{"messages": []any{map[string]any{"role": "user", "content": "x"}}, "max_output_tokens": float64(129)}, "params/max_output_tokens"},
		{"unknown tool", map[string]any{
			"messages": []any{map[string]any{"role": "user", "content": "x"}},
			"tools":    []any{map[string]any{"name": "x", "capability_version": "22222222-2222-4222-8222-222222222222"}},
		}, "params/tools/0/capability_version"},
		{"schema not object", map[string]any{"messages": []any{map[string]any{"role": "user", "content": "x"}}, "response_schema": map[string]any{"type": "string"}}, "params/response_schema/type"},
	}
	for _, testCase := range refusals {
		t.Run(testCase.name, func(t *testing.T) {
			_, failure := validateModelParams(testCase.params, profile, tools)
			if failure == nil {
				t.Fatalf("%s was accepted", testCase.name)
			}
			if failure.Field != testCase.field {
				t.Fatalf("expected field %s, got %s (%v)", testCase.field, failure.Field, failure)
			}
		})
	}
}

func TestValidateProviderResponse(t *testing.T) {
	tool := modelTool{
		Name: "lookup", Version: "11111111-1111-4111-8111-111111111111",
		Spec: &ToolSpec{InputSchema: map[string]any{
			"type": "object", "required": []any{"city"},
			"properties":           map[string]any{"city": map[string]any{"type": "string"}},
			"additionalProperties": false,
		}},
	}
	params := &ModelParams{
		Tools: []modelTool{tool},
		ResponseSchema: map[string]any{
			"type": "object", "required": []any{"answer"},
			"properties": map[string]any{"answer": map[string]any{"type": "string"}},
		},
	}
	valid := map[string]any{
		"text": "ok", "stub": true,
		"tool_calls": []any{map[string]any{
			"id": "call-1", "name": "lookup", "arguments": map[string]any{"city": "Berlin"},
		}},
		"structured": map[string]any{"answer": "42"},
		"usage":      map[string]any{"input_tokens": float64(3), "output_tokens": float64(4)},
	}
	projection, problems := validateProviderResponse(valid, params)
	if len(problems) != 0 {
		t.Fatalf("valid response was refused: %+v", problems)
	}
	if projection.TokensIn != 3 || projection.TokensOut != 4 || len(projection.ToolCalls) != 1 {
		t.Fatalf("unexpected projection: %+v", projection)
	}

	refusals := []struct {
		name     string
		data     map[string]any
		field    string
		noSchema bool
	}{
		{"duplicate ids", map[string]any{"tool_calls": []any{
			map[string]any{"id": "same", "name": "lookup", "arguments": map[string]any{"city": "x"}},
			map[string]any{"id": "same", "name": "lookup", "arguments": map[string]any{"city": "y"}},
		}}, "/tool_calls/1/id", false},
		{"unknown tool", map[string]any{"tool_calls": []any{
			map[string]any{"id": "a", "name": "delete", "arguments": map[string]any{}},
		}}, "/tool_calls/0/name", false},
		{"bad arguments", map[string]any{"tool_calls": []any{
			map[string]any{"id": "a", "name": "lookup", "arguments": map[string]any{"city": "x", "extra": true}},
		}}, "/tool_calls/0/arguments/extra", false},
		{"missing structured", map[string]any{"tool_calls": []any{}}, "/structured", false},
		{"unrequested structured", map[string]any{
			"tool_calls": []any{}, "structured": map[string]any{"answer": "x"},
		}, "/structured", true},
	}
	for _, testCase := range refusals {
		t.Run(testCase.name, func(t *testing.T) {
			active := params
			if testCase.noSchema {
				withoutSchema := *params
				withoutSchema.ResponseSchema = nil
				active = &withoutSchema
			}
			_, problems := validateProviderResponse(testCase.data, active)
			if len(problems) == 0 {
				t.Fatalf("%s was accepted", testCase.name)
			}
			found := false
			for _, problem := range problems {
				if problem.Field == testCase.field {
					found = true
				}
			}
			if !found {
				t.Fatalf("expected %s, got %+v", testCase.field, problems)
			}
		})
	}
}

// Retry classification: only a TransientError retries; a deployment fact
// (not configured / no adapter) never does.
func TestRetryClassification(t *testing.T) {
	if !modelproxy.IsTransient(&modelproxy.TransientError{Reason: "stub"}) {
		t.Fatal("a transient error was not classified as retryable")
	}
	for _, err := range []error{
		modelproxy.ErrNotConfigured,
		modelproxy.ErrAdapterUnavailable,
		modelproxy.ErrCandidateUnavailable,
		errors.New("permission denied"),
	} {
		if modelproxy.IsTransient(err) {
			t.Fatalf("%v was classified as retryable", err)
		}
	}
}

func TestSafeCode(t *testing.T) {
	if code, err := safeCode("tool_timeout"); err != nil || code != "tool_timeout" {
		t.Fatalf("safe code was refused: %q %v", code, err)
	}
	for _, unsafeValue := range []string{"", "Provider Body", "UPPER", "a-b", "x\ny"} {
		if _, err := safeCode(unsafeValue); err == nil {
			t.Fatalf("unsafe code %q was accepted", unsafeValue)
		}
	}
}
