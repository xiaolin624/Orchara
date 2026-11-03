package execution

// F03/T035: the SQL half of waiting conditions and the resume hand-over.
//
// Every statement here is called from the one transaction that owns the
// transition (SubmitResult's waiting branch, ConfirmExit's waiting branch, or
// maybeEnqueueResumeTx). Lock order is always Run -> run_waitings -> job/attempt,
// which is the order the ticket fixes; because every caller holds the Run row
// first, resolution and exit serialize instead of racing into two resumes.
//
// The database clock is the only clock: decision_at/exit_confirmed_at are written
// with now() in the statement, and the resume only exists after the row's CHECK
// pair (decision+decision_at, resume_job_id+exit_confirmed_at) is satisfied.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
)

// waitingRunFacts is one waiting Run locked for the hand-over.
type waitingRunFacts struct {
	ID                string
	OwnerID           string
	State             string
	Revision          int64
	BudgetPositive    bool
	CancelRequestedAt sql.NullTime
}

// waitingFactsRow is one locked platform.run_waitings row.
type waitingFactsRow struct {
	Kind             string
	CheckpointID     string
	Decision         []byte
	DecisionAt       sql.NullTime
	ExitConfirmedAt  sql.NullTime
	ResumeJobID      sql.NullString
	ResumeArtifactID sql.NullString
	// T036: the durable "this wait was cancelled" marker. It is never a decision
	// and never deletes the waiting evidence; it only makes the resume inert.
	CancelRequestedAt sql.NullTime
}

// waitingAttemptRow is the latest attempt of the Run (the one that stopped at the
// waiting checkpoint), locked for the hand-over.
type waitingAttemptRow struct {
	Token    string
	JobID    string
	ExitedAt sql.NullTime
}

// ErrWaitingConditionMissing is an internal invariant violation: a Run in a
// waiting_* state has no run_waitings row.
var ErrWaitingConditionMissing = errors.New("the waiting run has no waiting condition row")

