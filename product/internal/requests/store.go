package requests

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"time"

	"orchara/platform/internal/uuid"
)

// queueCandidate is the earliest unfinished request of one conversation.
type queueCandidate struct {
	ID                 string
	OwnerID            string
	State              string
	RequestedVersionID sql.NullString
	Revision           int64
}

// terminalRunStates is the Run terminal set that stops a dispatched request from
// being unfinished.
const terminalRunStates = "('succeeded','failed','cancelled')"

// slotOccupiedTx is the one logical-slot predicate:
//
//	a live top-level Run
//	OR a pre-dispatch request in routing/awaiting_input/awaiting_selection.
//
// Children never hold the slot (parent_run_id IS NULL), and a dispatched request
// stops holding it when its Run reaches a terminal state.
func slotOccupiedTx(ctx context.Context, tx *sql.Tx, conversationID string) (bool, error) {
	var occupied bool
	err := tx.QueryRowContext(ctx, `
		SELECT EXISTS (
		         SELECT 1 FROM platform.runs AS r
		          WHERE r.conversation_id = $1::uuid
		            AND r.parent_run_id IS NULL
		            AND r.deleted_at IS NULL
		            AND r.state NOT IN `+terminalRunStates+`
		       )
		   OR EXISTS (
		         SELECT 1 FROM platform.requests AS q
		          WHERE q.conversation_id = $1::uuid
		            AND q.state IN ('routing', 'awaiting_input', 'awaiting_selection')
		       )`, conversationID).Scan(&occupied)
	if err != nil {
		return false, fmt.Errorf("reading the conversation slot: %w", err)
	}
	return occupied, nil
}

// earliestUnfinishedTx returns the earliest request of the conversation that is
// not finished, locking it for the caller's transaction. "Unfinished" is
// state NOT IN (cancelled, failed) and, for a dispatched request, a non-terminal
// Run. A dispatched request with no Run at all is treated as finished: the Run is
// created in the dispatch transaction, so a missing one is not a live slot.
func earliestUnfinishedTx(ctx context.Context, tx *sql.Tx, conversationID string) (*queueCandidate, error) {
	var candidate queueCandidate
	err := tx.QueryRowContext(ctx, `
		SELECT r.id::text, r.owner_id::text, r.state,
		       r.requested_version_id::text, r.revision
		  FROM platform.requests AS r
		  LEFT JOIN platform.runs AS u
		         ON u.request_id = r.id AND u.deleted_at IS NULL
		 WHERE r.conversation_id = $1::uuid
		   AND r.state NOT IN ('cancelled', 'failed')
		   AND (
		        r.state <> 'dispatched'
		        OR (u.id IS NOT NULL AND u.state NOT IN `+terminalRunStates+`)
		       )
		 ORDER BY r.created_at, r.id
		 LIMIT 1
		 FOR UPDATE OF r`, conversationID,
	).Scan(&candidate.ID, &candidate.OwnerID, &candidate.State,
		&candidate.RequestedVersionID, &candidate.Revision)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the earliest unfinished request: %w", err)
	}
	return &candidate, nil
}

// ExpireInteractionsTx lazily expires pending interactions whose deadline passed
// and fails their requests with the stable `interaction_expired` code, so the
// slot they held is released without a background timer. It is the one expiry
// path; T079's reply path reports the same outcome deterministically.
func (s *Service) ExpireInteractionsTx(ctx context.Context, tx *sql.Tx, conversationID string) error {
	if _, err := tx.ExecContext(ctx, `
		WITH expired AS (
		    UPDATE platform.interactions AS i
		       SET state = 'expired'
		     WHERE i.state = 'pending'
		       AND i.expires_at <= now()
		       AND i.request_id IN (
		            SELECT id FROM platform.requests WHERE conversation_id = $1::uuid
		       )
		    RETURNING i.request_id
		)
		UPDATE platform.requests AS r
		   SET state = 'failed', error_code = 'interaction_expired',
		       revision = r.revision + 1, updated_at = now()
		  FROM expired
		 WHERE r.id = expired.request_id
		   AND r.state IN ('awaiting_input', 'awaiting_selection')`,
		conversationID,
	); err != nil {
		return fmt.Errorf("expiring conversation interactions: %w", err)
	}
	return nil
}

