package knowledge

// C3.01: the owner-bound readable-chunk module.
//
// Retrieval candidates, a single citation open and the retrieval-cache hit
// re-check need the same three things before any chunk text may be served:
//
//  1. the one permission/provenance predicate of readable_chunk.go;
//  2. the authoritative integrity metadata of the body artifact
//     (`platform.artifacts.sha256` and `size_bytes`) plus its object key;
//  3. one bounded read of the sealed object that counts the bytes and computes
//     the SHA-256 in the same pass, with text returned only when both equal the
//     authoritative values.
//
// This module owns those three. Callers keep their own business behaviour:
// retrieval ranks, bounds and marks partial results; the citation open maps
// "not readable right now" to its public not-found answer; the answer re-uses the
// citation open for every citation it returns; the cache hit keeps the key, the
// TTL and the decision to keep or delete a row. None of them composes the SQL,
// opens an object or compares a digest itself (C3.02 deleted the remaining
// copies: the raw body read and the duplicate cache verification helpers).
//
// The reader is bound to one owner for one request. It never caches an
// authorization decision and it is not a cross-request session.

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// chunkBodyReader is the one capability the module needs from the object store:
// open one sealed object. It stays a small interface so the unit seam can count
// opens and read passes (the verification-cost guard) without wrapping the
// production store.
type chunkBodyReader interface {
	Open(key string) (io.ReadCloser, int64, error)
}

// readableChunkColumns is the module's single metadata projection: the chunk
// identity/provenance every caller returns, plus the authoritative body-artifact
// integrity facts and object key the module needs to verify the bytes.
//
// Nullability is preserved on purpose. A NULL digest or size must reach the
// verifier as a refusal so the caller marks the answer partial; filtering such
// rows out in SQL would silently turn a damaged candidate into a "complete"
// result that can then be memoized.
const readableChunkColumns = `
		  SELECT kc.id::text, kc.document_id::text, kc.document_revision, kc.chunk_no,
		         kc.citation, kc.embedding, kc.body_artifact_id::text,
		         coalesce(a.sha256, ''), a.size_bytes, a.object_key`

// chunkCandidate is the metadata projection of one SQL-visible readable chunk.
// It is not yet readable text: BodySHA256 and sizeBytes are the authoritative
// artifact facts the sealed bytes must reproduce.
type chunkCandidate struct {
	ChunkID          string
	DocumentID       string
	DocumentRevision int64
	ChunkNo          int
	Citation         json.RawMessage
	Embedding        []float64
	BodyArtifactID   string
	BodySHA256       string
	sizeBytes        sql.NullInt64
	objectKey        string
}

type chunkCandidateScanner interface {
	Scan(dest ...any) error
}

// scanChunkCandidate reads one row of readableChunkColumns. The digest is
// normalized to lowercase hex because the authoritative column may be compared
// case-insensitively but the byte digest is always lowercase.
func scanChunkCandidate(row chunkCandidateScanner) (chunkCandidate, error) {
	var (
		candidate chunkCandidate
		citation  []byte
		embedding sql.NullString
	)
	if err := row.Scan(
		&candidate.ChunkID, &candidate.DocumentID, &candidate.DocumentRevision,
		&candidate.ChunkNo, &citation, &embedding, &candidate.BodyArtifactID,
		&candidate.BodySHA256, &candidate.sizeBytes, &candidate.objectKey,
	); err != nil {
		return chunkCandidate{}, err
	}
	candidate.Citation = json.RawMessage(citation)
	candidate.Embedding = parseFloatArray(embedding.String)
	candidate.BodySHA256 = strings.ToLower(strings.TrimSpace(candidate.BodySHA256))
	return candidate, nil
}

// chunkBodyFailureKind classifies one sealed body the module refuses to serve.
// It is internal: callers map it to their own public answer (skip the candidate,
// not-found, or one of the cache's safe discard reasons).
type chunkBodyFailureKind int

