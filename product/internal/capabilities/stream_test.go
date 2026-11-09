package capabilities

// F06/T044: unit tests for the generation buffer. They are deliberately pure:
// the real ledger/stream behaviour is covered by the real-stack pytest suite.
// The properties asserted here are the acceptance ones - no mixing between
// generations, and an incomplete tool call never becomes executable content.
// C2.02 adds the content-half contract the shared attempt driver relies on: the
// frames carry the physical attempt's generation id, and a stopped consumer is a
// cancellation rather than a retryable provider failure.

import (
	"context"
	"errors"
	"testing"

	"orchara/platform/internal/modelproxy"
)

func TestGenerationKeepsTextAndToolCallsTogether(t *testing.T) {
	generation := newStreamGeneration("op-1", "gen-1")
	generation.addText("hello ")
	generation.addText("world")
	generation.addToolDelta(modelproxy.ToolCallDelta{
		Index: 0, ID: "call-1", Name: "lookup", ArgumentsFragment: `{"city":`,
	})
	generation.addToolDelta(modelproxy.ToolCallDelta{
		Index: 0, ArgumentsFragment: `"Paris"}`,
	})
	if generation.textValue() != "hello world" {
		t.Fatalf("text was not accumulated: %q", generation.textValue())
	}
	calls := generation.completeToolCalls()
	if len(calls) != 1 {
		t.Fatalf("want one complete tool call, got %+v", calls)
	}
	call, ok := calls[0].(map[string]any)
	if !ok {
		t.Fatalf("tool call projection is not an object: %+v", calls[0])
	}
	if call["id"] != "call-1" || call["name"] != "lookup" {
		t.Fatalf("tool call identity changed: %+v", call)
	}
	arguments, ok := call["arguments"].(map[string]any)
	if !ok || arguments["city"] != "Paris" {
		t.Fatalf("tool call arguments were not assembled: %+v", call)
	}
}

func TestIncompleteToolCallIsDroppedNotExecuted(t *testing.T) {
	generation := newStreamGeneration("op-1", "gen-1")
	generation.addText("done")
	generation.addToolDelta(modelproxy.ToolCallDelta{
		Index: 0, ID: "call-1", Name: "lookup", ArgumentsFragment: `{"city":`,
	})
	generation.addToolDelta(modelproxy.ToolCallDelta{Index: 1, Name: "lookup"})
	generation.addToolDelta(modelproxy.ToolCallDelta{
		Index: 2, ID: "call-3", Name: "", ArgumentsFragment: `{}`,
	})
	generation.addToolDelta(modelproxy.ToolCallDelta{
		Index: 3, ID: "call-4", Name: "lookup", ArgumentsFragment: `[]`,
	})
	if calls := generation.completeToolCalls(); len(calls) != 0 {
		t.Fatalf("incomplete tool calls leaked into the final content: %+v", calls)
	}
}

func TestGenerationsNeverMixBuffers(t *testing.T) {
	first := newStreamGeneration("op-1", "gen-1")
	first.addText("old generation ")
	first.addToolDelta(modelproxy.ToolCallDelta{
		Index: 0, ID: "old-call", Name: "lookup", ArgumentsFragment: `{"city":"Rome"}`,
	})
	second := newStreamGeneration("op-1", "gen-2")
	second.addText("new generation")
	if first.textValue() == second.textValue() {
		t.Fatalf("the two generations share a buffer")
	}
	if len(second.completeToolCalls()) != 0 {
		t.Fatalf("the replacement generation inherited old tool calls")
	}
	if got := second.textValue(); got != "new generation" {
		t.Fatalf("replacement generation text is %q", got)
	}
}

func TestToolCallFragmentsAreOnlyCompleteWithAnObject(t *testing.T) {
	generation := newStreamGeneration("op-1", "gen-1")
	generation.addToolDelta(modelproxy.ToolCallDelta{
		Index: 0, ID: "call-1", Name: "lookup", ArgumentsFragment: `"not an object"`,
	})
	if calls := generation.completeToolCalls(); len(calls) != 0 {
		t.Fatalf("a non-object argument fragment must not complete a call: %+v", calls)
	}
}

func TestTerminalFrameClassification(t *testing.T) {
	if !isTerminalStreamFrame(Frame{Type: "final"}) {
		t.Fatalf("final must be terminal")
	}
	if isTerminalStreamFrame(Frame{Type: "error", Retryable: true}) {
		t.Fatalf("a retryable error frame is not terminal: the next generation still replaces the preview")
	}
	if !isTerminalStreamFrame(Frame{Type: "error"}) {
		t.Fatalf("a non-retryable error is terminal")
	}
	if isTerminalStreamFrame(Frame{Type: "delta"}) {
		t.Fatalf("a delta must not close the preview")
	}
}

// fakeStreamProvider is one in-process StreamProvider: it delivers the chunks it
// was given and stops when the consumer's emit fails, exactly like the real
// adapters.
type fakeStreamProvider struct {
	chunks   []modelproxy.Chunk
	response modelproxy.ProviderResponse
}

func (p *fakeStreamProvider) Name() string { return "fake-stream" }

func (p *fakeStreamProvider) Available(*modelproxy.Candidate) error { return nil }

func (p *fakeStreamProvider) Call(
	context.Context, modelproxy.CallRequest,
) (modelproxy.ProviderResponse, error) {
	return p.response, nil
}

