package background

import (
	"testing"

	"orchara/platform/internal/deletion"
)

// TestPurgeFailureCodesAreTheControllerVocabulary pins the one map between the
// deletion engine's outcome enum and the controller's closed error-code table.
// The deletion job row and the background job row must record the same spelling
// (deletion.PurgeFailure.ErrorCode), and the retry table must classify exactly
// the transient ones as retryable: a shared reference or an unsupported target
// can never become true on a second attempt.
func TestPurgeFailureCodesAreTheControllerVocabulary(t *testing.T) {
	cases := []struct {
		failure deletion.PurgeFailure
		code    string
		retry   bool
	}{
		{deletion.PurgeObjectsPresent, CodeTransientStorageIO, true},
		{deletion.PurgeIndexRemain, CodeTransientInternal, true},
		{deletion.PurgeCacheRemain, CodeTransientInternal, true},
		{deletion.PurgeSharedReference, CodeObjectSharedReference, false},
		{deletion.PurgeUnsupportedTarget, CodePurgeUnsupportedTarget, false},
	}
	for _, testCase := range cases {
		if got := purgeFailureCode(testCase.failure); got != testCase.code {
			t.Errorf("purgeFailureCode(%q) = %q, want %q",
				testCase.failure, got, testCase.code)
		}
		if got := testCase.failure.ErrorCode(); got != testCase.code {
			t.Errorf("PurgeFailure(%q).ErrorCode() = %q, want %q",
				testCase.failure, got, testCase.code)
		}
		if got := Retryable(KindDeleteContent, testCase.code); got != testCase.retry {
			t.Errorf("Retryable(delete_content, %q) = %t, want %t",
				testCase.code, got, testCase.retry)
		}
	}
	if got := purgeFailureCode(deletion.PurgeOK); got != CodeTransientInternal {
		t.Errorf("an unmapped purge failure must fail safe as transient_internal, got %q", got)
	}
}
