package catalog

import (
	"database/sql"
	"encoding/json"
	"strings"
	"testing"
)

func validDigest() sql.NullString {
	return sql.NullString{String: "sha256:" + strings.Repeat("b", 64), Valid: true}
}

func TestModelReadinessRules(t *testing.T) {
	missingCode, _ := modelReadinessProblem(modelDependency{}, false)
	if missingCode != CodeDependencyNotFound {
		t.Fatalf("missing model = %q", missingCode)
	}
	revokedCode, _ := modelReadinessProblem(modelDependency{validationState: "passed", revoked: true}, true)
	if revokedCode != CodeDependencyRevoked {
		t.Fatalf("revoked model = %q", revokedCode)
	}
	pendingCode, message := modelReadinessProblem(modelDependency{validationState: "pending"}, true)
	if pendingCode != CodeDependencyNotReady {
		t.Fatalf("pending model = %q", pendingCode)
	}
	if message == "" {
		t.Fatal("the readiness problem has no message")
	}
	if code, _ := modelReadinessProblem(modelDependency{validationState: "passed"}, true); code != "" {
		t.Fatalf("a passed model must be usable, got %q", code)
	}
}

func TestToolReadinessRules(t *testing.T) {
	caller := "11111111-1111-4111-8111-111111111111"
	owner := "22222222-2222-4222-8222-222222222222"
	readyApproved := toolDependency{ownerID: caller, state: "ready", approved: true, imageDigest: validDigest()}
	readyOwnUnapproved := toolDependency{ownerID: caller, state: "ready", approved: false, imageDigest: validDigest()}
	readyOtherUnapproved := toolDependency{ownerID: owner, state: "ready", approved: false, imageDigest: validDigest()}

	cases := []struct {
		name         string
		row          toolDependency
		present      bool
		privateDebug bool
		want         string
	}{
		{"missing", toolDependency{}, false, false, CodeDependencyNotFound},
		{"building", toolDependency{ownerID: caller, state: "building"}, true, false, CodeDependencyNotReady},
		{"failed", toolDependency{ownerID: caller, state: "failed"}, true, false, CodeDependencyNotReady},
		{"revoked", toolDependency{ownerID: caller, state: "revoked"}, true, false, CodeDependencyRevoked},
		{"ready without digest", toolDependency{ownerID: caller, state: "ready", approved: true}, true, false, CodeDependencyNotReady},
		{"unapproved shared", readyOwnUnapproved, true, false, CodeDependencyNotReady},
		{"approved shared", readyApproved, true, false, ""},
		{"authors own tool in private debug", readyOwnUnapproved, true, true, ""},
		{"another principals unapproved tool in private debug", readyOtherUnapproved, true, true, CodeDependencyNotVisible},
		{"another principals approved tool in private debug", toolDependency{ownerID: owner, state: "ready", approved: true, imageDigest: validDigest()}, true, true, ""},
	}
	for _, entry := range cases {
		t.Run(entry.name, func(t *testing.T) {
			code, _ := toolReadinessProblem(entry.row, entry.present, caller, entry.privateDebug)
			if code != entry.want {
				t.Fatalf("code = %q, want %q", code, entry.want)
			}
		})
	}
}

func TestSubappReadinessRules(t *testing.T) {
	if code, _ := subappReadinessProblem(subappDependency{}, false); code != CodeDependencyNotFound {
		t.Fatalf("missing sub-app = %q", code)
	}
	if code, _ := subappReadinessProblem(subappDependency{revoked: true}, true); code != CodeDependencyRevoked {
		t.Fatalf("revoked sub-app = %q", code)
	}
	if code, _ := subappReadinessProblem(subappDependency{archived: true}, true); code != CodeDependencyNotVisible {
		t.Fatalf("archived sub-app = %q", code)
	}
	if code, _ := subappReadinessProblem(subappDependency{}, true); code != "" {
		t.Fatalf("a live sub-app must pass the row rule, got %q", code)
	}
}