func (p *fakeStreamProvider) Stream(
	_ context.Context,
	_ modelproxy.CallRequest,
	emit func(modelproxy.Chunk) error,
) (modelproxy.ProviderResponse, error) {
	for _, chunk := range p.chunks {
		if err := emit(chunk); err != nil {
			return modelproxy.ProviderResponse{}, err
		}
	}
	return p.response, nil
}

// C2.02: every frame of one physical stream attempt is tagged with that
// attempt's operation_attempts row id (the generation id), and the generation
// buffer is exactly the streamed content. Nothing from another attempt can leak
// in because the content half creates a new buffer per Call.
func TestStreamContentTagsFramesWithTheAttemptGeneration(t *testing.T) {
	var frames []Frame
	content := &streamModelContent{
		operationID: "op-1",
		input:       Input{Params: map[string]any{"task": "agent"}},
		emit: func(frame Frame) error {
			frames = append(frames, frame)
			return nil
		},
	}
	provider := &fakeStreamProvider{
		chunks: []modelproxy.Chunk{
			{Type: modelproxy.StreamDelta, Text: "hel"},
			{Type: modelproxy.StreamDelta, Text: "lo"},
		},
		response: modelproxy.ProviderResponse{Data: map[string]any{"text": "hello"}},
	}
	info := modelAttemptInfo{Provider: provider, Attempt: 2, AttemptID: "gen-2"}

	response, err := content.call(context.Background(), info)
	if err != nil {
		t.Fatalf("the fake stream failed: %v", err)
	}
	if len(frames) != 2 {
		t.Fatalf("want two delta frames, got %+v", frames)
	}
	for index, frame := range frames {
		if frame.OperationID != "op-1" || frame.GenerationID != "gen-2" {
			t.Fatalf("frame %d is not scoped to this attempt: %+v", index, frame)
		}
	}
	if got := content.generation.generationID; got != "gen-2" {
		t.Fatalf("the buffered generation is %q, want gen-2", got)
	}
	if got := content.generation.textValue(); got != "hello" {
		t.Fatalf("the generation buffered %q, want hello", got)
	}
	if response.Data["text"] != "hello" {
		t.Fatalf("the provider response changed: %+v", response.Data)
	}
}

// C2.02: a stopped consumer must be classified as the streaming cancellation.
// Treating the emit error as an unclassified provider failure would make the
// driver retry or fail over after nobody is listening.
func TestStreamContentReportsConsumerStopAsCancellation(t *testing.T) {
	stopped := errors.New("the client connection is gone")
	content := &streamModelContent{
		operationID: "op-1",
		input:       Input{Params: map[string]any{"task": "agent"}},
		emit: func(Frame) error {
			return stopped
		},
	}
	provider := &fakeStreamProvider{
		chunks: []modelproxy.Chunk{
			{Type: modelproxy.StreamDelta, Text: "first"},
			{Type: modelproxy.StreamDelta, Text: "never"},
		},
	}
	info := modelAttemptInfo{Provider: provider, Attempt: 1, AttemptID: "gen-1"}

	_, err := content.call(context.Background(), info)
	if !errors.Is(err, errStreamConsumerStopped) {
		t.Fatalf("the consumer stop was not reported as a stream stop: %v", err)
	}
	if !errors.Is(err, stopped) {
		t.Fatalf("the emitter's error was dropped: %v", err)
	}
	failure := classifyModelFailure(context.Background(), err)
	if !failure.Cancelled || failure.Code != CodeStreamCancelled {
		t.Fatalf("a stopped consumer was classified as %+v, want a cancellation", failure)
	}
	if failure.Retryable || failure.FallbackEligible {
		t.Fatalf("a stopped consumer must not retry or fall back: %+v", failure)
	}
}

// C2.02: a retry/fallback notice is a delivery. Its failure is returned (the
// driver stops dispatching), and its frame is the provisional retryable frame
// tagged with the failed generation.
func TestStreamNoticePropagatesDeliveryFailure(t *testing.T) {
	stopped := errors.New("the client connection is gone")
	failing := &streamModelContent{
		operationID: "op-1",
		emit: func(Frame) error {
			return stopped
		},
	}
	info := modelAttemptInfo{Attempt: 1, AttemptID: "gen-1"}
	if err := failing.notice(context.Background(), info,
		modelNotice{Kind: NoticeRetry, Code: CodeProviderTransient}); !errors.Is(err, stopped) {
		t.Fatalf("the notice delivery failure was swallowed: %v", err)
	}

	var frames []Frame
	content := &streamModelContent{
		operationID: "op-1",
		emit: func(frame Frame) error {
			frames = append(frames, frame)
			return nil
		},
	}
	if err := content.notice(context.Background(), info,
		modelNotice{Kind: NoticeFallback, Code: CodeAdapterUnavailable}); err != nil {
		t.Fatalf("the notice could not be emitted: %v", err)
	}
	if len(frames) != 1 {
		t.Fatalf("want one notice frame, got %+v", frames)
	}
	notice := frames[0]
	if notice.Type != FrameError || !notice.Retryable {
		t.Fatalf("the notice is not a provisional retryable error: %+v", notice)
	}
	if notice.OperationID != "op-1" || notice.GenerationID != "gen-1" {
		t.Fatalf("the notice is not scoped to the failed attempt: %+v", notice)
	}
	if notice.Error == nil || notice.Error.Code != CodeAdapterUnavailable {
		t.Fatalf("the notice does not name the failure: %+v", notice.Error)
	}
}
