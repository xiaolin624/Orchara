package api

import (
	"context"
	"errors"
	"net/http"
	"time"

	"orchara/platform/internal/background"
)

// Internal mTLS roles fixed by API.md §6 and the F00B plan (D3). The certificate
// Subject.Organization must be exactly one of these, and the X-Internal-Role
// header must repeat it: mTLS proves which process is calling, the header states
// which capacity it believes it is using, and a mismatch is refused rather than
// silently trusted.
const (
	InternalRoleSupervisor  = "supervisor"
	InternalRoleMaintenance = "maintenance"
	InternalRoleWorker      = "worker"
)

var internalRoles = map[string]bool{
	InternalRoleSupervisor:  true,
	InternalRoleMaintenance: true,
	InternalRoleWorker:      true,
}

type internalRoleKey struct{}

// InternalRole returns the verified internal role of the request, or "".
func InternalRole(ctx context.Context) string {
	role, _ := ctx.Value(internalRoleKey{}).(string)
	return role
}

// RequireInternalMTLS is the authentication middleware for the separate
// internal listener.
//
// The listener itself uses RequireAndVerifyClientCert, so a request that reaches
// this middleware already has a chain-verified client certificate. The
// middleware then enforces the capacity rule that TLS alone cannot:
//
//  1. the certificate must carry exactly one organization, from the fixed set;
//  2. X-Internal-Role must be present and exactly equal to that organization.
//
// Anything else is 403 in the contract error envelope. It is an authorization
// refusal, not a handshake failure, so the caller can tell "wrong role" apart
// from "no certificate".
func RequireInternalMTLS(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.TLS == nil || len(r.TLS.PeerCertificates) == 0 {
			writeError(w, r, http.StatusForbidden, "forbidden",
				"a client certificate signed by the internal CA is required")
			return
		}
		organizations := r.TLS.PeerCertificates[0].Subject.Organization
		if len(organizations) != 1 || !internalRoles[organizations[0]] {
			writeError(w, r, http.StatusForbidden, "forbidden",
				"the client certificate must name exactly one internal role organization")
			return
		}
		role := r.Header.Get("X-Internal-Role")
		if role == "" {
			writeError(w, r, http.StatusForbidden, "forbidden",
				"the X-Internal-Role header is required")
			return
		}
		if role != organizations[0] {
			writeError(w, r, http.StatusForbidden, "forbidden",
				"the X-Internal-Role header does not match the client certificate")
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), internalRoleKey{}, role)))
	})
}

// requireInternalRole refuses a verified caller whose capacity is not the one
// the endpoint serves. A worker certificate that honestly says "worker" is
// authenticated but still may not claim maintenance work.
func requireInternalRole(w http.ResponseWriter, r *http.Request, role string) bool {
	if InternalRole(r.Context()) == role {
		return true
	}
	writeError(w, r, http.StatusForbidden, "forbidden",
		"this endpoint requires the "+role+" internal role")
	return false
}

// requireAnyInternalRole is the same capacity check for an endpoint that may be
// served in more than one internal capacity (heartbeat: supervisor or worker).
func requireAnyInternalRole(w http.ResponseWriter, r *http.Request, roles ...string) bool {
	role := InternalRole(r.Context())
	for _, allowed := range roles {
		if role == allowed {
			return true
		}
	}
	writeError(w, r, http.StatusForbidden, "forbidden",
		"this endpoint requires an internal role other than the presented one")
	return false
}

// RegisterInternal adds the internal contract routes. The listener that serves
// this mux is TLS with client certificates; nothing here is reachable on the
// public listener.
func (s *Server) RegisterInternal(mux *http.ServeMux) {
	mux.HandleFunc("POST /internal/v1/background/jobs/claim", s.handleInternalClaim)
	mux.HandleFunc("POST /internal/v1/background/jobs/{job_id}/heartbeat", s.handleInternalHeartbeat)
	mux.HandleFunc("POST /internal/v1/background/jobs/{job_id}/result", s.handleInternalResult)

	// F00B/T024: job-scoped artifact access and metered model calls. Every route
	// re-proves the live attempt token; the job id in the path is never trusted,
	// and an artifact outside the job's scope answers the same 404 as a missing
	// one.
	mux.HandleFunc("POST /internal/v1/background/jobs/{job_id}/artifact-uploads", s.handleInternalArtifactUpload)
	mux.HandleFunc("POST /internal/v1/background/jobs/{job_id}/artifacts/{artifact_id}/complete", s.handleInternalArtifactComplete)
	mux.HandleFunc("POST /internal/v1/background/jobs/{job_id}/artifact-downloads", s.handleInternalArtifactDownload)
	mux.HandleFunc("POST /internal/v1/background/jobs/{job_id}/artifact-status", s.handleInternalArtifactStatus)
	mux.HandleFunc("POST /internal/v1/background/jobs/{job_id}/model-calls", s.handleInternalModelCall)
	// F00B/T026: the job-scoped evaluation binding (kind, dataset, bound manifest
	// hash) the handler must put in its report. Non-contract internal extension;
	// see internal/api/evaluations.go for why the lease cannot carry it.
	mux.HandleFunc("POST /internal/v1/background/jobs/{job_id}/evaluation", s.handleInternalEvaluationBinding)

	// F03/T029: the execution protocol. Claim and attempt exit are supervisor
	// capacities; heartbeat may be served by either the supervisor or the worker
	// that holds the attempt token. Worker/maintenance certificates are refused
	// per endpoint (API.md §6), not by the TLS layer.
	mux.HandleFunc("POST /internal/v1/jobs/claim", s.handleInternalExecutionClaim)
	mux.HandleFunc("POST /internal/v1/runs/{run_id}/heartbeat", s.handleInternalRunHeartbeat)
	mux.HandleFunc("POST /internal/v1/runs/{run_id}/result", s.handleInternalRunResult)
	mux.HandleFunc("POST /internal/v1/runs/{run_id}/events", s.handleInternalRunEvents)
	mux.HandleFunc("POST /internal/v1/attempts/{attempt_id}/exited", s.handleInternalAttemptExit)
}

