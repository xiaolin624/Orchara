package execution

// F05/T033: run-bound artifact access for the execution protocol.
//
// The job-scoped flow (T024/T030) lets a maintenance handler move its own
// artifacts. A Run is different: the supervisor claims the attempt, the worker
// uploads, and the platform must be able to prove which attempt produced which
// artifact. This file implements the six contract operations:
//
//	createWorkerUpload     reserve one artifact for the live attempt + run
//	completeWorkerUpload   CAS uploading->scanning + one artifact_finalize job
//	createWorkerDownload   mint a sealed GET for this run's input artifact only
//	getWorkerArtifactStatus one artifact this attempt uploaded, or a run input
//	registerRunArtifact    link a ready artifact to the run (live attempt only)
//	(plus the owner-facing list, which lives in catalog with the Run projection)
//
// Two invariants shape every method:
//
//   - **The token is the credential and the database decides liveness.** Every
//     call resolves the token, the run in the path and the job's current token in
//     one statement; a superseded attempt, a revoked/exited token, an expired
//     lease, a foreign run or a terminal run is refused before any business row
//     is read. Nothing trusts a caller-supplied run id.
//   - **Provenance is the scope.** An artifact is visible to a run only when the
//     run input relation says so or `run_artifact_uploads` names this run and
//     attempt. A foreign artifact therefore answers exactly like a missing one
//     (404), so the endpoint cannot enumerate artifact ids.
//
// The rows this package writes are the platform's, not the worker's: the worker
// never sets `ready` (the artifact_finalize guard does) and never names an object
// key (only signed staging/sealed links move bytes).

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Run artifact purposes are exactly the contract createWorkerUpload enum. The
// purpose decides the run semantics: `log` is stdout/stderr (listed by purpose,
// never treated as run data), everything else is a produced artifact.
const (
	RunArtifactPurposeNodeOutput  = "node_output"
	RunArtifactPurposeFinalOutput = "final_output"
	RunArtifactPurposeLog         = "log"
	RunArtifactPurposeReceipt     = "receipt"
)

var runArtifactPurposes = map[string]bool{
	RunArtifactPurposeNodeOutput:  true,
	RunArtifactPurposeFinalOutput: true,
	RunArtifactPurposeLog:         true,
	RunArtifactPurposeReceipt:     true,
}

// runArtifactRelations is the closed relation vocabulary registerRunArtifact may
// write. It is not the contract enum (the contract only says `string`), so the
// server closes it; a caller inventing `relation=foo` gets 422 instead of an
// unqueryable run link.
var runArtifactRelations = map[string]bool{
	"output":   true,
	"artifact": true,
	"log":      true,
}

// runArtifactMediaTypes is the closed upload allowlist for run-scoped artifacts.
// These are the data formats a trusted preset tool may produce; everything else
// (for example a shell archive) is refused before an object is reserved.
var runArtifactMediaTypes = map[string]bool{
	"application/json": true,
	"text/csv":         true,
	"text/plain":       true,
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": true,
}

// Errors the HTTP layer maps to contract codes. ErrStaleAttempt,
// ErrRunTerminal and ErrRunStateConflict are shared with the other execution
// operations; these three are run-artifact specific.
var (
	// ErrRunArtifactNotFound is 404 artifact_not_found: no artifact in this
	// run's scope. It is the same answer for "does not exist", "another run's
	// artifact" and "another attempt's upload".
	ErrRunArtifactNotFound = errors.New("artifact not found for this run")
	// ErrArtifactNotReady is 409 artifact_not_ready: the artifact exists in
	// scope but is still uploading/scanning or was quarantined/deleted.
	ErrArtifactNotReady = errors.New("the run artifact is not ready")
	// ErrRunArtifactConflict is 409 artifact_conflict: a completion or
	// registration disagrees with what the artifact already recorded.
	ErrRunArtifactConflict = errors.New("run artifact conflict")
)

// RunArtifactUploadRequest is the contract createWorkerUpload body, normalised.
type RunArtifactUploadRequest struct {
	MediaType string
	SizeBytes int64
	Purpose   string
}

