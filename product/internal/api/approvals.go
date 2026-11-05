package api

// F06/T050: the approval protocol.
//
//	POST /internal/v1/runs/{run_id}/approvals      prepareApproval (worker mTLS)
//	GET  /api/v1/approvals                         listApprovals (approver only)
//	GET  /api/v1/approvals/{approval_id}           getApproval (approver or admin)
//	POST /api/v1/approvals/{approval_id}/decision  decideApproval (approver only)
//
// The handlers contain no SQL and no proposal logic: every field comes from
// internal/approvals, which builds the proposal from the run/operation/policy
// rows. The worker's `proposal` body member is accepted for schema compatibility
// and deliberately ignored.

import (
	"encoding/json"
	"net/http"
	"time"

	"orchara/platform/internal/approvals"
	"orchara/platform/internal/identity"
)

// approvalView is exactly the contract's Approval schema.
type approvalView struct {
	ID                 string `json:"id"`
	RunID              string `json:"run_id"`
	State              string `json:"state"`
	ProposalArtifactID string `json:"proposal_artifact_id"`
	ParamsSHA256       string `json:"params_sha256"`
	ExpiresAt          string `json:"expires_at"`
}

func approvalResponse(approval *approvals.Approval) approvalView {
	return approvalView{
		ID:                 approval.ID,
		RunID:              approval.RunID,
		State:              approval.State,
		ProposalArtifactID: approval.ProposalArtifactID,
		ParamsSHA256:       approval.ParamsSHA256,
		ExpiresAt:          approval.ExpiresAt.UTC().Format(time.RFC3339),
	}
}

// internalPrepareApprovalRequest is the contract's prepareApproval body. The
// `proposal` member is required by the schema but never trusted: the server
// builds the real proposal from the operation/policy/manifest facts.
type internalPrepareApprovalRequest struct {
	AttemptToken string         `json:"attempt_token"`
	LogicalKey   string         `json:"logical_key"`
	OperationID  string         `json:"operation_id"`
	Proposal     map[string]any `json:"proposal"`
}

func (s *Server) handleInternalPrepareApproval(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleWorker) {
		return
	}
	var request internalPrepareApprovalRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	approval, err := s.approvals.Prepare(r.Context(), approvals.PrepareRequest{
		RunID:        r.PathValue("run_id"),
		AttemptToken: request.AttemptToken,
		LogicalKey:   request.LogicalKey,
		OperationID:  request.OperationID,
	})
	if !s.writeApprovalError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusCreated, approvalResponse(approval))
}

// internalPrepareNodeApprovalRequest is the non-contract F04B/T087 body: the
// sealed node identity, the display title and expiry from the node config, and
// the run-resolved proposal object. No decision and no capability ride here.
type internalPrepareNodeApprovalRequest struct {
	AttemptToken   string          `json:"attempt_token"`
	NodeID         string          `json:"node_id"`
	Title          string          `json:"title"`
	ExpiresSeconds int             `json:"expires_seconds"`
	Proposal       json.RawMessage `json:"proposal"`
}

// nodeApprovalView is the internal node-approval answer the executor reads. It is
// a superset of the contract Approval (decided_at is needed for the node output);
// it is non-contract and never reachable from the public listener.
type nodeApprovalView struct {
	ID                 string  `json:"id"`
	State              string  `json:"state"`
	ExpiresAt          string  `json:"expires_at"`
	ParamsSHA256       string  `json:"params_sha256"`
	ProposalArtifactID string  `json:"proposal_artifact_id"`
	DecidedAt          *string `json:"decided_at"`
}

func nodeApprovalResponse(approval *approvals.Approval) nodeApprovalView {
	view := nodeApprovalView{
		ID:                 approval.ID,
		State:              approval.State,
		ExpiresAt:          approval.ExpiresAt.UTC().Format(time.RFC3339),
		ParamsSHA256:       approval.ParamsSHA256,
		ProposalArtifactID: approval.ProposalArtifactID,
	}
	if approval.DecidedAt != nil {
		decided := approval.DecidedAt.UTC().Format(time.RFC3339)
		view.DecidedAt = &decided
	}
	return view
}

