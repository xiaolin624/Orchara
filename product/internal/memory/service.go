// Package memory is the F09 personal long-term memory store.
//
// Two aggregates live here, in the order the ticket builds them:
//
//	candidate  a proposed personal fact whose body is a sealed artifact. It has
//	           an owner, an application scope derived from the run that produced
//	           it, a source run, a deadline and a four-state lifecycle. A
//	           candidate is never a recall source: nothing outside the candidate
//	           list/decision path reads its body, and confirming it is the only
//	           way it can become a memory.
//	memory     one confirmed personal fact. It is the recall source (T063), it
//	           carries a revision for the F09/T058 ETag update path, and it is
//	           reachable only by its owner (an administrator is not special: an
//	           administrator who does not own the row sees the same 404).
//
// Personal memory is human-only. A service credential is refused by the HTTP
// layer with `personal_scope_forbidden` before any store code runs, and the
// tables themselves reference `platform.users` for owner/confirmer, so a service
// principal cannot be made an owner even by a future writer.
//
// The store is deliberately synchronous: the body is sealed before the row is
// committed (or removed when the transaction rolls back), so a committed memory
// can never point at missing bytes.
package memory

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

const (
	// DefaultCandidateTTL is how long a candidate waits for a human decision when
	// the deployment sets no ORCHARA_MEMORY_CANDIDATE_TTL. Expiry is always
	// judged by the server against the stored expires_at.
	DefaultCandidateTTL = 7 * 24 * time.Hour

	// MaxTextRunes is the contract's MemoryInput/MemoryCandidate text ceiling.
	MaxTextRunes = 4000

	// PurposeCandidate and PurposeMemory name the sealed-artifact purposes.
	PurposeCandidate = "memory_candidate"
	PurposeMemory    = "memory_body"

	// TextMediaType is the media type recorded on both sealed text artifacts.
	TextMediaType = "text/plain; charset=utf-8"

	// MaxArtifactBytes bounds what a body read will accept. 4000 runes is at
	// most 16 KiB in UTF-8; the ceiling keeps a corrupted row from being read
	// without limit.
	MaxArtifactBytes = 64 * 1024

	// DefaultListLimit and MaxListLimit mirror the contract list query.
	DefaultListLimit = 20
	MaxListLimit     = 100

	// IdempotencyTTL is the API.md §1 recommended receipt lifetime.
	IdempotencyTTL = 24 * time.Hour

	// decisionMethod is the only HTTP method the decision receipt stores.
	decisionMethod = "POST"
)

// The stable error vocabulary of this aggregate. The HTTP layer maps each one
// onto the contract error envelope; the three decision codes mirror the
// approval aggregate's `approval_expired`/`approval_not_pending` precedent.
// `precondition_required`/`precondition_failed` are the shared optimistic-
// concurrency codes the draft PUT already uses (API.md: missing If-Match is 428,
// a stale one is 412); T058 reuses them instead of inventing a memory-only pair.
const (
	CodeResourceNotFound       = "resource_not_found"
	CodePersonalScope          = "personal_scope_forbidden"
	CodeStaleAttempt           = "stale_attempt"
	CodePermissionRevoked      = "permission_revoked"
	CodeRunNotRunning          = "run_not_running"
	CodeRunCancelled           = "run_cancelled"
	CodeCandidateExpired       = "memory_candidate_expired"
	CodeCandidateRejected      = "memory_candidate_rejected"
	CodeCandidateConfirmed     = "memory_candidate_confirmed"
	CodeIdempotencyConflict    = "idempotency_conflict"
	CodeObjectStoreUnavailable = "object_store_unavailable"
	CodePreconditionRequired   = "precondition_required"
	CodePreconditionFailed     = "precondition_failed"
)

// Error is one located refusal. It never carries memory text or a credential.
//
// Details carries the contract's error details for the few refusals that must
// teach the client how to recover (the current ETag on a stale If-Match, the
// header name on a missing one). It is merged with Field by the HTTP layer.
type Error struct {
	Status  int
	Code    string
	Message string
	Field   string
	Details map[string]any
}

func (e *Error) Error() string { return e.Message }

func failure(status int, code, message string) *Error {
	return &Error{Status: status, Code: code, Message: message}
}

func fieldFailure(status int, code, message, field string) *Error {
	return &Error{Status: status, Code: code, Message: message, Field: field}
}

// preconditionRequired is the 428 for a write with no If-Match at all.
func preconditionRequired() *Error {
	return &Error{
		Status:  http.StatusPreconditionRequired,
		Code:    CodePreconditionRequired,
		Message: "If-Match is required to update a memory",
		Details: map[string]any{"header": "If-Match"},
	}
}

