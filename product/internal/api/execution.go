package api

// F03/T029+T031: the internal execution-protocol operations.
//
//	POST /internal/v1/jobs/claim                              claimJob
//	POST /internal/v1/runs/{run_id}/heartbeat                 heartbeat
//	POST /internal/v1/runs/{run_id}/result                    submitWorkerResult
//	POST /internal/v1/attempts/{attempt_id}/exited            confirmAttemptExit
//
// They live on the separate mTLS listener (RegisterInternal) and are never
// reachable with a session cookie or API key. Capacity is decided by the
// certificate Organization AND the repeated X-Internal-Role header
// (RequireInternalMTLS); these handlers add the per-endpoint rule:
//
//   - claim and attempt exit: supervisor only. A worker cannot claim work or
//     certify its own exit (API.md §6, EXECUTION.md §4/§6).
//   - heartbeat and result: supervisor or worker. The attempt token is the
//     credential; a worker may propose a result but only the supervisor can
//     confirm the exit that lets the finalizer commit it.
//   - maintenance: none of these endpoints.
//
// The handlers decode the contract bodies strictly, then map the execution
// package's typed errors to the contract vocabulary. They write no SQL.

import (
	"encoding/json"
	"errors"
	"net/http"

	"orchara/platform/internal/execution"
)

type internalExecutionClaimRequest struct {
	WorkerID           string   `json:"worker_id"`
	SupportedCompilers []string `json:"supported_compilers"`
	ExecutionUnitID    string   `json:"execution_unit_id"`
}

func (s *Server) handleInternalExecutionClaim(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleSupervisor) {
		return
	}
	var request internalExecutionClaimRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	lease, err := s.execution.Claim(r.Context(), execution.ClaimRequest{
		WorkerID:           request.WorkerID,
		SupportedCompilers: request.SupportedCompilers,
		ExecutionUnitID:    request.ExecutionUnitID,
	})
	if !s.writeExecutionError(w, r, err) {
		return
	}
	if lease == nil {
		// No compatible job, no capacity, or an overdue queued Run that the claim
		// just terminalised (T034): 204 with no body and no lease. The overdue case
		// is the only 204 that changed a row: the Run is failed(deadline_exceeded)
		// and its never-run job is void, so nothing is dispatched.
		w.Header().Set("Cache-Control", "no-store")
		w.WriteHeader(http.StatusNoContent)
		return
	}
	writeJSON(w, http.StatusOK, lease)
}

type internalRunHeartbeatRequest struct {
	AttemptToken          string          `json:"attempt_token"`
	ExecutionUnitID       string          `json:"execution_unit_id"`
	CheckpointConnections json.RawMessage `json:"checkpoint_connections"`
}

func (s *Server) handleInternalRunHeartbeat(w http.ResponseWriter, r *http.Request) {
	if !requireAnyInternalRole(w, r, InternalRoleSupervisor, InternalRoleWorker) {
		return
	}
	var request internalRunHeartbeatRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	reply, err := s.execution.Heartbeat(r.Context(), execution.HeartbeatRequest{
		RunID:                 r.PathValue("run_id"),
		AttemptToken:          request.AttemptToken,
		ExecutionUnitID:       request.ExecutionUnitID,
		CheckpointConnections: request.CheckpointConnections,
	})
	if !s.writeExecutionError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, reply)
}

// handleInternalRunResult accepts the worker's result *proposal*. The 200 body
// is the contract Run read through the single owner-facing projection: a
// proposal-only call returns running/finalizing, and only a call that also
// completes the finalizer (the exit evidence was already present) returns the
// committed terminal Run.
func (s *Server) handleInternalRunResult(w http.ResponseWriter, r *http.Request) {
	if !requireAnyInternalRole(w, r, InternalRoleSupervisor, InternalRoleWorker) {
		return
	}
	var request execution.WorkerResult
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	outcome, err := s.execution.SubmitResult(r.Context(), r.PathValue("run_id"), request)
	if !s.writeExecutionError(w, r, err) {
		return
	}
	run, err := s.catalog.GetRun(r.Context(), outcome.OwnerID, outcome.RunID)
	if err != nil {
		s.internalError(w, r, err)
		return
	}
	w.Header().Set("X-Manifest-SHA256", run.ManifestSHA256)
	writeJSON(w, http.StatusOK, run)
}

