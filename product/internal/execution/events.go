package execution

// F03/T032: the durable event timeline.
//
// Two kinds of writer, one primitive:
//
//   - controllers append a `run.status` event in the *same* transaction as the
//     Run transition it describes (debug creation, claim, result proposal,
//     finalizer, recovery_pending, requeue);
//   - a worker submits `node.status` observations over mTLS, authenticated by a
//     live attempt token bound to the Run.
//
// The seq allocator is deliberately trivial because it is also the invariant:
// the caller already holds (or the helper takes) the Run row lock, reads
// `next_event_seq`, inserts at that seq with the *current* revision, then bumps
// the counter in the same transaction. A duplicate event_id is compared against
// the stored type+payload (JSONB equality) and never consumes a seq. The worker's
// `local_sequence` is diagnostic only: it is logged when it gaps or repeats, and
// it is never the display order (the request array order is the committed order).

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgconn"

	"orchara/platform/internal/uuid"
)

// Event schema/protocol constants fixed by contracts/event.schema.json.
const (
	// EventSchemaVersion is the envelope's schema_version.
	EventSchemaVersion = "1.0"
	// EventTypeRunStatus is the controller-derived Run state projection.
	EventTypeRunStatus = "run.status"
	// EventTypeNodeStatus is the only event type a worker may submit.
	EventTypeNodeStatus = "node.status"

	// maxWorkerEvents is the contract's batch maximum (minItems 1).
	maxWorkerEvents = 100
	// maxEventNodeID bounds the node identity in a node.status payload.
	maxEventNodeID = 256
	// maxEventState bounds the node state token. The contract's node status
	// vocabulary is enforced by the worker/supervisor, not invented here.
	maxEventState = 128
)

// Event is one durable event envelope exactly as contracts/event.schema.json
// defines it. Payload stays raw so the stored object is returned verbatim,
// never re-encoded through a map (key order is not semantic, bytes are).
type Event struct {
	SchemaVersion string          `json:"schema_version"`
	EventID       string          `json:"event_id"`
	RunID         string          `json:"run_id"`
	Seq           int64           `json:"seq"`
	Revision      int64           `json:"revision"`
	Type          string          `json:"type"`
	CreatedAt     string          `json:"created_at"`
	Payload       json.RawMessage `json:"payload"`
}

// RunStatusPayload is the contract-legal `run.status` payload: state plus an
// optional error_code, nothing else. The Run's phase is deliberately not here
// (the schema has no phase field); the owner's Run read is the phase authority.
type RunStatusPayload struct {
	State     string  `json:"state"`
	ErrorCode *string `json:"error_code,omitempty"`
}

// WorkerEvent is one normalised entry of submitWorkerEvents. Payload has already
// been validated against the node.status allowlist and re-encoded canonically.
type WorkerEvent struct {
	EventID       string
	LocalSequence int64
	Type          string
	Payload       []byte
}

// WorkerEventsRequest is the normalised submitWorkerEvents body.
type WorkerEventsRequest struct {
	AttemptToken string
	Events       []WorkerEvent
}

// WorkerEventsOutcome reports how many events were newly sequenced; duplicates
// are counted but never consume a seq.
type WorkerEventsOutcome struct {
	RunID      string
	Sequenced  int
	Duplicates int
}

// EventReplayFacts answers the SSE handler's retention/terminal question without
// reading the event bodies.
type EventReplayFacts struct {
	RunState string
	MaxSeq   int64
	HasMore  bool
	Expired  bool
}

// ---------------------------------------------------------------------------
// controller events: the one same-transaction helper
// ---------------------------------------------------------------------------

// AppendRunStatusTx appends exactly one `run.status` event for the Run's current
// committed state, revision and error_code, in the caller's transaction.
//
// The helper locks the Run row, reads next_event_seq/revision/state/error_code,
// inserts the event with a fresh uuid and bumps the counter. It must be called
// after the transition (so the payload and revision are post-transition) and
// inside the same transaction as that transition: a failure here rolls the
// business change back with the missing event instead of committing a state the
// timeline cannot explain.
//
// It is exported because catalog's debug-run creation transaction writes the
// first (`queued`) event with the same primitive; there is no second SQL path.
func AppendRunStatusTx(ctx context.Context, tx *sql.Tx, runID string) (*Event, error) {
	facts, err := lockEventRunTx(ctx, tx, runID)
	if err != nil {
		return nil, err
	}
	payload := RunStatusPayload{State: facts.State}
	if facts.ErrorCode.Valid {
		code := facts.ErrorCode.String
		payload.ErrorCode = &code
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("encoding the run.status payload: %w", err)
	}
	return insertNextEventTx(ctx, tx, runID, "", facts, EventTypeRunStatus, raw)
}

