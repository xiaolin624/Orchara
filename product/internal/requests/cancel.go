package requests

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/audit"
)

// Cancel records one user cancel intent for a Request the caller owns.
//
// The two shapes are the STATE-MACHINES.md §15/§17 rules:
//
//   - a pre-dispatch request (queued/routing/awaiting_input/awaiting_selection) is
//     cancelled in the same transaction that invalidates its pending interaction
//     and promotes the next queued request;
//   - a dispatched request keeps its state (the contract has no post-dispatch
//     transition) and its Run is cancelled through the existing F03/T036 path;
//     after that commit the freed slot promotes the next queued request.
//
// A repeated cancel is idempotent: the same 202 with the terminal state and no
// second event. A failed request is 409 state_conflict — the platform never revives
// it, and the user submits a new request instead.
func (s *Service) Cancel(ctx context.Context, callerID, requestID string) (*Request, error) {
	caller, ok := requireUUID(callerID)
	if !ok {
		return nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	id, ok := requireUUID(requestID)
	if !ok {
		return nil, requestNotFound()
	}

	// Read the request first (no lock) to learn its conversation; the conversation
	// row lock below serialises with submit/delete, then the request is re-read
	// under FOR UPDATE in the same lock order every mutator uses.
	var conversationID, state string
	err := s.DB.QueryRowContext(ctx, `
		SELECT conversation_id::text, state
		  FROM platform.requests
		 WHERE id = $1::uuid AND owner_id = $2::uuid`, id, caller,
	).Scan(&conversationID, &state)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, requestNotFound()
	}
	if err != nil {
		return nil, fmt.Errorf("reading the request: %w", err)
	}

	if state == StateDispatched {
		return s.cancelDispatched(ctx, caller, id, conversationID)
	}
	if state == StateFailed {
		return nil, failure(409, "state_conflict",
			"the request already failed; submit a new request instead of reviving it")
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the request cancel transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	if _, err := s.conversationAliveTx(ctx, tx, caller, conversationID); err != nil {
		return nil, err
	}
	var (
		currentState string
	)
	err = tx.QueryRowContext(ctx, `
		SELECT state
		  FROM platform.requests
		 WHERE id = $1::uuid AND owner_id = $2::uuid
		 FOR UPDATE`, id, caller,
	).Scan(&currentState)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, requestNotFound()
	}
	if err != nil {
		return nil, fmt.Errorf("locking the request: %w", err)
	}

	switch currentState {
	case StateCancelled:
		// Repeated cancel: the terminal state is authoritative; no new event, no
		// second queue advance (already done by the first cancel).
		request, err := s.readRequestTx(ctx, tx, id)
		if err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the repeated cancel: %w", err)
		}
		return request, nil
	case StateQueued, StateRouting, StateAwaitingInput, StateAwaitingSelection:
		if currentState == StateAwaitingInput || currentState == StateAwaitingSelection {
			if _, err := tx.ExecContext(ctx, `
				UPDATE platform.interactions
				   SET state = 'invalidated'
				 WHERE request_id = $1::uuid AND state = 'pending'`, id); err != nil {
				return nil, fmt.Errorf("invalidating the pending interaction: %w", err)
			}
		}
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.requests
			   SET state = 'cancelled', revision = revision + 1, updated_at = now()
			 WHERE id = $1::uuid AND state = $2`,
			id, currentState); err != nil {
			return nil, fmt.Errorf("cancelling the request: %w", err)
		}
		actor := caller
		metadata := map[string]any{"previous_state": currentState}
		if err := audit.Record(ctx, tx, &actor, "request.cancelled", "request", &id, metadata); err != nil {
			return nil, err
		}
		// The cancelled request released the slot (if it held one): promote the next
		// queued request in the same transaction.
		if _, err := s.AdvanceConversationQueueTx(ctx, tx, conversationID); err != nil {
			return nil, err
		}
		request, err := s.readRequestTx(ctx, tx, id)
		if err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the cancel: %w", err)
		}
		return request, nil
	default:
		return nil, failure(409, "state_conflict",
			"the request is in a state that cannot be cancelled")
	}
}

// cancelDispatched cancels the request's Run through the existing execution cancel
// path, then advances the conversation queue. The request state deliberately stays
// `dispatched` (STATE-MACHINES.md §17): the Run is the observable result.
func (s *Service) cancelDispatched(
	ctx context.Context, callerID, requestID, conversationID string,
) (*Request, error) {
	var runID string
	err := s.DB.QueryRowContext(ctx, `
		SELECT u.id::text
		  FROM platform.requests AS r
		  JOIN platform.conversations AS c ON c.id = r.conversation_id
		  LEFT JOIN platform.runs AS u ON u.request_id = r.id AND u.deleted_at IS NULL
		 WHERE r.id = $1::uuid AND r.owner_id = $2::uuid AND c.deleted_at IS NULL`,
		requestID, callerID,
	).Scan(&runID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, requestNotFound()
	}
	if err != nil {
		return nil, fmt.Errorf("reading the dispatched run: %w", err)
	}
	if strings.TrimSpace(runID) == "" {
		return nil, failure(409, "state_conflict",
			"the dispatched request has no Run to cancel")
	}
	if s.CancelRun == nil {
		return nil, failure(503, "cancel_unavailable",
			"the execution cancel path is not configured")
	}
	if err := s.CancelRun(ctx, callerID, runID); err != nil {
		return nil, err
	}

	request, _, err := s.GetRequest(ctx, callerID, requestID)
	if err != nil {
		return nil, err
	}
	// The Run cancel may have released the slot; promote the next queued request.
	// A concurrent advance is harmless (the conversation lock serialises it).
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the queue advance: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := s.conversationAliveTx(ctx, tx, callerID, conversationID); err != nil {
		return nil, err
	}
	if _, err := s.AdvanceConversationQueueTx(ctx, tx, conversationID); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the queue advance: %w", err)
	}
	return request, nil
}
