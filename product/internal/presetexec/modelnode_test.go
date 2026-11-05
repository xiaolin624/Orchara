package presetexec

// F04B/T086 Go seam tests: the model node over the scripted capability client.
//
// The model node is one controlled generation, never a loop: exactly one
// capability operation, no tools, no second turn, no retry of a schema failure.
// These tests pin the executor's own guard rails (closed config, strict prompt
// rendering, node-level schema re-validation, stable capability-state mapping)
// and the recovery seam. The real PostgreSQL + mTLS + dev-stub evidence lives in
// tests/f04b/test_t086_model_node.py.

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"orchara/platform/internal/capabilities"
)

// modelGraphDefinition is the T086 acceptance shape:
//
//	start -> analyse(model, json) -> end
//
// The response schema IS the node's output_schema; there is no response_schema
// config key.
const modelGraphDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object","properties":{"task":{"type":"string"}},
                   "required":["task"],"additionalProperties":false},
  "output_schema": {"type":"object","properties":{"summary":{"type":"string"}},
                    "required":["summary"],"additionalProperties":false},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
    {"id":"analyse","type":"model","inputs":{"task":{"ref":"input#/task"}},
     "config":{"model_profile_version":"22222222-2222-4222-8222-222222222222",
               "prompt_template":"Summarize {{task}}","response_mode":"json"},
     "output_schema":{"type":"object","properties":{"summary":{"type":"string"}},
                      "required":["summary"],"additionalProperties":false}},
    {"id":"end","type":"end","inputs":{"summary":{"ref":"node:analyse#/summary"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [
    {"source":"start","target":"analyse"},
    {"source":"analyse","target":"end"}
  ],
  "policy": {"max_active_seconds":900,"max_child_depth":3,"max_child_calls":12,
             "budget_limit":"1.00","currency":"USD"}
}`

// modelGraphDocument builds the fixture, lets a test mutate the decoded document
// and records the fixed profile in the sealed manifest closure.
func modelGraphDocument(t *testing.T, mutate func(document map[string]any)) *manifestDocument {
	t.Helper()
	var document map[string]any
	if err := json.Unmarshal([]byte(modelGraphDefinition), &document); err != nil {
		t.Fatalf("the model fixture is not JSON: %v", err)
	}
	if mutate != nil {
		mutate(document)
	}
	payload, err := json.Marshal(document)
	if err != nil {
		t.Fatalf("re-encoding the model fixture: %v", err)
	}
	manifest := definitionDocument(t, string(payload))
	manifest.ModelProfileVersions = []string{testModelProfileID}
	return manifest
}

// modelNodeOf returns the decoded model node of a mutated fixture.
func modelNodeOf(t *testing.T, document map[string]any) map[string]any {
	t.Helper()
	nodes, _ := document["nodes"].([]any)
	for _, raw := range nodes {
		node, _ := raw.(map[string]any)
		if node["type"] == "model" {
			return node
		}
	}
	t.Fatal("the fixture carries no model node")
	return nil
}

func modelConfigOf(node map[string]any) map[string]any {
	config, _ := node["config"].(map[string]any)
	return config
}

// modelSuccess answers one capability call with a succeeded projection.
func modelSuccess(data map[string]any) func(capabilities.Input) (*capabilities.Result, error) {
	return func(input capabilities.Input) (*capabilities.Result, error) {
		return &capabilities.Result{OperationID: input.OperationID, State: "succeeded", Data: data}, nil
	}
}

// runModelGraph runs one attempt of the model fixture with the scripted
// capability seam and the scripted trusted facts.
func runModelGraph(
	t *testing.T,
	document *manifestDocument,
	runInput string,
	script func(capabilities.Input) (*capabilities.Result, error),
	facts Facts,
) (*fakeClient, Report) {
	t.Helper()
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, runInput)
	client.invokeCapability = script
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox())
	if facts == nil {
		facts = currentFacts()
	}
	deps.Facts = facts
	return client, Run(context.Background(), deps)
}

func runModelGraphDefault(
	t *testing.T, document *manifestDocument, script func(capabilities.Input) (*capabilities.Result, error),
) (*fakeClient, Report) {
	t.Helper()
	return runModelGraph(t, document, `{"task":"quarterly numbers"}`, script, nil)
}

// ---------------------------------------------------------------------------
// plan: acceptance and the closed config
// ---------------------------------------------------------------------------

func TestBuildGraphPlanAcceptsModelNodesAndSelectsTheGraphPath(t *testing.T) {
	document := modelGraphDocument(t, nil)
	if !hasGraphConstructs(document) {
		t.Fatal("a model definition must select the graph path")
	}
	plan, err := buildGraphPlan(document)
	if err != nil {
		t.Fatalf("buildGraphPlan: %v", err)
	}
	node, known := plan.nodes["analyse"]
	if !known || node.model == nil {
		t.Fatalf("the plan did not parse the model config: %+v", node)
	}
	if node.model.ModelProfileVersion != testModelProfileID {
		t.Fatalf("profile = %q", node.model.ModelProfileVersion)
	}
	if node.model.PromptTemplate != "Summarize {{task}}" {
		t.Fatalf("template = %q", node.model.PromptTemplate)
	}
	if node.model.ResponseMode != "json" {
		t.Fatalf("response mode = %q", node.model.ResponseMode)
	}
	if len(plan.order) != 3 {
		t.Fatalf("order = %v", plan.order)
	}
}

func TestBuildGraphPlanRefusesModelConfigsOutsideTheClosedSet(t *testing.T) {
	cases := map[string]func(node map[string]any){
		"missing prompt_template": func(node map[string]any) {
			delete(modelConfigOf(node), "prompt_template")
		},
		"missing model_profile_version": func(node map[string]any) {
			delete(modelConfigOf(node), "model_profile_version")
		},
		"extra response_schema key": func(node map[string]any) {
			modelConfigOf(node)["response_schema"] = map[string]any{"type": "object"}
		},
		"non-uuid profile": func(node map[string]any) {
			modelConfigOf(node)["model_profile_version"] = "latest"
		},
		"unknown response_mode": func(node map[string]any) {
			modelConfigOf(node)["response_mode"] = "yaml"
		},
		"non-string template": func(node map[string]any) {
			modelConfigOf(node)["prompt_template"] = 12
		},
		"non-object output schema": func(node map[string]any) {
			node["output_schema"] = map[string]any{"type": "string"}
		},
	}
	for name, mutate := range cases {
		document := modelGraphDocument(t, func(document map[string]any) {
			mutate(modelNodeOf(t, document))
		})
		_, err := buildGraphPlan(document)
		if err == nil {
			t.Fatalf("%s: the model config was accepted", name)
		}
		if code := planErrorCode(t, err); code != CodeBadConfig {
			t.Fatalf("%s: code = %q, want %q (%v)", name, code, CodeBadConfig, err)
		}
	}
}

// TestGraphRunModelConfigRefusalHappensBeforeAnyNodeEvent proves the defensive
// re-verification is a plan refusal: no node.status is emitted and no result is
// proposed for a graph the executor cannot run.
func TestGraphRunModelConfigRefusalHappensBeforeAnyNodeEvent(t *testing.T) {
	document := modelGraphDocument(t, func(document map[string]any) {
		delete(modelConfigOf(modelNodeOf(t, document)), "prompt_template")
	})
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"task":"x"}`)
	report := Run(context.Background(),
		baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox()))
	if report.Outcome != "refused" || report.ErrorCode != CodeBadConfig {
		t.Fatalf("report = %+v", report)
	}
	if len(client.nodeEvents) != 0 {
		t.Fatalf("a plan refusal must not emit node events: %+v", client.nodeEvents)
	}
	if len(client.capabilityInputs) != 0 {
		t.Fatalf("a plan refusal must not call the model: %+v", client.capabilityInputs)
	}
}

