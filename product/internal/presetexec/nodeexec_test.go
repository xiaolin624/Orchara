package presetexec

// C4.01/T152: the controlled node execution seam.
//
// The agent node receives one explicit nodeExecution built by the owning
// scheduler and returns a typed nodeOutcome. These tests observe the executor's
// public Run seam: the same Run create/claim/execute path the production
// supervisor drives. They pin the C4.01 acceptance criteria that a hidden context
// value must not change the node's scheduler, that the binding namespace is a
// snapshot taken at start, and that recovery reuses a sealed node output instead
// of executing the node again.

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"orchara/platform/internal/capabilities"
	"orchara/platform/internal/sandbox"
)

// TestLinearAgentIgnoresHiddenGraphSchedulerContext proves a linear attempt is
// unaffected by a graph attempt record in the context. Before C4.01 the agent's
// failure/waiting helpers looked up graphAttemptKey through context.Value and
// returned a scheduler-neutral outcome instead of proposing and confirming its
// own result: a hidden context value decided who committed the run.
func TestLinearAgentIgnoresHiddenGraphSchedulerContext(t *testing.T) {
	t.Run("failure still proposes and confirms the exit", func(t *testing.T) {
		script := &agentScript{final: map[string]any{"wrong": true}}
		client, box, deps := newAgentClient(t, script)
		// A linear attempt never runs under the graph scheduler; injecting the
		// record must not change what it does.
		ctx := context.WithValue(context.Background(), graphAttemptKey{}, &graphNodeAttempt{})
		report := Run(ctx, deps)

		if report.Outcome != "failed" || report.ErrorCode != CodeNodeOutputInvalid {
			t.Fatalf("report = %+v", report)
		}
		if box.calls != 1 {
			t.Fatalf("sandbox calls = %d", box.calls)
		}
		if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "failed" ||
			client.proposals[0].ErrorCode != CodeNodeOutputInvalid {
			t.Fatalf("proposals = %+v", client.proposals)
		}
		if len(client.exits) != 1 {
			t.Fatalf("the linear scheduler must certify the attempt exit exactly once: %+v",
				client.exits)
		}
	})

	t.Run("waiting still parks the attempt", func(t *testing.T) {
		lease, manifest, _ := linearLease(t, agentDefinitionMap(t))
		client := linearClient(t, lease, manifest, inputArtifact(), []byte(salesCSV))
		client.invokeCapability = func(call capabilities.Input) (*capabilities.Result, error) {
			return &capabilities.Result{OperationID: call.OperationID, State: "awaiting_approval"}, nil
		}
		ctx := context.WithValue(context.Background(), graphAttemptKey{}, &graphNodeAttempt{})
		report := Run(ctx, agentDeps(t, client, successSandbox(), currentFacts()))

		if report.Outcome != "waiting_approval" || report.ErrorCode != "approval_required" {
			t.Fatalf("report = %+v", report)
		}
		if len(client.proposals) != 1 ||
			client.proposals[0].SuggestedState != "waiting_approval" {
			t.Fatalf("proposals = %+v", client.proposals)
		}
		if len(client.exits) != 1 {
			t.Fatalf("the waiting attempt must certify its exit: %+v", client.exits)
		}
	})
}

