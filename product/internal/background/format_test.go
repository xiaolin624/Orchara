package background

import "testing"

// The T028 format admission is a content refusal: it must quarantine the
// artifact, never be retried, and classify as a business failure. The test pins
// all three facts together so a later change to one table cannot silently
// detach the code from its meaning.
func TestFormatRejectionQuarantinesAndIsNeverRetried(t *testing.T) {
	for _, code := range []string{CodeArtifactFormatRejected, CodeArtifactUploadIncomplete} {
		if !ImpliesQuarantine(code) {
			t.Fatalf("%q must quarantine the artifact", code)
		}
		if Retryable(KindArtifactFinalize, code) {
			t.Fatalf("%q must never be retried", code)
		}
		if got := ClassifyFailure(KindArtifactFinalize, code); got != FailureBusiness {
			t.Fatalf("%q classified as %q, want business", code, got)
		}
	}
}

// The T022 staging-missing code keeps its documented behaviour (job fails,
// artifact is not activated and not quarantined). T028 uses its own
// upload-incomplete code for a user upload, so the two meanings stay distinct.
func TestStagingMissingKeepsItsT022Meaning(t *testing.T) {
	if ImpliesQuarantine(CodeArtifactStagingMissing) {
		t.Fatal("artifact_staging_missing must keep its T022 meaning")
	}
	if _, ok := quarantineCodes[CodeArtifactFormatRejected]; !ok {
		t.Fatal("the format rejection code is missing from the quarantine table")
	}
}
