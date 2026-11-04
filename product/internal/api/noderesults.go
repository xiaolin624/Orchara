package api

// F04A/T042: the per-node result surface (non-contract, documented in
// reports/f04a/T042-python-node.md and ADR 0007's T042 append).
//
//	POST /internal/v1/runs/{run_id}/node-results   recordNodeResults (mTLS: worker|supervisor)
//	GET  /api/v1/runs/{run_id}/node-results        listRunNodeResults (owner only)
//
// The internal route is the report half of the two-phase artifact flow: the
// supervisor already moved and registered the bytes through the contract's
// run-scoped worker routes, then reports which node produced which artifact as
// data/file/log. The endpoint accepts no content and refuses an artifact that is
// not ready, not tied to this run/attempt, or whose purpose disagrees with the
// reported class (a log can never be reported as data).
//
// The owner route answers the same four-field provenance plus the artifact facts
// (media_type/size_bytes/sha256) and no content. Content is read through the
// existing GET /api/v1/artifacts/{id}/content and /download, which already apply
// their own owner check. The route is non-contract; the contract has no
// node-results shape (issue #43 asks for the user path, not a frozen schema).

import (
	"errors"
	"net/http"
	"time"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/identity"
)

type internalNodeResultItem struct {
	NodeID     string `json:"node_id"`
	Kind       string `json:"kind"`
	ArtifactID string `json:"artifact_id"`
	Label      string `json:"label"`
}

type internalNodeResultsRequest struct {
	AttemptToken string                   `json:"attempt_token"`
	Results      []internalNodeResultItem `json:"results"`
}

// internalNodeResultsResponse acknowledges the batch. recorded is the number of
// newly inserted provenance rows; an idempotent replay reports zero.
type internalNodeResultsResponse struct {
	RunID    string `json:"run_id"`
	Recorded int    `json:"recorded"`
}

func (s *Server) handleInternalRunNodeResults(w http.ResponseWriter, r *http.Request) {
	if !requireAnyInternalRole(w, r, InternalRoleSupervisor, InternalRoleWorker) {
		return
	}
	var request internalNodeResultsRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	results := make([]execution.NodeResult, 0, len(request.Results))
	for _, item := range request.Results {
		results = append(results, execution.NodeResult{
			NodeID: item.NodeID, Kind: item.Kind, ArtifactID: item.ArtifactID, Label: item.Label,
		})
	}
	recorded, err := s.runArtifacts.RecordNodeResults(
		r.Context(), r.PathValue("run_id"), request.AttemptToken, results,
	)
	if !s.writeRunArtifactError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, internalNodeResultsResponse{
		RunID: r.PathValue("run_id"), Recorded: recorded,
	})
}

// runNodeResultListResponse is the owner read envelope. It mirrors the contract
// list envelope's shape (items + next_cursor) so the page can reuse its list
// handling; node results are bounded by a run and need no cursor.
type runNodeResultListResponse struct {
	Items      []execution.RunNodeResultView `json:"items"`
	NextCursor *string                       `json:"next_cursor"`
	// GeneratedAt is the read clock; the page uses it to say how fresh the panel is.
	GeneratedAt string `json:"generated_at"`
}

func (s *Server) handleListRunNodeResults(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	runID := r.PathValue("run_id")
	// Exactly getRun's owner-only rule: unknown, malformed, deleted and
	// not-owned ids are the same 404, so the route cannot enumerate runs.
	if _, err := s.catalog.GetRun(r.Context(), caller.PrincipalID, runID); err != nil {
		if errors.Is(err, catalog.ErrNotVisible) {
			writeError(w, r, http.StatusNotFound, "resource_not_found", "that run does not exist")
			return
		}
		s.internalError(w, r, err)
		return
	}
	items, err := s.runArtifacts.ListNodeResults(r.Context(), runID)
	if err != nil {
		s.internalError(w, r, err)
		return
	}
	if items == nil {
		items = []execution.RunNodeResultView{}
	}
	writeJSON(w, http.StatusOK, runNodeResultListResponse{
		Items:       items,
		NextCursor:  nil,
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
	})
}
