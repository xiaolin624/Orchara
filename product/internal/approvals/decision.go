package approvals

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/execution"
	"orchara/platform/internal/uuid"
)

// DecisionRequest is the public decision call. The caller identity comes from
// the authenticated session; `CallerIsAdmin` only distinguishes the 403 an
// authenticated administrator gets from the 404 a stranger gets.
type DecisionRequest struct {
	ApprovalID     string
	CallerID       string
	CallerIsAdmin  bool
	Decision       string
	ParamsSHA256   string
	IdempotencyKey string
}

// approvalRunFacts is the locked Run prefix a decision re-checks.
type approvalRunFacts struct {
	ID         string
	OwnerID    string
	State      string
	Cancel     sql.NullTime
	BudgetLeft bool
}

// Decide stores one approval decision and hands it to the single F03/T035 resume
// transaction.
//
// Locking order is Run -> approval, the same order the cancel path uses, so a
// cancel and a decision can never deadlock. Two opposite concurrent decisions
// therefore serialize: the second one observes the first state and answers 409;
// a replay of the same decision is the idempotent 200.
func (s *Service) Decide(ctx context.Context, request DecisionRequest) (*Approval, error) {
	decision, err := validateDecision(request.Decision)
	if err != nil {
		return nil, err
	}
	request.ApprovalID = strings.TrimSpace(request.ApprovalID)
	if !uuid.Valid(request.ApprovalID) {
		return nil, failure(404, CodeNotFound, "that approval does not exist")
	}
	request.CallerID = strings.TrimSpace(request.CallerID)
	if !uuid.Valid(request.CallerID) {
		return nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	request.ParamsSHA256 = strings.TrimSpace(request.ParamsSHA256)
	request.IdempotencyKey = strings.TrimSpace(request.IdempotencyKey)
	if request.ParamsSHA256 == "" {
		return nil, &Error{Status: 422, Code: "validation_failed",
			Message: "params_sha256 is required", Field: "params_sha256"}
	}
	if request.IdempotencyKey == "" || len(request.IdempotencyKey) > 200 {
		return nil, &Error{Status: 422, Code: "validation_failed",
			Message: "Idempotency-Key is required", Field: "Idempotency-Key"}
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the decision transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	peek, err := loadApprovalTx(ctx, tx, request.ApprovalID, false)
	if err != nil {
		return nil, fmt.Errorf("reading the approval: %w", err)
	}
	if peek == nil {
		return nil, failure(404, CodeNotFound, "that approval does not exist")
	}
	// Lock the Run first; the approval's decidability depends on the Run state,
	// and the cancel path takes the same order.
	run, err := lockApprovalRunTx(ctx, tx, peek.RunID)
	if err != nil {
		return nil, err
	}
	if run == nil {
		return nil, failure(404, CodeNotFound, "that run does not exist")
	}
	approval, err := loadApprovalTx(ctx, tx, request.ApprovalID, true)
	if err != nil {
		return nil, fmt.Errorf("locking the approval: %w", err)
	}
	if approval == nil {
		return nil, failure(404, CodeNotFound, "that approval does not exist")
	}
	if request.CallerID != approval.ApproverID {
		if request.CallerIsAdmin {
			return nil, failure(403, CodeNotApprover,
				"only the designated human approver may decide this run approval")
		}
		// A stranger learns nothing: the row does not exist for them.
		return nil, failure(404, CodeNotFound, "that approval does not exist")
	}

	if approval.State != "pending" {
		return s.replayDecisionTx(ctx, tx, approval, decision, request)
	}

	// Expiry is decided by the database clock, never the client's.
	var due bool
	if err := tx.QueryRowContext(ctx,
		`SELECT now() >= $1::timestamptz`, approval.ExpiresAt).Scan(&due); err != nil {
		return nil, fmt.Errorf("checking the approval deadline: %w", err)
	}
	if due {
		if err := expireApprovalTx(ctx, tx, approval, request); err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the expired approval: %w", err)
		}
		_ = s.resolveWaiting(ctx, approval, "expired")
		return nil, failure(409, CodeExpired, "that approval has expired; it cannot authorise the operation")
	}

	if approval.ParamsSHA256 != request.ParamsSHA256 {
		return nil, failure(409, CodeParamsMismatch,
			"the decision must carry the approval's exact params_sha256")
	}

	// Current permission: a disabled owner revokes the human's standing, and the
	// approval is invalidated without deciding the parked operation.
	disabled, err := principalDisabledTx(ctx, tx, run.OwnerID)
	if err != nil {
		return nil, err
	}
	if disabled {
		if err := invalidateApprovalTx(ctx, tx, approval, "permission_revoked"); err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the invalidated approval: %w", err)
		}
		// No waiting decision is stored: the existing F03/T035 owner-disabled
		// check fails the Run with permission_revoked once the exit is known,
		// which is the honest reason (not a human rejection).
		return nil, failure(403, CodePermissionRevoked,
			"the run owner's permission was revoked; the approval is invalid")
	}
	if run.Cancel.Valid || run.State == "cancelled" || run.State == "cancelling" {
		if err := invalidateApprovalTx(ctx, tx, approval, "run_cancelled"); err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the invalidated approval: %w", err)
		}
		return nil, failure(409, "run_state_conflict", "the run was cancelled; the approval is invalid")
	}
	switch run.State {
	case "running", "waiting_approval":
	default:
		if err := invalidateApprovalTx(ctx, tx, approval, "run_terminal"); err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the invalidated approval: %w", err)
		}
		return nil, failure(409, "run_state_conflict", "the run is not waiting for this approval")
	}
	// The frozen policy must still be the one the approval was prepared against,
	// and it must not have been revoked since.
	operationState, revoked, err := lockApprovalOperationTx(ctx, tx, approval.OperationID)
	if err != nil {
		return nil, err
	}
	if revoked {
		if err := invalidateApprovalTx(ctx, tx, approval, "policy_revoked"); err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the invalidated approval: %w", err)
		}
		return nil, failure(409, CodePolicyRevoked,
			"the connector policy was revoked; the approval is invalid")
	}
	if operationState != "awaiting_approval" {
		return nil, failure(409, CodeUsageConflict,
			"the parked operation is no longer awaiting approval")
	}

	newState := "approved"
	if decision == "reject" {
		newState = "rejected"
	}
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.approvals
		   SET state = $2, decided_at = now(), decided_by = $3::uuid, decision_key = $4
		 WHERE id = $1::uuid AND state = 'pending'`,
		approval.ID, newState, request.CallerID, request.IdempotencyKey,
	)
	if err != nil {
		return nil, fmt.Errorf("storing the decision: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("storing the decision: %w", err)
	}
	if affected != 1 {
		return nil, failure(409, CodeDecisionConflict,
			"another decision won the race for this approval")
	}
	if decision == "approve" {
		// Approval only *allows* the operation to proceed. The resumed
		// capability call still performs the authorized -> dispatching CAS and
		// re-checks the attempt, manifest, policy and budget.
		result, err = tx.ExecContext(ctx, `
			UPDATE platform.operations
			   SET state = 'authorized', updated_at = now()
			 WHERE id = $1::uuid AND state = 'awaiting_approval'`, approval.OperationID)
		if err != nil {
			return nil, fmt.Errorf("authorizing the parked operation: %w", err)
		}
		affected, err = result.RowsAffected()
		if err != nil {
			return nil, fmt.Errorf("authorizing the parked operation: %w", err)
		}
		if affected != 1 {
			return nil, failure(409, CodeUsageConflict,
				"the parked operation is no longer awaiting approval")
		}
	} else {
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.operations
			   SET state = 'failed', last_error_code = 'approval_rejected', updated_at = now()
			 WHERE id = $1::uuid AND state = 'awaiting_approval'`, approval.OperationID); err != nil {
			return nil, fmt.Errorf("failing the rejected operation: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the decision: %w", err)
	}

	// The decision is durable; the F03/T035 transaction is the only resume
	// creator. Reject/expire store the decision and the resume transaction fails
	// the waiting Run with approval_rejected/approval_expired once exit is known.
	approval.State = newState
	decidedAt := time.Now().UTC()
	approval.DecidedAt = &decidedAt
	if decision == "reject" {
		_ = s.resolveWaiting(ctx, approval, "rejected")
	} else {
		_ = s.resolveWaiting(ctx, approval, "approved")
	}
	return approval, nil
}