func (s *Server) handleInternalPrepareNodeApproval(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleWorker) {
		return
	}
	var request internalPrepareNodeApprovalRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	approval, err := s.approvals.PrepareNode(r.Context(), approvals.PrepareNodeRecord{
		RunID:          r.PathValue("run_id"),
		AttemptToken:   request.AttemptToken,
		NodeID:         request.NodeID,
		Title:          request.Title,
		ExpiresSeconds: request.ExpiresSeconds,
		Proposal:       request.Proposal,
	})
	if !s.writeApprovalError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusCreated, nodeApprovalResponse(approval))
}

func (s *Server) handleGetApproval(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	approval, err := s.approvals.Get(
		r.Context(), caller.PrincipalID, r.PathValue("approval_id"), caller.HasRole(identity.RoleAdmin),
	)
	if !s.writeApprovalError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, approvalResponse(approval))
}

func (s *Server) handleListApprovals(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	limit := 0
	if raw := r.URL.Query().Get("limit"); raw != "" {
		value, err := parseQueryInt(raw)
		if err != nil {
			writeError(w, r, http.StatusUnprocessableEntity, "validation_failed",
				"limit must be an integer between 1 and 100")
			return
		}
		limit = value
	}
	items, next, err := s.approvals.List(
		r.Context(), caller.PrincipalID, limit, r.URL.Query().Get("cursor"),
	)
	if !s.writeApprovalError(w, r, err) {
		return
	}
	response := struct {
		Items      []approvalView `json:"items"`
		NextCursor *string        `json:"next_cursor"`
	}{Items: make([]approvalView, 0, len(items))}
	for index := range items {
		response.Items = append(response.Items, approvalResponse(&items[index]))
	}
	if next != "" {
		response.NextCursor = &next
	}
	writeJSON(w, http.StatusOK, response)
}

type approvalDecisionRequest struct {
	Decision     string `json:"decision"`
	ParamsSHA256 string `json:"params_sha256"`
}

func (s *Server) handleDecideApproval(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationWrite); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	// The contract makes Idempotency-Key required; a missing one is a located
	// 422 before any row is touched.
	idempotencyKey := r.Header.Get("Idempotency-Key")
	if idempotencyKey == "" {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"Idempotency-Key is required", map[string]any{"field": "Idempotency-Key"})
		return
	}
	var request approvalDecisionRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	approval, err := s.approvals.Decide(r.Context(), approvals.DecisionRequest{
		ApprovalID:     r.PathValue("approval_id"),
		CallerID:       caller.PrincipalID,
		CallerIsAdmin:  caller.HasRole(identity.RoleAdmin),
		Decision:       request.Decision,
		ParamsSHA256:   request.ParamsSHA256,
		IdempotencyKey: idempotencyKey,
	})
	if !s.writeApprovalError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, approvalResponse(approval))
}

// writeApprovalError maps the approvals vocabulary onto the contract envelope.
func (s *Server) writeApprovalError(w http.ResponseWriter, r *http.Request, err error) bool {
	if err == nil {
		return true
	}
	if typed, ok := approvals.AsError(err); ok {
		var details map[string]any
		if typed.Field != "" {
			details = map[string]any{"field": typed.Field}
		}
		writeErrorWithDetails(w, r, typed.Status, typed.Code, typed.Message, details)
		return false
	}
	s.internalError(w, r, err)
	return false
}

// parseQueryInt is the small strict integer parser the list query needs; a
// non-integer is a 422, never a silently defaulted page.
func parseQueryInt(raw string) (int, error) {
	value := 0
	if raw == "" {
		return 0, nil
	}
	for _, char := range raw {
		if char < '0' || char > '9' {
			return 0, errNotAnInteger
		}
		value = value*10 + int(char-'0')
		if value > 1_000_000 {
			return 0, errNotAnInteger
		}
	}
	if value < 1 {
		return 0, errNotAnInteger
	}
	return value, nil
}

type notAnInteger struct{}

func (notAnInteger) Error() string { return "not an integer" }

var errNotAnInteger = notAnInteger{}
