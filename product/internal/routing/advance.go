package routing

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"orchara/platform/internal/audit"
	"orchara/platform/internal/catalog"
	"orchara/platform/internal/jcs"
	"orchara/platform/internal/modelproxy"
	"orchara/platform/internal/requests"
	"orchara/platform/internal/uuid"
)

// requestFacts is the routing-relevant projection of one request. It never
// carries the message text (read separately from the sealed artifact).
type requestFacts struct {
	ID                string
	ConversationID    string
	OwnerID           string
	State             string
	Revision          int64
	InputArtifactID   string
	ArtifactIDs       []string
	SelectedVersionID string
	ControlCalls      int
}

// roundPlan is what one routing round decided before the control call.
type roundPlan struct {
	Attempt     int
	Prompt      []byte
	Message     string
	OwnerID     string
	ControlUsed int
}

// Advance runs exactly one routing round for one `route_request` background job.
// It is idempotent: a retried job first looks up an existing Run or pending
// interaction and returns the committed outcome instead of creating another.
//
// The round has three phases:
//
//	prepare  — lock the conversation/request, apply the retry guard, the explicit
//	           user selection and the control budget, compute and persist the
//	           authorized candidate snapshot, and resolve the control model;
//	control  — one governed model call outside the transaction, so the
//	           conversation row is not held for the provider's latency;
//	commit   — lock again, re-validate the model output against the snapshot, the
//	           current version/ACL state and the required inputs, and commit
//	           exactly one interaction, one Run or one typed failure.
//
// Every committed failure releases the slot through requests.AdvanceConversationQueueTx.
func (s *Service) Advance(ctx context.Context, jobID, requestID, jobOwnerID string) (*Outcome, error) {
	if !uuid.Valid(jobID) || !uuid.Valid(requestID) {
		return nil, errors.New("a background job id and a request id are required")
	}
	pre, err := s.loadRequest(ctx, s.DB, requestID)
	if err != nil {
		return nil, err
	}
	if !strings.EqualFold(pre.OwnerID, jobOwnerID) {
		return nil, ErrRequestNotFound
	}
	if pre.State != requests.StateRouting {
		return &Outcome{RequestID: requestID, ConversationID: pre.ConversationID,
			State: pre.State, Result: "noop"}, nil
	}
	message, err := s.loadMessage(ctx, pre.InputArtifactID)
	if err != nil {
		return nil, err
	}

	plan, done, err := s.prepare(ctx, pre, message)
	if err != nil {
		return nil, err
	}
	if done != nil {
		return done, nil
	}

	control, err := s.controlCall(ctx, jobID, pre.OwnerID, requestID, plan)
	if err != nil {
		if IsTransient(err) {
			return nil, err
		}
		// A deployment/provider refusal is deterministic: commit the typed
		// request failure instead of retrying forever. No Run and no interaction
		// is created and the slot is released.
		return s.commitFailure(ctx, pre, plan, CodeRouteModelUnavailable)
	}
	return s.commitDecision(ctx, pre, plan, control)
}

// loadRequest reads the routing-relevant request facts inside a queryer.
func (s *Service) loadRequest(ctx context.Context, q queryer, requestID string) (*requestFacts, error) {
	var (
		facts        requestFacts
		rawArtifacts string
		selected     sql.NullString
	)
	err := q.QueryRowContext(ctx, `
		SELECT r.id::text, r.conversation_id::text, r.owner_id::text, r.state, r.revision,
		       r.input_artifact_id::text, coalesce(array_to_string(r.artifact_ids, ','), ''),
		       r.selected_version_id::text, r.control_calls
		  FROM platform.requests AS r
		  JOIN platform.conversations AS c ON c.id = r.conversation_id
		 WHERE r.id = $1::uuid AND c.deleted_at IS NULL`, requestID,
	).Scan(&facts.ID, &facts.ConversationID, &facts.OwnerID, &facts.State, &facts.Revision,
		&facts.InputArtifactID, &rawArtifacts, &selected, &facts.ControlCalls)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrRequestNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the routed request: %w", err)
	}
	facts.ArtifactIDs = splitIDs(rawArtifacts)
	if selected.Valid {
		facts.SelectedVersionID = selected.String
	}
	return &facts, nil
}

// ErrRequestNotFound is the one routing refusal for a missing/foreign/tombstoned
// request. The internal endpoint answers the same as a stale job.
var ErrRequestNotFound = errors.New("that request does not exist")

func splitIDs(raw string) []string {
	if strings.TrimSpace(raw) == "" {
		return nil
	}
	parts := strings.Split(raw, ",")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		if uuid.Valid(part) {
			out = append(out, strings.ToLower(part))
		}
	}
	return out
}

