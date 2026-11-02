package artifacts

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Service is the user-facing artifact store: upload reservation, completion
// into the finalize pipeline, owner-only reads and sealed-byte access.
//
// It never activates an artifact. CompleteUpload only moves uploading ->
// scanning and enqueues the one artifact_finalize job; the completion guard in
// internal/background owns the ready/quarantined decision (ADR 0004 §5/§20).
type Service struct {
	DB      *sql.DB
	Objects *objectstore.Store
	Signer  *objectstore.Signer
	// BaseURL is the absolute public base URL signed links are minted against.
	BaseURL string
}

// New builds the service. Objects/Signer may be nil in a process that never
// moves bytes; every operation that needs them then fails closed.
func New(db *sql.DB, objects *objectstore.Store, signer *objectstore.Signer, baseURL string) *Service {
	return &Service{
		DB: db, Objects: objects, Signer: signer,
		BaseURL: strings.TrimSuffix(strings.TrimSpace(baseURL), "/"),
	}
}

// Errors callers map to HTTP answers.
var (
	// ErrNotVisible is "missing, not yours, deleted or quarantined": every one
	// of those answers 404 and must not be distinguishable (no enumeration).
	ErrNotVisible = errors.New("artifact is not visible")
	// ErrNotReady is the 409 answer for download/content before ready.
	ErrNotReady = errors.New("artifact is not ready")
	// ErrObjectStoreUnavailable means ORCHARA_OBJECT_DIR is missing.
	ErrObjectStoreUnavailable = errors.New("the object store is not configured")
	// ErrSigningUnavailable means ORCHARA_OBJECT_SIGNING_KEY is empty.
	ErrSigningUnavailable = errors.New("object link signing is not configured")
)

// Conflict error codes exposed as stable HTTP error codes.
const (
	CodeHashConflict     = "artifact_hash_conflict"
	CodeArtifactConflict = "artifact_conflict"
)

// ConflictError is a 409 with a stable code and no state change.
type ConflictError struct {
	Code    string
	Message string
}

func (e *ConflictError) Error() string { return e.Code + ": " + e.Message }

// Artifact is the internal projection of one artifact row. The HTTP layer
// projects exactly the contract's four fields; QuarantineReason is carried in
// the documented non-contract response header instead.
type Artifact struct {
	ID               string
	State            string
	MediaType        string
	SizeBytes        int64
	QuarantineReason *string
}

// UploadTicket is the contract's Upload response.
type UploadTicket struct {
	ArtifactID string
	URL        string
	ExpiresAt  time.Time
}

