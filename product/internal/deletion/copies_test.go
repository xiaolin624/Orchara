package deletion

import (
	"errors"
	"testing"
)

// TestVendorCopyCheckDistinguishesAFailedCountFromAnAbsentPath pins the T072
// evidence rule: a model-call count that fails is a failed check recorded as
// such, never the "there was nothing to look for" absent outcome.
func TestVendorCopyCheckDistinguishesAFailedCountFromAnAbsentPath(t *testing.T) {
	cases := []struct {
		name        string
		calls       int
		countErr    error
		wantOutcome string
		wantReason  string
	}{
		{"count query failed", 0, errors.New("database is down"), "failed", "model_call_count_failed"},
		{"no model calls", 0, nil, "absent", "no_external_model_path"},
		{"model calls observed", 3, nil, "external", "model_provider_may_retain_prompts"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			outcome, reason := modelCallCopyOutcome(testCase.calls, testCase.countErr)
			if outcome != testCase.wantOutcome || reason != testCase.wantReason {
				t.Fatalf("modelCallCopyOutcome(%d, %v) = (%q, %q), want (%q, %q)",
					testCase.calls, testCase.countErr, outcome, reason,
					testCase.wantOutcome, testCase.wantReason)
			}
		})
	}
}
