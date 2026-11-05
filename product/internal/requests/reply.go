package requests

// F08/T079: the explicit reply to a pending input/selection interaction.
//
// A reply is a control operation, not a new queued task (STATE-MACHINES.md §19):
// it answers exactly one pending interaction, moves the original request back to
// `routing` and enqueues one route job for the resumed round. It never creates a
// Run itself; the router creates exactly one after re-validating the resumed
// choice/inputs.
//
// The 409 `interaction_closed` path uses the exact CAS predicate from the shared
// exploration notes §5.3:
//
//	state = 'pending' AND expires_at > now()
//
// A zero-row update is re-read in the same transaction. A still-pending but
// expired interaction is moved to `expired` and its request to
// `failed(interaction_expired)` in that same transaction, so a second reply is
// deterministic; an answered/invalidated interaction or a request that left
// `awaiting_*` is the same 409 and writes nothing. Expiry also releases the
// conversation slot through AdvanceConversationQueueTx, so the next queued request
// is admitted.

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"unicode/utf8"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/background"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/uuid"
)

// CodeInteractionClosed is the stable 409 code for an ended/expired interaction.
const CodeInteractionClosed = "interaction_closed"

// CodeCandidateNotInSnapshot refuses a candidate the current routing round did not
// offer. It is the reply-side twin of the router's route_candidate_invalid.
const CodeCandidateNotInSnapshot = "candidate_not_in_snapshot"

// ReplyPath is the contract operation path for one request's reply. The request id
// is part of the idempotency scope, so a key can never be borrowed by another
// request's reply.
func ReplyPath(requestID string) string {
	return "/api/v1/requests/" + strings.ToLower(requestID) + "/reply"
}

// ReplyInput is one validated InteractionReply body plus its canonical digest.
type ReplyInput struct {
	InteractionID      string
	Message            string
	CandidateVersionID string
	ArtifactIDs        []string
	RequestSHA256      string
}

var replyFields = map[string]bool{
	"interaction_id":       true,
	"message":              true,
	"candidate_version_id": true,
	"artifact_ids":         true,
}

// ParseReply validates the raw InteractionReply members. The contract is
// additionalProperties:false and anyOf[message | candidate_version_id |
// artifact_ids]; every violation is a located 422.
func ParseReply(fields map[string]json.RawMessage) (*ReplyInput, *Error) {
	unknown := make([]string, 0)
	for key := range fields {
		if !replyFields[key] {
			unknown = append(unknown, key)
		}
	}
	if len(unknown) > 0 {
		return nil, failureWithDetails(422, "validation_failed",
			"the reply body contains unknown members", map[string]any{"fields": sorted(unknown)})
	}
	input := &ReplyInput{}
	interactionID, err := stringValue(fields["interaction_id"])
	if err != nil || !uuid.Valid(interactionID) {
		return nil, located("interaction_id", "a valid interaction_id UUID is required")
	}
	input.InteractionID = strings.ToLower(interactionID)

	provided := false
	if raw, present := fields["message"]; present {
		provided = true
		message, err := stringValue(raw)
		if err != nil {
			return nil, located("message", "message must be a string")
		}
		if utf8.RuneCountInString(message) > maxMessageRunes {
			return nil, located("message", "message must be at most 32000 characters")
		}
		input.Message = message
	}
	if raw, present := fields["candidate_version_id"]; present {
		provided = true
		versionID, err := stringValue(raw)
		if err != nil || !uuid.Valid(versionID) {
			return nil, located("candidate_version_id", "candidate_version_id must be a UUID")
		}
		input.CandidateVersionID = strings.ToLower(versionID)
	}
	if raw, present := fields["artifact_ids"]; present {
		provided = true
		artifacts, parseErr := uuidList(raw, "artifact_ids", maxArtifacts)
		if parseErr != nil {
			return nil, parseErr
		}
		if len(artifacts) == 0 {
			return nil, located("artifact_ids", "artifact_ids must contain at least one artifact")
		}
		input.ArtifactIDs = artifacts
	}
	if !provided {
		return nil, failureWithDetails(422, "validation_failed",
			"a reply must carry a message, a candidate_version_id or artifact_ids",
			map[string]any{"anyOf": []string{"message", "candidate_version_id", "artifact_ids"}})
	}
	digest, err := replyDigest(input)
	if err != nil {
		return nil, failure(422, "validation_failed",
			"the reply body cannot be canonicalised: "+err.Error())
	}
	input.RequestSHA256 = digest
	return input, nil
}