const (
	// chunkBodyMetadataInvalid: the body artifact row has no usable sha256 or
	// size_bytes. Missing or illegal integrity metadata never passes.
	chunkBodyMetadataInvalid chunkBodyFailureKind = iota
	// chunkBodyUnreadable: a deterministic object-store refusal (the object is
	// gone, the key is not a platform key, the object exceeds the protocol cap).
	chunkBodyUnreadable
	// chunkBodyIntegrityMismatch: the sealed bytes are not the authoritative
	// bytes (wrong size or wrong digest).
	chunkBodyIntegrityMismatch
	// chunkBodyUnavailable: a transient storage/IO failure; the bytes may be
	// readable again later.
	chunkBodyUnavailable
)

// chunkBodyError is the internal refusal of one sealed chunk body. The message
// deliberately contains neither the object key nor a filesystem path: callers may
// log it or map it, but nothing here is allowed to reach a public error. The kind
// exists so the cache hit keeps ADR 0015's deterministic-vs-transient decision.
type chunkBodyError struct {
	kind  chunkBodyFailureKind
	cause error
}

func (e *chunkBodyError) Error() string {
	switch e.kind {
	case chunkBodyMetadataInvalid:
		return "the chunk body artifact has no usable integrity metadata"
	case chunkBodyUnreadable:
		return "the sealed chunk body is not readable"
	case chunkBodyIntegrityMismatch:
		return "the sealed chunk body does not match its authoritative digest and size"
	default:
		return "the sealed chunk body is temporarily unavailable"
	}
}

func (e *chunkBodyError) Unwrap() error { return e.cause }

// integrityMetadataError refuses a candidate whose authoritative digest or size
// cannot be trusted. A body can only be verified if both exist: a missing digest
// cannot prove the bytes, and a missing/zero/out-of-protocol size cannot bound
// the read.
func (c chunkCandidate) integrityMetadataError() error {
	if !validSHA256Hex(c.BodySHA256) {
		return &chunkBodyError{kind: chunkBodyMetadataInvalid}
	}
	if !c.sizeBytes.Valid || c.sizeBytes.Int64 <= 0 ||
		c.sizeBytes.Int64 > objectstore.MaxObjectBytes {
		return &chunkBodyError{kind: chunkBodyMetadataInvalid}
	}
	return nil
}

// readVerifiedBody opens the sealed object once and reads it with a bound of the
// authoritative size plus one byte. The same read feeds the SHA-256 hasher, so
// the byte count and the digest come from exactly one pass over the bytes:
//
//   - a body shorter than size_bytes (truncation) and a body longer than it (a
//     rewrite) both fail the byte count;
//   - a same-length replacement passes the count but fails the digest;
//   - the host's stat/size is never used as proof, because it describes the
//     filesystem entry, not the authoritative bytes.
func (c chunkCandidate) readVerifiedBody(objects chunkBodyReader) (string, error) {
	if err := c.integrityMetadataError(); err != nil {
		return "", err
	}
	if objects == nil {
		return "", &chunkBodyError{kind: chunkBodyUnavailable, cause: ErrObjectStoreUnavailable}
	}
	reader, _, err := objects.Open(c.objectKey)
	if err != nil {
		return "", &chunkBodyError{kind: classifyChunkBodyOpenError(err), cause: err}
	}
	defer func() { _ = reader.Close() }()

	hasher := sha256.New()
	content, err := io.ReadAll(io.LimitReader(io.TeeReader(reader, hasher), c.sizeBytes.Int64+1))
	if err != nil {
		return "", &chunkBodyError{kind: chunkBodyUnavailable, cause: err}
	}
	if int64(len(content)) != c.sizeBytes.Int64 {
		return "", &chunkBodyError{kind: chunkBodyIntegrityMismatch}
	}
	if hex.EncodeToString(hasher.Sum(nil)) != c.BodySHA256 {
		return "", &chunkBodyError{kind: chunkBodyIntegrityMismatch}
	}
	return string(content), nil
}

// classifyChunkBodyOpenError separates a deterministic object-store refusal from
// a transient storage failure. Retrieval skips both; the cache hit may only
// destroy an entry for the deterministic class.
func classifyChunkBodyOpenError(err error) chunkBodyFailureKind {
	switch {
	case errors.Is(err, objectstore.ErrNotExist),
		errors.Is(err, objectstore.ErrInvalidKey),
		errors.Is(err, objectstore.ErrTooLarge):
		return chunkBodyUnreadable
	default:
		return chunkBodyUnavailable
	}
}

