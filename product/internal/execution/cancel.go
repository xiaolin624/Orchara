package execution

// F03/T036: user-initiated cancellation and the unknown-operation bridge.
//
// Cancel has exactly two shapes, chosen by whether a controlled execution unit
// can still be live:
//
//   - **No live attempt and no leased job** (a queued Run, a waiting Run whose
//     unit already exited, a recovery/reconciliation park): the platform can prove
//     that nothing is running, so the Run commits `cancelled` (or
//     `needs_reconciliation` when an unknown operation is unresolved) in the
//     cancel transaction itself.
//   - **A live attempt** (running, finalizing, or waiting with a live token): the
//     platform only records the intent, moves the Run to `cancelling` (the phase
//     enum has no cancelling member, so the phase is kept) and appends one
//     `run.status` event. The exit evidence then drives the one T031 finalizer,
//     which lets `cancelled` win over an uncommitted result.
//
// The deadline (T034) keeps its `failed(deadline_exceeded)` outcome: the
// finalizer checks it before the cancel intent, so a Run whose active-time limit
// was already observed fails with the deadline reason even if a cancel request is
// recorded. Unknown operations take priority over both (EXECUTION.md §5/§7): the
// Run parks in `needs_reconciliation` until the last unknown is resolved, and
// only then may the cancel finalizer commit `cancelled`.
//
// F06 owns the real operations/receipts. The two internal endpoints in this file
// are the documented non-contract bridge (ADR 0005): they record a safe reason
// code against a (run, operation_id) pair and resolve it once; they never carry
// provider bodies, credentials or arbitrary text.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"orchara/platform/internal/uuid"
)

// UnknownOperationsErrorCode is the stable Run error_code committed when the
// unresolved-operation guard parks a cancel (or any finalizer) in
// needs_reconciliation. It is a run reason, not an HTTP error code.
const UnknownOperationsErrorCode = "unknown_operations"

// unknownOperationReasons is the closed safe-code allowlist. A caller can never
// store free text (which could contain a provider body, a URL or a credential);
// F06 may extend this map without a schema migration (the migration only bounds
// the text shape).
var unknownOperationReasons = map[string]bool{
	"dispatch_timeout":  true,
	"transport_error":   true,
	"worker_crash":      true,
	"receipt_missing":   true,
	"provider_timeout":  true,
	"cancellation_race": true,
	// F06/review2 P1-3: the connector write was sent but its receipt could not
	// be projected or sealed. These are safe run facts, not free text.
	"connector_receipt_unavailable": true,
	"receipt_seal_failed":           true,
}

// ErrUnknownOperationNotFound is 404 resource_not_found on the resolve bridge: no
// ledger row for that (run, operation) pair. A foreign Run's operation is
// indistinguishable from a missing one.
var ErrUnknownOperationNotFound = errors.New("that unknown operation does not exist")

// CancelOutcome is what the public cancel endpoint needs after the transaction:
// the Run to return (read through the one owner-facing projection) and its owner.
type CancelOutcome struct {
	RunID   string
	OwnerID string
}

// UnknownOperationRequest is the record bridge body, normalised.
type UnknownOperationRequest struct {
	AttemptToken string
	OperationID  string
	Reason       string
}

// UnknownOperation is the bridge's answer: the stored safe fact plus whether the
// operation is resolved and the Run's state after the call.
type UnknownOperation struct {
	RunID       string
	OperationID string
	Reason      string
	Resolved    bool
	ResolvedAt  *time.Time
	RunState    string
}