// CreateUpload reserves one artifact for a user upload.
//
// Two independent unguessable keys are preallocated: the staging key (the only
// target of the signed PUT) and the sealed key (written only by the finalize
// handler and the completion guard). The client never learns a path, and the
// sealed key is never a PUT target at any point.
func (s *Service) CreateUpload(
	ctx context.Context,
	ownerID string,
	input UploadInput,
) (UploadTicket, error) {
	normalized, _, err := NormalizeUploadInput(input)
	if err != nil {
		return UploadTicket{}, err
	}
	if s.Objects == nil {
		return UploadTicket{}, ErrObjectStoreUnavailable
	}
	if s.Signer == nil {
		return UploadTicket{}, ErrSigningUnavailable
	}

	stagingKey, err := objectstore.StagingKey()
	if err != nil {
		return UploadTicket{}, err
	}
	sealedKey, err := objectstore.SealedKey()
	if err != nil {
		return UploadTicket{}, err
	}
	artifactID := uuid.NewString()

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return UploadTicket{}, fmt.Errorf("starting the upload transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
			(id, owner_id, object_key, staging_object_key, purpose, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, 'uploading', 1)`,
		artifactID, ownerID, sealedKey, stagingKey, normalized.Purpose,
		normalized.MediaType, normalized.SizeBytes,
	); err != nil {
		return UploadTicket{}, fmt.Errorf("reserving the artifact: %w", err)
	}
	url, expiresAt, err := s.Signer.Sign(
		objectstore.OpPut, stagingKey, artifactID, normalized.SizeBytes,
	)
	if err != nil {
		return UploadTicket{}, fmt.Errorf("minting the upload link: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return UploadTicket{}, fmt.Errorf("committing the upload reservation: %w", err)
	}
	return UploadTicket{
		ArtifactID: artifactID,
		URL:        s.BaseURL + "/object-links/" + url,
		ExpiresAt:  expiresAt,
	}, nil
}

// CompleteUpload applies the uploader's declared SHA-256.
//
// The first completion is a CAS uploading -> scanning (revision bump) plus one
// artifact_finalize job in the same transaction. Repeating the same hash in any
// later state is idempotent and never creates a second job; a different hash is
// 409 artifact_hash_conflict. Completion never activates the artifact and never
// inspects the object store: a missing/not-uploaded staging object is discovered
// by the finalize handler, not guessed here.
func (s *Service) CompleteUpload(
	ctx context.Context,
	ownerID, artifactID, declaredSHA256 string,
) (Artifact, error) {
	if !uuid.Valid(strings.TrimSpace(artifactID)) {
		return Artifact{}, ErrNotVisible
	}
	sha := strings.TrimSpace(declaredSHA256)
	if !isLowerSHA256(sha) {
		return Artifact{}, &ValidationError{
			Field:   "sha256",
			Message: "sha256 must be 64 lowercase hexadecimal characters",
		}
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return Artifact{}, fmt.Errorf("starting the completion transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var (
		state     string
		expected  sql.NullString
		deleted   sql.NullTime
		revision  int64
		mediaType string
		sizeBytes sql.NullInt64
	)
	err = tx.QueryRowContext(ctx, `
		SELECT state, expected_sha256, deleted_at, revision, media_type, size_bytes
		  FROM platform.artifacts
		 WHERE id = $1::uuid AND owner_id = $2::uuid
		 FOR UPDATE`, artifactID, ownerID,
	).Scan(&state, &expected, &deleted, &revision, &mediaType, &sizeBytes)
	if errors.Is(err, sql.ErrNoRows) {
		return Artifact{}, ErrNotVisible
	}
	if err != nil {
		return Artifact{}, fmt.Errorf("reading the artifact: %w", err)
	}
	if deleted.Valid || state == "deleted" {
		return Artifact{}, &ConflictError{
			Code:    CodeArtifactConflict,
			Message: "the artifact is deleted",
		}
	}

	view := Artifact{
		ID: artifactID, State: state, MediaType: mediaType, SizeBytes: sizeBytes.Int64,
	}
	switch state {
	case "uploading":
		nextRevision := revision + 1
		result, err := tx.ExecContext(ctx, `
			UPDATE platform.artifacts
			   SET state = 'scanning', expected_sha256 = $3, revision = revision + 1
			 WHERE id = $1::uuid AND owner_id = $2::uuid
			   AND state = 'uploading' AND revision = $4 AND deleted_at IS NULL`,
			artifactID, ownerID, sha, revision,
		)
		if err != nil {
			return Artifact{}, fmt.Errorf("recording the upload hash: %w", err)
		}
		affected, err := result.RowsAffected()
		if err != nil {
			return Artifact{}, fmt.Errorf("recording the upload hash: %w", err)
		}
		if affected != 1 {
			return Artifact{}, &ConflictError{
				Code:    CodeArtifactConflict,
				Message: "the artifact changed while completing",
			}
		}
		if _, _, err := background.EnqueueTx(ctx, tx, background.EnqueueParams{
			Kind:             background.KindArtifactFinalize,
			TargetID:         artifactID,
			OwnerID:          ownerID,
			DedupKey:         finalizeDedupKey(artifactID, sha),
			ExpectedRevision: &nextRevision,
			ExpectedSHA256:   sha,
			InputArtifactID:  artifactID,
		}); err != nil {
			return Artifact{}, err
		}
		if err := tx.Commit(); err != nil {
			return Artifact{}, fmt.Errorf("committing the completion: %w", err)
		}
		view.State = "scanning"
		return view, nil

	case "scanning", "ready", "quarantined":
		recorded := ""
		if expected.Valid {
			recorded = strings.ToLower(strings.TrimSpace(expected.String))
		}
		if recorded != sha {
			return Artifact{}, &ConflictError{
				Code:    CodeHashConflict,
				Message: "a different sha256 was already recorded for this upload",
			}
		}
		if state == "quarantined" {
			return Artifact{}, &ConflictError{
				Code:    CodeArtifactConflict,
				Message: "the upload is quarantined and cannot be completed again",
			}
		}
		// Same hash in scanning/ready: the operation already happened. The one
		// finalize job is found by the stable dedup key; no second job is
		// written and the response has the same four-field shape.
		if err := tx.Commit(); err != nil {
			return Artifact{}, fmt.Errorf("committing the idempotent completion: %w", err)
		}
		return view, nil

	default:
		return Artifact{}, &ConflictError{
			Code:    CodeArtifactConflict,
			Message: "the artifact is not in a completable state",
		}
	}
}

// finalizeDedupKey names one artifact+hash intention, so a retried complete with
// the same hash can never create a second artifact_finalize job.
func finalizeDedupKey(artifactID, sha string) string {
	return "artifact:" + artifactID + ":" + sha
}

// Get returns one artifact to its owner. A deleted artifact is invisible; a
// quarantined artifact is visible (the owner must see the verdict) and carries
// the safe admission reason for the documented response header.
func (s *Service) Get(ctx context.Context, ownerID, artifactID string) (Artifact, error) {
	if !uuid.Valid(strings.TrimSpace(artifactID)) {
		return Artifact{}, ErrNotVisible
	}
	var (
		view    Artifact
		deleted sql.NullTime
		size    sql.NullInt64
		reason  sql.NullString
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT a.id::text, a.state, a.media_type, a.size_bytes, a.deleted_at, ad.reason
		  FROM platform.artifacts AS a
		  LEFT JOIN platform.artifact_admissions AS ad ON ad.artifact_id = a.id
		 WHERE a.id = $1::uuid AND a.owner_id = $2::uuid`,
		artifactID, ownerID,
	).Scan(&view.ID, &view.State, &view.MediaType, &size, &deleted, &reason)
	if errors.Is(err, sql.ErrNoRows) {
		return Artifact{}, ErrNotVisible
	}
	if err != nil {
		return Artifact{}, fmt.Errorf("reading the artifact: %w", err)
	}
	if deleted.Valid {
		return Artifact{}, ErrNotVisible
	}
	view.SizeBytes = size.Int64
	if reason.Valid {
		value := reason.String
		view.QuarantineReason = &value
	}
	return view, nil
}

