package presetexec

// C4.02/T153: the call_app node runs against the same explicit node execution as
// the agent node.
//
// The tests observe the executor's public Run seam (the same create/claim/execute
// path the production supervisor drives). They pin the C4.02 acceptance criteria:
// a hidden graph scheduler record in the context must not change what a linear
// call_app node does, the owning scheduler is the only writer of the run terminal
// result and the fixed child version / explicit binding facts stay sealed.

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

// TestLinearCallAppIgnoresHiddenGraphSchedulerContext proves a linear call_app
// node is unaffected by a graph attempt record in the context. Before C4.02 the
// delegation segment's failure/waiting helpers looked up graphAttemptKey through
// context.Value and returned a scheduler-neutral outcome instead of proposing and
// confirming its own result: a hidden context value decided who committed the run.
func TestLinearCallAppIgnoresHiddenGraphSchedulerContext(t *testing.T) {
	t.Run("waiting still proposes and confirms the exit", func(t *testing.T) {
		client, box, deps := delegationClient(t, callAppDefinition(testSubappID), []string{testSubappID})
		client.spawnChild = func(SpawnChildRequest) (*ChildRun, error) {
			return &ChildRun{ID: testChildRunID, State: "running"}, nil
		}
		ctx := context.WithValue(context.Background(), graphAttemptKey{}, &graphNodeAttempt{})
		report := Run(ctx, deps)

		if report.Outcome != "waiting_children" || report.ErrorCode != "children_pending" {
			t.Fatalf("report = %+v", report)
		}
		if box.calls != 0 {
			t.Fatalf("the parent ran a sandbox for a pure delegation: %d", box.calls)
		}
		if len(client.proposals) != 1 ||
			client.proposals[0].SuggestedState != "waiting_children" ||
			client.proposals[0].CheckpointID != "child:"+testChildRunID {
			t.Fatalf("the linear scheduler must submit the waiting proposal exactly once: %+v",
				client.proposals)
		}
		if len(client.exits) != 1 {
			t.Fatalf("the linear scheduler must certify the waiting exit exactly once: %+v",
				client.exits)
		}
	})

	t.Run("failure still proposes and confirms the exit", func(t *testing.T) {
		client, _, deps := delegationClient(t, callAppDefinition(testSubappID), []string{testSubappID})
		client.spawnChild = func(SpawnChildRequest) (*ChildRun, error) {
			return &ChildRun{ID: testChildRunID, State: "failed", ErrorCode: "sandbox_unavailable"}, nil
		}
		ctx := context.WithValue(context.Background(), graphAttemptKey{}, &graphNodeAttempt{})
		report := Run(ctx, deps)

		if report.Outcome != "failed" || report.ErrorCode != "sandbox_unavailable" {
			t.Fatalf("report = %+v", report)
		}
		if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "failed" ||
			client.proposals[0].ErrorCode != "sandbox_unavailable" {
			t.Fatalf("the linear scheduler must submit the failed proposal exactly once: %+v",
				client.proposals)
		}
		if len(client.exits) != 1 {
			t.Fatalf("the linear scheduler must certify the failed exit exactly once: %+v",
				client.exits)
		}
	})
}