// preconditionFailed is the 412 for a stale or garbled If-Match. It reports the
// revision and the exact strong ETag the client now has to use, which is what
// makes the page able to recover instead of guessing.
func preconditionFailed(revision int64) *Error {
	return &Error{
		Status:  http.StatusPreconditionFailed,
		Code:    CodePreconditionFailed,
		Message: "the memory has moved on; fetch it again and retry with the current ETag",
		Details: map[string]any{
			"current_revision": revision,
			"current_etag":     catalog.FormatETag(revision),
		},
	}
}

// AsError renders a typed *Error from err (nil otherwise).
func AsError(err error) (*Error, bool) {
	var typed *Error
	if errors.As(err, &typed) && typed != nil {
		return typed, true
	}
	return nil, false
}

// Service owns the two tables and the sealed bodies they point at.
type Service struct {
	DB      *sql.DB
	Objects *objectstore.Store
	// Applications applies the existing application ACL when a direct
	// createMemory names its scope. It may be nil in a process that never serves
	// createMemory; every scope check then fails closed with 404.
	Applications *catalog.Reader
	// CandidateTTL is the deployment's ORCHARA_MEMORY_CANDIDATE_TTL. Zero falls
	// back to DefaultCandidateTTL.
	CandidateTTL time.Duration
}

// New builds the service. Objects nil means "no byte store": every write and
// every read that needs a body fails closed with 503 rather than inventing text.
func New(db *sql.DB, objects *objectstore.Store, applications *catalog.Reader, ttl time.Duration) *Service {
	if ttl <= 0 {
		ttl = DefaultCandidateTTL
	}
	return &Service{DB: db, Objects: objects, Applications: applications, CandidateTTL: ttl}
}

// Candidate is the contract's MemoryCandidate plus the server-side facts the
// service and the page need. Text is loaded from the sealed body.
type Candidate struct {
	ID             string
	OwnerID        string
	ApplicationID  string
	BodyArtifactID string
	SourceRunID    string
	LogicalKey     string
	State          string
	ExpiresAt      time.Time
	CreatedAt      time.Time
	DecidedAt      *time.Time
	DecidedBy      string
	Text           string
}

// Memory is the contract's Memory (no state, no expiry) plus the server-side
// facts T058 will need. Revision is fixed at 1 for a memory created by T054.
type Memory struct {
	ID             string
	OwnerID        string
	ApplicationID  string
	BodyArtifactID string
	CandidateID    string
	Revision       int64
	ConfirmedAt    time.Time
	ConfirmedBy    string
	Text           string
}

// Decision is the contract's MemoryCandidateDecision: MemoryID is nil for a
// rejected candidate and the one memory id for a confirmed one.
type Decision struct {
	CandidateID string
	State       string
	MemoryID    *string
}

// validateText applies the contract's 1..4000 text rule. A blank string is not
// a memory: personal memory is user-visible content, and an empty row would be
// indistinguishable from "no memory".
func validateText(field, text string) (string, error) {
	if strings.TrimSpace(text) == "" {
		return "", fieldFailure(422, "validation_failed", field+" must not be blank", field)
	}
	if utf8.RuneCountInString(text) > MaxTextRunes {
		return "", fieldFailure(422, "validation_failed",
			fmt.Sprintf("%s must be at most %d characters", field, MaxTextRunes), field)
	}
	if len(text) > MaxArtifactBytes {
		return "", fieldFailure(422, "validation_failed", field+" is too large", field)
	}
	return text, nil
}

func validateLogicalKey(logicalKey string) (string, error) {
	key := strings.TrimSpace(logicalKey)
	if key == "" || len(key) > 200 {
		return "", fieldFailure(422, "validation_failed",
			"logical_key must be 1 to 200 characters", "logical_key")
	}
	return key, nil
}

// textDigest is the SHA-256 of the sealed body bytes. It is how a retried
// proposal is compared with the stored candidate without reading it back.
func textDigest(text string) string {
	sum := sha256.Sum256([]byte(text))
	return hex.EncodeToString(sum[:])
}