// ---------------------------------------------------------------------------
// json / text output
// ---------------------------------------------------------------------------

func TestGraphRunModelJSONRequestsTheOutputSchemaOnceAndPublishesStructuredOutput(t *testing.T) {
	document := modelGraphDocument(t, nil)
	client, report := runModelGraphDefault(t, document, modelSuccess(map[string]any{
		"text":       "stub text",
		"tool_calls": []any{},
		"structured": map[string]any{"summary": "stub-summary"},
	}))
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}

	// Exactly one controlled generation: no loop, no tools, no second turn.
	if len(client.capabilityInputs) != 1 {
		t.Fatalf("capability calls = %d, want exactly one", len(client.capabilityInputs))
	}
	call := client.capabilityInputs[0]
	if call.CapabilityKind != "model" || call.CapabilityVersion != testModelProfileID {
		t.Fatalf("capability input = %+v", call)
	}
	if call.LogicalKey != "model:analyse:1" {
		t.Fatalf("logical key = %q", call.LogicalKey)
	}
	wantOperation := deterministicOperationID(client.lease.RunID, "model:analyse:1")
	if call.OperationID != wantOperation {
		t.Fatalf("operation id = %q, want %q", call.OperationID, wantOperation)
	}
	if call.Params["task"] != "model" {
		t.Fatalf("task = %v", call.Params["task"])
	}
	messages, _ := call.Params["messages"].([]any)
	if len(messages) != 1 {
		t.Fatalf("messages = %+v", call.Params["messages"])
	}
	message, _ := messages[0].(map[string]any)
	if message["role"] != "user" || message["content"] != "Summarize quarterly numbers" {
		t.Fatalf("message = %+v", message)
	}
	if _, declared := call.Params["tools"]; declared {
		t.Fatalf("a model node must never declare tools: %+v", call.Params)
	}
	schema, ok := call.Params["response_schema"].(map[string]any)
	if !ok {
		t.Fatalf("the node output_schema was not requested: %+v", call.Params)
	}
	properties, _ := schema["properties"].(map[string]any)
	if _, present := properties["summary"]; !present {
		t.Fatalf("response schema = %+v", schema)
	}

	// The node's data artifact is the validated structured object.
	data := artifactData(t, client, "analyse")
	if len(data) != 1 || data["summary"] != "stub-summary" {
		t.Fatalf("node data = %v", data)
	}
	rows := nodeResultRows(client, "analyse")
	if len(rows) != 1 || rows[0].Kind != "data" || rows[0].Label != "data" {
		t.Fatalf("node rows = %+v", rows)
	}
	observed := observedNodeStates(client)
	for _, nodeID := range []string{"start", "analyse", "end"} {
		if got := strings.Join(observed[nodeID], ","); got != "pending,running,succeeded" {
			t.Fatalf("%s states = %v", nodeID, observed[nodeID])
		}
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "succeeded" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
}

