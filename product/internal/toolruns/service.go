// Package toolruns is the user-facing half of the T030 preset tool path.
//
// It creates one durable `tool_run` background job for a caller-owned, ready
// CSV/XLSX artifact and exposes the owner-only run read model. It is a
// documented non-contract bridge (ADR 0005 / the T030 report): the frozen
// contract has no tool-run endpoint yet, so this package reuses the existing
// idempotency receipt table and background-job pipeline instead of inventing a
// second execution mechanism. T033 will bind preset tools to the run/sandbox
// protocol and can replace this seam without changing the tool executor.
//
// Invariants:
//   - the artifact is the caller's own, ready, not deleted, with a real
//     SHA-256/size and a supported media type; anyone else gets the same 404;
//   - the idempotency receipt and the job are written in one transaction;
//   - the job is the existing `tool_run` kind with target/input = artifact id,
//     expected_revision = the artifact's current revision and a per-intention
//     dedup key; the idempotency receipt, not the job queue, is what makes the
//     same request replay (a new key is a new run, so a transient failure can be
//     retried by the user);
//   - the read model joins platform.background_job_artifacts and never exposes
//     an object key, a staging key or another principal's row.
package toolruns

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/tools"
	"orchara/platform/internal/uuid"
)

// IdempotencyTTL is how long a create receipt may be replayed. It matches the
// batch's create-operation receipts (debug runs use the same day-scale window).
const IdempotencyTTL = 24 * time.Hour

// Service owns tool-run creation and reads over one database handle.
type Service struct {
	DB       *sql.DB
	Notifier background.Notifier
}

// New builds the service. A nil notifier behaves like NopNotifier.
func New(db *sql.DB, notifier background.Notifier) *Service {
	if notifier == nil {
		notifier = background.NopNotifier{}
	}
	return &Service{DB: db, Notifier: notifier}
}

// CreateRequest is the exact POST /api/v1/tool-runs body.
type CreateRequest struct {
	Tool       string `json:"tool"`
	ArtifactID string `json:"artifact_id"`
}

// CreateView is the 202 body. It is also the stored idempotent response, so a
// replay returns the recorded state/tool/artifact unchanged.
type CreateView struct {
	ID         string `json:"id"`
	State      string `json:"state"`
	Tool       string `json:"tool"`
	ArtifactID string `json:"artifact_id"`
}

// ArtifactView is one owner-visible output of a tool run.
type ArtifactView struct {
	ID        string `json:"id"`
	Purpose   string `json:"purpose"`
	State     string `json:"state"`
	MediaType string `json:"media_type"`
	SizeBytes int64  `json:"size_bytes"`
}

// RunView is the owner-only GET /api/v1/tool-runs/{job_id} body.
type RunView struct {
	ID        string         `json:"id"`
	State     string         `json:"state"`
	ErrorCode *string        `json:"error_code"`
	Artifacts []ArtifactView `json:"artifacts"`
}

// IdempotencyScope is the receipt primary key. Path is the concrete request
// path, so the same key cannot be borrowed across resources.
type IdempotencyScope struct {
	PrincipalID string
	Method      string
	Path        string
	Key         string
}

// Errors callers map to HTTP answers.
var (
	// ErrNotVisible is "missing, another owner's, deleted or malformed": every
	// one of those is the same 404 (no enumeration).
	ErrNotVisible = errors.New("tool run or artifact is not visible")
	// ErrNotReady is the 409 for an artifact that exists but cannot be run yet.
	ErrNotReady = errors.New("the artifact is not ready for a tool run")
	// ErrIdempotencyConflict means the key was already used with a different
	// canonical request.
	ErrIdempotencyConflict = errors.New("the idempotency key was used for a different request")
	// ErrConflict is the defensive answer for a dedup key that somehow names a
	// different prior intention. With a per-intention nonce it should be
	// unreachable; it exists so a future dedup policy cannot silently return a
	// job that was created for different facts.
	ErrConflict = errors.New("the artifact revision already has a conflicting tool run")
)

// ValidationError names the request field that failed validation.
type ValidationError struct {
	Field   string
	Message string
}

func (e *ValidationError) Error() string { return e.Field + ": " + e.Message }

const createAcceptedStatus = 202

