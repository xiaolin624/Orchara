package background

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Completion is one worker result proposal.
//
// State is what the worker claims; ErrorCode is required for failed and is the
// vocabulary the retry table classifies. For artifact_finalize the controller
// re-verifies the sealed object before accepting a success, and can override a
// claimed success with a failure (the attempt fails; the artifact is never
// activated). ResultEventID is the idempotency key of this result.
type Completion struct {
	JobID            string
	AttemptToken     string
	EventID          string
	State            State
	ErrorCode        string
	ResultArtifactID string
}

// errGuardTransient marks a server-side object read that failed for an
// infrastructure reason. The completion becomes a retryable infrastructure
// failure with the fixed transient_storage_io code.
var errGuardTransient = errors.New("transient guard io failure")

// Complete applies one result proposal.
//
// Contract:
//   - state must be leased, the attempt token must be the current one and the
//     lease must not have expired; otherwise ErrStaleAttempt and nothing changes;
//   - a terminal job answers an identical replay (same event id and same request
//     digest) with the recorded job, and a different event with ErrEventConflict;
//   - failed results are retried only for codes in the per-kind retry table and
//     only while attempt_count < MaxAttempts; everything else fails immediately
//     (unknown codes are business failures);
//   - artifact_finalize completion re-reads the sealed object in the same
//     transaction that activates the artifact; a hash/size mismatch quarantines
//     it, a tombstone or moved revision refuses activation, and a missing object
//     store refuses the whole completion (fail closed).
func (c *Controller) Complete(ctx context.Context, completion Completion) (*Job, error) {
	normalized, err := validateCompletion(completion)
	if err != nil {
		return nil, err
	}

	tx, err := c.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the completion transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	job, err := scanJob(tx.QueryRowContext(ctx, `
		SELECT `+selectJobColumns+`
		  FROM platform.background_jobs
		 WHERE id = $1::uuid
		 FOR UPDATE`, normalized.JobID))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the background job: %w", err)
	}

	digest := requestDigest(normalized)
	if job.State.Terminal() {
		if job.ResultEventID != nil && *job.ResultEventID == normalized.EventID {
			if job.ResultEventDigest != nil && *job.ResultEventDigest == digest {
				// Idempotent replay: return exactly what was recorded, no second
				// count and no second side effect.
				if err := tx.Commit(); err != nil {
					return nil, fmt.Errorf("committing the idempotent completion: %w", err)
				}
				return job, nil
			}
			return nil, ErrEventConflict
		}
		return nil, ErrEventConflict
	}

	var dbNow sql.NullTime
	if err := tx.QueryRowContext(ctx, `SELECT now()`).Scan(&dbNow); err != nil {
		return nil, fmt.Errorf("reading the database clock: %w", err)
	}
	leaseLive := job.State == StateLeased && job.LeaseToken != nil &&
		*job.LeaseToken == normalized.AttemptToken && job.LeaseDeadline != nil &&
		job.LeaseDeadline.After(dbNow.Time)
	if !leaseLive {
		return nil, ErrStaleAttempt
	}

	// The guards are a registry keyed by kind, mirroring handlers.Registry: each
	// kind that needs a completion decision owns one function, and Complete never
	// branches on the kind itself. A guard runs before the terminal write and may
	// override the worker's proposal (a claimed success that cannot be proven
	// becomes a failure); it returns an error only for a refusal that must leave
	// the job untouched (for example a stale result-artifact reference).
	proposal := normalized
	if guard, ok := completionGuards[job.Kind]; ok {
		if err := guard(ctx, c, tx, job, &proposal); err != nil {
			return nil, err
		}
	}

	// A failed attempt is retried only for a code in the fixed per-kind table and
	// only while attempts remain. The expression lives here once and is shared by
	// the application_evaluate guard through retryAllowed.
	if proposal.State == StateFailed && retryAllowed(job, proposal.ErrorCode) {
		retried, err := c.retryJob(ctx, tx, job, proposal.ErrorCode)
		if err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the retry: %w", err)
		}
		return retried, nil
	}

	failureClass := any(nil)
	if proposal.State == StateFailed {
		failureClass = string(ClassifyFailure(job.Kind, proposal.ErrorCode))
	}
	if proposal.State == StateSucceeded || proposal.State == StateCancelled {
		proposal.ErrorCode = normalized.ErrorCode
		if proposal.State == StateSucceeded {
			proposal.ErrorCode = ""
			failureClass = nil
		}
	}
	if proposal.State == StateFailed {
		proposal.ResultArtifactID = "" // a failed job never carries a result artifact
	}

	finished, err := scanJob(tx.QueryRowContext(ctx, `
		UPDATE platform.background_jobs
		   SET state = $2,
		       error_code = $3,
		       failure_class = $4,
		       finished_at = now(),
		       result_event_id = $5::uuid,
		       result_event_digest = $6,
		       result_artifact_id = $7,
		       lease_token = NULL,
		       lease_deadline = NULL,
		       worker_id = NULL,
		       last_error_at = CASE WHEN $2 = 'failed' THEN now() ELSE NULL END
		 WHERE id = $1::uuid
		RETURNING `+selectJobColumns,
		job.ID, string(proposal.State), nullableText(proposal.ErrorCode), failureClass,
		normalized.EventID, digest, nullableText(proposal.ResultArtifactID),
	))
	if err != nil {
		if isUniqueViolation(err, "background_jobs_result_event_id_key") {
			return nil, ErrEventConflict
		}
		return nil, fmt.Errorf("completing the background job: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the completion: %w", err)
	}
	return finished, nil
}

