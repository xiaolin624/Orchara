package background

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/uuid"
)

// EnqueueParams is one background job to create inside a caller's transaction.
//
// ExpectedRevision, ExpectedSHA256 and InputArtifactID are the source facts a
// later guard compares against; a producer that has a versioned resource passes
// them so a stale result can be recognised at completion time.
type EnqueueParams struct {
	Kind             Kind
	TargetID         string
	OwnerID          string
	DedupKey         string
	ExpectedRevision *int64
	// ExpectedIndexVersionID binds a document_ingest job to one knowledge index
	// version (T061). It is stored with the job and re-read by the completion
	// guard; a stale job must never activate a newer index.
	ExpectedIndexVersionID string
	ExpectedSHA256         string
	InputArtifactID        string
	// AvailableAt delays the first claim (zero means now). Tests and retrying
	// producers use it; the admin CLI does not expose it yet.
	AvailableAt *time.Time
}

// DedupConflictError means (kind, dedup_key) already names a different
// intention: another target or different expected source facts. It is a
// conflict, not a validation problem, and no row is written.
type DedupConflictError struct {
	ExistingID string
	Reason     string
}

func (e *DedupConflictError) Error() string {
	if e.ExistingID == "" {
		return "dedup key conflict: " + e.Reason
	}
	return fmt.Sprintf("dedup key conflict with job %s: %s", e.ExistingID, e.Reason)
}

// Unwrap lets callers test errors.Is(err, ErrDedupConflict) without losing the
// diagnostic fields.
func (e *DedupConflictError) Unwrap() error { return ErrDedupConflict }

// EnqueueTx creates one job in tx.
//
// Idempotency is defined by UNIQUE(kind, dedup_key): an existing job with the
// same target, owner, expected revision, expected hash and input artifact is
// returned unchanged (same id, created=false). Any difference is a
// DedupConflictError; the unique index makes this race-safe under concurrency.
func EnqueueTx(ctx context.Context, tx *sql.Tx, params EnqueueParams) (*Job, bool, error) {
	if tx == nil {
		return nil, false, errors.New("EnqueueTx requires a transaction")
	}
	normalized, err := validateEnqueue(params)
	if err != nil {
		return nil, false, err
	}

	var expectedRevision any
	if normalized.ExpectedRevision != nil {
		expectedRevision = *normalized.ExpectedRevision
	}
	var expectedIndexVersion any
	if normalized.ExpectedIndexVersionID != "" {
		expectedIndexVersion = normalized.ExpectedIndexVersionID
	}
	var expectedSHA any
	if normalized.ExpectedSHA256 != "" {
		expectedSHA = normalized.ExpectedSHA256
	}
	var inputArtifact any
	if normalized.InputArtifactID != "" {
		inputArtifact = normalized.InputArtifactID
	}
	availableAt := "now()"
	var args []any
	args = append(args,
		uuid.NewString(), string(normalized.Kind), normalized.TargetID, normalized.OwnerID,
		expectedRevision, expectedIndexVersion, expectedSHA, normalized.DedupKey, inputArtifact,
	)
	if normalized.AvailableAt != nil {
		availableAt = "$10"
		args = append(args, normalized.AvailableAt.UTC())
	}

	created, err := scanJob(tx.QueryRowContext(ctx, `
		INSERT INTO platform.background_jobs
			(id, kind, target_id, owner_id, expected_revision, expected_index_version_id,
			 expected_sha256, dedup_key, input_artifact_id, state, available_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'ready', `+availableAt+`)
		ON CONFLICT (kind, dedup_key) DO NOTHING
		RETURNING `+selectJobColumns, args...))
	if err == nil {
		return created, true, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		if isUniqueViolation(err, "background_jobs_kind_dedup") {
			return nil, false, ErrDedupConflict
		}
		return nil, false, fmt.Errorf("creating a background job: %w", err)
	}

	existing, err := scanJob(tx.QueryRowContext(ctx, `
		SELECT `+selectJobColumns+`
		  FROM platform.background_jobs
		 WHERE kind = $1 AND dedup_key = $2
		 FOR UPDATE`, string(normalized.Kind), normalized.DedupKey))
	if errors.Is(err, sql.ErrNoRows) {
		// The conflicting row vanished between INSERT and SELECT; report a
		// conflict rather than pretending the job was created.
		return nil, false, ErrDedupConflict
	}
	if err != nil {
		return nil, false, fmt.Errorf("reading the deduplicated job: %w", err)
	}
	if reason := dedupDifference(existing, normalized); reason != "" {
		return nil, false, &DedupConflictError{ExistingID: existing.ID, Reason: reason}
	}
	return existing, false, nil
}

