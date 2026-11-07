package modelproxy

// F06/T044: the streaming provider seam.
//
// Streaming is a *temporary* projection, not a second result source. A provider
// may deliver text and tool-call fragments, but the caller still receives the
// same fully formed ProviderResponse it gets from Call; the server seals that
// response as the receipt only after the stream completed. The documented dev
// stub implements Stream deterministically from the canonical params; F06/T122
// adds the real chat-completions SSE adapter, which returns no partial answer on
// a half stream and classifies it as transient (never a fabricated stream).

import (
	"context"
	"encoding/json"
	"fmt"
	"time"
	"unicode/utf8"
)

// ChunkType values on the wire seam.
const (
	StreamDelta     = "delta"
	StreamToolDelta = "tool_delta"
)

// ToolCallDelta is one tool-call fragment emitted by a streaming provider.
// ArgumentsFragment is a JSON fragment, not necessarily a complete object: the
// server buffers fragments by index and only a complete, validated call may
// reach the final projection.
type ToolCallDelta struct {
	Index             int
	ID                string
	Name              string
	ArgumentsFragment string
}

// Chunk is one streamed provider event. A delta carries Text; a tool_delta
// carries one or more ToolCalls.
type Chunk struct {
	Type      string
	Text      string
	ToolCalls []ToolCallDelta
}

// StreamProvider is the optional streaming capability of a Provider. A provider
// that cannot stream honestly does not implement it, and Providers.StreamingFor
// answers ErrAdapterUnavailable instead of pretending.
type StreamProvider interface {
	Provider
	// Stream performs one physical attempt. emit is called for each chunk; a
	// non-nil emit error means the consumer stopped (client gone/cancelled) and
	// the provider must stop too. On failure the returned error classifies the
	// attempt exactly like Call: *TransientError is retryable, anything else is
	// a deployment/validation fact.
	Stream(ctx context.Context, request CallRequest, emit func(Chunk) error) (ProviderResponse, error)
}

// StreamingFor chooses the streaming provider for a candidate. The same profile
// gate as For applies; a real provider has no compiled adapter, so streaming it
// fails closed with ErrAdapterUnavailable.
func (p *Providers) StreamingFor(candidate *Candidate) (StreamProvider, error) {
	provider, err := p.For(candidate)
	if err != nil {
		return nil, err
	}
	streaming, ok := provider.(StreamProvider)
	if !ok {
		return nil, ErrAdapterUnavailable
	}
	return streaming, nil
}

// Stream implements the deterministic dev-stub stream.
//
// The complete answer is computed first (the same bytes Call would produce), then
// projected as chunks. Params:
//
//	stub_stream_chunks (1..64, default 3)      how many text deltas
//	stub_stream_fail_after (0..64)             emit N deltas, then fail transiently
//	stub_stream_fail_attempts (0..3, default 1) fail only attempts <= this number
//	stub_stream_incomplete_tool_call (bool)    emit one partial tool delta instead
//	stub_stream_delay_ms (0..2000)             delay between chunks (live preview)
func (s *stubProvider) Stream(ctx context.Context, request CallRequest, emit func(Chunk) error) (ProviderResponse, error) {
	response, err := s.Call(ctx, request)
	if err != nil {
		return ProviderResponse{}, err
	}
	if ctx == nil {
		ctx = context.Background()
	}
	text, _ := response.Data["text"].(string)
	chunkCount := 3
	if value, ok := integerParam(request.Params["stub_stream_chunks"]); ok {
		if value >= 1 && value <= 64 {
			chunkCount = int(value)
		}
	}
	delay := time.Duration(0)
	if value, ok := integerParam(request.Params["stub_stream_delay_ms"]); ok && value > 0 && value <= 2000 {
		delay = time.Duration(value) * time.Millisecond
	}

	failAfter, fails := int64(-1), false
	if value, ok := integerParam(request.Params["stub_stream_fail_after"]); ok && value >= 0 && value <= 64 {
		failAfter, fails = value, true
	}
	failAttempts := int64(1)
	if value, ok := integerParam(request.Params["stub_stream_fail_attempts"]); ok && value >= 0 && value <= 3 {
		failAttempts = value
	}
	failHere := fails && int64(request.Attempt) <= failAttempts

	chunks := splitStreamText(text, chunkCount)
	for index, piece := range chunks {
		if err := ctx.Err(); err != nil {
			return ProviderResponse{}, err
		}
		if failHere && int64(index) >= failAfter {
			return ProviderResponse{}, &TransientError{
				Reason: fmt.Sprintf("stub stream failed after %d chunk(s)", index),
			}
		}
		if delay > 0 {
			timer := time.NewTimer(delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return ProviderResponse{}, ctx.Err()
			case <-timer.C:
			}
		}
		if err := emit(Chunk{Type: StreamDelta, Text: piece}); err != nil {
			return ProviderResponse{}, err
		}
	}
	if failHere {
		return ProviderResponse{}, &TransientError{
			Reason: fmt.Sprintf("stub stream failed after %d chunk(s)", len(chunks)),
		}
	}

	incomplete, _ := request.Params["stub_stream_incomplete_tool_call"].(bool)
	calls, _ := response.Data["tool_calls"].([]any)
	if incomplete {
		// A provider that dies mid-tool-call: identity is known, arguments are
		// not a complete JSON object. The server must drop this call.
		if len(calls) > 0 {
			if first, ok := calls[0].(map[string]any); ok {
				id, _ := first["id"].(string)
				name, _ := first["name"].(string)
				if id != "" || name != "" {
					if err := emit(Chunk{Type: StreamToolDelta, ToolCalls: []ToolCallDelta{{
						Index: 0, ID: id, Name: name, ArgumentsFragment: `{"city":`,
					}}}); err != nil {
						return ProviderResponse{}, err
					}
				}
			}
		}
		return response, nil
	}
	for index, raw := range calls {
		call, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		id, _ := call["id"].(string)
		name, _ := call["name"].(string)
		arguments, ok := call["arguments"]
		if !ok || id == "" || name == "" {
			continue
		}
		fragment, err := json.Marshal(arguments)
		if err != nil {
			return ProviderResponse{}, fmt.Errorf("encoding a stub tool-call delta: %w", err)
		}
		if err := emit(Chunk{Type: StreamToolDelta, ToolCalls: []ToolCallDelta{{
			Index: index, ID: id, Name: name, ArgumentsFragment: string(fragment),
		}}}); err != nil {
			return ProviderResponse{}, err
		}
	}
	return response, nil
}

// splitStreamText breaks text into at most count non-empty rune chunks. The
// concatenation is exactly text, and the split is deterministic for replay.
func splitStreamText(text string, count int) []string {
	if text == "" || count < 1 {
		return nil
	}
	total := utf8.RuneCountInString(text)
	if count > total {
		count = total
	}
	pieces := make([]string, 0, count)
	runes := []rune(text)
	base, remainder := total/count, total%count
	offset := 0
	for index := 0; index < count; index++ {
		size := base
		if index < remainder {
			size++
		}
		pieces = append(pieces, string(runes[offset:offset+size]))
		offset += size
	}
	return pieces
}