// AdvanceConversationQueueTx is the one seam every later ticket calls after it
// decides a request's fate: expire timed-out interactions, then, while the slot is
// free, promote the earliest unfinished request. A promoted request either
// dispatches its explicit version immediately or enters `routing` with one durable
// route_request job. It returns the promoted request id, or "" when nothing moved.
//
// Callers must hold the conversation row lock in the same transaction (submit and
// cancel do); that is what makes the promote decision race-free.
func (s *Service) AdvanceConversationQueueTx(
	ctx context.Context, tx *sql.Tx, conversationID string,
) (string, error) {
	if tx == nil {
		return "", errors.New("a transaction is required")
	}
	if !uuid.Valid(conversationID) {
		return "", errors.New("a conversation id is required")
	}
	if err := s.ExpireInteractionsTx(ctx, tx, conversationID); err != nil {
		return "", err
	}
	for attempt := 0; attempt < 20; attempt++ {
		occupied, err := slotOccupiedTx(ctx, tx, conversationID)
		if err != nil {
			return "", err
		}
		if occupied {
			return "", nil
		}
		candidate, err := earliestUnfinishedTx(ctx, tx, conversationID)
		if err != nil {
			return "", err
		}
		if candidate == nil {
			return "", nil
		}
		if candidate.State != StateQueued {
			// A dispatched request whose Run is still live would have occupied the
			// slot; an active pre-dispatch request likewise. Anything else here is a
			// request already in flight, so there is nothing to promote.
			return "", nil
		}
		if candidate.RequestedVersionID.Valid && candidate.RequestedVersionID.String != "" {
			if _, err := s.DispatchVersionTx(ctx, tx, candidate.OwnerID, candidate.ID,
				conversationID, candidate.RequestedVersionID.String); err != nil {
				var refusal *Error
				if errors.As(err, &refusal) && refusal.Status == 409 {
					// The explicit version stopped being runnable (revoked, archived,
					// artifact gone) while the request waited. Fail this request with
					// the refusal code and let the next queued request try.
					if _, failErr := tx.ExecContext(ctx, `
						UPDATE platform.requests
						   SET state = 'failed', error_code = $2,
						       revision = revision + 1, updated_at = now()
						 WHERE id = $1::uuid AND state = 'queued'`,
						candidate.ID, refusal.Code,
					); failErr != nil {
						return "", fmt.Errorf("failing the unroutable request: %w", failErr)
					}
					continue
				}
				return "", err
			}
			return candidate.ID, nil
		}
		if err := promoteQueuedTx(ctx, tx, candidate.ID, candidate.OwnerID); err != nil {
			return "", err
		}
		return candidate.ID, nil
	}
	return "", errors.New("the conversation queue did not converge")
}

// queuePositionTx is the 1-based position of a queued request among the
// conversation's unfinished requests. It is only reported while the request is
// actually waiting; the caller decides whether to expose it.
func (s *Service) queuePositionTx(
	ctx context.Context, tx *sql.Tx, conversationID, requestID string,
) (int, error) {
	var position int
	err := tx.QueryRowContext(ctx, `
		SELECT count(*)
		  FROM platform.requests AS r2
		  LEFT JOIN platform.runs AS u2
		         ON u2.request_id = r2.id AND u2.deleted_at IS NULL
		 WHERE r2.conversation_id = $1::uuid
		   AND r2.state NOT IN ('cancelled', 'failed')
		   AND (
		        r2.state <> 'dispatched'
		        OR (u2.id IS NOT NULL AND u2.state NOT IN `+terminalRunStates+`)
		       )
		   AND (r2.created_at, r2.id) < (
		        SELECT created_at, id FROM platform.requests WHERE id = $2::uuid
		       )`,
		conversationID, requestID,
	).Scan(&position)
	if err != nil {
		return 0, fmt.Errorf("reading the queue position: %w", err)
	}
	return position + 1, nil
}

// readRequestTx reads the contract Request projection inside a transaction.
func (s *Service) readRequestTx(ctx context.Context, tx *sql.Tx, requestID string) (*Request, error) {
	request, err := scanRequest(tx.QueryRowContext(ctx, `
		SELECT r.id::text, r.conversation_id::text, r.state, r.revision, r.error_code,
		       coalesce(r.requested_version_id::text, ''), r.created_at, u.id::text
		  FROM platform.requests AS r
		  LEFT JOIN platform.runs AS u ON u.request_id = r.id AND u.deleted_at IS NULL
		 WHERE r.id = $1::uuid`, requestID))
	if err != nil {
		return nil, err
	}
	interaction, err := s.readPendingInteraction(ctx, tx, requestID)
	if err != nil {
		return nil, err
	}
	request.Interaction = interaction
	return request, nil
}

