package api

import (
	"errors"
	"net/http"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/modelproxy"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/providercodes"
)

// T024's five internal endpoints. They are all maintenance-role routes on the
// mTLS listener and all re-prove the live attempt token of the job in the path;
// a stale token is 409 stale_attempt and never changes anything.
//
// The bodies are the contract's shapes. The answers are projections with no
// owner ids, object keys or candidate references: a handler gets what it needs
// to move bytes and nothing it could use to reach another job.

type internalArtifactTokenRequest struct {
	AttemptToken string `json:"attempt_token"`
	ArtifactID   string `json:"artifact_id"`
}

type internalUploadRequest struct {
	AttemptToken string `json:"attempt_token"`
	MediaType    string `json:"media_type"`
	SizeBytes    int64  `json:"size_bytes"`
	Purpose      string `json:"purpose"`
}

type internalCompleteRequest struct {
	AttemptToken string `json:"attempt_token"`
	SHA256       string `json:"sha256"`
}

type internalModelCallRequest struct {
	AttemptToken          string         `json:"attempt_token"`
	LogicalKey            string         `json:"logical_key"`
	ModelProfileVersionID string         `json:"model_profile_version_id"`
	Params                map[string]any `json:"params"`
}

// jobAttemptRequest is the one method every job-scoped body must provide: the
// attempt token that proves the lease. It lets one wrapper decode the body,
// enforce the maintenance role and check the live attempt before a handler sees
// any business data, without repeating that prologue five times.
type jobAttemptRequest interface {
	attemptToken() string
}

func (r internalArtifactTokenRequest) attemptToken() string { return r.AttemptToken }
func (r internalUploadRequest) attemptToken() string        { return r.AttemptToken }
func (r internalCompleteRequest) attemptToken() string      { return r.AttemptToken }
func (r internalModelCallRequest) attemptToken() string     { return r.AttemptToken }

type uploadView struct {
	ArtifactID string `json:"artifact_id"`
	UploadURL  string `json:"upload_url"`
	ExpiresAt  string `json:"expires_at"`
}

type artifactView struct {
	ID        string `json:"id"`
	State     string `json:"state"`
	MediaType string `json:"media_type"`
	SizeBytes int64  `json:"size_bytes"`
}

type downloadView struct {
	URL       string `json:"url"`
	ExpiresAt string `json:"expires_at"`
}

type modelCallView struct {
	CallID           string         `json:"call_id"`
	State            string         `json:"state"`
	ResultArtifactID *string        `json:"result_artifact_id"`
	Data             map[string]any `json:"data,omitempty"`
}

func artifactViewOf(view background.ArtifactView) artifactView {
	return artifactView{
		ID:        view.ID,
		State:     view.State,
		MediaType: view.MediaType,
		SizeBytes: view.SizeBytes,
	}
}

// withJobData is the shared prologue of every job-scoped internal endpoint:
//
//	requireInternalRole(maintenance) -> decodeJSON -> requireJobAttempt.
//
// It preserves the existing statuses and body shapes (403 forbidden for a
// non-maintenance certificate, 400 invalid_json for a malformed body, and
// writeJobControlError's mapping for a bad job id/token). A handler that gets
// ok=true knows the caller holds the live lease and request is decoded.
func (s *Server) withJobData(
	w http.ResponseWriter,
	r *http.Request,
	request jobAttemptRequest,
) (*background.Job, bool) {
	if !requireInternalRole(w, r, InternalRoleMaintenance) {
		return nil, false
	}
	if err := decodeJSON(w, r, request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return nil, false
	}
	job, err := s.jobs.CheckAttempt(r.Context(), r.PathValue("job_id"), request.attemptToken())
	if !s.writeJobControlError(w, r, err) {
		return nil, false
	}
	return job, true
}

func (s *Server) handleInternalArtifactUpload(w http.ResponseWriter, r *http.Request) {
	var request internalUploadRequest
	job, ok := s.withJobData(w, r, &request)
	if !ok {
		return
	}
	ticket, err := s.artifacts.CreateUpload(r.Context(), job, background.UploadRequest{
		MediaType: request.MediaType,
		SizeBytes: request.SizeBytes,
		Purpose:   request.Purpose,
	})
	if !s.writeJobDataError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusCreated, uploadView{
		ArtifactID: ticket.ArtifactID,
		UploadURL:  ticket.URL,
		ExpiresAt:  ticket.ExpiresAt.UTC().Format(time.RFC3339),
	})
}