// graphCallAppWaitingDefinition forks a real python sandbox sibling and a call_app
// node, so the delegation's waiting decision is returned while another branch is
// already running. The join is required on both sources and the end maps it.
const graphCallAppWaitingDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object"},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
    {"id":"left","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"` + PresetToolID + `"},"output_schema":{"type":"object"}},
    {"id":"child","type":"call_app","inputs":{"source":{"literal":"alpha"}},
     "output_schema":{"type":"object","properties":{"answer":{"type":"string"}},
                      "required":["answer"],"additionalProperties":false},
     "config":{"application_version":"` + testSubappID + `"}},
    {"id":"join","type":"join","inputs":{},
     "config":{"fork_id":"start","mode":"all_active",
               "required_sources":["left","child"],"optional_sources":[]},
     "output_schema":{"type":"object"}},
    {"id":"end","type":"end","inputs":{"results":{"ref":"node:join#/results"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [
    {"source":"start","target":"left"},
    {"source":"start","target":"child"},
    {"source":"left","target":"join"},
    {"source":"child","target":"join"},
    {"source":"join","target":"end"}
  ]
}`

// TestGraphCallAppWaitingDrainsStartedSiblings proves the call_app waiting
// decision travels to the graph scheduler as data: the node does not submit the
// checkpoint or certify the exit itself. The scheduler stops the started sandbox
// sibling, waits for its cleanup proof and only then submits exactly one waiting
// proposal and one exit. The resumed attempt re-drives the cancelled sibling.
func TestGraphCallAppWaitingDrainsStartedSiblings(t *testing.T) {
	document := definitionDocument(t, graphCallAppWaitingDefinition)
	document.ToolArtifacts = []manifestToolArtifact{{
		ID: PresetToolID, ImageDigest: "sha256:" + strings.Repeat("bb", 32),
	}}
	document.SubappVersions = []string{testSubappID}
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{}`)
	box := &waitingSiblingSandbox{base: "unit-test-1", started: make(chan struct{})}

	var committedBeforeStop bool
	client.onSubmitResult = func(proposal Proposal) {
		if proposal.SuggestedState != "waiting_children" {
			return
		}
		if !box.stopped.Load() {
			committedBeforeStop = true
		}
	}
	client.spawnChild = func(request SpawnChildRequest) (*ChildRun, error) {
		if request.ApplicationVersionID != testSubappID {
			t.Errorf("spawn version = %q", request.ApplicationVersionID)
		}
		if request.LogicalKey != "call_app:child" {
			t.Errorf("logical key = %q", request.LogicalKey)
		}
		<-box.started
		return &ChildRun{ID: testChildRunID, State: "running"}, nil
	}
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox())
	deps.Sandbox = box

	report := Run(context.Background(), deps)
	if report.Outcome != "waiting_children" || report.ErrorCode != "children_pending" {
		t.Fatalf("report = %+v", report)
	}
	if committedBeforeStop {
		t.Fatal("the waiting proposal was submitted before the started sibling stopped")
	}
	if !box.stopped.Load() {
		t.Fatal("the sibling never observed the hand-over cancellation")
	}
	if len(client.proposals) != 1 ||
		client.proposals[0].SuggestedState != "waiting_children" ||
		client.proposals[0].CheckpointID != "child:"+testChildRunID {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	if len(client.exits) != 1 || client.exits[0].Evidence["sandbox_started"] != true {
		t.Fatalf("the exit must certify the drained sandbox: %+v", client.exits)
	}
	if len(client.spawnRequests) != 1 {
		t.Fatalf("spawn requests = %+v", client.spawnRequests)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["child"], ","); got != "pending,running" {
		t.Fatalf("a waiting call_app must not publish a failed fact: %v", observed["child"])
	}
	if got := strings.Join(observed["left"], ","); got != "pending,running,failed" {
		t.Fatalf("drained sibling states = %v", observed["left"])
	}
	for _, nodeID := range []string{"join", "end"} {
		if len(observed[nodeID]) != 0 {
			t.Fatalf("%s must not run or be skipped by a waiting hand-over: %v",
				nodeID, observed[nodeID])
		}
	}
}

// TestGraphCallAppFailureDrainsStartedSiblings proves the call_app failure
// decision follows the same scheduler ownership: the node publishes only its own
// failed fact and returns; the scheduler stops the started sandbox sibling, waits
// for its cleanup proof, emits the skips and commits exactly one failed result and
// one exit without a success proposal.
func TestGraphCallAppFailureDrainsStartedSiblings(t *testing.T) {
	document := definitionDocument(t, graphCallAppWaitingDefinition)
	document.ToolArtifacts = []manifestToolArtifact{{
		ID: PresetToolID, ImageDigest: "sha256:" + strings.Repeat("bb", 32),
	}}
	document.SubappVersions = []string{testSubappID}
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{}`)
	box := &waitingSiblingSandbox{base: "unit-test-1", started: make(chan struct{})}

	var committedBeforeStop bool
	client.onSubmitResult = func(proposal Proposal) {
		if proposal.SuggestedState != "failed" {
			return
		}
		if !box.stopped.Load() {
			committedBeforeStop = true
		}
	}
	client.spawnChild = func(SpawnChildRequest) (*ChildRun, error) {
		<-box.started
		return &ChildRun{ID: testChildRunID, State: "failed",
			ErrorCode: "sandbox_unavailable"}, nil
	}
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox())
	deps.Sandbox = box

	report := Run(context.Background(), deps)
	if report.Outcome != "failed" || report.ErrorCode != "sandbox_unavailable" {
		t.Fatalf("report = %+v", report)
	}
	if committedBeforeStop {
		t.Fatal("the failed proposal was submitted before the started sibling stopped")
	}
	if !box.stopped.Load() {
		t.Fatal("the sibling never observed the fail-fast cancellation")
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "failed" ||
		client.proposals[0].ErrorCode != "sandbox_unavailable" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	if len(client.exits) != 1 || client.exits[0].Evidence["sandbox_started"] != true {
		t.Fatalf("the exit must certify the drained sandbox: %+v", client.exits)
	}
	observed := observedNodeStates(client)
	for _, nodeID := range []string{"child", "left"} {
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
		t.Fatalf("the join must not run after a failure: %+v", rows)
	}
}

// TestGraphCallAppReusesSealedNodeOutputOnRecovery proves the recovery seam still
// applies to the call_app node after the explicit-context migration: a valid
// sealed output is reused byte-for-byte and no child is spawned or read again.
func TestGraphCallAppReusesSealedNodeOutputOnRecovery(t *testing.T) {
	document := definitionDocument(t, graphCallAppDefinitionJSON)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"requires_approval":true}`)
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox())
	deps.NodeOutputs = &fakeNodeOutputs{data: map[string][]byte{
		"start":  []byte(`{"requires_approval":true}`),
		"choose": []byte(`{"port":"yes","matched_case":0}`),
		"child":  []byte(`{"answer":"sealed"}`),
	}}

	report := Run(context.Background(), deps)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.spawnRequests) != 0 || len(client.childReads) != 0 {
		t.Fatalf("a reused call_app still delegated: spawns = %+v reads = %+v",
			client.spawnRequests, client.childReads)
	}
	if got := strings.Join(observedNodeStates(client)["child"], ","); got != "succeeded" {
		t.Fatalf("a reused node reports only its terminal fact: %v",
			observedNodeStates(client)["child"])
	}
	results := artifactData(t, client, "join")["results"].(map[string]any)
	child, _ := results["child"].(map[string]any)
	value, _ := child["value"].(map[string]any)
	if value == nil || value["answer"] != "sealed" {
		t.Fatalf("the sealed output was not reused: %v", child)
	}
}

