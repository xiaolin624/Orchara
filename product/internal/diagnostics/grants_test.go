package diagnostics

// F11/T073: pure validation tests for the grant request shape. They are the
// matrix the HTTP handler and the service both use; no database is involved.

import (
	"errors"
	"testing"
	"time"
)

const (
	runID     = "11111111-1111-4111-8111-111111111111"
	granteeID = "22222222-2222-4222-8222-222222222222"
	objectID  = "33333333-3333-4333-8333-333333333333"
)

func validParams(now time.Time) CreateParams {
	return CreateParams{
		RunID:       runID,
		GranteeID:   granteeID,
		Purposes:    []string{"troubleshooting", "quality_review"},
		ExpiresAt:   now.Add(time.Hour),
		ObjectScope: []string{"artifact:" + objectID},
	}
}

func TestNormalizeCreateAcceptsTheTicketScope(t *testing.T) {
	now := time.Date(2026, 11, 5, 12, 0, 0, 0, time.UTC)
	params, err := NormalizeCreate(validParams(now), now)
	if err != nil {
		t.Fatalf("valid params rejected: %v", err)
	}
	if params.ExpiresAt.Location() != time.UTC {
		t.Fatalf("expires_at was not normalised to UTC: %v", params.ExpiresAt)
	}
	if len(params.ObjectScope) != 1 || params.ObjectScope[0] != "artifact:"+objectID {
		t.Fatalf("object scope = %#v", params.ObjectScope)
	}
}

func TestNormalizeCreateRefusesOutOfShapeRequests(t *testing.T) {
	now := time.Date(2026, 11, 5, 12, 0, 0, 0, time.UTC)
	cases := []struct {
		name   string
		field  string
		mutate func(*CreateParams)
	}{
		{"run_id", "run_id", func(p *CreateParams) { p.RunID = "not-a-uuid" }},
		{"grantee_id", "grantee_id", func(p *CreateParams) { p.GranteeID = "not-a-uuid" }},
		{"purposes empty", "purposes", func(p *CreateParams) { p.Purposes = nil }},
		{"purposes too many", "purposes", func(p *CreateParams) {
			p.Purposes = []string{"a", "b", "c", "d", "e", "f", "g", "h", "i"}
		}},
		{"purpose uppercase", "purposes", func(p *CreateParams) {
			p.Purposes = []string{"Troubleshooting"}
		}},
		{"expires in past", "expires_at", func(p *CreateParams) {
			p.ExpiresAt = now.Add(-time.Minute)
		}},
		{"expires zero", "expires_at", func(p *CreateParams) { p.ExpiresAt = time.Time{} }},
		{"object wrong kind", "object_scope", func(p *CreateParams) {
			p.ObjectScope = []string{"run:" + objectID}
		}},
		{"object no uuid", "object_scope", func(p *CreateParams) {
			p.ObjectScope = []string{"artifact:not-a-uuid"}
		}},
		{"object empty", "object_scope", func(p *CreateParams) {
			p.ObjectScope = []string{""}
		}},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			params := validParams(now)
			testCase.mutate(&params)
			_, err := NormalizeCreate(params, now)
			if err == nil {
				t.Fatalf("%s was accepted", testCase.name)
			}
			if !errors.Is(err, ErrValidation) {
				t.Fatalf("error is not ErrValidation: %v", err)
			}
			typed, ok := AsValidation(err)
			if !ok {
				t.Fatalf("error is not a ValidationError: %v", err)
			}
			if typed.Field != testCase.field {
				t.Fatalf("field = %q, want %q", typed.Field, testCase.field)
			}
			if typed.Message != "" && containsAny(typed.Message, "Troubleshooting") {
				t.Fatalf("validation message echoes the value: %q", typed.Message)
			}
		})
	}
}

func TestNormalizeCreateDeduplicatesAndBoundsTheSnapshot(t *testing.T) {
	now := time.Date(2026, 11, 5, 12, 0, 0, 0, time.UTC)
	params := validParams(now)
	params.Purposes = []string{"troubleshooting", "troubleshooting", "quality_review"}
	params.ObjectScope = []string{
		"artifact:" + objectID,
		"artifact:" + objectID,
	}
	normalized, err := NormalizeCreate(params, now)
	if err != nil {
		t.Fatalf("valid params rejected: %v", err)
	}
	if len(normalized.Purposes) != 2 {
		t.Fatalf("purposes not deduplicated: %#v", normalized.Purposes)
	}
	if len(normalized.ObjectScope) != 1 {
		t.Fatalf("object scope not deduplicated: %#v", normalized.ObjectScope)
	}
}

func TestParseObjectVocabulary(t *testing.T) {
	kind, id, ok := ParseObject("artifact:" + objectID)
	if !ok || kind != ObjectArtifact || id != objectID {
		t.Fatalf("valid object rejected: %q %q %v", kind, id, ok)
	}
	for _, raw := range []string{
		"",
		"artifact",
		"artifact:",
		"artifact:not-a-uuid",
		"document:" + objectID,
		"run:" + objectID,
		"artifact:" + objectID + ":extra",
	} {
		if _, _, ok := ParseObject(raw); ok {
			t.Fatalf("%q was accepted as an object", raw)
		}
	}
}

func containsAny(haystack string, needles ...string) bool {
	for _, needle := range needles {
		if needle != "" && contains(haystack, needle) {
			return true
		}
	}
	return false
}

func contains(haystack, needle string) bool {
	for i := 0; i+len(needle) <= len(haystack); i++ {
		if haystack[i:i+len(needle)] == needle {
			return true
		}
	}
	return false
}
