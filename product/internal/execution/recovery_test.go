package execution

// F03/T038 unit tests for the conservative-recovery helpers that do not need a
// database: the closed operator-evidence shape, its canonical hash and the grace
// window bounds. The real-stack behaviour (sweep, refusals, one requeue) is
// exercised by tests/f03/test_t038_* against PostgreSQL, the compiled API and the
// compiled admin command.

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"
)

// recoveryEvidenceDocument is one complete, shape-valid operator document.
func recoveryEvidenceDocument(unit string) map[string]any {
	return map[string]any{
		"evidence_kind":     "operator_recovery",
		"execution_unit_id": unit,
		"observed_at":       "2026-11-03T02:15:04.123456Z",
		"process": map[string]any{
			"pid":      int64(4123),
			"command":  "ps -p 4123 -o pid=,stat=",
			"result":   "absent",
			"observed": "no matching pid",
		},
		"sandbox": map[string]any{
			"sandbox_id": "orchara-unit-1",
			"command":    "runsc list",
			"result":     "absent",
		},
		"notes": "host rebooted; the unit and its sandbox are gone",
	}
}

func encodeRecoveryEvidence(t *testing.T, document map[string]any) []byte {
	t.Helper()
	encoded, err := json.Marshal(document)
	if err != nil {
		t.Fatalf("encoding the evidence fixture: %v", err)
	}
	return encoded
}

func TestParseRecoveryEvidenceAcceptsTheClosedShape(t *testing.T) {
	raw := encodeRecoveryEvidence(t, recoveryEvidenceDocument("unit-1"))
	document, canonical, digest, err := parseRecoveryEvidence(raw)
	if err != nil {
		t.Fatalf("a complete document was refused: %v", err)
	}
	if document.ExecutionUnitID != "unit-1" {
		t.Fatalf("execution unit = %q", document.ExecutionUnitID)
	}
	if document.Process.Result != "absent" || document.Sandbox.Result != "absent" {
		t.Fatalf("unexpected normalised proof: %+v %+v", document.Process, document.Sandbox)
	}
	// The hash is over the RFC 8785 canonical form, not the caller's whitespace.
	expected := `{"evidence_kind":"operator_recovery","execution_unit_id":"unit-1",` +
		`"notes":"host rebooted; the unit and its sandbox are gone",` +
		`"observed_at":"2026-11-03T02:15:04.123456Z",` +
		`"process":{"command":"ps -p 4123 -o pid=,stat=","observed":"no matching pid",` +
		`"pid":4123,"result":"absent"},` +
		`"sandbox":{"command":"runsc list","result":"absent","sandbox_id":"orchara-unit-1"}}`
	if string(canonical) != expected {
		t.Fatalf("canonical evidence = %s", canonical)
	}
	if digest != jcsSHA256HexForTest([]byte(expected)) {
		t.Fatalf("digest = %s", digest)
	}

	// Whitespace/key order in the file never changes the recorded identity.
	reordered := []byte(`{"sandbox":{"result":"absent","command":"runsc list","sandbox_id":"orchara-unit-1"},` +
		`"process":{"result":"absent","pid":4123,"observed":"no matching pid",` +
		`"command":"ps -p 4123 -o pid=,stat="},"observed_at":"2026-11-03T02:15:04.123456Z",` +
		`"execution_unit_id":"unit-1","evidence_kind":"operator_recovery",` +
		`"notes":"host rebooted; the unit and its sandbox are gone"}`)
	_, _, digestAgain, err := parseRecoveryEvidence(reordered)
	if err != nil {
		t.Fatalf("a reordered document was refused: %v", err)
	}
	if digestAgain != digest {
		t.Fatalf("digest changed with key order: %s vs %s", digest, digestAgain)
	}
}

func TestParseRecoveryEvidenceRefusesFreeFormOrMalformedProof(t *testing.T) {
	cases := []struct {
		name     string
		mutate   func(map[string]any)
		raw      string
		contains string
	}{
		{name: "empty document", raw: `{}`, contains: "evidence_kind"},
		{name: "not an object", raw: `[{"execution_unit_id":"unit-1"}]`, contains: "JSON object"},
		{name: "free-form string only", raw: `"the process is dead"`, contains: "JSON object"},
		{
			name: "unknown top-level key",
			mutate: func(document map[string]any) {
				document["operator_says"] = "it is dead"
			},
			contains: "unknown field",
		},
		{
			name: "wrong kind",
			mutate: func(document map[string]any) {
				document["evidence_kind"] = "attestation"
			},
			contains: "evidence_kind",
		},
		{
			name: "missing sandbox proof",
			mutate: func(document map[string]any) {
				delete(document, "sandbox")
			},
			contains: "sandbox",
		},
		{
			name: "free-form process result",
			mutate: func(document map[string]any) {
				document["process"] = map[string]any{
					"pid": int64(4123), "command": "ps", "result": "probably dead",
				}
			},
			contains: "process.result",
		},
		{
			name: "pid is not positive",
			mutate: func(document map[string]any) {
				document["process"] = map[string]any{
					"pid": int64(0), "command": "ps", "result": "absent",
				}
			},
			contains: "process.pid",
		},
		{
			name: "missing command",
			mutate: func(document map[string]any) {
				document["sandbox"] = map[string]any{"sandbox_id": "s", "result": "absent"}
			},
			contains: "sandbox.command",
		},
		{
			name: "observed_at is not a timestamp",
			mutate: func(document map[string]any) {
				document["observed_at"] = "yesterday"
			},
			contains: "observed_at",
		},
		{
			name: "notes too long",
			mutate: func(document map[string]any) {
				document["notes"] = strings.Repeat("x", maxRecoveryNotes+1)
			},
			contains: "notes",
		},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			var raw []byte
			if testCase.raw != "" {
				raw = []byte(testCase.raw)
			} else {
				document := recoveryEvidenceDocument("unit-1")
				testCase.mutate(document)
				raw = encodeRecoveryEvidence(t, document)
			}
			_, _, _, err := parseRecoveryEvidence(raw)
			var refusal *RecoveryRefusal
			if !errors.As(err, &refusal) {
				t.Fatalf("expected a typed refusal, got %v", err)
			}
			if refusal.Code != RecoveryCodeEvidenceInvalid {
				t.Fatalf("code = %q, want %q", refusal.Code, RecoveryCodeEvidenceInvalid)
			}
			if !strings.Contains(refusal.Message, testCase.contains) {
				t.Fatalf("message %q does not mention %q", refusal.Message, testCase.contains)
			}
		})
	}
}

func TestRecoveryGraceIsBounded(t *testing.T) {
	for _, value := range []time.Duration{0, time.Second, time.Minute} {
		if err := validateRecoveryGrace(value); err != nil {
			t.Fatalf("grace %s refused: %v", value, err)
		}
	}
	for _, value := range []time.Duration{-time.Second, MaxRecoveryGrace + time.Second} {
		if err := validateRecoveryGrace(value); err == nil {
			t.Fatalf("grace %s was accepted", value)
		}
	}
	if normalizeRecoveryGrace(0) != DefaultRecoveryGrace {
		t.Fatalf("an unset grace must fall back to the documented default")
	}
}

// jcsSHA256HexForTest is an independent hash of the expected canonical bytes.
func jcsSHA256HexForTest(canonical []byte) string {
	sum := sha256.Sum256(canonical)
	return hex.EncodeToString(sum[:])
}
