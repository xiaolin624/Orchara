package execution

// F03/T036: the SQL half of cancellation and the unknown-operation ledger.
//
// Lock order is the package-wide Run -> waiting -> job/attempt order: the cancel
// endpoint and both unknown-operation endpoints lock the Run first, so they
// serialise with SubmitResult/ConfirmExit/maybeEnqueueResumeTx instead of racing
// them. Every timestamp is the database clock; Go only formats values read back.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"orchara/platform/internal/uuid"
)

// cancelRunFacts is one Run row locked for a cancel/unknown decision.
type cancelRunFacts struct {
	ID                string
	OwnerID           string
	State             string
	Phase             string
	Revision          int64
	CancelRequestedAt sql.NullTime
	ErrorCode         sql.NullString
}

// cancelJobFacts is the one live job row (ready/leased/held) of a Run.
type cancelJobFacts struct {
	ID    string
	State string
}

// cancelAttemptFacts is the latest attempt of a Run (used for the resolve hook).
type cancelAttemptFacts struct {
	Token    string
	JobID    string
	ExitedAt sql.NullTime
}

// unknownOperationFacts is one ledger row locked for a resolution.
type unknownOperationFacts struct {
	Reason     string
	Resolved   bool
	ResolvedAt sql.NullTime
}

// lockCancelRunTx locks one Run and reads the facts the cancel decision needs.
func lockCancelRunTx(ctx context.Context, tx *sql.Tx, runID string) (*cancelRunFacts, error) {
	var facts cancelRunFacts
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, owner_id::text, state, phase, revision,
		       cancel_requested_at, error_code
		  FROM platform.runs
		 WHERE id = $1::uuid
		   AND deleted_at IS NULL
		 FOR UPDATE`,
		runID,
	).Scan(
		&facts.ID, &facts.OwnerID, &facts.State, &facts.Phase, &facts.Revision,
		&facts.CancelRequestedAt, &facts.ErrorCode,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrRunNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the run for cancellation: %w", err)
	}
	return &facts, nil
}

// cancelDispatchFactsTx returns the Run's latest live attempt (if any) and its
// current live job. "Live" means not exited/revoked; the database decides it.
func cancelDispatchFactsTx(
	ctx context.Context, tx *sql.Tx, runID string,
) (*cancelAttemptFacts, *cancelJobFacts, error) {
	var attempt cancelAttemptFacts
	var jobState string
	err := tx.QueryRowContext(ctx, `
		SELECT a.token::text, a.job_id::text, j.state
		  FROM platform.run_attempts AS a
		  JOIN platform.jobs AS j ON j.id = a.job_id AND j.run_id = a.run_id
		 WHERE a.run_id = $1::uuid
		   AND a.exited_at IS NULL
		   AND a.revoked_at IS NULL
		 ORDER BY a.started_at DESC, a.token DESC
		 LIMIT 1`,
		runID,
	).Scan(&attempt.Token, &attempt.JobID, &jobState)
	if errors.Is(err, sql.ErrNoRows) {
		attempt = cancelAttemptFacts{}
	} else if err != nil {
		return nil, nil, fmt.Errorf("reading the live cancel attempt: %w", err)
	}
	if attempt.Token != "" {
		return &attempt, &cancelJobFacts{ID: attempt.JobID, State: jobState}, nil
	}
	var job cancelJobFacts
	err = tx.QueryRowContext(ctx, `
		SELECT id::text, state
		  FROM platform.jobs
		 WHERE run_id = $1::uuid
		   AND state IN ('ready', 'leased', 'held')
		 ORDER BY created_at DESC, id DESC
		 LIMIT 1`,
		runID,
	).Scan(&job.ID, &job.State)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil, nil
	}
	if err != nil {
		return nil, nil, fmt.Errorf("reading the live cancel job: %w", err)
	}
	return nil, &job, nil
}

// markWaitingCancelRequestedTx is the "cancelled wait" marker. The waiting row is
// never deleted: it keeps the checkpoint/decision/exit evidence for audit. A Run
// that never waited is a no-op.
func markWaitingCancelRequestedTx(ctx context.Context, tx *sql.Tx, runID string) error {
	_, err := tx.ExecContext(ctx, `
		UPDATE platform.run_waitings
		   SET cancel_requested_at = COALESCE(cancel_requested_at, now()),
		       updated_at = now()
		 WHERE run_id = $1::uuid`,
		runID,
	)
	if err != nil {
		return fmt.Errorf("marking the waiting condition cancelled: %w", err)
	}
	return nil
}

// moveRunToCancellingTx records the intent once and moves a Run with a live unit
// to cancelling. The phase is kept (the contract phase enum has no cancelling
// member); the CAS on state+revision makes a concurrent transition a conflict.
func moveRunToCancellingTx(ctx context.Context, tx *sql.Tx, run *cancelRunFacts) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET cancel_requested_at = COALESCE(cancel_requested_at, now()),
		       state = 'cancelling',
		       revision = revision + 1
		 WHERE id = $1::uuid
		   AND state = $2
		   AND revision = $3
		   AND deleted_at IS NULL`,
		run.ID, run.State, run.Revision,
	)
	if err != nil {
		return fmt.Errorf("moving the run to cancelling: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("moving the run to cancelling: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	// F06/T050: a pending approval can never be decided once the Run is
	// cancelling. The sealed proposal stays for audit; the state says a decision
	// is no longer possible.
	if err := invalidateRunApprovalsTx(ctx, tx, run.ID, "run_cancel_requested"); err != nil {
		return err
	}
	return nil
}

// commitImmediateCancelTx is the no-live-unit cancel: nothing can still run, so
// the Run commits cancelled in this transaction. A never-run job becomes void
// (STATE-MACHINES §3); the waiting row keeps its evidence but can never resume.
func commitImmediateCancelTx(
	ctx context.Context, tx *sql.Tx, run *cancelRunFacts, job *cancelJobFacts,
) error {
	if job != nil {
		if err := voidJobForCancelTx(ctx, tx, job.ID); err != nil {
			return err
		}
		if err := releaseAllocationIfPresentTx(ctx, tx, "job:"+job.ID); err != nil {
			return err
		}
	}
	// Defensive: no live attempt was observed under the Run lock, so this only
	// clears a stale unrevoked row if one exists.
	if err := revokeRunAttemptsTx(ctx, tx, run.ID); err != nil {
		return err
	}
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET state = 'cancelled',
		       phase = 'idle',
		       revision = revision + 1,
		       finished_at = now(),
		       output_artifact_id = NULL,
		       error_code = NULL,
		       cancel_requested_at = COALESCE(cancel_requested_at, now()),
		       recovery_reason = NULL,
		       recovery_since = NULL
		 WHERE id = $1::uuid
		   AND state = $2
		   AND revision = $3
		   AND deleted_at IS NULL`,
		run.ID, run.State, run.Revision,
	)
	if err != nil {
		return fmt.Errorf("committing the cancelled run: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("committing the cancelled run: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	if err := invalidateRunApprovalsTx(ctx, tx, run.ID, "run_cancelled"); err != nil {
		return err
	}
	if _, err := AppendRunStatusTx(ctx, tx, run.ID); err != nil {
		return err
	}
	return nil
}

// commitCancelReconciliationTx is the "unknown side effects exist" cancel
// outcome: the Run becomes needs_reconciliation (never cancelled) and the job is
// held so nothing new is dispatched. Resolving the last unknown re-invokes the
// finalizer, which may then commit cancelled.
func (s *Service) commitCancelReconciliationTx(
	ctx context.Context, tx *sql.Tx, run *cancelRunFacts, job *cancelJobFacts,
) error {
	if job != nil {
		if err := holdJobForCancelTx(ctx, tx, job.ID); err != nil {
			return err
		}
		if err := releaseAllocationIfPresentTx(ctx, tx, "job:"+job.ID); err != nil {
			return err
		}
	}
	if err := revokeRunAttemptsTx(ctx, tx, run.ID); err != nil {
		return err
	}
	if run.State == "needs_reconciliation" {
		// Already parked: only the intent is new, so no state change and no event.
		_, err := tx.ExecContext(ctx, `
			UPDATE platform.runs
			   SET cancel_requested_at = COALESCE(cancel_requested_at, now())
			 WHERE id = $1::uuid`,
			run.ID,
		)
		if err != nil {
			return fmt.Errorf("recording the cancel intent: %w", err)
		}
		return nil
	}
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET state = 'needs_reconciliation',
		       phase = 'idle',
		       revision = revision + 1,
		       finished_at = NULL,
		       output_artifact_id = NULL,
		       error_code = $4,
		       cancel_requested_at = COALESCE(cancel_requested_at, now()),
		       recovery_reason = NULL,
		       recovery_since = NULL
		 WHERE id = $1::uuid
		   AND state = $2
		   AND revision = $3
		   AND deleted_at IS NULL`,
		run.ID, run.State, run.Revision, UnknownOperationsErrorCode,
	)
	if err != nil {
		return fmt.Errorf("committing the reconciliation park: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("committing the reconciliation park: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	if err := invalidateRunApprovalsTx(ctx, tx, run.ID, "run_cancel_requested"); err != nil {
		return err
	}
	if _, err := AppendRunStatusTx(ctx, tx, run.ID); err != nil {
		return err
	}
	return nil
}

// voidJobForCancelTx voids a job that provably never ran (ready/held, or the
// leased job of a started=false exit) or was already void. A completed job stays
// completed (the execution happened).
func voidJobForCancelTx(ctx context.Context, tx *sql.Tx, jobID string) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.jobs
		   SET state = 'void',
		       current_attempt_token = NULL
		 WHERE id = $1::uuid
		   AND state IN ('ready', 'leased', 'held', 'void')`,
		jobID,
	)
	if err != nil {
		return fmt.Errorf("voiding the cancelled job: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("voiding the cancelled job: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// holdJobForCancelTx stops dispatching a job whose Run is parked for
// reconciliation; a ready job becomes held (idempotent for an already held one).
func holdJobForCancelTx(ctx context.Context, tx *sql.Tx, jobID string) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.jobs
		   SET state = 'held',
		       current_attempt_token = NULL
		 WHERE id = $1::uuid
		   AND state IN ('ready', 'leased', 'held')`,
		jobID,
	)
	if err != nil {
		return fmt.Errorf("holding the reconciled job: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("holding the reconciled job: %w", err)
	}
	if affected != 1 {
		return ErrStaleAttempt
	}
	return nil
}

// revokeRunAttemptsTx clears any still-unrevoked credential of the Run. After a
// no-live-attempt decision this is defensive; it never resurrects a unit.
func revokeRunAttemptsTx(ctx context.Context, tx *sql.Tx, runID string) error {
	_, err := tx.ExecContext(ctx, `
		UPDATE platform.run_attempts
		   SET revoked_at = COALESCE(revoked_at, now())
		 WHERE run_id = $1::uuid
		   AND revoked_at IS NULL`,
		runID,
	)
	if err != nil {
		return fmt.Errorf("revoking the cancelled attempts: %w", err)
	}
	return nil
}

// releaseAllocationIfPresentTx is the idempotent release for a cancel that may
// never have taken a worker slot (a queued Run has no allocation yet).
func releaseAllocationIfPresentTx(ctx context.Context, tx *sql.Tx, holderKey string) error {
	var exists bool
	err := tx.QueryRowContext(ctx, `
		SELECT EXISTS (
		    SELECT 1 FROM platform.quota_allocations
		     WHERE scope = $1 AND dimension = $2 AND holder_key = $3
		)`,
		quotaScope, quotaDimension, holderKey,
	).Scan(&exists)
	if err != nil {
		return fmt.Errorf("reading the cancel allocation: %w", err)
	}
	if !exists {
		return nil
	}
	return releaseAllocationOnceTx(ctx, tx, holderKey)
}

// requireLiveAttemptTx binds the presented token to the Run. A worker must hold
// the live lease; a supervisor resolving after the exit may present a token whose
// attempt already exited/revoked (allowAfterExit).
func requireLiveAttemptTx(
	ctx context.Context, tx *sql.Tx, runID, token string, allowAfterExit bool,
) error {
	if allowAfterExit {
		var exited sql.NullTime
		err := tx.QueryRowContext(ctx, `
			SELECT a.exited_at
			  FROM platform.run_attempts AS a
			  JOIN platform.jobs AS j ON j.id = a.job_id AND j.run_id = a.run_id
			 WHERE a.token = $1::uuid
			   AND a.run_id = $2::uuid`,
			token, runID,
		).Scan(&exited)
		if errors.Is(err, sql.ErrNoRows) {
			return ErrStaleAttempt
		}
		if err != nil {
			return fmt.Errorf("reading the unknown-operation attempt: %w", err)
		}
		return nil
	}
	var live bool
	err := tx.QueryRowContext(ctx, `
		SELECT (a.exited_at IS NULL
		        AND a.revoked_at IS NULL
		        AND a.lease_deadline > now()
		        AND j.current_attempt_token = a.token)
		  FROM platform.run_attempts AS a
		  JOIN platform.jobs AS j ON j.id = a.job_id AND j.run_id = a.run_id
		 WHERE a.token = $1::uuid
		   AND a.run_id = $2::uuid`,
		token, runID,
	).Scan(&live)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrStaleAttempt
	}
	if err != nil {
		return fmt.Errorf("reading the unknown-operation attempt: %w", err)
	}
	if !live {
		return ErrStaleAttempt
	}
	return nil
}

