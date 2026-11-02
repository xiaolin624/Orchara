package identity

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"fmt"

	"orchara/platform/internal/uuid"
)

// TokenBytes is the entropy of every opaque token (activation, session, CSRF).
const TokenBytes = 32

// NewToken returns a fresh opaque token: 32 random bytes, base64url without
// padding. Only the caller that just created it sees this value.
func NewToken() string {
	buf := make([]byte, TokenBytes)
	if _, err := rand.Read(buf); err != nil {
		// crypto/rand failure means the process can no longer issue safe
		// credentials; continuing would silently weaken them.
		panic(fmt.Sprintf("identity: system random source failed: %v", err))
	}
	return base64.RawURLEncoding.EncodeToString(buf)
}

// HashToken is the storage form of a token: lowercase SHA-256 hex.
func HashToken(raw string) string {
	sum := sha256.Sum256([]byte(raw))
	return hex.EncodeToString(sum[:])
}

// NewUUID returns a random RFC 4122 version 4 UUID string.
//
// The platform generates its own identifiers (the contract says "UUID by the
// server"), so no value from a client or the database is trusted as one.
func NewUUID() string {
	return uuid.NewString()
}
