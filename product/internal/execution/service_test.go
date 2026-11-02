package execution

import (
	"encoding/json"
	"testing"
	"time"
)

// The validation helpers decide the contract's 422 vocabulary before any row is
// touched. These tests pin the rules the real-stack Python suite exercises over
// HTTP: trimming, uniqueness, printable identifiers, checkpoint connection shape
// and the "only an explicit started=false releases" rule.

func TestValidateClaimRequestNormalisesAndLocates(t *testing.T) {
	normalized, err := validateClaimRequest(ClaimRequest{
		WorkerID:           "  supervisor-1  ",
		SupportedCompilers: []string{" orchara-compiler/1.0.0 ", "orchara-compiler/2.0.0"},
		ExecutionUnitID:    " unit-1",
	})
	if err != nil {
		t.Fatalf("a valid claim was refused: %v", err)
	}
	if normalized.WorkerID != "supervisor-1" || normalized.ExecutionUnitID != "unit-1" {
		t.Fatalf("identifiers were not trimmed: %+v", normalized)
	}
	if len(normalized.SupportedCompilers) != 2 ||
		normalized.SupportedCompilers[0] != "orchara-compiler/1.0.0" {
		t.Fatalf("compilers were not normalised: %+v", normalized.SupportedCompilers)
	}

	cases := []struct {
		name    string
		request ClaimRequest
		field   string
	}{
		{"blank worker", ClaimRequest{
			WorkerID: "  ", SupportedCompilers: []string{"c"}, ExecutionUnitID: "u",
		}, "worker_id"},
		{"control character worker", ClaimRequest{
			WorkerID: "w\x00", SupportedCompilers: []string{"c"}, ExecutionUnitID: "u",
		}, "worker_id"},
		{"no compilers", ClaimRequest{
			WorkerID: "w", SupportedCompilers: nil, ExecutionUnitID: "u",
		}, "supported_compilers"},
		{"blank compiler", ClaimRequest{
			WorkerID: "w", SupportedCompilers: []string{" "}, ExecutionUnitID: "u",
		}, "supported_compilers"},
		{"duplicate compiler", ClaimRequest{
			WorkerID: "w", SupportedCompilers: []string{"c", "c"}, ExecutionUnitID: "u",
		}, "supported_compilers"},
		{"blank unit", ClaimRequest{
			WorkerID: "w", SupportedCompilers: []string{"c"}, ExecutionUnitID: "\t",
		}, "execution_unit_id"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := validateClaimRequest(testCase.request)
			validation, ok := err.(*ValidationError)
			if !ok {
				t.Fatalf("expected a ValidationError, got %v", err)
			}
			if validation.Field != testCase.field {
				t.Fatalf("expected field %q, got %q", testCase.field, validation.Field)
			}
		})
	}
}

func TestValidateCheckpointConnections(t *testing.T) {
	valid := []byte(`[{"pid":42,"backend_start":"2026-11-03T00:00:00Z","application_name":"saver"}]`)
	normalized, err := validateCheckpointConnections(valid)
	if err != nil {
		t.Fatalf("a valid connection list was refused: %v", err)
	}
	var entries []map[string]any
	if err := json.Unmarshal(normalized, &entries); err != nil {
		t.Fatalf("the normalised list is not JSON: %v", err)
	}
	if len(entries) != 1 || entries[0]["pid"] != float64(42) {
		t.Fatalf("unexpected normalised list: %s", normalized)
	}

	absent, err := validateCheckpointConnections(nil)
	if err != nil || absent != nil {
		t.Fatalf("an absent field must stay absent: %v %v", absent, err)
	}

	invalid := []string{
		`"not-an-array"`,
		`[1]`,
		`[{"pid":0,"backend_start":"2026-11-03T00:00:00Z","application_name":"x"}]`,
		`[{"pid":1,"backend_start":"not-a-time","application_name":"x"}]`,
		`[{"pid":1,"backend_start":"2026-11-03T00:00:00Z"}]`,
		`[{"pid":1,"backend_start":"2026-11-03T00:00:00Z","application_name":"x","extra":1}]`,
	}
	for _, raw := range invalid {
		_, err := validateCheckpointConnections([]byte(raw))
		validation, ok := err.(*ValidationError)
		if !ok || validation.Field != "checkpoint_connections" {
			t.Fatalf("%s: expected a located ValidationError, got %v", raw, err)
		}
	}
}

func TestExitEvidenceOnlyAnExplicitStartedFalseReleases(t *testing.T) {
	cases := []struct {
		evidence string
		started  bool
	}{
		{`{"started":false}`, false},
		{`{"started":false,"reason":"spawn_failed"}`, false},
		{`{"started":true}`, true},
		{`{"reason":"drained"}`, true}, // absent proves nothing -> hold
		{`{}`, true},
	}
	for _, testCase := range cases {
		_, started, err := validateExitRequest(ExitRequest{
			AttemptID:       "11111111-1111-4111-8111-111111111111",
			ExecutionUnitID: "unit-1",
			Evidence:        []byte(testCase.evidence),
		})
		if err != nil {
			t.Fatalf("%s: unexpected error: %v", testCase.evidence, err)
		}
		if started != testCase.started {
			t.Fatalf("%s: started=%v want %v", testCase.evidence, started, testCase.started)
		}
	}

	for _, evidence := range []string{`[]`, `null`, `"x"`, `7`} {
		_, _, err := validateExitRequest(ExitRequest{
			AttemptID:       "11111111-1111-4111-8111-111111111111",
			ExecutionUnitID: "unit-1",
			Evidence:        []byte(evidence),
		})
		if validation, ok := err.(*ValidationError); !ok || validation.Field != "evidence" {
			t.Fatalf("%s: expected evidence ValidationError, got %v", evidence, err)
		}
	}

	_, _, err := validateExitRequest(ExitRequest{
		AttemptID:       "11111111-1111-4111-8111-111111111111",
		ExecutionUnitID: "unit-1",
		Evidence:        []byte(`{"started":"false"}`),
	})
	if validation, ok := err.(*ValidationError); !ok || validation.Field != "evidence.started" {
		t.Fatalf("expected evidence.started ValidationError, got %v", err)
	}
}

func TestNewNormalisesTheLeaseTTL(t *testing.T) {
	if service := New(nil, nil, nil, "http://example.test/", 0); service.LeaseTTL != DefaultLeaseTTL {
		t.Fatalf("zero TTL became %s, want %s", service.LeaseTTL, DefaultLeaseTTL)
	}
	if service := New(nil, nil, nil, "http://example.test/", time.Hour*24); service.LeaseTTL != DefaultLeaseTTL {
		t.Fatalf("an out-of-range TTL became %s, want %s", service.LeaseTTL, DefaultLeaseTTL)
	}
	service := New(nil, nil, nil, "http://example.test/", 5*time.Second)
	if service.LeaseTTL != 5*time.Second {
		t.Fatalf("a valid TTL became %s", service.LeaseTTL)
	}
	if service.BaseURL != "http://example.test" {
		t.Fatalf("the base URL was not normalised: %q", service.BaseURL)
	}
}
