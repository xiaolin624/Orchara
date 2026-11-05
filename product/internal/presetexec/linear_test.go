package presetexec

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"

	"orchara/platform/internal/sandbox"
)

// linearLease returns one lease with a signed run-input link and the manifest
// bytes whose definition is exactly the test's start -> python -> end chain.
func linearLease(t *testing.T, definition map[string]any) (*Lease, []byte, string) {
	t.Helper()
	document := map[string]any{
		"manifest_version":    "orchara-execution-manifest/1.0",
		"compiler_version":    CompilerVersion,
		"worker_image_digest": "sha256:" + strings.Repeat("aa", 32),
		"definition":          definition,
		"tool_artifacts": []any{map[string]any{
			"id": PresetToolID, "image_digest": "sha256:" + strings.Repeat("bb", 32),
		}},
		"model_profile_versions": []any{testModelProfileID},
	}
	payload, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(payload)
	lease := &Lease{
		JobID:           "job-1",
		RunID:           "11111111-2222-4333-8444-555555555555",
		AttemptToken:    "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
		ProtocolVersion: ProtocolVersion,
		ManifestSHA256:  hex.EncodeToString(sum[:]),
		ManifestURL:     "http://object.invalid/get/manifest",
		InputArtifactID: "77777777-6666-4555-8444-333333333333",
		InputURL:        "http://object.invalid/get/run-input",
	}
	return lease, payload, hex.EncodeToString(sum[:])
}

func linearDefinition(t *testing.T) map[string]any {
	t.Helper()
	var definition map[string]any
	if err := json.Unmarshal([]byte(linearDefinitionJSON), &definition); err != nil {
		t.Fatal(err)
	}
	return definition
}

// linearClient wires the fake protocol client for a linear attempt: the sealed
// run input, one linked CSV task_input and the real sales bytes.
func linearClient(t *testing.T, lease *Lease, manifest []byte, input InputArtifact, csv []byte) *fakeClient {
	t.Helper()
	return &fakeClient{
		lease:      lease,
		manifest:   manifest,
		inputs:     []InputArtifact{input},
		inputBytes: csv,
		runInput:   []byte(`{"source":"debug run"}`),
		runState:   "succeeded",
	}
}

