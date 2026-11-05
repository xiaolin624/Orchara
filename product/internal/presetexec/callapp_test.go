package presetexec

// F07/T078 Go seam tests for the delegation segment: the executor never creates a
// Run itself, it calls the platform's spawnChild operation, parks on a live child
// and adopts a terminal child's structured result. The scripted client stands in
// only for the server protocol; the real-stack acceptance is
// tests/f07/test_t078_child_runs.py.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
)

const testChildRunID = "99999999-8888-4777-8666-555555555555"

// delegationLease builds the sealed manifest and lease for a definition that
// closes over the given fixed sub-application versions.
func delegationLease(t *testing.T, definition map[string]any, subapps []string) (*Lease, []byte) {
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
		"subapp_versions":        subapps,
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
	return lease, payload
}

func callAppDefinition(childVersion string) map[string]any {
	return map[string]any{
		"dsl_version": "1.0",
		"input_schema": map[string]any{
			"type": "object", "properties": map[string]any{"source": map[string]any{"type": "string"}},
			"required": []any{"source"}, "additionalProperties": false,
		},
		"output_schema": map[string]any{
			"type": "object", "properties": map[string]any{"echo": map[string]any{"type": "string"}},
			"required": []any{"echo"}, "additionalProperties": false,
		},
		"nodes": []any{
			map[string]any{"id": "start", "type": "start", "inputs": map[string]any{}, "config": map[string]any{}},
			map[string]any{
				"id": "child", "type": "call_app",
				"inputs": map[string]any{"source": map[string]any{"ref": "input#/source"}},
				"output_schema": map[string]any{
					"type": "object", "properties": map[string]any{"echo": map[string]any{"type": "string"}},
					"required": []any{"echo"}, "additionalProperties": false,
				},
				"config": map[string]any{"application_version": childVersion},
			},
			map[string]any{
				"id": "end", "type": "end",
				"inputs": map[string]any{"echo": map[string]any{"ref": "node:child#/echo"}},
				"config": map[string]any{},
			},
		},
		"edges": []any{
			map[string]any{"source": "start", "target": "child"},
			map[string]any{"source": "child", "target": "end"},
		},
	}
}

func identityDefinition() map[string]any {
	return map[string]any{
		"dsl_version": "1.0",
		"input_schema": map[string]any{
			"type": "object", "properties": map[string]any{"source": map[string]any{"type": "string"}},
			"required": []any{"source"}, "additionalProperties": false,
		},
		"output_schema": map[string]any{
			"type": "object", "properties": map[string]any{"echo": map[string]any{"type": "string"}},
			"required": []any{"echo"}, "additionalProperties": false,
		},
		"nodes": []any{
			map[string]any{"id": "start", "type": "start", "inputs": map[string]any{}, "config": map[string]any{}},
			map[string]any{
				"id": "end", "type": "end",
				"inputs": map[string]any{"echo": map[string]any{"ref": "input#/source"}},
				"config": map[string]any{},
			},
		},
		"edges": []any{map[string]any{"source": "start", "target": "end"}},
	}
}

func delegationClient(t *testing.T, definition map[string]any, subapps []string) (*fakeClient, *fakeSandbox, Deps) {
	t.Helper()
	lease, manifest := delegationLease(t, definition, subapps)
	client := &fakeClient{
		lease: lease, manifest: manifest,
		runInput: []byte(`{"source":"parent-value"}`),
		runState: "succeeded",
	}
	box := successSandbox()
	return client, box, baseDeps(t, client, &fakeInputs{}, box)
}

