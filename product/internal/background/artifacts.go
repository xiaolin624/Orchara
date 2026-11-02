package background

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// ArtifactStore owns the job-scoped artifact rules fixed by T024:
//
//   - a job may create an artifact only for the kinds that produce controlled
//     output (model_validate now, application_evaluate/T026 next);
//   - an upload is reserved as an `uploading` row with a random staging key and
//     a random preallocated sealed key, then completed with a CAS
//     uploading -> scanning and an artifact_finalize job in the same
//     transaction;
//   - a job may read an artifact only when it is that job's input, its result,
//     or linked to it in platform.background_job_artifacts;
//   - bytes move only through signed links (PUT staging / GET sealed), never
//     through a path, URL or database read chosen by the worker.
//
// The row-level writes are the platform's, not the handler's: activation is
// still the finalize guard's decision, and this package never sets ready.
type ArtifactStore struct {
	DB      *sql.DB
	Objects *objectstore.Store
	Signer  *objectstore.Signer
	// BaseURL is the absolute public base URL signed links are minted against
	// (ORCHARA_PUBLIC_URL). It is what makes upload_url/input_url usable by
	// another process.
	BaseURL string
	// Notifier is the best-effort wakeup hook called after the finalize job is
	// committed. It is never required for delivery; nil means NopNotifier.
	Notifier Notifier
}

// NewArtifactStore builds the service. Objects and Signer may be nil in a
// process that never moves bytes; every byte operation then fails closed.
// notifier may be nil: job creation is durable regardless of whether a wakeup
// can be delivered.
func NewArtifactStore(
	db *sql.DB,
	objects *objectstore.Store,
	signer *objectstore.Signer,
	baseURL string,
	notifier Notifier,
) *ArtifactStore {
	if notifier == nil {
		notifier = NopNotifier{}
	}
	return &ArtifactStore{
		DB: db, Objects: objects, Signer: signer,
		BaseURL:  strings.TrimSuffix(strings.TrimSpace(baseURL), "/"),
		Notifier: notifier,
	}
}

// Errors callers map to HTTP answers.
var (
	// ErrArtifactNotFound is "not in this job's scope or does not exist". The
	// two are deliberately indistinguishable so the endpoint cannot enumerate
	// another job's artifacts.
	ErrArtifactNotFound = errors.New("artifact not found for this job")
	// ErrSigningUnavailable means ORCHARA_OBJECT_SIGNING_KEY is empty, so no
	// transfer link can be minted.
	ErrSigningUnavailable = errors.New("object link signing is not configured")
	// ErrArtifactConflict is a completion that disagrees with the recorded
	// upload intent (different hash, tombstone, or an unsupported state).
	ErrArtifactConflict = errors.New("artifact upload conflicts with the recorded intent")
	// ErrArtifactNotReady means the artifact exists in scope but cannot be
	// downloaded yet.
	ErrArtifactNotReady = errors.New("artifact is not ready")
)

// ArtifactConflictError carries a stable reason while matching
// ErrArtifactConflict with errors.Is.
type ArtifactConflictError struct{ Reason string }

func (e *ArtifactConflictError) Error() string { return "artifact conflict: " + e.Reason }
func (e *ArtifactConflictError) Unwrap() error { return ErrArtifactConflict }

// artifactProducerKinds is the closed set of kinds allowed to reserve uploads.
// IMPLEMENTATION-DETAILS §9 and BACKGROUND-JOBS §2 name these; a later ticket
// that gives another kind an artifact output extends this map in the same
// change instead of relaxing the check.
var artifactProducerKinds = map[Kind]bool{
	KindModelValidate:       true,
	KindApplicationEvaluate: true,
	// T030: the trusted preset tool registers its summary, CSV, diagnostics and
	// capped log through the same job-scoped upload flow.
	KindToolRun: true,
}

// uploadPurposes is the contract's createBackgroundUpload purpose enum plus the
// T030 tool-run purposes. `tool_summary` is the result artifact (verified by the
// tool_run completion guard), `tool_artifact` is the CSV, `diagnostics` is the
// safe rejection document and `log` is the capped stdout/stderr.
var uploadPurposes = map[string]bool{
	"report":        true,
	"log":           true,
	"receipt":       true,
	"parsed_data":   true,
	"tool_summary":  true,
	"tool_artifact": true,
	"diagnostics":   true,
}