// replayDecisionTx answers a repeat decision. The first decision is
// authoritative: the same decision is idempotent, the opposite is 409.
func (s *Service) replayDecisionTx(
	ctx context.Context, tx *sql.Tx, approval *Approval, decision string, request DecisionRequest,
) (*Approval, error) {
	same := (decision == "approve" && approval.State == "approved") ||
		(decision == "reject" && approval.State == "rejected")
	if !same {
		switch approval.State {
		case "expired":
			return nil, failure(409, CodeExpired, "that approval has expired")
		case "invalidated":
			return nil, failure(409, CodeNotPending, "that approval was invalidated")
		default:
			return nil, failure(409, CodeDecisionConflict,
				"the opposite decision is already stored for this approval")
		}
	}
	if approval.ParamsSHA256 != request.ParamsSHA256 {
		return nil, failure(409, CodeParamsMismatch,
			"the decision must carry the approval's exact params_sha256")
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the decision replay: %w", err)
	}
	outcome := "approved"
	if decision == "reject" {
		outcome = "rejected"
	}
	_ = s.resolveWaiting(ctx, approval, outcome)
	return approval, nil
}

// resolveWaiting hands the decision to the single resume transaction. A Run that
// has not yet stored its waiting result (the worker is still exiting) is not an
// error: SubmitResult adopts the durable approval decision when the result lands,
// so either order converges to one decision and at most one resume.
func (s *Service) resolveWaiting(ctx context.Context, approval *Approval, outcome string) error {
	if s.Execution == nil {
		return failure(503, "execution_unavailable", "no resume authority is configured")
	}
	_, err := s.Execution.ResolveWaiting(ctx, approval.RunID, execution.WaitingResolution{
		Kind: execution.WaitingKindApproval, Outcome: outcome, Reference: approval.ID,
	})
	if err == nil {
		return nil
	}
	if errors.Is(err, execution.ErrRunStateConflict) || errors.Is(err, execution.ErrDecisionConflict) {
		// The waiting result has not landed yet, the Run already resumed, or the
		// cancel path owns the terminal transition. The decision row is durable.
		return nil
	}
	return err
}

