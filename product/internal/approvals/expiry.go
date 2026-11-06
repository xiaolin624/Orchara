package approvals

// F04B/T087: the automatic approval-deadline sweeper.
//
// A prepared approval is a proposal with a server-owned deadline. The decision
// path already treats a due approval as expired, but a Run parked in
// `waiting_approval` must also terminate when nobody ever calls the decision
// endpoint. This file owns that second arrival order:
//
//	ExpireDue   claims a bounded set of due pending *node* approvals
//	            (`operation_id IS NULL`), expires each row in its own
//	            Run -> approval lock order, and hands the expired decision to the
//	            one F03/T035 resume transaction. F06 capability approvals keep
//	            their documented decision/lazy boundary.
//
// Safety properties:
//   - the database clock decides, never a process clock;
//   - each row is re-checked under lock, so a concurrent Approve/Reject wins
//     cleanly and the sweeper does nothing;
//   - only a wait whose checkpoint names this approval is resolved; a stale
//     pending approval from an earlier proposal cannot fail the Run that is
//     actually waiting on a later one;
//   - a Run whose waiting result has not landed yet simply keeps the durable
//     expired decision; `adoptApprovalDecisionTx` fails it when the result
//     arrives, exactly like the existing decision-before-waiting order.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"
)

// DefaultSweepLimit bounds one ExpireDue pass. It is deliberately small: a
// persistent database problem must not turn one tick into an unbounded batch.
const DefaultSweepLimit = 100

// ExpireDue expires up to limit due pending node approvals and resolves the
// waiting Runs that actually parked on them. It returns the number of rows this
// pass expired; per-row failures are joined so one broken row does not stop the
// rest.
func (s *Service) ExpireDue(ctx context.Context, limit int) (int, error) {
	if s == nil || s.DB == nil {
		return 0, errors.New("approval expiry has no database")
	}
	if limit <= 0 || limit > DefaultSweepLimit {
		limit = DefaultSweepLimit
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT id::text
		  FROM platform.approvals
		 WHERE state = 'pending' AND operation_id IS NULL AND expires_at <= now()
		 ORDER BY expires_at, id
		 LIMIT $1`, limit)
	if err != nil {
		return 0, fmt.Errorf("scanning due approvals: %w", err)
	}
	defer rows.Close()

	ids := make([]string, 0, limit)
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return 0, fmt.Errorf("scanning a due approval: %w", err)
		}
		ids = append(ids, id)
	}
	if err := rows.Err(); err != nil {
		return 0, fmt.Errorf("scanning due approvals: %w", err)
	}

	expired := 0
	var failures []error
	for _, id := range ids {
		result, err := s.expireOne(ctx, id)
		if err != nil {
			failures = append(failures, fmt.Errorf("expiring approval %s: %w", id, err))
			continue
		}
		if result == nil {
			// A concurrent decision, cancellation or expiry won; nothing to do.
			continue
		}
		expired++
		if !result.resolveWaiting {
			continue
		}
		if err := s.resolveWaiting(ctx, result.approval, "expired"); err != nil {
			failures = append(failures,
				fmt.Errorf("resolving the run waiting on approval %s: %w", id, err))
		}
	}
	return expired, errors.Join(failures...)
}

// expiredApproval is one row expired by the sweeper plus whether this approval
// is the Run's current waiting checkpoint.
type expiredApproval struct {
	approval       *Approval
	resolveWaiting bool
}

// expireOne re-checks and expires one due approval. It returns nil when another
// writer already moved the row or the deadline is no longer due.
//
// Locking order is the decision path's Run -> approval order, so a sweeper and a
// human decision serialize instead of deadlocking. The row is re-read under the
// lock because the database clock may have been overtaken by another writer.
func (s *Service) expireOne(ctx context.Context, approvalID string) (*expiredApproval, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the expiry transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	// Peek for the Run identity before taking any lock; the lock order is the
	// same one Decide uses.
	peek, err := loadApprovalTx(ctx, tx, approvalID, false)
	if err != nil {
		return nil, fmt.Errorf("reading the approval: %w", err)
	}
	if peek == nil || peek.State != "pending" {
		return nil, nil
	}
	run, err := lockApprovalRunTx(ctx, tx, peek.RunID)
	if err != nil {
		return nil, err
	}
	if run == nil {
		return nil, fmt.Errorf("the approval's run no longer exists")
	}
	approval, err := loadApprovalTx(ctx, tx, approvalID, true)
	if err != nil {
		return nil, fmt.Errorf("locking the approval: %w", err)
	}
	if approval == nil || approval.State != "pending" {
		return nil, nil
	}

	var due bool
	if err := tx.QueryRowContext(ctx,
		`SELECT now() >= expires_at FROM platform.approvals WHERE id = $1::uuid`,
		approval.ID,
	).Scan(&due); err != nil {
		return nil, fmt.Errorf("checking the approval deadline: %w", err)
	}
	if !due {
		return nil, nil
	}

	resolve, err := waitingReferencesApprovalTx(ctx, tx, approval)
	if err != nil {
		return nil, err
	}
	if resolve && s.Execution == nil {
		// Expiring the row without the resume authority would leave the Run
		// parked forever: fail this row closed and let the next pass retry.
		return nil, errors.New("no resume authority is configured")
	}

	var decidedAt time.Time
	err = tx.QueryRowContext(ctx, `
		UPDATE platform.approvals
		   SET state = 'expired', decided_at = now(), decided_by = NULL,
		       decision_key = 'sweeper:expired'
		 WHERE id = $1::uuid AND state = 'pending'
		 RETURNING decided_at`, approval.ID).Scan(&decidedAt)
	if errors.Is(err, sql.ErrNoRows) {
		// The row moved between the lock re-read and the update; another
		// decision owns it now.
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("expiring the approval: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the expired approval: %w", err)
	}
	approval.State = "expired"
	decided := decidedAt.UTC()
	approval.DecidedAt = &decided
	return &expiredApproval{approval: approval, resolveWaiting: resolve}, nil
}

// waitingReferencesApprovalTx reports whether the Run's durable waiting
// checkpoint names this approval. A pending approval that is no longer the
// current wait (for example an earlier proposal after the worker prepared a
// changed one) must still expire, but it must not resolve another wait.
func waitingReferencesApprovalTx(
	ctx context.Context, tx *sql.Tx, approval *Approval,
) (bool, error) {
	var checkpoint string
	err := tx.QueryRowContext(ctx, `
		SELECT checkpoint_id FROM platform.run_waitings WHERE run_id = $1::uuid`,
		approval.RunID).Scan(&checkpoint)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("reading the waiting checkpoint: %w", err)
	}
	return waitingReferencesApproval(checkpoint, approval), nil
}

// waitingReferencesApproval compares the node-approval checkpoint form. The
// sweeper deliberately does not expire F06 capability approvals: that aggregate's
// documented boundary is the decision/lazy check, and a parked operation has its
// own `capability:<operation_id>` wait.
func waitingReferencesApproval(checkpoint string, approval *Approval) bool {
	return strings.TrimSpace(checkpoint) == "approval:"+approval.ID
}