// RunArtifactTicket is the contract Upload response.
type RunArtifactTicket struct {
	ArtifactID string
	URL        string
	ExpiresAt  time.Time
}

// RunArtifactView is the contract Artifact response (exactly four fields).
type RunArtifactView struct {
	ID        string
	State     string
	MediaType string
	SizeBytes int64
}

// RunArtifactStore owns the run-scoped artifact rules.
type RunArtifactStore struct {
	DB      *sql.DB
	Objects *objectstore.Store
	Signer  *objectstore.Signer
	// BaseURL is the absolute public base URL signed links are minted against.
	BaseURL string
	// Notifier is the best-effort wakeup after an artifact_finalize enqueue.
	Notifier background.Notifier
}

// NewRunArtifactStore builds the service. Objects/Signer may be nil in a process
// that never moves bytes; every byte operation then fails closed.
func NewRunArtifactStore(
	db *sql.DB, objects *objectstore.Store, signer *objectstore.Signer,
	baseURL string, notifier background.Notifier,
) *RunArtifactStore {
	if notifier == nil {
		notifier = background.NopNotifier{}
	}
	return &RunArtifactStore{
		DB: db, Objects: objects, Signer: signer,
		BaseURL:  strings.TrimSuffix(strings.TrimSpace(baseURL), "/"),
		Notifier: notifier,
	}
}

// runAttemptScope is the verified (run, attempt, owner) triple of one call.
type runAttemptScope struct {
	RunID    string
	OwnerID  string
	Token    string
	JobID    string
	RunState string
}

// resolveScope proves the attempt token is the live attempt of the run in the
// path. The single statement binds token+run+job and asks the database whether
// the lease is live; a superseded attempt is simply not the job's current token.
func (s *RunArtifactStore) resolveScope(ctx context.Context, runID, token string) (*runAttemptScope, error) {
	return s.resolveScopeWith(ctx, runID, token, false)
}

// RunOwner re-proves the live attempt token of the run in the path and returns the
// run's owner id. It is the trusted-identity seam for read-only internal calls that
// act on behalf of the run (F04B/T088's knowledge retrieval): the owner comes from
// the runs row, never from the caller's body.
func (s *RunArtifactStore) RunOwner(ctx context.Context, runID, token string) (string, error) {
	scope, err := s.resolveScope(ctx, runID, token)
	if err != nil {
		return "", err
	}
	return scope.OwnerID, nil
}

// resolveScopeAllowCancelling is the receipt/query variant: completing an upload
// that was already in flight, asking for a status and minting an input download
// remain allowed while the Run is cancelling (only new execution-bearing calls
// are refused). It never allows a terminal Run.
func (s *RunArtifactStore) resolveScopeAllowCancelling(
	ctx context.Context, runID, token string,
) (*runAttemptScope, error) {
	return s.resolveScopeWith(ctx, runID, token, true)
}

func (s *RunArtifactStore) resolveScopeWith(
	ctx context.Context, runID, token string, allowCancelling bool,
) (*runAttemptScope, error) {
	if s == nil || s.DB == nil {
		return nil, fmt.Errorf("run artifact store is not configured")
	}
	if !uuid.Valid(strings.TrimSpace(runID)) || !uuid.Valid(strings.TrimSpace(token)) {
		// A malformed id is not a database error and must not reveal anything.
		return nil, ErrStaleAttempt
	}
	var scope runAttemptScope
	err := s.DB.QueryRowContext(ctx, `
		SELECT a.run_id::text, r.owner_id::text, a.token::text, a.job_id::text, r.state
		  FROM platform.run_attempts AS a
		  JOIN platform.runs AS r ON r.id = a.run_id
		  JOIN platform.jobs AS j ON j.id = a.job_id AND j.run_id = a.run_id
		 WHERE a.token = $2::uuid
		   AND a.run_id = $1::uuid
		   AND j.current_attempt_token = a.token
		   AND a.exited_at IS NULL
		   AND a.revoked_at IS NULL
		   AND a.lease_deadline > now()
		   AND r.deleted_at IS NULL`,
		runID, token,
	).Scan(&scope.RunID, &scope.OwnerID, &scope.Token, &scope.JobID, &scope.RunState)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrStaleAttempt
	}
	if err != nil {
		return nil, fmt.Errorf("reading the run attempt scope: %w", err)
	}
	// Terminal runs never accept uploads/registrations; a non-executing
	// non-terminal run (waiting/finalizing) is T035/T036's state and is refused
	// explicitly rather than guessed. The cancelling state still accepts the
	// receipt/query calls that were already in flight (T036).
	if terminalRunState(scope.RunState) {
		return nil, ErrRunTerminal
	}
	if scope.RunState == "running" {
		return &scope, nil
	}
	if allowCancelling && scope.RunState == "cancelling" {
		return &scope, nil
	}
	return nil, ErrRunStateConflict
}