// TestLinearCallAppKeepsTheFixedVersionAndTheExplicitBindingBoundary proves the
// linear call_app node sends exactly the sealed version and the declared bindings:
// the parent input field that is not mapped never crosses the child boundary.
func TestLinearCallAppKeepsTheFixedVersionAndTheExplicitBindingBoundary(t *testing.T) {
	definition := callAppDefinition(testSubappID)
	definition["input_schema"] = map[string]any{
		"type": "object",
		"properties": map[string]any{
			"source":  map[string]any{"type": "string"},
			"history": map[string]any{"type": "string"},
		},
		"required": []any{"source"}, "additionalProperties": true,
	}
	nodes := definition["nodes"].([]any)
	childNode := nodes[1].(map[string]any)
	childNode["inputs"] = map[string]any{
		"source": map[string]any{"ref": "input#/source"},
		"fixed":  map[string]any{"literal": "declared-only"},
	}
	childNode["output_schema"] = map[string]any{
		"type": "object", "properties": map[string]any{"echo": map[string]any{"type": "string"}},
		"required": []any{"echo"}, "additionalProperties": false,
	}

	client, _, deps := delegationClient(t, definition, []string{testSubappID})
	client.runInput = []byte(`{"source":"parent-value","history":"PARENT-HISTORY-MARKER"}`)
	client.spawnChild = func(request SpawnChildRequest) (*ChildRun, error) {
		if request.ApplicationVersionID != testSubappID {
			t.Errorf("spawn version = %q", request.ApplicationVersionID)
		}
		if len(request.Input) != 2 || request.Input["source"] != "parent-value" ||
			request.Input["fixed"] != "declared-only" {
			t.Errorf("the child input is not exactly the declared bindings: %+v", request.Input)
		}
		return &ChildRun{ID: testChildRunID, State: "succeeded"}, nil
	}
	client.childResult = func(string) (*ChildResult, error) {
		return &ChildResult{State: "succeeded", Output: map[string]any{"echo": "from-child"}}, nil
	}

	report := Run(context.Background(), deps)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.spawnRequests) != 1 {
		t.Fatalf("spawn requests = %+v", client.spawnRequests)
	}
	var final map[string]any
	for _, upload := range client.uploads {
		if upload.Request.Purpose == "final_output" {
			if err := json.Unmarshal(upload.Content, &final); err != nil {
				t.Fatalf("final output is not JSON: %v", err)
			}
		}
	}
	if final["echo"] != "from-child" {
		t.Fatalf("final output = %v", final)
	}
	found := false
	for _, result := range client.nodeResults {
		if result.NodeID == "child" && result.Label == "child-run:"+testChildRunID {
			found = true
		}
	}
	if !found {
		t.Fatalf("the child result was not reported as the node data: %+v", client.nodeResults)
	}
}
