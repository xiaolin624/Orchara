package knowledge

// C3.01 unit seam: the sealed-body verification primitive and its cost.
//
// The real-stack suite (tests/f10/test_c301_verified_retrieval.py) proves the
// outcome through the retrieval API. These tests pin the byte-level rules and the
// exact read cost the module is allowed to pay: one open per body, one bounded
// read pass (never a second open and never a full read of an oversized object),
// and a fail-closed refusal before any object is opened when the authoritative
// integrity metadata is missing or illegal.

import (
	"bytes"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"io"
	"strings"
	"testing"

	"orchara/platform/internal/objectstore"
)

func digestOf(content []byte) string {
	sum := sha256.Sum256(content)
	return hex.EncodeToString(sum[:])
}

// countingChunkBody is a chunkBodyReader that counts opens, Read calls and bytes.
// It deliberately reports a stored size of zero: the verifier must rely on the
// authoritative size_bytes and on the bytes it reads, never on the store's stat.
type countingChunkBody struct {
	content   []byte
	openErr   error
	openCount int
	readCalls int
	bytesRead int
	closeCnt  int
	lastKey   string
}

func (f *countingChunkBody) Open(key string) (io.ReadCloser, int64, error) {
	f.openCount++
	f.lastKey = key
	if f.openErr != nil {
		return nil, 0, f.openErr
	}
	return &countingReadCloser{body: f, reader: bytes.NewReader(f.content)}, 0, nil
}

type countingReadCloser struct {
	body   *countingChunkBody
	reader *bytes.Reader
}

func (c *countingReadCloser) Read(p []byte) (int, error) {
	read, err := c.reader.Read(p)
	c.body.readCalls++
	c.body.bytesRead += read
	return read, err
}

func (c *countingReadCloser) Close() error {
	c.body.closeCnt++
	return nil
}

func candidateFor(content []byte) chunkCandidate {
	return chunkCandidate{
		ChunkID:        "chunk-1",
		BodyArtifactID: "artifact-1",
		BodySHA256:     digestOf(content),
		sizeBytes:      sqlNullInt64(int64(len(content))),
		objectKey:      "sealed/" + strings.Repeat("a", 64),
	}
}

func sqlNullInt64(value int64) sql.NullInt64 {
	return sql.NullInt64{Int64: value, Valid: true}
}

func sqlNullInt64Absent() sql.NullInt64 {
	return sql.NullInt64{}
}

func TestReadVerifiedBodyReturnsTextAfterOneBoundedRead(t *testing.T) {
	content := []byte("the sealed chunk body")
	candidate := candidateFor(content)
	objects := &countingChunkBody{content: content}

	text, err := candidate.readVerifiedBody(objects)
	if err != nil {
		t.Fatalf("readVerifiedBody: %v", err)
	}
	if text != string(content) {
		t.Fatalf("text = %q", text)
	}
	if objects.openCount != 1 {
		t.Fatalf("opens = %d, want exactly one", objects.openCount)
	}
	if objects.closeCnt != 1 {
		t.Fatalf("closes = %d, want exactly one", objects.closeCnt)
	}
	if objects.bytesRead != len(content) {
		t.Fatalf("bytes read = %d, want %d", objects.bytesRead, len(content))
	}
	if objects.lastKey != candidate.objectKey {
		t.Fatalf("opened %q", objects.lastKey)
	}
}

func TestReadVerifiedBodyRefusesASameLengthReplacement(t *testing.T) {
	original := []byte("the sealed chunk body")
	replaced := []byte("THE SEALED CHUNK BODY")
	if len(replaced) != len(original) {
		t.Fatalf("test corpus must be a same-length replacement")
	}
	candidate := candidateFor(original)
	objects := &countingChunkBody{content: replaced}

	text, err := candidate.readVerifiedBody(objects)
	if text != "" {
		t.Fatalf("a same-length replacement returned %q", text)
	}
	assertBodyFailure(t, err, chunkBodyIntegrityMismatch)
	if objects.openCount != 1 {
		t.Fatalf("opens = %d", objects.openCount)
	}
}

func TestReadVerifiedBodyRefusesTruncation(t *testing.T) {
	original := []byte("the sealed chunk body")
	candidate := candidateFor(original)
	objects := &countingChunkBody{content: original[:len(original)-1]}

	text, err := candidate.readVerifiedBody(objects)
	if text != "" {
		t.Fatalf("a truncated body returned %q", text)
	}
	assertBodyFailure(t, err, chunkBodyIntegrityMismatch)
}

