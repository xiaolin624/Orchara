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

	state := normalized.State
	errorCode := normalized.ErrorCode
	resultArtifactID := normalized.ResultArtifactID

	// artifact_finalize activation is the server's: the result *is* the target
	// artifact, so a worker-supplied result_artifact_id could only be a second,
	// unverified artifact reference. Refuse it instead of recording it.
	if job.Kind == KindArtifactFinalize && state == StateSucceeded && resultArtifactID != "" {
		return nil, &ValidationError{
			Field:   "result_artifact_id",
			Message: "artifact_finalize activation is server-side; the result artifact is the job target",
		}
	}

	// model_validate completion records the compatibility report artifact the
	// handler registered. The handler cannot name an artifact it did not upload:
	// readiness, ownership and job provenance are re-checked in this
	// transaction. A failed compatibility verdict is still a successful job, so
	// only a succeeded result is checked here.
	if job.Kind == KindModelValidate && state == StateSucceeded {
		if resultArtifactID == "" {
			return nil, &ValidationError{
				Field:   "result_artifact_id",
				Message: "model_validate success must name the compatibility report artifact",
			}
		}
		if err := verifyJobResultArtifact(ctx, tx, job, resultArtifactID); err != nil {
			return nil, err
		}
	}

	// artifact_finalize: the server, not the worker, decides activation.
	if job.Kind == KindArtifactFinalize {
		switch state {
		case StateSucceeded:
			failureCode, guardErr := c.verifyAndActivateArtifact(ctx, tx, job)
			switch {
			case errors.Is(guardErr, errGuardTransient):
				state = StateFailed
				errorCode = CodeTransientStorageIO
			case guardErr != nil:
				return nil, guardErr
			case failureCode != "":
				// Guard override: any failure path must leave the artifact
				// unusable, so a claimed success that cannot be proven is a
				// failed attempt.
				state = StateFailed
				errorCode = failureCode
				resultArtifactID = ""
				if ImpliesQuarantine(failureCode) {
					if err := quarantineArtifact(ctx, tx, job.TargetID); err != nil {
						return nil, err
					}
				}
			}
		case StateFailed:
			if ImpliesQuarantine(errorCode) {
				if err := quarantineArtifact(ctx, tx, job.TargetID); err != nil {
					return nil, err
				}
			}
		}
	}

	// application_evaluate (T026): the completion guard reads the sealed report
	// artifact, verifies the manifest/dataset binding and the verdict rules, and
	// only then records the immutable report. A claimed success that cannot be
	// proven becomes a failed job with no report row; a business verdict
	// (status=failed) stays a succeeded job with a failed report. Retryable
	// guard failures leave the evaluation running for the next attempt; only a
	// terminal outcome fails/cancels the evaluation row.
	if job.Kind == KindApplicationEvaluate {
		if state == StateSucceeded {
			if resultArtifactID == "" {
				return nil, &ValidationError{
					Field:   "result_artifact_id",
					Message: "application_evaluate success must name the report artifact",
				}
			}
			if err := verifyJobResultArtifact(ctx, tx, job, resultArtifactID); err != nil {
				return nil, err
			}
			failureCode, guardErr := c.guardEvaluationSuccess(ctx, tx, job, resultArtifactID)
			switch {
			case errors.Is(guardErr, errGuardTransient):
				state = StateFailed
				errorCode = CodeTransientStorageIO
			case guardErr != nil:
				return nil, guardErr
			case failureCode != "":
				// The report is not something the server can accept: the job
				// fails with the stable guard code and no report row exists.
				state = StateFailed
				errorCode = failureCode
				resultArtifactID = ""
			}
		}
		if state == StateFailed {
			retryable := Retryable(job.Kind, errorCode) && job.AttemptCount < MaxAttempts
			if !retryable {
				if err := failEvaluationTx(ctx, tx, job.TargetID, errorCode); err != nil {
					return nil, err
				}
			}
		}
		if state == StateCancelled {
			if err := cancelEvaluationTx(ctx, tx, job.TargetID); err != nil {
				return nil, err
			}
		}
	}

	if state == StateFailed {
		retryable := Retryable(job.Kind, errorCode)
		if retryable && job.AttemptCount < MaxAttempts {
			retried, err := c.retryJob(ctx, tx, job, errorCode)
			if err != nil {
				return nil, err
			}
			if err := tx.Commit(); err != nil {
				return nil, fmt.Errorf("committing the retry: %w", err)
			}
			return retried, nil
		}
	}

	failureClass := any(nil)
	switch state {
	case StateFailed:
		failureClass = string(ClassifyFailure(job.Kind, errorCode))
	}
	if state == StateSucceeded || state == StateCancelled {
		errorCode = normalized.ErrorCode
		if state == StateSucceeded {
			errorCode = ""
			failureClass = nil
		}
	}
	if state == StateFailed {
		resultArtifactID = "" // a failed job never carries a result artifact
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
		job.ID, string(state), nullableText(errorCode), failureClass,
		normalized.EventID, digest, nullableText(resultArtifactID),
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
// artifact row lock for the duration, re-reads the sealed object bytes, and
// activates only when tombstone, state, revision, size and hash all agree.
//
// A non-empty failure code means "this attempt failed business-wise; quarantine
// when the code says so". errGuardTransient means the store could not be read
// for an infrastructure reason and the attempt may be retried.
func (c *Controller) verifyAndActivateArtifact(
	ctx context.Context,
	tx *sql.Tx,
	job *Job,
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
	if artifact.DeletedAt.Valid {
		return CodeSourceDeleted, nil
	}
	if artifact.State != "scanning" {
		return CodeArtifactStateNotScanning, nil
	}
	if job.ExpectedRevision != nil && *job.ExpectedRevision != artifact.Revision {
		return CodeSourceChanged, nil
	}
	expectedSHA := strings.ToLower(strings.TrimSpace(nullText(artifact.ExpectedSHA256)))
	if !isSHA256Hex(expectedSHA) {
		return CodeArtifactExpectedHashMiss, nil
	}
	if c.Store == nil {
		return "", ErrObjectStoreUnavailable
	}
	object, err := c.Store.Hash(artifact.ObjectKey)
	switch {
	case errors.Is(err, objectstore.ErrNotExist):
		return CodeArtifactObjectMissing, nil
	case errors.Is(err, objectstore.ErrTooLarge):
		return CodeArtifactTooLarge, nil
	case errors.Is(err, objectstore.ErrInvalidKey):
		return CodeArtifactObjectMissing, nil
	case err != nil:
		return "", fmt.Errorf("%w: %v", errGuardTransient, err)
	}
	if artifact.SizeBytes.Valid && object.Size != artifact.SizeBytes.Int64 {
		return CodeArtifactSizeMismatch, nil
	}
	if object.SHA256 != expectedSHA {
		return CodeArtifactHashMismatch, nil
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
