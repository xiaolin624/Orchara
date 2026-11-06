package approvals

import "testing"

// TestWaitingReferencesApproval pins the checkpoint form the sweeper may
// resolve. A stale pending approval from an earlier proposal must expire without
// resolving the Run that currently waits on a different one; a capability
// approval's wait is not a node-approval checkpoint either.
func TestWaitingReferencesApproval(t *testing.T) {
	approval := &Approval{ID: "11111111-1111-4111-8111-111111111111"}
	tests := []struct {
		name       string
		checkpoint string
		want       bool
	}{
		{"node approval match", "approval:" + approval.ID, true},
		{"other node wait", "approval:44444444-4444-4444-8444-444444444444", false},
		{"capability wait is not a node wait", "capability:" + approval.ID, false},
		{"whitespace is normalised", "  approval:" + approval.ID + "  ", true},
		{"empty checkpoint", "", false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := waitingReferencesApproval(test.checkpoint, approval); got != test.want {
				t.Fatalf("waitingReferencesApproval(%q) = %t, want %t",
					test.checkpoint, got, test.want)
			}
		})
	}
}
