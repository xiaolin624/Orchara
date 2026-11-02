package handlers

import (
	"crypto/sha256"
	"encoding/hex"
	"io"
	"strings"
	"testing"

	"orchara/platform/internal/objectstore"
)

// newFinalizeStore builds a real local object store under the test's temp dir.
func newFinalizeStore(t *testing.T) *objectstore.Store {
	t.Helper()
	store, err := objectstore.Open(t.TempDir())
	if err != nil {
		t.Fatalf("objectstore.Open: %v", err)
	}
	return store
}

// objectBytes reads one stored object back through the store. It is the test's
// independent view of the bytes, not a value any product code reported.
func objectBytes(t *testing.T, store *objectstore.Store, key string) []byte {
	t.Helper()
	reader, _, err := store.Open(key)
	if err != nil {
		t.Fatalf("opening %s: %v", key, err)
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(reader)
	if err != nil {
		t.Fatalf("reading %s: %v", key, err)
	}
	return content
}

func sealedFileNames(t *testing.T, store *objectstore.Store) []string {
	t.Helper()
	objects, err := store.List("sealed/")
	if err != nil {
		t.Fatalf("listing sealed objects: %v", err)
	}
	names := make([]string, 0, len(objects))
	for _, object := range objects {
		names = append(names, object.Key)
	}
	return names
}

func sha256Of(content []byte) string {
	sum := sha256.Sum256(content)
	return hex.EncodeToString(sum[:])
}

// sizePtr builds the "declared size" argument the product worker passes from the
// artifact row; nil means the row has no declared size.
func sizePtr(value int64) *int64 { return &value }

func TestSealAttemptObjectWritesOnlyTheAttemptKey(t *testing.T) {
	store := newFinalizeStore(t)
	content := []byte("finalize payload")
	staging, err := store.CreateStaging(strings.NewReader(string(content)), 0)
	if err != nil {
		t.Fatalf("CreateStaging: %v", err)
	}
	attemptKey := objectstore.AttemptSealedKey(
		"6f1c5f0e-3f8f-4a52-9a4e-0f40f8f0c9b1",
		"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
	)
	code, err := sealAttemptObject(store, staging.Key, attemptKey, sha256Of(content), sizePtr(int64(len(content))))
	if err != nil {
		t.Fatalf("sealAttemptObject: %v", err)
	}
	if code != "" {
		t.Fatalf("sealAttemptObject failed with %q, want success", code)
	}
	if got := objectBytes(t, store, attemptKey); string(got) != string(content) {
		t.Fatalf("attempt object = %q", got)
	}
	// Only the attempt key exists: the staging key was the source, and the
	// handler never invents a second sealed object.
	if names := sealedFileNames(t, store); len(names) != 1 || names[0] != attemptKey {
		t.Fatalf("sealed objects = %v, want only the attempt key", names)
	}
}

func TestSealAttemptObjectDeletesOnlyItsOwnBytesOnHashMismatch(t *testing.T) {
	store := newFinalizeStore(t)
	// The "active" object is what an earlier, newer activation sealed at the
	// artifact's own key. A stale attempt must never touch it.
	activeKey, err := objectstore.SealedKey()
	if err != nil {
		t.Fatalf("SealedKey: %v", err)
	}
	activeBytes := []byte("bytes sealed by the newer activation")
	if _, err := store.PutSealed(activeKey, strings.NewReader(string(activeBytes)), 0); err != nil {
		t.Fatalf("PutSealed active: %v", err)
	}

	content := []byte("stale bytes from the paused attempt")
	staging, err := store.CreateStaging(strings.NewReader(string(content)), 0)
	if err != nil {
		t.Fatalf("CreateStaging: %v", err)
	}
	attemptKey := objectstore.AttemptSealedKey(
		"6f1c5f0e-3f8f-4a52-9a4e-0f40f8f0c9b1",
		"11111111-2222-4333-8444-555555555555",
	)
	// The row declares a different hash, so this attempt must fail.
	code, err := sealAttemptObject(
		store, staging.Key, attemptKey,
		sha256Of([]byte("what the uploader claimed")), sizePtr(int64(len(content))),
	)
	if err != nil {
		t.Fatalf("sealAttemptObject: %v", err)
	}
	if code != "artifact_hash_mismatch" {
		t.Fatalf("failure code = %q, want artifact_hash_mismatch", code)
	}
	if _, err := store.Hash(attemptKey); err == nil {
		t.Fatal("a failed attempt left its own object behind")
	}
	if got := objectBytes(t, store, activeKey); string(got) != string(activeBytes) {
		t.Fatalf("the active object was changed by a failed attempt: %q", got)
	}
	if names := sealedFileNames(t, store); len(names) != 1 || names[0] != activeKey {
		t.Fatalf("sealed objects = %v, want only the active key", names)
	}
}

func TestSealAttemptObjectRefusesASizeMismatchWithoutTouchingTheActiveKey(t *testing.T) {
	store := newFinalizeStore(t)
	activeKey, err := objectstore.SealedKey()
	if err != nil {
		t.Fatalf("SealedKey: %v", err)
	}
	if _, err := store.PutSealed(activeKey, strings.NewReader("active"), 0); err != nil {
		t.Fatalf("PutSealed: %v", err)
	}
	content := []byte("declared size is wrong")
	staging, err := store.CreateStaging(strings.NewReader(string(content)), 0)
	if err != nil {
		t.Fatalf("CreateStaging: %v", err)
	}
	attemptKey := objectstore.AttemptSealedKey(
		"6f1c5f0e-3f8f-4a52-9a4e-0f40f8f0c9b1",
		"22222222-3333-4444-8555-666666666666",
	)
	code, err := sealAttemptObject(store, staging.Key, attemptKey, sha256Of(content), sizePtr(int64(len(content))+7))
	if err != nil {
		t.Fatalf("sealAttemptObject: %v", err)
	}
	if code != "artifact_size_mismatch" {
		t.Fatalf("failure code = %q, want artifact_size_mismatch", code)
	}
	if got := objectBytes(t, store, activeKey); string(got) != "active" {
		t.Fatalf("the active object was changed: %q", got)
	}
}

func TestSealAttemptObjectReportsAMissingStagingObject(t *testing.T) {
	store := newFinalizeStore(t)
	attemptKey := objectstore.AttemptSealedKey(
		"6f1c5f0e-3f8f-4a52-9a4e-0f40f8f0c9b1",
		"33333333-4444-4555-8666-777777777777",
	)
	code, err := sealAttemptObject(store, "staging/"+strings.Repeat("a", 64), attemptKey, sha256Of(nil), sizePtr(0))
	if err != nil {
		t.Fatalf("sealAttemptObject: %v", err)
	}
	if code != "artifact_staging_missing" {
		t.Fatalf("failure code = %q, want artifact_staging_missing", code)
	}
	if names := sealedFileNames(t, store); len(names) != 0 {
		t.Fatalf("a missing staging object created sealed bytes: %v", names)
	}
}
