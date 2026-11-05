package presetexec

// F04B/T083: the graph scheduler over one sealed definition.
//
// These are unit-level attempts of the real scheduler with the executor's
// protocol seams scripted (client, inputs, sandbox). The real PostgreSQL + mTLS +
// runsc evidence lives in product/tests/f04b; these tests pin the activation and
// skip semantics, the join-once output envelope and the recovery seam without
// paying for a full stack on every run.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
)

// ---------------------------------------------------------------------------
// fixtures
// ---------------------------------------------------------------------------

func graphLease(t *testing.T, document *manifestDocument) (*Lease, []byte) {
	t.Helper()
	payload, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(payload)
	return &Lease{
		JobID:           "job-graph",
		RunID:           "11111111-2222-4333-8444-555555555555",
		AttemptToken:    "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
		ProtocolVersion: ProtocolVersion,
		ManifestSHA256:  hex.EncodeToString(sum[:]),
		ManifestURL:     "http://object.invalid/get/manifest",
		InputArtifactID: "77777777-6666-4555-8444-333333333333",
		InputURL:        "http://object.invalid/get/run-input",
	}, payload
}

func graphClient(t *testing.T, lease *Lease, manifest []byte, runInput string) *fakeClient {
	t.Helper()
	return &fakeClient{
		lease:      lease,
		manifest:   manifest,
		inputs:     []InputArtifact{inputArtifact()},
		inputBytes: []byte(salesCSV),
		runInput:   []byte(runInput),
		runState:   "succeeded",
	}
}

func observedNodeStates(client *fakeClient) map[string][]string {
	observed := map[string][]string{}
	for _, event := range client.nodeEvents {
		observed[event.NodeID] = append(observed[event.NodeID], event.State)
	}
	return observed
}

func nodeResultRows(client *fakeClient, nodeID string) []NodeResult {
	rows := make([]NodeResult, 0, 2)
	for _, row := range client.nodeResults {
		if row.NodeID == nodeID {
			rows = append(rows, row)
		}
	}
	return rows
}

func uploadContent(t *testing.T, client *fakeClient, artifactID string) []byte {
	t.Helper()
	for _, upload := range client.uploads {
		if upload.Artifact == artifactID {
			return upload.Content
		}
	}
	t.Fatalf("no upload for artifact %q", artifactID)
	return nil
}

func artifactData(t *testing.T, client *fakeClient, nodeID string) map[string]any {
	t.Helper()
	rows := nodeResultRows(client, nodeID)
	for _, row := range rows {
		if row.Kind != "data" {
			continue
		}
		var document map[string]any
		if err := json.Unmarshal(uploadContent(t, client, row.ArtifactID), &document); err != nil {
			t.Fatalf("node %s data artifact is not JSON: %v", nodeID, err)
		}
		return document
	}
	t.Fatalf("node %s has no data artifact: %+v", nodeID, rows)
	return nil
}

