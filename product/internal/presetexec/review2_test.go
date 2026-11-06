package presetexec

// Batch review 2 regressions: four findings from the independent review of
// ea56392..525a28f. Each test reproduces the reported behaviour on the old code:
//   - a retrieve node reused from the recovery seam keeps a deleted document's
//     excerpt without re-checking readability;
//   - a waiting hand-over swallows a sibling branch's sandbox failure and
//     certifies the exit from the waiting node's (never started) sandbox;
//   - condition/join outputs bypass the node output_schema;
//   - the approval server takes the title/expiry from the worker request instead
//     of the sealed definition (see the approvals package test and the T087
//     real-stack case).

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"orchara/platform/internal/sandbox"
)

// ---------------------------------------------------------------------------
// finding 1: retrieve output must not be reused across a resume
// ---------------------------------------------------------------------------

func TestGraphRunResumeReExecutesRetrieveInsteadOfReusingTheSavedExcerpt(t *testing.T) {
	document := definitionDocument(t, retrieveDefinition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{"question":"north"}`)
	saved, err := json.Marshal(map[string]any{
		"items":     []any{},
		"citations": []any{},
		"count":     0,
	})
	if err != nil {
		t.Fatal(err)
	}
	scriptedRetrieve(client, &RetrieveResult{
		Items:     []RetrievedItem{retrievedItem("chunk-1", "doc-1", "north 1234")},
		Requested: 1, Authorized: 1,
	}, nil)
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox())
	deps.NodeOutputs = &fakeNodeOutputs{data: map[string][]byte{"search": saved}}

	report := Run(context.Background(), deps)
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if len(client.retrieveRequests) != 1 {
		t.Fatalf("retrieve requests = %d; a resumed retrieve must re-check document readability, not reuse the saved excerpt",
			len(client.retrieveRequests))
	}
	output := artifactData(t, client, "search")
	items, _ := output["items"].([]any)
	if len(items) != 1 {
		t.Fatalf("the fresh retrieve result must reach the node output: %v", output)
	}
}

// ---------------------------------------------------------------------------
// finding 3: a waiting hand-over must not hide a sibling sandbox failure
// ---------------------------------------------------------------------------

const approvalParallelWorkerDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object"},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
    {"id":"approve","type":"approval",
     "inputs":{"proposal":{"literal":{"subject":"order-7"}}},
     "config":{"title":"Approve the batch","expires_seconds":600},
     "output_schema":{"type":"object"}},
    {"id":"work","type":"python","inputs":{"file":{"ref":"node:start#/artifact"}},
     "config":{"tool_version":"11111111-1111-4111-8111-111111111111"},
     "output_schema":{"type":"object"}},
    {"id":"join","type":"join","inputs":{},
     "config":{"fork_id":"start","mode":"all_active",
               "required_sources":["approve","work"],"optional_sources":[]},
     "output_schema":{"type":"object"}},
    {"id":"end","type":"end","inputs":{"results":{"ref":"node:join#/results"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [
    {"source":"start","target":"approve"},
    {"source":"start","target":"work"},
    {"source":"approve","target":"join"},
    {"source":"work","target":"join"},
    {"source":"join","target":"end"}
  ],
  "policy": {"max_active_seconds":900,"max_child_depth":3,"max_child_calls":12,
             "budget_limit":"1.00","currency":"USD"}
}`

func TestGraphRunWaitingHandoverDoesNotSwallowASiblingSandboxFailure(t *testing.T) {
	document := definitionDocument(t, approvalParallelWorkerDefinition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, `{}`)
	client.nodeApprovalScript = []*NodeApproval{{
		ID:           "11111111-1111-4111-8111-111111111111",
		State:        "pending",
		ParamsSHA256: strings.Repeat("a", 64),
		ExpiresAt:    time.Now().Add(10 * time.Minute),
	}}
	prepared := make(chan struct{})
	client.onPrepareNodeApproval = func() { close(prepared) }
	box := &branchSandbox{
		unitBase: "unit-test-1",
		scripts: map[string]func(context.Context) (sandbox.Outcome, error){
			"work": func(context.Context) (sandbox.Outcome, error) {
				<-prepared
				outcome := successSandbox().outcome
				outcome.SandboxStarted = true
				outcome.ExitCode = 1
				return outcome, sandbox.ErrOrphan
			},
		},
	}
	deps := baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox())
	deps.Sandbox = box

	report := Run(context.Background(), deps)
	if report.Outcome != "failed" {
		t.Fatalf("a started sibling whose cleanup is unproven must fail the run, not hand it over: %+v", report)
	}
	if report.ErrorCode != CodeSandboxOrphan {
		t.Fatalf("error code = %q, want %q", report.ErrorCode, CodeSandboxOrphan)
	}
	if len(client.proposals) != 1 || client.proposals[0].Proposal.SuggestedState == "waiting_approval" {
		t.Fatalf("no waiting proposal may be submitted after a sibling failure: %+v", client.proposals)
	}
	if got := client.proposals[0].Proposal.SuggestedState; got != "failed" {
		t.Fatalf("the committed proposal must be the failure, got %q", got)
	}
	if len(client.exits) != 1 {
		t.Fatalf("exits = %+v", client.exits)
	}
	if started, _ := client.exits[0].Evidence["started"].(bool); !started {
		t.Fatalf("the exit evidence must certify the started sibling sandbox: %+v", client.exits[0].Evidence)
	}
}

// ---------------------------------------------------------------------------
// finding 4: condition/join outputs must be validated against output_schema
// ---------------------------------------------------------------------------

const conditionSchemaDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object","properties":{"requires_approval":{"type":"boolean"}},
                   "required":["requires_approval"],"additionalProperties":false},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
    {"id":"choose","type":"condition","inputs":{"requires_approval":{"ref":"input#/requires_approval"}},
     "config":{"join_id":"join","default_port":"no",
               "cases":[{"input":"requires_approval","op":"eq","value":true,"port":"yes"}]},
     "output_schema":{"type":"object","properties":{"port":{"type":"string"},"phantom":{"type":"string"}},
                      "required":["port","phantom"],"additionalProperties":false}},
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

const joinSchemaDefinition = `{
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
               "required_sources":["left","right"],"optional_sources":[]},
     "output_schema":{"type":"object","properties":{"results":{"type":"object"},"phantom":{"type":"string"}},
                      "required":["results","phantom"],"additionalProperties":false}},
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

func TestGraphRunConditionOutputIsValidatedAgainstItsSchema(t *testing.T) {
	client, report := runGraphAttempt(t, conditionSchemaDefinition, `{"requires_approval":true}`, successSandbox())
	if (report.Outcome != "failed" && report.Outcome != "refused") ||
		report.ErrorCode != CodeNodeOutputInvalid {
		t.Fatalf("report = %+v; a condition output that misses a required schema field must fail", report)
	}
	if rows := nodeResultRows(client, "choose"); len(rows) != 0 {
		t.Fatalf("an invalid condition output must not be published: %+v", rows)
	}
}

func TestGraphRunJoinOutputIsValidatedAgainstItsSchema(t *testing.T) {
	client, report := runGraphAttempt(t, joinSchemaDefinition, `{"requires_approval":true}`, successSandbox())
	if (report.Outcome != "failed" && report.Outcome != "refused") ||
		report.ErrorCode != CodeNodeOutputInvalid {
		t.Fatalf("report = %+v; a join output that misses a required schema field must fail", report)
	}
	if rows := nodeResultRows(client, "join"); len(rows) != 0 {
		t.Fatalf("an invalid join output must not be published: %+v", rows)
	}
}