// validSHA256Hex requires the authoritative digest to be exactly 64 lowercase
// hex characters. The digest is stored lowercase by the finalize guard; accepting
// a case variant would be harmless, but a wrong length or a non-hex "digest" can
// never be the SHA-256 of any bytes and must fail closed.
func validSHA256Hex(value string) bool {
	if len(value) != 64 {
		return false
	}
	for index := 0; index < len(value); index++ {
		char := value[index]
		if (char < '0' || char > '9') && (char < 'a' || char > 'f') {
			return false
		}
	}
	return true
}

// readableChunk is one chunk that passed the shared permission predicate and
// whose sealed bytes reproduced the authoritative digest and size.
type readableChunk struct {
	chunkCandidate
	Text string
}

// chunkReader is the owner-bound readable-chunk module. Service.reader builds one
// per request; it holds no authorization decision between requests.
type chunkReader struct {
	db      *sql.DB
	objects chunkBodyReader
	ownerID string
}

// reader binds the module to one caller's owner id. A nil object store is passed
// as a nil interface (not a typed nil pointer) so every read fails closed with
// ErrObjectStoreUnavailable instead of calling into a nil store.
func (s *Service) reader(ownerID string) chunkReader {
	reader := chunkReader{db: s.DB, ownerID: ownerID}
	if s.Objects != nil {
		reader.objects = s.Objects
	}
	return reader
}

// Candidates is the retrieval-candidate operation: the SQL-filtered candidate
// set of one knowledge base under the current embedding model, read and verified
// chunk by chunk. It keeps the existing candidate bound (MaxCandidates) and the
// existing SQL order; ranking stays with the caller.
//
// A query/scan failure is returned as an error because the caller cannot answer
// at all. A per-object failure (missing/invalid metadata, corrupt bytes, missing
// object, transient read error) is not an error: the candidate is skipped and
// counted. A non-zero skipped count is what marks the result partial so it is
// never memoized.
func (r chunkReader) Candidates(
	ctx context.Context, baseID, modelRevision string,
) ([]readableChunk, int, error) {
	rows, err := r.db.QueryContext(ctx, readableChunkColumns+
		// The shared "current readable chunk" predicate (readable_chunk.go): owner
		// ACL, tombstones, active ready index version and body-artifact ownership.
		currentReadableChunkSQL(2)+`
		   AND kb.id = $1::uuid
		   AND iv.embedding_model_revision = $3
		   AND kc.embedding IS NOT NULL
		   AND kc.embedding_model_revision = $3
		   AND kc.embedding_dimension = iv.embedding_dimension
		 ORDER BY kc.document_id, kc.chunk_no
		 LIMIT $4`,
		baseID, r.ownerID, modelRevision, MaxCandidates,
	)
	if err != nil {
		return nil, 0, fmt.Errorf("reading the retrieval candidates: %w", err)
	}
	defer func() { _ = rows.Close() }()

	candidates := make([]chunkCandidate, 0, 32)
	for rows.Next() {
		candidate, scanErr := scanChunkCandidate(rows)
		if scanErr != nil {
			return nil, 0, fmt.Errorf("reading a retrieval candidate: %w", scanErr)
		}
		candidates = append(candidates, candidate)
	}
	if err := rows.Err(); err != nil {
		return nil, 0, fmt.Errorf("reading the retrieval candidates: %w", err)
	}

	chunks := make([]readableChunk, 0, len(candidates))
	skipped := 0
	for _, candidate := range candidates {
		text, readErr := candidate.readVerifiedBody(r.objects)
		if readErr != nil {
			skipped++
			continue
		}
		chunks = append(chunks, readableChunk{chunkCandidate: candidate, Text: text})
	}
	return chunks, skipped, nil
}

// Open is the single-chunk operation behind a citation open: one chunk the owner
// may read right now, verified like every retrieval candidate. Every refusal
// (missing/foreign/revoked/superseded row, unusable metadata, unreadable or
// mismatching bytes) is ErrNotFound, so the caller keeps the citation surface's
// indistinguishable 404.
func (r chunkReader) Open(ctx context.Context, chunkID string) (readableChunk, error) {
	candidate, err := scanChunkCandidate(r.db.QueryRowContext(ctx, readableChunkColumns+
		currentReadableChunkSQL(2)+`
		   AND kc.id = $1::uuid`,
		chunkID, r.ownerID,
	))
	if errors.Is(err, sql.ErrNoRows) {
		return readableChunk{}, ErrNotFound
	}
	if err != nil {
		return readableChunk{}, fmt.Errorf("reading the chunk: %w", err)
	}
	text, err := candidate.readVerifiedBody(r.objects)
	if err != nil {
		return readableChunk{}, ErrNotFound
	}
	return readableChunk{chunkCandidate: candidate, Text: text}, nil
}