// CompletionGuard is the per-kind completion decision, the completion-side
// counterpart of handlers.Registry. A guard may rewrite the worker's proposal
// (state/error code/result artifact) but must not write the terminal job row:
// Complete does that once after every guard returns. An error aborts the whole
// completion and leaves the job and its business rows untouched.
type CompletionGuard func(
	ctx context.Context,
	c *Controller,
	tx *sql.Tx,
	job *Job,
	completion *Completion,
) error

// completionGuards is the fixed kind -> guard table. Kinds without an entry
// complete exactly as the worker reported (their results carry no server-side
// business fact). A new kind that needs a guard adds one here in the same change.
var completionGuards = map[Kind]CompletionGuard{
	KindArtifactFinalize:    guardArtifactFinalizeCompletion,
	KindModelValidate:       guardModelValidateCompletion,
	KindApplicationEvaluate: guardApplicationEvaluateCompletion,
	KindToolRun:             guardToolRunCompletion,
	KindToolBuild:           guardToolBuildCompletion,
}

// retryAllowed is the single retry decision: the code must be in the kind's
// fixed table and the claim must not have reached the attempt ceiling. It is
// used both by Complete's retry branch and by the evaluation guard, which must
// know whether a failed job will be retried before it fails the evaluation row.
func retryAllowed(job *Job, errorCode string) bool {
	return Retryable(job.Kind, errorCode) && job.AttemptCount < MaxAttempts
}

// guardArtifactFinalizeCompletion is the server-side activation decision. The
// result *is* the target artifact, so a worker-supplied result artifact id could
// only be a second, unverified reference; a claimed success is re-verified
// against the sealed bytes and any failure leaves the artifact unusable.
func guardArtifactFinalizeCompletion(
	ctx context.Context,
	c *Controller,
	tx *sql.Tx,
	job *Job,
	completion *Completion,
) error {
	if completion.State == StateSucceeded && completion.ResultArtifactID != "" {
		return &ValidationError{
			Field:   "result_artifact_id",
			Message: "artifact_finalize activation is server-side; the result artifact is the job target",
		}
	}
	switch completion.State {
	case StateSucceeded:
		failureCode, guardErr := c.verifyAndActivateArtifact(ctx, tx, job, completion.AttemptToken)
		switch {
		case errors.Is(guardErr, errGuardTransient):
			completion.State = StateFailed
			completion.ErrorCode = CodeTransientStorageIO
		case guardErr != nil:
			return guardErr
		case failureCode != "":
			// Guard override: any failure path must leave the artifact unusable,
			// so a claimed success that cannot be proven is a failed attempt.
			completion.State = StateFailed
			completion.ErrorCode = failureCode
			completion.ResultArtifactID = ""
			if ImpliesQuarantine(failureCode) {
				if err := quarantineArtifact(ctx, tx, job.TargetID); err != nil {
					return err
				}
			}
		}
	case StateFailed:
		if ImpliesQuarantine(completion.ErrorCode) {
			if err := quarantineArtifact(ctx, tx, job.TargetID); err != nil {
				return err
			}
		}
	}
	return nil
}