// CreateRun validates the artifact, writes the idempotency receipt and the
// tool_run job in one transaction, and returns the 202 body.
//
// Order is deliberate and shared with the API's error mapping:
//
//  0. body shape tool/id (validated before any row read);
//  1. stored receipt (replay or 409) — a replay needs no artifact read;
//  2. owner-only, ready, supported-media artifact (404/409/422);
//  3. one transaction: receipt + job, with the artifact row locked and its
//     revision re-read so a concurrent delete/state change cannot be raced. A
//     new key for the same artifact is a new run intention (re-run); only the
//     same key replays.
func (s *Service) CreateRun(
	ctx context.Context,
	callerID string,
	scope IdempotencyScope,
	request CreateRequest,
) (*CreateView, error) {
	tool := strings.TrimSpace(request.Tool)
	if tool != tools.ToolSalesStats {
		return nil, &ValidationError{
			Field:   "tool",
			Message: "tool must be sales_stats",
		}
	}
	artifactID := strings.ToLower(strings.TrimSpace(request.ArtifactID))
	if !uuid.Valid(artifactID) {
		return nil, ErrNotVisible
	}
	if strings.TrimSpace(scope.PrincipalID) == "" || !strings.EqualFold(scope.PrincipalID, callerID) ||
		strings.TrimSpace(scope.Key) == "" || scope.Method != "POST" {
		return nil, &ValidationError{
			Field:   "idempotency_key",
			Message: "a complete idempotency scope is required",
		}
	}
	digest := requestDigest(tool, artifactID)

	existing, err := s.lookupReceipt(ctx, scope)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		return replayReceipt(existing, digest)
	}

	artifact, err := s.readRunnableArtifact(ctx, callerID, artifactID)
	if err != nil {
		return nil, err
	}
	if err := ValidateMediaType(artifact.mediaType); err != nil {
		return nil, err
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the tool-run transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	result, err := tx.ExecContext(ctx, `
		INSERT INTO platform.idempotency_keys
		       (principal_id, method, path, key, request_sha256, expires_at)
		VALUES ($1::uuid, $2, $3, $4, $5, now() + $6::interval)
		ON CONFLICT (principal_id, method, path, key) DO NOTHING`,
		scope.PrincipalID, scope.Method, scope.Path, scope.Key, digest,
		intervalOf(IdempotencyTTL),
	)
	if err != nil {
		return nil, fmt.Errorf("claiming the idempotency receipt: %w", err)
	}
	claimed, err := result.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("reading the idempotency claim: %w", err)
	}
	if claimed == 0 {
		_ = tx.Rollback()
		winner, lookupErr := s.lookupReceipt(ctx, scope)
		if lookupErr != nil {
			return nil, lookupErr
		}
		if winner == nil {
			return nil, errors.New("the idempotency claim conflicted but no receipt is visible")
		}
		return replayReceipt(winner, digest)
	}

	// Re-read under a row lock: the artifact may have been deleted or moved to
	// another state between the pre-check and the transaction.
	var (
		ownerID   string
		state     string
		deleted   sql.NullTime
		revision  int64
		storedSHA sql.NullString
		size      sql.NullInt64
		mediaType string
	)
	err = tx.QueryRowContext(ctx, `
		SELECT owner_id::text, state, deleted_at, revision, sha256, size_bytes, media_type
		  FROM platform.artifacts
		 WHERE id = $1::uuid
		 FOR UPDATE`, artifactID,
	).Scan(&ownerID, &state, &deleted, &revision, &storedSHA, &size, &mediaType)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("locking the tool input artifact: %w", err)
	}
	if !strings.EqualFold(ownerID, callerID) || deleted.Valid {
		return nil, ErrNotVisible
	}
	if state != "ready" {
		return nil, ErrNotReady
	}
	if !strings.EqualFold(mediaType, artifact.mediaType) {
		return nil, ErrNotReady
	}
	sha := strings.ToLower(strings.TrimSpace(storedSHA.String))
	if !storedSHA.Valid || len(sha) != 64 {
		return nil, ErrNotReady
	}
	declaredSize := size.Int64
	if declaredSize < 1 || declaredSize > tools.MaxInputBytes {
		return nil, ErrNotReady
	}
	if revision != artifact.revision || sha != artifact.sha256 || declaredSize != artifact.sizeBytes {
		// The row moved between the two reads even though it is still ready:
		// a different revision is a different job. Refuse rather than enqueue
		// against facts the caller observed earlier.
		return nil, ErrNotReady
	}

	job, created, err := background.EnqueueTx(ctx, tx, background.EnqueueParams{
		Kind:             background.KindToolRun,
		TargetID:         artifactID,
		OwnerID:          callerID,
		DedupKey:         dedupKey(tool, artifactID, revision, uuid.NewString()),
		ExpectedRevision: &revision,
		ExpectedSHA256:   sha,
		InputArtifactID:  artifactID,
	})
	if err != nil {
		if errors.Is(err, background.ErrDedupConflict) {
			return nil, ErrConflict
		}
		return nil, err
	}
	view := &CreateView{
		ID:         job.ID,
		State:      string(job.State),
		Tool:       tool,
		ArtifactID: artifactID,
	}
	payload, err := json.Marshal(view)
	if err != nil {
		return nil, fmt.Errorf("encoding the tool-run response: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.idempotency_keys
		   SET response_code = $5, response_json = $6::jsonb
		 WHERE principal_id = $1::uuid AND method = $2 AND path = $3 AND key = $4`,
		scope.PrincipalID, scope.Method, scope.Path, scope.Key,
		createAcceptedStatus, string(payload),
	); err != nil {
		return nil, fmt.Errorf("storing the idempotent tool-run response: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the tool-run creation: %w", err)
	}
	if created {
		s.Notifier.Notify(ctx, job)
	}
	return view, nil
}

// GetRun returns one tool run to its owner. Another caller, an administrator
// and an unknown/malformed id are all the same ErrNotVisible (404).
func (s *Service) GetRun(ctx context.Context, callerID, jobID string) (*RunView, error) {
	jobID = strings.TrimSpace(jobID)
	if !uuid.Valid(jobID) || !uuid.Valid(callerID) {
		return nil, ErrNotVisible
	}
	var (
		view      RunView
		errorCode sql.NullString
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT id::text, state, error_code
		  FROM platform.background_jobs
		 WHERE id = $1::uuid AND kind = 'tool_run' AND owner_id = $2::uuid`,
		jobID, callerID,
	).Scan(&view.ID, &view.State, &errorCode)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotVisible
	}
	if err != nil {
		return nil, fmt.Errorf("reading the tool run: %w", err)
	}
	if errorCode.Valid {
		value := errorCode.String
		view.ErrorCode = &value
	}
	view.Artifacts = make([]ArtifactView, 0)
	rows, err := s.DB.QueryContext(ctx, `
		SELECT a.id::text, l.purpose, a.state, a.media_type, COALESCE(a.size_bytes, 0)
		  FROM platform.background_job_artifacts AS l
		  JOIN platform.artifacts AS a ON a.id = l.artifact_id
		 WHERE l.background_job_id = $1::uuid AND l.relation = 'output'
		 ORDER BY l.created_at, a.id`, jobID)
	if err != nil {
		return nil, fmt.Errorf("reading the tool-run artifacts: %w", err)
	}
	defer func() { _ = rows.Close() }()
	for rows.Next() {
		var artifact ArtifactView
		if err := rows.Scan(
			&artifact.ID, &artifact.Purpose, &artifact.State, &artifact.MediaType, &artifact.SizeBytes,
		); err != nil {
			return nil, fmt.Errorf("reading the tool-run artifact: %w", err)
		}
		view.Artifacts = append(view.Artifacts, artifact)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the tool-run artifacts: %w", err)
	}
	return &view, nil
}

