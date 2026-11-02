package background

import (
	"errors"
	"testing"
	"time"
)

func TestTheWhitelistIsTheEightSpecifiedKinds(t *testing.T) {
	want := []Kind{
		KindArtifactFinalize, KindRouteRequest, KindToolBuild, KindDocumentIngest,
		KindApplicationEvaluate, KindModelValidate, KindDeleteContent, KindCatalogIndex,
	}
	got := AllKinds()
	if len(got) != len(want) {
		t.Fatalf("whitelist has %d kinds, want %d: %v", len(got), len(want), got)
	}
	for index := range want {
		if got[index] != want[index] {
			t.Fatalf("kind %d = %q, want %q", index, got[index], want[index])
		}
		if !KnownKind(string(want[index])) {
			t.Fatalf("%q is missing from the known set", want[index])
		}
	}
	// A shell string or any other request text is not a kind.
	for _, raw := range []string{"sh -c id", "", "ARTIFACT_FINALIZE", "artifact-finalize"} {
		if KnownKind(raw) {
			t.Fatalf("%q must not be a known kind", raw)
		}
	}
	// The returned slice is a copy: extending it must not extend the whitelist.
	got[0] = "sh"
	if AllKinds()[0] != KindArtifactFinalize {
		t.Fatal("AllKinds returned the internal slice")
	}
}

func TestParseKindsRejectsUnknownAndDuplicateKinds(t *testing.T) {
	if _, err := parseKinds("supported_kinds", nil); err == nil {
		t.Fatal("an empty kind list must be refused")
	}
	var validation *ValidationError
	if _, err := parseKinds("supported_kinds", []string{"artifact_finalize", "sh"}); !errors.As(err, &validation) {
		t.Fatalf("unknown kind: expected a ValidationError, got %v", err)
	}
	if _, err := parseKinds("supported_kinds", []string{"artifact_finalize", "artifact_finalize"}); err == nil {
		t.Fatal("a duplicate kind must be refused")
	}
	kinds, err := parseKinds("supported_kinds", []string{" artifact_finalize "})
	if err != nil || len(kinds) != 1 || kinds[0] != KindArtifactFinalize {
		t.Fatalf("parseKinds = %v, %v", kinds, err)
	}
}

func TestOnlyThreeStatesCanBeSubmitted(t *testing.T) {
	for _, raw := range []State{StateSucceeded, StateFailed, StateCancelled} {
		if _, ok := ResultState(string(raw)); !ok {
			t.Fatalf("%q must be a submittable state", raw)
		}
	}
	for _, raw := range []State{StateReady, StateLeased, "done", ""} {
		if _, ok := ResultState(string(raw)); ok {
			t.Fatalf("%q must not be submittable by a worker", raw)
		}
	}
}

func TestRetryClassificationIsFixedPerKind(t *testing.T) {
	retryable := []string{CodeTransientStorageIO, CodeTransientInternal, CodeLeaseExpired}
	for _, code := range retryable {
		if !Retryable(KindArtifactFinalize, code) {
			t.Fatalf("%q should be retryable for artifact_finalize", code)
		}
		if ClassifyFailure(KindArtifactFinalize, code) != FailureInfrastructure {
			t.Fatalf("%q should classify as infrastructure", code)
		}
	}
	for _, code := range []string{
		CodeArtifactHashMismatch, CodeSourceDeleted, CodeSourceChanged,
		CodeArtifactTooLarge, "made_up_code", "",
	} {
		if Retryable(KindArtifactFinalize, code) {
			t.Fatalf("%q must not be retried", code)
		}
		if ClassifyFailure(KindArtifactFinalize, code) != FailureBusiness {
			t.Fatalf("%q must classify as business", code)
		}
	}
	// An unknown kind has no retry table: everything is business (fail safe).
	if Retryable(KindRouteRequest, CodeTransientStorageIO) {
		t.Fatal("an unregistered kind must not inherit another kind's retry table")
	}
}