type internalClaimRequest struct {
	WorkerID       string   `json:"worker_id"`
	SupportedKinds []string `json:"supported_kinds"`
}

// internalLeaseResponse is exactly the contract's BackgroundLease.
type internalLeaseResponse struct {
	JobID            string  `json:"job_id"`
	Kind             string  `json:"kind"`
	TargetID         string  `json:"target_id"`
	AttemptToken     string  `json:"attempt_token"`
	LeaseDeadline    string  `json:"lease_deadline"`
	ExpectedRevision *int64  `json:"expected_revision"`
	InputArtifactID  *string `json:"input_artifact_id"`
	InputURL         *string `json:"input_url"`
}

func (s *Server) handleInternalClaim(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleMaintenance) {
		return
	}
	var request internalClaimRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	kinds := make([]background.Kind, 0, len(request.SupportedKinds))
	for _, kind := range request.SupportedKinds {
		kinds = append(kinds, background.Kind(kind))
	}
	job, err := s.jobs.Claim(r.Context(), background.ClaimRequest{
		WorkerID:       request.WorkerID,
		SupportedKinds: kinds,
	})
	switch {
	case validationFailure(err) != nil:
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			err.Error(), validationFailure(err))
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	if job == nil {
		// No compatible ready job: 204 with no body per the contract.
		w.Header().Set("Cache-Control", "no-store")
		w.WriteHeader(http.StatusNoContent)
		return
	}
	response := internalLeaseResponse{
		JobID:            job.ID,
		Kind:             string(job.Kind),
		TargetID:         job.TargetID,
		AttemptToken:     derefString(job.LeaseToken),
		LeaseDeadline:    formatTimePtr(job.LeaseDeadline),
		ExpectedRevision: job.ExpectedRevision,
		InputArtifactID:  job.InputArtifactID,
		InputURL:         s.inputURL(r.Context(), job.InputArtifactID),
	}
	writeJSON(w, http.StatusOK, response)
}

// inputURL is the contract's BackgroundLease.input_url: a real signed sealed
// link when the job has a ready input artifact, and null otherwise (no input,
// not ready yet, or this process cannot sign). A handler must never invent an
// artifact URL; it asks the job-scoped endpoint if the lease did not carry one.
func (s *Server) inputURL(ctx context.Context, artifactID *string) *string {
	if artifactID == nil || s.artifacts == nil {
		return nil
	}
	return s.artifacts.SignedSealedURL(ctx, *artifactID)
}

type internalHeartbeatRequest struct {
	AttemptToken string `json:"attempt_token"`
}

func (s *Server) handleInternalHeartbeat(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleMaintenance) {
		return
	}
	var request internalHeartbeatRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	err := s.jobs.Heartbeat(r.Context(), r.PathValue("job_id"), request.AttemptToken)
	if !s.writeJobControlError(w, r, err) {
		return
	}
	// The contract's Accepted response. The job stayed leased.
	writeJSON(w, http.StatusOK, acceptedResponse{ID: r.PathValue("job_id"), State: string(background.StateLeased)})
}

type internalResultRequest struct {
	AttemptToken     string  `json:"attempt_token"`
	EventID          string  `json:"event_id"`
	State            string  `json:"state"`
	ResultArtifactID *string `json:"result_artifact_id"`
	ErrorCode        *string `json:"error_code"`
}

func (s *Server) handleInternalResult(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleMaintenance) {
		return
	}
	var request internalResultRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	job, err := s.jobs.Complete(r.Context(), background.Completion{
		JobID:            r.PathValue("job_id"),
		AttemptToken:     request.AttemptToken,
		EventID:          request.EventID,
		State:            background.State(request.State),
		ErrorCode:        derefString(request.ErrorCode),
		ResultArtifactID: derefString(request.ResultArtifactID),
	})
	if !s.writeJobControlError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, viewOfJob(job))
}

// writeJobControlError maps the controller's typed errors to the contract
// vocabulary and reports whether the caller may continue.
func (s *Server) writeJobControlError(w http.ResponseWriter, r *http.Request, err error) bool {
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
	case errors.Is(err, background.ErrEventConflict):
		writeError(w, r, http.StatusConflict, "event_conflict",
			"a different result event already completed this job")
		return false
	case errors.Is(err, background.ErrObjectStoreUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the server cannot verify the sealed object; the result was not applied")
		return false
	default:
		s.internalError(w, r, err)
		return false
	}
}

func validationFailure(err error) map[string]any {
	var validation *background.ValidationError
	if errors.As(err, &validation) && validation.Field != "" {
		return map[string]any{"field": validation.Field}
	}
	return nil
}

func formatTimePtr(value *time.Time) string {
	if value == nil {
		return ""
	}
	return value.UTC().Format(time.RFC3339)
}

func derefString(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}
