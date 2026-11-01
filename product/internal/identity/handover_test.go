package identity

import (
	"errors"
	"testing"
)

func handoverFixture() (from, to, by handoverPrincipal) {
	return handoverPrincipal{
			ID: "11111111-1111-4111-8111-111111111111", Kind: "user", HasUsersRow: true,
			Roles: []string{"creator", "user"},
		},
		handoverPrincipal{
			ID: "22222222-2222-4222-8222-222222222222", Kind: "user", HasUsersRow: true,
			Roles: []string{"admin"},
		},
		handoverPrincipal{
			ID: "33333333-3333-4333-8333-333333333333", Kind: "user", HasUsersRow: true,
			Roles: []string{"admin"},
		}
}

// TestHandoverTargetValidationMatrix fixes the authorization rule for a
// responsibility handover: the actor and the successor must both be enabled
// human administrators, because only they are already authorized to manage
// service accounts. A departing user may be disabled (that is the point of the
// command), but must still be a human.
func TestHandoverTargetValidationMatrix(t *testing.T) {
	cases := []struct {
		name    string
		mutate  func(*handoverPrincipal, *handoverPrincipal, *handoverPrincipal)
		wantErr error
	}{
		{"enabled admin hands over to another enabled admin", func(_, _, _ *handoverPrincipal) {}, nil},
		{"the departing user may be disabled", func(from, _, _ *handoverPrincipal) { from.Disabled = true }, nil},
		{"from and to are the same principal", func(_, to, _ *handoverPrincipal) {
			to.ID = "11111111-1111-4111-8111-111111111111"
		}, ErrHandoverSamePrincipal},
		{"successor is not an administrator", func(_, to, _ *handoverPrincipal) {
			to.Roles = []string{"creator", "user"}
		}, ErrHandoverTargetNotAdmin},
		{"successor is disabled", func(_, to, _ *handoverPrincipal) { to.Disabled = true }, ErrHandoverTargetNotAdmin},
		{"successor is a service principal", func(_, to, _ *handoverPrincipal) {
			to.Kind = "service"
			to.HasUsersRow = false
			to.Roles = nil
		}, ErrHandoverTargetNotAdmin},
		{"actor is not an administrator", func(_, _, by *handoverPrincipal) {
			by.Roles = []string{"user"}
		}, ErrHandoverActorNotAdmin},
		{"actor is disabled", func(_, _, by *handoverPrincipal) { by.Disabled = true }, ErrHandoverActorNotAdmin},
		{"actor is a service principal", func(_, _, by *handoverPrincipal) {
			by.Kind = "service"
			by.HasUsersRow = false
			by.Roles = nil
		}, ErrHandoverActorNotAdmin},
		{"departing principal is a service principal", func(from, _, _ *handoverPrincipal) {
			from.Kind = "service"
			from.HasUsersRow = false
			from.Roles = nil
		}, ErrHandoverFromNotHuman},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			from, to, by := handoverFixture()
			testCase.mutate(&from, &to, &by)
			err := validateHandover(from, to, by)
			if testCase.wantErr == nil {
				if err != nil {
					t.Fatalf("validateHandover returned %v for a valid handover", err)
				}
				return
			}
			if !errors.Is(err, testCase.wantErr) {
				t.Fatalf("validateHandover returned %v, want %v", err, testCase.wantErr)
			}
		})
	}
}