// Cancel records one user cancel intent for a Run the caller owns.
//
// The caller (the API layer) has already authenticated the principal and checked
// the API-key write scope; this method is the ownership and state decision.
// Missing, deleted and not-owned Runs are the same ErrRunNotFound (404), exactly
// like getRun. A terminal Run is returned unchanged (repeated cancel is
// idempotent). A Run that already carries a cancel intent is returned unchanged
// with no second event (first write wins — that is also how the T034 deadline
// intent and a racing user cancel cannot both rewrite the timestamp).
func (s *Service) Cancel(ctx context.Context, callerID, runID string) (*CancelOutcome, error) {
	runID = strings.TrimSpace(runID)
	if !uuid.Valid(runID) || !uuid.Valid(strings.TrimSpace(callerID)) {
		return nil, ErrRunNotFound
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the cancel transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	// Run first: every Run-scoped transition in this package uses the same lock
	// order, so a cancel serialises with the result/finalizer/resume paths instead
	// of racing them.
	run, err := lockCancelRunTx(ctx, tx, runID)
	if err != nil {
		return nil, err
	}
	if !strings.EqualFold(run.OwnerID, strings.TrimSpace(callerID)) {
		return nil, ErrRunNotFound
	}
	if terminalRunState(run.State) {
		// The terminal state (including cancelled) is authoritative: a repeated
		// cancel changes nothing and emits no event.
		return &CancelOutcome{RunID: run.ID, OwnerID: run.OwnerID}, nil
	}
	if run.CancelRequestedAt.Valid {
		// First write wins; a deadline intent also lands here, so a user cancel
		// never rewrites the recorded control fact or duplicates the event.
		return &CancelOutcome{RunID: run.ID, OwnerID: run.OwnerID}, nil
	}

	// Leaving executing must settle the attempt interval once (T034's helper, never
	// a second settlement path). Cancelling itself accrues no active time.
	if _, err := settleAttemptActiveTx(ctx, tx, run.ID); err != nil {
		return nil, err
	}
	// The waiting evidence is never deleted; this marker is what makes
	// maybeEnqueueResumeTx inert if a decision/exit is still racing in.
	if err := markWaitingCancelRequestedTx(ctx, tx, run.ID); err != nil {
		return nil, err
	}

	attempt, job, err := cancelDispatchFactsTx(ctx, tx, run.ID)
	if err != nil {
		return nil, err
	}
	if attempt == nil && (job == nil || job.State != "leased") {
		// Nothing is running: a queued/held job has no live token and a leased job
		// without a live attempt cannot be proven stopped, so the latter waits for
		// the exit/recovery path instead of claiming it.
		// Priority: a live child Run is a controlled execution unit even when
		// this Run has no live attempt. It blocks the terminal cancel exactly as
		// in the finalizer: record the intent, stay cancelling and hold the job.
		liveChildren, err := hasLiveChildrenTx(ctx, tx, run.ID)
		if err != nil {
			return nil, err
		}
		if liveChildren {
			if err := moveRunToCancellingTx(ctx, tx, run); err != nil {
				return nil, err
			}
			if job != nil {
				if err := holdJobForCancelTx(ctx, tx, job.ID); err != nil {
					return nil, err
				}
				if err := releaseAllocationIfPresentTx(ctx, tx, "job:"+job.ID); err != nil {
					return nil, err
				}
			}
			if _, err := AppendRunStatusTx(ctx, tx, run.ID); err != nil {
				return nil, err
			}
			if err := tx.Commit(); err != nil {
				return nil, fmt.Errorf("committing the blocked cancel transaction: %w", err)
			}
			return &CancelOutcome{RunID: run.ID, OwnerID: run.OwnerID}, nil
		}
		unknown, err := hasUnknownOperationsTx(ctx, tx, run.ID)
		if err != nil {
			return nil, err
		}
		if unknown {
			if err := s.commitCancelReconciliationTx(ctx, tx, run, job); err != nil {
				return nil, err
			}
		} else if err := commitImmediateCancelTx(ctx, tx, run, job); err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the cancel transaction: %w", err)
		}
		return &CancelOutcome{RunID: run.ID, OwnerID: run.OwnerID}, nil
	}

	// A controlled unit may still be live: record the intent and let the exit
	// evidence drive the one finalizer. The phase is deliberately kept (the
	// contract's phase enum has no "cancelling" member).
	if err := moveRunToCancellingTx(ctx, tx, run); err != nil {
		return nil, err
	}
	if _, err := AppendRunStatusTx(ctx, tx, run.ID); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the cancelling transition: %w", err)
	}
	return &CancelOutcome{RunID: run.ID, OwnerID: run.OwnerID}, nil
}

