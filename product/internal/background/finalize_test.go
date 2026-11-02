package background

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"

	"orchara/platform/internal/objectstore"
)

// These are unit tests of the object-store half of the finalize guard, on the
// real local store. They prove the fencing property the integration tests cannot
// pause a real handler to observe: an attempt can only ever write, verify and
// remove the object named by its own (job, attempt token) key, and only the guard
// can move verified bytes to the artifact's active key.

func newFinalizeStore(t *testing.T) *objectstore.Store {
	t.Helper()
	store, err := objectstore.Open(t.TempDir())
	if err != nil {
		t.Fatalf("objectstore.Open: %v", err)
	}
	return store
}

func testSHA256(content []byte) string {
	sum := sha256.Sum256(content)
	return hex.EncodeToString(sum[:])
}

func declaredSize(value int64) *int64 { return &value }

func sealedKeys(t *testing.T, store *objectstore.Store) []string {
	t.Helper()
	objects, err := store.List("sealed/")
	if err != nil {
		t.Fatalf("listing sealed objects: %v", err)
	}
	keys := make([]string, 0, len(objects))
	for _, object := range objects {
		keys = append(keys, object.Key)
	}
	return keys
}

func TestASuccessfulCompletionMovesTheAttemptObjectAndLeavesNoAttemptKey(t *testing.T) {
	store := newFinalizeStore(t)
	attemptKey := objectstore.AttemptSealedKey(
		"6f1c5f0e-3f8f-4a52-9a4e-0f40f8f0c9b1", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
	)
	activeKey, err := objectstore.SealedKey()
	if err != nil {
		t.Fatalf("SealedKey: %v", err)
	}
	content := []byte("the verified attempt bytes")
	if _, err := store.PutSealed(attemptKey, strings.NewReader(string(content)), 0); err != nil {
		t.Fatalf("PutSealed: %v", err)
	}

	object, code, err := promoteAttemptObject(
		store, attemptKey, activeKey, testSHA256(content), declaredSize(int64(len(content))),
	)
	if err != nil {
		t.Fatalf("promoteAttemptObject: %v", err)
	}
	if code != "" {
		t.Fatalf("promotion failed with %q, want success", code)
	}
	if object.SHA256 != testSHA256(content) {
		t.Fatalf("verified hash = %q", object.SHA256)
	}
	if keys := sealedKeys(t, store); len(keys) != 1 || keys[0] != activeKey {
		t.Fatalf("sealed objects = %v, want only the active key", keys)
	}
	if _, err := store.Hash(attemptKey); err == nil {
		t.Fatal("the attempt key still exists after a successful move")
	}
}

func TestARejectedAttemptLosesOnlyItsOwnObjectAndNeverTheActiveBytes(t *testing.T) {
	store := newFinalizeStore(t)
	activeKey, err := objectstore.SealedKey()
	if err != nil {
		t.Fatalf("SealedKey: %v", err)
	}
	activeBytes := []byte("bytes the newer activation already sealed")
	if _, err := store.PutSealed(activeKey, strings.NewReader(string(activeBytes)), 0); err != nil {
		t.Fatalf("PutSealed active: %v", err)
	}

	attemptKey := objectstore.AttemptSealedKey(
		"6f1c5f0e-3f8f-4a52-9a4e-0f40f8f0c9b1", "11111111-2222-4333-8444-555555555555",
	)
	if _, err := store.PutSealed(attemptKey, strings.NewReader("stale attempt bytes"), 0); err != nil {
		t.Fatalf("PutSealed attempt: %v", err)
	}
	// The artifact row declares a different hash: the guard must reject and
	// remove only the attempt object.
	_, code, err := promoteAttemptObject(
		store, attemptKey, activeKey, testSHA256([]byte("what the uploader claimed")), nil,
	)
	if err != nil {
		t.Fatalf("promoteAttemptObject: %v", err)
	}
	if code != CodeArtifactHashMismatch {
		t.Fatalf("failure code = %q, want %q", code, CodeArtifactHashMismatch)
	}
	if _, err := store.Hash(attemptKey); err == nil {
		t.Fatal("the rejected attempt object was left behind")
	}
	moved, err := store.Hash(activeKey)
	if err != nil {
		t.Fatalf("Hash active: %v", err)
	}
	if moved.SHA256 != testSHA256(activeBytes) || moved.Size != int64(len(activeBytes)) {
		t.Fatalf("the active object changed: %+v", moved)
	}
	if keys := sealedKeys(t, store); len(keys) != 1 || keys[0] != activeKey {
		t.Fatalf("sealed objects = %v, want only the active key", keys)
	}
}

