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
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/uuid"
)

// Stable Run failure codes for a human decision that refuses the operation.
const (
	ApprovalRejectedCode = "approval_rejected"
	ApprovalExpiredCode  = "approval_expired"
)

// adoptApprovalDecisionTx copies a decision that was stored before the waiting
// result into the waiting row. The decision must belong to the wait the worker
// named:
//
//   - `capability:<operation_id>` (F06/T050) adopts the decision of that parked
//     operation, otherwise a Run that has already completed an earlier approval
//     could adopt its stale decision and resume without the current operation's
//     human decision;
//   - `approval:<approval_id>` (F04B/T087) adopts the decision of that exact node
//     approval (`operation_id IS NULL`), so a capability approval with the same
//     id namespace can never be adopted by a node wait either.
//
// Only a committed decision state is adopted; an invalidated approval is not a
// decision and must not resume.
func adoptApprovalDecisionTx(
	ctx context.Context, tx *sql.Tx, runID, checkpointID string,
) error {
	if operationID, ok := approvalOperationFromCheckpoint(checkpointID); ok {
		var (
			state      string
			approvalID string
		)
		err := tx.QueryRowContext(ctx, `
			SELECT state, id::text
			  FROM platform.approvals
			 WHERE run_id = $1::uuid
			   AND operation_id = $2::uuid
			   AND state IN ('approved', 'rejected', 'expired')
			 ORDER BY decided_at DESC NULLS LAST, id DESC
			 LIMIT 1`, runID, operationID,
		).Scan(&state, &approvalID)
		if errors.Is(err, sql.ErrNoRows) {
			return nil
		}
		if err != nil {
			return fmt.Errorf("reading the approval decision: %w", err)
		}
		return adoptDecisionTx(ctx, tx, runID, state, approvalID)
	}
	if approvalID, ok := nodeApprovalFromCheckpoint(checkpointID); ok {
		var state string
		err := tx.QueryRowContext(ctx, `
			SELECT state
			  FROM platform.approvals
			 WHERE id = $1::uuid
			   AND run_id = $2::uuid
			   AND operation_id IS NULL
			   AND state IN ('approved', 'rejected', 'expired')`, approvalID, runID,
		).Scan(&state)
		if errors.Is(err, sql.ErrNoRows) {
			return nil
		}
		if err != nil {
			return fmt.Errorf("reading the node approval decision: %w", err)
		}
		return adoptDecisionTx(ctx, tx, runID, state, approvalID)
	}
	return nil
}

// adoptDecisionTx stores one already-committed approval decision as the waiting
// decision. The first stored decision wins; adopting is best effort only for the
// decision-before-waiting-result order.
func adoptDecisionTx(
	ctx context.Context, tx *sql.Tx, runID, outcome, approvalID string,
) error {
	document, err := canonicalWaitingDecision(WaitingResolution{
		Kind: WaitingKindApproval, Outcome: outcome, Reference: approvalID,
	})
	if err != nil {
		return err
	}
	if err := storeWaitingDecisionTx(ctx, tx, runID, WaitingKindApproval, document); err != nil {
		if errors.Is(err, ErrEventConflict) {
			return nil
		}
		return err
	}
	return nil
}

// approvalOperationFromCheckpoint extracts the operation id from the executor's
// stable synthetic checkpoint `capability:<operation_id>`. Any other checkpoint
// belongs to a different producer (T035's synthetic bridge) and is not adopted.
func approvalOperationFromCheckpoint(checkpointID string) (string, bool) {
	const prefix = "capability:"
	value := strings.TrimSpace(checkpointID)
	if !strings.HasPrefix(value, prefix) {
		return "", false
	}
	operationID := strings.ToLower(strings.TrimSpace(strings.TrimPrefix(value, prefix)))
	if !uuid.Valid(operationID) {
		return "", false
	}
	return operationID, true
}

// nodeApprovalFromCheckpoint extracts the approval id from the T087 executor's
// stable checkpoint `approval:<approval_id>`.
func nodeApprovalFromCheckpoint(checkpointID string) (string, bool) {
	const prefix = "approval:"
	value := strings.TrimSpace(checkpointID)
	if !strings.HasPrefix(value, prefix) {
		return "", false
	}
	approvalID := strings.ToLower(strings.TrimSpace(strings.TrimPrefix(value, prefix)))
	if !uuid.Valid(approvalID) {
		return "", false
	}
	return approvalID, true
}

// approvalDecisionRefusesTx reports whether a rejected/expired waiting decision
// names a durable *real* approval row (F06/T050). The T035 synthetic bridge's
// opaque reference is not a UUID in that table, so its documented resume-to-fail
// behaviour is preserved instead of being silently changed by this ticket.
func approvalDecisionRefusesTx(ctx context.Context, tx *sql.Tx, decision []byte) (bool, error) {
	var document struct {
		Reference string `json:"reference"`
	}
	if err := json.Unmarshal(decision, &document); err != nil {
		return false, nil
	}
	reference := strings.ToLower(strings.TrimSpace(document.Reference))
	if !uuid.Valid(reference) {
		return false, nil
	}
	var state string
	err := tx.QueryRowContext(ctx, `
		SELECT state FROM platform.approvals WHERE id = $1::uuid`, reference).Scan(&state)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("reading the approval decision reference: %w", err)
	}
	return state == "rejected" || state == "expired", nil
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
