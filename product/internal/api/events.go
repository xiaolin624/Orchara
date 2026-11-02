package api

// F03/T032: the durable run-event surface.
//
//	POST /internal/v1/runs/{run_id}/events   submitWorkerEvents (mTLS: worker|supervisor)
//	GET  /api/v1/runs/{run_id}/events        runEvents (session/API key: owner only)
//
// The internal endpoint accepts one bounded batch of worker observations and is
// entirely handled by execution.SubmitWorkerEvents (live attempt token, event_id
// idempotency, run-local seq under the Run row lock). This file only decodes the
// contract body and maps typed errors.
//
// The public endpoint is an SSE stream of *committed* events. It is owner-only
// with exactly getRun's rule (404 for everyone else, administrators included),
// replays seq > Last-Event-ID, answers 410 event_cursor_expired with a
// details.snapshot_url when the replay boundary is older than the configured
// retention window (a documented contract gap: the frozen Error schema has no
// snapshot_url field), emits a comment heartbeat on an idle stream (never a
// business seq), and closes after replaying a terminal state. It polls
// PostgreSQL: Redis, when a deployment has one, is only a wakeup hint.

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/identity"
)

// Deployment defaults for the SSE endpoint. They are overridable through
// ORCHARA_RUN_EVENT_* and each is validated by config.Load.
const (
	// DefaultRunEventRetention is the documented 24h replay window.
	DefaultRunEventRetention = 24 * time.Hour
	// DefaultRunEventPollInterval is how often an idle stream polls PostgreSQL.
	DefaultRunEventPollInterval = 500 * time.Millisecond
	// DefaultRunEventHeartbeatInterval is the 15s idle comment heartbeat.
	DefaultRunEventHeartbeatInterval = 15 * time.Second
)

// internalWorkerEvent is the contract's WorkerEvent as received. The payload is
// kept raw because the exact per-type allowlist is execution's job.
type internalWorkerEvent struct {
	EventID       string          `json:"event_id"`
	LocalSequence int64           `json:"local_sequence"`
	Type          string          `json:"type"`
	Payload       json.RawMessage `json:"payload"`
}

type internalSubmitEventsRequest struct {
	AttemptToken string                `json:"attempt_token"`
	Events       []internalWorkerEvent `json:"events"`
}

// handleInternalRunEvents accepts the contract's submitWorkerEvents batch.
func (s *Server) handleInternalRunEvents(w http.ResponseWriter, r *http.Request) {
	if !requireAnyInternalRole(w, r, InternalRoleSupervisor, InternalRoleWorker) {
		return
	}
	var request internalSubmitEventsRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	events := make([]execution.WorkerEvent, 0, len(request.Events))
	for _, event := range request.Events {
		events = append(events, execution.WorkerEvent{
			EventID:       event.EventID,
			LocalSequence: event.LocalSequence,
			Type:          event.Type,
			Payload:       event.Payload,
		})
	}
	outcome, err := s.execution.SubmitWorkerEvents(
		r.Context(),
		r.PathValue("run_id"),
		execution.WorkerEventsRequest{
			AttemptToken: request.AttemptToken,
			Events:       events,
		},
	)
	if !s.writeExecutionError(w, r, err) {
		return
	}
	// The contract's Accepted body. The batch is acknowledged as a whole; a
	// duplicate-only batch is still accepted and consumes no seq.
	writeJSON(w, http.StatusOK, acceptedResponse{ID: outcome.RunID, State: "accepted"})
}

// handleRunEvents replays the owner's durable timeline over SSE.
func (s *Server) handleRunEvents(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	runID := strings.TrimSpace(r.PathValue("run_id"))
	// Exactly getRun's visibility rule: unknown, malformed, deleted and
	// not-owned ids are the same 404, so the stream cannot enumerate runs.
	if _, err := s.catalog.GetRun(r.Context(), caller.PrincipalID, runID); err != nil {
		if errors.Is(err, catalog.ErrNotVisible) {
			writeError(w, r, http.StatusNotFound, "resource_not_found", "that run does not exist")
			return
		}
		s.internalError(w, r, err)
		return
	}

	after, err := parseLastEventID(r)
	if err != nil {
		writeErrorWithDetails(w, r, http.StatusBadRequest, "validation_failed",
			err.Error(), map[string]any{"field": "Last-Event-ID"})
		return
	}

	facts, err := s.execution.EventReplayFacts(
		r.Context(), runID, after, s.runEventRetention,
	)
	if err != nil {
		if errors.Is(err, execution.ErrRunNotFound) {
			writeError(w, r, http.StatusNotFound, "resource_not_found", "that run does not exist")
			return
		}
		s.internalError(w, r, err)
		return
	}
	if facts.Expired {
		// The frozen contract's Error schema has no snapshot_url field, so the
		// current authorized Run snapshot is the documented details extension.
		// Silently skipping the lost window would fake a complete history.
		writeErrorWithDetails(w, r, http.StatusGone, "event_cursor_expired",
			"the replay cursor is older than the run-event retention window; fetch the current run snapshot",
			map[string]any{"snapshot_url": "/api/v1/runs/" + runID})
		return
	}

	s.streamRunEvents(w, r, runID, after)
}