func TestReadVerifiedBodyRefusesAWriteThatExceedsTheAuthoritativeSize(t *testing.T) {
	original := []byte("the sealed chunk body")
	candidate := candidateFor(original)
	// The object was rewritten longer than the recorded size. The bounded read
	// takes size+1 bytes, so it can see that the object is longer, and it must not
	// stream the rest of the object to find that out.
	objects := &countingChunkBody{content: append(append([]byte{}, original...), []byte("tampered tail")...)}

	text, err := candidate.readVerifiedBody(objects)
	if text != "" {
		t.Fatalf("an oversized body returned %q", text)
	}
	assertBodyFailure(t, err, chunkBodyIntegrityMismatch)
	if objects.bytesRead != len(original)+1 {
		t.Fatalf("bounded read consumed %d bytes, want %d", objects.bytesRead, len(original)+1)
	}
}

func TestReadVerifiedBodyUsesTheAuthoritativeSizeNotOneFromTheStore(t *testing.T) {
	content := []byte("the sealed chunk body")
	candidate := candidateFor(content)
	// The candidate records the correct size; the store's reported size is 0 (see
	// countingChunkBody.Open). A verifier that trusted the store would fail here.
	if _, err := candidate.readVerifiedBody(&countingChunkBody{content: content}); err != nil {
		t.Fatalf("readVerifiedBody with a store-reported size of 0: %v", err)
	}

	// A recorded size that disagrees with the real bytes is a refusal even though
	// the store and the digest agree on the bytes.
	for _, wrong := range []int64{int64(len(content)) + 1, int64(len(content)) - 1} {
		mismatched := candidate
		mismatched.sizeBytes = sqlNullInt64(wrong)
		if text, err := mismatched.readVerifiedBody(
			&countingChunkBody{content: content},
		); text != "" || err == nil {
			t.Fatalf("size %d: text=%q err=%v", wrong, text, err)
		}
	}
}

func TestReadVerifiedBodyRefusesUnusableMetadataBeforeOpeningAnything(t *testing.T) {
	content := []byte("the sealed chunk body")
	mutations := map[string]func(*chunkCandidate){
		"sha256 missing": func(c *chunkCandidate) { c.BodySHA256 = "" },
		"sha256 short":   func(c *chunkCandidate) { c.BodySHA256 = strings.Repeat("0", 63) },
		"sha256 nonhex":  func(c *chunkCandidate) { c.BodySHA256 = strings.Repeat("z", 64) },
		"size missing":   func(c *chunkCandidate) { c.sizeBytes = sqlNullInt64Absent() },
		"size zero":      func(c *chunkCandidate) { c.sizeBytes = sqlNullInt64(0) },
		"size over cap": func(c *chunkCandidate) {
			c.sizeBytes = sqlNullInt64(objectstore.MaxObjectBytes + 1)
		},
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			candidate := candidateFor(content)
			mutate(&candidate)
			objects := &countingChunkBody{content: content}
			text, err := candidate.readVerifiedBody(objects)
			if text != "" {
				t.Fatalf("unusable metadata returned %q", text)
			}
			assertBodyFailure(t, err, chunkBodyMetadataInvalid)
			if objects.openCount != 0 {
				t.Fatalf("metadata refusal still opened the object %d time(s)", objects.openCount)
			}
		})
	}
}

func TestReadVerifiedBodyClassificationNeverLeaksTheObjectKeyOrAPath(t *testing.T) {
	content := []byte("the sealed chunk body")
	candidate := candidateFor(content)

	cases := map[string]struct {
		objects *countingChunkBody
		kind    chunkBodyFailureKind
	}{
		"missing object": {
			objects: &countingChunkBody{openErr: objectstore.ErrNotExist},
			kind:    chunkBodyUnreadable,
		},
		"invalid key": {
			objects: &countingChunkBody{openErr: objectstore.ErrInvalidKey},
			kind:    chunkBodyUnreadable,
		},
		"over protocol cap": {
			objects: &countingChunkBody{openErr: objectstore.ErrTooLarge},
			kind:    chunkBodyUnreadable,
		},
		"transient IO": {
			objects: &countingChunkBody{
				openErr: errors.New("open C:\\objects\\sealed\\private-key: sharing violation"),
			},
			kind: chunkBodyUnavailable,
		},
	}
	for name, testCase := range cases {
		t.Run(name, func(t *testing.T) {
			_, err := candidate.readVerifiedBody(testCase.objects)
			assertBodyFailure(t, err, testCase.kind)
			if strings.Contains(err.Error(), candidate.objectKey) ||
				strings.Contains(err.Error(), "private-key") ||
				strings.Contains(err.Error(), "C:\\objects") {
				t.Fatalf("the refusal leaked a key or path: %v", err)
			}
		})
	}
}

