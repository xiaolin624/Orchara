package evaluation

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

// validCase is one schema-valid case body; tests mutate single members so the
// refusal they assert is located at exactly that member.
func validCase(overrides map[string]any) map[string]any {
	item := map[string]any{
		"schema_version": "1.0",
		"case_id":        "sales-basic-001",
		"category":       "sales",
		"family_id":      "sales-three-orders",
		"split":          "dev",
		"target":         map[string]any{"kind": "debug_snapshot", "id": "snapshot-1"},
		"input":          map[string]any{"file": "input.csv"},
		"fixture_id":     "sales-three-orders-v1",
		"assertions": []any{
			map[string]any{"kind": "run_state", "expected": "succeeded"},
			map[string]any{"kind": "decimal_equals", "pointer": "/summary/total", "expected": "250.00"},
		},
		"provenance": map[string]any{
			"origin": "self_created", "reference": "examples/sales/input.csv",
			"license_review": "not_applicable",
		},
	}
	for key, value := range overrides {
		if value == nil {
			delete(item, key)
			continue
		}
		item[key] = value
	}
	return item
}

func datasetOf(t *testing.T, cases ...map[string]any) []byte {
	t.Helper()
	document := map[string]any{"cases": cases}
	encoded, err := json.Marshal(document)
	if err != nil {
		t.Fatalf("encoding the dataset: %v", err)
	}
	return encoded
}

func TestParseDatasetAcceptsTheContractShape(t *testing.T) {
	dataset, err := ParseDataset(datasetOf(t, validCase(nil)))
	if err != nil {
		t.Fatalf("a schema-valid dataset was refused: %v", err)
	}
	if len(dataset.Cases) != 1 {
		t.Fatalf("expected one case, got %d", len(dataset.Cases))
	}
	item := dataset.Cases[0]
	if item.CaseID != "sales-basic-001" || item.Target.Kind != "debug_snapshot" {
		t.Fatalf("unexpected case: %+v", item)
	}
	if len(item.Assertions) != 2 {
		t.Fatalf("expected two assertions, got %d", len(item.Assertions))
	}
	if item.Assertions[1].ExpectedDecimal != "250.00" {
		t.Fatalf("the decimal expectation was not preserved: %+v", item.Assertions[1])
	}
}

func TestParseDatasetAcceptsABareArrayOfCases(t *testing.T) {
	encoded, err := json.Marshal([]any{validCase(nil)})
	if err != nil {
		t.Fatalf("encoding: %v", err)
	}
	dataset, err := ParseDataset(encoded)
	if err != nil {
		t.Fatalf("a bare case array was refused: %v", err)
	}
	if len(dataset.Cases) != 1 {
		t.Fatalf("expected one case, got %d", len(dataset.Cases))
	}
}

func TestParseDatasetRefusesAnEmptyChecker(t *testing.T) {
	dataset := datasetOf(t, validCase(map[string]any{"assertions": []any{}}))
	_, err := ParseDataset(dataset)
	assertDatasetError(t, err, CodeDatasetEmptyAssertions, "/cases/0/assertions")
}

func TestParseDatasetRefusesADuplicateCaseID(t *testing.T) {
	dataset := datasetOf(t, validCase(nil), validCase(nil))
	_, err := ParseDataset(dataset)
	assertDatasetError(t, err, CodeDatasetDuplicateCase, "/cases/1/case_id")
}

func TestParseDatasetRefusesUnknownCaseMembers(t *testing.T) {
	dataset := datasetOf(t, validCase(map[string]any{"checker": "anything"}))
	_, err := ParseDataset(dataset)
	assertDatasetError(t, err, CodeDatasetUnknownField, "/cases/0/checker")
}

func TestParseDatasetRefusesAnUnknownAssertionMember(t *testing.T) {
	bad := map[string]any{"kind": "run_state", "expected": "succeeded", "pointer": "/x"}
	dataset := datasetOf(t, validCase(map[string]any{"assertions": []any{bad}}))
	_, err := ParseDataset(dataset)
	assertDatasetError(t, err, CodeDatasetUnknownField, "/cases/0/assertions/0/pointer")
}

func TestParseDatasetRefusesAnUnsupportedSchemaVersion(t *testing.T) {
	dataset := datasetOf(t, validCase(map[string]any{"schema_version": "2.0"}))
	_, err := ParseDataset(dataset)
	assertDatasetError(t, err, CodeDatasetSchema, "/cases/0/schema_version")
}