// DownloadURL mints a sealed GET link for a ready, owner-visible artifact.
func (s *Service) DownloadURL(
	ctx context.Context,
	ownerID, artifactID string,
) (string, time.Time, error) {
	row, err := s.readyRow(ctx, ownerID, artifactID)
	if err != nil {
		return "", time.Time{}, err
	}
	if s.Objects == nil {
		return "", time.Time{}, ErrObjectStoreUnavailable
	}
	if s.Signer == nil {
		return "", time.Time{}, ErrSigningUnavailable
	}
	url, expiresAt, err := s.Signer.Sign(
		objectstore.OpGet, row.objectKey, artifactID, row.sizeBytes,
	)
	if err != nil {
		return "", time.Time{}, fmt.Errorf("minting the download link: %w", err)
	}
	return s.BaseURL + "/object-links/" + url, expiresAt, nil
}

// OpenContent opens the sealed object of a ready artifact for streaming. It
// returns a reader, never a byte slice: the API copies the object to the
// response without loading up to 20 MiB into memory.
func (s *Service) OpenContent(
	ctx context.Context,
	ownerID, artifactID string,
) (io.ReadCloser, int64, error) {
	row, err := s.readyRow(ctx, ownerID, artifactID)
	if err != nil {
		return nil, 0, err
	}
	if s.Objects == nil {
		return nil, 0, ErrObjectStoreUnavailable
	}
	reader, size, err := s.Objects.Open(row.objectKey)
	if errors.Is(err, objectstore.ErrNotExist) || errors.Is(err, objectstore.ErrInvalidKey) {
		// The row says ready but the sealed bytes are gone. The content endpoint
		// must not read from anywhere else, and the caller learns nothing about
		// the store; report the resource as unavailable.
		return nil, 0, ErrNotVisible
	}
	if err != nil {
		return nil, 0, fmt.Errorf("opening the artifact bytes: %w", err)
	}
	return reader, size, nil
}

type readyArtifactRow struct {
	objectKey string
	sizeBytes int64
}

// readyRow enforces the one owner-only read rule used by download/content:
// missing, another owner's, deleted and quarantined are all ErrNotVisible (404);
// uploading/scanning are ErrNotReady (409).
func (s *Service) readyRow(
	ctx context.Context,
	ownerID, artifactID string,
) (readyArtifactRow, error) {
	if !uuid.Valid(strings.TrimSpace(artifactID)) {
		return readyArtifactRow{}, ErrNotVisible
	}
	var (
		row     readyArtifactRow
		state   string
		deleted sql.NullTime
		size    sql.NullInt64
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT object_key, state, deleted_at, size_bytes
		  FROM platform.artifacts
		 WHERE id = $1::uuid AND owner_id = $2::uuid`,
		artifactID, ownerID,
	).Scan(&row.objectKey, &state, &deleted, &size)
	if errors.Is(err, sql.ErrNoRows) {
		return readyArtifactRow{}, ErrNotVisible
	}
	if err != nil {
		return readyArtifactRow{}, fmt.Errorf("reading the artifact: %w", err)
	}
	if deleted.Valid || state == "deleted" || state == "quarantined" {
		return readyArtifactRow{}, ErrNotVisible
	}
	if state != "ready" {
		return readyArtifactRow{}, ErrNotReady
	}
	row.sizeBytes = size.Int64
	return row, nil
}

func isLowerSHA256(value string) bool {
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
