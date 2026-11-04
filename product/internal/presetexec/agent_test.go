package presetexec

// F04A/T043 Go seam tests: the agent loop over a scripted capability client and
// the real executor seams (bindings, uploads, registration, node results, node
// status, result proposal). The scripted client stands in for the T041 server
// only at the Go seam; the real-stack acceptance lives in
// tests/f06/test_t043_agent_node.py. These tests pin the executor's own guard
// rails: schema re-validation, typed waiting/unknown outcomes, turn limits,
// duplicate/unknown tool calls, cancellation and tool-failure reporting.

import (
	"context"
	"encoding/json"
	"strings"
	"sync"
	"testing"

	"orchara/platform/internal/capabilities"
	"orchara/platform/internal/sandbox"
	"orchara/platform/internal/uuid"
)

// fakeFacts is the trusted capability catalog seam.
type fakeFacts struct {
	model    ModelFact
	tools    []ToolFact
	modelErr error
	toolErr  error
}

func (f *fakeFacts) ModelProfile(context.Context, string) (ModelFact, error) {
	return f.model, f.modelErr
}

func (f *fakeFacts) ToolFacts(context.Context, string, []string) ([]ToolFact, error) {
	return f.tools, f.toolErr
}

func currentFacts() *fakeFacts {
	return &fakeFacts{
		model: ModelFact{ID: testModelProfileID, ValidationState: "passed"},
		tools: []ToolFact{{
			ID: PresetToolID, Name: PresetToolName, State: "ready", Callable: true,
		}},
	}
}

// agentScript scripts the capability seam: each model turn returns the next
// prepared projection and every tool grant is dispatching with the requested
// operation id.
type agentScript struct {
	mu         sync.Mutex
	turns      [][]any
	final      map[string]any
	modelErr   error
	toolErr    error
	invoked    int
	modelTurns int
}

func (s *agentScript) invoke(input capabilities.Input) (*capabilities.Result, error) {
	s.mu.Lock()
	s.invoked++
	s.mu.Unlock()
	if input.CapabilityKind == "tool" {
		if s.toolErr != nil {
			return nil, s.toolErr
		}
		return &capabilities.Result{
			OperationID: input.OperationID, State: "dispatching",
			Data: map[string]any{"grant": map[string]any{
				"operation_id":       input.OperationID,
				"capability_version": input.CapabilityVersion,
				"capability_kind":    "tool",
				"run_id":             input.RunID,
				"attempt_token":      input.AttemptToken,
			}},
		}, nil
	}
	if s.modelErr != nil {
		return nil, s.modelErr
	}
	s.mu.Lock()
	turn := s.modelTurns
	s.modelTurns++
	var calls []any
	if turn < len(s.turns) {
		calls = s.turns[turn]
	} else {
		calls = []any{}
	}
	final := s.final
	s.mu.Unlock()
	data := map[string]any{"text": "stub-agent:text", "tool_calls": calls}
	if len(calls) == 0 && final != nil {
		data["structured"] = final
	}
	return &capabilities.Result{OperationID: input.OperationID, State: "succeeded", Data: data}, nil
}

func agentDefinitionMap(t *testing.T) map[string]any {
	t.Helper()
	var definition map[string]any
	if err := json.Unmarshal([]byte(agentDefinitionJSON), &definition); err != nil {
		t.Fatalf("parse the agent definition: %v", err)
	}
	return definition
}

func agentDeps(t *testing.T, client *fakeClient, box *fakeSandbox, facts Facts) Deps {
	t.Helper()
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box)
	deps.Facts = facts
	return deps
}

func newAgentClient(t *testing.T, script *agentScript) (*fakeClient, *fakeSandbox, Deps) {
	t.Helper()
	lease, manifest, _ := linearLease(t, agentDefinitionMap(t))
	input := inputArtifact()
	client := linearClient(t, lease, manifest, input, []byte(salesCSV))
	client.invokeCapability = script.invoke
	box := successSandbox()
	return client, box, agentDeps(t, client, box, currentFacts())
}

func agentCallHint() map[string]any {
	return map[string]any{
		"id": "call-1", "name": PresetToolName, "arguments": map[string]any{},
	}
}