// invalidActivationDefinition is a publish-valid graph whose join can never
// satisfy "at least one activated required source" when the default branch runs:
//
//	choose --yes--> left(required)  (skipped when the default port is selected)
//	choose --no---> right(optional) (activated, but not required)
//
// The publish validator only checks that the sources cover the real
// predecessors; the executor must fail closed with invalid_activation.
const invalidActivationDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object","properties":{"requires_approval":{"type":"boolean"}},
                   "required":["requires_approval"],"additionalProperties":false},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
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
               "required_sources":["left"],"optional_sources":["right"]},
     "output_schema":{"type":"object"}},
    {"id":"end","type":"end","inputs":{"results":{"ref":"node:join#/results"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [
    {"source":"start","target":"choose"},
    {"source":"choose","target":"left","port":"yes"},
    {"source":"choose","target":"right","port":"no"},
    {"source":"left","target":"join"},
    {"source":"right","target":"join"},
    {"source":"join","target":"end"}
  ],
  "policy": {"max_active_seconds":900,"max_child_depth":3,"max_child_calls":12,
             "budget_limit":"1.00","currency":"USD"}
}`

func runGraphAttempt(t *testing.T, definitionRaw string, runInput string, box *fakeSandbox) (*fakeClient, Report) {
	t.Helper()
	document := definitionDocument(t, definitionRaw)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, runInput)
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	return client, report
}

// ---------------------------------------------------------------------------
// condition branching
// ---------------------------------------------------------------------------

func TestGraphRunConditionSelectsOneBranchAndSkipsTheOther(t *testing.T) {
	box := successSandbox()
	client, report := runGraphAttempt(t, flatConditionDefinition,
		`{"requires_approval":true}`, box)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 1 {
		t.Fatalf("sandbox calls = %d, want only the selected branch", box.calls)
	}
	observed := observedNodeStates(client)
	want := map[string][]string{
		"start":  {"pending", "running", "succeeded"},
		"choose": {"pending", "running", "succeeded"},
		"left":   {"pending", "running", "succeeded"},
		// The unselected branch emits only its skipped fact; it never runs.
		"right": {"skipped"},
		"join":  {"pending", "running", "succeeded"},
		"end":   {"pending", "running", "succeeded"},
	}
	for nodeID, states := range want {
		if strings.Join(observed[nodeID], ",") != strings.Join(states, ",") {
			t.Fatalf("node %s states = %v, want %v", nodeID, observed[nodeID], states)
		}
	}

	// The condition's data artifact is the run-view convention.
	conditionData := artifactData(t, client, "choose")
	if conditionData["port"] != "yes" || conditionData["matched_case"] != float64(0) {
		t.Fatalf("condition data = %v", conditionData)
	}

	// The join ran exactly once and recorded exactly the branch states.
	joinRows := nodeResultRows(client, "join")
	if len(joinRows) != 1 || joinRows[0].Kind != "data" {
		t.Fatalf("join rows = %+v", joinRows)
	}
	joinData := artifactData(t, client, "join")
	results, ok := joinData["results"].(map[string]any)
	if !ok {
		t.Fatalf("join data has no results envelope: %v", joinData)
	}
	left, _ := results["left"].(map[string]any)
	right, _ := results["right"].(map[string]any)
	if left["status"] != "succeeded" {
		t.Fatalf("left envelope = %v", left)
	}
	leftValue, ok := left["value"].(map[string]any)
	if !ok || leftValue["total"] != "200.00" {
		t.Fatalf("left value = %v", left)
	}
	if right["status"] != "skipped" {
		t.Fatalf("right envelope = %v", right)
	}
	if _, present := right["value"]; present {
		t.Fatalf("a skipped source must have no value: %v", right)
	}

	// One join firing: one data row and one succeeded event.
	joinSucceeded := 0
	for _, event := range client.nodeEvents {
		if event.NodeID == "join" && event.State == "succeeded" {
			joinSucceeded++
		}
	}
	if joinSucceeded != 1 {
		t.Fatalf("join succeeded events = %d", joinSucceeded)
	}

	// The end node sealed the join envelope behind its one output.
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
	if _, ok := finalDocument["results"].(map[string]any); !ok {
		t.Fatalf("final output = %v", finalDocument)
	}
}

func TestGraphRunUsesTheDefaultPort(t *testing.T) {
	box := successSandbox()
	client, report := runGraphAttempt(t, flatConditionDefinition,
		`{"requires_approval":false}`, box)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 1 {
		t.Fatalf("sandbox calls = %d", box.calls)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["left"], ","); got != "skipped" {
		t.Fatalf("left states = %v", observed["left"])
	}
	if got := strings.Join(observed["right"], ","); got != "pending,running,succeeded" {
		t.Fatalf("right states = %v", observed["right"])
	}
	conditionData := artifactData(t, client, "choose")
	if conditionData["port"] != "no" || conditionData["matched_case"] != "default" {
		t.Fatalf("condition data = %v", conditionData)
	}
	results := artifactData(t, client, "join")["results"].(map[string]any)
	if results["left"].(map[string]any)["status"] != "skipped" {
		t.Fatalf("left envelope = %v", results["left"])
	}
	if results["right"].(map[string]any)["status"] != "succeeded" {
		t.Fatalf("right envelope = %v", results["right"])
	}
}

func TestGraphRunParallelForkActivatesBothBranchesAndJoinsOnce(t *testing.T) {
	box := successSandbox()
	client, report := runGraphAttempt(t, parallelDefinition, `{}`, box)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
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
		if _, ok := entry["value"].(map[string]any); !ok {
			t.Fatalf("source %s has no value: %v", source, entry)
		}
	}
	if rows := nodeResultRows(client, "join"); len(rows) != 1 {
		t.Fatalf("join rows = %+v", rows)
	}
}

// ---------------------------------------------------------------------------
// fail-closed activation
// ---------------------------------------------------------------------------

func TestGraphRunJoinWithoutAnActivatedRequiredSourceFailsInvalidActivation(t *testing.T) {
	box := successSandbox()
	client, report := runGraphAttempt(t, invalidActivationDefinition,
		`{"requires_approval":false}`, box)
	if report.Outcome != "failed" || report.ErrorCode != CodeInvalidActivation {
		t.Fatalf("report = %+v", report)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["join"], ","); got != "failed" {
		t.Fatalf("join states = %v", observed["join"])
	}
	if got := strings.Join(observed["end"], ","); got != "skipped" {
		t.Fatalf("end states = %v", observed["end"])
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "failed" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	if client.proposals[0].ErrorCode != CodeInvalidActivation {
		t.Fatalf("proposal code = %q", client.proposals[0].ErrorCode)
	}
}

func TestGraphRunUnsupportedNodeTypeKeepsItsOwnRefusalCode(t *testing.T) {
	document := definitionDocument(t, flatConditionDefinition)
	for index := range document.Definition.Nodes {
		if document.Definition.Nodes[index].ID == "left" {
			// F04B/T086 runs model nodes; retrieve keeps its per-type refusal until
			// T088 replaces it.
			document.Definition.Nodes[index].Type = "retrieve"
			document.Definition.Nodes[index].Config = json.RawMessage(`{}`)
		}
	}
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"requires_approval":true}`)
	report := Run(context.Background(),
		baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox()))
	if report.Outcome != "refused" || report.ErrorCode != CodeNodeUnsupportedRetrieve {
		t.Fatalf("report = %+v", report)
	}
	if len(client.nodeEvents) != 0 {
		t.Fatalf("a plan refusal must not emit node events: %+v", client.nodeEvents)
	}
}

