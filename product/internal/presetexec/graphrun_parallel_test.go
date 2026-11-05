package presetexec

// F04B/T085: concurrent branch scheduling, optional/required convergence and
// fail-fast cancellation.
//
// The graph scheduler starts every ready node on its own goroutine, so two
// independent branches really overlap. These tests prove that with controllable
// blocking sandboxes (a branch cannot finish until the other has started), pin the
// optional error-code allowlist, and prove that a required failure cancels the
// other in-flight branch and waits for it to stop before the run fails.

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

// ---------------------------------------------------------------------------
// fixture helpers
// ---------------------------------------------------------------------------

// variantDefinition parses a sealed fixture, lets the test mutate it and returns
// the sealed definition JSON again.
func variantDefinition(t *testing.T, raw string, mutate func(*manifestDefinition)) string {
	t.Helper()
	var definition manifestDefinition
	if err := json.Unmarshal([]byte(raw), &definition); err != nil {
		t.Fatalf("fixture is not a sealed definition: %v", err)
	}
	mutate(&definition)
	payload, err := json.Marshal(definition)
	if err != nil {
		t.Fatalf("mutated fixture cannot be encoded: %v", err)
	}
	return string(payload)
}

func definitionNode(t *testing.T, definition *manifestDefinition, id string) *manifestNode {
	t.Helper()
	for index := range definition.Nodes {
		if definition.Nodes[index].ID == id {
			return &definition.Nodes[index]
		}
	}
	t.Fatalf("fixture has no node %q", id)
	return nil
}

func setJoinSources(
	t *testing.T, definition *manifestDefinition, joinID string,
	required, optional []string,
) {
	t.Helper()
	node := definitionNode(t, definition, joinID)
	var config map[string]any
	if err := json.Unmarshal(node.Config, &config); err != nil {
		t.Fatalf("join config is unreadable: %v", err)
	}
	config["required_sources"] = required
	config["optional_sources"] = optional
	payload, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	node.Config = payload
}

func setNodeOutputSchema(t *testing.T, definition *manifestDefinition, nodeID string, schema any) {
	t.Helper()
	node := definitionNode(t, definition, nodeID)
	payload, err := json.Marshal(schema)
	if err != nil {
		t.Fatal(err)
	}
	node.OutputSchema = payload
}

// missingKeySchema never matches the preset tool's output document: the required
// key is not produced. The node fails with node_output_invalid after its sandbox
// ran, which is exactly an explicit pure-compute error.
func missingKeySchema() map[string]any {
	return map[string]any{"type": "object", "required": []any{"never_produced"}}
}

// optionalFailureDefinition is parallelDefinition with left required and right
// optional, and right's sealed output_schema rejecting the tool output.
func optionalFailureDefinition(t *testing.T) string {
	t.Helper()
	return variantDefinition(t, parallelDefinition, func(definition *manifestDefinition) {
		setJoinSources(t, definition, "join", []string{"left"}, []string{"right"})
		setNodeOutputSchema(t, definition, "right", missingKeySchema())
	})
}

// requiredFailureDefinition makes both branches required; right's node fails.
func requiredFailureDefinition(t *testing.T) string {
	t.Helper()
	return variantDefinition(t, parallelDefinition, func(definition *manifestDefinition) {
		setNodeOutputSchema(t, definition, "right", missingKeySchema())
	})
}