// parseLastEventID reads the contract's optional `Last-Event-ID` header. The
// pattern is ^\d+$ for this Run's local seq; an invalid/duplicated header is a
// 400 instead of a guessed replay point.
func parseLastEventID(r *http.Request) (int64, error) {
	values := r.Header.Values("Last-Event-ID")
	if len(values) == 0 {
		return 0, nil
	}
	if len(values) != 1 {
		return 0, errors.New("Last-Event-ID must be supplied at most once")
	}
	raw := strings.TrimSpace(values[0])
	if raw == "" {
		return 0, errors.New("Last-Event-ID must be a non-negative integer")
	}
	for _, character := range raw {
		if character < '0' || character > '9' {
			return 0, errors.New("Last-Event-ID must be a non-negative integer")
		}
	}
	after, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || after < 0 {
		return 0, errors.New("Last-Event-ID must be a non-negative integer")
	}
	return after, nil
}

// streamRunEvents writes the SSE frames. Header errors are impossible after this
// point, so any read failure simply ends the stream (the client can reconnect
// with its last seq); the handler never writes partial JSON into an SSE body.
func (s *Server) streamRunEvents(
	w http.ResponseWriter, r *http.Request, runID string, after int64,
) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		s.internalError(w, r, errors.New("the response writer cannot stream SSE"))
		return
	}
	headers := w.Header()
	headers.Set("Content-Type", "text/event-stream; charset=utf-8")
	headers.Set("Cache-Control", "no-store")
	headers.Set("Connection", "keep-alive")
	// Reverse proxies must not buffer an event stream; there is no proxy in the
	// test/development deployment, but the header is the honest contract.
	headers.Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	heartbeat := time.NewTicker(s.runEventHeartbeat)
	defer heartbeat.Stop()
	poll := time.NewTicker(s.runEventPoll)
	defer poll.Stop()

	for {
		events, err := s.execution.ListEventsAfter(r.Context(), runID, after, 200)
		if err != nil {
			return
		}
		if len(events) > 0 {
			for _, event := range events {
				if !writeRunEventFrame(w, event) {
					return
				}
				after = event.Seq
			}
			flusher.Flush()
			continue
		}

		facts, err := s.execution.EventReplayFacts(
			r.Context(), runID, after, s.runEventRetention,
		)
		if err != nil {
			return
		}
		if isTerminalRunState(facts.RunState) && !facts.HasMore {
			// Every committed fact (including the terminal transition) has been
			// replayed; the stream's job is done.
			return
		}

		select {
		case <-r.Context().Done():
			return
		case <-poll.C:
			// PostgreSQL polling is the authority; there is no Redis wakeup in
			// this batch, and losing one would only delay a frame.
		case <-heartbeat.C:
			// A comment frame is not a business event: it carries no id/event/
			// data and never consumes a Run seq.
			if _, err := fmt.Fprint(w, ": heartbeat\n\n"); err != nil {
				return
			}
			flusher.Flush()
		}
	}
}

// writeRunEventFrame writes one contract-shaped SSE frame.
func writeRunEventFrame(w http.ResponseWriter, event execution.Event) bool {
	envelope, err := json.Marshal(event)
	if err != nil {
		return false
	}
	_, writeErr := fmt.Fprintf(w, "id: %d\nevent: %s\ndata: %s\n\n", event.Seq, event.Type, envelope)
	return writeErr == nil
}

func isTerminalRunState(state string) bool {
	switch state {
	case "succeeded", "failed", "cancelled":
		return true
	default:
		return false
	}
}
