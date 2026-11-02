package api

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"orchara/platform/internal/identity"
)

// cookieIdentity builds the identity Authenticate would return for a session
// cookie with the given roles.
func cookieIdentity(roles ...string) *identity.Identity {
	rawSession := "raw-session-token-for-tests"
	csrfToken := identity.DeriveCSRFToken(rawSession)
	return &identity.Identity{
		PrincipalID: "11111111-1111-4111-8111-111111111111",
		Kind:        "user",
		Roles:       roles,
		Method:      identity.AuthMethodSessionCookie,
		CSRFToken:   csrfToken,
		CSRFHash:    identity.HashToken(csrfToken),
		SessionHash: identity.HashToken(rawSession),
	}
}

// apiKeyIdentity stands in for the credential T020 adds: no CSRF token at all.
func apiKeyIdentity(roles ...string) *identity.Identity {
	return &identity.Identity{
		PrincipalID: "22222222-2222-4222-8222-222222222222",
		Kind:        "service",
		Roles:       roles,
		Method:      identity.AuthMethodAPIKey,
	}
}

func TestCheckAuthorizationMatrix(t *testing.T) {
	session := cookieIdentity("admin")
	sessionWithWrongCSRF := "some-other-session-csrf-token"

	cases := []struct {
		name      string
		caller    *identity.Identity
		method    string
		csrf      string
		role      string
		wantNil   bool
		wantCode  string
		wantState int
	}{
		{name: "safe method needs no CSRF", caller: cookieIdentity(), method: http.MethodGet, wantNil: true},
		{name: "HEAD needs no CSRF", caller: cookieIdentity(), method: http.MethodHead, wantNil: true},
		{
			name: "unsafe method without a CSRF header", caller: cookieIdentity(),
			method: http.MethodPost, wantCode: "csrf_invalid", wantState: http.StatusForbidden,
		},
		{
			name: "unsafe method with another session's CSRF token", caller: cookieIdentity(),
			method: http.MethodPost, csrf: sessionWithWrongCSRF, wantCode: "csrf_invalid", wantState: http.StatusForbidden,
		},
		{
			name: "PUT without CSRF", caller: cookieIdentity(), method: http.MethodPut,
			wantCode: "csrf_invalid", wantState: http.StatusForbidden,
		},
		{
			name: "PATCH without CSRF", caller: cookieIdentity(), method: http.MethodPatch,
			wantCode: "csrf_invalid", wantState: http.StatusForbidden,
		},
		{
			name: "DELETE without CSRF", caller: cookieIdentity(), method: http.MethodDelete,
			wantCode: "csrf_invalid", wantState: http.StatusForbidden,
		},
		{
			name: "unsafe method with the right CSRF token", caller: cookieIdentity(),
			method: http.MethodPost, csrf: cookieIdentity().CSRFToken, wantNil: true,
		},
		{
			name: "required role present", caller: session, method: http.MethodPost,
			csrf: session.CSRFToken, role: "admin", wantNil: true,
		},
		{
			name: "required role missing on a read", caller: cookieIdentity("user"),
			method: http.MethodGet, role: "admin", wantCode: "forbidden", wantState: http.StatusForbidden,
		},
		{
			name: "required role missing on a write", caller: cookieIdentity("user"),
			method: http.MethodPost, csrf: cookieIdentity().CSRFToken, role: "admin",
			wantCode: "forbidden", wantState: http.StatusForbidden,
		},
		{
			name: "CSRF is checked before the role", caller: session,
			method: http.MethodPost, csrf: "wrong", role: "admin",
			wantCode: "csrf_invalid", wantState: http.StatusForbidden,
		},
		{
			name: "an API key is exempt from CSRF", caller: apiKeyIdentity("admin"),
			method: http.MethodPost, wantNil: true,
		},
		{
			name: "an API key still needs the role", caller: apiKeyIdentity("user"),
			method: http.MethodPost, role: "admin", wantCode: "forbidden", wantState: http.StatusForbidden,
		},
	}

	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			failure := checkAuthorization(testCase.caller, testCase.method, testCase.csrf, testCase.role)
			if testCase.wantNil {
				if failure != nil {
					t.Fatalf("unexpected refusal: %+v", failure)
				}
				return
			}
			if failure == nil {
				t.Fatal("expected a refusal, got none")
			}
			if failure.Status != testCase.wantState {
				t.Fatalf("status = %d, want %d", failure.Status, testCase.wantState)
			}
			if failure.Code != testCase.wantCode {
				t.Fatalf("code = %q, want %q", failure.Code, testCase.wantCode)
			}
			if failure.Message == "" {
				t.Fatal("a refusal must carry a message for the contract envelope")
			}
		})
	}
}

func TestUnsafeMethodsRequiringCSRF(t *testing.T) {
	for _, method := range []string{
		http.MethodPost, http.MethodPut, http.MethodPatch, http.MethodDelete,
	} {
		if !requiresCSRF(method) {
			t.Fatalf("%s must require CSRF", method)
		}
	}
	for _, method := range []string{
		http.MethodGet, http.MethodHead, http.MethodOptions, http.MethodTrace,
	} {
		if requiresCSRF(method) {
			t.Fatalf("%s must not require CSRF", method)
		}
	}
}

func TestIdentityViewCarriesCSRFOnlyForACookieSession(t *testing.T) {
	expires := time.Date(2026, 11, 2, 12, 0, 0, 0, time.UTC)

	session := cookieIdentity("admin")
	session.PrincipalID = "33333333-3333-4333-8333-333333333333"
	session.ExpiresAt = expires
	view := identityViewFor(session)
	if view.CSRFToken == nil || *view.CSRFToken != session.CSRFToken {
		t.Fatalf("a cookie session must expose its CSRF token: %+v", view)
	}
	if view.PrincipalID != session.PrincipalID || view.Kind != "user" {
		t.Fatalf("the view did not carry the identity: %+v", view)
	}
	if view.Roles == nil || len(view.Roles) != 1 || view.Roles[0] != "admin" {
		t.Fatalf("roles = %v", view.Roles)
	}
	if view.ExpiresAt != "2026-11-02T12:00:00Z" {
		t.Fatalf("expires_at = %q", view.ExpiresAt)
	}

	// A credential with no browser CSRF (an API key, from T020) reports null and
	// an empty role array rather than a missing field.
	apiKey := apiKeyIdentity()
	apiKey.ExpiresAt = expires
	keyView := identityViewFor(apiKey)
	if keyView.CSRFToken != nil {
		t.Fatalf("an API key must not expose a CSRF token: %+v", keyView)
	}
	if keyView.Roles == nil || len(keyView.Roles) != 0 {
		t.Fatalf("roles = %v, want an empty array", keyView.Roles)
	}

	payload, err := json.Marshal(keyView)
	if err != nil {
		t.Fatalf("marshalling the view: %v", err)
	}
	for _, want := range []string{`"csrf_token":null`, `"roles":[]`} {
		if !strings.Contains(string(payload), want) {
			t.Fatalf("the JSON view does not contain %s: %s", want, payload)
		}
	}

	sessionPayload, err := json.Marshal(view)
	if err != nil {
		t.Fatalf("marshalling the view: %v", err)
	}
	want := `{"principal_id":"33333333-3333-4333-8333-333333333333","kind":"user","roles":["admin"],"csrf_token":"` + session.CSRFToken + `","expires_at":"2026-11-02T12:00:00Z"}`
	if string(sessionPayload) != want {
		t.Fatalf("IdentityView drifted from the contract:\n got %s\nwant %s", sessionPayload, want)
	}
}
