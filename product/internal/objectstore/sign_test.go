package objectstore

import (
	"errors"
	"strings"
	"testing"
	"time"
)

func newTestSigner(t *testing.T, ttl time.Duration) *Signer {
	t.Helper()
	signer, err := NewSigner(strings.Repeat("k", MinSigningKeyBytes), ttl)
	if err != nil {
		t.Fatalf("NewSigner: %v", err)
	}
	return signer
}

func testKey(prefix string) string { return prefix + strings.Repeat("a", keyNameLength) }

func testArtifactID() string { return "11111111-2222-4333-8444-555555555555" }

func TestAnEmptySigningKeyDisablesLinksWithoutFailingStartup(t *testing.T) {
	signer, err := NewSigner("  ", time.Minute)
	if err != nil {
		t.Fatalf("NewSigner(empty): %v", err)
	}
	if signer != nil {
		t.Fatalf("empty key must produce a nil signer, got %#v", signer)
	}
	if _, _, err := signer.Sign(OpGet, testKey(sealedPrefix), testArtifactID(), 10); !errors.Is(err, ErrSignerNotConfigured) {
		t.Fatalf("nil signer Sign: %v", err)
	}
	if _, err := signer.Verify("x.y", OpGet); !errors.Is(err, ErrSignerNotConfigured) {
		t.Fatalf("nil signer Verify: %v", err)
	}
}

func TestAConfiguredSigningKeyMustBeLongEnough(t *testing.T) {
	if _, err := NewSigner(strings.Repeat("k", MinSigningKeyBytes-1), time.Minute); err == nil {
		t.Fatal("a short signing key must be refused")
	}
}

func TestMintAndVerifyRoundTrip(t *testing.T) {
	signer := newTestSigner(t, time.Minute)
	key := testKey(stagingPrefix)
	token, expires, err := signer.Sign(OpPut, key, testArtifactID(), 1024)
	if err != nil {
		t.Fatalf("Sign: %v", err)
	}
	if !expires.After(time.Now()) {
		t.Fatalf("expiry is not in the future: %v", expires)
	}
	for _, char := range token {
		if !strings.ContainsRune("-_0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.", char) {
			t.Fatalf("token is not URL-safe: %q", token)
		}
	}
	claims, err := signer.Verify(token, OpPut)
	if err != nil {
		t.Fatalf("Verify: %v", err)
	}
	if claims.Key != key || claims.ArtifactID != testArtifactID() || claims.MaxBytes != 1024 || claims.Op != OpPut {
		t.Fatalf("claims round-tripped wrong: %#v", claims)
	}
	if claims.ExpiresAt.Unix() != expires.Unix() {
		t.Fatalf("expiry drift: %v vs %v", claims.ExpiresAt, expires)
	}
}

func TestASealedPutIsNeverMinted(t *testing.T) {
	signer := newTestSigner(t, time.Minute)
	if _, _, err := signer.Sign(OpPut, testKey(sealedPrefix), testArtifactID(), 10); !errors.Is(err, ErrLinkInvalid) {
		t.Fatalf("a sealed PUT must be refused, got %v", err)
	}
}

func TestAGetMustBeSealedAndAPutMustBeStaging(t *testing.T) {
	signer := newTestSigner(t, time.Minute)
	if _, _, err := signer.Sign(OpGet, testKey(stagingPrefix), testArtifactID(), 0); !errors.Is(err, ErrLinkInvalid) {
		t.Fatalf("a staging GET must be refused, got %v", err)
	}
	if _, _, err := signer.Sign(OpPut, testKey(sealedPrefix), testArtifactID(), 0); !errors.Is(err, ErrLinkInvalid) {
		t.Fatalf("a sealed PUT must be refused, got %v", err)
	}
}

func TestVerifyRefusesEveryBadLink(t *testing.T) {
	signer := newTestSigner(t, time.Minute)
	putToken, _, err := signer.Sign(OpPut, testKey(stagingPrefix), testArtifactID(), 10)
	if err != nil {
		t.Fatalf("Sign: %v", err)
	}
	getToken, _, err := signer.Sign(OpGet, testKey(sealedPrefix), testArtifactID(), 10)
	if err != nil {
		t.Fatalf("Sign: %v", err)
	}
	cases := map[string]struct {
		token string
		want  Op
	}{
		"swapped op (GET token used as PUT)": {getToken, OpPut},
		"swapped op (PUT token used as GET)": {putToken, OpGet},
		"tampered signature":                 {flipLast(putToken), OpPut},
		"truncated":                          {putToken[:len(putToken)-2], OpPut},
		"empty":                              {"", OpPut},
		"junk":                               {"not-a-token", OpPut},
	}
	for name, testCase := range cases {
		if _, err := signer.Verify(testCase.token, testCase.want); !errors.Is(err, ErrLinkInvalid) {
			t.Errorf("%s: expected ErrLinkInvalid, got %v", name, err)
		}
	}
}

// flipLast changes the final signature character so the payload stays intact
// and only the MAC no longer matches.
func flipLast(token string) string {
	last := token[len(token)-1]
	replacement := byte('A')
	if last == 'A' {
		replacement = 'B'
	}
	return token[:len(token)-1] + string(replacement)
}

func TestVerifyRefusesAnotherSigningKeyAndAnExpiredLink(t *testing.T) {
	first := newTestSigner(t, time.Minute)
	second, err := NewSigner(strings.Repeat("z", MinSigningKeyBytes), time.Minute)
	if err != nil {
		t.Fatalf("NewSigner: %v", err)
	}
	token, _, err := first.Sign(OpGet, testKey(sealedPrefix), testArtifactID(), 10)
	if err != nil {
		t.Fatalf("Sign: %v", err)
	}
	if _, err := second.Verify(token, OpGet); !errors.Is(err, ErrLinkInvalid) {
		t.Fatalf("a token signed with another key must be refused, got %v", err)
	}

	now := time.Now()
	expiring := newTestSigner(t, time.Minute)
	expiring.now = func() time.Time { return now }
	expired, _, err := expiring.Sign(OpGet, testKey(sealedPrefix), testArtifactID(), 10)
	if err != nil {
		t.Fatalf("Sign expired: %v", err)
	}
	now = now.Add(2 * time.Minute)
	if _, err := expiring.Verify(expired, OpGet); !errors.Is(err, ErrLinkInvalid) {
		t.Fatalf("an expired link must be refused, got %v", err)
	}
}

func TestSignRefusesAnUnknownOperationAndBadInput(t *testing.T) {
	signer := newTestSigner(t, time.Minute)
	if _, _, err := signer.Sign(Op("delete"), testKey(stagingPrefix), testArtifactID(), 10); !errors.Is(err, ErrLinkInvalid) {
		t.Fatalf("unknown op: %v", err)
	}
	if _, _, err := signer.Sign(OpPut, testKey(stagingPrefix), "not-a-uuid", 10); !errors.Is(err, ErrLinkInvalid) {
		t.Fatalf("bad artifact id: %v", err)
	}
	if _, _, err := signer.Sign(OpPut, testKey(stagingPrefix), testArtifactID(), MaxObjectBytes+1); !errors.Is(err, ErrLinkInvalid) {
		t.Fatalf("too-large max bytes: %v", err)
	}
	if _, _, err := signer.Sign(OpPut, "staging/short", testArtifactID(), 10); err == nil {
		t.Fatal("a malformed key must be refused")
	}
}