func TestGraphRunModelTextWrapsTheProviderTextAndDoesNotRequestASchema(t *testing.T) {
	document := modelGraphDocument(t, func(document map[string]any) {
		node := modelNodeOf(t, document)
		modelConfigOf(node)["response_mode"] = "text"
		textSchema := map[string]any{
			"type": "object",
			"properties": map[string]any{
				"text":   map[string]any{"type": "string"},
				"source": map[string]any{"type": "string"},
			},
			"required":             []any{"text"},
			"additionalProperties": false,
		}
		node["output_schema"] = textSchema
		document["output_schema"] = textSchema
		nodes, _ := document["nodes"].([]any)
		end, _ := nodes[2].(map[string]any)
		end["inputs"] = map[string]any{"text": map[string]any{"ref": "node:analyse#/text"}}
	})
	client, report := runModelGraphDefault(t, document, modelSuccess(map[string]any{
		"text": "stub-agent:0123456789abcdef", "tool_calls": []any{},
	}))
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	call := client.capabilityInputs[0]
	if _, present := call.Params["response_schema"]; present {
		t.Fatalf("text mode must not request a structured response: %+v", call.Params)
	}
	data := artifactData(t, client, "analyse")
	// text mode wraps as {text} and then validates node.output_schema: the
	// optional `source` property is not invented by the platform.
	if len(data) != 1 || data["text"] != "stub-agent:0123456789abcdef" {
		t.Fatalf("node data = %v", data)
	}
}

// ---------------------------------------------------------------------------
// schema failure: independent, located, non-retryable
// ---------------------------------------------------------------------------