type rowQuerier interface {
	QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row
}

func scanRequest(row *sql.Row) (*Request, error) {
	var (
		request   Request
		errorCode sql.NullString
		runID     sql.NullString
	)
	if err := row.Scan(&request.ID, &request.ConversationID, &request.State,
		&request.Revision, &errorCode, &request.RequestedVersionID, &request.CreatedAt,
		&runID); err != nil {
		return nil, err
	}
	request.ErrorCode = nullableString(errorCode)
	request.RunID = nullableString(runID)
	return &request, nil
}

func (s *Service) readPendingInteraction(
	ctx context.Context, q rowQuerier, requestID string,
) (*InteractionView, error) {
	var (
		view       InteractionView
		expiresAt  time.Time
		proposalID sql.NullString
	)
	err := q.QueryRowContext(ctx, `
		SELECT id::text, correlation_id::text, kind, expires_at, proposal_artifact_id::text
		  FROM platform.interactions
		 WHERE request_id = $1::uuid AND state = 'pending'
		 ORDER BY created_at DESC, id DESC
		 LIMIT 1`, requestID,
	).Scan(&view.InteractionID, &view.CorrelationID, &view.Kind, &expiresAt, &proposalID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the pending interaction: %w", err)
	}
	view.ExpiresAt = expiresAt.UTC().Format(time.RFC3339Nano)
	if proposalID.Valid && proposalID.String != "" {
		// F08/T079: the interaction proposal (question/candidates/missing fields)
		// is the sealed artifact. It is projected into Request.interaction.proposal,
		// the one open object of the contract Request schema, so the page never
		// needs an object key or a second endpoint. A missing/unreadable proposal
		// degrades to its id, never to invented candidates.
		proposal, err := s.readProposal(ctx, proposalID.String)
		if err != nil {
			return nil, err
		}
		if proposal == nil {
			encoded, err := json.Marshal(map[string]any{"proposal_artifact_id": proposalID.String})
			if err != nil {
				return nil, fmt.Errorf("encoding the interaction proposal: %w", err)
			}
			view.Proposal = json.RawMessage(encoded)
		} else {
			view.Proposal = proposal
		}
	}
	return &view, nil
}

// maxProposalBytes bounds an interaction proposal read. The proposal is a small
// six-field projection; anything larger is not one and is not served as JSON.
const maxProposalBytes = 1 << 20

