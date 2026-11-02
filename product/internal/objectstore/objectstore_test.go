package objectstore

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func newStore(t *testing.T) *Store {
	t.Helper()
	store, err := Open(t.TempDir())
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	return store
}

func TestOpenRejectsAnEmptyRoot(t *testing.T) {
	if _, err := Open(""); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("expected ErrNotConfigured, got %v", err)
	}
	if _, err := Open("   "); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("expected ErrNotConfigured for blank root, got %v", err)
	}
}

func TestANilStoreFailsClosed(t *testing.T) {
	var store *Store
	if _, err := store.CreateStaging(strings.NewReader("x"), 0); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("CreateStaging: expected ErrNotConfigured, got %v", err)
	}
	if _, _, err := store.Open("staging/" + strings.Repeat("a", 64)); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("Open: expected ErrNotConfigured, got %v", err)
	}
	if err := store.Delete("staging/" + strings.Repeat("a", 64)); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("Delete: expected ErrNotConfigured, got %v", err)
	}
}

func TestCreateStagingWritesTheBytesAndTheirHash(t *testing.T) {
	store := newStore(t)
	payload := []byte("hello finalize")
	object, err := store.CreateStaging(bytes.NewReader(payload), 0)
	if err != nil {
		t.Fatalf("CreateStaging: %v", err)
	}
	if !strings.HasPrefix(object.Key, "staging/") {
		t.Fatalf("key %q is not a staging key", object.Key)
	}
	if len(object.Key) != len("staging/")+64 {
		t.Fatalf("key %q is not a random 32-byte name", object.Key)
	}
	if object.Size != int64(len(payload)) {
		t.Fatalf("size = %d, want %d", object.Size, len(payload))
	}
	sum := sha256.Sum256(payload)
	if object.SHA256 != hex.EncodeToString(sum[:]) {
		t.Fatalf("sha256 = %q, want %q", object.SHA256, hex.EncodeToString(sum[:]))
	}

	reader, size, err := store.Open(object.Key)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer reader.Close()
	if size != int64(len(payload)) {
		t.Fatalf("stored size = %d", size)
	}
}

func TestCreateSealedUsesTheSealedPrefixAndNeverRepeatsAKey(t *testing.T) {
	store := newStore(t)
	first, err := store.CreateSealed(strings.NewReader("one"), 0)
	if err != nil {
		t.Fatalf("CreateSealed: %v", err)
	}
	second, err := store.CreateSealed(strings.NewReader("two"), 0)
	if err != nil {
		t.Fatalf("CreateSealed: %v", err)
	}
	if !strings.HasPrefix(first.Key, "sealed/") {
		t.Fatalf("key %q is not a sealed key", first.Key)
	}
	if first.Key == second.Key {
		t.Fatalf("two sealed writes reused the key %q", first.Key)
	}
}

func TestTheLimitIsEnforcedWithoutLeavingAPartialObject(t *testing.T) {
	store := newStore(t)
	_, err := store.CreateStaging(strings.NewReader("0123456789"), 4)
	if !errors.Is(err, ErrTooLarge) {
		t.Fatalf("expected ErrTooLarge, got %v", err)
	}
	entries, err := os.ReadDir(store.stagingDir)
	if err != nil {
		t.Fatalf("reading staging dir: %v", err)
	}
	if len(entries) != 0 {
		t.Fatalf("an over-limit write left %d object(s) behind", len(entries))
	}
}

func TestTheLimitCannotExceedTheStoreCeiling(t *testing.T) {
	// A caller may narrow the cap but never widen it beyond MaxObjectBytes.
	if got := effectiveLimit(MaxObjectBytes + 1); got != MaxObjectBytes {
		t.Fatalf("effectiveLimit above the ceiling = %d", got)
	}
	if got := effectiveLimit(1024); got != 1024 {
		t.Fatalf("effectiveLimit(1024) = %d", got)
	}
	if got := effectiveLimit(0); got != MaxObjectBytes {
		t.Fatalf("effectiveLimit(0) = %d", got)
	}
}