func TestGraphRunModelSchemaFailureIsIndependentAndNonRetryable(t *testing.T) {
	document := modelGraphDocument(t, nil)
	client, report := runModelGraphDefault(t, document, modelSuccess(map[string]any{
		"text":       "stub text",
		"tool_calls": []any{},
		"structured": map[string]any{"other": "not the contract"},
	}))
	// A model-only graph starts no sandbox, so the deterministic failure is the
	// graph path's "refused" shape (a committed failed result, sandbox_started
	// false) with the node's stable code. The run still ends failed.
	if report.Outcome != "refused" || report.ErrorCode != CodeNodeOutputInvalid {
		t.Fatalf("report = %+v", report)
	}
	if !strings.Contains(report.Error, "#/summary") {
		t.Fatalf("the schema refusal must locate the field: %q", report.Error)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["analyse"], ","); got != "pending,running,failed" {
		t.Fatalf("analyse states = %v", observed["analyse"])
	}
	if got := strings.Join(observed["end"], ","); got != "skipped" {
		t.Fatalf("end states = %v", observed["end"])
	}
	// No success output artifact exists for the failed node or for the run.
	if rows := nodeResultRows(client, "analyse"); len(rows) != 0 {
		t.Fatalf("a schema failure must not publish a data artifact: %+v", rows)
	}
	for _, upload := range client.uploads {
		if upload.Request.Purpose == "final_output" {
			t.Fatalf("a failed run must not seal a final output: %+v", upload)
		}
	}
	// One call, no retry and no "fix the JSON on another model" second attempt.
	if len(client.capabilityInputs) != 1 {
		t.Fatalf("capability calls = %d, want exactly one", len(client.capabilityInputs))
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "failed" ||
		client.proposals[0].ErrorCode != CodeNodeOutputInvalid {
		t.Fatalf("proposals = %+v", client.proposals)
	}
}

// ---------------------------------------------------------------------------
// prompt template: strict, bound inputs only
// ---------------------------------------------------------------------------

func TestGraphRunModelPromptRejectsUnknownAndExpressionPlaceholders(t *testing.T) {
	templates := []string{
		"{{missing}}",
		"{{task.amount}}",
		"{{task",
		"only a stray close }}",
		"{{ }}",
	}
	for _, template := range templates {
		document := modelGraphDocument(t, func(document map[string]any) {
			modelConfigOf(modelNodeOf(t, document))["prompt_template"] = template
		})
		client, report := runModelGraphDefault(t, document, modelSuccess(map[string]any{
			"structured": map[string]any{"summary": "never"},
		}))
		if report.Outcome != "refused" || report.ErrorCode != CodeNodePromptInvalid {
			t.Fatalf("template %q: report = %+v", template, report)
		}
		if len(client.capabilityInputs) != 0 {
			t.Fatalf("template %q: an invalid prompt must not spend a model call", template)
		}
		observed := observedNodeStates(client)
		if got := strings.Join(observed["analyse"], ","); got != "pending,running,failed" {
			t.Fatalf("template %q: analyse states = %v", template, observed["analyse"])
		}
		if got := strings.Join(observed["end"], ","); got != "skipped" {
			t.Fatalf("template %q: end states = %v", template, observed["end"])
		}
		if !strings.Contains(report.Error, "/nodes/1/config/prompt_template") {
			t.Fatalf("template %q: the refusal must be located: %q", template, report.Error)
		}
	}
}

func TestGraphRunModelPromptRendersOnlyBoundInputs(t *testing.T) {
	document := modelGraphDocument(t, func(document map[string]any) {
		node := modelNodeOf(t, document)
		node["inputs"] = map[string]any{
			"task":    map[string]any{"ref": "input#/task"},
			"payload": map[string]any{"ref": "input#/payload"},
			"count":   map[string]any{"ref": "input#/count"},
		}
		modelConfigOf(node)["prompt_template"] = "T={{ task }} P={{payload}} N={{count}}"
		document["input_schema"] = map[string]any{
			"type": "object",
			"properties": map[string]any{
				"task":    map[string]any{"type": "string"},
				"payload": map[string]any{"type": "object"},
				"count":   map[string]any{"type": "integer"},
			},
			"required": []any{"task"},
		}
	})
	client, report := runModelGraph(t, document,
		`{"task":"a","payload":{"x":1},"count":2}`,
		modelSuccess(map[string]any{"structured": map[string]any{"summary": "ok"}}), nil)
	if report.Outcome != "succeeded" {
		t.Fatalf("report = %+v", report)
	}
	message := client.capabilityInputs[0].Params["messages"].([]any)[0].(map[string]any)
	if message["content"] != `T=a P={"x":1} N=2` {
		t.Fatalf("prompt = %q", message["content"])
	}
}

// ---------------------------------------------------------------------------
// fixed profile and capability states
// ---------------------------------------------------------------------------

func TestGraphRunModelProfileUnavailableFailsBeforeAnyCall(t *testing.T) {
	document := modelGraphDocument(t, nil)
	cases := []struct {
		name  string
		facts Facts
		wire  bool
	}{
		{"not passed", &fakeFacts{model: ModelFact{ID: testModelProfileID, ValidationState: "pending"}}, true},
		{"revoked", &fakeFacts{model: ModelFact{ID: testModelProfileID, ValidationState: "passed", Revoked: true}}, true},
		// The executor must fail closed when the trusted catalog is not wired.
		{"no facts", nil, false},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			lease, manifest := graphLease(t, document)
			client := graphClient(t, lease, manifest, `{"task":"x"}`)
			client.invokeCapability = modelSuccess(map[string]any{
				"structured": map[string]any{"summary": "never"},
			})
			deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox())
			if testCase.wire {
				deps.Facts = testCase.facts
			}
			report := Run(context.Background(), deps)
			if report.Outcome != "refused" || report.ErrorCode != capabilities.CodeModelUnavailable {
				t.Fatalf("%s: report = %+v", testCase.name, report)
			}
			if len(client.capabilityInputs) != 0 {
				t.Fatalf("%s: an unusable profile must not spend a model call", testCase.name)
			}
			if got := strings.Join(observedNodeStates(client)["analyse"], ","); got != "pending,running,failed" {
				t.Fatalf("%s: analyse states = %v", testCase.name, observedNodeStates(client)["analyse"])
			}
		})
	}
}