func TestLinearChainRegistersDataFilesAndLogs(t *testing.T) {
	lease, manifest, _ := linearLease(t, linearDefinition(t))
	input := inputArtifact()
	client := linearClient(t, lease, manifest, input, []byte(salesCSV))
	box := successSandbox()

	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 1 {
		t.Fatalf("sandbox calls = %d, want 1", box.calls)
	}
	if box.last.MediaType != "text/csv" {
		t.Fatalf("sandbox media type = %q", box.last.MediaType)
	}
	var inputDocument struct {
		Tool     string `json:"tool"`
		File     string `json:"file"`
		Artifact struct {
			ArtifactID string `json:"artifact_id"`
			MediaType  string `json:"media_type"`
		} `json:"artifact"`
	}
	if err := json.Unmarshal(box.last.InputJSON, &inputDocument); err != nil {
		t.Fatalf("input.json is not JSON: %v", err)
	}
	if inputDocument.Tool != PresetToolName || inputDocument.File != "input.csv" {
		t.Fatalf("input.json = %+v", inputDocument)
	}
	if inputDocument.Artifact.ArtifactID != input.ID || inputDocument.Artifact.MediaType != "text/csv" {
		t.Fatalf("input.json artifact facts = %+v", inputDocument.Artifact)
	}

	// Upload purposes: one data artifact, two files, one log and the final output.
	purposes := map[string]int{}
	for _, upload := range client.uploads {
		purposes[upload.Request.Purpose]++
	}
	if purposes["node_output"] != 3 || purposes["log"] != 1 || purposes["final_output"] != 1 {
		t.Fatalf("upload purposes = %v", purposes)
	}

	// Node results: data + two files + log, all attached to the python node.
	if len(client.nodeResults) != 4 {
		t.Fatalf("node results = %+v", client.nodeResults)
	}
	byKind := map[string][]NodeResult{}
	for _, result := range client.nodeResults {
		byKind[result.Kind] = append(byKind[result.Kind], result)
	}
	if len(byKind["data"]) != 1 || len(byKind["file"]) != 2 || len(byKind["log"]) != 1 {
		t.Fatalf("node results by kind = %+v", byKind)
	}
	if byKind["data"][0].NodeID != "stats" || byKind["data"][0].Label != "data" {
		t.Fatalf("data row = %+v", byKind["data"][0])
	}
	fileLabels := []string{byKind["file"][0].Label, byKind["file"][1].Label}
	if fileLabels[0] != "summary.json" || fileLabels[1] != "summary.csv" {
		t.Fatalf("file labels = %v", fileLabels)
	}

	// The data artifact bytes are the canonical output.json.data, not output.json.
	for _, upload := range client.uploads {
		if upload.Request.Purpose == "node_output" && upload.Request.MediaType == "application/json" {
			var data map[string]any
			if err := json.Unmarshal(upload.Content, &data); err != nil {
				t.Fatalf("data artifact is not JSON: %v", err)
			}
			if data["total"] != "200.00" || data["order_count"] != float64(2) {
				t.Fatalf("data artifact = %s", upload.Content)
			}
			if strings.Contains(string(upload.Content), `"artifacts"`) {
				t.Fatalf("raw output.json was uploaded: %s", upload.Content)
			}
		}
	}

	// Every node reported its closed transition set.
	want := map[string][]string{
		"start": {"pending", "running", "succeeded"},
		"stats": {"pending", "running", "succeeded"},
		"end":   {"pending", "running", "succeeded"},
	}
	observed := map[string][]string{}
	for _, event := range client.nodeEvents {
		observed[event.NodeID] = append(observed[event.NodeID], event.State)
	}
	for nodeID, states := range want {
		if strings.Join(observed[nodeID], ",") != strings.Join(states, ",") {
			t.Fatalf("node %s states = %v, want %v", nodeID, observed[nodeID], states)
		}
	}

	// The final output is registered as the run output and proposed.
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "succeeded" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	var finalUpload recordedUpload
	for _, upload := range client.uploads {
		if upload.Request.Purpose == "final_output" {
			finalUpload = upload
		}
	}
	if client.proposals[0].OutputArtifactID != finalUpload.Artifact {
		t.Fatalf("proposal output = %q, final upload = %q",
			client.proposals[0].OutputArtifactID, finalUpload.Artifact)
	}
	var final map[string]any
	if err := json.Unmarshal(finalUpload.Content, &final); err != nil {
		t.Fatalf("final output is not JSON: %v", err)
	}
	if final["report"] != "sales summary" {
		t.Fatalf("final output = %s", finalUpload.Content)
	}
	if _, ok := final["summary"].(map[string]any); !ok {
		t.Fatalf("final output summary = %v", final["summary"])
	}
	if len(client.registers) != 5 {
		t.Fatalf("registered artifacts = %+v", client.registers)
	}
	if len(client.exits) != 1 || client.exits[0].Evidence["sandbox_started"] != true {
		t.Fatalf("exit evidence = %+v", client.exits)
	}
}

