package api

// F05/T033: run-scoped artifact endpoints of the execution protocol.
//
//	POST /internal/v1/runs/{run_id}/artifact-uploads              createWorkerUpload
//	POST /internal/v1/runs/{run_id}/artifacts/{artifact_id}/complete completeWorkerUpload
//	POST /internal/v1/runs/{run_id}/artifact-downloads           createWorkerDownload
//	POST /internal/v1/runs/{run_id}/artifact-status              getWorkerArtifactStatus
//	POST /internal/v1/runs/{run_id}/artifacts                    registerRunArtifact
//	GET  /api/v1/runs/{run_id}/artifacts                         listRunArtifacts
//
// The five internal routes are on the separate mTLS listener and require the
// `worker` certificate capacity exactly as the contract's x-internal-role says;
// a supervisor certificate is authenticated by TLS but refused 403 here (the
// supervisor claims and certifies exit, it does not move worker artifacts). The
// attempt token in the body is the credential every route re-proves against the
// run in the path, and the service layer decides everything else.
//
// The public list is owner-only with getRun's exact 404 rule, the shared opaque
// cursor and the four-field Artifact shape. It never exposes a log, an object
// key or a provenance token.

import (
	"errors"
	"net/http"
	"time"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/identity"
)

type internalRunArtifactTokenRequest struct {
	AttemptToken string `json:"attempt_token"`
	ArtifactID   string `json:"artifact_id"`
}

type internalRunUploadRequest struct {
	AttemptToken string `json:"attempt_token"`
	MediaType    string `json:"media_type"`
	SizeBytes    int64  `json:"size_bytes"`
	Purpose      string `json:"purpose"`
}

type internalRunCompleteRequest struct {
	AttemptToken string `json:"attempt_token"`
	SHA256       string `json:"sha256"`
}

type internalRunRegisterRequest struct {
	AttemptToken string `json:"attempt_token"`
	ArtifactID   string `json:"artifact_id"`
	Relation     string `json:"relation"`
}

func runArtifactViewOf(view execution.RunArtifactView) artifactView {
	return artifactView{
		ID:        view.ID,
		State:     view.State,
		MediaType: view.MediaType,
		SizeBytes: view.SizeBytes,
	}
}

// runArtifactPrologue is the shared prologue of the five worker routes: require
// the worker capacity, decode the strict contract body, and let the service
// re-prove the attempt token (403 is decided before any body is trusted).
func (s *Server) runArtifactPrologue(
	w http.ResponseWriter, r *http.Request, target any,
) bool {
	if !requireInternalRole(w, r, InternalRoleWorker) {
		return false
	}
	if err := decodeJSON(w, r, target); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return false
	}
	return true
}

func (s *Server) handleInternalRunArtifactUpload(w http.ResponseWriter, r *http.Request) {
	var request internalRunUploadRequest
	if !s.runArtifactPrologue(w, r, &request) {
		return
	}
	ticket, err := s.runArtifacts.CreateUpload(
		r.Context(), r.PathValue("run_id"), request.AttemptToken,
		execution.RunArtifactUploadRequest{
			MediaType: request.MediaType,
			SizeBytes: request.SizeBytes,
			Purpose:   request.Purpose,
		},
	)
	if !s.writeRunArtifactError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusCreated, uploadView{
		ArtifactID: ticket.ArtifactID,
		UploadURL:  ticket.URL,
		ExpiresAt:  ticket.ExpiresAt.UTC().Format(time.RFC3339),
	})
}

func (s *Server) handleInternalRunArtifactComplete(w http.ResponseWriter, r *http.Request) {
	var request internalRunCompleteRequest
	if !s.runArtifactPrologue(w, r, &request) {
		return
	}
	view, err := s.runArtifacts.CompleteUpload(
		r.Context(), r.PathValue("run_id"), request.AttemptToken,
		r.PathValue("artifact_id"), request.SHA256,
	)
	if !s.writeRunArtifactError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusAccepted, runArtifactViewOf(view))
}

