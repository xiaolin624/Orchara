package catalog

import (
	"encoding/json"
	"testing"
)

// T045: the declared fallbacks of a primary profile join the frozen dependency
// closure, keeping the primary's document pointer and naming their source.
func TestFallbackModelRefsExpandsClosure(t *testing.T) {
	primary := "22222222-2222-4222-8222-222222222222"
	first := "33333333-3333-4333-8333-333333333333"
	second := "44444444-4444-4444-8444-444444444444"
	models := map[string]modelDependency{
		primary: {validationState: "passed", fallbackIDs: []string{first, second, first}},
	}
	refs := []dependencyRef{{
		kind: dependencyModel, id: primary,
		pointer: "/nodes/0/config/model_profile_version", nodeID: "node-1",
	}}
	expanded := fallbackModelRefs(refs, models)
	if len(expanded) != 2 {
		t.Fatalf("expanded = %d refs, want 2: %+v", len(expanded), expanded)
	}
	if expanded[0].id != first || expanded[1].id != second {
		t.Fatalf("configured order is not preserved: %+v", expanded)
	}
	for _, ref := range expanded {
		if ref.kind != dependencyModel || ref.source != primary {
			t.Fatalf("bad expanded ref: %+v", ref)
		}
		if ref.pointer != "/nodes/0/config/model_profile_version" || ref.nodeID != "node-1" {
			t.Fatalf("the primary's location was not kept: %+v", ref)
		}
	}
}

func TestFallbackModelRefsSkipsUnknownPrimaries(t *testing.T) {
	refs := []dependencyRef{{kind: dependencyModel, id: "55555555-5555-4555-8555-555555555555", pointer: "/p"}}
	if got := fallbackModelRefs(refs, map[string]modelDependency{}); len(got) != 0 {
		t.Fatalf("a missing primary must not expand: %+v", got)
	}
	toolRefs := []dependencyRef{{kind: dependencyTool, id: "55555555-5555-4555-8555-555555555555", pointer: "/p"}}
	if got := fallbackModelRefs(toolRefs, map[string]modelDependency{}); len(got) != 0 {
		t.Fatalf("a tool ref must not expand: %+v", got)
	}
}

func TestModelFallbackIDsKeepsInvalidEntries(t *testing.T) {
	config, err := json.Marshal(map[string]any{
		"fallback_profile_version_ids": []any{"not-a-uuid", " 66666666-6666-4666-8666-666666666666 "},
	})
	if err != nil {
		t.Fatal(err)
	}
	ids := modelFallbackIDs(config)
	if len(ids) != 2 || ids[0] != "not-a-uuid" || ids[1] != "66666666-6666-4666-8666-666666666666" {
		// Invalid entries are kept so readiness can report dependency_invalid_id.
		t.Fatalf("ids = %v", ids)
	}
	if got := modelFallbackIDs([]byte(`{"kind":"generation"}`)); got != nil {
		t.Fatalf("no fallback list = %v", got)
	}
	if got := modelFallbackIDs([]byte(`not json`)); got != nil {
		t.Fatalf("invalid config = %v", got)
	}
}

// A fallback is subject to the same readiness rule as the primary: it must
// exist, be passed and be unrevoked.
func TestFallbackReadinessUsesModelRule(t *testing.T) {
	pending := modelDependency{validationState: "pending"}
	if code, _ := modelReadinessProblem(pending, true); code != CodeDependencyNotReady {
		t.Fatalf("a pending fallback must be not ready, got %q", code)
	}
	revoked := modelDependency{validationState: "passed", revoked: true}
	if code, _ := modelReadinessProblem(revoked, true); code != CodeDependencyRevoked {
		t.Fatalf("a revoked fallback must be refused, got %q", code)
	}
	if code, _ := modelReadinessProblem(modelDependency{}, false); code != CodeDependencyNotFound {
		t.Fatalf("a missing fallback must be not found, got %q", code)
	}
}
