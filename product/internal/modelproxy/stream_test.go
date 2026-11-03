package modelproxy

// F06/T044: the streaming provider seam. These tests use the documented dev stub
// only; no vendor is contacted and no real answer is fabricated.

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"orchara/platform/internal/config"
)

func streamParams(extra map[string]any) map[string]any {
	params := map[string]any{
		"task":     "agent",
		"messages": []any{map[string]any{"role": "user", "content": "stream the sales file"}},
	}
	for key, value := range extra {
		params[key] = value
	}
	return params
}

func collectStream(t *testing.T, provider StreamProvider, request CallRequest) ([]Chunk, ProviderResponse, error) {
	t.Helper()
	var chunks []Chunk
	response, err := provider.Stream(context.Background(), request, func(chunk Chunk) error {
		chunks = append(chunks, chunk)
		return nil
	})
	return chunks, response, err
}

func TestStubStreamIsDeterministicAndChunked(t *testing.T) {
	providers := stubProviderFor(t, config.ProfileDevStub)
	provider, err := providers.StreamingFor(testCandidate("stub"))
	if err != nil {
		t.Fatalf("StreamingFor: %v", err)
	}
	request := CallRequest{
		Candidate: testCandidate("stub"),
		Params:    streamParams(map[string]any{"stub_stream_chunks": float64(3)}),
		Attempt:   1,
	}
	first, response, err := collectStream(t, provider, request)
	if err != nil {
		t.Fatalf("first stream: %v", err)
	}
	second, _, err := collectStream(t, provider, request)
	if err != nil {
		t.Fatalf("second stream: %v", err)
	}
	if len(first) != 3 {
		t.Fatalf("want 3 delta chunks, got %d (%+v)", len(first), first)
	}
	var text strings.Builder
	for _, chunk := range first {
		if chunk.Type != "delta" {
			t.Fatalf("want text deltas, got %+v", chunk)
		}
		text.WriteString(chunk.Text)
	}
	if !strings.HasPrefix(text.String(), "stub-agent:") {
		t.Fatalf("stub stream text is not labelled: %q", text.String())
	}
	if response.Data["text"] != text.String() {
		t.Fatalf("final text %q != concatenated deltas %q", response.Data["text"], text.String())
	}
	if len(second) != 3 || second[0].Text != first[0].Text {
		t.Fatalf("stub stream is not deterministic: %+v vs %+v", first, second)
	}
}

func TestStubStreamFailsAfterNChunksOnTheFirstAttemptOnly(t *testing.T) {
	providers := stubProviderFor(t, config.ProfileDevStub)
	provider, err := providers.StreamingFor(testCandidate("stub"))
	if err != nil {
		t.Fatalf("StreamingFor: %v", err)
	}
	request := CallRequest{
		Candidate: testCandidate("stub"),
		Params: streamParams(map[string]any{
			"stub_stream_chunks":     float64(3),
			"stub_stream_fail_after": float64(1),
		}),
		Attempt: 1,
	}
	chunks, _, err := collectStream(t, provider, request)
	if err == nil || !IsTransient(err) {
		t.Fatalf("attempt 1 should fail transiently, got err=%v chunks=%+v", err, chunks)
	}
	if len(chunks) != 1 || chunks[0].Type != "delta" {
		t.Fatalf("want exactly one delta before the failure, got %+v", chunks)
	}
	request.Attempt = 2
	chunks, response, err := collectStream(t, provider, request)
	if err != nil {
		t.Fatalf("attempt 2 should stream completely: %v", err)
	}
	if len(chunks) != 3 {
		t.Fatalf("want 3 chunks on the retry, got %d", len(chunks))
	}
	if response.Data["text"] == "" {
		t.Fatalf("retry did not produce complete content")
	}
}