// loadMessage reads the sealed request text. It is the model's task input and is
// never copied into a routing table.
func (s *Service) loadMessage(ctx context.Context, artifactID string) (string, error) {
	if s.Objects == nil {
		return "", errors.New("the object store is not configured")
	}
	if !uuid.Valid(artifactID) {
		return "", errors.New("the request has no sealed message artifact")
	}
	var (
		objectKey string
		state     string
		deleted   sql.NullTime
	)
	err := s.DB.QueryRowContext(ctx, `
		SELECT object_key, state, deleted_at FROM platform.artifacts WHERE id = $1::uuid`,
		artifactID,
	).Scan(&objectKey, &state, &deleted)
	if errors.Is(err, sql.ErrNoRows) {
		return "", ErrRequestNotFound
	}
	if err != nil {
		return "", fmt.Errorf("reading the sealed request message: %w", err)
	}
	if deleted.Valid || state != "ready" {
		return "", errors.New("the sealed request message is not readable")
	}
	reader, size, err := s.Objects.Open(objectKey)
	if err != nil {
		return "", fmt.Errorf("opening the sealed request message: %w", err)
	}
	defer func() { _ = reader.Close() }()
	if size <= 0 || size > 32001 {
		return "", errors.New("the sealed request message has an unexpected size")
	}
	data, err := io.ReadAll(io.LimitReader(reader, size))
	if err != nil {
		return "", fmt.Errorf("reading the sealed request message: %w", err)
	}
	return string(data), nil
}

// prepare is the first phase: every guard that can end the round without a model
// call runs here, then the candidate snapshot is persisted.
func (s *Service) prepare(
	ctx context.Context, pre *requestFacts, message string,
) (*roundPlan, *Outcome, error) {
	plan := &roundPlan{Message: message, OwnerID: pre.OwnerID}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, nil, fmt.Errorf("starting the routing preparation: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	if err := lockConversationTx(ctx, tx, pre.ConversationID); err != nil {
		return nil, nil, err
	}
	current, err := lockRequestTx(ctx, tx, pre.ID)
	if err != nil {
		return nil, nil, err
	}
	if current.State != requests.StateRouting {
		return nil, &Outcome{RequestID: pre.ID, ConversationID: pre.ConversationID,
			State: current.State, Result: "noop"}, nil
	}

	// The retry guard: a repeat of this job must find the original Run or pending
	// interaction, never create a second one (IMPLEMENTATION-DETAILS.md §43).
	if runID, err := runForRequestTx(ctx, tx, pre.ID); err != nil {
		return nil, nil, err
	} else if runID != "" {
		return nil, &Outcome{RequestID: pre.ID, ConversationID: pre.ConversationID,
			State: requests.StateDispatched, Result: "dispatched", RunID: runID}, nil
	}
	if pending, err := pendingInteractionTx(ctx, tx, pre.ID); err != nil {
		return nil, nil, err
	} else if pending != nil {
		return nil, &Outcome{RequestID: pre.ID, ConversationID: pre.ConversationID,
			State: current.State, Result: interactionResult(pending.Kind),
			InteractionID: pending.ID, CorrelationID: pending.CorrelationID}, nil
	}

	attempt, err := decisionAttemptTx(ctx, tx, pre.ID)
	if err != nil {
		return nil, nil, err
	}
	plan.Attempt = attempt

	// An explicit candidate reply resumes without another model call: the user
	// already chose, and the server only re-validates that choice.
	if current.SelectedVersionID != "" {
		outcome, err := s.explicitSelectionTx(ctx, tx, current, attempt)
		if err != nil {
			return nil, nil, err
		}
		if outcome != nil {
			if err := tx.Commit(); err != nil {
				return nil, nil, fmt.Errorf("committing the explicit routing round: %w", err)
			}
			return nil, outcome, nil
		}
	}

	calls, err := controlCallsTx(ctx, tx, pre.ID)
	if err != nil {
		return nil, nil, err
	}
	plan.ControlUsed = calls
	if calls >= ControlBudget {
		outcome, err := s.degradeTx(ctx, tx, current, attempt)
		if err != nil {
			return nil, nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, nil, fmt.Errorf("committing the degraded routing round: %w", err)
		}
		return nil, outcome, nil
	}

	candidates, err := candidatesTx(ctx, tx, current.OwnerID, message, MaxCandidates)
	if err != nil {
		return nil, nil, err
	}
	if err := replaceCandidatesTx(ctx, tx, current.ID, candidates); err != nil {
		return nil, nil, err
	}
	if len(candidates) == 0 {
		outcome, err := s.failTx(ctx, tx, current, attempt, DecisionNoMatch,
			CodeNoMatchingApplication, nil)
		if err != nil {
			return nil, nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, nil, fmt.Errorf("committing the empty-candidate round: %w", err)
		}
		return nil, outcome, nil
	}
	if s.Models == nil {
		outcome, err := s.failTx(ctx, tx, current, attempt, DecisionNoMatch,
			CodeRouteModelUnconfigured, nil)
		if err != nil {
			return nil, nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, nil, fmt.Errorf("committing the unconfigured-model round: %w", err)
		}
		return nil, outcome, nil
	}
	if _, err := s.Models.RouteControlCandidate(ctx); err != nil {
		outcome, err := s.failTx(ctx, tx, current, attempt, DecisionNoMatch,
			CodeRouteModelUnconfigured, nil)
		if err != nil {
			return nil, nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, nil, fmt.Errorf("committing the unconfigured-model round: %w", err)
		}
		return nil, outcome, nil
	}
	prompt, err := buildPrompt(message, candidates)
	if err != nil {
		return nil, nil, err
	}
	plan.Prompt = prompt
	if err := tx.Commit(); err != nil {
		return nil, nil, fmt.Errorf("committing the routing preparation: %w", err)
	}
	return plan, nil, nil
}

