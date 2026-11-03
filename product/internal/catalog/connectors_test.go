package catalog

// F06/T048: the connector half of the execution closure. The end-to-end proof
// lives in the real-stack pytest suite; these unit tests pin the pure rules that
// decide *which* connector policies join a manifest and how a broken one is
// reported, without a database.

import (
	"strings"
	"testing"
)

func TestConnectorRefsExpandEachToolsDeclaredPoliciesAtTheToolsPointer(t *testing.T) {
	refs := []dependencyRef{
		{kind: dependencyTool, id: "11111111-1111-4111-8111-111111111111", pointer: "/nodes/1/config/tool_version", nodeID: "stats"},
	}
	tools := map[string]toolDependency{
		"11111111-1111-4111-8111-111111111111": {
			connectorIDs: []string{
				"22222222-2222-4222-8222-222222222222",
				"33333333-3333-4333-8333-333333333333",
			},
		},
	}
	expanded := connectorRefsForTools(refs, tools)
	if len(expanded) != 2 {
		t.Fatalf("expanded=%d, want 2: %#v", len(expanded), expanded)
	}
	for _, ref := range expanded {
		if ref.kind != dependencyConnector {
			t.Errorf("kind=%q, want connector", ref.kind)
		}
		if ref.pointer != "/nodes/1/config/tool_version" {
			t.Errorf("pointer=%q; the problem must be located at the tool selection", ref.pointer)
		}
		if ref.nodeID != "stats" {
			t.Errorf("nodeID=%q", ref.nodeID)
		}
		if ref.source != "11111111-1111-4111-8111-111111111111" {
			t.Errorf("source=%q, want the tool version", ref.source)
		}
	}
	// The same connector declared twice (two tools) is one readiness fact.
	dup := connectorRefsForTools(append(refs, refs...), tools)
	if len(dup) != 2 {
		t.Fatalf("duplicate expansion produced %d refs: %#v", len(dup), dup)
	}
}

func TestConnectorReadinessIsApprovedAndUnrevoked(t *testing.T) {
	cases := []struct {
		name    string
		row     connectorDependency
		present bool
		code    string
	}{
		{
			name: "missing", present: false, code: CodeDependencyNotFound,
		},
		{
			name: "revoked", present: true,
			row:  connectorDependency{approved: true, revoked: true, policySHA256: strings.Repeat("a", 64)},
			code: CodeDependencyRevoked,
		},
		{
			name: "unapproved", present: true,
			row:  connectorDependency{approved: false, policySHA256: strings.Repeat("a", 64)},
			code: CodeDependencyNotReady,
		},
		{
			name: "no digest", present: true,
			row:  connectorDependency{approved: true},
			code: CodeDependencyNotReady,
		},
		{
			name: "ready", present: true,
			row:  connectorDependency{approved: true, policySHA256: strings.Repeat("a", 64)},
			code: "",
		},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			code, message := connectorReadinessProblem(testCase.row, testCase.present)
			if code != testCase.code {
				t.Fatalf("code=%q, want %q (%s)", code, testCase.code, message)
			}
			if code != "" && message == "" {
				t.Fatal("a refusal must carry a message")
			}
		})
	}
}