// CheckCached is the cache hit re-check operation. It proves every stored item is
// still the current readable chunk and that the sealed bytes are the stored body:
// the same permission predicate, the same authoritative metadata and the same
// one-pass verification as Candidates/Open.
//
// The caller (the cache hit path) keeps the key, the TTL, the stored components
// and the decision to keep or delete the row. This operation only answers "may
// these bodies still be served?" with ADR 0015 D6's closed reason vocabulary: a
// transient failure is `cache_unavailable` (the row is kept by the caller), an
// integrity mismatch is `item_mismatch`, and every deterministic unavailability
// is `item_unreadable`.
func (r chunkReader) CheckCached(
	ctx context.Context, baseID string, items []retrievalCacheItem,
) (bool, string) {
	ids := make([]string, 0, len(items))
	for _, item := range items {
		if !uuid.Valid(item.ChunkID) {
			return false, CacheDiscardItemMismatch
		}
		ids = append(ids, item.ChunkID)
	}
	rows, err := r.db.QueryContext(ctx, readableChunkColumns+
		// The same predicate retrieval and citation open use. The owner is $3 here
		// and the candidate predicate is the stored chunk-id set of this KB.
		currentReadableChunkSQL(3)+`
		   AND kc.id = ANY(string_to_array($1::text, ',')::uuid[])
		   AND kb.id = $2::uuid`,
		strings.Join(ids, ","), baseID, r.ownerID,
	)
	if err != nil {
		return false, CacheDiscardUnavailable
	}
	defer func() { _ = rows.Close() }()

	current := make(map[string]chunkCandidate, len(items))
	for rows.Next() {
		candidate, scanErr := scanChunkCandidate(rows)
		if scanErr != nil {
			return false, CacheDiscardUnavailable
		}
		current[candidate.ChunkID] = candidate
	}
	if err := rows.Err(); err != nil {
		return false, CacheDiscardUnavailable
	}
	if len(current) != len(items) {
		return false, CacheDiscardItemUnreadable
	}
	for _, item := range items {
		candidate, ok := current[item.ChunkID]
		if !ok {
			return false, CacheDiscardItemUnreadable
		}
		// The stored provenance must name the row that is readable now, before a
		// byte is read: a replaced document/revision/artifact is a mismatch, not a
		// readable body.
		if candidate.DocumentID != item.DocumentID ||
			candidate.DocumentRevision != item.DocumentRevision ||
			candidate.ChunkNo != item.ChunkNo ||
			candidate.BodyArtifactID != item.BodyArtifactID ||
			candidate.BodySHA256 != item.BodySHA256 {
			return false, CacheDiscardItemMismatch
		}
		text, bodyErr := candidate.readVerifiedBody(r.objects)
		if bodyErr != nil {
			return false, cacheDiscardReasonForBodyError(bodyErr)
		}
		// The digest equality is already proved by the verification above and by
		// the provenance comparison, so only the memoized text has to be compared;
		// the bytes are never hashed a second time.
		if text != item.Text {
			return false, CacheDiscardItemMismatch
		}
	}
	return true, ""
}

// cacheDiscardReasonForBodyError maps the module's internal body refusal onto
// ADR 0015 D6's existing reason vocabulary. No new reason word is introduced: a
// transient read keeps the row for a later request, while a digest/size mismatch
// or a deterministic unavailability destroys it.
func cacheDiscardReasonForBodyError(err error) string {
	var bodyErr *chunkBodyError
	if errors.As(err, &bodyErr) {
		switch bodyErr.kind {
		case chunkBodyUnavailable:
			return CacheDiscardUnavailable
		case chunkBodyIntegrityMismatch:
			return CacheDiscardItemMismatch
		}
	}
	return CacheDiscardItemUnreadable
}