func (s *Server) handleInternalRunArtifactDownload(w http.ResponseWriter, r *http.Request) {
	var request internalRunArtifactTokenRequest
	if !s.runArtifactPrologue(w, r, &request) {
		return
	}
	url, expiresAt, err := s.runArtifacts.DownloadURL(
		r.Context(), r.PathValue("run_id"), request.AttemptToken, request.ArtifactID,
	)
	if !s.writeRunArtifactError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, downloadView{
		URL:       url,
		ExpiresAt: expiresAt.UTC().Format(time.RFC3339),
	})
}

func (s *Server) handleInternalRunArtifactStatus(w http.ResponseWriter, r *http.Request) {
	var request internalRunArtifactTokenRequest
	if !s.runArtifactPrologue(w, r, &request) {
		return
	}
	view, err := s.runArtifacts.ArtifactStatus(
		r.Context(), r.PathValue("run_id"), request.AttemptToken, request.ArtifactID,
	)
	if !s.writeRunArtifactError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, runArtifactViewOf(view))
}

func (s *Server) handleInternalRegisterRunArtifact(w http.ResponseWriter, r *http.Request) {
	var request internalRunRegisterRequest
	if !s.runArtifactPrologue(w, r, &request) {
		return
	}
	view, err := s.runArtifacts.Register(
		r.Context(), r.PathValue("run_id"), request.AttemptToken, request.ArtifactID, request.Relation,
	)
	if !s.writeRunArtifactError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, runArtifactViewOf(view))
}

// runArtifactListResponse is the contract's list envelope for Artifact items.
type runArtifactListResponse struct {
	Items      []catalog.Artifact `json:"items"`
	NextCursor *string            `json:"next_cursor"`
}

func (s *Server) handleListRunArtifacts(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	query := r.URL.Query()
	limit, err := catalog.ParseLimit(query.Get("limit"))
	if err != nil {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
		return
	}
	items, nextCursor, err := s.catalog.ListRunArtifacts(
		r.Context(), caller.PrincipalID, r.PathValue("run_id"), query.Get("cursor"), limit,
	)
	switch {
	case errors.Is(err, catalog.ErrInvalidCursor):
		writeError(w, r, http.StatusBadRequest, "invalid_cursor",
			"cursor is not a valid continuation token")
		return
	case errors.Is(err, catalog.ErrInvalidLimit):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
		return
	case errors.Is(err, catalog.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that run does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	if items == nil {
		items = []catalog.Artifact{}
	}
	writeJSON(w, http.StatusOK, runArtifactListResponse{Items: items, NextCursor: nextCursor})
}

// writeRunArtifactError maps the run-artifact service errors onto the contract
// vocabulary. It always writes, so callers return immediately.
func (s *Server) writeRunArtifactError(w http.ResponseWriter, r *http.Request, err error) bool {
	var validation *execution.ValidationError
	switch {
	case err == nil:
		return true
	case errors.As(err, &validation):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			validation.Error(), map[string]any{"field": validation.Field})
		return false
	case errors.Is(err, execution.ErrStaleAttempt):
		writeError(w, r, http.StatusConflict, "stale_attempt",
			"the attempt token does not own a live lease on this run")
		return false
	case errors.Is(err, execution.ErrRunTerminal):
		writeError(w, r, http.StatusConflict, "run_terminal",
			"the run is already terminal; late artifacts are never registered")
		return false
	case errors.Is(err, execution.ErrRunStateConflict):
		writeError(w, r, http.StatusConflict, "run_state_conflict",
			"the run is not executing; waiting/cancelling/reconciliation are owned by later F03 tickets")
		return false
	case errors.Is(err, execution.ErrRunArtifactNotFound):
		writeError(w, r, http.StatusNotFound, "artifact_not_found",
			"that artifact is not in this run's scope")
		return false
	case errors.Is(err, execution.ErrArtifactNotReady):
		writeError(w, r, http.StatusConflict, "artifact_not_ready",
			"the artifact is not ready yet; wait for the finalize guard")
		return false
	case errors.Is(err, execution.ErrRunArtifactConflict):
		writeError(w, r, http.StatusConflict, "artifact_conflict",
			"the run artifact completion/registration disagrees with the recorded intent")
		return false
	case errors.Is(err, execution.ErrObjectSigningUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "object_signing_unavailable",
			"object link signing is not configured on this server")
		return false
	case errors.Is(err, execution.ErrObjectStoreUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the object store is not configured on this server")
		return false
	default:
		s.internalError(w, r, err)
		return false
	}
}