// guardModelValidateCompletion records the compatibility report artifact the
// handler registered and reconciles the model/evaluation business state. The
// handler cannot name an artifact it did not upload: readiness, ownership, job
// provenance, the sealed report bindings and the model-call ledger row are all
// re-checked in this transaction. A failed compatibility verdict is still a
// successful job, so only a succeeded result is checked; a terminal failure or
// cancellation fails the evaluation and the model.
func guardModelValidateCompletion(
	ctx context.Context,
	c *Controller,
	tx *sql.Tx,
	job *Job,
	completion *Completion,
) error {
	if completion.State == StateSucceeded {
		if completion.ResultArtifactID == "" {
			return &ValidationError{
				Field:   "result_artifact_id",
				Message: "model_validate success must name the compatibility report artifact",
			}
		}
		if err := verifyJobResultArtifact(ctx, tx, job, completion.ResultArtifactID); err != nil {
			return err
		}
		failureCode, guardErr := c.guardModelValidateSuccess(ctx, tx, job, completion.ResultArtifactID)
		switch {
		case errors.Is(guardErr, errGuardTransient):
			completion.State = StateFailed
			completion.ErrorCode = CodeTransientStorageIO
		case guardErr != nil:
			return guardErr
		case failureCode != "":
			// A report the server cannot trust is a failed job with no report.
			completion.State = StateFailed
			completion.ErrorCode = failureCode
			completion.ResultArtifactID = ""
		}
	}
	if completion.State == StateFailed && !retryAllowed(job, completion.ErrorCode) {
		if err := failModelValidationForJobTx(ctx, tx, job.TargetID, completion.ErrorCode); err != nil {
			return err
		}
	}
	if completion.State == StateCancelled {
		if err := cancelModelValidationForJobTx(ctx, tx, job.TargetID); err != nil {
			return err
		}
	}
	return nil
}

// guardToolRunCompletion is the T030 completion guard. A successful preset tool
// run must name the summary artifact the handler registered; readiness, ownership
// and job provenance are re-checked in this transaction with the same predicate
// the model/evaluation guards use. A failed run never carries a result artifact
// (Complete clears it), so its output artifacts stay mere diagnostics links and
// can never be mistaken for a usable summary.
func guardToolRunCompletion(
	ctx context.Context,
	_ *Controller,
	tx *sql.Tx,
	job *Job,
	completion *Completion,
) error {
	if completion.State != StateSucceeded {
		return nil
	}
	if completion.ResultArtifactID == "" {
		return &ValidationError{
			Field:   "result_artifact_id",
			Message: "tool_run success must name the verified summary artifact",
		}
	}
	return verifyJobResultArtifact(ctx, tx, job, completion.ResultArtifactID)
}

// guardApplicationEvaluateCompletion is the T026 completion guard: it reads the
// sealed report artifact, verifies the manifest/dataset binding and the verdict
// rules, and only then records the immutable report (inside the same
// transaction). A claimed success that cannot be proven becomes a failed job
// with no report row; a business verdict (status=failed) stays a succeeded job
// with a failed report. Retryable guard failures leave the evaluation running
// for the next attempt; only a terminal outcome fails/cancels the evaluation row.
func guardApplicationEvaluateCompletion(
	ctx context.Context,
	c *Controller,
	tx *sql.Tx,
	job *Job,
	completion *Completion,
) error {
	if completion.State == StateSucceeded {
		if completion.ResultArtifactID == "" {
			return &ValidationError{
				Field:   "result_artifact_id",
				Message: "application_evaluate success must name the report artifact",
			}
		}
		if err := verifyJobResultArtifact(ctx, tx, job, completion.ResultArtifactID); err != nil {
			return err
		}
		failureCode, guardErr := c.guardEvaluationSuccess(ctx, tx, job, completion.ResultArtifactID)
		switch {
		case errors.Is(guardErr, errGuardTransient):
			completion.State = StateFailed
			completion.ErrorCode = CodeTransientStorageIO
		case guardErr != nil:
			return guardErr
		case failureCode != "":
			// The report is not something the server can accept: the job fails
			// with the stable guard code and no report row exists.
			completion.State = StateFailed
			completion.ErrorCode = failureCode
			completion.ResultArtifactID = ""
		}
	}
	if completion.State == StateFailed && !retryAllowed(job, completion.ErrorCode) {
		if err := failEvaluationTx(ctx, tx, job.TargetID, completion.ErrorCode); err != nil {
			return err
		}
	}
	if completion.State == StateCancelled {
		if err := cancelEvaluationTx(ctx, tx, job.TargetID); err != nil {
			return err
		}
	}
	return nil
}