// lockConversationTx locks the conversation row first, the same lock order every
// T074 mutator uses.
func lockConversationTx(ctx context.Context, tx *sql.Tx, conversationID string) error {
	var id string
	err := tx.QueryRowContext(ctx, `
		SELECT id::text FROM platform.conversations
		 WHERE id = $1::uuid AND deleted_at IS NULL FOR UPDATE`, conversationID).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrRequestNotFound
	}
	if err != nil {
		return fmt.Errorf("locking the routed conversation: %w", err)
	}
	return nil
}

// lockRequestTx re-reads the request under the conversation lock.
func lockRequestTx(ctx context.Context, tx *sql.Tx, requestID string) (*requestFacts, error) {
	var (
		facts        requestFacts
		rawArtifacts string
		selected     sql.NullString
	)
	err := tx.QueryRowContext(ctx, `
		SELECT r.id::text, r.conversation_id::text, r.owner_id::text, r.state, r.revision,
		       r.input_artifact_id::text, coalesce(array_to_string(r.artifact_ids, ','), ''),
		       r.selected_version_id::text, r.control_calls
		  FROM platform.requests AS r
		 WHERE r.id = $1::uuid FOR UPDATE`, requestID,
	).Scan(&facts.ID, &facts.ConversationID, &facts.OwnerID, &facts.State, &facts.Revision,
		&facts.InputArtifactID, &rawArtifacts, &selected, &facts.ControlCalls)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrRequestNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("locking the routed request: %w", err)
	}
	facts.ArtifactIDs = splitIDs(rawArtifacts)
	if selected.Valid {
		facts.SelectedVersionID = selected.String
	}
	return &facts, nil
}