// RequireLiveAttemptTx is the exported form of the run-scoped attempt proof for
// creation paths outside this package. F07/T078's children.Spawn uses it exactly
// like every execution-protocol route: worker mTLS plus a live, current attempt
// token is the whole authorization for creating a child Run.
func RequireLiveAttemptTx(ctx context.Context, tx *sql.Tx, runID, token string) error {
	return requireLiveAttemptTx(ctx, tx, runID, token, false)
}

// insertUnknownOperationTx records one operation id once. ON CONFLICT DO NOTHING
// is the idempotency; the caller re-reads the row and compares the reason.
func insertUnknownOperationTx(
	ctx context.Context, tx *sql.Tx, runID, operationID, reason string,
) error {
	_, err := tx.ExecContext(ctx, `
		INSERT INTO platform.run_unknown_operations
		    (id, run_id, operation_id, reason)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4)
		ON CONFLICT (run_id, operation_id) DO NOTHING`,
		uuid.NewString(), runID, operationID, reason,
	)
	if err != nil {
		return fmt.Errorf("recording the unknown operation: %w", err)
	}
	return nil
}

// readUnknownOperationTx reads one ledger row without locking.
func readUnknownOperationTx(
	ctx context.Context, tx *sql.Tx, runID, operationID string,
) (*UnknownOperation, error) {
	var (
		reason     string
		resolvedAt sql.NullTime
	)
	err := tx.QueryRowContext(ctx, `
		SELECT reason, resolved_at
		  FROM platform.run_unknown_operations
		 WHERE run_id = $1::uuid AND operation_id = $2::uuid`,
		runID, operationID,
	).Scan(&reason, &resolvedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrUnknownOperationNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the unknown operation: %w", err)
	}
	return &UnknownOperation{
		RunID:       runID,
		OperationID: operationID,
		Reason:      reason,
		Resolved:    resolvedAt.Valid,
		ResolvedAt:  timePtrOf(resolvedAt),
	}, nil
}

