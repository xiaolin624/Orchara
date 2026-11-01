package identity

import (
	"crypto/sha256"
	"encoding/base64"
	"testing"
)

// Fixed vectors were computed independently with Python's hmac/hashlib, so the
// Go implementation is never validated against itself.
const (
	vectorSessionToken = "fixed-session-token-for-vector"
	vectorCSRFToken    = "KMCn7pjQzLMDv06LeH08lKjV_823uTSywvAS4B-yZh8"
	vectorCSRFHash     = "7ba17fb44240e3bf42dd8c61e231482afe5694d8131cb494f48912c6c87e92fb"
)

func TestDeriveCSRFTokenMatchesAnIndependentVector(t *testing.T) {
	if got := DeriveCSRFToken(vectorSessionToken); got != vectorCSRFToken {
		t.Fatalf("DeriveCSRFToken(%q) = %q, want %q", vectorSessionToken, got, vectorCSRFToken)
	}
	if got := HashToken(vectorCSRFToken); got != vectorCSRFHash {
		t.Fatalf("stored csrf_hash = %q, want %q", got, vectorCSRFHash)
	}
}

func TestDeriveCSRFTokenIsDeterministicAndBoundToItsSession(t *testing.T) {
	first := DeriveCSRFToken("session-a")
	if again := DeriveCSRFToken("session-a"); again != first {
		t.Fatalf("the derivation is not deterministic: %q vs %q", first, again)
	}
	if other := DeriveCSRFToken("session-b"); other == first {
		t.Fatal("two different sessions derived the same CSRF token")
	}

	raw, err := base64.RawURLEncoding.DecodeString(first)
	if err != nil {
		t.Fatalf("the CSRF token is not base64url: %v", err)
	}
	if len(raw) != sha256.Size {
		t.Fatalf("the CSRF token carries %d bytes, want %d", len(raw), sha256.Size)
	}
}

func TestVerifyCSRFTokenComparesStoredHashes(t *testing.T) {
	rawSession := "session-for-verification"
	token := DeriveCSRFToken(rawSession)
	stored := HashToken(token)

	cases := []struct {
		name      string
		stored    string
		presented string
		want      bool
	}{
		{"presented token matches its stored hash", stored, token, true},
		{"another session's token", stored, DeriveCSRFToken("other-session"), false},
		{"the session token itself is not the CSRF token", stored, rawSession, false},
		{"empty presentation", stored, "", false},
		{"no stored hash (a credential without CSRF)", "", token, false},
		{"the stored hash is not accepted as the preimage", stored, stored, false},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			if got := VerifyCSRFToken(testCase.stored, testCase.presented); got != testCase.want {
				t.Fatalf("VerifyCSRFToken(%q, %q) = %v, want %v",
					testCase.stored, testCase.presented, got, testCase.want)
			}
		})
	}
}
