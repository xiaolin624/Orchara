package presetexec

// C4.03/T154: the scheduler regression matrix, observed through the executor's
// public Run seam.
//
// Removing the fake graph-as-linear view is a structural change: the agent and
// call_app nodes already return typed local outcomes, so no production behavior
// may move. These tests close the scheduler matrix the ticket names explicitly:
//
//   - every attempt publishes its own skipped facts exactly once (a waiting
//     attempt publishes none; the resumed attempt publishes the skip);
//   - an activated join runs exactly once per activation;
//   - a cancellation decision drains the started sibling before the one exit is
//     certified, and never proposes a result;
//   - two concurrent branch failures still commit exactly one final proposal;
//   - recovery reuses valid sealed outputs without inventing a completed node
//     for a branch the activation rule skipped, and never treats a malformed
//     sealed output as completed.
//
// The real PostgreSQL + mTLS + runsc evidence lives in product/tests/f04b
// (T083/T085/T087/T089/T091 and the C4.03 module); these tests pin the
// scheduler decisions deterministically with the existing test seams.

import (
	"context"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"orchara/platform/internal/sandbox"
)

// approvalThenConditionDefinition parks an approval before the already-covered
// condition/join region:
//
//	start -> approve1 -> choose --yes--> left  -> join -> end
//	                            \--no---> right -> join
//
// The first attempt parks at approve1; the resumed attempt publishes the one
// skip of the unselected branch.
const approvalThenConditionDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object"},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
    {"id":"approve1","type":"approval",
     "inputs":{"proposal":{"literal":{"subject":"order-42"}}},
     "config":{"title":"Approve the graph","expires_seconds":600},
     "output_schema":{"type":"object"}},
    {"id":"choose","type":"condition","inputs":{"requires_approval":{"ref":"input#/requires_approval"}},
     "config":{"join_id":"join","default_port":"no",
               "cases":[{"input":"requires_approval","op":"eq","value":true,"port":"yes"}]},
     "output_schema":{"type":"object"}},
    {"id":"left","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"11111111-1111-4111-8111-111111111111"},
     "output_schema":{"type":"object"}},
    {"id":"right","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"11111111-1111-4111-8111-111111111111"},
     "output_schema":{"type":"object"}},
    {"id":"join","type":"join","inputs":{},
     "config":{"fork_id":"choose","mode":"all_active",
               "required_sources":["left","right"],"optional_sources":[]},
     "output_schema":{"type":"object"}},
    {"id":"end","type":"end","inputs":{"results":{"ref":"node:join#/results"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [
    {"source":"start","target":"approve1"},
    {"source":"approve1","target":"choose"},
    {"source":"choose","target":"left","port":"yes"},
    {"source":"choose","target":"right","port":"no"},
    {"source":"left","target":"join"},
    {"source":"right","target":"join"},
    {"source":"join","target":"end"}
  ],
  "policy": {"max_active_seconds":900,"max_child_depth":3,"max_child_calls":12,
             "budget_limit":"1.00","currency":"USD"}
}`

// TestGraphRunPublishesEachAttemptsSkipsOnce closes the per-attempt skip rule:
// a waiting attempt leaves the future nodes unsettled and publishes no skipped
// fact; the resumed attempt publishes the unselected branch's skip exactly once.
func TestGraphRunPublishesEachAttemptsSkipsOnce(t *testing.T) {
	// Attempt 1: the approval is still pending, so the graph parks before choose.
	pendingClient, pendingBox, waiting := runApprovalAttempt(
		t, approvalThenConditionDefinition, `{"requires_approval":true}`,
		[]*NodeApproval{{
			ID:           "11111111-1111-4111-8111-111111111111",
			State:        "pending",
			ParamsSHA256: strings.Repeat("a", 64),
			ExpiresAt:    time.Now().Add(10 * time.Minute),
		}},
	)
	if waiting.Outcome != "waiting_approval" || waiting.ErrorCode != CodeApprovalRequired {
		t.Fatalf("first attempt report = %+v", waiting)
	}
	if pendingBox.calls != 0 {
		t.Fatalf("the waiting attempt ran a sandbox: %d", pendingBox.calls)
	}
	pendingStates := observedNodeStates(pendingClient)
	for _, nodeID := range []string{"choose", "left", "right", "join", "end"} {
		if states, present := pendingStates[nodeID]; present {
			t.Fatalf("a waiting attempt published %s states %v; it must not skip or run them",
				nodeID, states)
		}
	}
	if len(pendingClient.proposals) != 1 ||
		pendingClient.proposals[0].SuggestedState != "waiting_approval" {
		t.Fatalf("the waiting attempt must submit exactly one waiting proposal: %+v",
			pendingClient.proposals)
	}
	if len(pendingClient.exits) != 1 {
		t.Fatalf("the waiting attempt must certify exactly one exit: %+v", pendingClient.exits)
	}

	// Attempt 2: the approval is decided. choose selects "yes", so right is
	// skipped exactly once and the join/end run exactly once.
	document := definitionDocument(t, approvalThenConditionDefinition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"requires_approval":true}`)
	client.nodeApprovalScript = []*NodeApproval{{
		ID:           "11111111-1111-4111-8111-111111111111",
		State:        "approved",
		ParamsSHA256: strings.Repeat("a", 64),
		ExpiresAt:    time.Now().Add(10 * time.Minute),
		DecidedAt:    decidedAtValue(),
	}}
	box := successSandbox()
	report := Run(context.Background(),
		baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("resumed attempt report = %+v", report)
	}
	if box.calls != 1 {
		t.Fatalf("resumed attempt sandbox calls = %d, want only the selected branch", box.calls)
	}
	states := observedNodeStates(client)
	if got := strings.Join(states["right"], ","); got != "skipped" {
		t.Fatalf("the unselected branch must be skipped exactly once in the resumed attempt: %v",
			states["right"])
	}
	for nodeID, want := range map[string]string{
		"approve1": "pending,running,succeeded",
		"choose":   "pending,running,succeeded",
		"left":     "pending,running,succeeded",
		"join":     "pending,running,succeeded",
		"end":      "pending,running,succeeded",
	} {
		if got := strings.Join(states[nodeID], ","); got != want {
			t.Fatalf("%s states = %v, want %s", nodeID, states[nodeID], want)
		}
	}
	if rows := nodeResultRows(client, "join"); len(rows) != 1 {
		t.Fatalf("the join must run exactly once per activation: %+v", rows)
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "succeeded" {
		t.Fatalf("the resumed attempt must submit exactly one final proposal: %+v",
			client.proposals)
	}
	if len(client.exits) != 1 {
		t.Fatalf("the resumed attempt must certify exactly one exit: %+v", client.exits)
	}
}

// TestGraphRunCancellationDrainsStartedSiblings closes the cancellation half of
// the drain matrix for a non-delegation node: a python branch observes the cancel
// while its sibling is still inside a started sandbox. The scheduler must stop and
// wait for the sibling, publish the skips, certify one exit and propose nothing.
func TestGraphRunCancellationDrainsStartedSiblings(t *testing.T) {
	leftStarted := make(chan struct{})
	var leftStopped atomic.Bool
	box := &branchSandbox{
		unitBase: "unit-test-1",
		scripts: map[string]func(context.Context) (sandbox.Outcome, error){
			"left": func(ctx context.Context) (sandbox.Outcome, error) {
				close(leftStarted)
				<-ctx.Done()
				// Prove the scheduler waits for the sandbox cleanup, not only for
				// the cancel signal.
				time.Sleep(20 * time.Millisecond)
				leftStopped.Store(true)
				return sandbox.Outcome{
					SandboxID: "unit-test-1", ExecutionUnitID: "unit-test-1",
					SandboxStarted: true, Cancelled: true, CleanupVerified: true,
					Cleanup: sandbox.CleanupFacts{WorkspaceRemoved: true},
				}, sandbox.ErrCancelled
			},
			"right": func(context.Context) (sandbox.Outcome, error) {
				<-leftStarted
				return sandbox.Outcome{
					SandboxID: "unit-test-1", ExecutionUnitID: "unit-test-1",
					SandboxStarted: true, Cancelled: true, CleanupVerified: true,
					Cleanup: sandbox.CleanupFacts{WorkspaceRemoved: true},
				}, sandbox.ErrCancelled
			},
		},
	}

	document := definitionDocument(t, parallelDefinition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{}`)
	var committedBeforeStop atomic.Bool
	client.onConfirmExit = func(map[string]any) {
		if !leftStopped.Load() {
			committedBeforeStop.Store(true)
		}
	}
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox())
	deps.Sandbox = box

	report := Run(context.Background(), deps)
	if report.Outcome != "cancelled" || report.ErrorCode != "cancel_requested" {
		t.Fatalf("report = %+v", report)
	}
	if !leftStopped.Load() {
		t.Fatal("the started sibling never observed the cancellation")
	}
	if committedBeforeStop.Load() {
		t.Fatal("the exit was certified before the started sibling stopped")
	}
	if len(client.proposals) != 0 {
		t.Fatalf("a cancellation must never propose a result: %+v", client.proposals)
	}
	if len(client.exits) != 1 || client.exits[0].Evidence["sandbox_started"] != true {
		t.Fatalf("the cancellation must certify exactly one started exit: %+v", client.exits)
	}
	states := observedNodeStates(client)
	for _, nodeID := range []string{"left", "right"} {
		if got := strings.Join(states[nodeID], ","); got != "pending,running,failed" {
			t.Fatalf("%s states = %v", nodeID, states[nodeID])
		}
	}
	for _, nodeID := range []string{"join", "end"} {
		if got := strings.Join(states[nodeID], ","); got != "skipped" {
			t.Fatalf("%s states = %v, want one skipped fact", nodeID, states[nodeID])
		}
	}
	if rows := nodeResultRows(client, "join"); len(rows) != 0 {
		t.Fatalf("the join must not run after a cancellation: %+v", rows)
	}
}

// twoFailureSandbox holds both branch attempts until they overlapped, then lets
// both fail. The scheduler must commit exactly one failed proposal and one exit.
type twoFailureSandbox struct {
	arrived chan struct{}
	release chan struct{}
}

func newTwoFailureSandbox() *twoFailureSandbox {
	box := &twoFailureSandbox{
		arrived: make(chan struct{}, 2),
		release: make(chan struct{}),
	}
	go func() {
		for index := 0; index < 2; index++ {
			select {
			case <-box.arrived:
			case <-time.After(5 * time.Second):
				return
			}
		}
		close(box.release)
	}()
	return box
}

func (b *twoFailureSandbox) Run(
	ctx context.Context, _ sandbox.Request, _ sandbox.Control,
) (sandbox.Outcome, error) {
	select {
	case b.arrived <- struct{}{}:
	case <-ctx.Done():
		return sandbox.Outcome{}, ctx.Err()
	}
	select {
	case <-b.release:
	case <-ctx.Done():
		return sandbox.Outcome{}, ctx.Err()
	case <-time.After(5 * time.Second):
		return sandbox.Outcome{}, context.DeadlineExceeded
	}
	return sandbox.Outcome{
		SandboxID: "unit-test-1", ExecutionUnitID: "unit-test-1",
		SandboxStarted: true, ExitCode: 2, CleanupVerified: true,
		Cleanup: sandbox.CleanupFacts{WorkspaceRemoved: true},
	}, nil
}

// TestGraphRunCommitsOneProposalWhenTwoBranchesFailTogether proves the unique
// terminal proposal rule under the concurrent failure race: both branches fail
// after overlapping, but the run commits one failed result and one exit.
func TestGraphRunCommitsOneProposalWhenTwoBranchesFailTogether(t *testing.T) {
	box := newTwoFailureSandbox()
	document := definitionDocument(t, parallelDefinition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{}`)
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox())
	deps.Sandbox = box

	report := Run(context.Background(), deps)
	if report.Outcome != "failed" || report.ErrorCode != CodeToolCrash {
		t.Fatalf("report = %+v", report)
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "failed" ||
		client.proposals[0].ErrorCode != CodeToolCrash {
		t.Fatalf("two concurrent failures must commit exactly one failed proposal: %+v",
			client.proposals)
	}
	if len(client.exits) != 1 {
		t.Fatalf("two concurrent failures must certify exactly one exit: %+v", client.exits)
	}
	states := observedNodeStates(client)
	for _, nodeID := range []string{"join", "end"} {
		if got := strings.Join(states[nodeID], ","); got != "skipped" {
			t.Fatalf("%s states = %v, want one skipped fact", nodeID, states[nodeID])
		}
	}
	if rows := nodeResultRows(client, "join"); len(rows) != 0 {
		t.Fatalf("the join must not run after a failed region: %+v", rows)
	}
}

// TestGraphRunReusedOutputsNeverInventACompletedSkippedNode proves recovery reuses
// the sealed outputs of the nodes the activation rule runs, while an output that
// exists for an unselected branch is not treated as a completed node: that branch
// keeps its one skipped fact and produces no node data.
func TestGraphRunReusedOutputsNeverInventACompletedSkippedNode(t *testing.T) {
	document := definitionDocument(t, flatConditionDefinition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"requires_approval":true}`)
	box := successSandbox()
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box)
	deps.NodeOutputs = &fakeNodeOutputs{data: map[string][]byte{
		"start":  []byte(`{"requires_approval":true}`),
		"choose": []byte(`{"port":"yes","matched_case":0}`),
		"left":   []byte(`{"total":"9.00","regions":{"east":"9.00"},"order_count":1}`),
		// A sealed-looking output for the branch the selected port never
		// activates. It must not make the node "completed".
		"right": []byte(`{"total":"999.00"}`),
	}}

	report := Run(context.Background(), deps)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 0 {
		t.Fatalf("sandbox calls = %d; every activated node was sealed", box.calls)
	}
	states := observedNodeStates(client)
	if got := strings.Join(states["right"], ","); got != "skipped" {
		t.Fatalf("an unselected branch must stay skipped, not completed: %v", states["right"])
	}
	if rows := nodeResultRows(client, "right"); len(rows) != 0 {
		t.Fatalf("a skipped node must not gain a node result row: %+v", rows)
	}
	results := artifactData(t, client, "join")["results"].(map[string]any)
	left, _ := results["left"].(map[string]any)
	if value, _ := left["value"].(map[string]any); value["total"] != "9.00" {
		t.Fatalf("the sealed output was not reused: %v", left)
	}
	if right := results["right"].(map[string]any); right["status"] != "skipped" {
		t.Fatalf("a fabricated output must not enter the join as success: %v", right)
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "succeeded" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	if len(client.exits) != 1 {
		t.Fatalf("exits = %+v", client.exits)
	}
}

// TestGraphRunMalformedSealedOutputIsNotTreatedAsCompleted proves an unreadable
// sealed output degrades to re-execution: the node runs again and publishes fresh
// data instead of being marked completed from the bad object.
func TestGraphRunMalformedSealedOutputIsNotTreatedAsCompleted(t *testing.T) {
	document := definitionDocument(t, flatConditionDefinition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"requires_approval":true}`)
	box := successSandbox()
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box)
	deps.NodeOutputs = &fakeNodeOutputs{data: map[string][]byte{
		"start": []byte(`{"requires_approval":true,"artifact":` +
			`{"artifact_id":"99999999-8888-4777-8666-555555555555","media_type":"text/csv"}}`),
		"choose": []byte(`{"port":"yes","matched_case":0}`),
		// Not a JSON object: the recovery seam must refuse to reuse it.
		"left": []byte(`[1,2]`),
	}}

	report := Run(context.Background(), deps)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 1 {
		t.Fatalf("sandbox calls = %d; a malformed sealed output must re-run its node", box.calls)
	}
	if got := strings.Join(observedNodeStates(client)["left"], ","); got != "pending,running,succeeded" {
		t.Fatalf("left states = %v, want a real execution", observedNodeStates(client)["left"])
	}
	results := artifactData(t, client, "join")["results"].(map[string]any)
	left, _ := results["left"].(map[string]any)
	value, _ := left["value"].(map[string]any)
	if value == nil || value["total"] != "200.00" {
		t.Fatalf("the fresh output must replace the malformed sealed one: %v", left)
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "succeeded" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	if len(client.exits) != 1 {
		t.Fatalf("exits = %+v", client.exits)
	}
}
