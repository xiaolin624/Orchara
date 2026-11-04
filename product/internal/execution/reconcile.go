package execution

// F06/T051: the run-side half of unknown-write reconciliation.
//
// T036 records one unresolved external write per (run, operation_id) through the
// non-contract bridge and the single T031 finalizer refuses every terminal state
// while one is unresolved. T051 lets the run's owner (or the exact approver the
// operation was parked with) submit a conclusion backed by a verified evidence
// artifact. This file owns the durable run transition only:
//
//   - it resolves exactly the one unknown ledger row (never invents or deletes
//     one);
//   - it re-invokes the *same* finalizer, so a Run leaves `needs_reconciliation`
//     exactly the way T036 documents (cancelled when a cancel intent exists,
//     failed when the reconciled conclusion is a failure, succeeded/waiting when
//     the stored result proposal is authoritative). A second terminal write path
//     is deliberately not added;
//   - it is idempotent: an already resolved row is a no-op and the finalizer is
//     safe to re-run (T031's CAS on state+revision).
//
// The caller (internal/capabilities) has already verified the evidence artifact
// and updated the operation row inside the same transaction; this method locks the
// Run first (the package's fixed lock order) and then the unknown row.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"

	"orchara/platform/internal/uuid"
)

// Reconciliation conclusion vocabulary (contract `Reconciliation.conclusion`).
const (
	ReconciliationConfirmedSuccess = "confirmed_success"
	ReconciliationConfirmedNotSent = "confirmed_not_sent"
	ReconciliationConfirmedFailure = "confirmed_failure"
)

// Run failure codes a reconciled conclusion commits. They are stable lowercase
// run reasons, never a provider body or URL.
const (
	ReconciliationNotSentCode = "operation_not_sent"
	ReconciliationFailedCode  = "operation_reconciled_failure"
)

var (
	// ErrReconciliationForbidden means the caller is neither the Run owner nor an
	// approver of the operation (403).
	ErrReconciliationForbidden = errors.New(
		"only the run owner or the approving approver may reconcile this operation")
	// ErrReconciliationUnknown means the operation has no unknown ledger row for
	// that Run (or the Run is missing); the API answers 404/409 without leaking
	// another Run's facts.
	ErrReconciliationUnknown = errors.New("that operation has no unknown record to reconcile")
)

// ReconcileResolution is one owner-submitted conclusion for one unknown operation.
type ReconcileResolution struct {
	RunID       string
	OperationID string
	CallerID    string
	Conclusion  string
	// FailureCode is the safe run error_code for a failure conclusion. The
	// service supplies the operation's recorded safe code or a stable default.
	FailureCode string
}

// ReconcileUnknownOperationTx resolves the unknown row and re-drives the one
// finalizer inside the caller's transaction. It returns the Run state after the
// call (the Run may legitimately stay needs_reconciliation when the stored result
// proposal cannot be terminal, when another unknown remains, or when a live child
// still blocks the transition).
func (s *Service) ReconcileUnknownOperationTx(
	ctx context.Context, tx *sql.Tx, input ReconcileResolution,
) (string, error) {
	if !uuid.Valid(input.RunID) || !uuid.Valid(input.OperationID) || !uuid.Valid(input.CallerID) {
		return "", ErrRunNotFound
	}
	switch input.Conclusion {
	case ReconciliationConfirmedSuccess,
		ReconciliationConfirmedNotSent,
		ReconciliationConfirmedFailure:
	default:
		return "", &ValidationError{
			Field:   "conclusion",
			Message: "conclusion must be confirmed_success, confirmed_not_sent or confirmed_failure",
		}
	}
	run, err := lockCancelRunTx(ctx, tx, input.RunID)
	if err != nil {
		return "", err
	}
	if terminalRunState(run.State) {
		// The terminal outcome is the authority and is never revived, but the
		// caller still reports it (the operation/evidence facts are not rewritten).
		return run.State, ErrRunTerminal
	}
	if err := s.requireReconcileAuthorityTx(ctx, tx, run, input); err != nil {
		return "", err
	}
	stored, err := lockUnknownOperationTx(ctx, tx, run.ID, input.OperationID)
	if err != nil {
		if errors.Is(err, ErrUnknownOperationNotFound) {
			return run.State, ErrReconciliationUnknown
		}
		return "", err
	}
	if !stored.ResolvedAt.Valid {
		if err := resolveUnknownOperationTx(ctx, tx, run.ID, input.OperationID); err != nil {
			return "", err
		}
	}

	remaining, err := hasUnknownOperationsTx(ctx, tx, run.ID)
	if err != nil {
		return "", err
	}
	if remaining {
		// Another unknown is still open: the Run stays parked, exactly as the
		// T036 guard requires. Resolving this one is still a durable fact.
		return run.State, nil
	}
	attempt, err := lockLatestCancelAttemptTx(ctx, tx, run.ID)
	if err != nil {
		return "", err
	}
	if attempt == nil || !attempt.ExitedAt.Valid {
		// No exit evidence yet: the exit transaction will run the same finalizer.
		return run.State, nil
	}

	reported, err := s.reconcileProposalTx(ctx, tx, attempt, input)
	if err != nil {
		return "", err
	}
	if _, err := s.finalizeRunTx(ctx, tx, finalizeInput{
		RunID:          run.ID,
		RunOwnerID:     run.OwnerID,
		RunState:       run.State,
		RunRevision:    run.Revision,
		JobID:          attempt.JobID,
		AttemptToken:   attempt.Token,
		ReportedResult: reported,
		Exited:         true,
	}); err != nil {
		return "", err
	}
	return currentRunStateTx(ctx, tx, run.ID)
}

