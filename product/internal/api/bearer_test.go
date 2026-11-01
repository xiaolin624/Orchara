package api

import (
	"net/http"
	"testing"

	"orchara/platform/internal/identity"
)

// The bearer header is parsed in exactly one place. These cases fix the
// contract: one header, case-insensitive scheme, no extra words, and a present
// but malformed header must be distinguishable from an absent one (an absent
// header falls back to the session cookie; a malformed one never does).
func TestParseBearerHeaderAcceptsExactlyOneWellFormedHeader(t *testing.T) {
	cases := []struct {
		name        string
		values      []string
		wantSecret  string
		wantPresent bool
		wantOK      bool
	}{
		{"absent", nil, "", false, false},
		{"contract form", []string{"Bearer orchara_abc"}, "orchara_abc", true, true},
		{"lowercase scheme", []string{"bearer orchara_abc"}, "orchara_abc", true, true},
		{"uppercase scheme", []string{"BEARER orchara_abc"}, "orchara_abc", true, true},
		{"surrounding whitespace", []string{"  Bearer orchara_abc  "}, "orchara_abc", true, true},
		{"no scheme", []string{"orchara_abc"}, "", true, false},
		{"wrong scheme", []string{"Basic orchara_abc"}, "", true, false},
		{"empty value", []string{""}, "", true, false},
		{"scheme only", []string{"Bearer"}, "", true, false},
		{"empty secret", []string{"Bearer "}, "", true, false},
		{"double space", []string{"Bearer  orchara_abc"}, "", true, false},
		{"two words", []string{"Bearer orchara_abc extra"}, "", true, false},
		{"inner tab", []string{"Bearer orchara\tabc"}, "", true, false},
		{"duplicate headers", []string{"Bearer orchara_a", "Bearer orchara_b"}, "", true, false},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			secret, present, ok := parseBearerHeader(testCase.values)
			if secret != testCase.wantSecret || present != testCase.wantPresent || ok != testCase.wantOK {
				t.Fatalf("parseBearerHeader(%q) = (%q, %v, %v), want (%q, %v, %v)",
					testCase.values, secret, present, ok,
					testCase.wantSecret, testCase.wantPresent, testCase.wantOK)
			}
		})
	}
}

// requireScope only constrains API-key credentials: a human session is not
// scope-limited in F01, and an API key without the scope is refused before any
// catalog read.
func TestRequireScopeMatrix(t *testing.T) {
	key := func(scopes ...string) *identity.Identity {
		return &identity.Identity{Method: identity.AuthMethodAPIKey, Kind: "service", Scopes: scopes}
	}
	cases := []struct {
		name   string
		caller *identity.Identity
		want   *authzFailure
	}{
		{"session with no scopes", &identity.Identity{Method: identity.AuthMethodSessionCookie}, nil},
		{"nil caller", nil, nil},
		{"key with the scope", key(identity.ScopeApplicationRead), nil},
		{
			"key with another scope only",
			key("application:write"),
			&authzFailure{Status: http.StatusForbidden, Code: "insufficient_scope"},
		},
		{
			"key with no scopes",
			key(),
			&authzFailure{Status: http.StatusForbidden, Code: "insufficient_scope"},
		},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			failure := requireScope(testCase.caller, identity.ScopeApplicationRead)
			if testCase.want == nil {
				if failure != nil {
					t.Fatalf("unexpected refusal: %+v", failure)
				}
				return
			}
			if failure == nil {
				t.Fatal("expected a refusal, got none")
			}
			if failure.Status != testCase.want.Status || failure.Code != testCase.want.Code {
				t.Fatalf("got %+v, want %+v", failure, testCase.want)
			}
			if failure.Message == "" {
				t.Fatal("a refusal must carry a message for the contract envelope")
			}
		})
	}
}
