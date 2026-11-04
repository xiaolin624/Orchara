package api

// F06/T044: unit tests for the in-process preview hub. The real-stack behaviour
// (SSE, owner rule, live fan-out) is covered by tests/f06/test_t044_streaming.py;
// these tests pin the buffer semantics the browser relies on.

import (
	"testing"
	"time"

	"orchara/platform/internal/capabilities"
)

func previewFrame(kind, generation, text string, retryable bool) capabilities.Frame {
	frame := capabilities.Frame{
		Type: kind, OperationID: "op-1", GenerationID: generation, Text: text,
	}
	if retryable {
		frame.Retryable = true
	}
	if kind == capabilities.FrameError {
		frame.Error = &capabilities.StreamError{Code: "provider_transient", Message: "retry"}
	}
	return frame
}

func TestPreviewHubReplacesOldGeneration(t *testing.T) {
	hub := newPreviewHub()
	hub.Publish("run-1", previewFrame(capabilities.FrameDelta, "gen-1", "old draft", false))
	hub.Publish("run-1", capabilities.Frame{
		Type: capabilities.FrameError, OperationID: "op-1", GenerationID: "gen-1",
		Retryable: true, Error: &capabilities.StreamError{Code: "provider_transient"},
	})
	// A retryable error is not terminal: the preview keeps waiting for the new
	// generation, which then replaces the old buffer wholesale.
	hub.Publish("run-1", previewFrame(capabilities.FrameDelta, "gen-2", "new draft", false))

	snapshot, live, cancel, hasGeneration := hub.Subscribe("run-1")
	defer cancel()
	if !hasGeneration {
		t.Fatalf("the current generation must be visible")
	}
	if len(snapshot) != 1 {
		t.Fatalf("the old generation must be cleared, got %+v", snapshot)
	}
	if snapshot[0].GenerationID != "gen-2" || snapshot[0].Text != "new draft" {
		t.Fatalf("snapshot is not the current generation: %+v", snapshot[0])
	}
	if live == nil {
		t.Fatalf("a non-terminal preview must stay subscribed")
	}
	hub.Publish("run-1", capabilities.Frame{
		Type: capabilities.FrameFinal, OperationID: "op-1", GenerationID: "gen-2",
		Data: map[string]any{"text": "new answer"},
	})
	frame, open := <-live
	if !open || frame.Type != capabilities.FrameFinal || frame.GenerationID != "gen-2" {
		t.Fatalf("subscriber did not receive the terminal frame: %+v open=%v", frame, open)
	}

	// After final, a new subscriber replays only the final snapshot and is not
	// given a live channel.
	finalSnapshot, finalLive, finalCancel, finalHasGeneration := hub.Subscribe("run-1")
	defer finalCancel()
	if !finalHasGeneration || finalLive != nil {
		t.Fatalf("a terminal preview must replay and close")
	}
	if len(finalSnapshot) != 2 {
		t.Fatalf("terminal snapshot should hold the current generation frames, got %+v", finalSnapshot)
	}
	if finalSnapshot[len(finalSnapshot)-1].Type != capabilities.FrameFinal {
		t.Fatalf("terminal snapshot does not end with final: %+v", finalSnapshot)
	}
}

func TestPreviewHubRetryableErrorDoesNotClose(t *testing.T) {
	hub := newPreviewHub()
	hub.Publish("run-1", previewFrame(capabilities.FrameError, "gen-1", "", true))
	snapshot, live, cancel, hasGeneration := hub.Subscribe("run-1")
	defer cancel()
	if !hasGeneration || live == nil {
		t.Fatalf("a retryable error must not close the preview: %+v", snapshot)
	}
	hub.Publish("run-1", previewFrame(capabilities.FrameError, "gen-1", "", false))
	frame, open := <-live
	if !open || frame.Retryable || frame.Type != capabilities.FrameError {
		t.Fatalf("terminal error frame was not delivered: %+v", frame)
	}
}

func TestPreviewHubPrunesIdleBuffers(t *testing.T) {
	hub := newPreviewHub()
	now := time.Unix(1700000000, 0)
	hub.now = func() time.Time { return now }
	hub.Publish("run-1", previewFrame(capabilities.FrameDelta, "gen-1", "text", false))
	now = now.Add(previewTTL + time.Second)
	snapshot, live, cancel, hasGeneration := hub.Subscribe("run-1")
	defer cancel()
	if hasGeneration || len(snapshot) != 0 || live == nil {
		t.Fatalf("an expired preview must not be replayed: %+v", snapshot)
	}
}
