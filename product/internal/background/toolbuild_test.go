package background

import (
	"strings"
	"testing"
)

func TestContentBundleDigestIsDeterministicAndSorted(t *testing.T) {
	source := []byte("source-zip")
	lock := []byte(`{"requirements":[]}`)
	sbom := []byte(`{"sbom_version":1}`)

	digest := contentBundleDigest(source, lock, sbom)
	if !strings.HasPrefix(digest, "sha256:") || len(digest) != len("sha256:")+64 {
		t.Fatalf("digest = %q", digest)
	}
	if again := contentBundleDigest(source, lock, sbom); again != digest {
		t.Fatalf("the same bytes produced %q and %q", digest, again)
	}
	// The rule sorts the three byte strings, so argument order is irrelevant.
	if swapped := contentBundleDigest(sbom, source, lock); swapped != digest {
		t.Fatalf("argument order changed the digest: %q vs %q", digest, swapped)
	}
	if changed := contentBundleDigest(source, lock, []byte(`{"sbom_version":2}`)); changed == digest {
		t.Fatal("a different SBOM produced the same digest")
	}
}

func TestToolBuildFailureVocabularyIsClosedAndTransientOnly(t *testing.T) {
	for _, code := range []string{
		CodeTransientStorageIO,
		CodeTransientInternal,
		CodeLeaseExpired,
	} {
		if !Retryable(KindToolBuild, code) {
			t.Fatalf("%s must be retryable for tool_build", code)
		}
	}
	for _, code := range []string{
		CodeBuildFailed,
		CodeBuildSourceInvalid,
		CodeBuildSourceChanged,
		CodeBuildSandboxUnavailable,
		CodeBuildTimeout,
		CodeBuildCrashed,
		CodeBuildOutputInvalid,
		CodeBuildArtifactsInvalid,
		CodeBuildArtifactInvalid,
		CodeBuildDependenciesMissing,
		"something_made_up",
	} {
		if Retryable(KindToolBuild, code) {
			t.Fatalf("%s must not be retryable for tool_build", code)
		}
	}
	if ClassifyFailure(KindToolBuild, CodeBuildDependenciesMissing) != FailureBusiness {
		t.Fatal("an unavailable dependency is a business failure, not an infrastructure one")
	}
	if ClassifyFailure(KindToolBuild, CodeTransientStorageIO) != FailureInfrastructure {
		t.Fatal("a transient storage failure must be infrastructure")
	}
}

func TestFailedBuildCodeIsStoredOnlyFromTheSafeVocabulary(t *testing.T) {
	if got := safeToolBuildCode(CodeBuildDependenciesMissing); got != CodeBuildDependenciesMissing {
		t.Fatalf("safe code changed: %q", got)
	}
	if got := safeToolBuildCode("attacker supplied text\n"); got != CodeBuildFailed {
		t.Fatalf("an unknown code was stored verbatim: %q", got)
	}
	for code := range safeToolBuildCodes {
		if strings.TrimSpace(code) != code {
			t.Fatalf("safe code %q has surrounding whitespace", code)
		}
	}
}
