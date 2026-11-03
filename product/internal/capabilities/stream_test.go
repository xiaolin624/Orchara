package capabilities

// F06/T044: unit tests for the generation buffer. They are deliberately pure:
// the real ledger/stream behaviour is covered by the real-stack pytest suite.
// The properties asserted here are the acceptance ones - no mixing between
// generations, and an incomplete tool call never becomes executable content.

import (
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