func TestHashReReadsTheStoredBytes(t *testing.T) {
	store := newStore(t)
	object, err := store.CreateSealed(strings.NewReader("sealed bytes"), 0)
	if err != nil {
		t.Fatalf("CreateSealed: %v", err)
	}
	again, err := store.Hash(object.Key)
	if err != nil {
		t.Fatalf("Hash: %v", err)
	}
	if again.SHA256 != object.SHA256 || again.Size != object.Size {
		t.Fatalf("Hash = %+v, want %+v", again, object)
	}

	// Overwriting the bytes changes the hash: the guard is reading the file, not
	// replaying a stored field.
	path := filepath.Join(store.sealedDir, strings.TrimPrefix(object.Key, "sealed/"))
	if err := os.WriteFile(path, []byte("tampered"), 0o600); err != nil {
		t.Fatalf("tampering: %v", err)
	}
	tampered, err := store.Hash(object.Key)
	if err != nil {
		t.Fatalf("Hash after tamper: %v", err)
	}
	if tampered.SHA256 == object.SHA256 {
		t.Fatal("Hash returned the pre-tamper digest")
	}
}

func TestKeysCannotEscapeTheStore(t *testing.T) {
	store := newStore(t)
	for _, key := range []string{
		"",
		"staging/../sealed",
		"sealed/../../etc/passwd",
		"staging/" + strings.Repeat("A", 64), // uppercase is not our hex form
		"staging/short",
		"other/" + strings.Repeat("a", 64),
		"sealed/" + strings.Repeat("a", 63) + "/x",
	} {
		if _, err := store.Stat(key); !errors.Is(err, ErrInvalidKey) {
			t.Fatalf("Stat(%q): expected ErrInvalidKey, got %v", key, err)
		}
	}
}

func TestDeleteIsIdempotent(t *testing.T) {
	store := newStore(t)
	object, err := store.CreateStaging(strings.NewReader("gone"), 0)
	if err != nil {
		t.Fatalf("CreateStaging: %v", err)
	}
	if err := store.Delete(object.Key); err != nil {
		t.Fatalf("first Delete: %v", err)
	}
	if err := store.Delete(object.Key); err != nil {
		t.Fatalf("second Delete: %v", err)
	}
	if _, err := store.Hash(object.Key); !errors.Is(err, ErrNotExist) {
		t.Fatalf("Hash after delete: expected ErrNotExist, got %v", err)
	}
}

func TestAttemptSealedKeyIsStableFencedAndASealedKey(t *testing.T) {
	job := "6f1c5f0e-3f8f-4a52-9a4e-0f40f8f0c9b1"
	first := "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
	second := "11111111-2222-4333-8444-555555555555"

	key := AttemptSealedKey(job, first)
	if !strings.HasPrefix(key, "sealed/") || len(key) != len("sealed/")+64 {
		t.Fatalf("attempt key %q is not a sealed key with a 64-hex name", key)
	}
	// The exact formula is a cross-language contract: the Python test helpers
	// derive the same key to seed a worker's half. Pinning the digest here makes
	// an accidental domain-string change a test failure, not a silent divergence.
	const want = "sealed/07ada8bbc08e558d5943f84398c0c4d36143cde4c027db304a78328ac52c1e24"
	if key != want {
		t.Fatalf("AttemptSealedKey(%q, %q) = %q, want %q", job, first, key, want)
	}
	if AttemptSealedKey(job, second) == key {
		t.Fatal("two attempt tokens must derive different keys")
	}
	if AttemptSealedKey("22222222-2222-4222-8222-222222222222", first) == key {
		t.Fatal("two jobs must derive different keys")
	}
	if AttemptSealedKey(strings.ToUpper(job), strings.ToUpper(first)) != key {
		t.Fatal("the derivation must be case-insensitive on UUIDs")
	}
	if !validKey(key) {
		t.Fatalf("the store would refuse its own attempt key %q", key)
	}
}

