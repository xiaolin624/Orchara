package api

// F06/T051: reconcileOperation.
//
//	POST /api/v1/operations/{operation_id}/reconcile
//	body {conclusion, evidence_artifact_id}
//	Idempotency-Key required
//
// The contract answer is Accepted {id, state}: the operation id and its settled
// state. The owner ledger read carries the reconciliation facts and the physical
// attempt count the UI uses to show that nothing was resent. The handler only
// decodes strictly and maps the capability package's typed errors.

import (
	"net/http"
	"strings"

	"orchara/platform/internal/capabilities"
	"orchara/platform/internal/identity"
)

type reconcileRequest struct {
	Conclusion         string `json:"conclusion"`
	EvidenceArtifactID string `json:"evidence_artifact_id"`
}

// reconcileAcceptedResponse is the contract's Accepted shape (additionalProperties false).
type reconcileAcceptedResponse struct {
	ID    string `json:"id"`
	State string `json:"state"`
}

func (s *Server) handleReconcileOperation(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationWrite); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	idempotencyKey := strings.TrimSpace(r.Header.Get("Idempotency-Key"))
	if idempotencyKey == "" {
		// The contract makes the header required; a missing one is a located 422
		// before any row is touched (same vocabulary as the T050 decision path).
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"Idempotency-Key is required", map[string]any{"field": "Idempotency-Key"})
		return
	}
	var request reconcileRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	result, err := s.capabilities.ReconcileOperation(r.Context(), caller.PrincipalID,
		r.PathValue("operation_id"),
		capabilities.ReconcileOperationRequest{
			Conclusion:         request.Conclusion,
			EvidenceArtifactID: request.EvidenceArtifactID,
		}, idempotencyKey)
	if !s.writeCapabilityError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, reconcileAcceptedResponse{ID: result.OperationID, State: result.State})
}