func replyDigest(input *ReplyInput) (string, error) {
	canonical := map[string]any{"interaction_id": input.InteractionID}
	if input.Message != "" {
		canonical["message"] = input.Message
	}
	if input.CandidateVersionID != "" {
		canonical["candidate_version_id"] = input.CandidateVersionID
	}
	if input.ArtifactIDs != nil {
		canonical["artifact_ids"] = input.ArtifactIDs
	}
	text, err := json.Marshal(canonical)
	if err != nil {
		return "", err
	}
	bytes, err := jcs.CanonicalizeBytes(text)
	if err != nil {
		return "", err
	}
	return jcs.SHA256Hex(bytes), nil
}

// ReplyInteraction answers one pending interaction for the caller's request.
//
// A successful reply returns the updated Request (202) and stores an idempotency
// receipt; a replay of the same key returns that stored body and a different body
// under the same key is 409 idempotency_conflict. A closed/expired interaction is
// 409 interaction_closed and writes no receipt, so a retry cannot become a second
// control operation.
func (s *Service) ReplyInteraction(
	ctx context.Context, callerID, requestID string, scope IdempotencyScope, input *ReplyInput,
) (*SubmitOutcome, error) {
	caller, ok := requireUUID(callerID)
	if !ok {
		return nil, failure(401, "unauthenticated", "an authenticated human is required")
	}
	id, ok := requireUUID(requestID)
	if !ok {
		return nil, requestNotFound()
	}
	if input == nil || input.RequestSHA256 == "" {
		return nil, failure(422, "validation_failed", "a parsed reply body is required")
	}
	if !uuid.Valid(scope.PrincipalID) || !strings.EqualFold(scope.PrincipalID, caller) ||
		scope.Method != "POST" || scope.Path != ReplyPath(id) ||
		strings.TrimSpace(scope.Key) == "" {
		return nil, failureWithDetails(400, "idempotency_key_required",
			"the Idempotency-Key header is required and must be one non-empty value",
			map[string]any{"header": "Idempotency-Key"})
	}

	// The candidate must belong to the candidate snapshot the routing round
	// published, and must still be runnable now. This is the reply-side
	// re-validation API.md §16 requires; it happens before the interaction CAS so a
	// refused reply leaves the interaction pending and can be retried.
	if input.CandidateVersionID != "" {
		if err := s.checkCandidateSnapshot(ctx, id, input.CandidateVersionID); err != nil {
			return nil, err
		}
		if _, err := s.Catalog.ResolveRunnableVersion(ctx, caller, input.CandidateVersionID); err != nil {
			return nil, versionRefusal(err)
		}
	}
	if len(input.ArtifactIDs) > 0 {
		if err := s.checkArtifacts(ctx, caller, input.ArtifactIDs); err != nil {
			return nil, err
		}
	}

	responseBody, err := json.Marshal(map[string]any{
		"interaction_id":       input.InteractionID,
		"message":              input.Message,
		"candidate_version_id": input.CandidateVersionID,
		"artifact_ids":         input.ArtifactIDs,
	})
	if err != nil {
		return nil, fmt.Errorf("encoding the interaction reply: %w", err)
	}
	responseKey, responseSHA, responseSize, err := s.sealBytes(ctx, responseBody)
	if err != nil {
		return nil, err
	}
	outcome, err := s.replyTx(ctx, caller, id, scope, input, responseKey, responseSHA, responseSize)
	if err != nil || (outcome != nil && outcome.Replayed) {
		s.discardSealed(responseKey)
	}
	return outcome, err
}

// checkCandidateSnapshot refuses a version id that was not in the persisted
// candidate snapshot of the request.
func (s *Service) checkCandidateSnapshot(ctx context.Context, requestID, versionID string) error {
	var inSnapshot bool
	err := s.DB.QueryRowContext(ctx, `
		SELECT EXISTS (
		    SELECT 1 FROM platform.request_routing_candidates
		     WHERE request_id = $1::uuid AND application_version_id = $2::uuid
		)`, requestID, versionID).Scan(&inSnapshot)
	if err != nil {
		return fmt.Errorf("reading the candidate snapshot: %w", err)
	}
	if !inSnapshot {
		return failureWithDetails(422, CodeCandidateNotInSnapshot,
			"that version is not one of the candidates offered for this request",
			map[string]any{"field": "candidate_version_id"})
	}
	return nil
}