// replaceCandidatesTx writes the candidate snapshot for this round. One set per
// request: the previous round was answered before this round started.
func replaceCandidatesTx(
	ctx context.Context, tx *sql.Tx, requestID string, candidates []Candidate,
) error {
	if _, err := tx.ExecContext(ctx,
		`DELETE FROM platform.request_routing_candidates WHERE request_id = $1::uuid`,
		requestID); err != nil {
		return fmt.Errorf("replacing the candidate snapshot: %w", err)
	}
	for _, candidate := range candidates {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO platform.request_routing_candidates
			       (request_id, application_version_id, rank, application_name, version_no,
			        capability_card, card_sha256)
			VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb, $7)`,
			requestID, candidate.VersionID, candidate.Rank, candidate.ApplicationName,
			candidate.VersionNo, string(candidate.Card), jcs.SHA256Hex(candidate.Card),
		); err != nil {
			return fmt.Errorf("persisting the candidate snapshot: %w", err)
		}
	}
	return nil
}

// candidateSnapshotTx reads the persisted allowed set.
func candidateSnapshotTx(
	ctx context.Context, tx *sql.Tx, requestID string,
) ([]Candidate, error) {
	rows, err := tx.QueryContext(ctx, `
		SELECT application_version_id::text, application_name, version_no, capability_card::text, rank
		  FROM platform.request_routing_candidates
		 WHERE request_id = $1::uuid
		 ORDER BY rank, application_version_id`, requestID)
	if err != nil {
		return nil, fmt.Errorf("reading the candidate snapshot: %w", err)
	}
	defer rows.Close()
	candidates := make([]Candidate, 0, MaxCandidates)
	for rows.Next() {
		var (
			candidate Candidate
			cardText  string
		)
		if err := rows.Scan(&candidate.VersionID, &candidate.ApplicationName,
			&candidate.VersionNo, &cardText, &candidate.Rank); err != nil {
			return nil, fmt.Errorf("scanning the candidate snapshot: %w", err)
		}
		candidate.Card = json.RawMessage(cardText)
		candidates = append(candidates, candidate)
	}
	return candidates, rows.Err()
}

// explicitSelectionTx resumes a reply that named a candidate. The user's choice is
// honoured, but only after the candidate-snapshot membership, the current
// publication/ACL state and the required inputs are all re-checked. It returns nil
// when the request is no longer routing, which the caller treats as "continue".
func (s *Service) explicitSelectionTx(
	ctx context.Context, tx *sql.Tx, current *requestFacts, attempt int,
) (*Outcome, error) {
	candidates, err := candidateSnapshotTx(ctx, tx, current.ID)
	if err != nil {
		return nil, err
	}
	selected := findCandidate(candidates, current.SelectedVersionID)
	if selected == nil {
		// A selection outside the persisted snapshot is refused exactly like a
		// forged model suggestion: no Run, no interaction, typed failure.
		return s.failTx(ctx, tx, current, attempt, DecisionExplicit,
			CodeSelectedVersionInvalid, nil)
	}
	if _, err := s.Catalog.ResolveRunnableVersionTx(ctx, tx, current.OwnerID, selected.VersionID); err != nil {
		code, ok := versionRefusalCode(err)
		if !ok {
			return nil, err
		}
		return s.failTx(ctx, tx, current, attempt, DecisionExplicit, code, nil)
	}
	missing := missingInputs(cardRequiredInputs(selected.Card), current.ArtifactIDs)
	if len(missing) > 0 {
		proposal, err := inputProposalJSON(candidateProposal(*selected), missing, "")
		if err != nil {
			return nil, err
		}
		interactionID, correlationID, err := s.registerInteraction(
			ctx, tx, current.ID, current.OwnerID, "input",
			requests.StateAwaitingInput, proposal, InteractionTTL)
		if err != nil {
			return nil, err
		}
		if err := recordDecisionTx(ctx, tx, current.ID, attempt, DecisionExplicit,
			selected.VersionID, "", nil); err != nil {
			return nil, err
		}
		if err := audit.Record(ctx, tx, &current.OwnerID, "request.interaction_created",
			"request", &current.ID, map[string]any{
				"interaction_id": interactionID,
				"correlation_id": correlationID,
				"kind":           "input",
			}); err != nil {
			return nil, err
		}
		return &Outcome{RequestID: current.ID, ConversationID: current.ConversationID,
			State: requests.StateAwaitingInput, Result: "awaiting_input",
			InteractionID: interactionID, CorrelationID: correlationID}, nil
	}
	runID, err := s.Queue.DispatchVersionTx(ctx, tx, current.OwnerID, current.ID,
		current.ConversationID, selected.VersionID)
	if err != nil {
		code, ok := versionRefusalCode(err)
		if !ok {
			return nil, err
		}
		return s.failTx(ctx, tx, current, attempt, DecisionExplicit, code, nil)
	}
	if err := recordDecisionTx(ctx, tx, current.ID, attempt, DecisionExplicit,
		selected.VersionID, "", nil); err != nil {
		return nil, err
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.requests SET selected_version_id = NULL
		 WHERE id = $1::uuid`, current.ID); err != nil {
		return nil, fmt.Errorf("clearing the consumed candidate selection: %w", err)
	}
	return &Outcome{RequestID: current.ID, ConversationID: current.ConversationID,
		State: requests.StateDispatched, Result: "dispatched", RunID: runID}, nil
}

// degradeTx is the control-budget exit: no fourth model call. With at least two
// authorized candidates in the persisted snapshot the user gets the selection
// interaction; otherwise the request fails with the typed budget code.
func (s *Service) degradeTx(
	ctx context.Context, tx *sql.Tx, current *requestFacts, attempt int,
) (*Outcome, error) {
	candidates, err := candidateSnapshotTx(ctx, tx, current.ID)
	if err != nil {
		return nil, err
	}
	if len(candidates) >= 2 {
		proposal, err := selectionProposalJSON(candidates)
		if err != nil {
			return nil, err
		}
		interactionID, correlationID, err := s.registerInteraction(
			ctx, tx, current.ID, current.OwnerID, "selection",
			requests.StateAwaitingSelection, proposal, InteractionTTL)
		if err != nil {
			return nil, err
		}
		if err := recordDecisionTx(ctx, tx, current.ID, attempt, DecisionDegraded,
			"", "", nil); err != nil {
			return nil, err
		}
		if err := audit.Record(ctx, tx, &current.OwnerID, "request.interaction_created",
			"request", &current.ID, map[string]any{
				"interaction_id": interactionID,
				"correlation_id": correlationID,
				"kind":           "selection",
				"degraded":       true,
			}); err != nil {
			return nil, err
		}
		return &Outcome{RequestID: current.ID, ConversationID: current.ConversationID,
			State: requests.StateAwaitingSelection, Result: "awaiting_selection",
			InteractionID: interactionID, CorrelationID: correlationID}, nil
	}
	return s.failTx(ctx, tx, current, attempt, DecisionDegraded,
		CodeRouteBudgetExhausted, nil)
}

// failTx records the round and commits a typed request failure.
func (s *Service) failTx(
	ctx context.Context, tx *sql.Tx, current *requestFacts, attempt int,
	decision, code string, raw json.RawMessage,
) (*Outcome, error) {
	if err := recordDecisionTx(ctx, tx, current.ID, attempt, decision, "", "", raw); err != nil {
		return nil, err
	}
	return s.finishFailureTx(ctx, tx, current, code)
}