// requireReconcileAuthorityTx accepts the Run owner or a principal recorded as
// the operation's approver. Nothing else can settle an external side effect.
func (s *Service) requireReconcileAuthorityTx(
	ctx context.Context, tx *sql.Tx, run *cancelRunFacts, input ReconcileResolution,
) error {
	if run.OwnerID == input.CallerID {
		return nil
	}
	var approver bool
	if err := tx.QueryRowContext(ctx, `
		SELECT EXISTS (
			SELECT 1 FROM platform.approvals
			 WHERE operation_id = $1::uuid AND approver_id = $2::uuid)`,
		input.OperationID, input.CallerID,
	).Scan(&approver); err != nil {
		return fmt.Errorf("reading the reconcile authority: %w", err)
	}
	if !approver {
		return ErrReconciliationForbidden
	}
	return nil
}

// reconcileProposalTx builds the finalizer input for this conclusion.
//
// confirmed_success reuses the worker's stored result proposal verbatim: if the
// worker had already proposed success before the unknown receipt parked the Run,
// the finalizer can now commit it. When the worker proposed failure (the usual
// case for a lost receipt) the Run fails with the stored code even though the
// write succeeded — the evidence proves the side effect, not the workflow result,
// and T051 never fabricates a success proposal.
//
// confirmed_not_sent/confirmed_failure commit a failed outcome with a stable safe
// code; the finalizer still lets a cancel intent win (T036) and still respects a
// live child (the Run stays parked).
func (s *Service) reconcileProposalTx(
	ctx context.Context, tx *sql.Tx, attempt *cancelAttemptFacts, input ReconcileResolution,
) ([]byte, error) {
	var cancelRequested bool
	if err := tx.QueryRowContext(ctx, `
		SELECT EXISTS (
			SELECT 1 FROM platform.runs
			 WHERE id = $1::uuid AND cancel_requested_at IS NOT NULL)`,
		input.RunID,
	).Scan(&cancelRequested); err != nil {
		return nil, fmt.Errorf("reading the cancel intent: %w", err)
	}
	if cancelRequested {
		// The finalizer's cancel branch is authoritative and needs no proposal.
		return nil, nil
	}
	if input.Conclusion != ReconciliationConfirmedSuccess {
		code := input.FailureCode
		if code == "" {
			code = ReconciliationFailedCode
		}
		proposal, err := json.Marshal(map[string]any{
			"suggested_state": "failed",
			"error_code":      code,
		})
		if err != nil {
			return nil, fmt.Errorf("building the reconciled failure proposal: %w", err)
		}
		return proposal, nil
	}
	var reported []byte
	err := tx.QueryRowContext(ctx, `
		SELECT reported_result FROM platform.run_attempts WHERE token = $1::uuid`,
		attempt.Token,
	).Scan(&reported)
	if errors.Is(err, sql.ErrNoRows) || reported == nil {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the stored result proposal: %w", err)
	}
	return reported, nil
}

// currentRunStateTx reads the committed-in-transaction Run state after the
// finalizer ran (the finalizer's CAS already wrote it).
func currentRunStateTx(ctx context.Context, tx *sql.Tx, runID string) (string, error) {
	var state string
	if err := tx.QueryRowContext(ctx, `
		SELECT state FROM platform.runs WHERE id = $1::uuid`, runID).Scan(&state); err != nil {
		return "", fmt.Errorf("reading the reconciled run state: %w", err)
	}
	return state, nil
}
