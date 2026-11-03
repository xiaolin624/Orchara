package execution

import (
	"encoding/json"
	"strings"
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

func TestExitEvidenceRequiresIdentityAndStarted(t *testing.T) {
	manifestHash := strings.Repeat("a", 64)
	valid := `{"started":false,"protocol_version":"1.0","manifest_sha256":"` + manifestHash +
		`","image_digest":"sha256:aaa","reason":"spawn_failed"}`
	_, evidence, err := validateExitRequest(ExitRequest{
		AttemptID:       "11111111-1111-4111-8111-111111111111",
		ExecutionUnitID: "unit-1",
		Evidence:        []byte(valid),
	})
	if err != nil {
		t.Fatalf("valid identity evidence was refused: %v", err)
	}
	if evidence.Started || evidence.ProtocolVersion != "1.0" ||
		evidence.ManifestSHA256 != manifestHash || evidence.ImageDigest != "sha256:aaa" {
		t.Fatalf("unexpected parsed evidence: %+v", evidence)
	}

	// Every required key is located when absent: T031 refuses an ambiguous exit.
	base := map[string]string{
		"started":          "true",
		"protocol_version": `"1.0"`,
		"manifest_sha256":  `"` + manifestHash + `"`,
		"image_digest":     `"sha256:aaa"`,
	}
	for _, missing := range []string{"started", "protocol_version", "manifest_sha256", "image_digest"} {
		parts := make([]string, 0, len(base)-1)
		for key, raw := range base {
			if key == missing {
				continue
			}
			parts = append(parts, `"`+key+`":`+raw)
		}
		_, _, err := validateExitRequest(ExitRequest{
			AttemptID:       "11111111-1111-4111-8111-111111111111",
			ExecutionUnitID: "unit-1",
			Evidence:        []byte("{" + strings.Join(parts, ",") + "}"),
		})
		validation, ok := err.(*ValidationError)
		if !ok || validation.Field != "evidence."+missing {
			t.Fatalf("missing %s: expected a located ValidationError, got %v", missing, err)
		}
	}

	for evidenceValue, field := range map[string]string{
		`{"started":"false","protocol_version":"1.0","manifest_sha256":"` + manifestHash + `","image_digest":"sha256:aaa"}`: "evidence.started",
		`{"started":true,"protocol_version":"  ","manifest_sha256":"` + manifestHash + `","image_digest":"sha256:aaa"}`:     "evidence.protocol_version",
		`{"started":true,"protocol_version":"1.0","manifest_sha256":"abc","image_digest":"sha256:aaa"}`:                     "evidence.manifest_sha256",
		`{"started":true,"protocol_version":"1.0","manifest_sha256":"` + manifestHash + `","image_digest":""}`:              "evidence.image_digest",
	} {
		_, _, err := validateExitRequest(ExitRequest{
			AttemptID:       "11111111-1111-4111-8111-111111111111",
			ExecutionUnitID: "unit-1",
			Evidence:        []byte(evidenceValue),
		})
		validation, ok := err.(*ValidationError)
		if !ok || validation.Field != field {
			t.Fatalf("%s: expected field %q, got %v", evidenceValue, field, err)
		}
	}

	for _, evidenceValue := range []string{`[]`, `null`, `"x"`, `7`} {
		_, _, err := validateExitRequest(ExitRequest{
			AttemptID:       "11111111-1111-4111-8111-111111111111",
			ExecutionUnitID: "unit-1",
			Evidence:        []byte(evidenceValue),
		})
		if validation, ok := err.(*ValidationError); !ok || validation.Field != "evidence" {
			t.Fatalf("%s: expected evidence ValidationError, got %v", evidenceValue, err)
		}
	}
}

func TestValidateWorkerResultLocatesTheContractRules(t *testing.T) {
	validArtifact := "22222222-2222-4222-8222-222222222222"
	validEvent := "33333333-3333-4333-8333-333333333333"
	validToken := "11111111-1111-4111-8111-111111111111"

	normalized, err := validateWorkerResult(WorkerResult{
		AttemptToken:     " " + strings.ToUpper(validToken) + " ",
		EventID:          validEvent,
		SuggestedState:   "succeeded",
		OutputArtifactID: strPtr(strings.ToUpper(validArtifact)),
	})
	if err != nil {
		t.Fatalf("a valid succeeded proposal was refused: %v", err)
	}
	if normalized.AttemptToken != validToken || normalized.EventID != validEvent {
		t.Fatalf("identifiers were not normalised: %+v", normalized)
	}
	if normalized.OutputArtifactID == nil || *normalized.OutputArtifactID != validArtifact {
		t.Fatalf("the artifact id was not lower-cased: %+v", normalized)
	}

	cases := []struct {
		name   string
		result WorkerResult
		field  string
	}{
		{"bad token", WorkerResult{AttemptToken: "x", EventID: validEvent, SuggestedState: "failed", ErrorCode: strPtr("boom")}, "attempt_token"},
		{"bad event", WorkerResult{AttemptToken: validToken, EventID: "x", SuggestedState: "failed", ErrorCode: strPtr("boom")}, "event_id"},
		{"bad state", WorkerResult{AttemptToken: validToken, EventID: validEvent, SuggestedState: "cheerful"}, "suggested_state"},
		{"succeeded without artifact", WorkerResult{AttemptToken: validToken, EventID: validEvent, SuggestedState: "succeeded"}, "output_artifact_id"},
		{"failed without code", WorkerResult{AttemptToken: validToken, EventID: validEvent, SuggestedState: "failed"}, "error_code"},
		{"waiting without checkpoint", WorkerResult{AttemptToken: validToken, EventID: validEvent, SuggestedState: "waiting_approval"}, "checkpoint_id"},
		{"waiting children without checkpoint", WorkerResult{AttemptToken: validToken, EventID: validEvent, SuggestedState: "waiting_children"}, "checkpoint_id"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := validateWorkerResult(testCase.result)
			validation, ok := err.(*ValidationError)
			if !ok || validation.Field != testCase.field {
				t.Fatalf("expected field %q, got %v", testCase.field, err)
			}
		})
	}

	// needs_reconciliation needs no extra field, and a blank checkpoint is located.
	if _, err := validateWorkerResult(WorkerResult{
		AttemptToken: validToken, EventID: validEvent, SuggestedState: "needs_reconciliation",
	}); err != nil {
		t.Fatalf("a needs_reconciliation proposal was refused: %v", err)
	}
	if _, err := validateWorkerResult(WorkerResult{
		AttemptToken: validToken, EventID: validEvent, SuggestedState: "waiting_approval",
		CheckpointID: strPtr("   "),
	}); err == nil {
		t.Fatalf("a blank checkpoint_id was accepted")
	}
}