func TestASizeMismatchAndAMissingAttemptObjectAreBusinessRejections(t *testing.T) {
	store := newFinalizeStore(t)
	activeKey, err := objectstore.SealedKey()
	if err != nil {
		t.Fatalf("SealedKey: %v", err)
	}
	if _, err := store.PutSealed(activeKey, strings.NewReader("active"), 0); err != nil {
		t.Fatalf("PutSealed: %v", err)
	}
	attemptKey := objectstore.AttemptSealedKey(
		"6f1c5f0e-3f8f-4a52-9a4e-0f40f8f0c9b1", "22222222-3333-4444-8555-666666666666",
	)
	content := []byte("size is declared wrong")
	if _, err := store.PutSealed(attemptKey, strings.NewReader(string(content)), 0); err != nil {
		t.Fatalf("PutSealed: %v", err)
	}
	if _, code, err := promoteAttemptObject(
		store, attemptKey, activeKey, testSHA256(content), declaredSize(int64(len(content))+1),
	); err != nil || code != CodeArtifactSizeMismatch {
		t.Fatalf("size mismatch = %q, %v", code, err)
	}
	if _, err := store.Hash(attemptKey); err == nil {
		t.Fatal("the size-mismatched attempt object was left behind")
	}

	missingKey := objectstore.AttemptSealedKey(
		"6f1c5f0e-3f8f-4a52-9a4e-0f40f8f0c9b1", "33333333-4444-4555-8666-777777777777",
	)
	if _, code, err := promoteAttemptObject(
		store, missingKey, activeKey, testSHA256(content), nil,
	); err != nil || code != CodeArtifactObjectMissing {
		t.Fatalf("missing attempt = %q, %v", code, err)
	}
	if got, err := store.Hash(activeKey); err != nil || got.SHA256 != testSHA256([]byte("active")) {
		t.Fatalf("the active object changed: %+v, %v", got, err)
	}
}

func TestAnAttemptObjectForAnotherTokenIsNotAcceptable(t *testing.T) {
	// The guard derives the key from the token the completion presents. Bytes a
	// previous attempt wrote under its own key are invisible to it: a stale
	// attempt cannot "complete" using an object it does not own, and it cannot
	// overwrite the active key by naming it.
	store := newFinalizeStore(t)
	attemptKey := objectstore.AttemptSealedKey(
		"6f1c5f0e-3f8f-4a52-9a4e-0f40f8f0c9b1", "44444444-5555-4666-8777-888888888888",
	)
	otherKey := objectstore.AttemptSealedKey(
		"6f1c5f0e-3f8f-4a52-9a4e-0f40f8f0c9b1", "99999999-aaaa-4bbb-8ccc-dddddddddddd",
	)
	content := []byte("bytes from another attempt")
	if _, err := store.PutSealed(otherKey, strings.NewReader(string(content)), 0); err != nil {
		t.Fatalf("PutSealed: %v", err)
	}
	activeKey, err := objectstore.SealedKey()
	if err != nil {
		t.Fatalf("SealedKey: %v", err)
	}
	if _, code, err := promoteAttemptObject(
		store, attemptKey, activeKey, testSHA256(content), nil,
	); err != nil || code != CodeArtifactObjectMissing {
		t.Fatalf("promotion with no own object = %q, %v", code, err)
	}
	if _, err := store.Hash(otherKey); err != nil {
		t.Fatalf("another attempt's object was touched: %v", err)
	}
	if _, err := store.Hash(activeKey); err == nil {
		t.Fatal("the active key was created without a verified attempt object")
	}
}
