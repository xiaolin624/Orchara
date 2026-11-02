package compiler

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"orchara/platform/internal/jcs"
	"orchara/platform/internal/objectstore"
)

func sealedManifest(t *testing.T, payload []byte) (*objectstore.Store, ArtifactRef, objectstore.Object) {
	t.Helper()
	store, err := objectstore.Open(t.TempDir())
	if err != nil {
		t.Fatalf("open the object store: %v", err)
	}
	object, err := store.CreateSealed(bytes.NewReader(payload), 0)
	if err != nil {
		t.Fatalf("write the sealed manifest: %v", err)
	}
	return store, ArtifactRef{ObjectKey: object.Key, SHA256: object.SHA256, SizeBytes: object.Size}, object
}

func goldenJCS(t *testing.T) ([]byte, string) {
	t.Helper()
	payload, err := os.ReadFile(filepath.Join(snapshotsRoot, "sales", "manifest.jcs"))
	if err != nil {
		t.Fatalf("read the golden JCS bytes: %v", err)
	}
	shaText, err := os.ReadFile(filepath.Join(snapshotsRoot, "sales", "manifest.sha256"))
	if err != nil {
		t.Fatalf("read the golden digest: %v", err)
	}
	return payload, strings.TrimSpace(string(shaText))
}

func TestVerifyManifestBytesAcceptsTheGoldenSnapshot(t *testing.T) {
	payload, recorded := goldenJCS(t)
	store, artifact, object := sealedManifest(t, payload)
	if object.SHA256 != recorded {
		t.Fatalf("sealed bytes hash to %s, snapshot says %s", object.SHA256, recorded)
	}
	manifest, verified, err := VerifyManifestBytes(store, artifact)
	if err != nil {
		t.Fatalf("VerifyManifestBytes: %v", err)
	}
	if !bytes.Equal(verified, payload) {
		t.Fatal("verified bytes differ from the sealed bytes")
	}
	if manifest.ManifestVersion != ManifestVersion || manifest.SourceRevision != 1 {
		t.Fatalf("decoded manifest = %+v", manifest)
	}
	if manifest.RuntimeLockSHA256 == "" || manifest.WorkerImageDigest == "" {
		t.Fatalf("decoded manifest lost its deployment identity: %+v", manifest)
	}
}

func TestVerifyManifestBytesRejectsTamperedBytesBeforeParsing(t *testing.T) {
	payload, recorded := goldenJCS(t)
	tampered := append([]byte(nil), payload...)
	// Flip the version digit inside the `"manifest_version":"1.0"` value. The
	// bytes stay valid JSON but no longer hash to the recorded digest.
	const marker = `"manifest_version":"1.0"`
	index := bytes.Index(tampered, []byte(marker))
	if index < 0 {
		t.Fatal("the golden manifest has no manifest_version literal")
	}
	tampered[index+len(`"manifest_version":"`)] = '2'

	store, err := objectstore.Open(t.TempDir())
	if err != nil {
		t.Fatalf("open the object store: %v", err)
	}
	object, err := store.CreateSealed(bytes.NewReader(tampered), 0)
	if err != nil {
		t.Fatalf("write the tampered object: %v", err)
	}
	_, _, err = VerifyManifestBytes(store, ArtifactRef{
		ObjectKey: object.Key, SHA256: recorded, SizeBytes: int64(len(tampered)),
	})
	if !errors.Is(err, ErrManifestHashMismatch) {
		t.Fatalf("tampered bytes must fail the hash check, got %v", err)
	}

	// Re-recording the tampered digest gets past the hash check but the content
	// is still checked against the schema, and the changed version is rejected.
	_, _, err = VerifyManifestBytes(store, ArtifactRef{
		ObjectKey: object.Key, SHA256: object.SHA256, SizeBytes: object.Size,
	})
	if !errors.Is(err, ErrManifestInvalid) {
		t.Fatalf("tampered content must fail schema validation, got %v", err)
	}
}