func strPtr(value string) *string { return &value }

func TestValidateWorkerEventsEnforcesTheNodeStatusAllowlist(t *testing.T) {
	token := "11111111-1111-4111-8111-111111111111"
	eventID := "22222222-2222-4222-8222-222222222222"
	payload := []byte(`{"node_id":"n1","state":"running","attempt_no":2}`)

	normalized, err := validateWorkerEvents(WorkerEventsRequest{
		AttemptToken: "  " + token + " ",
		Events: []WorkerEvent{{
			EventID:       " " + eventID,
			LocalSequence: 3,
			Type:          "node.status",
			Payload:       payload,
		}},
	})
	if err != nil {
		t.Fatalf("a valid node.status batch was refused: %v", err)
	}
	if normalized.AttemptToken != token || normalized.Events[0].EventID != eventID {
		t.Fatalf("the batch was not normalised: %+v", normalized)
	}
	var received map[string]any
	if err := json.Unmarshal(normalized.Events[0].Payload, &received); err != nil {
		t.Fatalf("the canonical payload is not JSON: %v", err)
	}
	if received["node_id"] != "n1" || received["state"] != "running" || received["attempt_no"] != float64(2) {
		t.Fatalf("the payload changed: %v", received)
	}

	cases := []struct {
		name    string
		request WorkerEventsRequest
		field   string
	}{
		{"bad token", WorkerEventsRequest{
			AttemptToken: "x",
			Events:       []WorkerEvent{{EventID: eventID, LocalSequence: 1, Type: "node.status", Payload: payload}},
		}, "attempt_token"},
		{"empty batch", WorkerEventsRequest{AttemptToken: token}, "events"},
		{"bad event id", WorkerEventsRequest{
			AttemptToken: token,
			Events:       []WorkerEvent{{EventID: "x", LocalSequence: 1, Type: "node.status", Payload: payload}},
		}, "events[0].event_id"},
		{"local sequence 0", WorkerEventsRequest{
			AttemptToken: token,
			Events:       []WorkerEvent{{EventID: eventID, LocalSequence: 0, Type: "node.status", Payload: payload}},
		}, "events[0].local_sequence"},
		{"controller type", WorkerEventsRequest{
			AttemptToken: token,
			Events:       []WorkerEvent{{EventID: eventID, LocalSequence: 1, Type: "run.status", Payload: payload}},
		}, "events[0].type"},
		{"body-like payload key", WorkerEventsRequest{
			AttemptToken: token,
			Events: []WorkerEvent{{
				EventID: eventID, LocalSequence: 1, Type: "node.status",
				Payload: []byte(`{"node_id":"n1","state":"running","attempt_no":2,"content":"secret"}`),
			}},
		}, "events[0].payload.content"},
		{"missing attempt_no", WorkerEventsRequest{
			AttemptToken: token,
			Events: []WorkerEvent{{
				EventID: eventID, LocalSequence: 1, Type: "node.status",
				Payload: []byte(`{"node_id":"n1","state":"running"}`),
			}},
		}, "events[0].payload.attempt_no"},
		{"scalar payload", WorkerEventsRequest{
			AttemptToken: token,
			Events: []WorkerEvent{{
				EventID: eventID, LocalSequence: 1, Type: "node.status",
				Payload: []byte(`42`),
			}},
		}, "events[0].payload"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := validateWorkerEvents(testCase.request)
			validation, ok := err.(*ValidationError)
			if !ok || validation.Field != testCase.field {
				t.Fatalf("expected field %q, got %v", testCase.field, err)
			}
		})
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
