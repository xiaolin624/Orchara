package identity

import (
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
)

// CSRFDerivationMessage is the fixed message the session-bound CSRF token is
// derived from. It is a domain separator, not a secret. Changing it changes
// every derived CSRF token, and therefore every sessions.csrf_hash, so it is
// effectively part of the schema rather than a tunable.
const CSRFDerivationMessage = "orchara.csrf.v1"

// DeriveCSRFToken returns the CSRF token bound to one raw session token:
//
//	base64url(HMAC-SHA256(key = raw session token, message = CSRFDerivationMessage))
//
// HMAC is a standard pseudorandom function; the platform invents no new
// cryptography. Binding the CSRF token to the session token (instead of storing
// a second random value) buys two properties at once:
//
//   - deterministic: GET /api/v1/auth/me can hand the same token back after a
//     page refresh, with nothing kept in process memory or in the browser;
//   - bound: another session's token derives a different value, so a CSRF token
//     from one session cannot be replayed against another.
//
// The session cookie is HttpOnly, so cross-site JavaScript can read neither the
// session token nor this derived value, and a cross-site form post cannot set
// the X-CSRF-Token header at all.
func DeriveCSRFToken(rawSessionToken string) string {
	mac := hmac.New(sha256.New, []byte(rawSessionToken))
	mac.Write([]byte(CSRFDerivationMessage))
	return base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

// VerifyCSRFToken compares the SHA-256 storage form of a presented token with
// the stored sessions.csrf_hash in constant time. The raw values are never
// compared: only hashes are persisted, so only hashes are compared.
//
// An empty stored hash means the credential type has no CSRF token (an API key,
// later); it can never verify. An empty presented token never verifies either.
func VerifyCSRFToken(storedHash, presented string) bool {
	if storedHash == "" || presented == "" {
		return false
	}
	expected := HashToken(presented)
	if len(expected) != len(storedHash) {
		// ConstantTimeCompare returns 0 for unequal lengths anyway; this keeps
		// the intent explicit without leaking anything about either value.
		return false
	}
	return subtle.ConstantTimeCompare([]byte(expected), []byte(storedHash)) == 1
}
