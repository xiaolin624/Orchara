package api

// F03/T029: the three internal execution-protocol operations.
//
//	POST /internal/v1/jobs/claim                              claimJob
//	POST /internal/v1/runs/{run_id}/heartbeat                 heartbeat
//	POST /internal/v1/attempts/{attempt_id}/exited            confirmAttemptExit
//
// They live on the separate mTLS listener (RegisterInternal) and are never
// reachable with a session cookie or API key. Capacity is decided by the
// certificate Organization AND the repeated X-Internal-Role header
// (RequireInternalMTLS); these handlers add the per-endpoint rule:
//
//   - claim and attempt exit: supervisor only. A worker cannot claim work or
//     certify its own exit (API.md §6, EXECUTION.md §4/§6).
//   - heartbeat: supervisor or worker. The attempt token is the credential; the
//     handler does not know which process holds it.
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
		// No compatible job or no capacity: 204 with no body, nothing changed.
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
	case executionValidationFailure(err) != nil:
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			err.Error(), executionValidationFailure(err))
		return false
	case errors.Is(err, execution.ErrAttemptNotFound):
		writeError(w, r, http.StatusNotFound, "attempt_not_found",
			"that execution attempt does not exist")
		return false
	case errors.Is(err, execution.ErrStaleAttempt):
		writeError(w, r, http.StatusConflict, "stale_attempt",
			"the attempt token does not own a live lease on this run")
		return false
	case errors.Is(err, execution.ErrEvidenceConflict):
		writeError(w, r, http.StatusConflict, "attempt_evidence_conflict",
			"this attempt already exited with different evidence")
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