// lockWaitingRunTx locks one Run and reads the facts the resume/failure decision
// needs. A missing/deleted Run is ErrRunNotFound (404 on the bridge).
func lockWaitingRunTx(ctx context.Context, tx *sql.Tx, runID string) (*waitingRunFacts, error) {
	var facts waitingRunFacts
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, owner_id::text, state, revision,
		       (budget_limit > 0) AS budget_positive, cancel_requested_at
		  FROM platform.runs
		 WHERE id = $1::uuid
		   AND deleted_at IS NULL
		 FOR UPDATE`,
		runID,
	).Scan(
		&facts.ID, &facts.OwnerID, &facts.State, &facts.Revision,
		&facts.BudgetPositive, &facts.CancelRequestedAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrRunNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the waiting run: %w", err)
	}
	return &facts, nil
}

// lockWaitingTx locks one Run's waiting condition. nil with a nil error means the
// Run never waited (the bridge answers run_state_conflict).
func lockWaitingTx(ctx context.Context, tx *sql.Tx, runID string) (*waitingFactsRow, error) {
	var facts waitingFactsRow
	err := tx.QueryRowContext(ctx, `
		SELECT kind, checkpoint_id, decision::text, decision_at, exit_confirmed_at,
		       resume_job_id::text, resume_artifact_id::text, cancel_requested_at
		  FROM platform.run_waitings
		 WHERE run_id = $1::uuid
		 FOR UPDATE`,
		runID,
	).Scan(
		&facts.Kind, &facts.CheckpointID, &facts.Decision, &facts.DecisionAt,
		&facts.ExitConfirmedAt, &facts.ResumeJobID, &facts.ResumeArtifactID,
		&facts.CancelRequestedAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the waiting condition: %w", err)
	}
	return &facts, nil
}

// lockLatestWaitingAttemptTx locks the latest attempt of the Run. It is the
// attempt that presented the waiting proposal and whose exit the supervisor must
// confirm before a resume exists.
func lockLatestWaitingAttemptTx(
	ctx context.Context, tx *sql.Tx, runID string,
) (*waitingAttemptRow, error) {
	var facts waitingAttemptRow
	err := tx.QueryRowContext(ctx, `
		SELECT token::text, job_id::text, exited_at
		  FROM platform.run_attempts
		 WHERE run_id = $1::uuid
		 ORDER BY started_at DESC, token DESC
		 LIMIT 1
		 FOR UPDATE`,
		runID,
	).Scan(&facts.Token, &facts.JobID, &facts.ExitedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the waiting attempt: %w", err)
	}
	return &facts, nil
}

// insertWaitingConditionTx writes the waiting condition for the current stop.
//
// One row per Run (PK run_id). A later resume can stop again at a new checkpoint:
// in that case the row is reset to the new wait. The reset only happens for a row
// that already produced a resume job; a row that still belongs to the current stop
// is an invariant violation rather than something to overwrite.
func insertWaitingConditionTx(
	ctx context.Context, tx *sql.Tx, runID, kind, checkpointID string,
) error {
	var stored string
	err := tx.QueryRowContext(ctx, `
		INSERT INTO platform.run_waitings (run_id, kind, checkpoint_id)
		VALUES ($1::uuid, $2, $3)
		ON CONFLICT (run_id) DO UPDATE
		   SET kind = EXCLUDED.kind,
		       checkpoint_id = EXCLUDED.checkpoint_id,
		       decision = NULL,
		       decision_at = NULL,
		       exit_confirmed_at = NULL,
		       resume_job_id = NULL,
		       resume_artifact_id = NULL,
		       updated_at = now()
		 WHERE platform.run_waitings.resume_job_id IS NOT NULL
		RETURNING run_id::text`,
		runID, kind, checkpointID,
	).Scan(&stored)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrWaitingConditionMissing
	}
	if err != nil {
		return fmt.Errorf("storing the waiting condition: %w", err)
	}
	return nil
}

// holdJobForWaitingTx holds the job without clearing its live attempt token: the
// attempt may still be running until the supervisor's exit evidence arrives, and
// that evidence is bound to the token. Completion/revocation happens only in the
// resume transaction.
func holdJobForWaitingTx(ctx context.Context, tx *sql.Tx, jobID string) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.jobs
		   SET state = 'held'
		 WHERE id = $1::uuid AND state IN ('leased', 'held')`,
		jobID,
	)
	if err != nil {
		return fmt.Errorf("holding the waiting job: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("holding the waiting job: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// moveRunToWaitingTx is the aggregate transition into waiting_*: state, phase
// waiting and revision+1, CAS on the state+revision the caller locked.
func moveRunToWaitingTx(
	ctx context.Context, tx *sql.Tx, runID, expectedState string,
	expectedRevision int64, kind string,
) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET state = $2,
		       phase = 'waiting',
		       revision = revision + 1
		 WHERE id = $1::uuid
		   AND state = $3
		   AND revision = $4
		   AND deleted_at IS NULL`,
		runID, waitingStateForKind(kind), expectedState, expectedRevision,
	)
	if err != nil {
		return fmt.Errorf("moving the run to waiting: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("moving the run to waiting: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// storeWaitingDecisionTx stores the resolution once, with the database clock.
// The `decision IS NULL` predicate is the once-only rule; the caller already holds
// the waiting row lock.
func storeWaitingDecisionTx(
	ctx context.Context, tx *sql.Tx, runID, kind string, decision []byte,
) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.run_waitings
		   SET decision = $2::jsonb,
		       decision_at = now(),
		       updated_at = now()
		 WHERE run_id = $1::uuid
		   AND kind = $3
		   AND decision IS NULL`,
		runID, decision, kind,
	)
	if err != nil {
		return fmt.Errorf("storing the waiting decision: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("storing the waiting decision: %w", err)
	}
	if affected != 1 {
		return ErrEventConflict
	}
	return nil
}

// clearWaitingJobTokenTx drops the dead attempt token from the held job once the
// supervisor confirmed the exit. The credential is already revoked; the held job
// must not keep naming it (the T031 exit-without-result park clears it too). The
// held state keeps the row in one_live_job_per_run, so no second job can appear.
func clearWaitingJobTokenTx(ctx context.Context, tx *sql.Tx, jobID string) error {
	_, err := tx.ExecContext(ctx, `
		UPDATE platform.jobs
		   SET current_attempt_token = NULL
		 WHERE id = $1::uuid AND state = 'held'`,
		jobID,
	)
	if err != nil {
		return fmt.Errorf("clearing the waiting job token: %w", err)
	}
	return nil
}

// confirmWaitingExitTx records the supervisor's exit fact once.
func confirmWaitingExitTx(ctx context.Context, tx *sql.Tx, runID string) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.run_waitings
		   SET exit_confirmed_at = COALESCE(exit_confirmed_at, now()),
		       updated_at = now()
		 WHERE run_id = $1::uuid`,
		runID,
	)
	if err != nil {
		return fmt.Errorf("confirming the waiting exit: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("confirming the waiting exit: %w", err)
	}
	if affected != 1 {
		return ErrWaitingConditionMissing
	}
	return nil
}

// waitingDecisionAtTx reads back the stored decision timestamp.
func waitingDecisionAtTx(ctx context.Context, tx *sql.Tx, runID string) (time.Time, error) {
	var decisionAt sql.NullTime
	err := tx.QueryRowContext(ctx, `
		SELECT decision_at FROM platform.run_waitings WHERE run_id = $1::uuid`,
		runID,
	).Scan(&decisionAt)
	if errors.Is(err, sql.ErrNoRows) {
		return time.Time{}, ErrWaitingConditionMissing
	}
	if err != nil {
		return time.Time{}, fmt.Errorf("reading the waiting decision time: %w", err)
	}
	if !decisionAt.Valid {
		return time.Time{}, errors.New("the waiting decision has no decision_at")
	}
	return decisionAt.Time, nil
}

// ownerDisabledTx reports whether the Run owner is disabled (or missing, which
// fails closed: a principal that cannot be loaded cannot resume work).
func ownerDisabledTx(ctx context.Context, tx *sql.Tx, ownerID string) (bool, error) {
	var disabled bool
	err := tx.QueryRowContext(ctx, `
		SELECT COALESCE(disabled_at IS NOT NULL, true)
		  FROM platform.principals
		 WHERE id = $1::uuid`,
		ownerID,
	).Scan(&disabled)
	if errors.Is(err, sql.ErrNoRows) {
		return true, nil
	}
	if err != nil {
		return false, fmt.Errorf("reading the run owner permission: %w", err)
	}
	return disabled, nil
}

// checkWaitingChildTx enforces the children rule: the stored decision must name a
// Run whose parent is this Run, which still exists and is terminal. A refusal is a
// typed 409 (the resolution is rolled back), never a terminal parent outcome.
func checkWaitingChildTx(
	ctx context.Context, tx *sql.Tx, parentRunID string, decision []byte,
) error {
	var document struct {
		ChildRunID string `json:"child_run_id"`
	}
	if err := json.Unmarshal(decision, &document); err != nil {
		return fmt.Errorf("decoding the stored children decision: %w", err)
	}
	childID := strings.ToLower(strings.TrimSpace(document.ChildRunID))
	if childID == "" {
		return &WaitingChildError{Reason: ChildReasonNotFound}
	}
	var (
		parent  sql.NullString
		state   string
		deleted sql.NullTime
	)
	err := tx.QueryRowContext(ctx, `
		SELECT parent_run_id::text, state, deleted_at
		  FROM platform.runs
		 WHERE id = $1::uuid`,
		childID,
	).Scan(&parent, &state, &deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return &WaitingChildError{Reason: ChildReasonNotFound}
	}
	if err != nil {
		return fmt.Errorf("reading the referenced child run: %w", err)
	}
	if deleted.Valid || !parent.Valid || !strings.EqualFold(parent.String, parentRunID) {
		return &WaitingChildError{Reason: ChildReasonNotChild}
	}
	if !terminalRunState(state) {
		return &WaitingChildError{Reason: ChildReasonNotTerminal}
	}
	return nil
}

// insertResumeArtifactTx writes the sealed canonical resume payload as a ready
// artifact owned by the Run owner. The bytes are written by the caller first; the
// real SHA-256/size come from the object store.
func insertResumeArtifactTx(
	ctx context.Context, tx *sql.Tx, artifactID, ownerID, objectKey, sha256 string, sizeBytes int64,
) error {
	_, err := tx.ExecContext(ctx, `
		INSERT INTO platform.artifacts
		    (id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision)
		VALUES ($1::uuid, $2::uuid, $3, 'resume_input', $4, 'application/json', $5, 'ready', 1)`,
		artifactID, ownerID, objectKey, sha256, sizeBytes,
	)
	if err != nil {
		return fmt.Errorf("storing the resume artifact: %w", err)
	}
	return nil
}

// insertResumeJobTx inserts the one resume job and names the sealed payload.
func insertResumeJobTx(
	ctx context.Context, tx *sql.Tx, jobID, runID, artifactID string,
) error {
	_, err := tx.ExecContext(ctx, `
		INSERT INTO platform.jobs (id, run_id, kind, state, resume_artifact_id)
		VALUES ($1::uuid, $2::uuid, 'resume', 'ready', $3::uuid)`,
		jobID, runID, artifactID,
	)
	if err != nil {
		return fmt.Errorf("inserting the resume job: %w", err)
	}
	return nil
}

// updateWaitingResumeTx attaches the single resume job to the waiting condition.
// The `resume_job_id IS NULL` predicate is the once-only rule behind the UNIQUE
// column; no second resume can name a different job.
func updateWaitingResumeTx(
	ctx context.Context, tx *sql.Tx, runID, jobID, artifactID string,
) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.run_waitings
		   SET resume_job_id = $2::uuid,
		       resume_artifact_id = $3::uuid,
		       updated_at = now()
		 WHERE run_id = $1::uuid
		   AND resume_job_id IS NULL`,
		runID, jobID, artifactID,
	)
	if err != nil {
		return fmt.Errorf("attaching the resume job: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("attaching the resume job: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// queueWaitingRunTx is the resume's Run aggregate transition: waiting_* -> queued,
// phase idle, revision+1, CAS on the locked state+revision.
func queueWaitingRunTx(
	ctx context.Context, tx *sql.Tx, run *waitingRunFacts, kind string,
) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET state = 'queued',
		       phase = 'idle',
		       revision = revision + 1
		 WHERE id = $1::uuid
		   AND state = $2
		   AND revision = $3
		   AND deleted_at IS NULL`,
		run.ID, waitingStateForKind(kind), run.Revision,
	)
	if err != nil {
		return fmt.Errorf("queueing the resumed run: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("queueing the resumed run: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// failWaitingRunTx is the "do not resume" outcome: the wait is over (the exit fact
// exists) but the owner lost permission or the budget/deadline refuses more work.
// The Run commits failed(reason) with no output artifact; the old job completes,
// the token is revoked and the slot is released exactly once. The reason is
// recorded in runs.error_code and the same-transaction run.status event.
func failWaitingRunTx(
	ctx context.Context, tx *sql.Tx, run *waitingRunFacts, attempt *waitingAttemptRow, errorCode string,
) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET state = 'failed',
		       phase = 'idle',
		       revision = revision + 1,
		       finished_at = now(),
		       output_artifact_id = NULL,
		       error_code = $4
		 WHERE id = $1::uuid
		   AND state = $2
		   AND revision = $3
		   AND deleted_at IS NULL`,
		run.ID, run.State, run.Revision, errorCode,
	)
	if err != nil {
		return fmt.Errorf("failing the waiting run: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("failing the waiting run: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	if err := completeJobTx(ctx, tx, attempt.JobID); err != nil {
		return err
	}
	if err := revokeAttemptTx(ctx, tx, attempt.Token); err != nil {
		return err
	}
	if err := releaseAllocationOnceTx(ctx, tx, "job:"+attempt.JobID); err != nil {
		return err
	}
	if _, err := AppendRunStatusTx(ctx, tx, run.ID); err != nil {
		return err
	}
	return nil
}

// waitingFactsRead is the owner-facing read (no lock, no write).
func waitingFactsRead(ctx context.Context, db *sql.DB, runID string) (*WaitingFacts, error) {
	var (
		facts      WaitingFacts
		decision   []byte
		decisionAt sql.NullTime
		exitAt     sql.NullTime
		resumeJob  sql.NullString
		resumeArt  sql.NullString
		jobState   sql.NullString
	)
	err := db.QueryRowContext(ctx, `
		SELECT w.kind, w.checkpoint_id, w.decision::text, w.decision_at,
		       w.exit_confirmed_at, w.resume_job_id::text,
		       w.resume_artifact_id::text, j.state
		  FROM platform.run_waitings AS w
		  LEFT JOIN platform.jobs AS j ON j.id = w.resume_job_id
		 WHERE w.run_id = $1::uuid`,
		runID,
	).Scan(
		&facts.Kind, &facts.CheckpointID, &decision, &decisionAt,
		&exitAt, &resumeJob, &resumeArt, &jobState,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the waiting facts: %w", err)
	}
	facts.Outcome = waitingDecisionOutcome(decision)
	if decisionAt.Valid {
		value := decisionAt.Time
		facts.DecisionAt = &value
	}
	if exitAt.Valid {
		value := exitAt.Time
		facts.ExitConfirmedAt = &value
	}
	if resumeJob.Valid {
		value := resumeJob.String
		facts.ResumeJobID = &value
	}
	if resumeArt.Valid {
		value := resumeArt.String
		facts.ResumeArtifactID = &value
	}
	if jobState.Valid {
		facts.ResumeJobState = jobState.String
	}
	return &facts, nil
}
