package presetexec

// F04B/T087: the approval node on the graph scheduler.
//
// These are unit-level attempts of the real scheduler with the executor's
// protocol seams scripted (client/inputs/sandbox). They pin what the executor
// does *around* the server-built approval: it asks the platform to prepare the
// approval from the run facts, parks with the fixed `approval:<id>` checkpoint,
// publishes no node output until the decision is approved, and never invents a
// decision locally. The real PostgreSQL + mTLS + supervisor evidence lives in
// product/tests/f04b/test_t087_approval_node.py.

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// approvalDefinition is the identity approval graph:
//
//	start -> approve1(approval) -> end
//
// The proposal is a bound input (literal object) and the approval node's output
// carries the decision facts the end node maps into the final output.
const approvalDefinition = `{
  "dsl_version": "1.0",
  "input_schema": {"type":"object"},
  "output_schema": {"type":"object"},
  "nodes": [
    {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
    {"id":"approve1","type":"approval",
     "inputs":{"proposal":{"literal":{"subject":"order-42","amount":"1250.00"}}},
     "config":{"title":"Approve the order","expires_seconds":600},
     "output_schema":{"type":"object",
       "properties":{"approval_id":{"type":"string"},"decision":{"type":"string"},
                     "decided_at":{"type":"string"},"params_sha256":{"type":"string"},
                     "proposal":{"type":"object"}},
       "required":["approval_id","decision","decided_at","params_sha256","proposal"],
       "additionalProperties":false}},
    {"id":"end","type":"end","inputs":{"approval_id":{"ref":"node:approve1#/approval_id"},
                                       "decision":{"ref":"node:approve1#/decision"}},
     "config":{},"output_schema":{"type":"object"}}
  ],
  "edges": [
    {"source":"start","target":"approve1"},
    {"source":"approve1","target":"end"}
  ],
  "policy": {"max_active_seconds":900,"max_child_depth":3,"max_child_calls":12,
             "budget_limit":"1.00","currency":"USD"}
}`

func decidedAtValue() *time.Time {
	value := time.Date(2026, 11, 5, 12, 0, 0, 0, time.UTC)
	return &value
}

func runApprovalAttempt(
	t *testing.T, rawDefinition, runInput string, script []*NodeApproval,
) (*fakeClient, *fakeSandbox, Report) {
	t.Helper()
	document := definitionDocument(t, rawDefinition)
	lease, manifest := graphLease(t, document)
	client := graphClient(t, lease, manifest, runInput)
	client.nodeApprovalScript = script
	box := successSandbox()
	report := Run(context.Background(), baseDeps(t, client, &fakeInputs{inputs: client.inputs}, box))
	return client, box, report
}