// TestCallAppParksTheParentOnALiveChild is the waiting hand-over: spawn once,
// propose waiting_children at `child:<id>`, certify the exit.
func TestCallAppParksTheParentOnALiveChild(t *testing.T) {
	client, box, deps := delegationClient(t, callAppDefinition(testSubappID), []string{testSubappID})
	client.spawnChild = func(request SpawnChildRequest) (*ChildRun, error) {
		if request.ApplicationVersionID != testSubappID {
			t.Fatalf("spawn version = %q", request.ApplicationVersionID)
		}
		if request.LogicalKey != "call_app:child" {
			t.Fatalf("logical key = %q", request.LogicalKey)
		}
		if request.Input["source"] != "parent-value" {
			t.Fatalf("mapped input = %+v", request.Input)
		}
		return &ChildRun{ID: testChildRunID, State: "running",
			ApplicationVersionID: testSubappID, ParentRunID: deps.Options.WorkerID}, nil
	}
	report := Run(context.Background(), deps)
	if report.Outcome != "waiting_children" || report.ErrorCode != "children_pending" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.proposals) != 1 ||
		client.proposals[0].SuggestedState != "waiting_children" ||
		client.proposals[0].CheckpointID != "child:"+testChildRunID {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	if len(client.exits) != 1 || len(client.spawnRequests) != 1 {
		t.Fatalf("exits = %d spawns = %d", len(client.exits), len(client.spawnRequests))
	}
	if box.calls != 0 {
		t.Fatalf("the parent ran a sandbox for a pure delegation: %d", box.calls)
	}
}

// TestCallAppAdoptsATerminalChildResult proves the resume path: the replayed
// spawn finds the same terminal child and the node publishes its validated result.
func TestCallAppAdoptsATerminalChildResult(t *testing.T) {
	client, _, deps := delegationClient(t, callAppDefinition(testSubappID), []string{testSubappID})
	client.spawnChild = func(SpawnChildRequest) (*ChildRun, error) {
		return &ChildRun{ID: testChildRunID, State: "succeeded", ApplicationVersionID: testSubappID}, nil
	}
	client.childResult = func(childRunID string) (*ChildResult, error) {
		if childRunID != testChildRunID {
			t.Fatalf("read child = %q", childRunID)
		}
		return &ChildResult{
			ChildRunID: testChildRunID, State: "succeeded",
			Output: map[string]any{"echo": "from-child"},
		}, nil
	}
	report := Run(context.Background(), deps)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "succeeded" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	var final []byte
	for _, upload := range client.uploads {
		if upload.Request.Purpose == "final_output" {
			final = upload.Content
		}
	}
	var output map[string]any
	if err := json.Unmarshal(final, &output); err != nil || output["echo"] != "from-child" {
		t.Fatalf("final output = %s (%v)", final, err)
	}
	foundChildData := false
	for _, result := range client.nodeResults {
		if result.NodeID == "child" && result.Kind == "data" &&
			result.Label == "child-run:"+testChildRunID {
			foundChildData = true
		}
	}
	if !foundChildData {
		t.Fatalf("the child result was not reported as the node data: %+v", client.nodeResults)
	}
}

// TestCallAppFailsTheRunWhenTheRequiredChildFails pins the fail-fast rule.
func TestCallAppFailsTheRunWhenTheRequiredChildFails(t *testing.T) {
	client, _, deps := delegationClient(t, callAppDefinition(testSubappID), []string{testSubappID})
	client.spawnChild = func(SpawnChildRequest) (*ChildRun, error) {
		return &ChildRun{ID: testChildRunID, State: "failed", ErrorCode: "sandbox_unavailable"}, nil
	}
	report := Run(context.Background(), deps)
	if report.Outcome != "failed" || report.ErrorCode != "sandbox_unavailable" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "failed" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
}

// TestCallAppRefusalCodeBecomesTheRunCode keeps the platform vocabulary: a spawn
// refusal (for example the child depth/count limit) is the run's error code.
func TestCallAppRefusalCodeBecomesTheRunCode(t *testing.T) {
	client, _, deps := delegationClient(t, callAppDefinition(testSubappID), []string{testSubappID})
	client.spawnChild = func(SpawnChildRequest) (*ChildRun, error) {
		return nil, codedError{code: "child_calls_exceeded"}
	}
	report := Run(context.Background(), deps)
	if report.Outcome != "failed" || report.ErrorCode != "child_calls_exceeded" {
		t.Fatalf("report = %+v", report)
	}
}

type codedError struct{ code string }

func (e codedError) Error() string     { return e.code }
func (e codedError) ErrorCode() string { return e.code }