// CreateUpload reserves one artifact for a job. It writes the artifact row and
// the job->artifact provenance in one transaction, and returns a signed PUT link
// that targets exactly the reserved staging key with the declared size ceiling.
func (s *ArtifactStore) CreateUpload(
	ctx context.Context,
	job *Job,
	request UploadRequest,
) (UploadTicket, error) {
	if job == nil {
		return UploadTicket{}, ErrNotFound
	}
	if !artifactProducerKinds[job.Kind] {
		return UploadTicket{}, &ValidationError{
			Field:   "job_id",
			Message: "this background job kind may not create artifacts",
		}
	}
	mediaType := strings.TrimSpace(request.MediaType)
	if mediaType == "" || len(mediaType) > 255 || strings.ContainsAny(mediaType, "\r\n\x00") {
		return UploadTicket{}, &ValidationError{Field: "media_type", Message: "media_type is required"}
	}
	purpose := strings.TrimSpace(request.Purpose)
	if !uploadPurposes[purpose] {
		return UploadTicket{}, &ValidationError{
			Field: "purpose",
			Message: "purpose must be report, log, receipt, parsed_data, tool_summary, " +
				"tool_artifact or diagnostics",
		}
	}
	if request.SizeBytes < 0 || request.SizeBytes > objectstore.MaxObjectBytes {
		return UploadTicket{}, &ValidationError{
			Field:   "size_bytes",
			Message: fmt.Sprintf("size_bytes must be between 0 and %d", objectstore.MaxObjectBytes),
		}
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
		artifactID, job.OwnerID, sealedKey, stagingKey, purpose, mediaType, request.SizeBytes,
	); err != nil {
		return UploadTicket{}, fmt.Errorf("reserving the artifact: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.background_job_artifacts
			(background_job_id, artifact_id, relation, purpose)
		VALUES ($1::uuid, $2::uuid, 'output', $3)`,
		job.ID, artifactID, purpose,
	); err != nil {
		return UploadTicket{}, fmt.Errorf("linking the artifact to the job: %w", err)
	}
	url, expiresAt, err := s.Signer.Sign(objectstore.OpPut, stagingKey, artifactID, request.SizeBytes)
	if err != nil {
		return UploadTicket{}, fmt.Errorf("minting the upload link: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return UploadTicket{}, fmt.Errorf("committing the upload reservation: %w", err)
	}
	return UploadTicket{ArtifactID: artifactID, URL: s.linkURL(url), ExpiresAt: expiresAt}, nil
}

// linkURL turns a signed token into the absolute transfer URL a worker can
// reach. The token is URL-safe base64 plus '.', so it needs no escaping.
func (s *ArtifactStore) linkURL(token string) string {
	return s.BaseURL + "/object-links/" + token
}

// CompleteUpload applies the uploader's declared SHA-256.
//
// The first completion moves uploading -> scanning (revision bump) and creates
// the artifact_finalize job in the same transaction. Replaying the same hash is
// idempotent and re-runs the deduplicated enqueue (so a duplicate job cannot
// appear); a different hash or a tombstoned artifact is a conflict. The finalize
// guard, not this method, is what can make the artifact ready.
func (s *ArtifactStore) CompleteUpload(
	ctx context.Context,
	job *Job,
	artifactID, declaredSHA256 string,
) (ArtifactView, error) {
	if job == nil {
		return ArtifactView{}, ErrNotFound
	}
	if !uuid.Valid(strings.TrimSpace(artifactID)) {
		// A malformed id is not a database error and must not reveal whether a
		// row exists: answer exactly like an out-of-scope artifact.
		return ArtifactView{}, ErrArtifactNotFound
	}
	sha := strings.ToLower(strings.TrimSpace(declaredSHA256))
	if !isSHA256Hex(sha) {
		return ArtifactView{}, &ValidationError{
			Field:   "sha256",
			Message: "sha256 must be 64 lowercase hexadecimal characters",
		}
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return ArtifactView{}, fmt.Errorf("starting the completion transaction: %w", err)
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
		SELECT a.state, a.expected_sha256, a.deleted_at, a.revision, a.media_type, a.size_bytes
		  FROM platform.artifacts AS a
		  JOIN platform.background_job_artifacts AS l
		    ON l.artifact_id = a.id AND l.background_job_id = $2::uuid
		 WHERE a.id = $1::uuid
		 FOR UPDATE OF a`,
		artifactID, job.ID,
	).Scan(&state, &expected, &deleted, &revision, &mediaType, &sizeBytes)
	if errors.Is(err, sql.ErrNoRows) {
		return ArtifactView{}, ErrArtifactNotFound
	}
	if err != nil {
		return ArtifactView{}, fmt.Errorf("reading the artifact: %w", err)
	}
	if deleted.Valid || state == "deleted" {
		return ArtifactView{}, &ArtifactConflictError{Reason: "the artifact is deleted"}
	}

	view := ArtifactView{ID: artifactID, State: state, MediaType: mediaType, SizeBytes: sizeBytes.Int64}
	switch state {
	case "uploading":
		nextRevision := revision + 1
		result, err := tx.ExecContext(ctx, `
			UPDATE platform.artifacts
			   SET state = 'scanning', expected_sha256 = $2, revision = revision + 1
			 WHERE id = $1::uuid AND state = 'uploading' AND revision = $3`,
			artifactID, sha, revision,
		)
		if err != nil {
			return ArtifactView{}, fmt.Errorf("recording the upload hash: %w", err)
		}
		affected, err := result.RowsAffected()
		if err != nil {
			return ArtifactView{}, fmt.Errorf("recording the upload hash: %w", err)
		}
		if affected != 1 {
			return ArtifactView{}, &ArtifactConflictError{Reason: "the artifact changed while completing"}
		}
		finalize, _, err := EnqueueTx(ctx, tx, EnqueueParams{
			Kind:             KindArtifactFinalize,
			TargetID:         artifactID,
			OwnerID:          job.OwnerID,
			DedupKey:         finalizeDedupKey(artifactID, sha),
			ExpectedRevision: &nextRevision,
			ExpectedSHA256:   sha,
			InputArtifactID:  artifactID,
		})
		if err != nil {
			return ArtifactView{}, err
		}
		if err := tx.Commit(); err != nil {
			return ArtifactView{}, fmt.Errorf("committing the completion: %w", err)
		}
		notify(ctx, s.Notifier, finalize)
		view.State = "scanning"
		return view, nil

	case "scanning", "ready", "quarantined":
		recorded := ""
		if expected.Valid {
			recorded = strings.ToLower(strings.TrimSpace(expected.String))
		}
		if recorded != sha {
			return ArtifactView{}, &ArtifactConflictError{Reason: "a different sha256 was already recorded"}
		}
		// Same hash: keep the answer idempotent and prove the finalize job is
		// deduplicated rather than re-created. If the artifact has already moved
		// on (ready/quarantined), the recorded job's expected revision no longer
		// matches this row, so a dedup conflict here is expected and not an
		// error: the completion is still the same intention.
		finalize, _, err := EnqueueTx(ctx, tx, EnqueueParams{
			Kind:             KindArtifactFinalize,
			TargetID:         artifactID,
			OwnerID:          job.OwnerID,
			DedupKey:         finalizeDedupKey(artifactID, sha),
			ExpectedRevision: revisionPointer(revision),
			ExpectedSHA256:   sha,
			InputArtifactID:  artifactID,
		})
		if err != nil && !errors.Is(err, ErrDedupConflict) {
			return ArtifactView{}, err
		}
		if err := tx.Commit(); err != nil {
			return ArtifactView{}, fmt.Errorf("committing the idempotent completion: %w", err)
		}
		if finalize != nil {
			notify(ctx, s.Notifier, finalize)
		}
		return view, nil

	default:
		return ArtifactView{}, &ArtifactConflictError{
			Reason: "the artifact is not in an uploadable state",
		}
	}
}

// finalizeDedupKey names one artifact+hash intention. A retried complete with
// the same hash deduplicates; a different hash is a different intention (and is
// refused before it can become a second finalize job).
func finalizeDedupKey(artifactID, sha string) string {
	return "artifact:" + artifactID + ":" + sha
}

func revisionPointer(revision int64) *int64 {
	value := revision
	return &value
}

// sha256Hex is the server-side hash of bytes this process just read. It is
// never compared against a client-declared value without the finalize guard
// re-reading the sealed object later.
func sha256Hex(content []byte) string {
	sum := sha256.Sum256(content)
	return hex.EncodeToString(sum[:])
}

// ArtifactStatus returns one in-scope artifact's state without any bytes.
func (s *ArtifactStore) ArtifactStatus(
	ctx context.Context,
	job *Job,
	artifactID string,
) (ArtifactView, error) {
	row, err := s.scopedArtifact(ctx, job, artifactID)
	if err != nil {
		return ArtifactView{}, err
	}
	return row.view, nil
}

// DownloadURL mints a sealed GET link for a ready, in-scope artifact.
func (s *ArtifactStore) DownloadURL(
	ctx context.Context,
	job *Job,
	artifactID string,
) (string, time.Time, error) {
	row, err := s.scopedArtifact(ctx, job, artifactID)
	if err != nil {
		return "", time.Time{}, err
	}
	if row.state != "ready" {
		return "", time.Time{}, ErrArtifactNotReady
	}
	if s.Objects == nil {
		return "", time.Time{}, ErrObjectStoreUnavailable
	}
	if s.Signer == nil {
		return "", time.Time{}, ErrSigningUnavailable
	}
	url, expiresAt, err := s.Signer.Sign(objectstore.OpGet, row.objectKey, artifactID, row.size)
	if err != nil {
		return "", time.Time{}, fmt.Errorf("minting the download link: %w", err)
	}
	return s.linkURL(url), expiresAt, nil
}

// InputURL mints the contract's BackgroundLease.input_url for a job input.
// It returns nil (not an error) when there is no input artifact, the artifact is
// not ready yet, or this process cannot sign links: the claim still succeeds and
// the handler can ask the job-scoped endpoint later.
func (s *ArtifactStore) InputURL(ctx context.Context, artifactID string) *string {
	if strings.TrimSpace(artifactID) == "" {
		return nil
	}
	var (
		objectKey string
		state     string
		deleted   sql.NullTime
		size      sql.NullInt64
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT object_key, state, deleted_at, size_bytes
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, artifactID,
	).Scan(&objectKey, &state, &deleted, &size)
	if err != nil || deleted.Valid || state != "ready" {
		return nil
	}
	if s.Signer == nil || s.Objects == nil {
		return nil
	}
	limit := size.Int64
	if limit < 0 || limit > objectstore.MaxObjectBytes {
		limit = 0
	}
	url, _, err := s.Signer.Sign(objectstore.OpGet, objectKey, artifactID, limit)
	if err != nil {
		return nil
	}
	link := s.linkURL(url)
	return &link
}

// ObjectBytes is a server-side read of a ready sealed object.
type ObjectBytes struct {
	Content   []byte
	SHA256    string
	Size      int64
	MediaType string
	Purpose   string
	OwnerID   string
}

// artifactMetadata is the artifact row facts the server checks before it opens
// any bytes. Keeping the metadata read separate from the byte read is what lets
// a non-fixture purpose be refused without first loading business content into
// the API process.
type artifactMetadata struct {
	ObjectKey string
	State     string
	Deleted   sql.NullTime
	Size      sql.NullInt64
	MediaType string
	Purpose   string
	OwnerID   string
}

func (s *ArtifactStore) readArtifactMetadata(
	ctx context.Context,
	artifactID string,
) (artifactMetadata, error) {
	var metadata artifactMetadata
	err := s.DB.QueryRowContext(ctx, `
		SELECT object_key, state, deleted_at, size_bytes, media_type, purpose, owner_id::text
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, artifactID,
	).Scan(
		&metadata.ObjectKey, &metadata.State, &metadata.Deleted, &metadata.Size,
		&metadata.MediaType, &metadata.Purpose, &metadata.OwnerID,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return artifactMetadata{}, ErrArtifactNotFound
	}
	if err != nil {
		return artifactMetadata{}, fmt.Errorf("reading the artifact: %w", err)
	}
	return metadata, nil
}

// readObjectBytes opens the sealed object of an already-authorized metadata row.
func (s *ArtifactStore) readObjectBytes(metadata artifactMetadata) (ObjectBytes, error) {
	if s.Objects == nil {
		return ObjectBytes{}, ErrObjectStoreUnavailable
	}
	reader, storedSize, err := s.Objects.Open(metadata.ObjectKey)
	if errors.Is(err, objectstore.ErrNotExist) || errors.Is(err, objectstore.ErrInvalidKey) {
		return ObjectBytes{}, ErrArtifactNotFound
	}
	if err != nil {
		return ObjectBytes{}, fmt.Errorf("opening the artifact bytes: %w", err)
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, objectstore.MaxObjectBytes+1))
	if err != nil {
		return ObjectBytes{}, fmt.Errorf("reading the artifact bytes: %w", err)
	}
	if int64(len(content)) > objectstore.MaxObjectBytes {
		return ObjectBytes{}, objectstore.ErrTooLarge
	}
	return ObjectBytes{
		Content:   content,
		SHA256:    sha256Hex(content),
		Size:      storedSize,
		MediaType: metadata.MediaType,
		Purpose:   metadata.Purpose,
		OwnerID:   metadata.OwnerID,
	}, nil
}

// ReadReady reads a ready, non-deleted artifact's sealed bytes for a server-side
// decision. There is no job scope here on purpose: the caller is the internal
// endpoint, which has already proven which job it is acting for and passes only
// that job's input artifact id.
func (s *ArtifactStore) ReadReady(ctx context.Context, artifactID string) (ObjectBytes, error) {
	metadata, err := s.readArtifactMetadata(ctx, artifactID)
	if err != nil {
		return ObjectBytes{}, err
	}
	if metadata.Deleted.Valid || metadata.State != "ready" {
		return ObjectBytes{}, ErrArtifactNotReady
	}
	return s.readObjectBytes(metadata)
}

// ReadSyntheticFixture reads the job's model fixture after proving it really is
// a server-registered synthetic fixture owned by this job's owner.
//
// The purpose and ownership checks happen before any bytes are opened: a
// business artifact (report/log/receipt/parsed_data) must be refused even though
// the endpoint could technically read it, because T024's boundary is "never try
// a model on user content". A job that names another principal's artifact gets
// the same fixture_missing answer as a job without a fixture, so the endpoint
// cannot be used to probe artifact ids.
func (s *ArtifactStore) ReadSyntheticFixture(
	ctx context.Context,
	artifactID, ownerID string,
) (ObjectBytes, error) {
	metadata, err := s.readArtifactMetadata(ctx, artifactID)
	if err != nil {
		return ObjectBytes{}, err
	}
	if metadata.Purpose != PurposeModelFixture {
		return ObjectBytes{}, fmt.Errorf("%w: purpose is %q", ErrFixtureNotSynthetic, metadata.Purpose)
	}
	if !strings.EqualFold(metadata.OwnerID, ownerID) {
		return ObjectBytes{}, ErrFixtureNotOwned
	}
	if metadata.Deleted.Valid || metadata.State != "ready" {
		return ObjectBytes{}, ErrArtifactNotReady
	}
	return s.readObjectBytes(metadata)
}

// SignedSealedURL is used by the claim projection: it returns a link for any
// ready sealed artifact without a job-scope check because the caller already
// holds the job row. It is InputURL under another name for readability.
func (s *ArtifactStore) SignedSealedURL(ctx context.Context, artifactID string) *string {
	return s.InputURL(ctx, artifactID)
}

type scopedArtifactRow struct {
	view      ArtifactView
	state     string
	objectKey string
	size      int64
}

// scopedArtifact resolves one artifact and enforces the job-scope rule in one
// statement: input, result, or linked provenance. A miss is ErrArtifactNotFound,
// the same answer for "does not exist" and "not yours".
func (s *ArtifactStore) scopedArtifact(
	ctx context.Context,
	job *Job,
	artifactID string,
) (scopedArtifactRow, error) {
	if job == nil {
		return scopedArtifactRow{}, ErrArtifactNotFound
	}
	if !uuid.Valid(strings.TrimSpace(artifactID)) {
		return scopedArtifactRow{}, ErrArtifactNotFound
	}
	inputID := ""
	if job.InputArtifactID != nil {
		inputID = *job.InputArtifactID
	}
	resultID := ""
	if job.ResultArtifactID != nil {
		resultID = *job.ResultArtifactID
	}
	var (
		row      scopedArtifactRow
		deleted  sql.NullTime
		size     sql.NullInt64
		mediaTyp string
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT a.id::text, a.state, a.media_type, a.size_bytes, a.object_key, a.deleted_at
		  FROM platform.artifacts AS a
		 WHERE a.id = $1::uuid
		   AND (
		        a.id = NULLIF($2, '')::uuid
		     OR a.id = NULLIF($3, '')::uuid
		     OR EXISTS (
		          SELECT 1 FROM platform.background_job_artifacts AS l
		           WHERE l.background_job_id = $4::uuid AND l.artifact_id = a.id
		        )
		   )`,
		artifactID, inputID, resultID, job.ID,
	).Scan(&row.view.ID, &row.state, &mediaTyp, &size, &row.objectKey, &deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return scopedArtifactRow{}, ErrArtifactNotFound
	}
	if err != nil {
		return scopedArtifactRow{}, fmt.Errorf("reading the scoped artifact: %w", err)
	}
	if deleted.Valid {
		return scopedArtifactRow{}, ErrArtifactNotFound
	}
	row.view.State = row.state
	row.view.MediaType = mediaTyp
	// artifacts.size_bytes is nullable in the contract while Artifact.size_bytes
	// is required; an unset size can only be reported as 0 (T024 never creates
	// such a row, but a seeded one must not crash the read model).
	row.view.SizeBytes = size.Int64
	row.size = size.Int64
	return row, nil
}