func TestAgentChainCallsToolAndValidatesFinalOutput(t *testing.T) {
	final := map[string]any{"text": "validated summary"}
	script := &agentScript{
		turns: [][]any{{agentCallHint()}},
		final: final,
	}
	client, box, deps := newAgentClient(t, script)
	report := Run(context.Background(), deps)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 2 {
		t.Fatalf("sandbox calls = %d, want python + one tool call", box.calls)
	}
	if box.last.Program != sandbox.ProgramTool {
		t.Fatalf("the tool call did not run the sandbox tool program: %+v", box.last)
	}

	// Exactly three capability operations: model turn 1, one tool, model turn 2.
	if len(client.capabilityInputs) != 3 {
		t.Fatalf("capability calls = %+v", client.capabilityInputs)
	}
	wantKeys := []string{
		"agent:analysis:model:1", "agent:analysis:tool:call-1", "agent:analysis:model:2",
	}
	for index, want := range wantKeys {
		call := client.capabilityInputs[index]
		if call.LogicalKey != want {
			t.Fatalf("logical key %d = %q, want %q", index, call.LogicalKey, want)
		}
		if !uuid.Valid(call.OperationID) {
			t.Fatalf("operation id %q is not a UUID", call.OperationID)
		}
	}
	if client.capabilityInputs[0].OperationID == client.capabilityInputs[2].OperationID {
		t.Fatal("two model turns reused one operation id")
	}
	first := client.capabilityInputs[0]
	if first.CapabilityKind != "model" || first.CapabilityVersion != testModelProfileID {
		t.Fatalf("first model input = %+v", first)
	}
	if first.Params["task"] != "agent" {
		t.Fatalf("model params task = %v", first.Params["task"])
	}
	if _, ok := first.Params["response_schema"].(map[string]any); !ok {
		t.Fatalf("the node output_schema was not requested: %+v", first.Params)
	}
	tools, _ := first.Params["tools"].([]any)
	if len(tools) != 1 {
		t.Fatalf("declared tools = %+v", first.Params["tools"])
	}
	tool := tools[0].(map[string]any)
	if tool["name"] != PresetToolName || tool["capability_version"] != PresetToolID {
		t.Fatalf("declared tool = %+v", tool)
	}
	messages := first.Params["messages"].([]any)
	if len(messages) != 2 {
		t.Fatalf("first-turn messages = %+v", messages)
	}
	user := messages[1].(map[string]any)
	if user["role"] != "user" || !strings.Contains(user["content"].(string), `"stats"`) {
		t.Fatalf("user message = %+v", user)
	}
	// Turn 2 includes the tool result message and the same response schema.
	second := client.capabilityInputs[2]
	secondMessages := second.Params["messages"].([]any)
	if len(secondMessages) != 3 || secondMessages[2].(map[string]any)["role"] != "tool" {
		t.Fatalf("second-turn messages = %+v", secondMessages)
	}

	// The tool grant result is reported once, succeeded, pointing at the ready
	// tool data artifact; the operation id is the granted one.
	if len(client.capabilityReports) != 1 {
		t.Fatalf("capability reports = %+v", client.capabilityReports)
	}
	reportRow := client.capabilityReports[0]
	if reportRow.State != "succeeded" || reportRow.ResultArtifactID == "" {
		t.Fatalf("tool result report = %+v", reportRow)
	}
	if reportRow.OperationID != client.capabilityInputs[1].OperationID {
		t.Fatalf("tool report operation = %q", reportRow.OperationID)
	}

	// Node results: one agent data row, one context-trace file row (T047), the
	// tool's files and its log.
	agentResults := 0
	traceRows := 0
	for _, result := range client.nodeResults {
		if result.NodeID == "analysis" {
			agentResults++
			if result.Kind == "file" && result.Label == "context-trace" {
				traceRows++
			}
		}
	}
	if agentResults != 5 {
		t.Fatalf("agent node results = %d: %+v", agentResults, client.nodeResults)
	}
	if traceRows != 1 {
		t.Fatalf("context trace rows = %d: %+v", traceRows, client.nodeResults)
	}
	kinds := map[string]int{}
	for _, result := range client.nodeResults {
		if result.NodeID == "analysis" {
			kinds[result.Kind]++
		}
	}
	if kinds["data"] != 1 || kinds["file"] != 3 || kinds["log"] != 1 {
		t.Fatalf("agent node result kinds = %+v", kinds)
	}
	// The trace artifact carries budget metadata and protected key paths, never
	// the message text or a raw amount.
	var traceUpload *recordedUpload
	for index := range client.uploads {
		upload := &client.uploads[index]
		if upload.Request.Purpose == "node_output" && strings.Contains(string(upload.Content), `"turns"`) {
			traceUpload = upload
		}
	}
	if traceUpload == nil {
		t.Fatalf("no context trace upload: %+v", client.uploads)
	}
	traceText := string(traceUpload.Content)
	for _, marker := range []string{`"turns"`, `"protected"`, `"memory_state":"not_run"`, `"budget_tokens"`} {
		if !strings.Contains(traceText, marker) {
			t.Fatalf("context trace misses %s: %s", marker, traceText)
		}
	}
	if strings.Contains(traceText, "250.00") || strings.Contains(traceText, "validated summary") {
		t.Fatalf("context trace leaks content: %s", traceText)
	}

	// Node status: every node pending/running/succeeded, exactly once.
	observed := map[string][]string{}
	for _, event := range client.nodeEvents {
		observed[event.NodeID] = append(observed[event.NodeID], event.State)
	}
	want := []string{"pending", "running", "succeeded"}
	for _, nodeID := range []string{"start", "stats", "analysis", "end"} {
		if strings.Join(observed[nodeID], ",") != strings.Join(want, ",") {
			t.Fatalf("node events for %s = %v", nodeID, observed[nodeID])
		}
	}

	// The end node proposed success with the final output artifact.
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "succeeded" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	finalUpload, ok := client.uploadsByPurpose()["final_output"]
	if !ok || len(finalUpload.Content) == 0 {
		t.Fatalf("final output upload = %+v", client.uploads)
	}
	var sealed map[string]any
	if err := json.Unmarshal(finalUpload.Content, &sealed); err != nil {
		t.Fatalf("final output is not JSON: %v", err)
	}
	if sealed["text"] != "validated summary" {
		t.Fatalf("final output = %+v", sealed)
	}
}