func TestQuarantineCodesAreExactlyTheContentFailures(t *testing.T) {
	for _, code := range []string{
		CodeArtifactHashMismatch, CodeArtifactSizeMismatch, CodeArtifactTooLarge,
		CodeArtifactObjectMissing, CodeArtifactExpectedHashMiss,
	} {
		if !ImpliesQuarantine(code) {
			t.Fatalf("%q must quarantine the artifact", code)
		}
	}
	for _, code := range []string{
		CodeSourceDeleted, CodeSourceChanged, CodeTransientStorageIO,
		CodeArtifactStagingMissing, CodeLeaseExpired,
	} {
		if ImpliesQuarantine(code) {
			t.Fatalf("%q must not quarantine the artifact", code)
		}
	}
}

func TestRetryDelayDoublesPerAttemptAndIsCapped(t *testing.T) {
	base := 100 * time.Millisecond
	want := []time.Duration{100 * time.Millisecond, 200 * time.Millisecond, 400 * time.Millisecond}
	for attempt, expected := range want {
		if got := RetryDelay(attempt+1, base); got != expected {
			t.Fatalf("RetryDelay(%d) = %s, want %s", attempt+1, got, expected)
		}
	}
	if got := RetryDelay(1, time.Hour); got != time.Hour {
		t.Fatalf("RetryDelay at the cap = %s", got)
	}
	if got := RetryDelay(3, time.Hour); got != maxRetryBackoff {
		t.Fatalf("RetryDelay above the cap = %s, want %s", got, maxRetryBackoff)
	}
	if got := RetryDelay(1, 0); got != DefaultRetryBackoff {
		t.Fatalf("RetryDelay with no base = %s", got)
	}
}

func TestValidateEnqueueRefusesBadInput(t *testing.T) {
	valid := EnqueueParams{
		Kind:     KindArtifactFinalize,
		TargetID: "6f1c5f0e-3f8f-4a52-9a4e-0f40f8f0c9b1",
		OwnerID:  "8f2d6b1a-4c3e-4f60-8b7a-2d1c0e9f8a7b",
		DedupKey: "artifact_finalize:6f1c5f0e",
	}
	if _, err := validateEnqueue(valid); err != nil {
		t.Fatalf("the baseline params should validate: %v", err)
	}
	cases := map[string]func(*EnqueueParams){
		"kind":              func(p *EnqueueParams) { p.Kind = "sh -c id" },
		"target_id":         func(p *EnqueueParams) { p.TargetID = "not-a-uuid" },
		"owner_id":          func(p *EnqueueParams) { p.OwnerID = " " },
		"dedup_key":         func(p *EnqueueParams) { p.DedupKey = "  " },
		"expected_sha256":   func(p *EnqueueParams) { p.ExpectedSHA256 = "ABC" },
		"input_artifact":    func(p *EnqueueParams) { p.InputArtifactID = "nope" },
		"expected_revision": func(p *EnqueueParams) { zero := int64(0); p.ExpectedRevision = &zero },
	}
	for label, mutate := range cases {
		params := valid
		mutate(&params)
		if _, err := validateEnqueue(params); err == nil {
			t.Fatalf("%s: invalid params must be refused", label)
		}
	}
	// The upper-case hash is normalised, not rejected: it is still the same bytes.
	upper := valid
	upper.ExpectedSHA256 = "ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789"
	normalized, err := validateEnqueue(upper)
	if err != nil {
		t.Fatalf("an upper-case hash should normalise: %v", err)
	}
	if normalized.ExpectedSHA256 != "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789" {
		t.Fatalf("normalised hash = %q", normalized.ExpectedSHA256)
	}
}

func TestRequestDigestSeparatesContent(t *testing.T) {
	baseline := Completion{State: StateSucceeded}
	if requestDigest(baseline) != requestDigest(Completion{State: StateSucceeded}) {
		t.Fatal("the same request must have the same digest")
	}
	variants := []Completion{
		{State: StateFailed, ErrorCode: CodeSourceDeleted},
		{State: StateSucceeded, ResultArtifactID: "6f1c5f0e-3f8f-4a52-9a4e-0f40f8f0c9b1"},
	}
	for _, variant := range variants {
		if requestDigest(baseline) == requestDigest(variant) {
			t.Fatalf("digest collision: %+v", variant)
		}
	}
}
