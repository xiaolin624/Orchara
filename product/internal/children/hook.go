package children

// F07/T081: the child lifecycle hook - waiting capacity convergence.
//
// A delegated child Run is an ordinary platform Run, so its terminal transition is
// committed by the F03/T031 finalizer (execution.ConfirmExit / SubmitResult /
// Cancel), never here. What is missing without this file is the *parent* half:
//
//   - a required child that failed/cancelled must fail the parent branch and ask
//     its live siblings to stop (EXECUTION section 7 fail-fast);
//   - once no controlled unit is live, a parent waiting_children gets exactly one
//     children decision and the one resume job (the same maybeEnqueueResumeTx the
//     T035 bridge used to call manually);
//   - a parent that was already cancelling is re-driven through the one finalizer,
//     so `cancelled` is committed only after the last child stopped.
//
// Design constraints (recorded in ADR 0032 / the T081 report):
//
//   - The hook runs in **its own idempotent transaction after the child's terminal
//     commit**. A hook inside finalizeRunTx would lock the parent while the child
//     row is locked, inverting spawn's parent -> child order.
//   - Nothing here duplicates a state machine: decisions/resumes go through
//     execution.ResolveChildrenWaitingTx, terminal transitions through
//     execution.RedriveParentAfterChildrenStoppedTx, cancellation through the
//     injected execution.Cancel seam.
//   - Every decision is deterministic from durable rows (the earliest failing
//     required child, else the earliest terminal child), so two racing hooks that
//     both observe "all children stopped" compute the same decision and the
//     database's first-decision-wins rule turns the loser into a no-op.
//   - The hook is replayable: OnChildTerminal on a non-terminal/unknown Run is a
//     no-op, on an already-resolved wait it is a no-op, and on a terminal parent it
//     is a no-op. Reconcile() re-drives crash cases on the claim path.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/uuid"
)

// OnChildTerminal reconciles the parent of one child Run. It is safe to call for
// any Run id: a top-level Run, a non-terminal child, an already-resolved parent or
// a terminal parent are all no-ops.
func (s *Service) OnChildTerminal(ctx context.Context, childRunID string) error {
	child, err := s.terminalChildFacts(ctx, childRunID)
	if err != nil || child == nil {
		return err
	}
	// Fail-fast: a required branch that failed/cancelled asks every live sibling
	// to stop. This runs before the parent transaction and is idempotent: a repeat
	// call sees the sibling's recorded cancel intent and Cancel is a no-op.
	if child.required && (child.state == "failed" || child.state == "cancelled") {
		if err := s.cancelLiveSiblings(ctx, child.parentRunID, child.ownerID); err != nil {
			return err
		}
	}
	return s.reconcileParent(ctx, child.parentRunID)
}

