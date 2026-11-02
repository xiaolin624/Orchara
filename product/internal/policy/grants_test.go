package policy

import (
	"errors"
	"strings"
	"testing"
	"time"
)

const (
	validPrincipal = "11111111-1111-4111-8111-111111111111"
	validResource  = "22222222-2222-4222-8222-222222222222"
)

func validGrantParams() GrantParams {
	return GrantParams{
		PrincipalID:  validPrincipal,
		ResourceType: ResourceApplication,
		ResourceID:   validResource,
		Action:       ActionRead,
	}
}

func TestValidateGrantAcceptsTheContractTuple(t *testing.T) {
	now := time.Date(2026, 5, 6, 7, 8, 9, 0, time.UTC)
	if err := ValidateGrant(validGrantParams(), now); err != nil {
		t.Fatalf("the contract tuple was rejected: %v", err)
	}
	future := validGrantParams()
	expires := now.Add(time.Hour)
	future.ExpiresAt = &expires
	if err := ValidateGrant(future, now); err != nil {
		t.Fatalf("a future expiry was rejected: %v", err)
	}
}

func TestValidateGrantReportsTheOffendingField(t *testing.T) {
	now := time.Date(2026, 5, 6, 7, 8, 9, 0, time.UTC)
	past := now.Add(-time.Second)
	equal := now
	cases := []struct {
		name   string
		mutate func(*GrantParams)
		field  string
	}{
		{"malformed principal", func(p *GrantParams) { p.PrincipalID = "not-a-uuid" }, "principal_id"},
		{"empty principal", func(p *GrantParams) { p.PrincipalID = "" }, "principal_id"},
		{"unknown resource type", func(p *GrantParams) { p.ResourceType = "memory" }, "resource_type"},
		{"empty resource type", func(p *GrantParams) { p.ResourceType = "" }, "resource_type"},
		{"malformed resource id", func(p *GrantParams) { p.ResourceID = "abc" }, "resource_id"},
		{"unknown action", func(p *GrantParams) { p.Action = "write" }, "action"},
		{"empty action", func(p *GrantParams) { p.Action = "" }, "action"},
		{"past expiry", func(p *GrantParams) { p.ExpiresAt = &past }, "expires_at"},
		{"expiry equal to now", func(p *GrantParams) { p.ExpiresAt = &equal }, "expires_at"},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			params := validGrantParams()
			testCase.mutate(&params)
			err := ValidateGrant(params, now)
			if err == nil {
				t.Fatalf("%s was accepted", testCase.name)
			}
			if !errors.Is(err, ErrValidation) {
				t.Fatalf("%s is not a validation error: %v", testCase.name, err)
			}
			var validation *ValidationError
			if !errors.As(err, &validation) {
				t.Fatalf("%s is not typed: %v", testCase.name, err)
			}
			if validation.Field != testCase.field {
				t.Fatalf("%s reported field %q, want %q", testCase.name, validation.Field, testCase.field)
			}
		})
	}
}

func TestTheGrantVocabularyIsExtendableButClosed(t *testing.T) {
	// F01 fixes exactly one resource type and one action. A typo must be a
	// validation error, not a silently unreadable row.
	if !ResourceTypes[ResourceApplication] {
		t.Fatal("application is not a known resource type")
	}
	if !Actions[ActionRead] {
		t.Fatal("read is not a known action")
	}
	if len(ResourceTypes) != 1 || len(Actions) != 1 {
		t.Fatalf("F01 vocabulary changed unexpectedly: %v %v", ResourceTypes, Actions)
	}
}

func TestValidateSelfGrantRejectsTheActingAdministratorCaseInsensitively(t *testing.T) {
	params := validGrantParams()
	if err := validateSelfGrant(validPrincipal, params); err == nil {
		t.Fatal("an administrator was allowed to grant themselves read access")
	} else {
		if !errors.Is(err, ErrValidation) {
			t.Fatalf("a self grant is not a validation error: %v", err)
		}
		var validation *ValidationError
		if !errors.As(err, &validation) || validation.Field != "principal_id" {
			t.Fatalf("a self grant did not report principal_id: %v", err)
		}
		if !strings.Contains(validation.Message, "another administrator") {
			t.Fatalf("the self-grant message does not name the two-person path: %q", validation.Message)
		}
	}

	// UUID text is case-insensitive. An upper-case spelling of the caller's own
	// id must not slip past the check and become a real grant row.
	upper := strings.ToUpper(validPrincipal)
	params.PrincipalID = upper
	if err := validateSelfGrant(validPrincipal, params); err == nil {
		t.Fatal("an upper-case spelling of the caller's id bypassed the self-grant rule")
	}

	params.PrincipalID = "33333333-3333-4333-8333-333333333333"
	if err := validateSelfGrant(validPrincipal, params); err != nil {
		t.Fatalf("a grant to another principal was refused: %v", err)
	}
}