func TestParseDatasetRefusesAnInvalidJSONPointer(t *testing.T) {
	bad := map[string]any{"kind": "json_equals", "pointer": "summary/total", "expected": 1}
	dataset := datasetOf(t, validCase(map[string]any{"assertions": []any{bad}}))
	_, err := ParseDataset(dataset)
	assertDatasetError(t, err, CodeDatasetInvalidPointer, "/cases/0/assertions/0/pointer")
}

func TestParseDatasetRefusesAFloatDecimalExpectation(t *testing.T) {
	bad := map[string]any{"kind": "decimal_equals", "pointer": "/total", "expected": 250.0}
	dataset := datasetOf(t, validCase(map[string]any{"assertions": []any{bad}}))
	_, err := ParseDataset(dataset)
	assertDatasetError(t, err, CodeDatasetSchema, "/cases/0/assertions/0/expected")
}

func TestParseDatasetAcceptsZeroAsAnEffectCountExpectation(t *testing.T) {
	assertion := map[string]any{
		"kind": "effect_count", "capability": "test.send", "expected": float64(0), "source": "stub_ledger",
	}
	dataset, err := ParseDataset(datasetOf(t, validCase(
		map[string]any{"assertions": []any{assertion}},
	)))
	if err != nil {
		t.Fatalf("expected 0 must be a valid effect_count expectation: %v", err)
	}
	if dataset.Cases[0].Assertions[0].ExpectedCount != 0 {
		t.Fatalf("expected 0 was not preserved")
	}
}

func TestParseDatasetRefusesANegativeEffectCount(t *testing.T) {
	assertion := map[string]any{
		"kind": "effect_count", "capability": "test.send", "expected": float64(-1), "source": "stub_ledger",
	}
	_, err := ParseDataset(datasetOf(t, validCase(map[string]any{"assertions": []any{assertion}})))
	assertDatasetError(t, err, CodeDatasetInvalidAssert, "/cases/0/assertions/0/expected")
}

func TestParseDatasetRefusesAnEmptyDataset(t *testing.T) {
	_, err := ParseDataset([]byte(`{"cases":[]}`))
	assertDatasetError(t, err, CodeDatasetSchema, "/cases")
}

func TestIsLegacySyntheticDataset(t *testing.T) {
	legacy := []byte(`{"schema":"orchara.evaluation-dataset/1","cases":[{"id":"c","input":{"q":1}}]}`)
	if !IsLegacySyntheticDataset(legacy) {
		t.Fatal("the T026 synthetic document must be recognised as legacy")
	}
	if IsLegacySyntheticDataset(datasetOf(t, validCase(nil))) {
		t.Fatal("an evaluation dataset must not be mistaken for the legacy document")
	}
}

func TestRunInputStripsTheReservedInteractionMember(t *testing.T) {
	item := Case{
		CaseID: "c1",
		Input:  json.RawMessage(`{"file":"input.csv","interactions":[{"kind":"cancel"}]}`),
	}
	derived, err := item.RunInput()
	if err != nil {
		t.Fatalf("RunInput: %v", err)
	}
	if strings.Contains(string(derived), InteractionKey) {
		t.Fatalf("the reserved member leaked into the run input: %s", derived)
	}
	if !strings.Contains(string(derived), "input.csv") {
		t.Fatalf("the case input was lost: %s", derived)
	}
	interactions, err := item.Interactions()
	if err != nil {
		t.Fatalf("Interactions: %v", err)
	}
	if len(interactions) != 1 || interactions[0].Kind != "cancel" {
		t.Fatalf("unexpected interactions: %+v", interactions)
	}
}

func assertDatasetError(t *testing.T, err error, code, pointer string) {
	t.Helper()
	if err == nil {
		t.Fatalf("expected a dataset refusal (%s at %s), got none", code, pointer)
	}
	var located *Error
	if !errors.As(err, &located) {
		t.Fatalf("expected *evaluation.Error, got %T: %v", err, err)
	}
	if located.Code != code {
		t.Fatalf("expected code %s, got %s (%s)", code, located.Code, located.Message)
	}
	if located.Pointer != pointer {
		t.Fatalf("expected pointer %s, got %s (%s)", pointer, located.Pointer, located.Message)
	}
}