// OnParentCancelRequested propagates a parent's cancel intent to its live
// **delegated** children (those with a run_children receipt). The child's own
// cancel path is the existing T036 one: a queued child commits cancelled
// immediately, a running child moves to cancelling and the parent waits for its
// exit evidence before converging. A synthetic/legacy child Run without a
// delegation receipt is deliberately untouched: T036's documented semantics for
// those is that the parent records the intent and waits, and the child's own
// controller decides.
func (s *Service) OnParentCancelRequested(ctx context.Context, parentRunID string) error {
	if s.CancelRun == nil || !uuid.Valid(strings.TrimSpace(parentRunID)) {
		return nil
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT child.id::text, parent.owner_id::text
		  FROM platform.runs AS child
		  JOIN platform.runs AS parent ON parent.id = child.parent_run_id
		  JOIN platform.run_children AS receipt ON receipt.child_run_id = child.id
		 WHERE child.parent_run_id = $1::uuid
		   AND child.deleted_at IS NULL
		   AND child.state NOT IN ('succeeded', 'failed', 'cancelled')
		 ORDER BY child.created_at, child.id`,
		parentRunID,
	)
	if err != nil {
		return fmt.Errorf("reading the parent's live children: %w", err)
	}
	var (
		childIDs []string
		ownerID  string
	)
	for rows.Next() {
		var childID string
		if err := rows.Scan(&childID, &ownerID); err != nil {
			rows.Close()
			return fmt.Errorf("reading the parent's live children: %w", err)
		}
		childIDs = append(childIDs, childID)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return fmt.Errorf("reading the parent's live children: %w", err)
	}
	if err := rows.Close(); err != nil {
		return fmt.Errorf("reading the parent's live children: %w", err)
	}
	var firstErr error
	for _, childID := range childIDs {
		if err := s.CancelRun(ctx, ownerID, childID); err != nil && firstErr == nil {
			// The next reconcile re-checks the durable state: a child that
			// terminalised between the read and the cancel is not an error.
			firstErr = err
		}
	}
	return firstErr
}

// Reconcile re-drives the crash window between a child's terminal commit and its
// post-commit hook. It is bounded (a handful of rows), idempotent and called by
// execution.Service.Claim before any claim lock is held. Two classes of work:
//
//  1. a required child failed while a live sibling has no cancel intent: ask the
//     siblings to stop (OnChildTerminal owns the propagation);
//  2. a parent with no live child still needs convergence: resolve its wait or
//     re-drive its finalizer (reconcileParent).
func (s *Service) Reconcile(ctx context.Context) error {
	if s.Execution == nil {
		return nil
	}
	if err := s.reconcileCancellations(ctx); err != nil {
		return err
	}
	parents, err := s.parentsNeedingReconcile(ctx)
	if err != nil {
		return err
	}
	var firstErr error
	for _, parentID := range parents {
		if err := s.reconcileParent(ctx, parentID); err != nil && firstErr == nil {
			firstErr = err
		}
	}
	return firstErr
}

// reconcileCancellations finds a required child that already failed while a live
// sibling has no recorded cancel intent, and re-runs the propagation.
func (s *Service) reconcileCancellations(ctx context.Context) error {
	rows, err := s.DB.QueryContext(ctx, `
		SELECT failed.child_run_id::text
		  FROM platform.run_children AS failed
		  JOIN platform.runs AS child ON child.id = failed.child_run_id
		  JOIN platform.runs AS parent ON parent.id = failed.parent_run_id
		 WHERE failed.required
		   AND child.deleted_at IS NULL
		   AND child.state IN ('failed', 'cancelled')
		   AND parent.deleted_at IS NULL
		   AND parent.state NOT IN ('succeeded', 'failed', 'cancelled')
		   AND EXISTS (
		       SELECT 1 FROM platform.runs AS sibling
		        WHERE sibling.parent_run_id = parent.id
		          AND sibling.deleted_at IS NULL
		          AND sibling.state NOT IN ('succeeded', 'failed', 'cancelled')
		          AND sibling.cancel_requested_at IS NULL
		          AND EXISTS (SELECT 1 FROM platform.run_children AS receipt
		                       WHERE receipt.child_run_id = sibling.id))
		 ORDER BY child.created_at, failed.child_run_id
		 LIMIT 5`)
	if err != nil {
		return fmt.Errorf("reading the failed required children: %w", err)
	}
	var childIDs []string
	for rows.Next() {
		var childID string
		if err := rows.Scan(&childID); err != nil {
			rows.Close()
			return fmt.Errorf("reading the failed required children: %w", err)
		}
		childIDs = append(childIDs, childID)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return fmt.Errorf("reading the failed required children: %w", err)
	}
	if err := rows.Close(); err != nil {
		return fmt.Errorf("reading the failed required children: %w", err)
	}
	var firstErr error
	for _, childID := range childIDs {
		if err := s.OnChildTerminal(ctx, childID); err != nil && firstErr == nil {
			firstErr = err
		}
	}
	return firstErr
}

// parentsNeedingReconcile lists non-terminal parents whose last child is stopped
// but whose transition is still pending. The predicates mirror the states the two
// execution seams act on; they never fabricate a reason to terminalise.
func (s *Service) parentsNeedingReconcile(ctx context.Context) ([]string, error) {
	rows, err := s.DB.QueryContext(ctx, `
		SELECT parent.id::text
		  FROM platform.runs AS parent
		 WHERE parent.deleted_at IS NULL
		   AND parent.state IN ('waiting_children', 'cancelling', 'running',
		                        'recovery_pending', 'needs_reconciliation')
		   AND EXISTS (SELECT 1 FROM platform.runs AS child
		                WHERE child.parent_run_id = parent.id)
		   AND NOT EXISTS (SELECT 1 FROM platform.runs AS live
		                    WHERE live.parent_run_id = parent.id
		                      AND live.deleted_at IS NULL
		                      AND live.state NOT IN ('succeeded', 'failed', 'cancelled'))
		   AND (
		       (parent.state = 'waiting_children' AND EXISTS (
		            SELECT 1 FROM platform.run_waitings AS w
		             WHERE w.run_id = parent.id AND w.kind = 'children'
		               AND w.cancel_requested_at IS NULL
		               AND (w.decision IS NULL
		                    OR (w.resume_job_id IS NULL AND w.exit_confirmed_at IS NOT NULL))))
		    OR (EXISTS (SELECT 1 FROM platform.run_attempts AS a
		                 WHERE a.run_id = parent.id AND a.exited_at IS NOT NULL
		                   AND (a.reported_result IS NOT NULL
		                        OR parent.cancel_requested_at IS NOT NULL)))
		   )
		 ORDER BY parent.created_at, parent.id
		 LIMIT 10`)
	if err != nil {
		return nil, fmt.Errorf("listing the parents needing reconcile: %w", err)
	}
	defer rows.Close()
	parents := make([]string, 0, 4)
	for rows.Next() {
		var parentID string
		if err := rows.Scan(&parentID); err != nil {
			return nil, fmt.Errorf("listing the parents needing reconcile: %w", err)
		}
		parents = append(parents, parentID)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("listing the parents needing reconcile: %w", err)
	}
	return parents, nil
}

// terminalChild is the durable fact the hook needs about one child.
type terminalChild struct {
	childRunID  string
	parentRunID string
	ownerID     string
	state       string
	required    bool
}

// terminalChildFacts returns nil (no error) when the Run is not a terminal
// delegated child of a live parent.
func (s *Service) terminalChildFacts(ctx context.Context, childRunID string) (*terminalChild, error) {
	childRunID = strings.TrimSpace(childRunID)
	if !uuid.Valid(childRunID) {
		return nil, nil
	}
	var (
		facts   terminalChild
		removed sql.NullTime
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT child.id::text, child.parent_run_id::text, child.owner_id::text,
		       child.state, child.deleted_at, receipt.required
		  FROM platform.runs AS child
		  JOIN platform.run_children AS receipt ON receipt.child_run_id = child.id
		 WHERE child.id = $1::uuid`,
		childRunID,
	).Scan(
		&facts.childRunID, &facts.parentRunID, &facts.ownerID,
		&facts.state, &removed, &facts.required,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the child Run: %w", err)
	}
	if removed.Valid || !terminalState(facts.state) {
		return nil, nil
	}
	return &facts, nil
}

