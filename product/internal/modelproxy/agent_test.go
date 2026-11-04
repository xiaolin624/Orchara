package modelproxy

import (
	"bytes"
	"context"
	"testing"
)

// The F06 stub agent branch must be deterministic from params alone, label
// itself stub, pass the documented hint through and expose a testable transient
// failure. These are properties the capability ledger relies on (a stub call is
// replayable and its attempts are countable), so they are pinned here.
func TestStubAgentDeterministic(t *testing.T) {
	candidate := &Candidate{ID: "model-1", Provider: "stub", ModelID: "stub-agent"}
	params := map[string]any{
		"task":     "agent",
		"messages": []any{map[string]any{"role": "user", "content": "hello"}},
		"response_schema": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"answer": map[string]any{"type": "string"},
				"count":  map[string]any{"type": "integer"},
			},
			"additionalProperties": false,
		},
	}
	provider := &stubProvider{}
	first, err := provider.Call(context.Background(), CallRequest{Candidate: candidate, Params: params, Attempt: 1})
	if err != nil {
		t.Fatal(err)
	}
	second, err := provider.Call(context.Background(), CallRequest{Candidate: candidate, Params: params, Attempt: 1})
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(first.Output, second.Output) {
		t.Fatal("the stub agent answer is not deterministic")
	}
	if stub, _ := first.Data["stub"].(bool); !stub {
		t.Fatalf("stub answer is not labelled stub: %+v", first.Data)
	}
	structured, ok := first.Data["structured"].(map[string]any)
	if !ok {
		t.Fatalf("structured answer missing: %+v", first.Data)
	}
	if _, ok := structured["answer"].(string); !ok {
		t.Fatalf("structured answer does not satisfy the schema: %+v", structured)
	}
	if first.TokensIn <= 0 || first.TokensOut <= 0 {
		t.Fatalf("stub usage must be real: %+v", first)
	}
}

func TestStubAgentToolCallHintPassesThrough(t *testing.T) {
	provider := &stubProvider{}
	response, err := provider.Call(context.Background(), CallRequest{
		Candidate: &Candidate{Provider: "stub", ModelID: "m"},
		Params: map[string]any{
			"messages": []any{map[string]any{"role": "user", "content": "hi"}},
			"stub_tool_calls": []any{map[string]any{
				"id": "call-1", "name": "lookup",
				"arguments": map[string]any{"city": "Berlin"},
			}},
		},
		Attempt: 1,
	})
	if err != nil {
		t.Fatal(err)
	}
	calls, ok := response.Data["tool_calls"].([]any)
	if !ok || len(calls) != 1 {
		t.Fatalf("tool-call hint was not passed through: %+v", response.Data)
	}
}

func TestStubAgentTransientFailureIsCountable(t *testing.T) {
	provider := &stubProvider{}
	request := CallRequest{
		Candidate: &Candidate{Provider: "stub", ModelID: "m"},
		Params: map[string]any{
			"messages":                []any{map[string]any{"role": "user", "content": "hi"}},
			"stub_transient_failures": float64(1),
		},
		Attempt: 1,
	}
	if _, err := provider.Call(context.Background(), request); !IsTransient(err) {
		t.Fatalf("attempt 1 was not transient: %v", err)
	}
	request.Attempt = 2
	if _, err := provider.Call(context.Background(), request); err != nil {
		t.Fatalf("attempt 2 did not succeed: %v", err)
	}
}
