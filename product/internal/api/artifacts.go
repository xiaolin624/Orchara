package api

// F05/T028: the user artifact endpoints.
//
//	POST /api/v1/artifacts/uploads                    -> 201 Upload
//	POST /api/v1/artifacts/{artifact_id}/complete      -> 202 Artifact
//	GET  /api/v1/artifacts/{artifact_id}               -> 200 Artifact
//	GET  /api/v1/artifacts/{artifact_id}/download      -> 200 Download
//	GET  /api/v1/artifacts/{artifact_id}/content       -> 200 octet-stream
//
// Access is owner-only for every read and for completion: another user (and an
// administrator, who is not granted arbitrary content access in this ticket)
// gets the same 404 as a missing row. Uploading is a normal-user capability, so
// a session needs no creator role; an API key needs the platform's two existing
// scopes, application:write for create/complete and application:read for reads
// (documented as an interim mapping until finer artifact scopes exist).
//
// Quarantined artifacts stay visible through GET (the owner must see the
// verdict) but are not downloadable or readable; the safe admission reason
// travels in the documented non-contract header X-Artifact-Quarantine-Reason,
// because the contract's Artifact schema has no field for it.

import (
	"errors"
	"io"
	"net/http"
	"strconv"
	"time"

	"orchara/platform/internal/artifacts"
	"orchara/platform/internal/identity"
)

type userUploadView struct {
	ArtifactID string `json:"artifact_id"`
	UploadURL  string `json:"upload_url"`
	ExpiresAt  string `json:"expires_at"`
}

type userArtifactView struct {
	ID        string `json:"id"`
	State     string `json:"state"`
	MediaType string `json:"media_type"`
	SizeBytes int64  `json:"size_bytes"`
}

type userDownloadView struct {
	URL       string `json:"url"`
	ExpiresAt string `json:"expires_at"`
}

type completeRequest struct {
	SHA256 string `json:"sha256"`
}

func viewOfArtifact(artifact artifacts.Artifact) userArtifactView {
	return userArtifactView{
		ID:        artifact.ID,
		State:     artifact.State,
		MediaType: artifact.MediaType,
		SizeBytes: artifact.SizeBytes,
	}
}

// writeArtifactFailure maps the service's typed errors onto the contract
// envelope. It always writes, so callers return immediately afterwards.
func (s *Server) writeArtifactFailure(w http.ResponseWriter, r *http.Request, err error) {
	var (
		validation *artifacts.ValidationError
		conflict   *artifacts.ConflictError
	)
	switch {
	case errors.Is(err, artifacts.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that artifact does not exist")
	case errors.As(err, &validation):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			validation.Error(), map[string]any{"field": validation.Field})
	case errors.As(err, &conflict):
		writeError(w, r, http.StatusConflict, conflict.Code, conflict.Message)
	case errors.Is(err, artifacts.ErrNotReady):
		writeError(w, r, http.StatusConflict, "artifact_not_ready",
			"the artifact is not ready to be read")
	case errors.Is(err, artifacts.ErrObjectStoreUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the object store is not configured on this server")
	case errors.Is(err, artifacts.ErrSigningUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "object_signing_unavailable",
			"object link signing is not configured on this server")
	default:
		s.internalError(w, r, err)
	}
}

// requireArtifactScope applies the interim API-key scope mapping. Sessions are
// not scope-limited; an API key without the scope is refused before any row is
// read, so a scope never replaces the owner-only ACL.
func (s *Server) requireArtifactScope(
	w http.ResponseWriter,
	r *http.Request,
	caller *identity.Identity,
	scope string,
) bool {
	if failure := requireScope(caller, scope); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return false
	}
	return true
}

func (s *Server) handleCreateUpload(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireArtifactScope(w, r, caller, identity.ScopeApplicationWrite) {
		return
	}
	var request artifacts.UploadInput
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	ticket, err := s.userArtifacts.CreateUpload(r.Context(), caller.PrincipalID, request)
	if err != nil {
		s.writeArtifactFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, userUploadView{
		ArtifactID: ticket.ArtifactID,
		UploadURL:  ticket.URL,
		ExpiresAt:  ticket.ExpiresAt.UTC().Format(time.RFC3339),
	})
}

func (s *Server) handleCompleteUpload(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireArtifactScope(w, r, caller, identity.ScopeApplicationWrite) {
		return
	}
	var request completeRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	artifact, err := s.userArtifacts.CompleteUpload(
		r.Context(), caller.PrincipalID, r.PathValue("artifact_id"), request.SHA256,
	)
	if err != nil {
		s.writeArtifactFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusAccepted, viewOfArtifact(artifact))
}

func (s *Server) handleGetArtifact(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireArtifactScope(w, r, caller, identity.ScopeApplicationRead) {
		return
	}
	artifact, err := s.userArtifacts.Get(r.Context(), caller.PrincipalID, r.PathValue("artifact_id"))
	if err != nil {
		s.writeArtifactFailure(w, r, err)
		return
	}
	if artifact.State == "quarantined" && artifact.QuarantineReason != nil {
		// Non-contract extension, documented in the T028 report: the four-field
		// Artifact schema has no place for the safe admission reason.
		w.Header().Set("X-Artifact-Quarantine-Reason", *artifact.QuarantineReason)
	}
	writeJSON(w, http.StatusOK, viewOfArtifact(artifact))
}

func (s *Server) handleDownloadArtifact(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireArtifactScope(w, r, caller, identity.ScopeApplicationRead) {
		return
	}
	url, expiresAt, err := s.userArtifacts.DownloadURL(
		r.Context(), caller.PrincipalID, r.PathValue("artifact_id"),
	)
	if err != nil {
		s.writeArtifactFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, userDownloadView{
		URL:       url,
		ExpiresAt: expiresAt.UTC().Format(time.RFC3339),
	})
}

// handleArtifactContent is the immediate-revocation download: it streams the
// sealed object through the API instead of handing out a link. The ACL/tombstone
// decision happens before a single byte is opened; the body is copied, never
// buffered, and is served as an attachment so the platform origin cannot execute
// it.
func (s *Server) handleArtifactContent(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireArtifactScope(w, r, caller, identity.ScopeApplicationRead) {
		return
	}
	reader, size, err := s.userArtifacts.OpenContent(
		r.Context(), caller.PrincipalID, r.PathValue("artifact_id"),
	)
	if err != nil {
		s.writeArtifactFailure(w, r, err)
		return
	}
	defer func() { _ = reader.Close() }()

	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Length", strconv.FormatInt(size, 10))
	w.Header().Set("Content-Disposition", "attachment")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusOK)
	if _, err := io.Copy(w, reader); err != nil {
		// The status and first bytes are already on the wire; the request log
		// records the failure and the caller sees a truncated body.
		s.logger.Printf("request_id=%s artifact content read failed: %v", RequestID(r.Context()), err)
	}
}
