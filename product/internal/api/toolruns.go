package api

// F05/T030: the preset-tool run endpoints (documented non-contract bridge).
//
//	POST /api/v1/tool-runs          -> 202 {id, state, tool, artifact_id}
//	GET  /api/v1/tool-runs/{job_id} -> 200 {id, state, error_code, artifacts}
//
// The frozen contract has no tool-run path yet (T033/F04A will bind preset tools
// to the run/sandbox protocol), so this is an explicit extension rather than a
// silent change to a contract path. The create needs a browser session + CSRF
// or an API key with application:write and a required Idempotency-Key; the read
// is owner-only (another user or an administrator gets 404) and needs
// application:read for an API key, the same interim scope mapping T028 uses.

import (
	"errors"
	"net/http"
	"strings"

	"orchara/platform/internal/identity"
	"orchara/platform/internal/toolruns"
)

func (s *Server) handleCreateToolRun(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationWrite); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	values := r.Header.Values("Idempotency-Key")
	key := ""
	if len(values) == 1 {
		key = strings.TrimSpace(values[0])
	}
	if len(values) != 1 || key == "" {
		writeErrorWithDetails(w, r, http.StatusBadRequest, "idempotency_key_required",
			"the Idempotency-Key header is required and must be one non-empty value",
			map[string]any{"header": "Idempotency-Key"})
		return
	}
	var request toolruns.CreateRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	view, err := s.toolRuns.CreateRun(
		r.Context(),
		caller.PrincipalID,
		toolruns.IdempotencyScope{
			PrincipalID: caller.PrincipalID,
			Method:      http.MethodPost,
			Path:        r.URL.Path,
			Key:         key,
		},
		request,
	)
	if err != nil {
		s.writeToolRunFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusAccepted, view)
}

func (s *Server) handleGetToolRun(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	view, err := s.toolRuns.GetRun(r.Context(), caller.PrincipalID, r.PathValue("job_id"))
	if err != nil {
		s.writeToolRunFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, view)
}

// writeToolRunFailure maps the service's typed errors onto the contract
// envelope. It always writes, so every caller returns immediately after it.
func (s *Server) writeToolRunFailure(w http.ResponseWriter, r *http.Request, err error) {
	var validation *toolruns.ValidationError
	switch {
	case errors.As(err, &validation):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			validation.Error(), map[string]any{"field": validation.Field})
	case errors.Is(err, toolruns.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found",
			"that artifact or tool run does not exist")
	case errors.Is(err, toolruns.ErrNotReady):
		writeError(w, r, http.StatusConflict, "artifact_not_ready",
			"the artifact is not ready to run the preset tool")
	case errors.Is(err, toolruns.ErrIdempotencyConflict):
		writeErrorWithDetails(w, r, http.StatusConflict, "idempotency_conflict",
			"this Idempotency-Key was already used for a different request",
			map[string]any{"header": "Idempotency-Key"})
	case errors.Is(err, toolruns.ErrConflict):
		writeError(w, r, http.StatusConflict, "tool_run_conflict",
			"this artifact revision already has a conflicting tool run")
	default:
		s.internalError(w, r, err)
	}
}