// TestNodeExecutionSnapshotsTheBindingNamespaceAndSealedScope proves the explicit
// input is a snapshot, not a live view: replacing an upstream entry after the
// execution was built does not change what the node resolves, the sealed node-id
// scope is copied, and the binding source kinds come from the sealed definition
// rather than a synthetic plan.
func TestNodeExecutionSnapshotsTheBindingNamespaceAndSealedScope(t *testing.T) {
	definition := &manifestDefinition{Nodes: []manifestNode{
		{ID: "start", Type: "start"},
		{ID: "stats", Type: "python"},
		{ID: "analysis", Type: "agent", Inputs: map[string]planBinding{
			"from_start":  {Ref: "node:start#/artifact"},
			"from_python": {Ref: "node:stats#/total"},
			"from_input":  {Ref: "input#/task"},
			"constant":    {Literal: json.RawMessage(`"creator"`)},
		}},
		{ID: "end", Type: "end"},
	}}
	startOutput := map[string]any{"artifact": map[string]any{"artifact_id": "a-1"}}
	statsOutput := map[string]any{"total": "9.00"}
	available := map[string]any{
		"input": startOutput,
		"start": startOutput,
		"stats": statsOutput,
	}
	plan := &graphPlan{
		definition: definition, startID: "start",
		order: []string{"start", "stats", "analysis", "end"},
	}
	view := &graphNodeView{available: available}
	exec := (&runner{}).newGraphNodeExecution(plan, nil, &definition.Nodes[2], view)

	// The scheduler and the other branches mutate their own maps, never the
	// snapshot the node holds.
	available["stats"] = map[string]any{"total": "tampered"}
	available["late"] = map[string]any{"total": "late"}
	plan.order[1] = "renamed"
	if got := exec.namespace["stats"].(map[string]any)["total"]; got != "9.00" {
		t.Fatalf("the namespace changed after the execution was built: %v", exec.namespace["stats"])
	}
	if _, present := exec.namespace["late"]; present {
		t.Fatal("a node saw an upstream output published after it started")
	}
	if len(exec.scopeNodeIDs) != 4 || exec.scopeNodeIDs[1] != "stats" {
		t.Fatalf("the sealed scope was not snapshotted: %v", exec.scopeNodeIDs)
	}

	scope := agentContextScope(exec)
	if strings.Join(scope.NodeIDs, ",") != "start,stats,analysis,end" {
		t.Fatalf("context scope = %v", scope.NodeIDs)
	}
	if len(scope.ArtifactIDs) != 0 {
		t.Fatalf("the scope must not grant free artifact ids: %v", scope.ArtifactIDs)
	}

	inputs := resolvedAgentInputs(exec, map[string]any{
		"from_start": startOutput, "from_python": statsOutput,
		"from_input": startOutput["artifact"], "constant": "creator",
	})
	kinds := map[string]string{}
	nodes := map[string]string{}
	for _, input := range inputs {
		kinds[input.Label] = input.Source.Kind
		nodes[input.Label] = input.Source.NodeID
	}
	want := map[string][2]string{
		"from_start":  {"start", "start"},
		"from_python": {"python", "stats"},
		"from_input":  {"start", "start"},
		"constant":    {"literal", ""},
	}
	for label, expected := range want {
		if kinds[label] != expected[0] || nodes[label] != expected[1] {
			t.Fatalf("%s source = %q/%q, want %v", label, kinds[label], nodes[label], expected)
		}
	}
}

// waitingSiblingSandbox reproduces the graph race a waiting agent must not hide:
// a python sibling is already running when the agent returns its waiting decision.
// The sibling observes the scheduler's hand-over cancel and proves it drained before
// the waiting proposal was submitted.
type waitingSiblingSandbox struct {
	base    string
	started chan struct{}
	once    sync.Once
	stopped atomic.Bool
}

func (b *waitingSiblingSandbox) Run(
	ctx context.Context, request sandbox.Request, _ sandbox.Control,
) (sandbox.Outcome, error) {
	if request.ExecutionUnitID != graphSandboxUnit(b.base, "left") {
		return sandbox.Outcome{}, errors.New("the waiting fixture named an unexpected sandbox unit")
	}
	b.once.Do(func() { close(b.started) })
	<-ctx.Done()
	// The scheduler must wait for this process to stop, not merely send the cancel.
	time.Sleep(20 * time.Millisecond)
	b.stopped.Store(true)
	return sandbox.Outcome{SandboxStarted: true, Cancelled: true,
			CleanupVerified: true, Cleanup: sandbox.CleanupFacts{WorkspaceRemoved: true}},
		sandbox.ErrCancelled
}

const graphAgentWaitingDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object"},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
    {"id":"left","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"` + PresetToolID + `"},"output_schema":{"type":"object"}},
    {"id":"analysis","type":"agent","inputs":{"task":{"literal":"wait for approval"}},
     "output_schema":{"type":"object"},
     "config":{"model_profile_version":"` + testModelProfileID + `",
               "instructions":"Wait for the platform decision.","max_turns":1,
               "tool_versions":[],"subapp_versions":[]}},
    {"id":"join","type":"join","inputs":{},
     "config":{"fork_id":"start","mode":"all_active",
               "required_sources":["left","analysis"],"optional_sources":[]},
     "output_schema":{"type":"object"}},
    {"id":"end","type":"end","inputs":{"results":{"ref":"node:join#/results"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [
    {"source":"start","target":"left"},
    {"source":"start","target":"analysis"},
    {"source":"left","target":"join"},
    {"source":"analysis","target":"join"},
    {"source":"join","target":"end"}
  ]
}`

// TestGraphAgentWaitingDrainsStartedSiblings proves the agent's waiting decision
// travels to the graph scheduler as data: the node does not submit the checkpoint
// itself. The scheduler stops the started sibling, waits for its cleanup proof and
// only then submits exactly one waiting proposal and one exit.
func TestGraphAgentWaitingDrainsStartedSiblings(t *testing.T) {
	document := definitionDocument(t, graphAgentWaitingDefinition)
	document.ToolArtifacts = []manifestToolArtifact{{
		ID: PresetToolID, ImageDigest: "sha256:" + strings.Repeat("bb", 32),
	}}
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{}`)
	box := &waitingSiblingSandbox{base: "unit-test-1", started: make(chan struct{})}

	var committedBeforeStop atomic.Bool
	client.onSubmitResult = func(proposal Proposal) {
		if proposal.SuggestedState != "waiting_approval" {
			return
		}
		if !box.stopped.Load() {
			committedBeforeStop.Store(true)
		}
	}
	client.invokeCapability = func(call capabilities.Input) (*capabilities.Result, error) {
		<-box.started
		return &capabilities.Result{OperationID: call.OperationID,
			State: "awaiting_approval"}, nil
	}
	deps := agentDeps(t, client, successSandbox(), currentFacts())
	deps.Sandbox = box

	report := Run(context.Background(), deps)
	if report.Outcome != "waiting_approval" || report.ErrorCode != "approval_required" {
		t.Fatalf("report = %+v", report)
	}
	if committedBeforeStop.Load() {
		t.Fatal("the waiting proposal was submitted before the started sibling stopped")
	}
	if !box.stopped.Load() {
		t.Fatal("the sibling never observed the hand-over cancellation")
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "waiting_approval" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	operationID := client.capabilityInputs[0].OperationID
	if got := client.proposals[0].CheckpointID; got != "capability:"+operationID {
		t.Fatalf("checkpoint = %q", got)
	}
	if len(client.exits) != 1 || client.exits[0].Evidence["sandbox_started"] != true {
		t.Fatalf("the exit must certify the drained sandbox: %+v", client.exits)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["analysis"], ","); got != "pending,running" {
		t.Fatalf("a waiting agent must not publish a failed fact: %v", observed["analysis"])
	}
	if got := strings.Join(observed["left"], ","); got != "pending,running,failed" {
		t.Fatalf("cancelled sibling states = %v", observed["left"])
	}
	for _, nodeID := range []string{"join", "end"} {
		if len(observed[nodeID]) != 0 {
			t.Fatalf("%s must not run or be skipped by a waiting hand-over: %v",
				nodeID, observed[nodeID])
		}
	}
}

// TestGraphAgentCancellationDrainsStartedSiblings proves the cancellation outcome
// follows the same scheduler ownership: the node publishes only its own failed
// fact and returns; the scheduler stops the started sibling, waits for its cleanup
// proof, emits the skips and confirms exactly one exit without a success proposal.
func TestGraphAgentCancellationDrainsStartedSiblings(t *testing.T) {
	document := definitionDocument(t, graphAgentWaitingDefinition)
	document.ToolArtifacts = []manifestToolArtifact{{
		ID: PresetToolID, ImageDigest: "sha256:" + strings.Repeat("bb", 32),
	}}
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{}`)
	box := &waitingSiblingSandbox{base: "unit-test-1", started: make(chan struct{})}
	client.invokeCapability = func(call capabilities.Input) (*capabilities.Result, error) {
		<-box.started
		return nil, codedTestError{code: capabilities.CodeRunCancelled}
	}
	deps := agentDeps(t, client, successSandbox(), currentFacts())
	deps.Sandbox = box

	report := Run(context.Background(), deps)
	if report.Outcome != "cancelled" || report.ErrorCode != "cancel_requested" {
		t.Fatalf("report = %+v", report)
	}
	if !box.stopped.Load() {
		t.Fatal("the started sibling never observed the hand-over cancellation")
	}
	if len(client.proposals) != 0 {
		t.Fatalf("a cancellation must never propose a result: %+v", client.proposals)
	}
	if len(client.exits) != 1 || client.exits[0].Evidence["sandbox_started"] != true {
		t.Fatalf("the exit must certify the drained sandbox: %+v", client.exits)
	}
	observed := observedNodeStates(client)
	for _, nodeID := range []string{"analysis", "left"} {
		if got := strings.Join(observed[nodeID], ","); got != "pending,running,failed" {
			t.Fatalf("%s states = %v", nodeID, observed[nodeID])
		}
	}
	for _, nodeID := range []string{"join", "end"} {
		if got := strings.Join(observed[nodeID], ","); got != "skipped" {
			t.Fatalf("%s states = %v, want skipped", nodeID, observed[nodeID])
		}
	}
	if rows := nodeResultRows(client, "join"); len(rows) != 0 {
		t.Fatalf("the join must not run after a cancellation: %+v", rows)
	}
}

// TestGraphAgentReusesSealedNodeOutputOnRecovery proves the recovery seam still
// applies to the agent node after the explicit-context migration: a valid sealed
// output is reused byte-for-byte and the node performs no model or tool call.
func TestGraphAgentReusesSealedNodeOutputOnRecovery(t *testing.T) {
	document := definitionDocument(t, graphAgentDefinitionJSON)
	document.ToolArtifacts = []manifestToolArtifact{{
		ID: PresetToolID, ImageDigest: "sha256:" + strings.Repeat("bb", 32),
	}}
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"requires_approval":true}`)
	script := &agentScript{}
	client.invokeCapability = script.invoke

	deps := agentDeps(t, client, successSandbox(), currentFacts())
	deps.NodeOutputs = &fakeNodeOutputs{data: map[string][]byte{
		"start":    []byte(`{"requires_approval":true}`),
		"choose":   []byte(`{"port":"yes","matched_case":0}`),
		"analysis": []byte(`{"text":"sealed agent output"}`),
	}}
	report := Run(context.Background(), deps)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if script.invoked != 0 {
		t.Fatalf("a reused agent node spent %d capability calls", script.invoked)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["analysis"], ","); got != "succeeded" {
		t.Fatalf("a reused node reports only its terminal fact: %v", observed["analysis"])
	}
	results := artifactData(t, client, "join")["results"].(map[string]any)
	value, _ := results["analysis"].(map[string]any)["value"].(map[string]any)
	if value == nil || value["text"] != "sealed agent output" {
		t.Fatalf("the sealed output was not reused: %v", results["analysis"])
	}
}

// TestAgentLocalSuccessCannotBypassRegistrationFailure keeps the C4.01 output
// invariant: a validated final object is not a node success until its artifact was
// registered. A refused registration fails the run and never proposes success.
func TestAgentLocalSuccessCannotBypassRegistrationFailure(t *testing.T) {
	definition := map[string]any{
		"nodes": []any{
			map[string]any{"id": "start", "type": "start", "config": map[string]any{}},
			map[string]any{"id": "analysis", "type": "agent",
				"inputs":        map[string]any{"task": map[string]any{"literal": "register"}},
				"output_schema": map[string]any{"type": "object"},
				"config": map[string]any{
					"model_profile_version": testModelProfileID,
					"instructions":          "Summarise.",
					"max_turns":             float64(1),
					"tool_versions":         []any{},
					"subapp_versions":       []any{},
				}},
			map[string]any{"id": "end", "type": "end", "config": map[string]any{}},
		},
		"edges": []any{
			map[string]any{"source": "start", "target": "analysis"},
			map[string]any{"source": "analysis", "target": "end"},
		},
	}
	lease, manifest := delegationLease(t, definition, nil)
	client := linearClient(t, lease, manifest, inputArtifact(), []byte(salesCSV))
	client.invokeCapability = (&agentScript{final: map[string]any{"text": "validated"}}).invoke
	client.registerErr = errors.New("registration refused")
	report := Run(context.Background(), agentDeps(t, client, successSandbox(), currentFacts()))

	if report.Outcome != "failed" || report.ErrorCode != CodeSandboxFailed {
		t.Fatalf("report = %+v", report)
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "failed" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	if len(client.nodeResults) != 0 {
		t.Fatalf("an unregistered agent output produced node results: %+v", client.nodeResults)
	}
	for _, upload := range client.uploads {
		if upload.Request.Purpose == "final_output" {
			t.Fatal("an unregistered agent output reached the final output")
		}
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["analysis"], ","); got != "pending,running,failed" {
		t.Fatalf("agent node states = %v", observed["analysis"])
	}
	if got := strings.Join(observed["end"], ","); got != "skipped" {
		t.Fatalf("end node states = %v", observed["end"])
	}
}