// readProposal reads one sealed interaction proposal artifact. A nil result means
// the artifact is missing, deleted or unreadable; the caller then exposes only the
// artifact id instead of a fabricated proposal.
func (s *Service) readProposal(ctx context.Context, artifactID string) (json.RawMessage, error) {
	if s.Objects == nil {
		return nil, nil
	}
	var (
		objectKey string
		state     string
		deleted   sql.NullTime
		size      sql.NullInt64
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT object_key, state, deleted_at, size_bytes
		  FROM platform.artifacts WHERE id = $1::uuid`, artifactID,
	).Scan(&objectKey, &state, &deleted, &size)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the interaction proposal artifact: %w", err)
	}
	if deleted.Valid || state != "ready" || !size.Valid || size.Int64 <= 0 || size.Int64 > maxProposalBytes {
		return nil, nil
	}
	reader, _, err := s.Objects.Open(objectKey)
	if err != nil {
		return nil, nil
	}
	defer func() { _ = reader.Close() }()
	content, err := io.ReadAll(io.LimitReader(reader, maxProposalBytes+1))
	if err != nil || int64(len(content)) > maxProposalBytes {
		return nil, nil
	}
	var value map[string]any
	if err := json.Unmarshal(content, &value); err != nil || value == nil {
		return nil, nil
	}
	return json.RawMessage(content), nil
}

// RequestFacts are the documented non-contract headers a single-request read
// exposes: the handling version (once a Run exists), the queue position while
// waiting and the pending interaction's correlation id. The contract Request
// schema is additionalProperties:false, so these facts must not become JSON fields.
type RequestFacts struct {
	ApplicationVersionID string
	VersionNo            int64
	QueuePosition        int
	InteractionID        string
	CorrelationID        string
}

// GetRequest returns one request to its owner. Missing, foreign, malformed and a
// request whose conversation was tombstoned are the same 404.
//
// A queued request additionally triggers one lazy queue advance before it is
// returned: a Run that finished through any path (including the existing
// POST /runs/{id}/cancel or a supervisor result) must not leave the next request
// waiting for an unrelated write. This is the read-side counterpart of the lazy
// interaction expiry and is documented in the T074 report/ADR.
func (s *Service) GetRequest(
	ctx context.Context, callerID, requestID string,
) (*Request, *RequestFacts, error) {
	request, facts, err := s.readRequestForOwner(ctx, callerID, requestID)
	if err != nil {
		return nil, nil, err
	}
	if request.State != StateQueued {
		return request, facts, nil
	}
	if err := s.advanceOnRead(ctx, callerID, request.ConversationID); err != nil {
		return nil, nil, err
	}
	return s.readRequestForOwner(ctx, callerID, requestID)
}

// advanceOnRead is the bounded, transactional queue convergence behind a queued
// request read. It is idempotent: when the slot is occupied or nothing is queued it
// writes nothing.
func (s *Service) advanceOnRead(ctx context.Context, callerID, conversationID string) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("starting the queue convergence: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := s.conversationAliveTx(ctx, tx, callerID, conversationID); err != nil {
		return err
	}
	if _, err := s.AdvanceConversationQueueTx(ctx, tx, conversationID); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("committing the queue convergence: %w", err)
	}
	return nil
}

func (s *Service) readRequestForOwner(
	ctx context.Context, callerID, requestID string,
) (*Request, *RequestFacts, error) {
	owner, ok := requireUUID(callerID)
	if !ok {
		return nil, nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	id, ok := requireUUID(requestID)
	if !ok {
		return nil, nil, requestNotFound()
	}
	var (
		request   Request
		errorCode sql.NullString
		runID     sql.NullString
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT r.id::text, r.conversation_id::text, r.state, r.revision, r.error_code,
		       coalesce(r.requested_version_id::text, ''), r.created_at, u.id::text
		  FROM platform.requests AS r
		  JOIN platform.conversations AS c ON c.id = r.conversation_id
		  LEFT JOIN platform.runs AS u ON u.request_id = r.id AND u.deleted_at IS NULL
		 WHERE r.id = $1::uuid AND r.owner_id = $2::uuid AND c.deleted_at IS NULL`,
		id, owner,
	).Scan(&request.ID, &request.ConversationID, &request.State, &request.Revision,
		&errorCode, &request.RequestedVersionID, &request.CreatedAt, &runID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil, requestNotFound()
	}
	if err != nil {
		return nil, nil, fmt.Errorf("reading the request: %w", err)
	}
	request.ErrorCode = nullableString(errorCode)
	request.RunID = nullableString(runID)
	interaction, err := s.readPendingInteraction(ctx, s.DB, id)
	if err != nil {
		return nil, nil, err
	}
	request.Interaction = interaction
	facts, err := s.Facts(ctx, &request)
	if err != nil {
		return nil, nil, err
	}
	return &request, facts, nil
}

// Facts computes the documented non-contract facts of one request read: the
// handling version once a Run exists, the 1-based queue position while waiting and
// the pending interaction's correlation id. The contract Request schema has no
// fields for them (additionalProperties:false), so the HTTP layer carries them as
// response headers only.
func (s *Service) Facts(ctx context.Context, request *Request) (*RequestFacts, error) {
	if request == nil {
		return nil, errors.New("a request is required")
	}
	facts := &RequestFacts{}
	if request.RunID != nil {
		var (
			versionID string
			versionNo int64
		)
		err := s.DB.QueryRowContext(ctx, `
			SELECT coalesce(u.application_version_id::text, ''), coalesce(v.version_no, 0)
			  FROM platform.runs AS u
			  LEFT JOIN platform.application_versions AS v ON v.id = u.application_version_id
			 WHERE u.id = $1::uuid`, *request.RunID,
		).Scan(&versionID, &versionNo)
		if err != nil {
			return nil, fmt.Errorf("reading the handling version: %w", err)
		}
		facts.ApplicationVersionID = versionID
		facts.VersionNo = versionNo
	}
	if request.Interaction != nil {
		facts.InteractionID = request.Interaction.InteractionID
		facts.CorrelationID = request.Interaction.CorrelationID
	}
	if request.State == StateQueued && request.ConversationID != "" {
		tx, err := s.DB.BeginTx(ctx, nil)
		if err != nil {
			return nil, fmt.Errorf("starting the queue-position read: %w", err)
		}
		defer func() { _ = tx.Rollback() }()
		position, err := s.queuePositionTx(ctx, tx, request.ConversationID, request.ID)
		if err != nil {
			return nil, err
		}
		facts.QueuePosition = position
	}
	return facts, nil
}