func terminalRunState(state string) bool {
	return state == "succeeded" || state == "failed" || state == "cancelled"
}

// linkURL turns a signed token into the absolute transfer URL.
func (s *RunArtifactStore) linkURL(token string) string {
	return s.BaseURL + "/object-links/" + token
}

// CreateUpload reserves one artifact for the live attempt and returns a signed
// PUT link to exactly the reserved staging key.
func (s *RunArtifactStore) CreateUpload(
	ctx context.Context, runID, token string, request RunArtifactUploadRequest,
) (RunArtifactTicket, error) {
	scope, err := s.resolveScope(ctx, runID, token)
	if err != nil {
		return RunArtifactTicket{}, err
	}
	mediaType := strings.ToLower(strings.TrimSpace(request.MediaType))
	if mediaType == "" || len(mediaType) > 255 || strings.ContainsAny(mediaType, "\r\n\x00") {
		return RunArtifactTicket{}, &ValidationError{Field: "media_type", Message: "media_type is required"}
	}
	if !runArtifactMediaTypes[mediaType] {
		return RunArtifactTicket{}, &ValidationError{
			Field: "media_type",
			Message: "media_type must be application/json, text/csv, text/plain or the XLSX " +
				"spreadsheet media type",
		}
	}
	purpose := strings.TrimSpace(request.Purpose)
	if !runArtifactPurposes[purpose] {
		return RunArtifactTicket{}, &ValidationError{
			Field:   "purpose",
			Message: "purpose must be node_output, final_output, log or receipt",
		}
	}
	if request.SizeBytes < 0 || request.SizeBytes > objectstore.MaxObjectBytes {
		return RunArtifactTicket{}, &ValidationError{
			Field:   "size_bytes",
			Message: fmt.Sprintf("size_bytes must be between 0 and %d", objectstore.MaxObjectBytes),
		}
	}
	if s.Objects == nil {
		return RunArtifactTicket{}, ErrObjectStoreUnavailable
	}
	if s.Signer == nil {
		return RunArtifactTicket{}, ErrObjectSigningUnavailable
	}

	stagingKey, err := objectstore.StagingKey()
	if err != nil {
		return RunArtifactTicket{}, err
	}
	sealedKey, err := objectstore.SealedKey()
	if err != nil {
		return RunArtifactTicket{}, err
	}
	artifactID := uuid.NewString()

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return RunArtifactTicket{}, fmt.Errorf("starting the run upload transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
			(id, owner_id, object_key, staging_object_key, purpose, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, 'uploading', 1)`,
		artifactID, scope.OwnerID, sealedKey, stagingKey, purpose, mediaType, request.SizeBytes,
	); err != nil {
		return RunArtifactTicket{}, fmt.Errorf("reserving the run artifact: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.run_artifact_uploads
			(artifact_id, run_id, attempt_token, purpose)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4)`,
		artifactID, scope.RunID, scope.Token, purpose,
	); err != nil {
		return RunArtifactTicket{}, fmt.Errorf("recording the upload provenance: %w", err)
	}
	url, expiresAt, err := s.Signer.Sign(objectstore.OpPut, stagingKey, artifactID, request.SizeBytes)
	if err != nil {
		return RunArtifactTicket{}, fmt.Errorf("minting the run upload link: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return RunArtifactTicket{}, fmt.Errorf("committing the run upload reservation: %w", err)
	}
	return RunArtifactTicket{ArtifactID: artifactID, URL: s.linkURL(url), ExpiresAt: expiresAt}, nil
}

// CompleteUpload applies the uploader's declared SHA-256 and enqueues exactly one
// artifact_finalize job in the same transaction. Activation is still the
// finalize guard's decision; this method never sets ready.
func (s *RunArtifactStore) CompleteUpload(
	ctx context.Context, runID, token, artifactID, declaredSHA256 string,
) (RunArtifactView, error) {
	scope, err := s.resolveScopeAllowCancelling(ctx, runID, token)
	if err != nil {
		return RunArtifactView{}, err
	}
	if !uuid.Valid(strings.TrimSpace(artifactID)) {
		return RunArtifactView{}, ErrRunArtifactNotFound
	}
	sha := strings.ToLower(strings.TrimSpace(declaredSHA256))
	if !isHex64(sha) {
		return RunArtifactView{}, &ValidationError{
			Field:   "sha256",
			Message: "sha256 must be 64 lowercase hexadecimal characters",
		}
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return RunArtifactView{}, fmt.Errorf("starting the run upload completion: %w", err)
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
		  JOIN platform.run_artifact_uploads AS u
		    ON u.artifact_id = a.id AND u.run_id = $2::uuid AND u.attempt_token = $3::uuid
		 WHERE a.id = $1::uuid
		 FOR UPDATE OF a`,
		artifactID, scope.RunID, scope.Token,
	).Scan(&state, &expected, &deleted, &revision, &mediaType, &sizeBytes)
	if errors.Is(err, sql.ErrNoRows) {
		return RunArtifactView{}, ErrRunArtifactNotFound
	}
	if err != nil {
		return RunArtifactView{}, fmt.Errorf("reading the run artifact: %w", err)
	}
	if deleted.Valid || state == "deleted" {
		// A deleted artifact is no longer in this run's visible scope; answer
		// exactly like a missing one so completion cannot probe tombstones.
		return RunArtifactView{}, ErrRunArtifactNotFound
	}
	view := RunArtifactView{ID: artifactID, State: state, MediaType: mediaType, SizeBytes: sizeBytes.Int64}

	switch state {
	case "uploading":
		nextRevision := revision + 1
		result, execErr := tx.ExecContext(ctx, `
			UPDATE platform.artifacts
			   SET state = 'scanning', expected_sha256 = $2, revision = revision + 1
			 WHERE id = $1::uuid AND state = 'uploading' AND revision = $3`,
			artifactID, sha, revision,
		)
		if execErr != nil {
			return RunArtifactView{}, fmt.Errorf("recording the run upload hash: %w", execErr)
		}
		affected, execErr := result.RowsAffected()
		if execErr != nil {
			return RunArtifactView{}, fmt.Errorf("recording the run upload hash: %w", execErr)
		}
		if affected != 1 {
			return RunArtifactView{}, &runArtifactConflictError{reason: "the artifact changed while completing"}
		}
		finalize, _, enqueueErr := background.EnqueueTx(ctx, tx, background.EnqueueParams{
			Kind:             background.KindArtifactFinalize,
			TargetID:         artifactID,
			OwnerID:          scope.OwnerID,
			DedupKey:         runFinalizeDedupKey(artifactID, sha),
			ExpectedRevision: &nextRevision,
			ExpectedSHA256:   sha,
			InputArtifactID:  artifactID,
		})
		if enqueueErr != nil {
			return RunArtifactView{}, enqueueErr
		}
		if commitErr := tx.Commit(); commitErr != nil {
			return RunArtifactView{}, fmt.Errorf("committing the run upload completion: %w", commitErr)
		}
		if s.Notifier != nil {
			s.Notifier.Notify(ctx, finalize)
		}
		view.State = "scanning"
		return view, nil

	case "scanning", "ready":
		recorded := ""
		if expected.Valid {
			recorded = strings.ToLower(strings.TrimSpace(expected.String))
		}
		if recorded != sha {
			return RunArtifactView{}, &runArtifactConflictError{reason: "a different sha256 was already recorded"}
		}
		// The completion already happened: the first call enqueued exactly one
		// finalize job and it is still the authority (ready means it ran). A
		// replay must not attempt another enqueue at all — its expected revision
		// would name a past state and could only produce a doomed job/conflict.
		if commitErr := tx.Commit(); commitErr != nil {
			return RunArtifactView{}, fmt.Errorf("committing the idempotent run completion: %w", commitErr)
		}
		return view, nil

	case "quarantined":
		recorded := ""
		if expected.Valid {
			recorded = strings.ToLower(strings.TrimSpace(expected.String))
		}
		if recorded != sha {
			return RunArtifactView{}, &runArtifactConflictError{reason: "a different sha256 was already recorded"}
		}
		// Quarantine is a final admission verdict, not an idempotent replay:
		// completing it again is a conflict (same rule as T028's user uploads).
		return RunArtifactView{}, &runArtifactConflictError{
			reason: "the artifact is quarantined and cannot be completed again",
		}

	default:
		return RunArtifactView{}, &runArtifactConflictError{reason: "the artifact is not in an uploadable state"}
	}
}

// runArtifactConflictError carries a stable reason while matching
// ErrRunArtifactConflict with errors.Is.
type runArtifactConflictError struct{ reason string }

func (e *runArtifactConflictError) Error() string { return "run artifact conflict: " + e.reason }
func (e *runArtifactConflictError) Unwrap() error { return ErrRunArtifactConflict }

// runFinalizeDedupKey names one artifact+hash intention, distinct from the
// job-scoped key so a run upload and a job upload of the same artifact id cannot
// collide (the ids are globally unique, but the namespace is documented).
func runFinalizeDedupKey(artifactID, sha string) string {
	return "run-artifact:" + artifactID + ":" + sha
}

// DownloadURL mints a sealed GET link for one of this run's input artifacts.
// Only inputs are downloadable through the execution protocol: an output is
// fetched by its owner through the owner-only artifact download endpoint.
func (s *RunArtifactStore) DownloadURL(
	ctx context.Context, runID, token, artifactID string,
) (string, time.Time, error) {
	scope, err := s.resolveScopeAllowCancelling(ctx, runID, token)
	if err != nil {
		return "", time.Time{}, err
	}
	if !uuid.Valid(strings.TrimSpace(artifactID)) {
		return "", time.Time{}, ErrRunArtifactNotFound
	}
	var (
		objectKey string
		size      sql.NullInt64
	)
	err = s.DB.QueryRowContext(ctx, `
		SELECT a.object_key, a.size_bytes
		  FROM platform.artifacts AS a
		  JOIN platform.runs AS r ON r.id = $1::uuid
		 WHERE a.id = $2::uuid
		   AND a.state = 'ready'
		   AND a.deleted_at IS NULL
		   AND r.deleted_at IS NULL
		   AND (
		        a.id = r.input_artifact_id
		        OR EXISTS (
		            SELECT 1 FROM platform.run_artifacts AS ra
		             WHERE ra.run_id = r.id AND ra.artifact_id = a.id AND ra.relation = 'input'
		        )
		   )`,
		scope.RunID, artifactID,
	).Scan(&objectKey, &size)
	if errors.Is(err, sql.ErrNoRows) {
		return "", time.Time{}, ErrRunArtifactNotFound
	}
	if err != nil {
		return "", time.Time{}, fmt.Errorf("reading the run input artifact: %w", err)
	}
	if s.Objects == nil {
		return "", time.Time{}, ErrObjectStoreUnavailable
	}
	if s.Signer == nil {
		return "", time.Time{}, ErrObjectSigningUnavailable
	}
	limit := size.Int64
	if limit < 0 || limit > objectstore.MaxObjectBytes {
		limit = 0
	}
	url, expiresAt, err := s.Signer.Sign(objectstore.OpGet, objectKey, artifactID, limit)
	if err != nil {
		return "", time.Time{}, fmt.Errorf("minting the run input link: %w", err)
	}
	return s.linkURL(url), expiresAt, nil
}

// ArtifactStatus returns one artifact this attempt uploaded, or one of this run's
// inputs. Anything else is the same 404 as a missing row.
func (s *RunArtifactStore) ArtifactStatus(
	ctx context.Context, runID, token, artifactID string,
) (RunArtifactView, error) {
	scope, err := s.resolveScopeAllowCancelling(ctx, runID, token)
	if err != nil {
		return RunArtifactView{}, err
	}
	if !uuid.Valid(strings.TrimSpace(artifactID)) {
		return RunArtifactView{}, ErrRunArtifactNotFound
	}
	var (
		view      RunArtifactView
		size      sql.NullInt64
		deletedAt sql.NullTime
	)
	err = s.DB.QueryRowContext(ctx, `
		SELECT a.id::text, a.state, a.media_type, a.size_bytes, a.deleted_at
		  FROM platform.artifacts AS a
		 WHERE a.id = $2::uuid
		   AND (
		        EXISTS (
		            SELECT 1 FROM platform.run_artifact_uploads AS u
		             WHERE u.artifact_id = a.id
		               AND u.run_id = $1::uuid
		               AND u.attempt_token = $3::uuid
		        )
		        OR EXISTS (
		            SELECT 1 FROM platform.runs AS r
		             WHERE r.id = $1::uuid
		               AND r.deleted_at IS NULL
		               AND (
		                    r.input_artifact_id = a.id
		                    OR EXISTS (
		                        SELECT 1 FROM platform.run_artifacts AS ra
		                         WHERE ra.run_id = r.id AND ra.artifact_id = a.id
		                           AND ra.relation = 'input'
		                    )
		               )
		        )
		   )`,
		scope.RunID, artifactID, scope.Token,
	).Scan(&view.ID, &view.State, &view.MediaType, &size, &deletedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return RunArtifactView{}, ErrRunArtifactNotFound
	}
	if err != nil {
		return RunArtifactView{}, fmt.Errorf("reading the run artifact status: %w", err)
	}
	if deletedAt.Valid {
		return RunArtifactView{}, ErrRunArtifactNotFound
	}
	view.SizeBytes = size.Int64
	return view, nil
}

// Register links one ready, attempt-uploaded artifact to the run. It is the only
// writer of run_artifacts for outputs (T027 writes the input links).
func (s *RunArtifactStore) Register(
	ctx context.Context, runID, token, artifactID, relation string,
) (RunArtifactView, error) {
	scope, err := s.resolveScope(ctx, runID, token)
	if err != nil {
		return RunArtifactView{}, err
	}
	if !uuid.Valid(strings.TrimSpace(artifactID)) {
		return RunArtifactView{}, ErrRunArtifactNotFound
	}
	relation = strings.TrimSpace(relation)
	if !runArtifactRelations[relation] {
		return RunArtifactView{}, &ValidationError{
			Field:   "relation",
			Message: "relation must be output, artifact or log",
		}
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return RunArtifactView{}, fmt.Errorf("starting the registration transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var (
		ownerID     string
		state       string
		deletedAt   sql.NullTime
		sha256      sql.NullString
		size        sql.NullInt64
		mediaType   string
		provenance  bool
		existingRel sql.NullString
	)
	err = tx.QueryRowContext(ctx, `
		SELECT a.owner_id::text, a.state, a.deleted_at, a.sha256, a.size_bytes, a.media_type,
		       EXISTS (
		           SELECT 1 FROM platform.run_artifact_uploads AS u
		            WHERE u.artifact_id = a.id AND u.run_id = $2::uuid AND u.attempt_token = $3::uuid
		       ) AS provenance,
		       (
		           SELECT ra.relation FROM platform.run_artifacts AS ra
		            WHERE ra.run_id = $2::uuid AND ra.artifact_id = a.id
		       ) AS existing_relation
		  FROM platform.artifacts AS a
		 WHERE a.id = $1::uuid
		 FOR UPDATE OF a`,
		artifactID, scope.RunID, scope.Token,
	).Scan(&ownerID, &state, &deletedAt, &sha256, &size, &mediaType, &provenance, &existingRel)
	if errors.Is(err, sql.ErrNoRows) {
		return RunArtifactView{}, ErrRunArtifactNotFound
	}
	if err != nil {
		return RunArtifactView{}, fmt.Errorf("reading the artifact to register: %w", err)
	}
	if !provenance {
		// The artifact was not uploaded by this attempt for this run: the same
		// answer as a missing row, so an old attempt cannot probe the id.
		return RunArtifactView{}, ErrRunArtifactNotFound
	}
	if !strings.EqualFold(ownerID, scope.OwnerID) {
		return RunArtifactView{}, ErrRunArtifactNotFound
	}
	if state != "ready" || deletedAt.Valid || !sha256.Valid || !size.Valid {
		return RunArtifactView{}, ErrArtifactNotReady
	}
	view := RunArtifactView{ID: artifactID, State: state, MediaType: mediaType, SizeBytes: size.Int64}
	if existingRel.Valid {
		if existingRel.String != relation {
			return RunArtifactView{}, &runArtifactConflictError{
				reason: "the artifact is already linked to this run with a different relation",
			}
		}
		if commitErr := tx.Commit(); commitErr != nil {
			return RunArtifactView{}, fmt.Errorf("committing the idempotent registration: %w", commitErr)
		}
		return view, nil
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.run_artifacts (run_id, artifact_id, attempt_token, relation)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4)
		ON CONFLICT (run_id, artifact_id) DO NOTHING`,
		scope.RunID, artifactID, scope.Token, relation,
	); err != nil {
		return RunArtifactView{}, fmt.Errorf("linking the run artifact: %w", err)
	}
	if commitErr := tx.Commit(); commitErr != nil {
		return RunArtifactView{}, fmt.Errorf("committing the registration: %w", commitErr)
	}
	return view, nil
}

// RunInputArtifact is one candidate input of a run, resolved for a supervisor.
type RunInputArtifact struct {
	ID        string
	Purpose   string
	MediaType string
	SizeBytes int64
	SHA256    string
}

// ListRunInputArtifacts resolves the run's linked input artifacts with the
// supervisor's trusted database identity. It exists because the claim Lease only
// carries the canonical run input; the supervisor must still discover which
// task_input artifact to feed the tool. The list is deliberately read-only and
// scoped to one run id the caller just claimed, and the bytes still move through
// the run-scoped signed download link (never through this query).
func ListRunInputArtifacts(ctx context.Context, db *sql.DB, runID string) ([]RunInputArtifact, error) {
	if db == nil || !uuid.Valid(strings.TrimSpace(runID)) {
		return nil, ErrRunNotFound
	}
	rows, err := db.QueryContext(ctx, `
		SELECT a.id::text, a.purpose, a.media_type, COALESCE(a.size_bytes, 0),
		       COALESCE(a.sha256, '')
		  FROM platform.run_artifacts AS ra
		  JOIN platform.artifacts AS a ON a.id = ra.artifact_id
		 WHERE ra.run_id = $1::uuid
		   AND ra.relation = 'input'
		   AND a.state = 'ready'
		   AND a.deleted_at IS NULL
		 ORDER BY a.created_at, a.id`, runID)
	if err != nil {
		return nil, fmt.Errorf("reading the run input artifacts: %w", err)
	}
	defer rows.Close()
	artifacts := make([]RunInputArtifact, 0, 2)
	for rows.Next() {
		var entry RunInputArtifact
		if err := rows.Scan(&entry.ID, &entry.Purpose, &entry.MediaType, &entry.SizeBytes, &entry.SHA256); err != nil {
			return nil, fmt.Errorf("reading the run input artifacts: %w", err)
		}
		artifacts = append(artifacts, entry)
	}
	return artifacts, rows.Err()
}