// TestCallAppRejectsAnUnrefinedChildResult proves the node's output_schema is the
// authority even when the child itself succeeded.
func TestCallAppRejectsAnUnrefinedChildResult(t *testing.T) {
	client, _, deps := delegationClient(t, callAppDefinition(testSubappID), []string{testSubappID})
	client.spawnChild = func(SpawnChildRequest) (*ChildRun, error) {
		return &ChildRun{ID: testChildRunID, State: "succeeded"}, nil
	}
	client.childResult = func(string) (*ChildResult, error) {
		return &ChildResult{State: "succeeded", Output: map[string]any{"bogus": "x"}}, nil
	}
	report := Run(context.Background(), deps)
	if report.Outcome != "failed" || report.ErrorCode != CodeNodeOutputInvalid {
		t.Fatalf("report = %+v", report)
	}
}

// TestIdentityChainExecutesWithoutASandbox is the child graph a delegated Run may
// use: start validates the sealed input, end maps the declared output.
func TestIdentityChainExecutesWithoutASandbox(t *testing.T) {
	client, box, deps := delegationClient(t, identityDefinition(), nil)
	report := Run(context.Background(), deps)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 0 {
		t.Fatalf("an identity chain started the sandbox %d times", box.calls)
	}
	var final []byte
	for _, upload := range client.uploads {
		if upload.Request.Purpose == "final_output" {
			final = upload.Content
		}
	}
	var output map[string]any
	if err := json.Unmarshal(final, &output); err != nil || output["echo"] != "parent-value" {
		t.Fatalf("final output = %s (%v)", final, err)
	}
}

// TestAgentSubappToolSpawnsAChild proves an agent's allowed sub-application set is
// a real delegation path: the model's validated tool call becomes spawnChild with
// the fixed version and a per-call logical key.
func TestAgentSubappToolSpawnsAChild(t *testing.T) {
	definition := agentDefinitionMap(t)
	nodes := definition["nodes"].([]any)
	agentNode := nodes[2].(map[string]any)
	config := agentNode["config"].(map[string]any)
	config["subapp_versions"] = []any{testSubappID}
	agentNode["config"] = config

	lease, manifest := delegationLease(t, definition, []string{testSubappID})
	input := inputArtifact()
	client := linearClient(t, lease, manifest, input, []byte(salesCSV))
	script := &agentScript{
		turns: [][]any{{map[string]any{
			"id": "call-9", "name": "call_app_1",
			"arguments": map[string]any{"source": "agent-value"},
		}}},
		final: map[string]any{"text": "summary"},
	}
	client.invokeCapability = script.invoke
	client.spawnChild = func(request SpawnChildRequest) (*ChildRun, error) {
		if request.ApplicationVersionID != testSubappID {
			t.Fatalf("spawn version = %q", request.ApplicationVersionID)
		}
		if request.LogicalKey != "agent:analysis:subapp:call-9" {
			t.Fatalf("logical key = %q", request.LogicalKey)
		}
		if request.Input["source"] != "agent-value" {
			t.Fatalf("subapp input = %+v", request.Input)
		}
		return &ChildRun{ID: testChildRunID, State: "succeeded"}, nil
	}
	client.childResult = func(string) (*ChildResult, error) {
		return &ChildResult{State: "succeeded", Output: map[string]any{"echo": "agent-child"}}, nil
	}
	box := successSandbox()
	deps := agentDeps(t, client, box, currentFacts())
	report := Run(context.Background(), deps)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.spawnRequests) != 1 || len(client.childReads) != 1 {
		t.Fatalf("spawns = %+v reads = %v", client.spawnRequests, client.childReads)
	}
	// The model's next turn must have seen the child result as a tool message.
	found := false
	for _, call := range client.capabilityInputs {
		if call.CapabilityKind != "model" {
			continue
		}
		messages, _ := call.Params["messages"].([]any)
		for _, raw := range messages {
			message, _ := raw.(map[string]any)
			if message["role"] == "tool" {
				content, _ := message["content"].(string)
				if strings.Contains(content, "agent-child") {
					found = true
				}
			}
		}
	}
	if !found {
		t.Fatal("the child result never reached the model as a tool message")
	}
}