// TestVerifyManifestBytesRejectsASealedObjectTamperedInPlace is the real
// sealed-object tamper flow: the canonical bytes are sealed and verified first,
// then one byte is overwritten inside the store's sealed object (the file the
// finalize guard/producer wrote, not a second object with a different digest).
// VerifyManifestBytes must hash the bytes before parsing and report the typed
// hash mismatch. The Python HTTP test can only assert that a flipped file has a
// different SHA-256; it cannot fail for a verifier bug, so the verifier itself is
// proved here.
func TestVerifyManifestBytesRejectsASealedObjectTamperedInPlace(t *testing.T) {
	payload, recorded := goldenJCS(t)
	root := t.TempDir()
	store, err := objectstore.Open(root)
	if err != nil {
		t.Fatalf("open the object store: %v", err)
	}
	object, err := store.CreateSealed(bytes.NewReader(payload), 0)
	if err != nil {
		t.Fatalf("seal the golden manifest: %v", err)
	}
	if object.SHA256 != recorded {
		t.Fatalf("sealed bytes hash to %s, snapshot says %s", object.SHA256, recorded)
	}
	artifact := ArtifactRef{
		ObjectKey: object.Key, SHA256: recorded, SizeBytes: int64(len(payload)),
	}
	manifest, verified, err := VerifyManifestBytes(store, artifact)
	if err != nil {
		t.Fatalf("the untampered sealed object must verify: %v", err)
	}
	if !bytes.Equal(verified, payload) || manifest.SourceRevision != 1 {
		t.Fatal("the untampered verification did not return the sealed manifest")
	}

	// Overwrite one byte in the object that is already sealed on disk. The key and
	// the recorded digest do not change; only the stored bytes do.
	path := filepath.Join(root, object.Key)
	stored, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read the sealed object: %v", err)
	}
	if !bytes.Equal(stored, payload) {
		t.Fatal("the sealed file does not contain the canonical bytes")
	}
	tampered := append([]byte(nil), stored...)
	tampered[len(tampered)/2] ^= 0xFF
	if len(tampered) != len(stored) {
		t.Fatal("the tamper changed the object size instead of one byte")
	}
	if err := os.WriteFile(path, tampered, 0o600); err != nil {
		t.Fatalf("overwrite the sealed object: %v", err)
	}

	_, _, err = VerifyManifestBytes(store, artifact)
	if !errors.Is(err, ErrManifestHashMismatch) {
		t.Fatalf("an in-place tampered sealed object must fail the hash check, got %v", err)
	}

	// Re-recording the tampered digest gets past the hash check, and the content
	// check still rejects it: a verifier that only compared the recorded value
	// would have accepted these bytes.
	tamperedObject, err := store.Hash(object.Key)
	if err != nil {
		t.Fatalf("hash the tampered object: %v", err)
	}
	if tamperedObject.SHA256 == recorded {
		t.Fatal("the tampered object has the recorded digest")
	}
	_, _, err = VerifyManifestBytes(store, ArtifactRef{
		ObjectKey: object.Key, SHA256: tamperedObject.SHA256, SizeBytes: tamperedObject.Size,
	})
	if !errors.Is(err, ErrManifestInvalid) {
		t.Fatalf("tampered content must fail the content check, got %v", err)
	}
}

func TestVerifyManifestBytesRejectsNonCanonicalAndSchemaInvalidBytes(t *testing.T) {
	payload, _ := goldenJCS(t)
	var pretty any
	if err := json.Unmarshal(payload, &pretty); err != nil {
		t.Fatalf("parse the golden bytes: %v", err)
	}
	prettyBytes, err := json.MarshalIndent(pretty, "", "  ")
	if err != nil {
		t.Fatalf("encode pretty bytes: %v", err)
	}
	store, artifact, _ := sealedManifest(t, prettyBytes)
	_, _, err = VerifyManifestBytes(store, ArtifactRef{
		ObjectKey: artifact.ObjectKey, SHA256: artifact.SHA256, SizeBytes: artifact.SizeBytes,
	})
	if !errors.Is(err, ErrManifestInvalid) {
		t.Fatalf("pretty-printed bytes must be rejected as non-canonical, got %v", err)
	}

	// Remove a required field, keep the bytes canonical and hash-consistent.
	var fields map[string]any
	if err := json.Unmarshal(payload, &fields); err != nil {
		t.Fatalf("parse fields: %v", err)
	}
	delete(fields, "worker_image_digest")
	encoded, err := json.Marshal(fields)
	if err != nil {
		t.Fatalf("encode fields: %v", err)
	}
	invalid, err := jcs.CanonicalizeBytes(encoded)
	if err != nil {
		t.Fatalf("canonicalise fields: %v", err)
	}
	store, artifact, _ = sealedManifest(t, invalid)
	_, _, err = VerifyManifestBytes(store, artifact)
	if !errors.Is(err, ErrManifestInvalid) {
		t.Fatalf("a missing required field must be rejected, got %v", err)
	}
}

func TestVerifyManifestBytesChecksTheRecordedSize(t *testing.T) {
	payload, _ := goldenJCS(t)
	store, artifact, _ := sealedManifest(t, payload)
	artifact.SizeBytes = int64(len(payload)) + 1
	_, _, err := VerifyManifestBytes(store, artifact)
	if !errors.Is(err, ErrManifestSizeMismatch) {
		t.Fatalf("a wrong recorded size must fail, got %v", err)
	}
}

func TestVerifyManifestBytesFailsClosedWithoutAStoreOrObject(t *testing.T) {
	if _, _, err := VerifyManifestBytes(nil, ArtifactRef{}); !errors.Is(err, objectstore.ErrNotConfigured) {
		t.Fatalf("a nil store must fail closed, got %v", err)
	}
	store, err := objectstore.Open(t.TempDir())
	if err != nil {
		t.Fatalf("open the object store: %v", err)
	}
	_, _, err = VerifyManifestBytes(store, ArtifactRef{
		ObjectKey: "sealed/" + strings.Repeat("0", 64),
		SHA256:    strings.Repeat("0", 64),
		SizeBytes: 1,
	})
	if !errors.Is(err, objectstore.ErrNotExist) {
		t.Fatalf("a missing object must be reported as ErrNotExist, got %v", err)
	}
}
