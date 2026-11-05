package presetexec

// F04B/T083: agent and call_app nodes inside the graph scheduler.
//
// The graph path shares the linear node segments by presenting them as a one-node
// linear view. These tests pin that the shared code still works when it is the
// graph (not the chain) that owns activation, skip propagation and the join.

import (
	"context"
	"strings"
	"testing"
)

const graphAgentDefinitionJSON = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object","properties":{"requires_approval":{"type":"boolean"}},
                   "required":["requires_approval"],"additionalProperties":false},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"output_schema":{"type":"object"},"config":{}},
    {"id":"choose","type":"condition","inputs":{"requires_approval":{"ref":"input#/requires_approval"}},
     "config":{"join_id":"join","default_port":"no",
               "cases":[{"input":"requires_approval","op":"eq","value":true,"port":"yes"}]},
     "output_schema":{"type":"object"}},
    {"id":"analysis","type":"agent","inputs":{"task":{"literal":"graph agent"}},
     "output_schema":{"type":"object","properties":{"text":{"type":"string"}},
                      "required":["text"],"additionalProperties":false},
     "config":{"model_profile_version":"` + testModelProfileID + `",
               "instructions":"Summarise the task.",
               "max_turns":2,
               "tool_versions":["` + PresetToolID + `"],
               "subapp_versions":[]}},
    {"id":"left","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"` + PresetToolID + `"},"output_schema":{"type":"object"}},
    {"id":"join","type":"join","inputs":{},
     "config":{"fork_id":"choose","mode":"all_active",
               "required_sources":["analysis","left"],"optional_sources":[]},
     "output_schema":{"type":"object"}},
    {"id":"end","type":"end","inputs":{"results":{"ref":"node:join#/results"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [
    {"source":"start","target":"choose"},
    {"source":"choose","target":"analysis","port":"yes"},
    {"source":"choose","target":"left","port":"no"},
    {"source":"analysis","target":"join"},
    {"source":"left","target":"join"},
    {"source":"join","target":"end"}
  ]
}`

const graphCallAppDefinitionJSON = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object","properties":{"requires_approval":{"type":"boolean"}},
                   "required":["requires_approval"],"additionalProperties":false},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"output_schema":{"type":"object"},"config":{}},
    {"id":"choose","type":"condition","inputs":{"requires_approval":{"ref":"input#/requires_approval"}},
     "config":{"join_id":"join","default_port":"no",
               "cases":[{"input":"requires_approval","op":"eq","value":true,"port":"yes"}]},
     "output_schema":{"type":"object"}},
    {"id":"child","type":"call_app","inputs":{"source":{"literal":"graph"}},
     "output_schema":{"type":"object","properties":{"answer":{"type":"string"}},
                      "required":["answer"],"additionalProperties":false},
     "config":{"application_version":"33333333-4444-4555-8666-777777777777"}},
    {"id":"left","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"` + PresetToolID + `"},"output_schema":{"type":"object"}},
    {"id":"join","type":"join","inputs":{},
     "config":{"fork_id":"choose","mode":"all_active",
               "required_sources":["child","left"],"optional_sources":[]},
     "output_schema":{"type":"object"}},
    {"id":"end","type":"end","inputs":{"results":{"ref":"node:join#/results"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [
    {"source":"start","target":"choose"},
    {"source":"choose","target":"child","port":"yes"},
    {"source":"choose","target":"left","port":"no"},
    {"source":"child","target":"join"},
    {"source":"left","target":"join"},
    {"source":"join","target":"end"}
  ]
}`

func TestGraphRunAgentNodeUsesTheControlledLoopInsideAGraph(t *testing.T) {
	document := definitionDocument(t, graphAgentDefinitionJSON)
	// The agent node's declared tool version must be in the sealed tool closure.
	document.ToolArtifacts = []manifestToolArtifact{{
		ID: PresetToolID, ImageDigest: "sha256:" + strings.Repeat("bb", 32),
	}}
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"requires_approval":true}`)
	script := &agentScript{final: map[string]any{"text": "graph agent"}}
	client.invokeCapability = script.invoke
	deps := agentDeps(t, client, successSandbox(), currentFacts())

	report := Run(context.Background(), deps)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["analysis"], ","); got != "pending,running,succeeded" {
		t.Fatalf("agent node states = %v", observed["analysis"])
	}
	if got := strings.Join(observed["left"], ","); got != "skipped" {
		t.Fatalf("unselected branch states = %v", observed["left"])
	}
	if script.modelTurns != 1 {
		t.Fatalf("the agent node made %d model turns, want one controlled generation", script.modelTurns)
	}
	results := artifactData(t, client, "join")["results"].(map[string]any)
	analysis, _ := results["analysis"].(map[string]any)
	if analysis["status"] != "succeeded" {
		t.Fatalf("agent envelope = %v", analysis)
	}
	value, _ := analysis["value"].(map[string]any)
	if value == nil || value["text"] != "graph agent" {
		t.Fatalf("agent output did not reach the join: %v", analysis)
	}
}

func TestGraphRunCallAppNodeDelegatesInsideAGraph(t *testing.T) {
	document := definitionDocument(t, graphCallAppDefinitionJSON)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"requires_approval":true}`)
	client.spawnChild = func(request SpawnChildRequest) (*ChildRun, error) {
		return &ChildRun{ID: "child-run-1", State: "succeeded",
			ApplicationVersionID: request.ApplicationVersionID}, nil
	}
	client.childResult = func(childRunID string) (*ChildResult, error) {
		return &ChildResult{ChildRunID: childRunID, State: "succeeded",
			Output: map[string]any{"answer": "ok"}}, nil
	}

	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox()))
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.spawnRequests) != 1 || client.spawnRequests[0].LogicalKey != "call_app:child" {
		t.Fatalf("spawn requests = %+v", client.spawnRequests)
	}
	if len(client.childReads) != 1 || client.childReads[0] != "child-run-1" {
		t.Fatalf("child reads = %+v", client.childReads)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["child"], ","); got != "pending,running,succeeded" {
		t.Fatalf("call_app states = %v", observed["child"])
	}
	if got := strings.Join(observed["left"], ","); got != "skipped" {
		t.Fatalf("unselected branch states = %v", observed["left"])
	}
	results := artifactData(t, client, "join")["results"].(map[string]any)
	child, _ := results["child"].(map[string]any)
	if child["status"] != "succeeded" {
		t.Fatalf("call_app envelope = %v", child)
	}
	value, _ := child["value"].(map[string]any)
	if value == nil || value["answer"] != "ok" {
		t.Fatalf("child output did not reach the join: %v", child)
	}
}