// cancelLiveSiblings asks every live direct sibling to stop. The parent owner is
// the caller because a delegated child's owner is always the parent's owner.
func (s *Service) cancelLiveSiblings(ctx context.Context, parentRunID, ownerID string) error {
	if s.CancelRun == nil {
		return nil
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT sibling.id::text FROM platform.runs AS sibling
		 WHERE sibling.parent_run_id = $1::uuid
		   AND sibling.deleted_at IS NULL
		   AND sibling.state NOT IN ('succeeded', 'failed', 'cancelled')
		   AND sibling.cancel_requested_at IS NULL
		   AND EXISTS (SELECT 1 FROM platform.run_children AS receipt
		                WHERE receipt.child_run_id = sibling.id)
		 ORDER BY sibling.created_at, sibling.id`,
		parentRunID,
	)
	if err != nil {
		return fmt.Errorf("reading the live siblings: %w", err)
	}
	var ids []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			rows.Close()
			return fmt.Errorf("reading the live siblings: %w", err)
		}
		ids = append(ids, id)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return fmt.Errorf("reading the live siblings: %w", err)
	}
	if err := rows.Close(); err != nil {
		return fmt.Errorf("reading the live siblings: %w", err)
	}
	var firstErr error
	for _, id := range ids {
		if err := s.CancelRun(ctx, ownerID, id); err != nil && firstErr == nil {
			firstErr = err
		}
	}
	return firstErr
}

// reconcileParent is the idempotent parent transaction. It locks nothing until the
// execution seam locks the parent row, and it decides only from durable rows.
func (s *Service) reconcileParent(ctx context.Context, parentRunID string) error {
	parentRunID = strings.TrimSpace(parentRunID)
	if s.Execution == nil || !uuid.Valid(parentRunID) {
		return nil
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("starting the child lifecycle transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var state string
	err = tx.QueryRowContext(ctx, `
		SELECT state FROM platform.runs
		 WHERE id = $1::uuid AND deleted_at IS NULL`, parentRunID).Scan(&state)
	if errors.Is(err, sql.ErrNoRows) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("reading the parent Run: %w", err)
	}
	if terminalState(state) {
		return nil
	}
	var live bool
	if err := tx.QueryRowContext(ctx, `
		SELECT EXISTS (
		    SELECT 1 FROM platform.runs AS child
		     WHERE child.parent_run_id = $1::uuid
		       AND child.deleted_at IS NULL
		       AND child.state NOT IN ('succeeded', 'failed', 'cancelled')
		)`, parentRunID).Scan(&live); err != nil {
		return fmt.Errorf("checking live child Runs: %w", err)
	}
	if live {
		// A controlled unit is still running: nothing may converge yet.
		return nil
	}

	if state == "waiting_children" {
		outcome, decisionChildID, ok, err := s.childrenDecisionTx(ctx, tx, parentRunID)
		if err != nil {
			return err
		}
		if ok {
			if _, err := s.Execution.ResolveChildrenWaitingTx(
				ctx, tx, parentRunID, outcome, decisionChildID,
			); err != nil {
				return err
			}
		}
		// Decision already stored and the exit fact arrived later (crash between
		// the two halves): the same idempotent maybeEnqueueResumeTx completes it.
		if _, err := s.Execution.MaybeEnqueueResumeTx(ctx, tx, parentRunID); err != nil {
			return err
		}
	} else {
		if _, err := s.Execution.RedriveParentAfterChildrenStoppedTx(ctx, tx, parentRunID); err != nil {
			return err
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("committing the child lifecycle transaction: %w", err)
	}
	return nil
}

// childrenDecisionTx derives the one deterministic children decision from durable
// receipts: a required child that failed/cancelled wins (failed), otherwise every
// terminal child means succeeded. ok is false when no delegated child exists (for
// example a T035 fixture child without a receipt): the documented bridge remains
// the only resolver for those.
func (s *Service) childrenDecisionTx(
	ctx context.Context, tx *sql.Tx, parentRunID string,
) (outcome, childRunID string, ok bool, err error) {
	rows, err := tx.QueryContext(ctx, `
		SELECT receipt.child_run_id::text, receipt.required, child.state
		  FROM platform.run_children AS receipt
		  JOIN platform.runs AS child ON child.id = receipt.child_run_id
		 WHERE receipt.parent_run_id = $1::uuid
		   AND child.deleted_at IS NULL
		 ORDER BY child.created_at, receipt.child_run_id`,
		parentRunID,
	)
	if err != nil {
		return "", "", false, fmt.Errorf("reading the delegation receipts: %w", err)
	}
	type receipt struct {
		childID  string
		required bool
		state    string
	}
	var receipts []receipt
	for rows.Next() {
		var item receipt
		if err := rows.Scan(&item.childID, &item.required, &item.state); err != nil {
			rows.Close()
			return "", "", false, fmt.Errorf("reading the delegation receipts: %w", err)
		}
		receipts = append(receipts, item)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return "", "", false, fmt.Errorf("reading the delegation receipts: %w", err)
	}
	if err := rows.Close(); err != nil {
		return "", "", false, fmt.Errorf("reading the delegation receipts: %w", err)
	}
	if len(receipts) == 0 {
		return "", "", false, nil
	}
	firstTerminal := ""
	for _, item := range receipts {
		if !terminalState(item.state) {
			// The live check above should have caught this; never decide early.
			return "", "", false, nil
		}
		if firstTerminal == "" {
			firstTerminal = item.childID
		}
		if item.required && (item.state == "failed" || item.state == "cancelled") {
			return "failed", item.childID, true, nil
		}
	}
	return "succeeded", firstTerminal, true, nil
}

// terminalState is the closed terminal Run-state vocabulary (mirrors the
// execution package; duplicated here only because the receipt read is local).
func terminalState(state string) bool {
	switch state {
	case "succeeded", "failed", "cancelled":
		return true
	default:
		return false
	}
}
