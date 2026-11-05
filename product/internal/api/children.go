package api

// F07/T078: child Run creation and the reads around it.
//
//	POST /internal/v1/runs/{run_id}/children                          spawnChild (contract, worker mTLS, 202 Run)
//	POST /internal/v1/runs/{run_id}/children/{child_run_id}/result    non-contract terminal child result (worker mTLS)
//	GET  /api/v1/runs/{run_id}/children                               non-contract owner-only child Run list
//
// spawnChild is the only surface that creates a child Run. Its body is exactly
// the contract's four fields; everything else (owner, snapshot, budget, depth,
// mapping, input identity) is derived server-side. The 202 response is the
// contract Run, and a replayed invocation key returns the original Run id with no
// second row — the contract has no "replayed" flag, so id equality is the proof.
//
// The result route is the non-contract read the parent executor needs to return a
// terminal child's structured result. It re-proves the parent's live attempt
// token and the parent_run_id link; it never exposes another Run's artifact. The
// owner list is what the Run page uses to show the independent child timeline; it
// is owner-only exactly like GET /runs/{id} and returns the same Run projection.
//
// The handlers write no SQL: internal/children owns every read/write and maps its
// typed refusals to contract status/code pairs here.

import (
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/children"
	"orchara/platform/internal/identity"
)

type internalSpawnChildRequest struct {
	AttemptToken         string          `json:"attempt_token"`
	LogicalKey           string          `json:"logical_key"`
	ApplicationVersionID string          `json:"application_version_id"`
	Input                json.RawMessage `json:"input"`
}

func (s *Server) handleInternalSpawnChild(w http.ResponseWriter, r *http.Request) {
	// The contract declares x-internal-role: worker for this operation. A
	// supervisor or maintenance certificate is authenticated but refused: it does
	// not execute nodes, so it has no attempt token to delegate with.
	if !requireInternalRole(w, r, InternalRoleWorker) {
		return
	}
	var request internalSpawnChildRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	outcome, err := s.children.Spawn(r.Context(), r.PathValue("run_id"), children.SpawnRequest{
		AttemptToken:         request.AttemptToken,
		LogicalKey:           request.LogicalKey,
		ApplicationVersionID: request.ApplicationVersionID,
		Input:                request.Input,
	})
	if !s.writeChildrenError(w, r, err) {
		return
	}
	w.Header().Set("X-Manifest-SHA256", outcome.Run.ManifestSHA256)
	writeJSON(w, http.StatusAccepted, outcome.Run)
}

type internalChildResultRequest struct {
	AttemptToken string `json:"attempt_token"`
}

func (s *Server) handleInternalChildResult(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleWorker) {
		return
	}
	var request internalChildResultRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	result, err := s.children.Result(
		r.Context(), r.PathValue("run_id"), r.PathValue("child_run_id"), request.AttemptToken,
	)
	if !s.writeChildrenError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, result)
}

// childRunListResponse mirrors the contract list envelope shape (items plus a
// nullable cursor) so the page reuses its existing list handling. A run's child
// count is bounded by its frozen policy, so no cursor is invented.
type childRunListResponse struct {
	Items       []children.RunView `json:"items"`
	NextCursor  *string            `json:"next_cursor"`
	GeneratedAt string             `json:"generated_at"`
}

func (s *Server) handleListChildRuns(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	runID := r.PathValue("run_id")
	// Exactly getRun's owner-only rule: unknown, malformed, deleted and not-owned
	// ids are the same 404, so the route cannot enumerate runs.
	if _, err := s.catalog.GetRun(r.Context(), caller.PrincipalID, runID); err != nil {
		if errors.Is(err, catalog.ErrNotVisible) {
			writeError(w, r, http.StatusNotFound, "resource_not_found", "that run does not exist")
			return
		}
		s.internalError(w, r, err)
		return
	}
	items, err := s.children.ListForParent(r.Context(), runID)
	if !s.writeChildrenError(w, r, err) {
		return
	}
	if items == nil {
		items = []children.RunView{}
	}
	writeJSON(w, http.StatusOK, childRunListResponse{
		Items:       items,
		NextCursor:  nil,
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
	})
}

// writeChildrenError maps internal/children's typed refusals to the contract
// vocabulary and reports whether the caller may continue.
func (s *Server) writeChildrenError(w http.ResponseWriter, r *http.Request, err error) bool {
	if err == nil {
		return true
	}
	if typed, ok := children.AsError(err); ok {
		if len(typed.Details) > 0 {
			writeErrorWithDetails(w, r, typed.Status, typed.Code, typed.Message, typed.Details)
		} else {
			writeError(w, r, typed.Status, typed.Code, typed.Message)
		}
		return false
	}
	s.internalError(w, r, err)
	return false
}