// RecordUnknownOperation stores one unresolved external write for a Run. The
// caller must present the Run's live attempt token (worker or supervisor); the
// Run must not be terminal. Recording is idempotent for the same
// (run, operation_id) and a different reason is a conflict.
func (s *Service) RecordUnknownOperation(
	ctx context.Context, runID string, request UnknownOperationRequest,
) (*UnknownOperation, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the unknown-operation transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	stored, err := s.RecordUnknownOperationTx(
		ctx, tx, runID, request.AttemptToken, request.OperationID, request.Reason,
	)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the unknown-operation transaction: %w", err)
	}
	return stored, nil
}

// RecordUnknownOperationTx is the same record bridge inside a caller-owned
// transaction. F06/T051 needs it because the connector dispatcher must commit the
// operation's `unknown` state and the run's unknown ledger row atomically: an
// operation marked unknown without a ledger row would let the finalizer claim a
// failure for a write that may have happened, and a ledger row without the
// operation state would leave the single CAS sender replaying `dispatching`
// forever.
func (s *Service) RecordUnknownOperationTx(
	ctx context.Context, tx *sql.Tx, runID, attemptToken, operationID, reason string,
) (*UnknownOperation, error) {
	normalized, err := validateUnknownOperationRequest(runID, UnknownOperationRequest{
		AttemptToken: attemptToken, OperationID: operationID, Reason: reason,
	})
	if err != nil {
		return nil, err
	}
	run, err := lockCancelRunTx(ctx, tx, strings.TrimSpace(runID))
	if err != nil {
		return nil, err
	}
	if terminalRunState(run.State) {
		return nil, ErrRunTerminal
	}
	if err := requireLiveAttemptTx(ctx, tx, run.ID, normalized.AttemptToken, false); err != nil {
		return nil, err
	}
	if err := insertUnknownOperationTx(
		ctx, tx, run.ID, normalized.OperationID, normalized.Reason,
	); err != nil {
		return nil, err
	}
	stored, err := readUnknownOperationTx(ctx, tx, run.ID, normalized.OperationID)
	if err != nil {
		return nil, err
	}
	if stored.Reason != normalized.Reason {
		return nil, ErrEventConflict
	}
	stored.RunState = run.State
	return stored, nil
}