// lockUnknownOperationTx locks one ledger row for the resolution transaction.
func lockUnknownOperationTx(
	ctx context.Context, tx *sql.Tx, runID, operationID string,
) (*unknownOperationFacts, error) {
	var facts unknownOperationFacts
	err := tx.QueryRowContext(ctx, `
		SELECT reason, resolved_at
		  FROM platform.run_unknown_operations
		 WHERE run_id = $1::uuid AND operation_id = $2::uuid
		 FOR UPDATE`,
		runID, operationID,
	).Scan(&facts.Reason, &facts.ResolvedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrUnknownOperationNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("locking the unknown operation: %w", err)
	}
	facts.Resolved = facts.ResolvedAt.Valid
	return &facts, nil
}

// resolveUnknownOperationTx marks the operation resolved once, with the database
// clock. The NULL predicate is the idempotency under the row lock.
func resolveUnknownOperationTx(
	ctx context.Context, tx *sql.Tx, runID, operationID string,
) error {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.run_unknown_operations
		   SET resolved_at = now()
		 WHERE run_id = $1::uuid
		   AND operation_id = $2::uuid
		   AND resolved_at IS NULL`,
		runID, operationID,
	)
	if err != nil {
		return fmt.Errorf("resolving the unknown operation: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("resolving the unknown operation: %w", err)
	}
	if affected != 1 {
		return ErrEventConflict
	}
	return nil
}

// lockLatestCancelAttemptTx locks the latest attempt of a Run. It is the attempt
// whose exit fact allows the resolve hook to re-drive the finalizer.
func lockLatestCancelAttemptTx(
	ctx context.Context, tx *sql.Tx, runID string,
) (*cancelAttemptFacts, error) {
	var facts cancelAttemptFacts
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
		return nil, fmt.Errorf("reading the latest cancel attempt: %w", err)
	}
	return &facts, nil
}

// cancelRunState reads the committed Run state after the resolution transaction.
func cancelRunState(ctx context.Context, db *sql.DB, runID string) (string, error) {
	var state string
	err := db.QueryRowContext(ctx, `
		SELECT state FROM platform.runs WHERE id = $1::uuid`, runID).Scan(&state)
	if errors.Is(err, sql.ErrNoRows) {
		return "", ErrRunNotFound
	}
	if err != nil {
		return "", fmt.Errorf("reading the resolved run state: %w", err)
	}
	return state, nil
}

// runCancelIntentTx reads the Run's cancel intent under its already-held lock.
// The finalizer uses it to let cancel win over an uncommitted result.
func runCancelIntentTx(
	ctx context.Context, tx *sql.Tx, runID string,
) (sql.NullTime, error) {
	var cancelAt sql.NullTime
	err := tx.QueryRowContext(ctx, `
		SELECT cancel_requested_at FROM platform.runs WHERE id = $1::uuid`,
		runID,
	).Scan(&cancelAt)
	if err != nil {
		return sql.NullTime{}, fmt.Errorf("reading the run cancel intent: %w", err)
	}
	return cancelAt, nil
}

// runErrorCodeTx reads the Run's recorded error code under its already-held lock.
func runErrorCodeTx(
	ctx context.Context, tx *sql.Tx, runID string,
) (sql.NullString, error) {
	var code sql.NullString
	err := tx.QueryRowContext(ctx, `
		SELECT error_code FROM platform.runs WHERE id = $1::uuid`,
		runID,
	).Scan(&code)
	if err != nil {
		return sql.NullString{}, fmt.Errorf("reading the run error code: %w", err)
	}
	return code, nil
}

// timePtrOf turns a nullable timestamp into the bridge's optional JSON value.
func timePtrOf(value sql.NullTime) *time.Time {
	if !value.Valid {
		return nil
	}
	copied := value.Time
	return &copied
}
