package identity

import (
	"strings"
	"testing"
)

// The expected PHC strings were produced with golang.org/x/crypto/argon2
// directly (not through HashPassword), so these tests pin the wire format to an
// independent source rather than recomputing it with the code under test.
const (
	knownPassword = "correct horse battery staple"
	knownPHC      = "$argon2id$v=19$m=65536,t=3,p=2$dDAxNS1zYWx0LTAxMjM0NQ$qNNes8JR8BsZYmPRS2ksMnb2n2GhMAIM+IoI20oPapU"
	weakerPHC     = "$argon2id$v=19$m=32768,t=3,p=2$dDAxNS1zYWx0LTAxMjM0NQ$7aXD/XAjPwBTjmsHyEYgQCqax3yA9k691eiSxBvAtzQ"
)

func TestVerifyAcceptsAnIndependentlyGeneratedHash(t *testing.T) {
	ok, needsRehash := VerifyPassword(knownPHC, knownPassword)
	if !ok {
		t.Fatal("a valid argon2id hash did not verify")
	}
	if needsRehash {
		t.Fatal("the default parameters must not request a rehash")
	}
}

func TestVerifyRejectsAWrongPassword(t *testing.T) {
	if ok, _ := VerifyPassword(knownPHC, knownPassword+"x"); ok {
		t.Fatal("a wrong password verified")
	}
}

func TestHashRoundTripsAndUsesTheFixedParameters(t *testing.T) {
	hash, err := HashPassword(knownPassword)
	if err != nil {
		t.Fatalf("HashPassword: %v", err)
	}
	if !strings.HasPrefix(hash, "$argon2id$v=19$m=65536,t=3,p=2$") {
		t.Fatalf("unexpected PHC parameters: %s", hash)
	}
	ok, needsRehash := VerifyPassword(hash, knownPassword)
	if !ok || needsRehash {
		t.Fatalf("round trip failed (ok=%v needsRehash=%v)", ok, needsRehash)
	}
}

func TestHashUsesARandomSalt(t *testing.T) {
	first, err := HashPassword(knownPassword)
	if err != nil {
		t.Fatal(err)
	}
	second, err := HashPassword(knownPassword)
	if err != nil {
		t.Fatal(err)
	}
	if first == second {
		t.Fatal("two hashes of the same password are identical; the salt is not random")
	}
	for _, hash := range []string{first, second} {
		if ok, _ := VerifyPassword(hash, knownPassword); !ok {
			t.Fatalf("a freshly generated hash does not verify: %s", hash)
		}
	}
}

func TestWeakerStoredParametersRequestARehash(t *testing.T) {
	ok, needsRehash := VerifyPassword(weakerPHC, knownPassword)
	if !ok {
		t.Fatal("a valid hash with lower memory did not verify")
	}
	if !needsRehash {
		t.Fatal("a hash with non-default parameters must request a rehash")
	}
}

func TestPendingActivationNeverVerifies(t *testing.T) {
	if ok, _ := VerifyPassword(PendingActivation, PendingActivation); ok {
		t.Fatal("the pending-activation sentinel verified against itself")
	}
	if ok, _ := VerifyPassword(PendingActivation, "anything"); ok {
		t.Fatal("the pending-activation sentinel verified")
	}
}

func TestMalformedHashesNeverVerify(t *testing.T) {
	cases := map[string]string{
		"empty":            "",
		"plaintext":        "hunter2",
		"other algorithm":  "$argon2i$v=19$m=65536,t=3,p=2$c2FsdHNhbHQ$aGFzaA",
		"unknown version":  "$argon2id$v=16$m=65536,t=3,p=2$c2FsdHNhbHQ$aGFzaA",
		"missing params":   "$argon2id$v=19$$c2FsdHNhbHQ$aGFzaA",
		"bad base64 salt":  "$argon2id$v=19$m=65536,t=3,p=2$***$qNNes8JR8BsZYmPRS2ksMnb2n2GhMAIM+IoI20oPapU",
		"truncated":        "$argon2id$v=19$m=65536,t=3,p=2$c2FsdHNhbHQ",
		"absurd memory":    "$argon2id$v=19$m=4294967295,t=3,p=2$c2FsdHNhbHQ$aGFzaA",
		"zero iterations":  "$argon2id$v=19$m=65536,t=0,p=2$c2FsdHNhbHQ$aGFzaA",
		"memory below 8*p": "$argon2id$v=19$m=8,t=3,p=8$c2FsdHNhbHQ$aGFzaA",
		"short key":        "$argon2id$v=19$m=65536,t=3,p=2$c2FsdHNhbHQ$YQ",
	}
	for name, encoded := range cases {
		t.Run(name, func(t *testing.T) {
			defer func() {
				if recovered := recover(); recovered != nil {
					t.Fatalf("VerifyPassword panicked on malformed input: %v", recovered)
				}
			}()
			if ok, needsRehash := VerifyPassword(encoded, "password"); ok || needsRehash {
				t.Fatalf("malformed hash verified (ok=%v needsRehash=%v)", ok, needsRehash)
			}
		})
	}
}