func TestMoveSealedIsAtomicAndRefusesNonSealedKeys(t *testing.T) {
	store := newStore(t)
	source, err := SealedKey()
	if err != nil {
		t.Fatalf("SealedKey: %v", err)
	}
	if _, err := store.PutSealed(source, strings.NewReader("attempt bytes"), 0); err != nil {
		t.Fatalf("PutSealed: %v", err)
	}
	destination, err := SealedKey()
	if err != nil {
		t.Fatalf("SealedKey: %v", err)
	}
	if _, err := store.PutSealed(destination, strings.NewReader("old bytes"), 0); err != nil {
		t.Fatalf("PutSealed destination: %v", err)
	}
	if err := store.Move(source, destination); err != nil {
		t.Fatalf("Move: %v", err)
	}
	if _, err := store.Hash(source); !errors.Is(err, ErrNotExist) {
		t.Fatalf("the source key must be gone after Move, got %v", err)
	}
	moved, err := store.Hash(destination)
	if err != nil {
		t.Fatalf("Hash destination: %v", err)
	}
	if moved.SHA256 != sha256Hex("attempt bytes") {
		t.Fatalf("the destination bytes are not the moved bytes: %+v", moved)
	}
	// Missing source: the caller must be able to tell "nothing was written".
	missing, err := SealedKey()
	if err != nil {
		t.Fatalf("SealedKey: %v", err)
	}
	if err := store.Move(missing, destination); !errors.Is(err, ErrNotExist) {
		t.Fatalf("moving a missing object: expected ErrNotExist, got %v", err)
	}
	// Only sealed -> sealed is a platform move: a staging key is never a sealed
	// destination and vice versa.
	staging, err := StagingKey()
	if err != nil {
		t.Fatalf("StagingKey: %v", err)
	}
	if _, err := store.CreateStaging(strings.NewReader("x"), 0); err != nil {
		t.Fatalf("CreateStaging: %v", err)
	}
	listed, err := store.List("staging/")
	if err != nil || len(listed) != 1 {
		t.Fatalf("staging listing = %v, %v", listed, err)
	}
	if err := store.Move(listed[0].Key, destination); !errors.Is(err, ErrInvalidKey) {
		t.Fatalf("a staging source must be refused, got %v", err)
	}
	if err := store.Move(destination, staging); !errors.Is(err, ErrInvalidKey) {
		t.Fatalf("a staging destination must be refused, got %v", err)
	}
	// An empty store root is not configured; failure is explicit.
	var nilStore *Store
	if err := nilStore.Move(source, destination); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("a nil store must fail closed, got %v", err)
	}
}

func sha256Hex(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])
}

func TestPutSealedFillsAPreallocatedKeyAndCanRewriteIt(t *testing.T) {
	store := newStore(t)
	key, err := SealedKey()
	if err != nil {
		t.Fatalf("SealedKey: %v", err)
	}
	first, err := store.PutSealed(key, strings.NewReader("first"), 0)
	if err != nil {
		t.Fatalf("PutSealed: %v", err)
	}
	if first.Key != key {
		t.Fatalf("PutSealed wrote %q instead of the preallocated %q", first.Key, key)
	}
	// A finalize retry rewrites the same sealed key after the lease expired; the
	// replacement must be whole, not appended or left as two objects.
	if _, err := store.PutSealed(key, strings.NewReader("second version"), 0); err != nil {
		t.Fatalf("second PutSealed: %v", err)
	}
	reader, size, err := store.Open(key)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer reader.Close()
	body := make([]byte, size)
	if _, err := io.ReadFull(reader, body); err != nil {
		t.Fatalf("reading the rewritten object: %v", err)
	}
	if string(body) != "second version" {
		t.Fatalf("rewritten object = %q", body)
	}

	// A staging key is refused: staging is a one-shot upload target, never a
	// sealed destination.
	staging, err := StagingKey()
	if err != nil {
		t.Fatalf("StagingKey: %v", err)
	}
	if _, err := store.PutSealed(staging, strings.NewReader("x"), 0); !errors.Is(err, ErrInvalidKey) {
		t.Fatalf("expected ErrInvalidKey for a staging target, got %v", err)
	}
}