func TestCacheDiscardReasonReusesTheClosedVocabulary(t *testing.T) {
	cases := []struct {
		err    error
		reason string
	}{
		{&chunkBodyError{kind: chunkBodyIntegrityMismatch}, CacheDiscardItemMismatch},
		{&chunkBodyError{kind: chunkBodyUnavailable}, CacheDiscardUnavailable},
		{&chunkBodyError{kind: chunkBodyUnreadable}, CacheDiscardItemUnreadable},
		{&chunkBodyError{kind: chunkBodyMetadataInvalid}, CacheDiscardItemUnreadable},
		{errors.New("something else"), CacheDiscardItemUnreadable},
	}
	allowed := map[string]bool{
		CacheDiscardExpired:           true,
		CacheDiscardComponentsChanged: true,
		CacheDiscardItemUnreadable:    true,
		CacheDiscardItemMismatch:      true,
		CacheDiscardUnavailable:       true,
	}
	for _, testCase := range cases {
		reason := cacheDiscardReasonForBodyError(testCase.err)
		if reason != testCase.reason {
			t.Fatalf("reason = %q, want %q", reason, testCase.reason)
		}
		if !allowed[reason] {
			t.Fatalf("reason %q is not in ADR 0015's vocabulary", reason)
		}
	}
}

func TestValidSHA256Hex(t *testing.T) {
	if !validSHA256Hex(strings.Repeat("a", 64)) {
		t.Fatal("a 64-character lowercase hex digest was refused")
	}
	for _, value := range []string{"", strings.Repeat("a", 63), strings.Repeat("a", 65),
		strings.Repeat("A", 64), strings.Repeat("g", 64), " sha256"} {
		if validSHA256Hex(value) {
			t.Fatalf("%q was accepted as a SHA-256 digest", value)
		}
	}
}

func assertBodyFailure(t *testing.T, err error, kind chunkBodyFailureKind) {
	t.Helper()
	var bodyErr *chunkBodyError
	if !errors.As(err, &bodyErr) {
		t.Fatalf("error %v is not a chunkBodyError", err)
	}
	if bodyErr.kind != kind {
		t.Fatalf("failure kind = %d, want %d", bodyErr.kind, kind)
	}
	if err.Error() == "" {
		t.Fatal("the refusal has no message")
	}
}

// BenchmarkReadVerifiedBody records the added verification cost on the real
// local object store: one open, one bounded read and one SHA-256 pass over a
// typical chunk body. It is evidence, not a latency assertion (no test depends on
// its numbers; the functional guard is TestReadVerifiedBodyReturnsTextAfterOneBoundedRead).
func BenchmarkReadVerifiedBody(b *testing.B) {
	store, err := objectstore.Open(b.TempDir())
	if err != nil {
		b.Fatalf("objectstore.Open: %v", err)
	}
	// ~2.4 KiB, the shape of a paragraph chunk near the 1200-rune bound.
	content := []byte(strings.Repeat("chunk body 段落 ", 160))
	object, err := store.CreateSealed(bytes.NewReader(content), 0)
	if err != nil {
		b.Fatalf("CreateSealed: %v", err)
	}
	candidate := chunkCandidate{
		ChunkID:        "chunk-bench",
		BodyArtifactID: "artifact-bench",
		BodySHA256:     object.SHA256,
		sizeBytes:      sqlNullInt64(object.Size),
		objectKey:      object.Key,
	}
	b.SetBytes(object.Size)
	b.ResetTimer()
	for index := 0; index < b.N; index++ {
		if _, err := candidate.readVerifiedBody(store); err != nil {
			b.Fatalf("readVerifiedBody: %v", err)
		}
	}
}