// ---------------------------------------------------------------------------
// failure and skip propagation
// ---------------------------------------------------------------------------

func TestGraphRunSelectedBranchFailureSkipsEverythingDownstream(t *testing.T) {
	box := successSandbox()
	box.outcome.ExitCode = 2 // the tool crashed
	box.outcome.OutputJSON = nil
	box.outcome.Artifacts = nil
	client, report := runGraphAttempt(t, flatConditionDefinition,
		`{"requires_approval":true}`, box)
	if report.Outcome != "failed" || report.ErrorCode != CodeToolCrash {
		t.Fatalf("report = %+v", report)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["left"], ","); got != "pending,running,failed" {
		t.Fatalf("left states = %v", observed["left"])
	}
	for _, nodeID := range []string{"right", "join", "end"} {
		if got := strings.Join(observed[nodeID], ","); got != "skipped" {
			t.Fatalf("%s states = %v, want one skipped", nodeID, observed[nodeID])
		}
	}
	if len(nodeResultRows(client, "join")) != 0 {
		t.Fatalf("a failed branch must not produce a join result: %+v", client.nodeResults)
	}
}

// ---------------------------------------------------------------------------
// recovery seam
// ---------------------------------------------------------------------------

type fakeNodeOutputs struct {
	data map[string][]byte
	err  error
}

