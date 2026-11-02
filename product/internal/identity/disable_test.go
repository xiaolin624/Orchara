package identity

import (
	"errors"
	"testing"
)

// TestDisableStateIdempotency fixes the rule that a request which asks for the
// state the principal is already in changes nothing: no audit row and no policy
// epoch bump. The table is the whole rule, so an implementation cannot quietly
// treat "enable an enabled principal" as a change.
func TestDisableStateIdempotency(t *testing.T) {
	cases := []struct {
		name      string
		requested bool
		current   bool
		want      bool
	}{
		{"disable an enabled principal changes state", true, false, true},
		{"enable a disabled principal changes state", false, true, true},
		{"disable an already disabled principal is a no-op", true, true, false},
		{"enable an already enabled principal is a no-op", false, false, false},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			got := disableChangesState(testCase.requested, testCase.current)
			if got != testCase.want {
				t.Fatalf("disableChangesState(%v, %v) = %v, want %v",
					testCase.requested, testCase.current, got, testCase.want)
			}
		})
	}
}

// TestDisablingLastAdminMatrix fixes the last-administrator rule for disable:
// only an enabled human who holds admin can be the last one, and only when no
// other enabled human administrator remains. A service principal is never an
// administrator, and a disabled principal was already excluded.
func TestDisablingLastAdminMatrix(t *testing.T) {
	cases := []struct {
		name     string
		kind     string
		disabled bool
		roles    []string
		others   int
		want     bool
	}{
		{"sole enabled human admin", "user", false, []string{"admin"}, 0, true},
		{"admin with a second enabled admin", "user", false, []string{"admin"}, 1, false},
		{"admin with two others", "user", false, []string{"admin", "creator"}, 2, false},
		{"human admin who is already disabled", "user", true, []string{"admin"}, 0, false},
		{"human non-admin", "user", false, []string{"creator", "user"}, 0, false},
		{"human with no roles", "user", false, nil, 0, false},
		{"service principal that somehow has admin", "service", false, []string{"admin"}, 0, false},
		{"disabled service principal", "service", true, []string{"admin"}, 0, false},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			got := disablingLastAdmin(testCase.kind, testCase.disabled, testCase.roles, testCase.others)
			if got != testCase.want {
				t.Fatalf("disablingLastAdmin(%q, %v, %v, %d) = %v, want %v",
					testCase.kind, testCase.disabled, testCase.roles, testCase.others, got, testCase.want)
			}
		})
	}
}

// TestDisableReasonIsRequiredAndTrimmed keeps the reason a non-secret, human
// readable audit field: whitespace is not a reason, and surrounding whitespace
// must not survive into the audit row.
func TestDisableReasonIsRequiredAndTrimmed(t *testing.T) {
	for _, blank := range []string{"", "   ", "\t\n "} {
		reason, err := validateDisableReason(blank)
		if err == nil {
			t.Fatalf("validateDisableReason(%q) accepted a blank reason", blank)
		}
		if reason != "" {
			t.Fatalf("validateDisableReason(%q) returned %q with an error", blank, reason)
		}
		if !errors.Is(err, ErrValidation) {
			t.Fatalf("validateDisableReason(%q) error %v is not a validation error", blank, err)
		}
		var validation *ValidationError
		if !errors.As(err, &validation) || validation.Field != "reason" {
			t.Fatalf("validateDisableReason(%q) did not name the reason field: %v", blank, err)
		}
	}

	reason, err := validateDisableReason("  离职交接，账号停用  ")
	if err != nil {
		t.Fatalf("validateDisableReason returned an error for a valid reason: %v", err)
	}
	if reason != "离职交接，账号停用" {
		t.Fatalf("validateDisableReason did not trim: %q", reason)
	}
}