func TestStubStreamEmitsCompleteToolCallDeltas(t *testing.T) {
	providers := stubProviderFor(t, config.ProfileDevStub)
	provider, err := providers.StreamingFor(testCandidate("stub"))
	if err != nil {
		t.Fatalf("StreamingFor: %v", err)
	}
	toolVersion := "33333333-3333-4333-8333-333333333333"
	request := CallRequest{
		Candidate: testCandidate("stub"),
		Params: streamParams(map[string]any{
			"stub_stream_chunks": float64(2),
			"tools": []any{map[string]any{
				"name": "lookup", "capability_version": toolVersion,
			}},
		}),
		Attempt: 1,
	}
	chunks, response, err := collectStream(t, provider, request)
	if err != nil {
		t.Fatalf("stream: %v", err)
	}
	var deltas []Chunk
	for _, chunk := range chunks {
		if chunk.Type == "tool_delta" {
			deltas = append(deltas, chunk)
		}
	}
	if len(deltas) != 1 || len(deltas[0].ToolCalls) != 1 {
		t.Fatalf("want one complete tool-call delta, got %+v", chunks)
	}
	call := deltas[0].ToolCalls[0]
	if call.ID == "" || call.Name != "lookup" {
		t.Fatalf("tool-call delta lacks identity: %+v", call)
	}
	var arguments map[string]any
	if err := json.Unmarshal([]byte(call.ArgumentsFragment), &arguments); err != nil {
		t.Fatalf("tool-call delta arguments are not a complete JSON object: %q", call.ArgumentsFragment)
	}
	if calls, ok := response.Data["tool_calls"].([]any); !ok || len(calls) != 1 {
		t.Fatalf("stub final answer lost the tool call hint: %+v", response.Data)
	}
}

func TestStubStreamCanEmitAnIncompleteToolCall(t *testing.T) {
	providers := stubProviderFor(t, config.ProfileDevStub)
	provider, err := providers.StreamingFor(testCandidate("stub"))
	if err != nil {
		t.Fatalf("StreamingFor: %v", err)
	}
	request := CallRequest{
		Candidate: testCandidate("stub"),
		Params: streamParams(map[string]any{
			"stub_stream_chunks":               float64(2),
			"stub_stream_incomplete_tool_call": true,
			"tools": []any{map[string]any{
				"name": "lookup", "capability_version": "33333333-3333-4333-8333-333333333333",
			}},
		}),
		Attempt: 1,
	}
	chunks, _, err := collectStream(t, provider, request)
	if err != nil {
		t.Fatalf("stream: %v", err)
	}
	var deltas []Chunk
	for _, chunk := range chunks {
		if chunk.Type == "tool_delta" {
			deltas = append(deltas, chunk)
		}
	}
	if len(deltas) != 1 || len(deltas[0].ToolCalls) != 1 {
		t.Fatalf("want one partial tool-call delta, got %+v", chunks)
	}
	call := deltas[0].ToolCalls[0]
	if call.Name == "" || call.ID == "" {
		t.Fatalf("the partial delta needs identity so the UI can show it as pending: %+v", call)
	}
	var object map[string]any
	if err := json.Unmarshal([]byte(call.ArgumentsFragment), &object); err == nil {
		t.Fatalf("the incomplete delta must not be a complete argument object: %+v", call)
	}
}

func TestRealStreamingIsAdapterUnavailable(t *testing.T) {
	providers := stubProviderFor(t, config.ProfileDevStub)
	if _, err := providers.StreamingFor(testCandidate("openai")); !errors.Is(err, ErrAdapterUnavailable) {
		t.Fatalf("want ErrAdapterUnavailable for a real provider, got %v", err)
	}
}

func TestStubStreamIsNotAvailableOnARealProfile(t *testing.T) {
	providers := stubProviderFor(t, config.ProfileRealModel)
	if _, err := providers.StreamingFor(testCandidate("stub")); err == nil {
		t.Fatalf("a stub candidate must not stream under a real-model profile")
	}
}