// verifyJobResultArtifact refuses a result artifact that is not this job's own,
// already-ready output. A handler that completed its upload but has not waited
// for the finalize guard to activate it gets a validation error instead of a
// job that points at an unusable artifact.
func verifyJobResultArtifact(ctx context.Context, tx *sql.Tx, job *Job, artifactID string) error {
	var (
		owner   string
		state   string
		deleted sql.NullTime
	)
	err := tx.QueryRowContext(ctx, `
		SELECT a.owner_id::text, a.state, a.deleted_at
		  FROM platform.artifacts AS a
		  JOIN platform.background_job_artifacts AS l
		    ON l.artifact_id = a.id AND l.background_job_id = $2::uuid
		 WHERE a.id = $1::uuid`, artifactID, job.ID,
	).Scan(&owner, &state, &deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return &ValidationError{
			Field:   "result_artifact_id",
			Message: "the result artifact is not linked to this job",
		}
	}
	if err != nil {
		return fmt.Errorf("reading the result artifact: %w", err)
	}
	if owner != job.OwnerID {
		return &ValidationError{
			Field:   "result_artifact_id",
			Message: "the result artifact belongs to another owner",
		}
	}
	if deleted.Valid || state != "ready" {
		return &ValidationError{
			Field:   "result_artifact_id",
			Message: "the result artifact must be ready and not deleted",
		}
	}
	return nil
}

// retryJob returns a retryable failure to ready with exponential backoff. The
// attempt was already counted at claim time, so the next claim is attempt N+1.
func (c *Controller) retryJob(
	ctx context.Context,
	tx *sql.Tx,
	job *Job,
	errorCode string,
) (*Job, error) {
	delay := RetryDelay(job.AttemptCount, c.RetryBackoff)
	retried, err := scanJob(tx.QueryRowContext(ctx, `
		UPDATE platform.background_jobs
		   SET state = 'ready',
		       error_code = $2,
		       failure_class = $3,
		       last_error_at = now(),
		       available_at = now() + make_interval(secs => $4::double precision),
		       lease_token = NULL,
		       lease_deadline = NULL,
		       worker_id = NULL
		 WHERE id = $1::uuid
		RETURNING `+selectJobColumns,
		job.ID, errorCode, string(FailureInfrastructure), delay.Seconds(),
	))
	if err != nil {
		return nil, fmt.Errorf("returning the job to ready: %w", err)
	}
	return retried, nil
}

// artifactRow is the subset of platform.artifacts the guard reads.
type artifactRow struct {
	ObjectKey      string
	StagingKey     sql.NullString
	ExpectedSHA256 sql.NullString
	StoredSHA256   sql.NullString
	SizeBytes      sql.NullInt64
	State          string
	DeletedAt      sql.NullTime
	Revision       int64
}