// finishFailureTx is the shared failure commit: state, error_code, audit and the
// queue advance. It is also used after a deterministic model refusal.
func (s *Service) finishFailureTx(
	ctx context.Context, tx *sql.Tx, current *requestFacts, code string,
) (*Outcome, error) {
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.requests
		   SET state = 'failed', error_code = $2, revision = revision + 1,
		       selected_version_id = NULL, updated_at = now()
		 WHERE id = $1::uuid AND state = 'routing'`, current.ID, code)
	if err != nil {
		return nil, fmt.Errorf("failing the unroutable request: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("reading the routing failure transition: %w", err)
	}
	if affected != 1 {
		return nil, errors.New("the request left routing before its failure could be committed")
	}
	if err := audit.Record(ctx, tx, &current.OwnerID, "request.routing_failed", "request",
		&current.ID, map[string]any{"error_code": code}); err != nil {
		return nil, err
	}
	// The request was the slot holder. Promote the next queued request in the same
	// transaction, exactly like a cancel does, so a routing failure never strands a
	// conversation.
	if _, err := s.Queue.AdvanceConversationQueueTx(ctx, tx, current.ConversationID); err != nil {
		return nil, err
	}
	return &Outcome{RequestID: current.ID, ConversationID: current.ConversationID,
		State: requests.StateFailed, Result: "failed", ErrorCode: code}, nil
}

// commitFailure commits a typed failure after a deterministic model refusal.
func (s *Service) commitFailure(
	ctx context.Context, pre *requestFacts, plan *roundPlan, code string,
) (*Outcome, error) {
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the routing failure transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	if err := lockConversationTx(ctx, tx, pre.ConversationID); err != nil {
		return nil, err
	}
	current, err := lockRequestTx(ctx, tx, pre.ID)
	if err != nil {
		return nil, err
	}
	if current.State != requests.StateRouting {
		if err := tx.Commit(); err != nil {
			return nil, err
		}
		return &Outcome{RequestID: pre.ID, ConversationID: pre.ConversationID,
			State: current.State, Result: "noop"}, nil
	}
	if err := recordDecisionTx(ctx, tx, current.ID, plan.Attempt, DecisionNoMatch,
		"", "", nil); err != nil {
		return nil, err
	}
	if err := bumpControlCallsTx(ctx, tx, current.ID); err != nil {
		return nil, err
	}
	outcome, err := s.finishFailureTx(ctx, tx, current, code)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the routing failure: %w", err)
	}
	return outcome, nil
}

// controlCall performs the one governed model call of the round. User text is
// handed only to the platform model proxy; the ledger row is owned by the route
// job and no Run exists yet.
func (s *Service) controlCall(
	ctx context.Context, jobID, ownerID, requestID string, plan *roundPlan,
) (*modelproxy.InvokeResult, error) {
	if s.Models == nil {
		return nil, modelproxy.ErrNotConfigured
	}
	candidate, err := s.Models.RouteControlCandidate(ctx)
	if err != nil {
		return nil, err
	}
	result, err := s.Models.Invoke(ctx, modelproxy.InvokeInput{
		JobID:         jobID,
		OwnerID:       ownerID,
		Candidate:     candidate,
		LogicalKey:    roundLabel(requestID, plan.Attempt),
		Params:        map[string]any{"task": "route"},
		Fixture:       plan.Prompt,
		FixtureSHA256: jcs.SHA256Hex(plan.Prompt),
	})
	if err != nil {
		if modelproxy.IsTransient(err) {
			return nil, &TransientError{Err: err}
		}
		return nil, err
	}
	return &result, nil
}

// commitDecision is the third phase: re-lock, re-validate the model output and
// commit exactly one interaction, one Run or one typed failure.
func (s *Service) commitDecision(
	ctx context.Context, pre *requestFacts, plan *roundPlan, control *modelproxy.InvokeResult,
) (*Outcome, error) {
	raw := json.RawMessage(mustJSON(control.Data))
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the routing decision: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	if err := lockConversationTx(ctx, tx, pre.ConversationID); err != nil {
		return nil, err
	}
	current, err := lockRequestTx(ctx, tx, pre.ID)
	if err != nil {
		return nil, err
	}
	if current.State != requests.StateRouting {
		if err := tx.Commit(); err != nil {
			return nil, err
		}
		return &Outcome{RequestID: pre.ID, ConversationID: pre.ConversationID,
			State: current.State, Result: "noop"}, nil
	}
	if runID, err := runForRequestTx(ctx, tx, pre.ID); err != nil {
		return nil, err
	} else if runID != "" {
		if err := tx.Commit(); err != nil {
			return nil, err
		}
		return &Outcome{RequestID: pre.ID, ConversationID: pre.ConversationID,
			State: requests.StateDispatched, Result: "dispatched", RunID: runID}, nil
	}
	candidates, err := candidateSnapshotTx(ctx, tx, pre.ID)
	if err != nil {
		return nil, err
	}
	if err := bumpControlCallsTx(ctx, tx, pre.ID); err != nil {
		return nil, err
	}
	decision, decisionErr := parseDecision(control.Data)
	if decisionErr != nil {
		if err := recordDecisionTx(ctx, tx, current.ID, plan.Attempt, DecisionNoMatch,
			"", control.CallID, raw); err != nil {
			return nil, err
		}
		outcome, err := s.finishFailureTx(ctx, tx, current, CodeRouteOutputInvalid)
		if err != nil {
			return nil, err
		}
		if err := tx.Commit(); err != nil {
			return nil, err
		}
		return outcome, nil
	}
	// AC1: a suggestion outside the persisted authorized set is refused, no Run.
	if decision.Decision == DecisionSelect {
		if findCandidate(candidates, decision.CandidateVersionID) == nil {
			if err := recordDecisionTx(ctx, tx, current.ID, plan.Attempt, decision.Decision,
				"", control.CallID, raw); err != nil {
				return nil, err
			}
			outcome, err := s.finishFailureTx(ctx, tx, current, CodeRouteCandidateInvalid)
			if err != nil {
				return nil, err
			}
			if err := tx.Commit(); err != nil {
				return nil, err
			}
			return outcome, nil
		}
	}
	outcome, err := s.applyPolicyTx(ctx, tx, current, plan.Attempt, decision,
		candidates, control.CallID, raw)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the routing decision: %w", err)
	}
	return outcome, nil
}

// applyPolicyTx is the server's own decision rule. The model's suggestion is
// advisory: the server owns the input check and the "never silently pick the
// nearest of several candidates" rule.
func (s *Service) applyPolicyTx(
	ctx context.Context, tx *sql.Tx, current *requestFacts, attempt int,
	decision *Decision, candidates []Candidate, modelCallID string, raw json.RawMessage,
) (*Outcome, error) {
	if decision.Decision == DecisionNoMatch || len(candidates) == 0 {
		return s.failTx(ctx, tx, current, attempt, DecisionNoMatch,
			CodeNoMatchingApplication, raw)
	}
	// AC7: two or more plausible candidates always go to an explicit selection
	// interaction with every candidate visible. The model's pick is never applied.
	if len(candidates) > 1 {
		proposal, err := selectionProposalJSON(candidates)
		if err != nil {
			return nil, err
		}
		interactionID, correlationID, err := s.registerInteraction(
			ctx, tx, current.ID, current.OwnerID, "selection",
			requests.StateAwaitingSelection, proposal, InteractionTTL)
		if err != nil {
			return nil, err
		}
		if err := recordDecisionTx(ctx, tx, current.ID, attempt,
			normaliseDecision(decision.Decision), "", modelCallID, raw); err != nil {
			return nil, err
		}
		if err := audit.Record(ctx, tx, &current.OwnerID, "request.interaction_created",
			"request", &current.ID, map[string]any{
				"interaction_id": interactionID,
				"correlation_id": correlationID,
				"kind":           "selection",
			}); err != nil {
			return nil, err
		}
		return &Outcome{RequestID: current.ID, ConversationID: current.ConversationID,
			State: requests.StateAwaitingSelection, Result: "awaiting_selection",
			InteractionID: interactionID, CorrelationID: correlationID}, nil
	}

	// Exactly one authorized candidate. The model may still ask a clarifying
	// question; the server may also find a required input missing.
	chosen := candidates[0]
	missing := missingInputs(cardRequiredInputs(chosen.Card), current.ArtifactIDs)
	question := ""
	if decision.Decision == DecisionClarify {
		question = decision.Question
		if len(decision.MissingFields) > 0 {
			missing = append([]string(nil), decision.MissingFields...)
		}
	}
	if len(missing) > 0 || decision.Decision == DecisionClarify {
		proposal, err := inputProposalJSON(candidateProposal(chosen), missing, question)
		if err != nil {
			return nil, err
		}
		interactionID, correlationID, err := s.registerInteraction(
			ctx, tx, current.ID, current.OwnerID, "input",
			requests.StateAwaitingInput, proposal, InteractionTTL)
		if err != nil {
			return nil, err
		}
		if err := recordDecisionTx(ctx, tx, current.ID, attempt,
			DecisionClarify, chosen.VersionID, modelCallID, raw); err != nil {
			return nil, err
		}
		if err := audit.Record(ctx, tx, &current.OwnerID, "request.interaction_created",
			"request", &current.ID, map[string]any{
				"interaction_id": interactionID,
				"correlation_id": correlationID,
				"kind":           "input",
			}); err != nil {
			return nil, err
		}
		return &Outcome{RequestID: current.ID, ConversationID: current.ConversationID,
			State: requests.StateAwaitingInput, Result: "awaiting_input",
			InteractionID: interactionID, CorrelationID: correlationID}, nil
	}

	// The one legitimate automatic selection: exactly one authorized candidate and
	// every required input present. Re-check the version state in this transaction
	// so a revocation that committed first is refused.
	if _, err := s.Catalog.ResolveRunnableVersionTx(ctx, tx, current.OwnerID, chosen.VersionID); err != nil {
		code, ok := versionRefusalCode(err)
		if !ok {
			return nil, err
		}
		return s.failTx(ctx, tx, current, attempt, normaliseDecision(decision.Decision), code, raw)
	}
	runID, err := s.Queue.DispatchVersionTx(ctx, tx, current.OwnerID, current.ID,
		current.ConversationID, chosen.VersionID)
	if err != nil {
		code, ok := versionRefusalCode(err)
		if !ok {
			return nil, err
		}
		return s.failTx(ctx, tx, current, attempt, normaliseDecision(decision.Decision), code, raw)
	}
	if err := recordDecisionTx(ctx, tx, current.ID, attempt,
		normaliseDecision(decision.Decision), chosen.VersionID, modelCallID, raw); err != nil {
		return nil, err
	}
	return &Outcome{RequestID: current.ID, ConversationID: current.ConversationID,
		State: requests.StateDispatched, Result: "dispatched", RunID: runID}, nil
}

// Decision is the validated shape of a control-model answer.
type Decision struct {
	Decision           string
	CandidateVersionID string
	Question           string
	MissingFields      []string
	Reason             string
}

// parseDecision reads only the three documented decision shapes from the model's
// sanitized data. Anything else is a typed route_output_invalid.
func parseDecision(data map[string]any) (*Decision, error) {
	if data == nil {
		return nil, errors.New("the control model returned no decision object")
	}
	decision := &Decision{}
	value, ok := data["decision"].(string)
	if !ok || strings.TrimSpace(value) == "" {
		return nil, errors.New("the control model returned no decision class")
	}
	decision.Decision = strings.TrimSpace(value)
	switch decision.Decision {
	case DecisionSelect:
		id, ok := data["candidate_version_id"].(string)
		if !ok || !uuid.Valid(id) {
			return nil, errors.New("a select decision needs a valid candidate_version_id")
		}
		decision.CandidateVersionID = strings.ToLower(id)
	case DecisionClarify:
		question, _ := data["question"].(string)
		if strings.TrimSpace(question) == "" {
			return nil, errors.New("a clarify decision needs a non-empty question")
		}
		decision.Question = strings.TrimSpace(question)
		if raw, present := data["missing_fields"]; present {
			list, ok := raw.([]any)
			if !ok {
				return nil, errors.New("missing_fields must be an array of strings")
			}
			for _, item := range list {
				text, ok := item.(string)
				if !ok || strings.TrimSpace(text) == "" {
					return nil, errors.New("missing_fields must be an array of strings")
				}
				decision.MissingFields = append(decision.MissingFields, strings.TrimSpace(text))
			}
		}
	case DecisionNoMatch:
		reason, _ := data["reason"].(string)
		if strings.TrimSpace(reason) == "" {
			return nil, errors.New("a no_match decision needs a reason")
		}
		decision.Reason = strings.TrimSpace(reason)
	default:
		return nil, fmt.Errorf("unknown decision class %q", decision.Decision)
	}
	return decision, nil
}

func normaliseDecision(value string) string {
	switch value {
	case DecisionSelect, DecisionClarify, DecisionNoMatch:
		return value
	default:
		return DecisionNoMatch
	}
}

// recordDecisionTx inserts one round row. Re-inserting the same attempt is a
// retry, not a second decision: the row is overwritten with the same content.
func recordDecisionTx(
	ctx context.Context, tx *sql.Tx, requestID string, attempt int, decision string,
	selectedVersionID string, modelCallID string, raw json.RawMessage,
) error {
	if attempt < 1 {
		return errors.New("a routing attempt must be positive")
	}
	var call any
	if uuid.Valid(modelCallID) {
		call = modelCallID
	}
	var rawValue any
	if len(raw) > 0 {
		rawValue = string(raw)
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.request_routing_decisions
		       (request_id, attempt, decision, selected_version_id, model_call_id, raw_decision)
		VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb)
		ON CONFLICT (request_id, attempt) DO UPDATE
		   SET decision = EXCLUDED.decision,
		       selected_version_id = EXCLUDED.selected_version_id,
		       model_call_id = EXCLUDED.model_call_id,
		       raw_decision = EXCLUDED.raw_decision,
		       decided_at = now()`,
		requestID, attempt, decision, nullableUUID(selectedVersionID), call, rawValue,
	); err != nil {
		return fmt.Errorf("recording the routing decision: %w", err)
	}
	return nil
}

