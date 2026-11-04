package api

// F06/T044: the streaming model protocol.
//
//	POST /internal/v1/runs/{run_id}/model-stream   streamModelCall (contract, worker mTLS, SSE)
//	GET  /api/v1/runs/{run_id}/model-preview       owner-only temporary preview (non-contract, SSE)
//
// The model stream is a temporary projection. It never writes run events: the
// durable Run SSE (GET /runs/{id}/events) is untouched and remains the authority
// for committed facts. Frames are published to the in-process preview hub as they
// are written so the owner can watch the same generation that the worker sees.

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"time"

	"orchara/platform/internal/capabilities"
	"orchara/platform/internal/catalog"
	"orchara/platform/internal/identity"
)

// handleInternalModelStream serves the contract's streamModelCall. It is
// worker-only; the same authorization and ledger rules as invokeCapability are
// applied inside capabilities.Stream. The body is the contract's CapabilityInput.
func (s *Server) handleInternalModelStream(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleWorker) {
		return
	}
	var request internalCapabilityRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		s.internalError(w, r, errors.New("the response writer cannot stream SSE"))
		return
	}
	runID := r.PathValue("run_id")
	started := false
	terminalWritten := false
	lastOperationID, lastGenerationID := "", ""

	writeFrame := func(frame capabilities.Frame) error {
		if !started {
			writeModelStreamHeaders(w)
			flusher.Flush()
			started = true
		}
		lastOperationID, lastGenerationID = frame.OperationID, frame.GenerationID
		// Publish before the write: a preview subscriber that connected first must
		// be able to observe the frame even if this worker connection drops.
		s.preview.Publish(runID, frame)
		payload, err := json.Marshal(frame)
		if err != nil {
			return err
		}
		if _, err := fmt.Fprintf(w, "event: %s\ndata: %s\n\n", frame.Type, payload); err != nil {
			return err
		}
		flusher.Flush()
		if terminalStreamFrame(frame) {
			terminalWritten = true
		}
		return nil
	}

	_, err := s.capabilities.Stream(r.Context(), runID, InternalRole(r.Context()),
		capabilities.Input{
			AttemptToken:      request.AttemptToken,
			OperationID:       request.OperationID,
			LogicalKey:        request.LogicalKey,
			CapabilityVersion: request.CapabilityVersion,
			CapabilityKind:    request.CapabilityKind,
			Params:            request.Params,
		}, writeFrame)
	if err == nil {
		return
	}
	if !started {
		// Nothing was written yet, so the honest answer is still a JSON contract
		// error with its real status (stale attempt, conflict, validation...).
		s.writeCapabilityError(w, r, err)
		return
	}
	if terminalWritten {
		// The stream already told the client how it ended; no second body.
		return
	}
	code, message := capabilities.CodeInternal, "the model stream ended unexpectedly"
	if typed, ok := capabilities.AsError(err); ok {
		code, message = typed.Code, typed.Message
	}
	frame := capabilities.Frame{
		Type:         capabilities.FrameError,
		OperationID:  lastOperationID,
		GenerationID: lastGenerationID,
		Error:        &capabilities.StreamError{Code: code, Message: message},
	}
	s.preview.Publish(runID, frame)
	payload, marshalErr := json.Marshal(frame)
	if marshalErr != nil {
		return
	}
	_, _ = fmt.Fprintf(w, "event: %s\ndata: %s\n\n", frame.Type, payload)
	flusher.Flush()
}

// writeModelStreamHeaders starts the SSE response. It is called lazily so a
// pre-generation authorization failure can still be a JSON error.
func writeModelStreamHeaders(w http.ResponseWriter) {
	headers := w.Header()
	headers.Set("Content-Type", "text/event-stream; charset=utf-8")
	headers.Set("Cache-Control", "no-store")
	headers.Set("Connection", "keep-alive")
	headers.Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
}

// handleRunModelPreview serves the owner-only temporary preview. Visibility is
// exactly getRun's rule: unknown, malformed, deleted and not-owned ids are the
// same 404. The preview replays the current generation (if any) and then streams
// live frames until final/error or client disconnect. A retryable error does not
// close it: the next generation replaces the old draft.
func (s *Server) handleRunModelPreview(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	runID := r.PathValue("run_id")
	run, err := s.catalog.GetRun(r.Context(), caller.PrincipalID, runID)
	if err != nil {
		if errors.Is(err, catalog.ErrNotVisible) {
			writeError(w, r, http.StatusNotFound, "resource_not_found", "that run does not exist")
			return
		}
		s.internalError(w, r, err)
		return
	}
	snapshot, live, cancel, hasGeneration := s.preview.Subscribe(runID)
	if !hasGeneration && isTerminalRunState(run.State) {
		// The run is over and no temporary preview was retained. The persistent
		// run snapshot/events are the authority, not this endpoint.
		writeError(w, r, http.StatusNotFound, "resource_not_found",
			"no temporary model preview is retained for that run")
		return
	}
	defer cancel()

	flusher, ok := w.(http.Flusher)
	if !ok {
		s.internalError(w, r, errors.New("the response writer cannot stream SSE"))
		return
	}
	writeModelStreamHeaders(w)
	flusher.Flush()

	writePreviewFrame := func(frame capabilities.Frame) bool {
		payload, err := json.Marshal(frame)
		if err != nil {
			return false
		}
		if _, err := fmt.Fprintf(w, "event: %s\ndata: %s\n\n", frame.Type, payload); err != nil {
			return false
		}
		flusher.Flush()
		return true
	}
	for _, frame := range snapshot {
		if !writePreviewFrame(frame) {
			return
		}
		if terminalStreamFrame(frame) {
			return
		}
	}
	if live == nil {
		return
	}
	heartbeat := time.NewTicker(15 * time.Second)
	defer heartbeat.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-heartbeat.C:
			if _, err := fmt.Fprint(w, ": heartbeat\n\n"); err != nil {
				return
			}
			flusher.Flush()
		case frame, open := <-live:
			if !open {
				return
			}
			if !writePreviewFrame(frame) {
				return
			}
			if terminalStreamFrame(frame) {
				return
			}
		}
	}
}
