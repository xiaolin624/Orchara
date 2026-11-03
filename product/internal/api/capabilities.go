package api

// F06/T041: the run-scoped capability protocol.
//
//	POST /internal/v1/runs/{run_id}/capabilities          invokeCapability (contract)
//	POST /internal/v1/runs/{run_id}/capability-results    non-contract tool result report
//	GET  /api/v1/runs/{run_id}/capabilities               owner-only ledger read
//
// The internal routes live on the mTLS listener and are worker-only: the worker
// certificate, the run in the path and the live attempt token are all required.
// There is no public capability endpoint. The result endpoint is the documented
// non-contract bridge T042/T043 report through; it is not part of openapi.json
// and follows the same attempt-token rule.
//
// The handlers write no SQL and no provider logic: they decode the contract
// bodies strictly, call internal/capabilities and map its typed errors.

import (
	"errors"
	"net/http"

	"orchara/platform/internal/capabilities"
	"orchara/platform/internal/identity"
)

type internalCapabilityRequest struct {
	AttemptToken      string         `json:"attempt_token"`
	OperationID       string         `json:"operation_id"`
	LogicalKey        string         `json:"logical_key"`
	CapabilityVersion string         `json:"capability_version"`
	Params            map[string]any `json:"params"`
	CapabilityKind    string         `json:"capability_kind"`
}

// internalCapabilityResponse is exactly the contract's CapabilityResult.
type internalCapabilityResponse struct {
	OperationID       string         `json:"operation_id"`
	State             string         `json:"state"`
	ReceiptArtifactID *string        `json:"receipt_artifact_id"`
	ApprovalID        *string        `json:"approval_id"`
	Data              map[string]any `json:"data,omitempty"`
}

func capabilityResponse(result *capabilities.Result) internalCapabilityResponse {
	return internalCapabilityResponse{
		OperationID:       result.OperationID,
		State:             result.State,
		ReceiptArtifactID: result.ReceiptArtifactID,
		ApprovalID:        result.ApprovalID,
		Data:              result.Data,
	}
}

func (s *Server) handleInternalCapability(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleWorker) {
		return
	}
	var request internalCapabilityRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	result, err := s.capabilities.Invoke(r.Context(), r.PathValue("run_id"), capabilities.Input{
		AttemptToken:      request.AttemptToken,
		OperationID:       request.OperationID,
		LogicalKey:        request.LogicalKey,
		CapabilityVersion: request.CapabilityVersion,
		CapabilityKind:    request.CapabilityKind,
		Params:            request.Params,
	})
	if !s.writeCapabilityError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, capabilityResponse(result))
}

// internalCapabilityResultRequest is the non-contract result report body. The
// attempt token is required: a tool result is only accepted from the live
// attempt that received the grant.
type internalCapabilityResultRequest struct {
	AttemptToken     string         `json:"attempt_token"`
	OperationID      string         `json:"operation_id"`
	State            string         `json:"state"`
	ResultArtifactID string         `json:"result_artifact_id,omitempty"`
	Data             map[string]any `json:"data,omitempty"`
	ErrorCode        string         `json:"error_code,omitempty"`
}

type internalCapabilityResultResponse struct {
	OperationID       string  `json:"operation_id"`
	State             string  `json:"state"`
	ReceiptArtifactID *string `json:"receipt_artifact_id"`
	ErrorCode         *string `json:"error_code,omitempty"`
}

func (s *Server) handleInternalCapabilityResult(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleWorker) {
		return
	}
	var request internalCapabilityResultRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	result, err := s.capabilities.ReportResult(r.Context(), r.PathValue("run_id"), capabilities.ResultReport{
		AttemptToken:     request.AttemptToken,
		OperationID:      request.OperationID,
		State:            request.State,
		ResultArtifactID: request.ResultArtifactID,
		Data:             request.Data,
		ErrorCode:        request.ErrorCode,
	})
	if !s.writeCapabilityError(w, r, err) {
		return
	}
	response := internalCapabilityResultResponse{
		OperationID:       result.OperationID,
		State:             result.State,
		ReceiptArtifactID: result.ReceiptArtifactID,
	}
	if result.Data != nil {
		if code, ok := result.Data["error_code"].(string); ok {
			response.ErrorCode = &code
		}
	}
	writeJSON(w, http.StatusOK, response)
}

// runCapabilityLedgerResponse is the non-contract owner read shape. The service
// projection already excludes params and receipt bodies.
type runCapabilityLedgerResponse struct {
	Items []capabilities.LedgerOperation `json:"items"`
}

func (s *Server) handleRunCapabilities(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	items, err := s.capabilities.Ledger(r.Context(), caller.PrincipalID, r.PathValue("run_id"))
	if !s.writeCapabilityError(w, r, err) {
		return
	}
	if items == nil {
		items = []capabilities.LedgerOperation{}
	}
	writeJSON(w, http.StatusOK, runCapabilityLedgerResponse{Items: items})
}

// writeCapabilityError maps the capability package's typed errors to the
// contract vocabulary. Provider payloads never reach the response body.
func (s *Server) writeCapabilityError(w http.ResponseWriter, r *http.Request, err error) bool {
	if err == nil {
		return true
	}
	if capabilityError, ok := capabilities.AsError(err); ok {
		var details map[string]any
		if capabilityError.Field != "" {
			details = map[string]any{"field": capabilityError.Field}
		}
		writeErrorWithDetails(w, r, capabilityError.Status, capabilityError.Code,
			capabilityError.Message, details)
		return false
	}
	// A successful call converted to the error interface is a typed nil; the
	// handler continues and writes the success body.
	var typedNil *capabilities.Error
	if errors.As(err, &typedNil) {
		return typedNil == nil
	}
	s.internalError(w, r, err)
	return false
}