func (s *Server) handleInternalArtifactComplete(w http.ResponseWriter, r *http.Request) {
	var request internalCompleteRequest
	job, ok := s.withJobData(w, r, &request)
	if !ok {
		return
	}
	view, err := s.artifacts.CompleteUpload(r.Context(), job, r.PathValue("artifact_id"), request.SHA256)
	if !s.writeJobDataError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusAccepted, artifactViewOf(view))
}

func (s *Server) handleInternalArtifactDownload(w http.ResponseWriter, r *http.Request) {
	var request internalArtifactTokenRequest
	job, ok := s.withJobData(w, r, &request)
	if !ok {
		return
	}
	url, expiresAt, err := s.artifacts.DownloadURL(r.Context(), job, request.ArtifactID)
	if !s.writeJobDataError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, downloadView{
		URL:       url,
		ExpiresAt: expiresAt.UTC().Format(time.RFC3339),
	})
}

func (s *Server) handleInternalArtifactStatus(w http.ResponseWriter, r *http.Request) {
	var request internalArtifactTokenRequest
	job, ok := s.withJobData(w, r, &request)
	if !ok {
		return
	}
	view, err := s.artifacts.ArtifactStatus(r.Context(), job, request.ArtifactID)
	if !s.writeJobDataError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, artifactViewOf(view))
}

func (s *Server) handleInternalModelCall(w http.ResponseWriter, r *http.Request) {
	var request internalModelCallRequest
	job, ok := s.withJobData(w, r, &request)
	if !ok {
		return
	}
	switch {
	case job.Kind != background.KindModelValidate:
		writeError(w, r, http.StatusUnprocessableEntity, "model_not_allowed",
			"this background job kind may not invoke a model")
		return
	case request.ModelProfileVersionID != job.TargetID:
		// The handler may only validate the candidate it was created for. A
		// different candidate id is refused before the fixture is even read.
		writeError(w, r, http.StatusUnprocessableEntity, "candidate_mismatch",
			"the requested candidate is not this job's target")
		return
	case request.Params == nil:
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"params: params is required", map[string]any{"field": "params"})
		return
	case job.InputArtifactID == nil || *job.InputArtifactID == "":
		writeError(w, r, http.StatusUnprocessableEntity, "fixture_missing",
			"this validation job has no fixture artifact")
		return
	}
	candidate, err := s.models.Candidate(r.Context(), job.TargetID)
	if !s.writeJobDataError(w, r, err) {
		return
	}
	// Re-read the job's input artifact and prove it is a synthetic fixture owned
	// by the job owner BEFORE opening its bytes. Enqueue already refused a
	// business artifact, but this second check is what makes the model boundary
	// hold even for a hand-written or stale job row: T024's acceptance forbids
	// trying a model on business user content.
	fixture, err := s.artifacts.ReadSyntheticFixture(
		r.Context(), *job.InputArtifactID, job.OwnerID,
	)
	if !s.writeJobDataError(w, r, err) {
		return
	}
	result, err := s.models.Invoke(r.Context(), modelproxy.InvokeInput{
		JobID:             job.ID,
		OwnerID:           job.OwnerID,
		Candidate:         candidate,
		LogicalKey:        request.LogicalKey,
		Params:            request.Params,
		Fixture:           fixture.Content,
		FixtureSHA256:     fixture.SHA256,
		FixtureArtifactID: *job.InputArtifactID,
	})
	if !s.writeJobDataError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, modelCallView{
		CallID:           result.CallID,
		State:            result.State,
		ResultArtifactID: result.ResultArtifactID,
		Data:             result.Data,
	})
}