// verifyAndActivateArtifact is the server-side finalize guard. It holds the
// artifact row lock for the duration, re-reads the bytes the CURRENT attempt
// wrote under its own fenced key, and activates only when tombstone, state,
// revision, size and hash all agree. Verified bytes are moved to the artifact's
// active object_key in one atomic rename immediately before the row is updated;
// the guard is the only writer of that key.
//
// A non-empty failure code means "this attempt failed business-wise; quarantine
// when the code says so". errGuardTransient means the store could not be read
// for an infrastructure reason and the attempt may be retried. Every rejection
// removes only the attempt key, so a paused older attempt that resumes cannot
// affect an active sealed object.
func (c *Controller) verifyAndActivateArtifact(
	ctx context.Context,
	tx *sql.Tx,
	job *Job,
	attemptToken string,
) (string, error) {
	var artifact artifactRow
	err := tx.QueryRowContext(ctx, `
		SELECT object_key, staging_object_key, expected_sha256, sha256, size_bytes,
		       state, deleted_at, revision
		  FROM platform.artifacts
		 WHERE id = $1::uuid
		 FOR UPDATE`, job.TargetID,
	).Scan(
		&artifact.ObjectKey, &artifact.StagingKey, &artifact.ExpectedSHA256,
		&artifact.StoredSHA256, &artifact.SizeBytes, &artifact.State,
		&artifact.DeletedAt, &artifact.Revision,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return CodeSourceDeleted, nil
	}
	if err != nil {
		return "", fmt.Errorf("reading the artifact: %w", err)
	}
	attemptKey := objectstore.AttemptSealedKey(job.ID, attemptToken)
	if artifact.DeletedAt.Valid {
		discardAttemptObject(c.Store, attemptKey)
		return CodeSourceDeleted, nil
	}
	if artifact.State != "scanning" {
		// A newer attempt may already have activated this artifact; this
		// attempt's own bytes are useless now.
		discardAttemptObject(c.Store, attemptKey)
		return CodeArtifactStateNotScanning, nil
	}
	if job.ExpectedRevision != nil && *job.ExpectedRevision != artifact.Revision {
		discardAttemptObject(c.Store, attemptKey)
		return CodeSourceChanged, nil
	}
	expectedSHA := strings.ToLower(strings.TrimSpace(nullText(artifact.ExpectedSHA256)))
	if !isSHA256Hex(expectedSHA) {
		discardAttemptObject(c.Store, attemptKey)
		return CodeArtifactExpectedHashMiss, nil
	}
	if c.Store == nil {
		return "", ErrObjectStoreUnavailable
	}
	var declaredSize *int64
	if artifact.SizeBytes.Valid {
		size := artifact.SizeBytes.Int64
		declaredSize = &size
	}
	object, failureCode, err := promoteAttemptObject(
		c.Store, attemptKey, artifact.ObjectKey, expectedSHA, declaredSize,
	)
	if err != nil {
		return "", err
	}
	if failureCode != "" {
		return failureCode, nil
	}

	result, err := tx.ExecContext(ctx, `
		UPDATE platform.artifacts
		   SET state = 'ready', sha256 = $2, revision = revision + 1
		 WHERE id = $1::uuid
		   AND state = 'scanning'
		   AND deleted_at IS NULL
		   AND revision = $3`,
		job.TargetID, object.SHA256, artifact.Revision,
	)
	if err != nil {
		return "", fmt.Errorf("activating the artifact: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return "", fmt.Errorf("activating the artifact: %w", err)
	}
	if affected != 1 {
		// The row lock should make this impossible; if it ever happens, refuse to
		// claim a success rather than report an activation that did not occur.
		return CodeSourceChanged, nil
	}
	return "", nil
}

// quarantineArtifact moves a still-scanning, non-deleted artifact to
// quarantined. The revision bump makes any in-flight job against the old
// revision stale.
func quarantineArtifact(ctx context.Context, tx *sql.Tx, artifactID string) error {
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.artifacts
		   SET state = 'quarantined', revision = revision + 1
		 WHERE id = $1::uuid
		   AND state = 'scanning'
		   AND deleted_at IS NULL`, artifactID); err != nil {
		return fmt.Errorf("quarantining the artifact: %w", err)
	}
	return nil
}

func validateCompletion(completion Completion) (Completion, error) {
	if !uuid.Valid(completion.JobID) {
		return Completion{}, ErrNotFound
	}
	if !uuid.Valid(completion.AttemptToken) {
		return Completion{}, &ValidationError{
			Field:   "attempt_token",
			Message: "attempt_token must be a UUID",
		}
	}
	if !uuid.Valid(completion.EventID) {
		return Completion{}, &ValidationError{Field: "event_id", Message: "event_id must be a UUID"}
	}
	state, ok := ResultState(string(completion.State))
	if !ok {
		return Completion{}, &ValidationError{
			Field:   "state",
			Message: "state must be succeeded, failed or cancelled",
		}
	}
	errorCode := strings.TrimSpace(completion.ErrorCode)
	if state == StateFailed && errorCode == "" {
		return Completion{}, &ValidationError{
			Field:   "error_code",
			Message: "a failed result must name its error_code",
		}
	}
	if errorCode != "" && len(errorCode) > 100 {
		return Completion{}, &ValidationError{
			Field:   "error_code",
			Message: "error_code must be at most 100 characters",
		}
	}
	resultArtifactID := strings.TrimSpace(completion.ResultArtifactID)
	if resultArtifactID != "" && !uuid.Valid(resultArtifactID) {
		return Completion{}, &ValidationError{
			Field:   "result_artifact_id",
			Message: "result_artifact_id must be a UUID",
		}
	}
	return Completion{
		JobID:            completion.JobID,
		AttemptToken:     completion.AttemptToken,
		EventID:          completion.EventID,
		State:            state,
		ErrorCode:        errorCode,
		ResultArtifactID: resultArtifactID,
	}, nil
}

// requestDigest is the content half of result-event idempotency: the same event
// id with a different state/code/result is a conflict, not a replay.
func requestDigest(completion Completion) string {
	sum := sha256.Sum256([]byte(
		string(completion.State) + "\x00" + completion.ErrorCode + "\x00" + completion.ResultArtifactID,
	))
	return hex.EncodeToString(sum[:])
}

func nullableText(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}

func nullText(value sql.NullString) string {
	if !value.Valid {
		return ""
	}
	return value.String
}
