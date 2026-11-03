package execution

// F06/T050 integration: the approval aggregate's two hooks into the F03/T035
// waiting machinery.
//
//   - A decision may be durable before the worker's waiting result lands (the
//     human approves while the attempt is still exiting). SubmitResult calls
//     adoptApprovalDecisionTx so the already-stored decision is copied into the
//     fresh run_waitings row; the ordinary maybeEnqueueResumeTx then remains the
//     only resume creator.
//   - A rejected or expired decision must never resume. maybeEnqueueResumeTx
//     fails the waiting Run with the stable approval_rejected / approval_expired
//     code once the supervisor exit fact exists, so both arrival orders converge
//     on one decision and zero dispatches.
//   - A cancelled or terminal Run invalidates its pending approvals rather than
//     leaving a decision that could authorise an operation after the run ended.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"orchara/platform/internal/uuid"
)

// Stable Run failure codes for a human decision that refuses the operation.
const (
	ApprovalRejectedCode = "approval_rejected"
	ApprovalExpiredCode  = "approval_expired"
)

// adoptApprovalDecisionTx copies a decision that was stored before the waiting
// result into the waiting row. Only a committed decision state is adopted; an
// invalidated approval is not a decision and must not resume the Run.
func adoptApprovalDecisionTx(ctx context.Context, tx *sql.Tx, runID string) error {
	var (
		state      string
		approvalID string
	)
	err := tx.QueryRowContext(ctx, `
		SELECT state, id::text
		  FROM platform.approvals
		 WHERE run_id = $1::uuid AND state IN ('approved', 'rejected', 'expired')
		 ORDER BY decided_at DESC NULLS LAST, id DESC
		 LIMIT 1`, runID,
	).Scan(&state, &approvalID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("reading the approval decision: %w", err)
	}
	resolution := WaitingResolution{
		Kind: WaitingKindApproval, Outcome: state, Reference: approvalID,
	}
	document, err := canonicalWaitingDecision(resolution)
	if err != nil {
		return err
	}
	if err := storeWaitingDecisionTx(ctx, tx, runID, WaitingKindApproval, document); err != nil {
		// A decision already in the waiting row wins; adopting is best effort
		// only for the decision-before-waiting-result order.
		if errors.Is(err, ErrEventConflict) {
			return nil
		}
		return err
	}
	return nil
}

// invalidateRunApprovalsTx is the terminal/cancel hook: no pending approval may
// survive the Run it belongs to. The row keeps its proposal artifact for audit;
// its state says a decision is no longer possible.
func invalidateRunApprovalsTx(ctx context.Context, tx *sql.Tx, runID, reason string) error {
	if !uuid.Valid(runID) {
		return nil
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.approvals
		   SET state = 'invalidated', decided_at = now(), decision_key = $2
		 WHERE run_id = $1::uuid AND state = 'pending'`, runID, reason); err != nil {
		return fmt.Errorf("invalidating the run approvals: %w", err)
	}
	return nil
}