// Enqueue is EnqueueTx in its own transaction. Producers that already write a
// resource row must call EnqueueTx with that transaction instead.
//
// After the commit the controller emits a best-effort wakeup. The job is durable
// before the notification is attempted, so a dropped wakeup delays the next
// claim by at most one poll interval and can never lose the work.
func (c *Controller) Enqueue(ctx context.Context, params EnqueueParams) (*Job, bool, error) {
	tx, err := c.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, false, fmt.Errorf("starting the enqueue transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	job, created, err := EnqueueTx(ctx, tx, params)
	if err != nil {
		return nil, false, err
	}
	if err := tx.Commit(); err != nil {
		return nil, false, fmt.Errorf("committing the enqueue transaction: %w", err)
	}
	notify(ctx, c.Notifier, job)
	return job, created, nil
}

func validateEnqueue(params EnqueueParams) (EnqueueParams, error) {
	if !KnownKind(string(params.Kind)) {
		return EnqueueParams{}, &ValidationError{
			Field:   "kind",
			Message: fmt.Sprintf("unknown kind %q", params.Kind),
		}
	}
	targetID, err := validateUUID("target_id", params.TargetID)
	if err != nil {
		return EnqueueParams{}, err
	}
	ownerID, err := validateUUID("owner_id", params.OwnerID)
	if err != nil {
		return EnqueueParams{}, err
	}
	dedupKey := strings.TrimSpace(params.DedupKey)
	if dedupKey == "" {
		return EnqueueParams{}, &ValidationError{Field: "dedup_key", Message: "dedup_key is required"}
	}
	if len(dedupKey) > 200 {
		return EnqueueParams{}, &ValidationError{
			Field:   "dedup_key",
			Message: "dedup_key must be at most 200 characters",
		}
	}
	if params.ExpectedRevision != nil && *params.ExpectedRevision < 1 {
		return EnqueueParams{}, &ValidationError{
			Field:   "expected_revision",
			Message: "expected_revision must be at least 1",
		}
	}
	sha := strings.ToLower(strings.TrimSpace(params.ExpectedSHA256))
	if sha != "" && !isSHA256Hex(sha) {
		return EnqueueParams{}, &ValidationError{
			Field:   "expected_sha256",
			Message: "expected_sha256 must be 64 lowercase hexadecimal characters",
		}
	}
	inputArtifact := strings.TrimSpace(params.InputArtifactID)
	if inputArtifact != "" {
		if _, err := validateUUID("input_artifact_id", inputArtifact); err != nil {
			return EnqueueParams{}, err
		}
	}
	expectedIndexVersion := strings.TrimSpace(params.ExpectedIndexVersionID)
	if expectedIndexVersion != "" {
		if _, err := validateUUID("expected_index_version_id", expectedIndexVersion); err != nil {
			return EnqueueParams{}, err
		}
	}
	var availableAt *time.Time
	if params.AvailableAt != nil {
		value := params.AvailableAt.UTC()
		availableAt = &value
	}
	return EnqueueParams{
		Kind:                   params.Kind,
		TargetID:               targetID,
		OwnerID:                ownerID,
		DedupKey:               dedupKey,
		ExpectedRevision:       params.ExpectedRevision,
		ExpectedIndexVersionID: expectedIndexVersion,
		ExpectedSHA256:         sha,
		InputArtifactID:        inputArtifact,
		AvailableAt:            availableAt,
	}, nil
}

// dedupDifference names the first field that makes an existing job a different
// intention. An empty result means it is the same request and may be returned.
func dedupDifference(existing *Job, params EnqueueParams) string {
	if existing.TargetID != params.TargetID {
		return "target_id differs"
	}
	if existing.OwnerID != params.OwnerID {
		return "owner_id differs"
	}
	switch {
	case existing.ExpectedRevision == nil && params.ExpectedRevision != nil,
		existing.ExpectedRevision != nil && params.ExpectedRevision == nil:
		return "expected_revision differs"
	case existing.ExpectedRevision != nil && *existing.ExpectedRevision != *params.ExpectedRevision:
		return "expected_revision differs"
	}
	existingSHA := ""
	if existing.ExpectedSHA256 != nil {
		existingSHA = *existing.ExpectedSHA256
	}
	if existingSHA != params.ExpectedSHA256 {
		return "expected_sha256 differs"
	}
	existingIndex := ""
	if existing.ExpectedIndexVersionID != nil {
		existingIndex = *existing.ExpectedIndexVersionID
	}
	if existingIndex != params.ExpectedIndexVersionID {
		return "expected_index_version_id differs"
	}
	existingInput := ""
	if existing.InputArtifactID != nil {
		existingInput = *existing.InputArtifactID
	}
	if existingInput != params.InputArtifactID {
		return "input_artifact_id differs"
	}
	return ""
}

func isSHA256Hex(value string) bool {
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
