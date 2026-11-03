package api

// F03/T029+T031: the internal execution-protocol operations.
//
//	POST /internal/v1/jobs/claim                              claimJob
//	POST /internal/v1/runs/{run_id}/heartbeat                 heartbeat
//	POST /internal/v1/runs/{run_id}/result                    submitWorkerResult
//	POST /internal/v1/runs/{run_id}/waiting-resolution        T035 synthetic bridge
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
	"time"

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

// internalWaitingResolutionRequest is the documented non-contract T035 bridge
// body. The shape is closed: an approval carries a reference, a children
// resolution carries a child_run_id, and nothing else (no Command payload, thread
// id or user identifier). Unknown fields are refused by decodeJSON.
type internalWaitingResolutionRequest struct {
	Kind       string `json:"kind"`
	Outcome    string `json:"outcome"`
	Reference  string `json:"reference"`
	ChildRunID string `json:"child_run_id"`
}

// internalWaitingResolutionResponse is the bridge's result: the stored safe
// decision and (once the exit fact is present too) the one resume job.
type internalWaitingResolutionResponse struct {
	RunID       string  `json:"run_id"`
	Kind        string  `json:"kind"`
	Outcome     string  `json:"outcome"`
	DecisionAt  string  `json:"decision_at"`
	ResumeJobID *string `json:"resume_job_id"`
}

// handleInternalWaitingResolution stores the synthetic approval/child result.
//
// F06 (approvals) and F07 (children) own the real sources; until they exist this
// supervisor-only mTLS endpoint is the documented bridge (recorded in the T035
// report). It can only advance a Run that is actually waiting for that kind, it
// stores the first decision once (an identical replay is 200, a different one is
// 409 decision_conflict) and it never accepts a worker-supplied Command.
func (s *Server) handleInternalWaitingResolution(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleSupervisor) {
		return
	}
	var request internalWaitingResolutionRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	outcome, err := s.execution.ResolveWaiting(r.Context(), r.PathValue("run_id"), execution.WaitingResolution{
		Kind:       request.Kind,
		Outcome:    request.Outcome,
		Reference:  request.Reference,
		ChildRunID: request.ChildRunID,
	})
	if !s.writeExecutionError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, internalWaitingResolutionResponse{
		RunID:       outcome.RunID,
		Kind:        outcome.Kind,
		Outcome:     outcome.Outcome,
		DecisionAt:  outcome.DecisionAt.UTC().Format(time.RFC3339Nano),
		ResumeJobID: outcome.ResumeJobID,
	})
}

// internalUnknownOperationRequest is the documented non-contract T036 bridge body
// for recording an unresolved external write. The reason is a safe closed code;
// no provider body, URL or credential is accepted.
type internalUnknownOperationRequest struct {
	AttemptToken string `json:"attempt_token"`
	OperationID  string `json:"operation_id"`
	Reason       string `json:"reason"`
}

// internalUnknownOperationResolveRequest is the resolve bridge body: the attempt
// token binds the call to the Run (a worker must still be live, a supervisor may
// resolve after the exit fact).
type internalUnknownOperationResolveRequest struct {
	AttemptToken string `json:"attempt_token"`
}

// internalUnknownOperationResponse is the bridge's answer. It carries the safe
// reason and the resolved fact, never a provider payload.
type internalUnknownOperationResponse struct {
	RunID       string  `json:"run_id"`
	OperationID string  `json:"operation_id"`
	Reason      string  `json:"reason"`
	Resolved    bool    `json:"resolved"`
	ResolvedAt  *string `json:"resolved_at"`
	RunState    string  `json:"run_state"`
}

func unknownOperationResponse(operation *execution.UnknownOperation) internalUnknownOperationResponse {
	response := internalUnknownOperationResponse{
		RunID:       operation.RunID,
		OperationID: operation.OperationID,
		Reason:      operation.Reason,
		Resolved:    operation.Resolved,
		RunState:    operation.RunState,
	}
	if operation.ResolvedAt != nil {
		value := operation.ResolvedAt.UTC().Format(time.RFC3339Nano)
		response.ResolvedAt = &value
	}
	return response
}

