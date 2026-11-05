package execution

// F07/T081: the transaction-scoped seams the child lifecycle hook calls.
//
// The hook itself lives in internal/children (it owns platform.run_children), but
// every transition it drives belongs to this package's one state machine:
//
//	ResolveChildrenWaitingTx       store the children decision once and try the
//	                               single resume (the same maybeEnqueueResumeTx the
//	                               T035 bridge uses);
//	RedriveParentAfterChildrenStoppedTx
//	                               re-invoke the one finalizer for a parent whose
//	                               terminal transition was blocked by a live child.
//
// Both are idempotent and are called inside the hook's own transaction, which has
// already locked the parent Run. They never lock a child Run, so the
// child-finalizer -> parent lock inversion that would deadlock against the
// parent -> child spawn order cannot happen.
//
// Boundary: the hook never invents a terminal state. It resolves a *wait* only
// when the wait is still `waiting_children` with no decision and no cancel
// intent; a cancel always wins and is committed by finalizeRunTx. An optional
// branch is not produced by this build (spawnChild always records required=true,
// the contract has no `required` field); the decision rule in the children
// package documents the seam.

import (
	"context"
	"database/sql"

	"orchara/platform/internal/uuid"
)

// ResolveChildrenWaitingTx stores one children resolution for a Run that is
// waiting_children and tries the one resume. It is a no-op (false, nil) unless
// the Run is waiting with no stored decision, no cancel marker and a genuine
// terminal child of this parent. The first stored decision is authoritative: a
// replay with the same document is a no-op too.
func (s *Service) ResolveChildrenWaitingTx(
	ctx context.Context, tx *sql.Tx, parentRunID, outcome, childRunID string,
) (bool, error) {
	if !uuid.Valid(parentRunID) || !uuid.Valid(childRunID) {
		return false, nil
	}
	resolution, err := validateWaitingResolution(WaitingResolution{
		Kind:       WaitingKindChildren,
		Outcome:    outcome,
		ChildRunID: childRunID,
	})
	if err != nil {
		return false, err
	}
	decision, err := canonicalWaitingDecision(resolution)
	if err != nil {
		return false, err
	}

	run, err := lockWaitingRunTx(ctx, tx, parentRunID)
	if err != nil {
		return false, err
	}
	if _, waiting := waitingKindForState(run.State); !waiting {
		// Already resumed or terminal: the exit/resume transaction owns it.
		return false, nil
	}
	if run.State == "cancelling" {
		return false, nil
	}
	waiting, err := lockWaitingTx(ctx, tx, parentRunID)
	if err != nil {
		return false, err
	}
	if waiting == nil || waiting.Kind != WaitingKindChildren {
		return false, nil
	}
	if waiting.CancelRequestedAt.Valid || run.CancelRequestedAt.Valid {
		// The wait can never resume; the cancel finalizer owns the convergence.
		return false, nil
	}
	if len(waiting.Decision) > 0 {
		return false, nil
	}
	// Under the parent lock every controlled unit must already be stopped; the
	// caller checks this too, this is the authoritative re-check.
	live, err := hasLiveChildrenTx(ctx, tx, parentRunID)
	if err != nil {
		return false, err
	}
	if live {
		return false, nil
	}
	// A decision may only name a real terminal child of this Run; the same check
	// the bridge and maybeEnqueueResumeTx run. A crash during the hook leaves the
	// wait unresolved and the reconcile sweep drives it again.
	if err := checkWaitingChildTx(ctx, tx, parentRunID, decision); err != nil {
		return false, err
	}
	if err := storeWaitingDecisionTx(ctx, tx, parentRunID, waiting.Kind, decision); err != nil {
		return false, err
	}
	if _, err := s.maybeEnqueueResumeTx(ctx, tx, parentRunID); err != nil {
		return false, err
	}
	return true, nil
}

// MaybeEnqueueResumeTx is the idempotent "the evidence is complete now" seam the
// child lifecycle hook uses after the decision and the exit fact both exist. It
// is exactly the T035 one-resume transaction; a waiting Run with missing evidence,
// a recorded cancel intent or an existing resume is a no-op.
func (s *Service) MaybeEnqueueResumeTx(
	ctx context.Context, tx *sql.Tx, runID string,
) (bool, error) {
	outcome, err := s.maybeEnqueueResumeTx(ctx, tx, runID)
	if err != nil {
		return false, err
	}
	return outcome != nil && outcome.Queued, nil
}

// RedriveParentAfterChildrenStoppedTx is the re-drive for a parent whose terminal
// transition was blocked by a live child: the last child is now terminal, so the
// one finalizer is re-invoked and commits (or refuses again) exactly once. It
// only acts when no child, unknown operation or live attempt can still run, and
// it needs a real reason to terminalise (a cancel intent or a stored proposal).
// A `waiting_children` parent is left to ResolveChildrenWaitingTx.
func (s *Service) RedriveParentAfterChildrenStoppedTx(
	ctx context.Context, tx *sql.Tx, parentRunID string,
) (bool, error) {
	if !uuid.Valid(parentRunID) {
		return false, nil
	}
	run, err := lockCancelRunTx(ctx, tx, parentRunID)
	if err != nil {
		return false, err
	}
	if terminalRunState(run.State) {
		return false, nil
	}
	switch run.State {
	case "running", "recovery_pending", "cancelling", "needs_reconciliation":
	default:
		// waiting_*: the wait resolution owns the transition; queued: nothing ran.
		return false, nil
	}
	live, err := hasLiveChildrenTx(ctx, tx, run.ID)
	if err != nil {
		return false, err
	}
	if live {
		return false, nil
	}
	unresolved, err := hasUnknownOperationsTx(ctx, tx, run.ID)
	if err != nil {
		return false, err
	}
	if unresolved {
		// Unknown external writes always win; the resolve/reconcile path owns it.
		return false, nil
	}
	attempt, err := lockLatestCancelAttemptTx(ctx, tx, run.ID)
	if err != nil {
		return false, err
	}
	if attempt == nil || !attempt.ExitedAt.Valid {
		// The exit transaction will re-drive the same finalizer.
		return false, nil
	}
	cancelIntent := run.CancelRequestedAt.Valid || run.State == "cancelling"
	if !cancelIntent && len(attempt.ReportedResult) == 0 {
		// A running parent with no committed result has not decided anything; its
		// own executor (not the hook) owns the next proposal.
		return false, nil
	}
	finalized, err := s.finalizeRunTx(ctx, tx, finalizeInput{
		RunID:          run.ID,
		RunOwnerID:     run.OwnerID,
		RunState:       run.State,
		RunRevision:    run.Revision,
		JobID:          attempt.JobID,
		AttemptToken:   attempt.Token,
		ReportedResult: attempt.ReportedResult,
		Exited:         true,
	})
	if err != nil {
		return false, err
	}
	return finalized, nil
}