type internalAttemptExitRequest struct {
	ExecutionUnitID string          `json:"execution_unit_id"`
	Evidence        json.RawMessage `json:"evidence"`
}

func (s *Server) handleInternalAttemptExit(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleSupervisor) {
		return
	}
	var request internalAttemptExitRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	result, err := s.execution.ConfirmExit(r.Context(), execution.ExitRequest{
		AttemptID:       r.PathValue("attempt_id"),
		ExecutionUnitID: request.ExecutionUnitID,
		Evidence:        request.Evidence,
	})
	if !s.writeExecutionError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, result)
}

// writeExecutionError maps the execution package's typed errors to the contract
// vocabulary and reports whether the caller may continue.
func (s *Server) writeExecutionError(w http.ResponseWriter, r *http.Request, err error) bool {
	switch {
	case err == nil:
		return true
	case executionOutputArtifactFailure(err) != nil:
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			err.Error(), executionOutputArtifactFailure(err))
		return false
	case executionValidationFailure(err) != nil:
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			err.Error(), executionValidationFailure(err))
		return false
	case errors.Is(err, execution.ErrAttemptNotFound):
		writeError(w, r, http.StatusNotFound, "attempt_not_found",
			"that execution attempt does not exist")
		return false
	case errors.Is(err, execution.ErrRunNotFound):
		writeError(w, r, http.StatusNotFound, "run_not_found", "that run does not exist")
		return false
	case errors.Is(err, execution.ErrStaleAttempt):
		writeError(w, r, http.StatusConflict, "stale_attempt",
			"the attempt token does not own a live lease on this run")
		return false
	case errors.Is(err, execution.ErrEvidenceConflict):
		writeError(w, r, http.StatusConflict, "attempt_evidence_conflict",
			"this attempt already exited with different evidence")
		return false
	case errors.Is(err, execution.ErrEventConflict):
		writeError(w, r, http.StatusConflict, "event_conflict",
			"this idempotency identity already carries different content")
		return false
	case errors.Is(err, execution.ErrRunTerminal):
		writeError(w, r, http.StatusConflict, "run_terminal",
			"the run is already terminal; a late result never revives it")
		return false
	case errors.Is(err, execution.ErrRunStateConflict):
		writeError(w, r, http.StatusConflict, "run_state_conflict",
			"the run is not executing; waiting/cancelling/reconciliation states are owned by later F03 tickets")
		return false
	case errors.Is(err, execution.ErrIdentityMismatch):
		writeError(w, r, http.StatusConflict, "execution_identity_mismatch",
			"the exit evidence does not match the claimed protocol/manifest/image identity")
		return false
	case errors.Is(err, execution.ErrDeadlineExceeded):
		// F03/T034: the run reached its frozen active-time limit, so this result is
		// not accepted. The intent is already committed; the supervisor's exit
		// finalizes failed(deadline_exceeded).
		writeError(w, r, http.StatusConflict, execution.DeadlineErrorCode,
			"the run reached its active-time deadline; no result is accepted")
		return false
	case errors.Is(err, execution.ErrObjectSigningUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "object_signing_unavailable",
			"the server cannot mint sealed object links; no lease was issued")
		return false
	case errors.Is(err, execution.ErrObjectStoreUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the server cannot verify the sealed execution object; no lease was issued")
		return false
	case errors.Is(err, execution.ErrQuotaUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "execution_capacity_unavailable",
			"the execution quota counter is not configured")
		return false
	default:
		s.internalError(w, r, err)
		return false
	}
}

func executionValidationFailure(err error) map[string]any {
	var validation *execution.ValidationError
	if errors.As(err, &validation) && validation.Field != "" {
		return map[string]any{"field": validation.Field}
	}
	return nil
}

// executionOutputArtifactFailure locates a refused succeeded-proposal artifact:
// 422 validation_failed with the field and the stable reason code, exactly like
// T027's located execution refusals.
func executionOutputArtifactFailure(err error) map[string]any {
	var artifact *execution.OutputArtifactError
	if errors.As(err, &artifact) {
		return map[string]any{"field": "output_artifact_id", "code": artifact.Code}
	}
	return nil
}