// ResolveUnknownOperation marks one unresolved operation resolved once and, when
// it was the last unknown of a cancelled Run whose controlled unit already
// exited, re-invokes the one finalizer so the Run can become `cancelled` exactly
// once. A terminal Run is never revived (409 run_terminal); a worker must present
// a live token, a supervisor may resolve after the exit fact (that is the
// documented "supervisor after exit" branch).
func (s *Service) ResolveUnknownOperation(
	ctx context.Context, runID, operationID, attemptToken, internalRole string,
) (*UnknownOperation, error) {
	runID = strings.TrimSpace(runID)
	operationID = strings.TrimSpace(operationID)
	attemptToken = strings.TrimSpace(attemptToken)
	if !uuid.Valid(runID) {
		return nil, ErrRunNotFound
	}
	if !uuid.Valid(operationID) {
		return nil, &ValidationError{Field: "operation_id", Message: "operation_id must be a UUID"}
	}
	if !uuid.Valid(attemptToken) {
		return nil, &ValidationError{Field: "attempt_token", Message: "attempt_token must be a UUID"}
	}
	supervisor := internalRole == "supervisor"
	if internalRole != "supervisor" && internalRole != "worker" {
		return nil, &ValidationError{Field: "role", Message: "internal role must be worker or supervisor"}
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the unknown-operation resolution: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	run, err := lockCancelRunTx(ctx, tx, runID)
	if err != nil {
		return nil, err
	}
	if terminalRunState(run.State) {
		// A late resolution never revives a terminal Run; the fact is not even
		// rewritten (the terminal outcome remains the authority).
		return nil, ErrRunTerminal
	}
	// A worker must still own the live lease. A supervisor may resolve after the
	// exit; its certificate + the run-bound token are the audit binding.
	if err := requireLiveAttemptTx(ctx, tx, run.ID, attemptToken, supervisor); err != nil {
		return nil, err
	}
	stored, err := lockUnknownOperationTx(ctx, tx, run.ID, operationID)
	if err != nil {
		return nil, err
	}
	result := &UnknownOperation{
		RunID:       run.ID,
		OperationID: operationID,
		Reason:      stored.Reason,
		Resolved:    stored.Resolved,
		ResolvedAt:  timePtrOf(stored.ResolvedAt),
	}
	if !stored.ResolvedAt.Valid {
		if err := resolveUnknownOperationTx(ctx, tx, run.ID, operationID); err != nil {
			return nil, err
		}
		updated, err := readUnknownOperationTx(ctx, tx, run.ID, operationID)
		if err != nil {
			return nil, err
		}
		result.Resolved = updated.Resolved
		result.ResolvedAt = updated.ResolvedAt
	}
	if run.CancelRequestedAt.Valid {
		if err := s.redriveCancelAfterUnknownResolvedTx(ctx, tx, run); err != nil {
			return nil, err
		}
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the unknown-operation resolution: %w", err)
	}
	state, err := cancelRunState(ctx, s.DB, run.ID)
	if err != nil {
		return nil, err
	}
	result.RunState = state
	return result, nil
}

// redriveCancelAfterUnknownResolvedTx is the resolve bridge's finalizer hook: it
// only acts when every unknown is resolved, a cancel intent exists, the latest
// attempt already exited and no controlled unit is live. The one finalizer then
// commits `cancelled` (or stays blocked by a live child) exactly once.
func (s *Service) redriveCancelAfterUnknownResolvedTx(
	ctx context.Context, tx *sql.Tx, run *cancelRunFacts,
) error {
	if run.State != "cancelling" && run.State != "needs_reconciliation" &&
		run.State != "running" && run.State != "recovery_pending" {
		return nil
	}
	unresolved, err := hasUnknownOperationsTx(ctx, tx, run.ID)
	if err != nil {
		return err
	}
	if unresolved {
		return nil
	}
	attempt, err := lockLatestCancelAttemptTx(ctx, tx, run.ID)
	if err != nil {
		return err
	}
	if attempt == nil || !attempt.ExitedAt.Valid {
		// No exit fact yet: the exit transaction will run the same finalizer.
		return nil
	}
	_, err = s.finalizeRunTx(ctx, tx, finalizeInput{
		RunID:        run.ID,
		RunOwnerID:   run.OwnerID,
		RunState:     run.State,
		RunRevision:  run.Revision,
		JobID:        attempt.JobID,
		AttemptToken: attempt.Token,
		Exited:       true,
	})
	return err
}

// validateUnknownOperationRequest checks the closed bridge body before any row is
// touched.
func validateUnknownOperationRequest(
	runID string, request UnknownOperationRequest,
) (UnknownOperationRequest, error) {
	normalized := UnknownOperationRequest{
		AttemptToken: strings.TrimSpace(request.AttemptToken),
		OperationID:  strings.ToLower(strings.TrimSpace(request.OperationID)),
		Reason:       strings.TrimSpace(request.Reason),
	}
	if !uuid.Valid(strings.TrimSpace(runID)) {
		return UnknownOperationRequest{}, ErrRunNotFound
	}
	if !uuid.Valid(normalized.AttemptToken) {
		return UnknownOperationRequest{}, &ValidationError{
			Field: "attempt_token", Message: "attempt_token must be a UUID",
		}
	}
	if !uuid.Valid(normalized.OperationID) {
		return UnknownOperationRequest{}, &ValidationError{
			Field: "operation_id", Message: "operation_id must be a UUID",
		}
	}
	if !unknownOperationReasons[normalized.Reason] {
		return UnknownOperationRequest{}, &ValidationError{
			Field:   "reason",
			Message: "reason must be one of the safe unknown-operation codes",
		}
	}
	return normalized, nil
}