func TestLinearChainRefusesAnUnresolvedBindingBeforeTheSandbox(t *testing.T) {
	definition := linearDefinition(t)
	// Point the python `file` binding at a node that has not run.
	nodes := definition["nodes"].([]any)
	pythonNode := nodes[1].(map[string]any)
	pythonNode["inputs"] = map[string]any{"file": map[string]any{"ref": "node:end#/missing"}}

	lease, manifest, _ := linearLease(t, definition)
	input := inputArtifact()
	client := linearClient(t, lease, manifest, input, []byte(salesCSV))
	box := successSandbox()
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Outcome != "refused" || report.ErrorCode != CodeNodeInputInvalid {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 0 {
		t.Fatalf("sandbox ran for an unresolved binding: %d", box.calls)
	}
	states := map[string][]string{}
	for _, event := range client.nodeEvents {
		states[event.NodeID] = append(states[event.NodeID], event.State)
	}
	if strings.Join(states["stats"], ",") != "pending,running,failed" {
		t.Fatalf("stats states = %v", states["stats"])
	}
	if strings.Join(states["end"], ",") != "skipped" {
		t.Fatalf("end states = %v", states["end"])
	}
}

func TestLinearChainRefusesOutputThatViolatesTheNodeSchema(t *testing.T) {
	definition := linearDefinition(t)
	nodes := definition["nodes"].([]any)
	pythonNode := nodes[1].(map[string]any)
	pythonNode["output_schema"] = map[string]any{
		"type":       "object",
		"properties": map[string]any{"total": map[string]any{"type": "integer"}},
		"required":   []any{"total"},
	}
	lease, manifest, _ := linearLease(t, definition)
	input := inputArtifact()
	client := linearClient(t, lease, manifest, input, []byte(salesCSV))
	box := successSandbox()
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Outcome != "failed" || report.ErrorCode != CodeNodeOutputInvalid {
		t.Fatalf("report = %+v", report)
	}
	// A failed attempt may publish its safe diagnostics document as node_output
	// evidence, but it must never register tool data/files or a node result row.
	for _, upload := range client.uploads {
		if upload.Request.Purpose == "final_output" {
			t.Fatalf("a violating tool output was uploaded as the final output")
		}
		if upload.Request.Purpose == "node_output" {
			var document map[string]any
			if json.Unmarshal(upload.Content, &document) != nil ||
				document["diagnostics_version"] == nil {
				t.Fatalf("a violating tool output was uploaded as data: %s", upload.Content)
			}
		}
	}
	if len(client.registers) != 0 {
		// F13A/T056 links the safe failure evidence (the log and the diagnostics
		// document) to the run so a per-case result can offer the actual
		// attachments of a failed case. Tool data/files must still never be
		// registered.
		purposeOf := map[string]string{}
		contentOf := map[string][]byte{}
		for _, upload := range client.uploads {
			purposeOf[upload.Artifact] = upload.Request.Purpose
			contentOf[upload.Artifact] = upload.Content
		}
		for _, registration := range client.registers {
			purpose := purposeOf[registration.ArtifactID]
			switch purpose {
			case "log":
			case "node_output":
				var document map[string]any
				if json.Unmarshal(contentOf[registration.ArtifactID], &document) != nil ||
					document["diagnostics_version"] == nil {
					t.Fatalf("a violating tool output was registered as data: %+v", registration)
				}
			default:
				t.Fatalf("a violating tool output was registered as %q: %+v",
					purpose, registration)
			}
		}
	}
	if len(client.nodeResults) != 0 {
		t.Fatalf("node results recorded for a schema violation: %+v", client.nodeResults)
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "failed" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
}

func TestLinearChainRefusesRunInputThatViolatesTheInputSchema(t *testing.T) {
	lease, manifest, _ := linearLease(t, linearDefinition(t))
	input := inputArtifact()
	client := linearClient(t, lease, manifest, input, []byte(salesCSV))
	client.runInput = []byte(`{"source": 5}`)
	box := successSandbox()
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Outcome != "refused" || report.ErrorCode != CodeNodeInputInvalid {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 0 {
		t.Fatalf("sandbox ran with an invalid run input")
	}
}

// TestArtifactClaimsAreUntrusted proves an output.json that lists a file the
// driver did not collect is refused; the claim never becomes an artifact.
func TestArtifactClaimsAreUntrusted(t *testing.T) {
	lease, manifest, _ := linearLease(t, linearDefinition(t))
	input := inputArtifact()
	client := linearClient(t, lease, manifest, input, []byte(salesCSV))
	box := successSandbox()
	box.outcome.Artifacts = nil // claims summary.json/csv but collects nothing
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Outcome != "failed" || report.ErrorCode != CodeToolArtifactInvalid {
		t.Fatalf("report = %+v", report)
	}
}

// TestLegacyAgentGraphStillTakesTheLegacyPath is the T033 compatibility proof:
// the sealed golden graph contains an agent node but exactly one preset python
// node and the legacy single task_input, so the old flow must run unchanged.
func TestLegacyAgentGraphStillTakesTheLegacyPath(t *testing.T) {
	definition := map[string]any{
		"nodes": []any{
			map[string]any{"id": "start", "type": "start", "config": map[string]any{}},
			map[string]any{"id": "stats", "type": "python",
				"inputs": map[string]any{"file": map[string]any{"ref": "input#/file"}},
				"config": map[string]any{"tool_version": PresetToolID}},
			map[string]any{"id": "analysis", "type": "agent", "config": map[string]any{}},
			map[string]any{"id": "end", "type": "end", "config": map[string]any{}},
		},
		"edges": []any{
			map[string]any{"source": "start", "target": "stats"},
			map[string]any{"source": "stats", "target": "analysis"},
			map[string]any{"source": "analysis", "target": "end"},
		},
	}
	lease, manifest, _ := linearLease(t, definition)
	input := inputArtifact()
	client := linearClient(t, lease, manifest, input, []byte(salesCSV))
	box := successSandbox()
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	if report.Outcome != "succeeded" {
		t.Fatalf("legacy graph no longer runs: %+v", report)
	}
	if len(client.nodeEvents) != 0 {
		t.Fatalf("legacy path emitted linear node events: %+v", client.nodeEvents)
	}
	if _, ok := client.uploadsByPurpose()["final_output"]; !ok {
		t.Fatalf("legacy upload purposes = %+v", client.uploads)
	}
}

// TestAgentOnlyGraphWithoutPythonIsAcceptedByTheLinearPlan pins F07/T078's
// relaxation: an agent node no longer requires a preset python node in front of
// it. This graph's agent config is empty, so the node is refused with the stable
// agent code before any sandbox starts, not with a graph-shape code.
func TestAgentOnlyGraphWithoutPythonIsAcceptedByTheLinearPlan(t *testing.T) {
	definition := map[string]any{
		"nodes": []any{
			map[string]any{"id": "start", "type": "start", "config": map[string]any{}},
			map[string]any{"id": "analysis", "type": "agent", "config": map[string]any{}},
			map[string]any{"id": "end", "type": "end", "config": map[string]any{}},
		},
		"edges": []any{
			map[string]any{"source": "start", "target": "analysis"},
			map[string]any{"source": "analysis", "target": "end"},
		},
	}
	lease, manifest, _ := linearLease(t, definition)
	input := inputArtifact()
	client := linearClient(t, lease, manifest, input, []byte(salesCSV))
	box := successSandbox()
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	// There is no preset python node; the linear plan accepts the shape and the
	// agent config validation refuses it before any sandbox starts.
	if report.Outcome != "refused" || report.ErrorCode != CodeAgentConfigInvalid {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 0 {
		t.Fatalf("an agent-only graph reached the sandbox")
	}
}

// TestHostileArtifactNamesNeverReachThePlatform keeps the claim admission closed:
// an absolute path, a traversal name or a symlink-looking entry is refused by the
// protocol before it can be uploaded.
func TestHostileArtifactNamesNeverReachThePlatform(t *testing.T) {
	for _, name := range []string{"/etc/passwd", "../escape", "sub/dir", "..", ""} {
		if _, _, err := sandbox.ParseOutputDocument(
			[]byte(`{"data":{},"artifacts":["` + name + `"]}`),
		); err == nil {
			t.Fatalf("hostile artifact name %q was accepted", name)
		}
	}
}