// handleInternalUnknownOperation records one unresolved external write for a Run.
//
// F06 owns the real operations/receipts; this supervisor|worker mTLS endpoint is
// the documented bridge (recorded in the T036 report). It requires the Run's live
// attempt token, stores a safe reason code once, is idempotent for the same
// (run, operation_id) and refuses a different reason (409 event_conflict). It
// never commits a Run transition by itself: the finalizer reads the ledger when
// result+exit meet.
func (s *Server) handleInternalUnknownOperation(w http.ResponseWriter, r *http.Request) {
	if !requireAnyInternalRole(w, r, InternalRoleSupervisor, InternalRoleWorker) {
		return
	}
	var request internalUnknownOperationRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	operation, err := s.execution.RecordUnknownOperation(
		r.Context(),
		r.PathValue("run_id"),
		execution.UnknownOperationRequest{
			AttemptToken: request.AttemptToken,
			OperationID:  request.OperationID,
			Reason:       request.Reason,
		},
	)
	if !s.writeExecutionError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, unknownOperationResponse(operation))
}

// handleInternalResolveUnknownOperation marks one unknown operation resolved once.
// A worker must still hold the live lease; a supervisor may act after the exit
// fact. Resolving the last unknown of a cancel-requested Run re-invokes the one
// finalizer so the Run can become cancelled exactly once; a terminal Run is never
// revived (409 run_terminal).
func (s *Server) handleInternalResolveUnknownOperation(w http.ResponseWriter, r *http.Request) {
	if !requireAnyInternalRole(w, r, InternalRoleSupervisor, InternalRoleWorker) {
		return
	}
	var request internalUnknownOperationResolveRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	operation, err := s.execution.ResolveUnknownOperation(
		r.Context(),
		r.PathValue("run_id"),
		r.PathValue("operation_id"),
		request.AttemptToken,
		InternalRole(r.Context()),
	)
	if !s.writeExecutionError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, unknownOperationResponse(operation))
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
	case errors.Is(err, execution.ErrUnknownOperationNotFound):
		// F03/T036: a foreign Run's operation answers exactly like a missing one.
		writeError(w, r, http.StatusNotFound, "resource_not_found",
			"that unknown operation does not exist")
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
	case errors.Is(err, execution.ErrDecisionConflict):
		// F03/T035: the first stored approval/child result is authoritative; a
		// different resolution for the same wait is never silently replaced.
		writeError(w, r, http.StatusConflict, execution.CodeDecisionConflict,
			"a different waiting resolution is already stored")
		return false
	case executionWaitingChildFailure(err) != nil:
		// A synthetic children resolution that names a foreign/live/missing child is
		// refused before anything is stored: it cannot resume (or fail) the parent.
		writeErrorWithDetails(w, r, http.StatusConflict, "run_state_conflict",
			err.Error(), map[string]any{"reason": executionWaitingChildFailure(err).Reason})
		return false
	case errors.Is(err, execution.ErrRunTerminal):
		writeError(w, r, http.StatusConflict, "run_terminal",
			"the run is already terminal; a late result never revives it")
		return false
	case errors.Is(err, execution.ErrRunStateConflict):
		writeError(w, r, http.StatusConflict, "run_state_conflict",
			"the run is not executing; waiting/finalizing/cancelling/reconciliation permit only heartbeat, exit and receipt/query calls")
		return false
	case errors.Is(err, execution.ErrIdentityMismatch):
		writeError(w, r, http.StatusConflict, "execution_identity_mismatch",
			"the exit evidence does not match the claimed protocol/manifest/image identity")
		return false
	case errors.Is(err, execution.ErrDatabaseWritesInFlight):
		// F03 exit guard: the recorded checkpoint connection still has an active
		// transaction, so the old execution's writes are not drained and no terminal
		// state may be committed yet. Nothing is written; the supervisor retries.
		writeError(w, r, http.StatusConflict, "database_writes_in_flight",
			"a checkpoint database connection still has an open transaction; the run is not finalized")
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

// executionWaitingChildFailure locates a refused synthetic children resolution
// (T035): the referenced Run is missing, not this parent's child, or not terminal.
func executionWaitingChildFailure(err error) *execution.WaitingChildError {
	var child *execution.WaitingChildError
	if errors.As(err, &child) {
		return child
	}
	return nil
}
