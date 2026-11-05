package audit

// F11/T073: the content-read audit record is a closed typed whitelist. These
// tests prove the fail-closed matrix and the exact metadata shape without a
// database: a caller cannot smuggle a body, query text or credential into the
// audit trail through this helper.

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

const (
	testGrant  = "11111111-1111-4111-8111-111111111111"
	testRun    = "22222222-2222-4222-8222-222222222222"
	testObject = "33333333-3333-4333-8333-333333333333"
	testActor  = "44444444-4444-4444-8444-444444444444"
	testOp     = "55555555-5555-4555-8555-555555555555"
	testToken  = "66666666-6666-4666-8666-666666666666"
)

func validContentRead() ContentRead {
	actor := testActor
	return ContentRead{
		ActorID:      &actor,
		GrantID:      testGrant,
		Purpose:      "troubleshooting",
		RunID:        testRun,
		ResourceType: "artifact",
		ResourceID:   testObject,
		OperationID:  testOp,
		AttemptToken: testToken,
		RequestID:    "req-0123456789abcdef",
		Outcome:      OutcomeServed,
	}
}

func TestValidateContentReadAcceptsTheTypedRecord(t *testing.T) {
	if err := ValidateContentRead(validContentRead()); err != nil {
		t.Fatalf("valid record rejected: %v", err)
	}
}

func TestValidateContentReadRefusesEverySmugglingShape(t *testing.T) {
	const marker = "SECRET-BODY-MARKER"
	cases := map[string]func(*ContentRead){
		"actor is not a uuid":   func(read *ContentRead) { *read.ActorID = marker },
		"grant is not a uuid":   func(read *ContentRead) { read.GrantID = marker },
		"run is not a uuid":     func(read *ContentRead) { read.RunID = marker },
		"purpose has uppercase": func(read *ContentRead) { read.Purpose = "Troubleshooting" },
		"purpose carries body":  func(read *ContentRead) { read.Purpose = marker },
		"resource type is free": func(read *ContentRead) { read.ResourceType = "artifact:" + marker },
		"resource id is body":   func(read *ContentRead) { read.ResourceID = marker },
		"operation is body":     func(read *ContentRead) { read.OperationID = marker },
		"attempt is body":       func(read *ContentRead) { read.AttemptToken = marker },
		"request id has space":  func(read *ContentRead) { read.RequestID = marker + " with spaces" },
		"unknown outcome":       func(read *ContentRead) { read.Outcome = "ok" },
		"served with error":     func(read *ContentRead) { read.ErrorCode = "diagnostic_scope_denied" },
		"refused without code": func(read *ContentRead) {
			read.Outcome = OutcomeRefused
			read.ErrorCode = ""
		},
		"error code carries body": func(read *ContentRead) {
			read.Outcome = OutcomeFailed
			read.ErrorCode = marker
		},
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			read := validContentRead()
			mutate(&read)
			err := ValidateContentRead(read)
			if err == nil {
				t.Fatalf("%s was accepted", name)
			}
			if !errors.Is(err, ErrInvalidContentRead) {
				t.Fatalf("error is not ErrInvalidContentRead: %v", err)
			}
		})
	}
}

func TestRecordedMetadataIsTheClosedTenKeyTypedWhitelist(t *testing.T) {
	read := validContentRead()
	metadata := contentReadMetadata(read)

	want := []string{
		"grant_id", "purpose", "run_id", "resource_type", "resource_id",
		"operation_id", "attempt_token", "request_id", "outcome", "error_code",
	}
	if len(metadata) != len(want) {
		t.Fatalf("metadata has %d keys, want %d: %#v", len(metadata), len(want), metadata)
	}
	for _, key := range want {
		if _, ok := metadata[key]; !ok {
			t.Fatalf("metadata is missing %q: %#v", key, metadata)
		}
	}

	encoded, err := json.Marshal(metadata)
	if err != nil {
		t.Fatalf("metadata is not JSON encodable: %v", err)
	}
	if strings.Contains(string(encoded), "SECRET") {
		t.Fatalf("metadata carries a body sentinel: %s", encoded)
	}
	// Every value is a bounded scalar or null; there is no nested object a body
	// could hide in.
	for key, value := range metadata {
		switch typed := value.(type) {
		case nil, string:
		default:
			t.Fatalf("%s is %T, want string/null", key, typed)
		}
		if value == nil {
			continue
		}
		if text, ok := value.(string); ok && len(text) > 128 {
			t.Fatalf("%s is longer than 128 bytes: %q", key, text)
		}
	}
}

func TestAbsentCorrelationIsNullNotOmitted(t *testing.T) {
	read := validContentRead()
	read.OperationID = ""
	read.AttemptToken = ""
	read.RequestID = ""
	metadata := contentReadMetadata(read)
	for _, key := range []string{"operation_id", "attempt_token", "request_id"} {
		value, present := metadata[key]
		if !present {
			t.Fatalf("%s was omitted instead of null", key)
		}
		if value != nil {
			t.Fatalf("%s = %#v, want null", key, value)
		}
	}
}
