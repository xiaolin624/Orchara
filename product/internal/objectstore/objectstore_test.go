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