func TestAgentFinalOutputSchemaViolationIsLocatedAndNeverSucceeds(t *testing.T) {
	// The model projection is invalid for the node schema (the real server would
	// normally refuse it first; the executor must not trust that).
	script := &agentScript{final: map[string]any{"wrong": true}}
	client, box, deps := newAgentClient(t, script)
	report := Run(context.Background(), deps)
	if report.Outcome != "failed" || report.ErrorCode != CodeNodeOutputInvalid {
		t.Fatalf("report = %+v", report)
	}
	if !strings.Contains(report.Error, "analysis") || !strings.Contains(report.Error, "#") {
		t.Fatalf("the failure is not located at a node pointer: %q", report.Error)
	}
	if box.calls != 1 {
		t.Fatalf("the tool sandbox ran for an invalid final output: %d", box.calls)
	}
	if len(client.nodeResults) != 4 {
		t.Fatalf("a failed agent changed the python node's result rows: %+v", client.nodeResults)
	}
	for _, result := range client.nodeResults {
		if result.NodeID == "analysis" {
			t.Fatalf("a failed agent registered an agent node result: %+v", result)
		}
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "failed" ||
		client.proposals[0].ErrorCode != CodeNodeOutputInvalid {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	for _, upload := range client.uploads {
		if upload.Request.Purpose == "final_output" {
			t.Fatal("an invalid agent output produced a final artifact")
		}
	}
}

func TestAgentAwaitingApprovalParksTheAttempt(t *testing.T) {
	lease, manifest, _ := linearLease(t, agentDefinitionMap(t))
	input := inputArtifact()
	client := linearClient(t, lease, manifest, input, []byte(salesCSV))
	var invoked capabilities.Input
	client.invokeCapability = func(call capabilities.Input) (*capabilities.Result, error) {
		invoked = call
		return &capabilities.Result{OperationID: call.OperationID, State: "awaiting_approval"}, nil
	}
	box := successSandbox()
	report := Run(context.Background(), agentDeps(t, client, box, currentFacts()))
	if report.Outcome != "waiting_approval" || report.ErrorCode != "approval_required" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.proposals) != 1 {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	proposal := client.proposals[0]
	if proposal.SuggestedState != "waiting_approval" || proposal.ErrorCode != "approval_required" {
		t.Fatalf("proposal = %+v", proposal)
	}
	wantCheckpoint := "capability:" + invoked.OperationID
	if proposal.CheckpointID != wantCheckpoint {
		t.Fatalf("checkpoint = %q, want %q", proposal.CheckpointID, wantCheckpoint)
	}
	if len(client.exits) != 1 {
		t.Fatalf("the waiting attempt did not certify its exit: %+v", client.exits)
	}
}

func TestAgentUnknownOutcomeStillFails(t *testing.T) {
	lease, manifest, _ := linearLease(t, agentDefinitionMap(t))
	input := inputArtifact()
	client := linearClient(t, lease, manifest, input, []byte(salesCSV))
	client.invokeCapability = func(call capabilities.Input) (*capabilities.Result, error) {
		return &capabilities.Result{OperationID: call.OperationID, State: "unknown"}, nil
	}
	box := successSandbox()
	report := Run(context.Background(), agentDeps(t, client, box, currentFacts()))
	if report.Outcome != "failed" || report.ErrorCode != CodeAgentUnknown {
		t.Fatalf("report = %+v", report)
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "failed" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
}

func TestAgentCapabilityErrorBecomesAPlatformCode(t *testing.T) {
	lease, manifest, _ := linearLease(t, agentDefinitionMap(t))
	input := inputArtifact()
	client := linearClient(t, lease, manifest, input, []byte(salesCSV))
	client.invokeCapability = func(capabilities.Input) (*capabilities.Result, error) {
		return nil, codedTestError{code: capabilities.CodeCapabilityNotAllowed}
	}
	box := successSandbox()
	report := Run(context.Background(), agentDeps(t, client, box, currentFacts()))
	if report.Outcome != "failed" || report.ErrorCode != capabilities.CodeCapabilityNotAllowed {
		t.Fatalf("report = %+v", report)
	}
}

type codedTestError struct{ code string }

func (e codedTestError) Error() string     { return e.code }
func (e codedTestError) ErrorCode() string { return e.code }

func TestAgentTurnLimitFailsWithoutSuccess(t *testing.T) {
	lease, manifest, _ := linearLease(t, agentDefinitionMap(t))
	// max_turns=1: the first turn's tool call consumes the only turn.
	var definition map[string]any
	if err := json.Unmarshal([]byte(agentDefinitionJSON), &definition); err != nil {
		t.Fatal(err)
	}
	for _, node := range definition["nodes"].([]any) {
		entry := node.(map[string]any)
		if entry["type"] == "agent" {
			entry["config"].(map[string]any)["max_turns"] = float64(1)
		}
	}
	lease, manifest, _ = linearLease(t, definition)
	input := inputArtifact()
	client := linearClient(t, lease, manifest, input, []byte(salesCSV))
	client.invokeCapability = (&agentScript{turns: [][]any{{agentCallHint()}}}).invoke
	box := successSandbox()
	report := Run(context.Background(), agentDeps(t, client, box, currentFacts()))
	if report.Outcome != "failed" || report.ErrorCode != CodeAgentTurnLimit {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 2 {
		t.Fatalf("sandbox calls = %d (python + one tool)", box.calls)
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState == "succeeded" {
		t.Fatalf("turn limit proposed success: %+v", client.proposals)
	}
}

func TestAgentToolCallGuards(t *testing.T) {
	cases := []struct {
		name  string
		calls []any
		code  string
	}{
		{"duplicate id", []any{agentCallHint(), agentCallHint()}, CodeAgentToolCallInvalid},
		{"empty id", []any{map[string]any{"id": "", "name": PresetToolName, "arguments": map[string]any{}}}, CodeAgentToolCallInvalid},
		{"unknown name", []any{map[string]any{"id": "call-x", "name": "other_tool", "arguments": map[string]any{}}}, CodeToolUnsupportedTool},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			lease, manifest, _ := linearLease(t, agentDefinitionMap(t))
			input := inputArtifact()
			client := linearClient(t, lease, manifest, input, []byte(salesCSV))
			script := &agentScript{turns: [][]any{testCase.calls}}
			client.invokeCapability = script.invoke
			box := successSandbox()
			report := Run(context.Background(), agentDeps(t, client, box, currentFacts()))
			if report.Outcome != "failed" || report.ErrorCode != testCase.code {
				t.Fatalf("report = %+v", report)
			}
			if box.calls != 1 {
				t.Fatalf("an invalid tool call reached the sandbox: %d", box.calls)
			}
			if len(client.capabilityReports) != 0 {
				t.Fatalf("an invalid tool call produced a result report: %+v", client.capabilityReports)
			}
		})
	}
}

func TestAgentStopsOnCancelBeforeTheFirstTurn(t *testing.T) {
	lease, manifest, _ := linearLease(t, agentDefinitionMap(t))
	input := inputArtifact()
	client := linearClient(t, lease, manifest, input, []byte(salesCSV))
	client.cancelAt = 1
	client.runState = "cancelling"
	box := successSandbox()
	report := Run(context.Background(), agentDeps(t, client, box, currentFacts()))
	if report.Outcome != "cancelled" || report.ErrorCode != "cancel_requested" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.capabilityInputs) != 0 {
		t.Fatalf("a cancelled agent spent a model call: %+v", client.capabilityInputs)
	}
	if len(client.proposals) != 0 {
		t.Fatalf("a cancelled agent proposed a result: %+v", client.proposals)
	}
}

func TestAgentToolFailureFailsTheOperationAndTheNode(t *testing.T) {
	script := &agentScript{turns: [][]any{{agentCallHint()}}}
	client, box, deps := newAgentClient(t, script)
	base := box.outcome
	box.interrupt = func(context.Context) (sandbox.Outcome, error) {
		box.mu.Lock()
		calls := box.calls
		box.mu.Unlock()
		if calls == 2 {
			failed := base
			failed.ExitCode = 1
			failed.OutputJSON = []byte(`{"data":{},"artifacts":[]}`)
			return failed, nil
		}
		return base, nil
	}
	report := Run(context.Background(), deps)
	if report.Outcome != "failed" || report.ErrorCode != CodeToolCrash {
		t.Fatalf("report = %+v", report)
	}
	if len(client.capabilityReports) != 1 {
		t.Fatalf("tool failure reports = %+v", client.capabilityReports)
	}
	if client.capabilityReports[0].State != "failed" ||
		client.capabilityReports[0].ErrorCode != CodeToolCrash {
		t.Fatalf("tool failure report = %+v", client.capabilityReports[0])
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState == "succeeded" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
}

func TestAgentRequiresPassedModelAndCallableTool(t *testing.T) {
	cases := []struct {
		name  string
		facts *fakeFacts
		code  string
	}{
		{"revoked model", &fakeFacts{model: ModelFact{ValidationState: "passed", Revoked: true}}, capabilities.CodeModelUnavailable},
		{"unvalidated model", &fakeFacts{model: ModelFact{ValidationState: "pending"}}, capabilities.CodeModelUnavailable},
		{"unapproved tool", &fakeFacts{
			model: ModelFact{ValidationState: "passed"},
			tools: []ToolFact{{ID: PresetToolID, State: "ready", Callable: false}},
		}, capabilities.CodeToolUnavailable},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			lease, manifest, _ := linearLease(t, agentDefinitionMap(t))
			input := inputArtifact()
			client := linearClient(t, lease, manifest, input, []byte(salesCSV))
			client.invokeCapability = (&agentScript{}).invoke
			box := successSandbox()
			report := Run(context.Background(), agentDeps(t, client, box, testCase.facts))
			if report.Outcome != "failed" || report.ErrorCode != testCase.code {
				t.Fatalf("report = %+v", report)
			}
			if len(client.capabilityInputs) != 0 {
				t.Fatal("an unusable capability was invoked")
			}
		})
	}
}
