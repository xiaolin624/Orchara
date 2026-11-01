package identity

import (
	"errors"
	"testing"
)

func TestNormalizeRolesEnforcesTheBindableVocabulary(t *testing.T) {
	if _, err := normalizeRoles(nil); !errors.Is(err, ErrValidation) {
		t.Fatalf("an empty role set was accepted: %v", err)
	}
	for name, roles := range map[string][]string{
		"duplicate": {"admin", "admin"},
		"unknown":   {"superuser"},
		"blank":     {""},
	} {
		if _, err := normalizeRoles(roles); !errors.Is(err, ErrValidation) {
			t.Fatalf("%s roles were accepted: %v", name, err)
		}
	}
	// Surrounding whitespace is trimmed rather than silently binding " admin".
	got, err := normalizeRoles([]string{" admin ", "user"})
	if err != nil {
		t.Fatalf("the valid set was rejected: %v", err)
	}
	if len(got) != 2 || got[0] != "admin" || got[1] != "user" {
		t.Fatalf("roles were not normalized: %v", got)
	}
}

func TestNormalizeRolesReportsTheOffendingField(t *testing.T) {
	_, err := normalizeRoles([]string{"creator", "wizard"})
	var validation *ValidationError
	if !errors.As(err, &validation) {
		t.Fatalf("not a typed validation error: %v", err)
	}
	if validation.Field != "roles" {
		t.Fatalf("reported field %q, want roles", validation.Field)
	}
}

func TestRemovingLastAdminIsRefusedOnlyWhenNobodyElseRemains(t *testing.T) {
	cases := []struct {
		name              string
		current           []string
		next              []string
		otherEnabledAdmin int
		blocked           bool
	}{
		{"sole admin demoted", []string{"admin"}, []string{"user"}, 0, true},
		{"sole admin demoted to creator", []string{"admin", "creator"}, []string{"creator"}, 0, true},
		{"another admin remains", []string{"admin"}, []string{"user"}, 1, false},
		{"admin keeps the role", []string{"admin", "user"}, []string{"admin"}, 0, false},
		{"non-admin demoted", []string{"creator"}, []string{"user"}, 0, false},
		{"admin promoted, not removed", []string{"user"}, []string{"admin", "user"}, 0, false},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			blocked := removingLastAdmin(testCase.current, testCase.next, testCase.otherEnabledAdmin)
			if blocked != testCase.blocked {
				t.Fatalf("blocked=%v, want %v", blocked, testCase.blocked)
			}
		})
	}
}