func lockApprovalRunTx(ctx context.Context, tx *sql.Tx, runID string) (*approvalRunFacts, error) {
	run := &approvalRunFacts{}
	err := tx.QueryRowContext(ctx, `
		SELECT id::text, owner_id::text, state, cancel_requested_at, budget_limit > 0
		  FROM platform.runs WHERE id = $1::uuid FOR UPDATE`, runID,
	).Scan(&run.ID, &run.OwnerID, &run.State, &run.Cancel, &run.BudgetLeft)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("locking the run: %w", err)
	}
	return run, nil
}

func principalDisabledTx(ctx context.Context, tx *sql.Tx, principalID string) (bool, error) {
	var disabled sql.NullTime
	err := tx.QueryRowContext(ctx, `
		SELECT disabled_at FROM platform.principals WHERE id = $1::uuid`, principalID).Scan(&disabled)
	if errors.Is(err, sql.ErrNoRows) {
		return true, nil
	}
	if err != nil {
		return false, fmt.Errorf("reading the principal: %w", err)
	}
	return disabled.Valid, nil
}

// lockApprovalOperationTx returns the operation state and whether its connector
// policy version is revoked. The operation row is locked so the decision cannot
// race a concurrent dispatcher.
func lockApprovalOperationTx(
	ctx context.Context, tx *sql.Tx, operationID string,
) (string, bool, error) {
	var state, kind, version string
	err := tx.QueryRowContext(ctx, `
		SELECT state, capability_kind, capability_version
		  FROM platform.operations WHERE id = $1::uuid FOR UPDATE`, operationID,
	).Scan(&state, &kind, &version)
	if errors.Is(err, sql.ErrNoRows) {
		return "", false, failure(404, CodeNotFound, "the parked operation does not exist")
	}
	if err != nil {
		return "", false, fmt.Errorf("locking the operation: %w", err)
	}
	if kind != "connector" {
		return state, false, nil
	}
	var revoked sql.NullTime
	err = tx.QueryRowContext(ctx, `
		SELECT revoked_at FROM platform.connector_policies WHERE id = $1::uuid`, version,
	).Scan(&revoked)
	if errors.Is(err, sql.ErrNoRows) {
		return state, true, nil
	}
	if err != nil {
		return "", false, fmt.Errorf("reading the connector policy: %w", err)
	}
	return state, revoked.Valid, nil
}

func expireApprovalTx(ctx context.Context, tx *sql.Tx, approval *Approval, request DecisionRequest) error {
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.approvals
		   SET state = 'expired', decided_at = now(), decided_by = $2::uuid, decision_key = $3
		 WHERE id = $1::uuid AND state = 'pending'`,
		approval.ID, request.CallerID, request.IdempotencyKey,
	); err != nil {
		return fmt.Errorf("expiring the approval: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = 'failed', last_error_code = 'approval_expired', updated_at = now()
		 WHERE id = $1::uuid AND state = 'awaiting_approval'`, approval.OperationID); err != nil {
		return fmt.Errorf("failing the expired operation: %w", err)
	}
	return nil
}

func invalidateApprovalTx(ctx context.Context, tx *sql.Tx, approval *Approval, reason string) error {
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.approvals
		   SET state = 'invalidated', decided_at = now(), decision_key = $2
		 WHERE id = $1::uuid AND state = 'pending'`, approval.ID, reason); err != nil {
		return fmt.Errorf("invalidating the approval: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.operations
		   SET state = 'failed', last_error_code = $2, updated_at = now()
		 WHERE id = $1::uuid AND state = 'awaiting_approval'`, approval.OperationID, reason); err != nil {
		return fmt.Errorf("failing the invalidated operation: %w", err)
	}
	return nil
}
