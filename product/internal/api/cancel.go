package api

// F03/T036: the public cancel endpoint.
//
//	POST /api/v1/runs/{run_id}/cancel -> 202 Run
//
// Access is owner-only: a Run has no grant type in this batch, so unknown,
// malformed, deleted and not-owned ids are the same 404 (the getRun rule), and an
// administrator is not exempt. A browser session is authenticated by its cookie
// and must present the CSRF token (authorize); an API key must carry
// application:write. Neither path can cancel another principal's Run.
//
// The response is always 202 with the contract Run, including a terminal Run
// (a repeated cancel never changes it). "Accepted" is not "stopped": the caller
// learns the outcome from the Run state returned here and from
// GET /api/v1/runs/{id} (X-Run-Cancel-Requested-At, and cancelling/cancelled).

import (
	"errors"
	"net/http"
	"time"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/identity"
)

func (s *Server) handleCancelRun(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	// The ticket chooses: session CSRF (authorize above) or an API key with the
	// application:write scope. A human session does not need the creator role to
	// stop its own Run (that would strand work after a role change).
	if caller.Method == identity.AuthMethodAPIKey {
		if failure := requireScope(caller, identity.ScopeApplicationWrite); failure != nil {
			writeError(w, r, failure.Status, failure.Code, failure.Message)
			return
		}
	}
	outcome, err := s.execution.Cancel(r.Context(), caller.PrincipalID, r.PathValue("run_id"))
	switch {
	case errors.Is(err, execution.ErrRunNotFound):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that run does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	// Read the committed Run through the one owner-facing projection, exactly like
	// the result protocol: the body is the contract Run and no internal row leaks.
	run, err := s.catalog.GetRun(r.Context(), caller.PrincipalID, outcome.RunID)
	switch {
	case errors.Is(err, catalog.ErrNotVisible):
		// Defensive: the owner was just verified in the cancel transaction.
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that run does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	w.Header().Set("X-Manifest-SHA256", run.ManifestSHA256)
	if cancelAt, err := s.execution.CancelRequestedAt(r.Context(), run.ID); err != nil {
		s.internalError(w, r, err)
		return
	} else if cancelAt != nil {
		w.Header().Set("X-Run-Cancel-Requested-At", cancelAt.UTC().Format(time.RFC3339Nano))
	}
	writeJSON(w, http.StatusAccepted, run)
}