func (f *fakeNodeOutputs) NodeData(context.Context, string) (map[string][]byte, error) {
	return f.data, f.err
}

func TestGraphRunReusesCompletedNodeOutputsWithoutReExecuting(t *testing.T) {
	document := definitionDocument(t, flatConditionDefinition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"requires_approval":true}`)
	box := successSandbox()
	completed := &fakeNodeOutputs{data: map[string][]byte{
		"start": []byte(`{"requires_approval":true}`),
		"left":  []byte(`{"total":"9.00","regions":{"east":"9.00"},"order_count":1}`),
	}}
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box)
	deps.NodeOutputs = completed

	report := Run(context.Background(), deps)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 0 {
		t.Fatalf("sandbox calls = %d; a completed node must not run again", box.calls)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["start"], ","); got != "succeeded" {
		t.Fatalf("start states = %v, want the terminal fact only", observed["start"])
	}
	if got := strings.Join(observed["left"], ","); got != "succeeded" {
		t.Fatalf("left states = %v", observed["left"])
	}
	results := artifactData(t, client, "join")["results"].(map[string]any)
	left, _ := results["left"].(map[string]any)
	value, _ := left["value"].(map[string]any)
	if value == nil || value["total"] != "9.00" {
		t.Fatalf("the reused value must reach the join: %v", left)
	}
}

func TestGraphRunKeepsTheLegacyLinearPathOutOfGraphDefinitions(t *testing.T) {
	// A single preset python node bound the legacy way would take the T033 path
	// if the graph constructs did not force this executor to the graph scheduler.
	const legacyShapedGraph = `{
      "dsl_version": "1.0",
      "input_schema": {"type":"object","properties":{"requires_approval":{"type":"boolean"}},
                       "required":["requires_approval"],"additionalProperties":false},
      "output_schema": {"type":"object"},
      "nodes": [
        {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
        {"id":"choose","type":"condition","inputs":{"requires_approval":{"ref":"input#/requires_approval"}},
         "config":{"join_id":"join","default_port":"no",
                   "cases":[{"input":"requires_approval","op":"eq","value":true,"port":"yes"}]},
         "output_schema":{"type":"object"}},
        {"id":"stats","type":"python","inputs":{"file":{"ref":"input#/file"}},
         "config":{"tool_version":"11111111-1111-4111-8111-111111111111"},
         "output_schema":{"type":"object"}},
        {"id":"join","type":"join","inputs":{},
         "config":{"fork_id":"choose","mode":"all_active",
                   "required_sources":["stats","choose"],"optional_sources":[]},
         "output_schema":{"type":"object"}},
        {"id":"end","type":"end","inputs":{"results":{"ref":"node:join#/results"}},
         "config":{},"output_schema":{"type":"object"}}
      ],
      "edges": [
        {"source":"start","target":"choose"},
        {"source":"choose","target":"stats","port":"yes"},
        {"source":"choose","target":"join","port":"no"},
        {"source":"stats","target":"join"},
        {"source":"join","target":"end"}
      ],
      "policy": {"max_active_seconds":900,"max_child_depth":3,"max_child_calls":12,
                 "budget_limit":"1.00","currency":"USD"}
    }`
	document := definitionDocument(t, legacyShapedGraph)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"requires_approval":false}`)
	report := Run(context.Background(),
		baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox()))
	if report.Outcome != "succeeded" {
		t.Fatalf("report = %+v", report)
	}
	// The legacy path produces no node.status and no node-results rows.
	if len(client.nodeEvents) == 0 {
		t.Fatal("the graph scheduler must serve a condition graph, not the legacy executor")
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["choose"], ","); got != "pending,running,succeeded" {
		t.Fatalf("choose states = %v", observed["choose"])
	}
	if got := strings.Join(observed["stats"], ","); got != "skipped" {
		t.Fatalf("stats states = %v", observed["stats"])
	}
	if len(nodeResultRows(client, "join")) != 1 {
		t.Fatalf("the join must report exactly one data row: %+v", client.nodeResults)
	}
}