// writeJobDataError maps the T024 service errors to the contract vocabulary and
// reports whether the caller may continue.
func (s *Server) writeJobDataError(w http.ResponseWriter, r *http.Request, err error) bool {
	switch {
	case err == nil:
		return true
	case validationFailure(err) != nil:
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			err.Error(), validationFailure(err))
		return false
	case errors.Is(err, background.ErrNotFound):
		writeError(w, r, http.StatusNotFound, "job_not_found", "that background job does not exist")
		return false
	case errors.Is(err, background.ErrStaleAttempt):
		writeError(w, r, http.StatusConflict, "stale_attempt",
			"the attempt token does not own an unexpired lease on this job")
		return false
	case errors.Is(err, background.ErrArtifactNotFound):
		// Deliberately the same answer as a missing row: the endpoint must not
		// reveal another job's (or another user's) artifact ids.
		writeError(w, r, http.StatusNotFound, "artifact_not_found",
			"that artifact is not in this job's scope")
		return false
	case errors.Is(err, background.ErrArtifactNotReady):
		writeError(w, r, http.StatusConflict, "artifact_not_ready",
			"the artifact is not ready yet")
		return false
	case errors.Is(err, background.ErrArtifactConflict):
		writeError(w, r, http.StatusConflict, "artifact_conflict",
			"the upload completion disagrees with the recorded artifact")
		return false
	case errors.Is(err, background.ErrFixtureNotSynthetic):
		// A user-uploaded artifact is never a model fixture. Stable code: the
		// handler maps it to model_call_refused (business, no retry).
		writeError(w, r, http.StatusUnprocessableEntity, "fixture_not_synthetic",
			"the job input is not a synthetic model fixture; user content may not be sent to a model")
		return false
	case errors.Is(err, background.ErrFixtureNotOwned):
		// Same answer as "no fixture": the endpoint must not reveal that
		// another principal's artifact id exists.
		writeError(w, r, http.StatusUnprocessableEntity, "fixture_missing",
			"this validation job has no fixture artifact owned by its owner")
		return false
	case errors.Is(err, background.ErrSigningUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "object_signing_unavailable",
			"object link signing is not configured on this server")
		return false
	case errors.Is(err, background.ErrObjectStoreUnavailable),
		errors.Is(err, modelproxy.ErrResultStoreUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the object store is not configured on this server")
		return false
	case errors.Is(err, objectstore.ErrTooLarge):
		writeError(w, r, http.StatusRequestEntityTooLarge, "payload_too_large",
			"the object exceeds the allowed size")
		return false
	case errors.Is(err, modelproxy.ErrCandidateUnavailable):
		writeError(w, r, http.StatusUnprocessableEntity, "candidate_unavailable",
			"the candidate model profile is not available for validation")
		return false
	case errors.Is(err, modelproxy.ErrCallConflict):
		writeError(w, r, http.StatusConflict, "model_call_conflict",
			"this logical model call was already used with different parameters")
		return false
	case errors.Is(err, modelproxy.ErrNotConfigured):
		writeError(w, r, http.StatusServiceUnavailable, providercodes.NotConfigured,
			"no real model endpoint is configured on this server")
		return false
	case errors.Is(err, modelproxy.ErrAdapterUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, providercodes.DispatchAdapterUnavailable,
			"no vendor adapter is compiled for this provider in this build")
		return false
	case errors.Is(err, modelproxy.ErrDestinationUnapproved):
		// F06/T122: a candidate names a destination the deployer did not register.
		writeError(w, r, http.StatusServiceUnavailable, providercodes.DestinationNotApproved,
			"the model endpoint destination is not approved by the deployment catalog")
		return false
	case modelproxy.IsTransient(err):
		// F06/T122: 429/5xx/timeout. The job retry table treats this as an
		// infrastructure failure (bounded retry), never as a delivered answer.
		writeError(w, r, http.StatusServiceUnavailable, providercodes.Transient,
			"the model provider failed transiently")
		return false
	case modelproxy.ProviderCode(err) != "":
		// F06/T122: a non-retryable provider refusal (4xx, malformed answer,
		// oversized answer). The code is stable and never contains a payload.
		writeError(w, r, http.StatusServiceUnavailable, modelproxy.ProviderCode(err),
			"the model provider refused the call")
		return false
	default:
		s.internalError(w, r, err)
		return false
	}
}