func (s *Service) replyTx(
	ctx context.Context, caller, requestID string, scope IdempotencyScope, input *ReplyInput,
	responseKey, responseSHA string, responseSize int64,
) (*SubmitOutcome, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the interaction reply transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var conversationID string
	err = tx.QueryRowContext(ctx, `
		SELECT conversation_id::text FROM platform.requests
		 WHERE id = $1::uuid AND owner_id = $2::uuid`, requestID, caller).Scan(&conversationID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, requestNotFound()
	}
	if err != nil {
		return nil, fmt.Errorf("reading the replied request: %w", err)
	}
	if _, err := s.conversationAliveTx(ctx, tx, caller, conversationID); err != nil {
		return nil, err
	}

	// Claim the receipt first: a concurrent duplicate blocks here and then loses
	// the insert, replaying the committed body below.
	result, err := tx.ExecContext(ctx, `
		INSERT INTO platform.idempotency_keys
		       (principal_id, method, path, key, request_sha256, expires_at)
		VALUES ($1::uuid, $2, $3, $4, $5, now() + make_interval(secs => $6))
		ON CONFLICT (principal_id, method, path, key) DO NOTHING`,
		scope.PrincipalID, scope.Method, scope.Path, scope.Key,
		input.RequestSHA256, idempotencyTTL,
	)
	if err != nil {
		return nil, fmt.Errorf("claiming the reply receipt: %w", err)
	}
	claimed, err := result.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("reading the reply receipt claim: %w", err)
	}
	if claimed == 0 {
		_ = tx.Rollback()
		return s.replay(ctx, scope, input.RequestSHA256)
	}

	// The exact CAS predicate (notes §5.3). answered_at is set with the state; the
	// response artifact is linked in a second statement after its row exists, so
	// the immediate FK is satisfied and a losing CAS writes nothing.
	var correlationID string
	err = tx.QueryRowContext(ctx, `
		UPDATE platform.interactions
		   SET state = 'answered', answered_at = now()
		 WHERE id = $1::uuid AND request_id = $2::uuid
		   AND state = 'pending' AND expires_at > now()
		 RETURNING correlation_id::text`,
		input.InteractionID, requestID,
	).Scan(&correlationID)
	if errors.Is(err, sql.ErrNoRows) {
		// The CAS lost. Roll the receipt claim and every other write back first: an
		// expired/answered reply must not leave a receipt row (notes §5.3), so a
		// retry with the same key repeats the same 409 instead of replaying an
		// empty receipt.
		_ = tx.Rollback()
		return nil, s.closeInteraction(ctx, conversationID, requestID, input.InteractionID)
	}
	if err != nil {
		return nil, fmt.Errorf("answering the interaction: %w", err)
	}

	if responseKey != "" {
		responseArtifactID := uuid.NewString()
		if err := s.insertArtifactTx(ctx, tx, responseArtifactID, caller, responseKey,
			"interaction_reply", "application/json", responseSHA, responseSize); err != nil {
			return nil, err
		}
		if _, err := tx.ExecContext(ctx, `
			UPDATE platform.interactions SET response_artifact_id = $2::uuid
			 WHERE id = $1::uuid`, input.InteractionID, responseArtifactID); err != nil {
			return nil, fmt.Errorf("linking the interaction reply artifact: %w", err)
		}
	}

	revision, err := applyReplyTx(ctx, tx, requestID, input)
	if err != nil {
		return nil, err
	}
	if _, _, err := background.EnqueueTx(ctx, tx, background.EnqueueParams{
		Kind:             background.KindRouteRequest,
		TargetID:         requestID,
		OwnerID:          caller,
		DedupKey:         requestID + ":reply:" + input.InteractionID,
		ExpectedRevision: &revision,
	}); err != nil {
		return nil, fmt.Errorf("enqueueing the resumed route_request job: %w", err)
	}
	actor := caller
	if err := audit.Record(ctx, tx, &actor, "request.interaction_replied", "request",
		&requestID, map[string]any{
			"interaction_id": input.InteractionID,
			"correlation_id": correlationID,
			"candidate":      input.CandidateVersionID != "",
			"artifacts":      len(input.ArtifactIDs),
		}); err != nil {
		return nil, err
	}

	created, err := s.readRequestTx(ctx, tx, requestID)
	if err != nil {
		return nil, err
	}
	payload, err := json.Marshal(created)
	if err != nil {
		return nil, fmt.Errorf("encoding the reply response: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.idempotency_keys
		   SET response_code = 202, response_json = $5::jsonb
		 WHERE principal_id = $1::uuid AND method = $2 AND path = $3 AND key = $4`,
		scope.PrincipalID, scope.Method, scope.Path, scope.Key, string(payload),
	); err != nil {
		return nil, fmt.Errorf("storing the idempotent reply response: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the interaction reply: %w", err)
	}
	return &SubmitOutcome{Request: created, Replayed: false}, nil
}

// applyReplyTx records the answer content on the request and returns the new
// revision. Artifacts supplied by the reply become part of the request's input
// closure (they are exactly the missing fields the next Run links); a candidate
// choice is recorded for the resumed round.
func applyReplyTx(
	ctx context.Context, tx *sql.Tx, requestID string, input *ReplyInput,
) (int64, error) {
	var revision int64
	err := tx.QueryRowContext(ctx, `
		UPDATE platform.requests
		   SET state = 'routing',
		       revision = revision + 1,
		       error_code = NULL,
		       selected_version_id = COALESCE($2::uuid, selected_version_id),
		       artifact_ids = (
		           SELECT coalesce(array_agg(DISTINCT entry), '{}'::uuid[])
		             FROM unnest(artifact_ids || $3::uuid[]) AS entry
		       ),
		       updated_at = now()
		 WHERE id = $1::uuid AND state IN ('awaiting_input', 'awaiting_selection')
		 RETURNING revision`,
		requestID, nullableUUID(input.CandidateVersionID), uuidArrayLiteral(input.ArtifactIDs),
	).Scan(&revision)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, failure(409, CodeInteractionClosed,
			"that interaction is already closed; submit a new request instead of reviving it")
	}
	if err != nil {
		return 0, fmt.Errorf("resuming the replied request: %w", err)
	}
	return revision, nil
}

// closeInteraction re-reads the CAS loser in its own transaction and commits the
// deterministic outcome. It returns a 409 error (interaction_closed) unless the
// interaction does not belong to this request (404). An expired pending interaction
// is closed and its request fails in this transaction, releasing the conversation
// slot. The failed reply never writes a receipt.
func (s *Service) closeInteraction(
	ctx context.Context, conversationID, requestID, interactionID string,
) error {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("starting the interaction closure: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	var (
		state        string
		expired      bool
		requestState string
	)
	err = tx.QueryRowContext(ctx, `
		SELECT i.state, i.expires_at <= now(), r.state
		  FROM platform.interactions AS i
		  JOIN platform.requests AS r ON r.id = i.request_id
		 WHERE i.id = $1::uuid AND i.request_id = $2::uuid`,
		interactionID, requestID,
	).Scan(&state, &expired, &requestState)
	if errors.Is(err, sql.ErrNoRows) {
		return requestNotFound()
	}
	if err != nil {
		return fmt.Errorf("reading the closed interaction: %w", err)
	}
	closed := failure(409, CodeInteractionClosed,
		"that interaction is already closed; submit a new request instead of reviving it")
	if state != InteractionPending || !expired ||
		(requestState != StateAwaitingInput && requestState != StateAwaitingSelection) {
		// Answered, invalidated, or the request already left the waiting state:
		// nothing to write and nothing to revive.
		return closed
	}
	// Still pending but past its deadline: expire it and fail the request in one
	// transaction so a second reply is deterministic. Lock the conversation row in
	// the same order every queue mutator uses, because the expiry releases the slot
	// while AdvanceConversationQueueTx promotes the next request below.
	var locked string
	if err := tx.QueryRowContext(ctx, `
		SELECT id::text FROM platform.conversations
		 WHERE id = $1::uuid AND deleted_at IS NULL FOR UPDATE`, conversationID,
	).Scan(&locked); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return requestNotFound()
		}
		return fmt.Errorf("locking the expired request's conversation: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.interactions SET state = 'expired'
		 WHERE id = $1::uuid AND request_id = $2::uuid AND state = 'pending'`,
		interactionID, requestID); err != nil {
		return fmt.Errorf("expiring the interaction: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.requests
		   SET state = 'failed', error_code = 'interaction_expired',
		       revision = revision + 1, selected_version_id = NULL, updated_at = now()
		 WHERE id = $1::uuid AND state IN ('awaiting_input', 'awaiting_selection')`,
		requestID); err != nil {
		return fmt.Errorf("failing the expired request: %w", err)
	}
	if err := audit.Record(ctx, tx, nil, "request.interaction_expired", "request",
		&requestID, map[string]any{"interaction_id": interactionID}); err != nil {
		return err
	}
	// Expiry releases the logical slot: promote the next queued request now.
	if _, err := s.AdvanceConversationQueueTx(ctx, tx, conversationID); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("committing the interaction expiry: %w", err)
	}
	return closed
}