func TestGraphRunModelCapabilityStatesMapToStableCodes(t *testing.T) {
	failure := func(state string, data map[string]any) func(capabilities.Input) (*capabilities.Result, error) {
		return func(input capabilities.Input) (*capabilities.Result, error) {
			return &capabilities.Result{OperationID: input.OperationID, State: state, Data: data}, nil
		}
	}
	cases := []struct {
		name string
		run  func(capabilities.Input) (*capabilities.Result, error)
		code string
	}{
		{
			name: "recorded failure replays its code",
			run:  failure("failed", map[string]any{"error_code": capabilities.CodeBudgetExhausted}),
			code: capabilities.CodeBudgetExhausted,
		},
		{
			name: "unknown outcome is never swallowed",
			run:  failure("unknown", map[string]any{}),
			code: CodeNodeCapabilityUnknown,
		},
		{
			name: "a provider response that violates the schema is a node schema failure",
			run: func(capabilities.Input) (*capabilities.Result, error) {
				return nil, codedError{code: capabilities.CodeProviderResponseInvalid}
			},
			code: CodeNodeOutputInvalid,
		},
		{
			name: "a refused profile keeps its capability code",
			run: func(capabilities.Input) (*capabilities.Result, error) {
				return nil, codedError{code: capabilities.CodeModelUnavailable}
			},
			code: capabilities.CodeModelUnavailable,
		},
		{
			name: "an uncoded transport failure is a node capability failure",
			run: func(capabilities.Input) (*capabilities.Result, error) {
				return nil, context.DeadlineExceeded
			},
			code: CodeNodeCapabilityFailed,
		},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			document := modelGraphDocument(t, nil)
			client, report := runModelGraphDefault(t, document, testCase.run)
			if report.Outcome != "refused" || report.ErrorCode != testCase.code {
				t.Fatalf("report = %+v, want code %q", report, testCase.code)
			}
			if len(client.capabilityInputs) != 1 {
				t.Fatalf("capability calls = %d, want one (never a hidden retry)", len(client.capabilityInputs))
			}
			if got := strings.Join(observedNodeStates(client)["end"], ","); got != "skipped" {
				t.Fatalf("end states = %v", observedNodeStates(client)["end"])
			}
		})
	}
}

func TestGraphRunModelAwaitingApprovalParksTheAttemptWithoutSuccess(t *testing.T) {
	document := modelGraphDocument(t, nil)
	client, report := runModelGraphDefault(t, document, func(input capabilities.Input) (*capabilities.Result, error) {
		return &capabilities.Result{OperationID: input.OperationID, State: "awaiting_approval"}, nil
	})
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
	operationID := deterministicOperationID(client.lease.RunID, "model:analyse:1")
	if proposal.CheckpointID != "capability:"+operationID {
		t.Fatalf("checkpoint = %q", proposal.CheckpointID)
	}
	for _, event := range client.nodeEvents {
		if event.NodeID == "analyse" && event.State == "succeeded" {
			t.Fatal("a parked model call must not report node success")
		}
	}
}

// ---------------------------------------------------------------------------
// recovery: a completed model node is reused, not re-called
// ---------------------------------------------------------------------------

func TestGraphRunModelReusesACompletedOutputWithoutCallingTheModel(t *testing.T) {
	document := modelGraphDocument(t, nil)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"task":"x"}`)
	client.invokeCapability = func(capabilities.Input) (*capabilities.Result, error) {
		return nil, codedError{code: "must_not_be_called"}
	}
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox())
	deps.Facts = currentFacts()
	deps.NodeOutputs = &fakeNodeOutputs{data: map[string][]byte{
		"analyse": []byte(`{"summary":"reused"}`),
	}}
	report := Run(context.Background(), deps)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.capabilityInputs) != 0 {
		t.Fatalf("a completed model node must not be re-called: %+v", client.capabilityInputs)
	}
	if got := strings.Join(observedNodeStates(client)["analyse"], ","); got != "succeeded" {
		t.Fatalf("analyse states = %v, want only the reused fact", observedNodeStates(client)["analyse"])
	}
	var final map[string]any
	for _, upload := range client.uploads {
		if upload.Request.Purpose != "final_output" {
			continue
		}
		if err := json.Unmarshal(upload.Content, &final); err != nil {
			t.Fatalf("final output: %v", err)
		}
	}
	if final["summary"] != "reused" {
		t.Fatalf("final output = %v", final)
	}
}