func TestGraphRunApprovalNodeParksAtTheServerApprovalWithTheFixedCheckpoint(t *testing.T) {
	client, box, report := runApprovalAttempt(t, approvalDefinition, `{}`, []*NodeApproval{{
		ID:            "11111111-1111-4111-8111-111111111111",
		State:         "pending",
		ParamsSHA256:  strings.Repeat("a", 64),
		ExpiresAt:     time.Now().Add(10 * time.Minute),
		ProposalArtifactID: "22222222-2222-4222-8222-222222222222",
	}})
	if report.Outcome != "waiting_approval" || report.ErrorCode != CodeApprovalRequired {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 0 {
		t.Fatalf("sandbox calls = %d; an approval identity graph runs no sandbox", box.calls)
	}

	// The executor asked the platform to prepare the approval from run facts, not
	// from worker-supplied text: the node id, the sealed config and the resolved
	// proposal object are the only inputs.
	if len(client.nodeApprovalRequests) != 1 {
		t.Fatalf("prepare requests = %+v", client.nodeApprovalRequests)
	}
	request := client.nodeApprovalRequests[0]
	if request.NodeID != "approve1" || request.Title != "Approve the order" ||
		request.ExpiresSeconds != 600 {
		t.Fatalf("prepare request = %+v", request)
	}
	if request.Proposal["subject"] != "order-42" || request.Proposal["amount"] != "1250.00" {
		t.Fatalf("prepare proposal = %+v", request.Proposal)
	}

	// Exactly one waiting proposal, at the stable checkpoint that names the
	// approval (not a capability operation).
	if len(client.proposals) != 1 {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	proposal := client.proposals[0].Proposal
	if proposal.SuggestedState != "waiting_approval" ||
		proposal.CheckpointID != "approval:11111111-1111-4111-8111-111111111111" ||
		proposal.ErrorCode != "approval_required" {
		t.Fatalf("waiting proposal = %+v", proposal)
	}
	// The supervisor exit fact is certified before any resume may exist.
	if len(client.exits) != 1 {
		t.Fatalf("exits = %+v", client.exits)
	}
	// A parked node does not report success and produces no data artifact.
	if rows := nodeResultRows(client, "approve1"); len(rows) != 0 {
		t.Fatalf("approval node data rows = %+v", rows)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["approve1"], ","); got != "pending,running" {
		t.Fatalf("approve1 states = %v", observed["approve1"])
	}
}

func TestGraphRunApprovedNodePublishesTheDecisionAndContinues(t *testing.T) {
	client, box, report := runApprovalAttempt(t, approvalDefinition, `{}`, []*NodeApproval{
		{
			ID:                 "11111111-1111-4111-8111-111111111111",
			State:              "approved",
			ParamsSHA256:       strings.Repeat("a", 64),
			ExpiresAt:          time.Now().Add(10 * time.Minute),
			ProposalArtifactID: "22222222-2222-4222-8222-222222222222",
			DecidedAt:          decidedAtValue(),
		},
	})
	if report.Outcome != "succeeded" || report.ErrorCode != "" {
		t.Fatalf("report = %+v", report)
	}
	if box.calls != 0 {
		t.Fatalf("sandbox calls = %d", box.calls)
	}
	if got := strings.Join(observedNodeStates(client)["approve1"], ","); got != "pending,running,succeeded" {
		t.Fatalf("approve1 states = %v", observedNodeStates(client)["approve1"])
	}
	document := artifactData(t, client, "approve1")
	if document["approval_id"] != "11111111-1111-4111-8111-111111111111" {
		t.Fatalf("approval output = %v", document)
	}
	if document["decision"] != "approved" || document["decided_at"] != "2026-11-05T12:00:00Z" {
		t.Fatalf("approval decision facts = %v", document)
	}
	if document["params_sha256"] != strings.Repeat("a", 64) {
		t.Fatalf("approval hash = %v", document)
	}
	proposal, ok := document["proposal"].(map[string]any)
	if !ok || proposal["subject"] != "order-42" {
		t.Fatalf("approval proposal = %v", document["proposal"])
	}
	if rows := nodeResultRows(client, "approve1"); len(rows) != 1 || rows[0].Kind != "data" {
		t.Fatalf("approval rows = %+v", rows)
	}

	// The end node mapped only the decision facts; the final output was sealed by
	// the same committed-succeeded path as every other node.
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
	if finalDocument["approval_id"] != document["approval_id"] ||
		finalDocument["decision"] != "approved" {
		t.Fatalf("final output = %v", finalDocument)
	}
}

func TestGraphRunRejectedNodeApprovalFailsWithoutPublishingOutput(t *testing.T) {
	client, _, report := runApprovalAttempt(t, approvalDefinition, `{}`, []*NodeApproval{{
		ID:            "11111111-1111-4111-8111-111111111111",
		State:         "rejected",
		ParamsSHA256:  strings.Repeat("a", 64),
		ExpiresAt:     time.Now().Add(10 * time.Minute),
		DecidedAt:     decidedAtValue(),
	}})
	if report.ErrorCode != CodeApprovalRejected {
		t.Fatalf("report = %+v", report)
	}
	if rows := nodeResultRows(client, "approve1"); len(rows) != 0 {
		t.Fatalf("a rejected approval must not publish node data: %+v", rows)
	}
	observed := observedNodeStates(client)
	if got := strings.Join(observed["approve1"], ","); got != "pending,running,failed" {
		t.Fatalf("approve1 states = %v", observed["approve1"])
	}
	if got := strings.Join(observed["end"], ","); got != "skipped" {
		t.Fatalf("end states = %v", observed["end"])
	}
	if len(client.proposals) != 1 || client.proposals[0].SuggestedState != "failed" {
		t.Fatalf("proposals = %+v", client.proposals)
	}
	if client.proposals[0].ErrorCode != CodeApprovalRejected {
		t.Fatalf("failed proposal code = %q", client.proposals[0].ErrorCode)
	}
}

func TestGraphRunExpiredNodeApprovalFailsWithTheExpiredCode(t *testing.T) {
	client, _, report := runApprovalAttempt(t, approvalDefinition, `{}`, []*NodeApproval{{
		ID:           "11111111-1111-4111-8111-111111111111",
		State:        "expired",
		ParamsSHA256: strings.Repeat("a", 64),
		ExpiresAt:    time.Now().Add(-time.Minute),
	}})
	if report.ErrorCode != CodeApprovalExpired {
		t.Fatalf("report = %+v", report)
	}
	if len(client.proposals) != 1 || client.proposals[0].ErrorCode != CodeApprovalExpired {
		t.Fatalf("proposals = %+v", client.proposals)
	}
}

func TestGraphRunApprovalNodeRejectsAnInvalidConfigOrMissingProposal(t *testing.T) {
	for name, raw := range map[string]string{
		"missing_title": `{
          "dsl_version":"1.0","input_schema":{"type":"object"},"output_schema":{"type":"object"},
          "nodes":[
            {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
            {"id":"approve1","type":"approval","inputs":{"proposal":{"literal":{"a":1}}},
             "config":{"expires_seconds":600},"output_schema":{"type":"object"}},
            {"id":"end","type":"end","inputs":{},"config":{},"output_schema":{"type":"object"}}],
          "edges":[{"source":"start","target":"approve1"},{"source":"approve1","target":"end"}],
          "policy":{"max_active_seconds":900,"max_child_depth":0,"max_child_calls":0,
                    "budget_limit":"1.00","currency":"USD"}}`,
		"expiry_out_of_range": `{
          "dsl_version":"1.0","input_schema":{"type":"object"},"output_schema":{"type":"object"},
          "nodes":[
            {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
            {"id":"approve1","type":"approval","inputs":{"proposal":{"literal":{"a":1}}},
             "config":{"title":"t","expires_seconds":5},"output_schema":{"type":"object"}},
            {"id":"end","type":"end","inputs":{},"config":{},"output_schema":{"type":"object"}}],
          "edges":[{"source":"start","target":"approve1"},{"source":"approve1","target":"end"}],
          "policy":{"max_active_seconds":900,"max_child_depth":0,"max_child_calls":0,
                    "budget_limit":"1.00","currency":"USD"}}`,
		"missing_proposal": `{
          "dsl_version":"1.0","input_schema":{"type":"object"},"output_schema":{"type":"object"},
          "nodes":[
            {"id":"start","type":"start","inputs":{},"config":{},"output_schema":{"type":"object"}},
            {"id":"approve1","type":"approval","inputs":{},
             "config":{"title":"t","expires_seconds":600},"output_schema":{"type":"object"}},
            {"id":"end","type":"end","inputs":{},"config":{},"output_schema":{"type":"object"}}],
          "edges":[{"source":"start","target":"approve1"},{"source":"approve1","target":"end"}],
          "policy":{"max_active_seconds":900,"max_child_depth":0,"max_child_calls":0,
                    "budget_limit":"1.00","currency":"USD"}}`,
	} {
		t.Run(name, func(t *testing.T) {
			document := definitionDocument(t, raw)
			lease, manifest := graphLease(t, document)
			client := graphClient(t, lease, manifest, `{}`)
			report := Run(context.Background(),
				baseDeps(t, client, &fakeInputs{inputs: client.inputs}, successSandbox()))
			if report.Outcome != "refused" || report.ErrorCode != CodeBadConfig {
				t.Fatalf("report = %+v", report)
			}
			if len(client.nodeEvents) != 0 || len(client.nodeApprovalRequests) != 0 {
				t.Fatalf("a plan refusal must not touch the run: events=%v requests=%v",
					client.nodeEvents, client.nodeApprovalRequests)
			}
		})
	}
}

func TestGraphRunApprovalNodeWithAChangedProposalAsksAgain(t *testing.T) {
	// The same node with a different resolved proposal is a different approval:
	// the executor forwards it and the server (not the worker) decides identity.
	definition := strings.Replace(approvalDefinition,
		`"literal":{"subject":"order-42","amount":"1250.00"}`,
		`"literal":{"subject":"order-42","amount":"999.00"}`, 1)
	client, _, _ := runApprovalAttempt(t, definition, `{}`, []*NodeApproval{{
		ID:           "11111111-1111-4111-8111-111111111111",
		State:        "pending",
		ParamsSHA256: strings.Repeat("b", 64),
		ExpiresAt:    time.Now().Add(10 * time.Minute),
	}})
	if len(client.nodeApprovalRequests) != 1 {
		t.Fatalf("prepare requests = %+v", client.nodeApprovalRequests)
	}
	if client.nodeApprovalRequests[0].Proposal["amount"] != "999.00" {
		t.Fatalf("prepare proposal = %+v", client.nodeApprovalRequests[0].Proposal)
	}
}
