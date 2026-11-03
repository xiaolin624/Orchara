package background

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
)

// bundleDigestOracle builds the documented canonical JCS array by hand. It is an
// independent construction (not a call to contentBundleDigest): the role labels
// are what make the identity injective, so swapping two payloads must change it.
func bundleDigestOracle(source, lock, sbom []byte) string {
	type entry struct {
		Role   string `json:"role"`
		SHA256 string `json:"sha256"`
	}
	sum := func(payload []byte) string {
		digest := sha256.Sum256(payload)
		return hex.EncodeToString(digest[:])
	}
	document, err := json.Marshal([]entry{
		{Role: "source", SHA256: sum(source)},
		{Role: "lock", SHA256: sum(lock)},
		{Role: "sbom", SHA256: sum(sbom)},
	})
	if err != nil {
		panic(err)
	}
	digest := sha256.Sum256(document)
	return "sha256:" + hex.EncodeToString(digest[:])
}

func TestContentBundleDigestIsInjectiveAcrossRoles(t *testing.T) {
	source := []byte("source-zip")
	lock := []byte(`{"requirements":[]}`)
	sbom := []byte(`{"sbom_version":1}`)

	digest := contentBundleDigest(source, lock, sbom)
	if digest != bundleDigestOracle(source, lock, sbom) {
		t.Fatalf("digest = %q, want the canonical {role,sha256} array hash", digest)
	}
	if again := contentBundleDigest(source, lock, sbom); again != digest {
		t.Fatalf("the same bytes produced %q and %q", digest, again)
	}
	// Role labels are part of the identity: a swap of payloads is a different
	// bundle, never the same digest.
	if swapped := contentBundleDigest(sbom, source, lock); swapped == digest {
		t.Fatal("swapping source/lock/SBOM roles produced the same digest")
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