// bumpControlCallsTx mirrors the ledger count on the request for observability.
func bumpControlCallsTx(ctx context.Context, tx *sql.Tx, requestID string) error {
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.requests
		   SET control_calls = least(control_calls + 1, 3), updated_at = now()
		 WHERE id = $1::uuid`, requestID); err != nil {
		return fmt.Errorf("recording the control-call mirror: %w", err)
	}
	return nil
}

func nullableUUID(value string) any {
	if !uuid.Valid(value) {
		return nil
	}
	return strings.ToLower(value)
}

func mustJSON(data map[string]any) []byte {
	raw, err := json.Marshal(data)
	if err != nil {
		return []byte("{}")
	}
	return raw
}

func findCandidate(candidates []Candidate, versionID string) *Candidate {
	for index := range candidates {
		if strings.EqualFold(candidates[index].VersionID, versionID) {
			return &candidates[index]
		}
	}
	return nil
}

func interactionResult(kind string) string {
	if kind == "selection" {
		return "awaiting_selection"
	}
	return "awaiting_input"
}

// versionRefusalCode maps the catalog runnable errors onto stable request codes.
func versionRefusalCode(err error) (string, bool) {
	switch {
	case errors.Is(err, catalog.ErrVersionRevoked):
		return "version_revoked", true
	case errors.Is(err, catalog.ErrVersionArchived):
		return "version_archived", true
	case errors.Is(err, catalog.ErrNotVisible):
		return "version_unavailable", true
	default:
		var located *requests.Error
		if errors.As(err, &located) {
			return located.Code, true
		}
		return "", false
	}
}

// proposalCandidate is the six-field capability projection shown in an
// interaction. It has no score field by construction.
type proposalCandidate struct {
	Rank                 int      `json:"rank"`
	ApplicationVersionID string   `json:"application_version_id"`
	ApplicationID        string   `json:"application_id"`
	ApplicationName      string   `json:"application_name"`
	VersionNo            int64    `json:"version_no"`
	Purpose              string   `json:"purpose"`
	Includes             []string `json:"includes"`
	Excludes             []string `json:"excludes"`
	RequiredInputs       []string `json:"required_inputs"`
	ExpectedOutputs      []string `json:"expected_outputs"`
	Examples             []string `json:"examples"`
}

func candidateProposal(candidate Candidate) proposalCandidate {
	var card struct {
		Purpose         string   `json:"purpose"`
		Includes        []string `json:"includes"`
		Excludes        []string `json:"excludes"`
		RequiredInputs  []string `json:"required_inputs"`
		ExpectedOutputs []string `json:"expected_outputs"`
		Examples        []string `json:"examples"`
	}
	_ = json.Unmarshal(candidate.Card, &card)
	return proposalCandidate{
		Rank:                 candidate.Rank,
		ApplicationVersionID: candidate.VersionID,
		ApplicationID:        candidate.ApplicationID,
		ApplicationName:      candidate.ApplicationName,
		VersionNo:            candidate.VersionNo,
		Purpose:              card.Purpose,
		Includes:             card.Includes,
		Excludes:             card.Excludes,
		RequiredInputs:       card.RequiredInputs,
		ExpectedOutputs:      card.ExpectedOutputs,
		Examples:             card.Examples,
	}
}

type selectionProposal struct {
	Kind       string              `json:"kind"`
	Question   string              `json:"question"`
	Candidates []proposalCandidate `json:"candidates"`
}

type inputProposal struct {
	Kind          string             `json:"kind"`
	Question      string             `json:"question"`
	MissingFields []string           `json:"missing_fields"`
	Candidate     *proposalCandidate `json:"candidate,omitempty"`
}

const selectionQuestion = "有多个应用可能适合这个需求，请选择一个继续。"

func selectionProposalJSON(candidates []Candidate) ([]byte, error) {
	proposal := selectionProposal{Kind: "selection", Question: selectionQuestion,
		Candidates: make([]proposalCandidate, 0, len(candidates))}
	for _, candidate := range candidates {
		proposal.Candidates = append(proposal.Candidates, candidateProposal(candidate))
	}
	raw, err := json.Marshal(proposal)
	if err != nil {
		return nil, fmt.Errorf("building the selection proposal: %w", err)
	}
	return raw, nil
}

func inputProposalJSON(
	candidate proposalCandidate, missing []string, question string,
) ([]byte, error) {
	if strings.TrimSpace(question) == "" {
		if len(missing) > 0 {
			question = "该应用需要补充以下输入：" + strings.Join(missing, "、")
		} else {
			question = "请补充这个需求还缺少的信息。"
		}
	}
	proposal := inputProposal{Kind: "input", Question: question,
		MissingFields: missing, Candidate: &candidate}
	raw, err := json.Marshal(proposal)
	if err != nil {
		return nil, fmt.Errorf("building the input proposal: %w", err)
	}
	return raw, nil
}