func TestCollectDependencyRefsLocatesEveryConfigField(t *testing.T) {
	document := map[string]any{
		"nodes": []any{
			map[string]any{"id": "start", "type": "start", "config": map[string]any{}},
			map[string]any{"id": "stats", "type": "python", "config": map[string]any{
				"tool_version": "11111111-1111-4111-8111-111111111111",
			}},
			map[string]any{"id": "analysis", "type": "agent", "config": map[string]any{
				"model_profile_version": "22222222-2222-4222-8222-222222222222",
				"tool_versions":         []any{"33333333-3333-4333-8333-333333333333", "11111111-1111-4111-8111-111111111111"},
				"subapp_versions":       []any{"44444444-4444-4444-8444-444444444444"},
			}},
			map[string]any{"id": "child", "type": "call_app", "config": map[string]any{
				"application_version": "55555555-5555-4555-8555-555555555555",
			}},
		},
	}
	refs := collectDependencyRefs(document)
	// Document order, with the python tool and the agent's reference to the same
	// tool kept as two locations for one dependency.
	want := []struct{ kind, id, pointer, nodeID string }{
		{"tool", "11111111-1111-4111-8111-111111111111", "/nodes/1/config/tool_version", "stats"},
		{"model", "22222222-2222-4222-8222-222222222222", "/nodes/2/config/model_profile_version", "analysis"},
		{"tool", "33333333-3333-4333-8333-333333333333", "/nodes/2/config/tool_versions/0", "analysis"},
		{"tool", "11111111-1111-4111-8111-111111111111", "/nodes/2/config/tool_versions/1", "analysis"},
		{"subapp", "44444444-4444-4444-8444-444444444444", "/nodes/2/config/subapp_versions/0", "analysis"},
		{"subapp", "55555555-5555-4555-8555-555555555555", "/nodes/3/config/application_version", "child"},
	}
	if len(refs) != len(want) {
		t.Fatalf("collected %d refs, want %d: %+v", len(refs), len(want), refs)
	}
	for index, expected := range want {
		ref := refs[index]
		if ref.kind != expected.kind || ref.id != expected.id ||
			ref.pointer != expected.pointer || ref.nodeID != expected.nodeID {
			t.Fatalf("ref[%d] = %+v, want %+v", index, ref, expected)
		}
	}
}

func TestFindCycleDetectsARealCycleAndLeavesTheAcyclicGraphAlone(t *testing.T) {
	if path := findCycle(map[string][]string{
		"a": {"b"}, "b": {"c"}, "c": {"a"},
	}); len(path) != 4 || path[0] != "a" || path[len(path)-1] != "a" {
		t.Fatalf("cycle path = %v", path)
	}
	if path := findCycle(map[string][]string{"a": {"b"}, "b": {"c"}}); path != nil {
		t.Fatalf("acyclic graph reported a cycle: %v", path)
	}
	if got := reachableVersions("a", map[string][]string{"a": {"b"}, "b": {"c"}, "c": {"a"}}); len(got) != 3 {
		t.Fatalf("reachable = %v", got)
	}
}

func TestCapabilityCardDocumentLocatesTheEmptyCardAndPartialShapes(t *testing.T) {
	_, located := capabilityCardDocument(json.RawMessage(`{}`))
	if len(located) != 1 || located[0].Code != CodeCapabilityCardIncomplete || located[0].Pointer != "/capability_card" {
		t.Fatalf("empty card errors = %+v", located)
	}
	raw := json.RawMessage(`{"purpose":"p","includes":[],"excludes":[],"required_inputs":[],"expected_outputs":[]}`)
	_, located = capabilityCardDocument(raw)
	if len(located) != 1 || located[0].Pointer != "/capability_card/examples" {
		t.Fatalf("missing examples errors = %+v", located)
	}
	card, located := capabilityCardDocument(json.RawMessage(`{"purpose":"p","includes":[],"excludes":[],"required_inputs":[],"expected_outputs":[],"examples":["a","b"]}`))
	if len(located) != 0 || card == nil {
		t.Fatalf("a full card must be accepted: %+v / %+v", card, located)
	}
}