// eventRunFacts is the locked Run state the event allocator reads.
type eventRunFacts struct {
	State     string
	ErrorCode sql.NullString
	Revision  int64
	NextSeq   int64
}

// lockEventRunTx takes the Run row lock and reads the event allocation facts.
// The lock is what makes seq allocation and event_id dedupe serial per Run.
func lockEventRunTx(ctx context.Context, tx *sql.Tx, runID string) (*eventRunFacts, error) {
	var facts eventRunFacts
	err := tx.QueryRowContext(ctx, `
		SELECT state, error_code, revision, next_event_seq
		  FROM platform.runs
		 WHERE id = $1::uuid
		   FOR UPDATE`,
		runID,
	).Scan(&facts.State, &facts.ErrorCode, &facts.Revision, &facts.NextSeq)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrRunNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the run event facts: %w", err)
	}
	return &facts, nil
}

// insertNextEventTx writes the event at facts.NextSeq and increments the Run's
// counter. Controller events mint a fresh uuid (eventID == ""); a worker event
// keeps the client's id because that id is its idempotency identity.
func insertNextEventTx(
	ctx context.Context,
	tx *sql.Tx,
	runID, eventID string,
	facts *eventRunFacts,
	eventType string,
	payload []byte,
) (*Event, error) {
	if eventID == "" {
		eventID = uuid.NewString()
	}
	event := &Event{
		SchemaVersion: EventSchemaVersion,
		EventID:       eventID,
		RunID:         runID,
		Seq:           facts.NextSeq,
		Revision:      facts.Revision,
		Type:          eventType,
		Payload:       json.RawMessage(payload),
	}
	var createdAt time.Time
	err := tx.QueryRowContext(ctx, `
		INSERT INTO platform.run_events (run_id, seq, event_id, type, revision, payload)
		VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6::jsonb)
		RETURNING created_at`,
		runID, event.Seq, event.EventID, eventType, event.Revision, payload,
	).Scan(&createdAt)
	if err != nil {
		if isUniqueViolation(err, "run_events_event_id_key") {
			// A concurrent writer used the same event_id for different content;
			// the dedupe read below happened before it committed.
			return nil, ErrEventConflict
		}
		return nil, fmt.Errorf("inserting the run event: %w", err)
	}
	event.CreatedAt = createdAt.UTC().Format(time.RFC3339Nano)
	result, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET next_event_seq = next_event_seq + 1
		 WHERE id = $1::uuid AND next_event_seq = $2`,
		runID, facts.NextSeq,
	)
	if err != nil {
		return nil, fmt.Errorf("incrementing the run event counter: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("incrementing the run event counter: %w", err)
	}
	if affected != 1 {
		// The run row is locked for this transaction, so a mismatch means an
		// invariant violation, not a race; refuse instead of reusing a seq.
		return nil, fmt.Errorf(
			"incrementing the run event counter: expected one row, got %d", affected,
		)
	}
	return event, nil
}

func isUniqueViolation(err error, constraint string) bool {
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != "23505" {
		return false
	}
	return constraint == "" || pgErr.ConstraintName == constraint
}

// ---------------------------------------------------------------------------
// worker events: submitWorkerEvents
// ---------------------------------------------------------------------------

// SubmitWorkerEvents accepts one bounded batch of worker observations.
//
// Order inside the transaction:
//
//  1. lock the Run: terminal is 409 run_terminal (late observations never
//     revive), non-executing is 409 run_state_conflict;
//  2. lock the live attempt bound to this Run (job.current_attempt_token and an
//     unexpired, unexited, unrevoked attempt): anything else is 409
//     stale_attempt;
//  3. per event in request order: existing event_id + same run/type/payload is
//     the idempotent no-op; any different content is 409 event_conflict; a new
//     event is inserted at next_event_seq with the Run's current revision.
//
// The batch is all-or-nothing: one conflict/invalid event and nothing new is
// sequenced.
func (s *Service) SubmitWorkerEvents(
	ctx context.Context, runID string, request WorkerEventsRequest,
) (*WorkerEventsOutcome, error) {
	runID = strings.TrimSpace(runID)
	if !uuid.Valid(runID) {
		return nil, ErrRunNotFound
	}
	normalized, err := validateWorkerEvents(request)
	if err != nil {
		return nil, err
	}

	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the worker-events transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	run, err := lockRunTx(ctx, tx, runID)
	if err != nil {
		return nil, err
	}
	switch run.State {
	case "succeeded", "failed", "cancelled":
		return nil, ErrRunTerminal
	case "running":
		// The only state in which a worker can still own a live attempt.
	default:
		return nil, ErrRunStateConflict
	}
	if err := lockLiveAttemptForEventsTx(ctx, tx, runID, normalized.AttemptToken); err != nil {
		return nil, err
	}

	outcome := &WorkerEventsOutcome{RunID: runID}
	accepted := make([]WorkerEvent, 0, len(normalized.Events))
	for _, event := range normalized.Events {
		exists, same, err := matchStoredEventTx(ctx, tx, event, runID)
		if err != nil {
			return nil, err
		}
		if exists {
			if !same {
				return nil, ErrEventConflict
			}
			outcome.Duplicates++
			continue
		}
		facts, err := lockEventRunTx(ctx, tx, runID)
		if err != nil {
			return nil, err
		}
		if _, err := insertNextEventTx(
			ctx, tx, runID, event.EventID, facts, EventTypeNodeStatus, event.Payload,
		); err != nil {
			if errors.Is(err, ErrEventConflict) {
				return nil, err
			}
			return nil, err
		}
		accepted = append(accepted, event)
		outcome.Sequenced++
	}
	s.logLocalSequenceDiagnostics(runID, accepted)

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the worker-events transaction: %w", err)
	}
	return outcome, nil
}

// lockLiveAttemptForEventsTx proves the token is this Run's current, live
// attempt. A foreign Run's token, a superseded attempt, an exited/revoked one,
// an expired lease or a job that no longer names the token are all the same
// stale_attempt answer.
func lockLiveAttemptForEventsTx(
	ctx context.Context, tx *sql.Tx, runID, token string,
) error {
	var found string
	err := tx.QueryRowContext(ctx, `
		SELECT a.token::text
		  FROM platform.run_attempts AS a
		  JOIN platform.jobs AS j ON j.id = a.job_id AND j.run_id = a.run_id
		 WHERE a.token = $1::uuid
		   AND a.run_id = $2::uuid
		   AND j.current_attempt_token = a.token
		   AND j.state = 'leased'
		   AND a.exited_at IS NULL
		   AND a.revoked_at IS NULL
		   AND a.lease_deadline > now()
		   FOR UPDATE OF a, j`,
		token, runID,
	).Scan(&found)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrStaleAttempt
	}
	if err != nil {
		return fmt.Errorf("reading the live attempt for events: %w", err)
	}
	return nil
}

// matchStoredEventTx answers the event_id idempotency question. It returns
// exists=false for a new id. For an existing id, `same` is true only when the
// row belongs to this Run, has the same type and JSONB-equal payload.
func matchStoredEventTx(
	ctx context.Context, tx *sql.Tx, event WorkerEvent, runID string,
) (exists bool, same bool, err error) {
	var (
		storedRunID   string
		storedType    string
		storedPayload []byte
	)
	err = tx.QueryRowContext(ctx, `
		SELECT run_id::text, type, payload
		  FROM platform.run_events
		 WHERE event_id = $1::uuid`,
		event.EventID,
	).Scan(&storedRunID, &storedType, &storedPayload)
	if errors.Is(err, sql.ErrNoRows) {
		return false, false, nil
	}
	if err != nil {
		return false, false, fmt.Errorf("reading the stored event id: %w", err)
	}
	return true, storedRunID == runID && storedType == event.Type &&
		sameProposal(storedPayload, event.Payload), nil
}

// logLocalSequenceDiagnostics records gaps/repeats in the worker's diagnostic
// counter. It deliberately logs only the Run id and the sequence numbers: the
// attempt token is a credential and never appears in logs.
func (s *Service) logLocalSequenceDiagnostics(runID string, events []WorkerEvent) {
	if s.Logger == nil || len(events) < 2 {
		return
	}
	sorted := append([]WorkerEvent(nil), events...)
	sort.SliceStable(sorted, func(left, right int) bool {
		return sorted[left].LocalSequence < sorted[right].LocalSequence
	})
	previous := sorted[0].LocalSequence
	for _, event := range sorted[1:] {
		switch {
		case event.LocalSequence == previous:
			s.Logger.Printf(
				"worker events: run=%s duplicate local_sequence=%d (diagnostic only)",
				runID, event.LocalSequence,
			)
		case event.LocalSequence > previous+1:
			s.Logger.Printf(
				"worker events: run=%s local_sequence gap=%d..%d (diagnostic only)",
				runID, previous+1, event.LocalSequence-1,
			)
		}
		previous = event.LocalSequence
	}
}

// validateWorkerEvents enforces the contract body before any row is touched:
// 1..100 events, each {event_id uuid, local_sequence >=1, type node.status,
// payload exactly {node_id, state, attempt_no}}.
func validateWorkerEvents(request WorkerEventsRequest) (WorkerEventsRequest, error) {
	normalized := WorkerEventsRequest{
		AttemptToken: strings.TrimSpace(request.AttemptToken),
	}
	if !uuid.Valid(normalized.AttemptToken) {
		return WorkerEventsRequest{}, &ValidationError{
			Field: "attempt_token", Message: "attempt_token must be a UUID",
		}
	}
	if len(request.Events) < 1 {
		return WorkerEventsRequest{}, &ValidationError{
			Field: "events", Message: "events must contain between 1 and 100 entries",
		}
	}
	if len(request.Events) > maxWorkerEvents {
		return WorkerEventsRequest{}, &ValidationError{
			Field: "events", Message: "events must contain between 1 and 100 entries",
		}
	}
	normalized.Events = make([]WorkerEvent, 0, len(request.Events))
	for index, event := range request.Events {
		entry := WorkerEvent{
			EventID:       strings.TrimSpace(event.EventID),
			LocalSequence: event.LocalSequence,
			Type:          strings.TrimSpace(event.Type),
		}
		if !uuid.Valid(entry.EventID) {
			return WorkerEventsRequest{}, &ValidationError{
				Field:   fmt.Sprintf("events[%d].event_id", index),
				Message: "event_id must be a UUID",
			}
		}
		if entry.LocalSequence < 1 {
			return WorkerEventsRequest{}, &ValidationError{
				Field:   fmt.Sprintf("events[%d].local_sequence", index),
				Message: "local_sequence must be at least 1",
			}
		}
		// Worker observations only: controllers derive every other type.
		if entry.Type != EventTypeNodeStatus {
			return WorkerEventsRequest{}, &ValidationError{
				Field:   fmt.Sprintf("events[%d].type", index),
				Message: "a worker may submit only node.status events",
			}
		}
		payload, err := validateNodeStatusPayload(index, event.Payload)
		if err != nil {
			return WorkerEventsRequest{}, err
		}
		entry.Payload = payload
		normalized.Events = append(normalized.Events, entry)
	}
	return normalized, nil
}

// nodeStatusPayload is the exact node.status allowlist from event.schema.json.
type nodeStatusPayload struct {
	NodeID    string `json:"node_id"`
	State     string `json:"state"`
	AttemptNo int64  `json:"attempt_no"`
}

// validateNodeStatusPayload enforces the exact three-key payload. Unknown keys
// (content/body/delta/text/...) are refused with a located field: a body must
// never ride a metadata event.
func validateNodeStatusPayload(index int, raw []byte) ([]byte, error) {
	base := fmt.Sprintf("events[%d].payload", index)
	if len(raw) == 0 {
		return nil, &ValidationError{Field: base, Message: "payload must be an object"}
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil || fields == nil {
		return nil, &ValidationError{Field: base, Message: "payload must be an object"}
	}
	allowed := map[string]bool{"node_id": true, "state": true, "attempt_no": true}
	unknown := make([]string, 0)
	for key := range fields {
		if !allowed[key] {
			unknown = append(unknown, key)
		}
	}
	if len(unknown) > 0 {
		sort.Strings(unknown)
		return nil, &ValidationError{
			Field:   base + "." + unknown[0],
			Message: "node.status payload allows exactly node_id, state and attempt_no; metadata events never carry content",
		}
	}

	nodeID, err := eventStringField(fields, "node_id", base, maxEventNodeID)
	if err != nil {
		return nil, err
	}
	state, err := eventStringField(fields, "state", base, maxEventState)
	if err != nil {
		return nil, err
	}
	rawAttempt, present := fields["attempt_no"]
	if !present {
		return nil, &ValidationError{Field: base + ".attempt_no", Message: "attempt_no is required"}
	}
	var attemptNo int64
	if err := json.Unmarshal(rawAttempt, &attemptNo); err != nil || attemptNo < 1 {
		return nil, &ValidationError{
			Field: base + ".attempt_no", Message: "attempt_no must be an integer at least 1",
		}
	}
	payload, marshalErr := json.Marshal(nodeStatusPayload{
		NodeID: nodeID, State: state, AttemptNo: attemptNo,
	})
	if marshalErr != nil {
		return nil, fmt.Errorf("encoding the node.status payload: %w", marshalErr)
	}
	return payload, nil
}

func eventStringField(
	fields map[string]json.RawMessage, name, base string, maxLength int,
) (string, error) {
	raw, present := fields[name]
	if !present {
		return "", &ValidationError{Field: base + "." + name, Message: name + " is required"}
	}
	var value string
	if err := json.Unmarshal(raw, &value); err != nil {
		return "", &ValidationError{Field: base + "." + name, Message: name + " must be a string"}
	}
	value = strings.TrimSpace(value)
	if value == "" || len(value) > maxLength || !isPrintable(value) {
		return "", &ValidationError{
			Field:   base + "." + name,
			Message: fmt.Sprintf("%s must be printable and at most %d characters", name, maxLength),
		}
	}
	return value, nil
}

// ---------------------------------------------------------------------------
// replay read model (used by the SSE handler)
// ---------------------------------------------------------------------------

// EventReplayFacts reports whether the next event after `after` exists, whether
// the Run is terminal and whether the replay boundary is outside the retention
// window. The window check is on the event the client would receive next: a
// cursor at the latest event is never expired (there is nothing to replay).
func (s *Service) EventReplayFacts(
	ctx context.Context, runID string, after int64, retention time.Duration,
) (*EventReplayFacts, error) {
	if !uuid.Valid(strings.TrimSpace(runID)) {
		return nil, ErrRunNotFound
	}
	if after < 0 {
		after = 0
	}
	var facts EventReplayFacts
	err := s.DB.QueryRowContext(ctx, `
		SELECT r.state,
		       COALESCE(boundary.max_seq, 0) AS max_seq,
		       (next_event.seq IS NOT NULL) AS has_more,
		       COALESCE(
		           next_event.created_at < now() - make_interval(secs => $3::double precision),
		           false
		       ) AS expired
		  FROM platform.runs AS r
		  LEFT JOIN LATERAL (
		      SELECT max(e.seq) AS max_seq
		        FROM platform.run_events AS e
		       WHERE e.run_id = r.id
		  ) AS boundary ON true
		  LEFT JOIN LATERAL (
		      SELECT e.seq, e.created_at
		        FROM platform.run_events AS e
		       WHERE e.run_id = r.id AND e.seq > $2
		       ORDER BY e.seq
		       LIMIT 1
		  ) AS next_event ON true
		 WHERE r.id = $1::uuid`,
		runID, after, retention.Seconds(),
	).Scan(&facts.RunState, &facts.MaxSeq, &facts.HasMore, &facts.Expired)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrRunNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the event replay boundary: %w", err)
	}
	return &facts, nil
}

// ListEventsAfter returns at most limit durable events with seq > after, in
// seq order. It is the SSE replay read; it does not interpret payloads.
func (s *Service) ListEventsAfter(
	ctx context.Context, runID string, after int64, limit int,
) ([]Event, error) {
	if !uuid.Valid(strings.TrimSpace(runID)) {
		return nil, ErrRunNotFound
	}
	if after < 0 {
		after = 0
	}
	if limit < 1 || limit > 500 {
		limit = 200
	}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT event_id::text, run_id::text, seq, revision, type, payload, created_at
		  FROM platform.run_events
		 WHERE run_id = $1::uuid AND seq > $2
		 ORDER BY seq
		 LIMIT $3`,
		runID, after, limit,
	)
	if err != nil {
		return nil, fmt.Errorf("listing run events: %w", err)
	}
	defer rows.Close()
	events := make([]Event, 0, limit)
	for rows.Next() {
		var (
			event     Event
			payload   []byte
			createdAt time.Time
		)
		if err := rows.Scan(
			&event.EventID, &event.RunID, &event.Seq, &event.Revision,
			&event.Type, &payload, &createdAt,
		); err != nil {
			return nil, fmt.Errorf("scanning a run event: %w", err)
		}
		event.SchemaVersion = EventSchemaVersion
		event.Payload = json.RawMessage(payload)
		event.CreatedAt = createdAt.UTC().Format(time.RFC3339Nano)
		events = append(events, event)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("listing run events: %w", err)
	}
	return events, nil
}