// parallelAgentOptionalDefinition forks onto a python branch (required) and an
// agent branch (optional): an agent failure must never be folded into the join.
const parallelAgentOptionalDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object"},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
    {"id":"left","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"` + PresetToolID + `"},"output_schema":{"type":"object"}},
    {"id":"analysis","type":"agent","inputs":{"task":{"literal":"graph agent"}},
     "output_schema":{"type":"object","properties":{"text":{"type":"string"}},
                      "required":["text"],"additionalProperties":false},
     "config":{"model_profile_version":"` + testModelProfileID + `",
               "instructions":"Summarise the task.",
               "max_turns":2,
               "tool_versions":["` + PresetToolID + `"],
               "subapp_versions":[]}},
    {"id":"join","type":"join","inputs":{},
     "config":{"fork_id":"start","mode":"all_active",
               "required_sources":["left"],"optional_sources":["analysis"]},
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

// parallelInConditionDefinition nests a parallel fork inside a condition's "yes"
// branch: start -> choose --yes--> fork -> (deepA|deepB) -> join2 -> join -> end,
// with choose --no--> direct -> join.
const parallelInConditionDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object","properties":{"mode":{"type":"string"}},
                   "required":["mode"],"additionalProperties":false},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
    {"id":"choose","type":"condition","inputs":{"mode":{"ref":"input#/mode"}},
     "config":{"join_id":"join","default_port":"no",
               "cases":[{"input":"mode","op":"eq","value":"deep","port":"yes"}]},
     "output_schema":{"type":"object"}},
    {"id":"fork","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"` + PresetToolID + `"},"output_schema":{"type":"object"}},
    {"id":"deepA","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"` + PresetToolID + `"},"output_schema":{"type":"object"}},
    {"id":"deepB","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"` + PresetToolID + `"},"output_schema":{"type":"object"}},
    {"id":"join2","type":"join","inputs":{},
     "config":{"fork_id":"fork","mode":"all_active",
               "required_sources":["deepA","deepB"],"optional_sources":[]},
     "output_schema":{"type":"object"}},
    {"id":"direct","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"` + PresetToolID + `"},"output_schema":{"type":"object"}},
    {"id":"join","type":"join","inputs":{},
     "config":{"fork_id":"choose","mode":"all_active",
               "required_sources":["join2","direct"],"optional_sources":[]},
     "output_schema":{"type":"object"}},
    {"id":"end","type":"end","inputs":{"results":{"ref":"node:join#/results"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [
    {"source":"start","target":"choose"},
    {"source":"choose","target":"fork","port":"yes"},
    {"source":"choose","target":"direct","port":"no"},
    {"source":"fork","target":"deepA"},
    {"source":"fork","target":"deepB"},
    {"source":"deepA","target":"join2"},
    {"source":"deepB","target":"join2"},
    {"source":"join2","target":"join"},
    {"source":"direct","target":"join"},
    {"source":"join","target":"end"}
  ]
}`

// ---------------------------------------------------------------------------
// sandbox seams
// ---------------------------------------------------------------------------

// barrierSandbox holds every call until `parties` calls are in flight. Sequential
// execution therefore times out (the first call waits for a second that never
// starts); concurrent execution releases both.
type barrierSandbox struct {
	mu      sync.Mutex
	calls   int
	arrived chan struct{}
	release chan struct{}
	outcome sandbox.Outcome
}

func newBarrierSandbox(parties int) *barrierSandbox {
	box := &barrierSandbox{
		arrived: make(chan struct{}, parties),
		release: make(chan struct{}),
		outcome: successSandbox().outcome,
	}
	go func() {
		for index := 0; index < parties; index++ {
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

func (b *barrierSandbox) Run(
	ctx context.Context, _ sandbox.Request, _ sandbox.Control,
) (sandbox.Outcome, error) {
	b.mu.Lock()
	b.calls++
	b.mu.Unlock()
	select {
	case b.arrived <- struct{}{}:
	case <-ctx.Done():
		return sandbox.Outcome{}, ctx.Err()
	}
	select {
	case <-b.release:
		return b.outcome, nil
	case <-ctx.Done():
		return sandbox.Outcome{}, ctx.Err()
	case <-time.After(5 * time.Second):
		return sandbox.Outcome{}, errors.New(
			"the two branches never ran at the same time: each sandbox call waited for the other")
	}
}

// failFastSandbox runs both branches into a barrier, then lets the "right"
// branch fail and holds "left" until its context is cancelled.
type failFastSandbox struct {
	unitLeft    string
	mu          sync.Mutex
	calls       int
	arrived     chan struct{}
	release     chan struct{}
	leftStopped atomic.Bool
}

func newFailFastSandbox(unitLeft string) *failFastSandbox {
	box := &failFastSandbox{
		unitLeft: unitLeft,
		arrived:  make(chan struct{}, 2),
		release:  make(chan struct{}),
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

func (b *failFastSandbox) Run(
	ctx context.Context, request sandbox.Request, _ sandbox.Control,
) (sandbox.Outcome, error) {
	b.mu.Lock()
	b.calls++
	b.mu.Unlock()
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
		return sandbox.Outcome{}, errors.New("the branches never overlapped")
	}
	if request.ExecutionUnitID == b.unitLeft {
		<-ctx.Done()
		// Prove the scheduler really waited for the unit to stop, not merely sent
		// the cancel and committed the run.
		time.Sleep(25 * time.Millisecond)
		b.leftStopped.Store(true)
		return sandbox.Outcome{}, sandbox.ErrCancelled
	}
	return successSandbox().outcome, nil
}

// branchSandbox routes a call to a script by the node-derived sandbox unit id.
type branchSandbox struct {
	unitBase string
	scripts  map[string]func(ctx context.Context) (sandbox.Outcome, error)
}

func (b *branchSandbox) Run(
	ctx context.Context, request sandbox.Request, _ sandbox.Control,
) (sandbox.Outcome, error) {
	for nodeID, script := range b.scripts {
		if request.ExecutionUnitID == graphSandboxUnit(b.unitBase, nodeID) {
			return script(ctx)
		}
	}
	return sandbox.Outcome{}, errors.New("the sandbox call names no scripted node")
}

// runGraphAttemptWithSandbox runs one graph attempt with an arbitrary Sandbox
// seam (the existing runGraphAttempt keeps the shared fake).
func runGraphAttemptWithSandbox(
	t *testing.T, definitionRaw, runInput string, box Sandbox,
) (*fakeClient, Report) {
	t.Helper()
	document := definitionDocument(t, definitionRaw)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, runInput)
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox())
	deps.Sandbox = box
	return client, Run(context.Background(), deps)
}

// ---------------------------------------------------------------------------
// concurrency
// ---------------------------------------------------------------------------

func TestGraphRunRunsIndependentBranchesConcurrently(t *testing.T) {
	box := newBarrierSandbox(2)
	client, report := runGraphAttemptWithSandbox(t, parallelDefinition, `{}`, box)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("two ready branches must run at the same time; report = %+v", report)
	}
	if box.calls != 2 {
		t.Fatalf("sandbox calls = %d, want both branches", box.calls)
	}
	results := artifactData(t, client, "join")["results"].(map[string]any)
	for _, source := range []string{"left", "right"} {
		entry, _ := results[source].(map[string]any)
		if entry["status"] != "succeeded" {
			t.Fatalf("source %s envelope = %v", source, entry)
		}
	}
	if rows := nodeResultRows(client, "join"); len(rows) != 1 {
		t.Fatalf("join rows = %+v", rows)
	}
}

// ---------------------------------------------------------------------------
// optional failure convergence
// ---------------------------------------------------------------------------

func TestGraphRunFoldsAnOptionalFailureIntoTheJoinEnvelope(t *testing.T) {
	box := successSandbox()
	client, report := runGraphAttempt(t, optionalFailureDefinition(t), `{}`, box)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("an optional pure-compute failure must not fail the run; report = %+v", report)
	}
	if box.calls != 2 {
		t.Fatalf("sandbox calls = %d, want both branches to run", box.calls)
	}

	observed := observedNodeStates(client)
	if got := strings.Join(observed["right"], ","); got != "pending,running,failed" {
		t.Fatalf("optional branch states = %v", observed["right"])
	}
	if got := strings.Join(observed["left"], ","); got != "pending,running,succeeded" {
		t.Fatalf("required branch states = %v", observed["left"])
	}
	if got := strings.Join(observed["join"], ","); got != "pending,running,succeeded" {
		t.Fatalf("join states = %v", got)
	}

	joinData := artifactData(t, client, "join")
	results, _ := joinData["results"].(map[string]any)
	right, _ := results["right"].(map[string]any)
	if right["status"] != "failed" || right["error_code"] != CodeNodeOutputInvalid {
		t.Fatalf("optional failed entry = %v", right)
	}
	if _, present := right["value"]; present {
		t.Fatalf("an optional failure must not carry a value: %v", right)
	}
	left, _ := results["left"].(map[string]any)
	if left["status"] != "succeeded" || left["value"] == nil {
		t.Fatalf("required entry = %v", left)
	}
	if len(nodeResultRows(client, "join")) != 1 {
		t.Fatalf("the join must run exactly once: %+v", client.nodeResults)
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "succeeded" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	var final recordedUpload
	for _, upload := range client.uploads {
		if upload.Request.Purpose == "final_output" {
			final = upload
		}
	}
	var finalDocument map[string]any
	if err := json.Unmarshal(final.Content, &finalDocument); err != nil {
		t.Fatalf("final output is not JSON: %v", err)
	}
	if finalDocument["results"].(map[string]any)["right"].(map[string]any)["status"] != "failed" {
		t.Fatalf("the converged error data must reach the final output: %v", finalDocument)
	}
}

func TestGraphRunOptionalNeverSwallowsAnAgentFailure(t *testing.T) {
	document := definitionDocument(t, parallelAgentOptionalDefinition)
	document.ToolArtifacts = []manifestToolArtifact{{
		ID: PresetToolID, ImageDigest: "sha256:" + strings.Repeat("bb", 32),
	}}
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{}`)
	client.invokeCapability = func(capabilities.Input) (*capabilities.Result, error) {
		return nil, capabilityError{code: capabilities.CodeModelUnavailable}
	}
	deps := agentDeps(t, client, successSandbox(), currentFacts())
	report := Run(context.Background(), deps)

	if report.ErrorCode != capabilities.CodeModelUnavailable {
		t.Fatalf("an optional agent failure must fail the run with its own code; report = %+v", report)
	}
	if report.Outcome != "failed" && report.Outcome != "refused" {
		t.Fatalf("report = %+v", report)
	}
	if rows := nodeResultRows(client, "join"); len(rows) != 0 {
		t.Fatalf("the join must not run after a non-absorbable failure: %+v", rows)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["analysis"], ","); got != "pending,running,failed" {
		t.Fatalf("agent node states = %v", observed["analysis"])
	}
	for _, nodeID := range []string{"join", "end"} {
		if got := strings.Join(observed[nodeID], ","); got != "skipped" {
			t.Fatalf("%s states = %v, want skipped", nodeID, observed[nodeID])
		}
	}
}

// capabilityError is a stable coded capability failure at the client seam.
type capabilityError struct{ code string }

func (e capabilityError) Error() string     { return e.code }
func (e capabilityError) ErrorCode() string { return e.code }

func TestGraphRunOptionalNeverSwallowsANonAllowlistedErrorCode(t *testing.T) {
	box := &branchSandbox{
		unitBase: "unit-test-1",
		scripts: map[string]func(context.Context) (sandbox.Outcome, error){
			"left": func(context.Context) (sandbox.Outcome, error) {
				return successSandbox().outcome, nil
			},
			"right": func(context.Context) (sandbox.Outcome, error) {
				// A sandbox persistence/cleanup failure is never folded into the
				// join envelope, even on an optional branch.
				return sandbox.Outcome{}, sandbox.ErrOrphan
			},
		},
	}
	client, report := runGraphAttemptWithSandbox(t, optionalFailureDefinition(t), `{}`, box)
	if report.ErrorCode != CodeSandboxOrphan {
		t.Fatalf("a sandbox_orphan failure must never be optional; report = %+v", report)
	}
	if rows := nodeResultRows(client, "join"); len(rows) != 0 {
		t.Fatalf("the join must not run after a non-absorbable failure: %+v", rows)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["join"], ","); got != "skipped" {
		t.Fatalf("join states = %v", observed["join"])
	}
	if got := strings.Join(observed["end"], ","); got != "skipped" {
		t.Fatalf("end states = %v", observed["end"])
	}
}

// ---------------------------------------------------------------------------
// required fail-fast
// ---------------------------------------------------------------------------

func TestGraphRunRequiredFailureCancelsTheOtherBranchAndNeverJoins(t *testing.T) {
	unitLeft := graphSandboxUnit("unit-test-1", "left")
	box := newFailFastSandbox(unitLeft)
	var stoppedWhenCommitted atomic.Bool
	committedBeforeStop := atomic.Bool{}

	document := definitionDocument(t, requiredFailureDefinition(t))
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{}`)
	client.onSubmitResult = func(proposal Proposal) {
		if proposal.SuggestedState != "failed" {
			return
		}
		if !box.leftStopped.Load() {
			committedBeforeStop.Store(true)
		} else {
			stoppedWhenCommitted.Store(true)
		}
	}
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox())
	deps.Sandbox = box

	report := Run(context.Background(), deps)
	if report.Outcome != "failed" || report.ErrorCode != CodeNodeOutputInvalid {
		t.Fatalf("report = %+v", report)
	}
	if !box.leftStopped.Load() {
		t.Fatal("the in-flight branch never observed the fail-fast cancellation")
	}
	if committedBeforeStop.Load() {
		t.Fatal("the run failure was committed before the other branch confirmed it stopped")
	}
	if !stoppedWhenCommitted.Load() {
		t.Fatal("the failed proposal was not recorded after the branch stopped")
	}
	if rows := nodeResultRows(client, "join"); len(rows) != 0 {
		t.Fatalf("join must not run after a required failure: %+v", rows)
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "failed" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["right"], ","); got != "pending,running,failed" {
		t.Fatalf("failing branch states = %v", observed["right"])
	}
	for _, nodeID := range []string{"join", "end"} {
		if got := strings.Join(observed[nodeID], ","); got != "skipped" {
			t.Fatalf("%s states = %v, want skipped", nodeID, observed[nodeID])
		}
	}
	// The cancelled branch records an interrupted terminal state, never success.
	leftStates := observed["left"]
	if len(leftStates) < 2 || leftStates[len(leftStates)-1] != "failed" {
		t.Fatalf("cancelled branch states = %v", leftStates)
	}
}

// ---------------------------------------------------------------------------
// nested regions
// ---------------------------------------------------------------------------

func TestGraphRunNestedParallelRegionInsideAConditionBranch(t *testing.T) {
	box := successSandbox()
	client, report := runGraphAttempt(t, parallelInConditionDefinition, `{"mode":"deep"}`, box)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 3 {
		t.Fatalf("sandbox calls = %d, want the fork and its two branches", box.calls)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["direct"], ","); got != "skipped" {
		t.Fatalf("unselected branch states = %v", observed["direct"])
	}
	inner := artifactData(t, client, "join2")["results"].(map[string]any)
	for _, source := range []string{"deepA", "deepB"} {
		entry, _ := inner[source].(map[string]any)
		if entry["status"] != "succeeded" {
			t.Fatalf("inner source %s envelope = %v", source, entry)
		}
	}
	outer := artifactData(t, client, "join")["results"].(map[string]any)
	if outer["join2"].(map[string]any)["status"] != "succeeded" {
		t.Fatalf("the inner join must converge before the parent region: %v", outer)
	}
	if outer["direct"].(map[string]any)["status"] != "skipped" {
		t.Fatalf("outer envelope = %v", outer)
	}
	if len(nodeResultRows(client, "join2")) != 1 || len(nodeResultRows(client, "join")) != 1 {
		t.Fatalf("each join must fire once: %+v", client.nodeResults)
	}
}

// ---------------------------------------------------------------------------
// join-once under replay
// ---------------------------------------------------------------------------

func TestGraphRunReplayReusesTheJoinWithoutFiringItAgain(t *testing.T) {
	envelope := `{"results":{"left":{"status":"succeeded","value":` +
		`{"total":"9.00","regions":{"east":"9.00"},"order_count":1}},` +
		`"right":{"status":"skipped"}}}`
	document := definitionDocument(t, flatConditionDefinition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"requires_approval":true}`)
	box := successSandbox()
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box)
	deps.NodeOutputs = &fakeNodeOutputs{data: map[string][]byte{
		"start":  []byte(`{"requires_approval":true}`),
		"choose": []byte(`{"port":"yes","matched_case":0}`),
		"left":   []byte(`{"total":"9.00","regions":{"east":"9.00"},"order_count":1}`),
		"join":   []byte(envelope),
	}}

	report := Run(context.Background(), deps)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 0 {
		t.Fatalf("sandbox calls = %d; a replayed run must reuse completed nodes", box.calls)
	}
	observed := observedNodeStates(client)
	for nodeID, states := range map[string]string{
		"start": "succeeded", "choose": "succeeded", "left": "succeeded", "join": "succeeded",
	} {
		if got := strings.Join(observed[nodeID], ","); got != states {
			t.Fatalf("%s states = %v, want the terminal fact only", nodeID, observed[nodeID])
		}
	}
	if got := strings.Join(observed["right"], ","); got != "skipped" {
		t.Fatalf("right states = %v", observed["right"])
	}
	if rows := nodeResultRows(client, "join"); len(rows) != 0 {
		t.Fatalf("a reused join must not record a second firing: %+v", rows)
	}
	var final recordedUpload
	for _, upload := range client.uploads {
		if upload.Request.Purpose == "final_output" {
			final = upload
		}
	}
	var finalDocument map[string]any
	if err := json.Unmarshal(final.Content, &finalDocument); err != nil {
		t.Fatalf("final output is not JSON: %v", err)
	}
	results, _ := finalDocument["results"].(map[string]any)
	if results["left"].(map[string]any)["value"].(map[string]any)["total"] != "9.00" {
		t.Fatalf("the reused envelope must be sealed as the final output: %v", finalDocument)
	}
	if results["right"].(map[string]any)["status"] != "skipped" {
		t.Fatalf("final output = %v", finalDocument)
	}
}
