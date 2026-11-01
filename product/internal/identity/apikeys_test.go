package identity

import (
	"encoding/hex"
	"errors"
	"regexp"
	"strings"
	"testing"
	"time"
)

// The F01 scope registry is exactly one scope. The test asserts the closed
// vocabulary rather than the code's own map so an accidental widening fails here.
func TestNormalizeScopesAcceptsTheF01Vocabulary(t *testing.T) {
	scopes, err := normalizeScopes([]string{" application:read "})
	if err != nil {
		t.Fatalf("the contract scope was rejected: %v", err)
	}
	if len(scopes) != 1 || scopes[0] != ScopeApplicationRead {
		t.Fatalf("scopes were not normalized: %v", scopes)
	}
}

func TestNormalizeScopesRejectsEmptyUnknownAndDuplicateSets(t *testing.T) {
	cases := map[string][]string{
		"empty":      nil,
		"blank only": {"   "},
		"unknown":    {"application:write"},
		"duplicate":  {"application:read", "application:read"},
	}
	for name, scopes := range cases {
		t.Run(name, func(t *testing.T) {
			_, err := normalizeScopes(scopes)
			if !errors.Is(err, ErrValidation) {
				t.Fatalf("%s scopes were accepted: %v", name, err)
			}
			var validation *ValidationError
			if !errors.As(err, &validation) || validation.Field != "scopes" {
				t.Fatalf("%s reported %v, want field scopes", name, err)
			}
		})
	}
}

func TestPersonalAndMemoryScopesAreRefusedAsPersonalData(t *testing.T) {
	for _, scope := range []string{
		"personal:read", "personal:write", "memory:read", "memory:delete",
	} {
		t.Run(scope, func(t *testing.T) {
			_, err := normalizeScopes([]string{scope})
			if !errors.Is(err, ErrPersonalScope) {
				t.Fatalf("%s was not refused as a personal scope: %v", scope, err)
			}
			var personal *PersonalScopeError
			if !errors.As(err, &personal) {
				t.Fatalf("%s is not a typed personal-scope error: %v", scope, err)
			}
			if personal.Scope != scope {
				t.Fatalf("the refusal names %q, want %q", personal.Scope, scope)
			}
		})
	}
}

func TestAPIKeyExpiryMustBeStrictlyInTheFuture(t *testing.T) {
	now := time.Date(2026, 11, 2, 1, 2, 3, 0, time.UTC)
	if err := validateAPIKeyExpiry(now.Add(time.Second), now); err != nil {
		t.Fatalf("a future expiry was rejected: %v", err)
	}
	for name, expiresAt := range map[string]time.Time{
		"now":      now,
		"past":     now.Add(-time.Hour),
		"zero":     time.Time{},
		"one tick": now.Add(-time.Nanosecond),
	} {
		t.Run(name, func(t *testing.T) {
			err := validateAPIKeyExpiry(expiresAt, now)
			if !errors.Is(err, ErrValidation) {
				t.Fatalf("%s expiry was accepted: %v", name, err)
			}
			var validation *ValidationError
			if !errors.As(err, &validation) || validation.Field != "expires_at" {
				t.Fatalf("%s reported %v, want field expires_at", name, err)
			}
		})
	}
}

func TestNewAPIKeySecretHasTheContractShape(t *testing.T) {
	secret, hash := newAPIKeySecret()
	if !strings.HasPrefix(secret, APIKeyPrefix) {
		t.Fatalf("secret %q does not start with %q", secret, APIKeyPrefix)
	}
	encoded := strings.TrimPrefix(secret, APIKeyPrefix)
	if len(encoded) != 43 {
		t.Fatalf("the 32-byte secret encodes to %d base64url characters, want 43", len(encoded))
	}
	if !regexp.MustCompile(`^[A-Za-z0-9_-]+$`).MatchString(encoded) {
		t.Fatalf("secret is not unpadded base64url: %q", encoded)
	}
	if len(hash) != 64 {
		t.Fatalf("hash is %d characters, want a SHA-256 hex digest", len(hash))
	}
	if _, err := hex.DecodeString(hash); err != nil {
		t.Fatalf("hash is not hexadecimal: %v", err)
	}
	if hash != HashToken(secret) {
		t.Fatalf("the stored hash is not SHA-256(secret)")
	}
	if other, _ := newAPIKeySecret(); other == secret {
		t.Fatalf("two generated secrets are identical")
	}
}

func TestIdentityHasScopeOnlyForItsOwnScopes(t *testing.T) {
	key := &Identity{Method: AuthMethodAPIKey, Scopes: []string{ScopeApplicationRead}}
	if !key.HasScope(ScopeApplicationRead) {
		t.Fatal("the key's own scope was not reported")
	}
	if key.HasScope("application:write") {
		t.Fatal("an absent scope was reported as present")
	}
	session := &Identity{Method: AuthMethodSessionCookie}
	if session.HasScope(ScopeApplicationRead) {
		t.Fatal("a session with no scopes reported a scope")
	}
	var nilIdentity *Identity
	if nilIdentity.HasScope(ScopeApplicationRead) {
		t.Fatal("a nil identity reported a scope")
	}
}

// apiKeyFailure is the pure decision that AuthenticateAPIKey applies to one
// stored row. The matrix fixes the ordering the ticket requires: an invalid
// credential (unknown/revoked/expired) says nothing, then a disabled principal,
// then a service account whose responsible human is gone or disabled.
func TestAPIKeyFailureMatrix(t *testing.T) {
	healthy := apiKeyState{
		Kind:              "service",
		ResponsibleExists: true,
	}
	cases := []struct {
		name   string
		mutate func(*apiKeyState)
		want   error
	}{
		{"healthy service key", func(*apiKeyState) {}, nil},
		{"revoked", func(state *apiKeyState) { state.Revoked = true }, ErrInvalidKey},
		{"expired", func(state *apiKeyState) { state.Expired = true }, ErrInvalidKey},
		{"revoked and expired", func(state *apiKeyState) {
			state.Revoked, state.Expired = true, true
		}, ErrInvalidKey},
		{"service principal disabled", func(state *apiKeyState) {
			state.PrincipalDisabled = true
		}, ErrPrincipalDisabled},
		{"human principal", func(state *apiKeyState) { state.Kind = "user" }, ErrPrincipalDisabled},
		{"responsible user gone", func(state *apiKeyState) {
			state.ResponsibleExists = false
		}, ErrResponsibleDisabled},
		{"responsible user disabled", func(state *apiKeyState) {
			state.ResponsibleDisabled = true
		}, ErrResponsibleDisabled},
		{"invalid beats disabled", func(state *apiKeyState) {
			state.Revoked, state.PrincipalDisabled = true, true
		}, ErrInvalidKey},
		{"principal disabled beats responsible disabled", func(state *apiKeyState) {
			state.PrincipalDisabled, state.ResponsibleDisabled = true, true
		}, ErrPrincipalDisabled},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			state := healthy
			testCase.mutate(&state)
			err := apiKeyFailure(state)
			if testCase.want == nil {
				if err != nil {
					t.Fatalf("unexpected refusal: %v", err)
				}
				return
			}
			if !errors.Is(err, testCase.want) {
				t.Fatalf("got %v, want %v", err, testCase.want)
			}
		})
	}
}
