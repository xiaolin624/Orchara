package catalog

// Unit tests for the debug submission's pure decision layer. The database and
// HTTP behaviour lives in product/tests/f02a (real PostgreSQL, real binaries);
// these tests pin the rules that do not need either: body shape, the canonical
// request digest, the numeric(20,8) budget boundary and the submitted input's
// canonical bytes.

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"orchara/platform/internal/jcs"
)

func debugBody(t *testing.T, request map[string]any) DebugRequest {
	t.Helper()
	encoded, err := json.Marshal(request)
	if err != nil {
		t.Fatalf("encoding the test body: %v", err)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(encoded, &fields); err != nil {
		t.Fatalf("decoding the test body: %v", err)
	}
	decoded := DebugRequest{
		DraftRevision: fields["draft_revision"],
		Input:         fields["input"],
		ArtifactIDs:   fields["artifact_ids"],
	}
	for key := range fields {
		switch key {
		case "draft_revision", "input", "artifact_ids":
		default:
			decoded.UnknownFields = append(decoded.UnknownFields, key)
		}
	}
	return decoded
}

func TestValidateDebugRequestAcceptsTheContractShape(t *testing.T) {
	request := debugBody(t, map[string]any{
		"draft_revision": 3,
		"input":          map[string]any{"text": "hello"},
		"artifact_ids": []string{
			"BBBBBBBB-BBBB-4BBB-8BBB-BBBBBBBBBBBB",
			"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		},
	})
	plan, err := ValidateDebugRequest(request)
	if err != nil {
		t.Fatalf("a contract-shaped request was refused: %v", err)
	}
	if plan.DraftRevision != 3 {
		t.Fatalf("draft_revision = %d, want 3", plan.DraftRevision)
	}
	wantBytes := `{"text":"hello"}`
	if string(plan.InputBytes) != wantBytes {
		t.Fatalf("canonical input = %s, want %s", plan.InputBytes, wantBytes)
	}
	wantIDs := []string{
		"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
	}
	if len(plan.ArtifactIDs) != 2 || plan.ArtifactIDs[0] != wantIDs[0] || plan.ArtifactIDs[1] != wantIDs[1] {
		t.Fatalf("artifact ids = %v, want %v", plan.ArtifactIDs, wantIDs)
	}

	// The canonical bytes are exactly the JCS form the artifact must hold.
	canonical, err := jcs.CanonicalizeBytes(plan.InputBytes)
	if err != nil {
		t.Fatalf("the plan's bytes are not canonicalisable: %v", err)
	}
	if string(canonical) != wantBytes {
		t.Fatalf("the plan's bytes are not the canonical form: %s", canonical)
	}

	// An empty artifact list is allowed; the key is still required.
	empty := debugBody(t, map[string]any{
		"draft_revision": 1, "input": map[string]any{}, "artifact_ids": []string{},
	})
	plan, err = ValidateDebugRequest(empty)
	if err != nil {
		t.Fatalf("an empty artifact_ids list was refused: %v", err)
	}
	if len(plan.ArtifactIDs) != 0 {
		t.Fatalf("artifact ids = %v, want none", plan.ArtifactIDs)
	}
}

func TestValidateDebugRequestRefusesBodyProblemsWithTheirField(t *testing.T) {
	cases := []struct {
		name  string
		body  map[string]any
		field string
	}{
		{"missing revision", map[string]any{"input": map[string]any{}}, "draft_revision"},
		{"zero revision", map[string]any{"draft_revision": 0, "input": map[string]any{}, "artifact_ids": []string{}}, "draft_revision"},
		{"string revision", map[string]any{"draft_revision": "2", "input": map[string]any{}, "artifact_ids": []string{}}, "draft_revision"},
		{"fractional revision", map[string]any{"draft_revision": 1.5, "input": map[string]any{}, "artifact_ids": []string{}}, "draft_revision"},
		{"missing input", map[string]any{"draft_revision": 1, "artifact_ids": []string{}}, "input"},
		{"array input", map[string]any{"draft_revision": 1, "input": []any{1}, "artifact_ids": []string{}}, "input"},
		{"null input", map[string]any{"draft_revision": 1, "input": nil, "artifact_ids": []string{}}, "input"},
		{"missing artifact_ids", map[string]any{"draft_revision": 1, "input": map[string]any{}}, "artifact_ids"},
		{"null artifact_ids", map[string]any{"draft_revision": 1, "input": map[string]any{}, "artifact_ids": nil}, "artifact_ids"},
		{"too many artifacts", map[string]any{
			"draft_revision": 1,
			"input":          map[string]any{},
			"artifact_ids": []string{
				"11111111-1111-4111-8111-111111111111",
				"22222222-2222-4222-8222-222222222222",
				"33333333-3333-4333-8333-333333333333",
				"44444444-4444-4444-8444-444444444444",
				"55555555-5555-4555-8555-555555555555",
				"66666666-6666-4666-8666-666666666666",
			},
		}, "artifact_ids"},
		{"duplicate artifact", map[string]any{
			"draft_revision": 1,
			"input":          map[string]any{},
			"artifact_ids": []string{
				"11111111-1111-4111-8111-111111111111",
				"11111111-1111-4111-8111-111111111111",
			},
		}, "artifact_ids"},
		{"malformed artifact", map[string]any{"draft_revision": 1, "input": map[string]any{}, "artifact_ids": []string{"not-a-uuid"}}, "artifact_ids"},
		{"unknown field", map[string]any{"draft_revision": 1, "input": map[string]any{}, "artifact_ids": []string{}, "extra": true}, "extra"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := ValidateDebugRequest(debugBody(t, testCase.body))
			var validation *ValidationError
			if !errors.As(err, &validation) {
				t.Fatalf("expected a validation error, got %v", err)
			}
			if validation.Field != testCase.field {
				t.Fatalf("field = %q, want %q (%v)", validation.Field, testCase.field, validation)
			}
		})
	}
}

func TestValidateDebugRequestRefusesDuplicateJSONKeysAndNonCanonicalNumbers(t *testing.T) {
	duplicate := DebugRequest{
		DraftRevision: json.RawMessage(`1`),
		Input:         json.RawMessage(`{"a":1,"a":2}`),
		ArtifactIDs:   json.RawMessage(`[]`),
	}
	_, err := ValidateDebugRequest(duplicate)
	var validation *ValidationError
	if !errors.As(err, &validation) || validation.Field != "input" {
		t.Fatalf("a duplicate-key input was not refused at input: %v", err)
	}
	if !strings.Contains(validation.Message, "canonicalisable") {
		t.Fatalf("the refusal does not explain canonicalisability: %v", validation)
	}
}

func TestDebugRequestDigestIsCanonicalAndArtifactOrderInsensitive(t *testing.T) {
	first := debugBody(t, map[string]any{
		"draft_revision": 4,
		"input":          map[string]any{"b": 2, "a": map[string]any{"y": true, "x": nil}},
		"artifact_ids": []string{
			"11111111-1111-4111-8111-111111111111",
			"22222222-2222-4222-8222-222222222222",
		},
	})
	second := debugBody(t, map[string]any{
		"draft_revision": 4,
		"input":          map[string]any{"a": map[string]any{"x": nil, "y": true}, "b": 2},
		"artifact_ids": []string{
			"22222222-2222-4222-8222-222222222222",
			"11111111-1111-4111-8111-111111111111",
		},
	})
	applicationID := "33333333-3333-4333-8333-333333333333"

	planA, err := ValidateDebugRequest(first)
	if err != nil {
		t.Fatalf("first request refused: %v", err)
	}
	planB, err := ValidateDebugRequest(second)
	if err != nil {
		t.Fatalf("second request refused: %v", err)
	}
	digestA, err := debugRequestDigest(applicationID, planA)
	if err != nil {
		t.Fatalf("digest A: %v", err)
	}
	digestB, err := debugRequestDigest(applicationID, planB)
	if err != nil {
		t.Fatalf("digest B: %v", err)
	}
	if digestA != digestB {
		t.Fatalf("the same semantic request produced two digests: %s vs %s", digestA, digestB)
	}
	if len(digestA) != 64 {
		t.Fatalf("digest is not 64 hex characters: %q", digestA)
	}

	// A different input, revision, application or artifact set is a different
	// canonical request (and therefore a 409 on a reused key).
	differentInput := debugBody(t, map[string]any{
		"draft_revision": 4, "input": map[string]any{"b": 3}, "artifact_ids": []string{},
	})
	differentRevision := debugBody(t, map[string]any{
		"draft_revision": 5, "input": map[string]any{"b": 2, "a": map[string]any{"y": true, "x": nil}}, "artifact_ids": []string{},
	})
	differentArtifacts := debugBody(t, map[string]any{
		"draft_revision": 4,
		"input":          map[string]any{"b": 2, "a": map[string]any{"y": true, "x": nil}},
		"artifact_ids":   []string{"11111111-1111-4111-8111-111111111111"},
	})
	for name, request := range map[string]DebugRequest{
		"input":     differentInput,
		"revision":  differentRevision,
		"app":       first,
		"artifacts": differentArtifacts,
	} {
		plan, err := ValidateDebugRequest(request)
		if err != nil {
			t.Fatalf("%s: request refused: %v", name, err)
		}
		otherApp := applicationID
		if name == "app" {
			otherApp = "44444444-4444-4444-8444-444444444444"
		}
		digest, err := debugRequestDigest(otherApp, plan)
		if err != nil {
			t.Fatalf("%s: digest: %v", name, err)
		}
		if digest == digestA {
			t.Fatalf("%s: a different canonical request kept the digest %s", name, digest)
		}
	}
}

func TestFitsNumeric208(t *testing.T) {
	accepted := []string{"0", "1", "1.5", "0.00000001", "999999999999", "999999999999.99999999", "000001.10"}
	refused := []string{"", "-1", "1.", ".5", "1.000000001", "1000000000000", "1234567890123", "1e3", "abc", " 1"}
	for _, value := range accepted {
		if !fitsNumeric208(value) {
			t.Fatalf("%q should fit numeric(20,8)", value)
		}
	}
	for _, value := range refused {
		if fitsNumeric208(value) {
			t.Fatalf("%q should not fit numeric(20,8)", value)
		}
	}
}

func TestDebugPolicyReadsTheBudgetAndRefusesOutOfRangeValues(t *testing.T) {
	budget, currency, maxActiveMS, problem := debugPolicy(map[string]any{
		"policy": map[string]any{
			"budget_limit": "12.50", "currency": "USD", "max_active_seconds": float64(900),
		},
	})
	if problem != nil {
		t.Fatalf("a valid policy was refused: %v", problem)
	}
	if budget != "12.50" || currency != "USD" || maxActiveMS != 900_000 {
		t.Fatalf("budget/currency/max = %q/%q/%d, want 12.50/USD/900000",
			budget, currency, maxActiveMS)
	}

	for name, definition := range map[string]map[string]any{
		"missing policy":  {},
		"no budget":       {"policy": map[string]any{"currency": "USD", "max_active_seconds": float64(900)}},
		"bad budget":      {"policy": map[string]any{"budget_limit": "1e3", "currency": "USD", "max_active_seconds": float64(900)}},
		"huge budget":     {"policy": map[string]any{"budget_limit": "1000000000000", "currency": "USD", "max_active_seconds": float64(900)}},
		"bad currency":    {"policy": map[string]any{"budget_limit": "1", "currency": "US", "max_active_seconds": float64(900)}},
		"no max active":   {"policy": map[string]any{"budget_limit": "1", "currency": "USD"}},
		"zero max active": {"policy": map[string]any{"budget_limit": "1", "currency": "USD", "max_active_seconds": float64(0)}},
		"fractional max":  {"policy": map[string]any{"budget_limit": "1", "currency": "USD", "max_active_seconds": 1.5}},
		"huge max active": {"policy": map[string]any{"budget_limit": "1", "currency": "USD", "max_active_seconds": float64(3601)}},
		"string max":      {"policy": map[string]any{"budget_limit": "1", "currency": "USD", "max_active_seconds": "900"}},
	} {
		_, _, _, problem := debugPolicy(definition)
		if problem == nil {
			t.Fatalf("%s: accepted an unusable policy", name)
		}
		if name == "huge budget" {
			if problem.Code != CodeBudgetOutOfRange {
				t.Fatalf("huge budget: code = %q, want %q", problem.Code, CodeBudgetOutOfRange)
			}
			if problem.Pointer != "/policy/budget_limit" {
				t.Fatalf("huge budget: pointer = %q", problem.Pointer)
			}
		}
		if problem.Code != CodePolicyIncomplete && problem.Code != CodeBudgetOutOfRange {
			t.Fatalf("%s: unexpected code %q", name, problem.Code)
		}
	}
}
