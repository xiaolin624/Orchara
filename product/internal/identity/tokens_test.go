package identity

import (
	"encoding/base64"
	"regexp"
	"testing"
)

func TestNewTokenIs32RandomBytes(t *testing.T) {
	seen := make(map[string]bool, 200)
	for range 200 {
		token := NewToken()
		raw, err := base64.RawURLEncoding.DecodeString(token)
		if err != nil {
			t.Fatalf("token is not base64url: %v", err)
		}
		if len(raw) != TokenBytes {
			t.Fatalf("token carries %d bytes, want %d", len(raw), TokenBytes)
		}
		if seen[token] {
			t.Fatal("NewToken repeated a value")
		}
		seen[token] = true
	}
}

func TestHashTokenIsSHA256Hex(t *testing.T) {
	// Independently known SHA-256 of "abc".
	const sha256OfABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
	if got := HashToken("abc"); got != sha256OfABC {
		t.Fatalf("HashToken(\"abc\") = %s, want %s", got, sha256OfABC)
	}
	if HashToken("a") == HashToken("b") {
		t.Fatal("different tokens hashed to the same value")
	}
}

var uuidV4Pattern = regexp.MustCompile(
	`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`,
)

func TestNewUUIDIsVersion4(t *testing.T) {
	seen := make(map[string]bool, 100)
	for range 100 {
		id := NewUUID()
		if !uuidV4Pattern.MatchString(id) {
			t.Fatalf("not a version-4 UUID: %s", id)
		}
		if seen[id] {
			t.Fatal("NewUUID repeated a value")
		}
		seen[id] = true
	}
}