// sealText writes the text body as a ready, sealed artifact of one purpose.
// It returns the artifact id and the sealed key so the caller can remove the
// object when its transaction rolls back.
func (s *Service) sealText(
	ctx context.Context, tx *sql.Tx, ownerID, purpose, text string,
) (artifactID, sealedKey string, err error) {
	if s.Objects == nil {
		return "", "", failure(503, CodeObjectStoreUnavailable, "the object store is not configured")
	}
	key, err := objectstore.SealedKey()
	if err != nil {
		return "", "", fmt.Errorf("allocating a sealed key: %w", err)
	}
	payload := []byte(text)
	object, err := s.Objects.PutSealed(key, strings.NewReader(text), int64(len(payload)))
	if err != nil {
		return "", "", failure(503, CodeObjectStoreUnavailable, "the memory body could not be sealed")
	}
	if object.SHA256 != textDigest(text) || object.Size != int64(len(payload)) {
		_ = s.Objects.Delete(key)
		return "", "", errors.New("the sealed memory body does not match the text bytes")
	}
	artifactID = uuid.NewString()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
		       (id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, 'ready', 1)`,
		artifactID, ownerID, key, purpose, object.SHA256, TextMediaType, object.Size,
	); err != nil {
		_ = s.Objects.Delete(key)
		return "", "", fmt.Errorf("registering the memory body artifact: %w", err)
	}
	return artifactID, key, nil
}

// discardObject removes a sealed body whose transaction did not commit.
func (s *Service) discardObject(key string) {
	if s.Objects != nil && key != "" {
		_ = s.Objects.Delete(key)
	}
}

// readBody reads and verifies one sealed body. The digest is the artifact row's
// recorded sha256; a mismatch or a missing object fails closed with 503, never
// with empty text.
func (s *Service) readBody(key, digest string) (string, error) {
	if s.Objects == nil {
		return "", failure(503, CodeObjectStoreUnavailable, "the object store is not configured")
	}
	reader, _, err := s.Objects.Open(key)
	if err != nil {
		return "", failure(503, CodeObjectStoreUnavailable, "the memory body could not be read")
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, MaxArtifactBytes+1))
	if err != nil {
		return "", failure(503, CodeObjectStoreUnavailable, "the memory body could not be read")
	}
	if len(content) > MaxArtifactBytes {
		return "", failure(503, CodeObjectStoreUnavailable, "the memory body exceeds the read ceiling")
	}
	if strings.TrimSpace(digest) != "" && textDigest(string(content)) != strings.ToLower(digest) {
		return "", failure(503, CodeObjectStoreUnavailable,
			"the sealed memory body does not match the recorded digest")
	}
	return string(content), nil
}

// decodeListInput validates the shared list query: an optional application
// scope (a well-formed UUID) and the opaque keyset cursor.
func decodeListInput(applicationID, cursor string) (string, *cursorPosition, error) {
	scope := strings.TrimSpace(applicationID)
	if scope != "" && !uuid.Valid(scope) {
		// A malformed query value is a located 422, the same rule `limit` uses; the
		// opaque cursor keeps its own 400 invalid_cursor code because it is not a
		// value the client is expected to author.
		return "", nil, fieldFailure(422, "validation_failed",
			"application_id must be a UUID", "application_id")
	}
	position := &cursorPosition{}
	if strings.TrimSpace(cursor) != "" {
		at, id, err := catalog.DecodeCursor(cursor)
		if err != nil {
			return "", nil, failure(400, "invalid_cursor", "cursor is not a valid continuation token")
		}
		position.At, position.ID, position.Set = at, id, true
	}
	return scope, position, nil
}

type cursorPosition struct {
	At  time.Time
	ID  string
	Set bool
}

func nextCursor(at time.Time, id string, hasMore bool) *string {
	if !hasMore {
		return nil
	}
	token := catalog.EncodeCursor(at, id)
	return &token
}

// bumpEpoch raises the memory cache epoch for one owner+application scope in the
// caller's transaction.
//
// The row lives in the generic `platform.policy_epochs` table (scope, revision),
// the same table the ACL epoch uses, so no new table is introduced. The authorizer
// only reads the scopes it knows (`acl`), so a memory scope is inert for
// authorization and is purely the F09/T058 // T063 // T067 invalidation seam:
// confirm, update and delete raise it in the same transaction as the content
// change, and a cache key that embeds it is stale by construction.
func bumpMemoryEpoch(ctx context.Context, tx *sql.Tx, ownerID, applicationID string) error {
	scope := "memory:" + strings.ToLower(strings.TrimSpace(ownerID)) +
		":" + strings.ToLower(strings.TrimSpace(applicationID))
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.policy_epochs (scope, revision)
		VALUES ($1, 1)
		ON CONFLICT (scope) DO UPDATE
		   SET revision = platform.policy_epochs.revision + 1`, scope); err != nil {
		return fmt.Errorf("raising the memory cache epoch: %w", err)
	}
	return nil
}

// MemoryEpoch reads the current memory cache epoch of one owner+application
// scope (0 when it has never been raised). The page/read model does not use it;
// it exists so T058/T063 can verify their invalidation seam.
func (s *Service) MemoryEpoch(ctx context.Context, ownerID, applicationID string) (int64, error) {
	scope := "memory:" + strings.ToLower(strings.TrimSpace(ownerID)) +
		":" + strings.ToLower(strings.TrimSpace(applicationID))
	var revision int64
	err := s.DB.QueryRowContext(ctx,
		`SELECT revision FROM platform.policy_epochs WHERE scope = $1`, scope).Scan(&revision)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, nil
	}
	if err != nil {
		return 0, fmt.Errorf("reading the memory cache epoch: %w", err)
	}
	return revision, nil
}