// runnableArtifact is the platform's own view of the input before it is locked.
type runnableArtifact struct {
	mediaType string
	revision  int64
	sha256    string
	sizeBytes int64
}

// readRunnableArtifact applies the owner/ready/media admission rule before any
// transaction. It returns ErrNotVisible for a missing row, another owner, a
// tombstone or a malformed id, and ErrNotReady for an existing artifact that is
// not runnable.
func (s *Service) readRunnableArtifact(
	ctx context.Context, callerID, artifactID string,
) (runnableArtifact, error) {
	var (
		ownerID   string
		state     string
		deleted   sql.NullTime
		revision  int64
		storedSHA sql.NullString
		size      sql.NullInt64
		mediaType string
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT owner_id::text, state, deleted_at, revision, sha256, size_bytes, media_type
		  FROM platform.artifacts
		 WHERE id = $1::uuid`, artifactID,
	).Scan(&ownerID, &state, &deleted, &revision, &storedSHA, &size, &mediaType)
	if errors.Is(err, sql.ErrNoRows) {
		return runnableArtifact{}, ErrNotVisible
	}
	if err != nil {
		return runnableArtifact{}, fmt.Errorf("reading the tool input artifact: %w", err)
	}
	if !strings.EqualFold(ownerID, callerID) || deleted.Valid {
		return runnableArtifact{}, ErrNotVisible
	}
	if state != "ready" {
		return runnableArtifact{}, ErrNotReady
	}
	sha := strings.ToLower(strings.TrimSpace(storedSHA.String))
	declaredSize := size.Int64
	if !storedSHA.Valid || len(sha) != 64 || declaredSize < 1 || declaredSize > tools.MaxInputBytes {
		return runnableArtifact{}, ErrNotReady
	}
	return runnableArtifact{
		mediaType: mediaType,
		revision:  revision,
		sha256:    sha,
		sizeBytes: declaredSize,
	}, nil
}

// ValidateMediaType applies the preset tool's closed media-type allowlist. It
// lives here rather than in the HTTP layer so the service owns the rule.
func ValidateMediaType(mediaType string) error {
	switch strings.ToLower(strings.TrimSpace(mediaType)) {
	case tools.MediaTypeCSV, tools.MediaTypeXLSX:
		return nil
	default:
		return &ValidationError{
			Field:   "artifact_id",
			Message: "the preset tool supports only ready text/csv or XLSX artifacts",
		}
	}
}

// dedupKey names one create intention. The idempotency receipt is what makes the
// SAME request idempotent; the random nonce keeps distinct accepted requests
// distinct even for the same artifact revision, so a user can re-run a tool after
// a transient failure (the background job itself still retries three times). The
// artifact id + revision remain in the key for diagnostics.
func dedupKey(tool, artifactID string, revision int64, nonce string) string {
	return fmt.Sprintf("tool-run:%s:%s:%d:%s", tool, artifactID, revision, nonce)
}

func requestDigest(tool, artifactID string) string {
	sum := sha256.Sum256([]byte("tool-runs\x00" + tool + "\x00" + artifactID))
	return hex.EncodeToString(sum[:])
}

func intervalOf(duration time.Duration) string {
	seconds := int64(duration / time.Second)
	if seconds < 1 {
		seconds = 1
	}
	return fmt.Sprintf("%d seconds", seconds)
}

// receipt is one stored idempotent response.
type receipt struct {
	RequestSHA256 string
	ResponseCode  sql.NullInt64
	ResponseJSON  sql.NullString
}

func (s *Service) lookupReceipt(ctx context.Context, scope IdempotencyScope) (*receipt, error) {
	var record receipt
	err := s.DB.QueryRowContext(ctx, `
		SELECT request_sha256, response_code, response_json::text
		  FROM platform.idempotency_keys
		 WHERE principal_id = $1::uuid AND method = $2 AND path = $3 AND key = $4`,
		scope.PrincipalID, scope.Method, scope.Path, scope.Key,
	).Scan(&record.RequestSHA256, &record.ResponseCode, &record.ResponseJSON)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the tool-run idempotency receipt: %w", err)
	}
	return &record, nil
}

func replayReceipt(record *receipt, digest string) (*CreateView, error) {
	if record.RequestSHA256 != digest {
		return nil, ErrIdempotencyConflict
	}
	if !record.ResponseCode.Valid || record.ResponseCode.Int64 != createAcceptedStatus ||
		!record.ResponseJSON.Valid {
		return nil, errors.New("the tool-run idempotency receipt has no stored 202 response")
	}
	var view CreateView
	if err := json.Unmarshal([]byte(record.ResponseJSON.String), &view); err != nil {
		return nil, fmt.Errorf("decoding the stored tool-run response: %w", err)
	}
	return &view, nil
}
