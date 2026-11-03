package api

// F06/T048: the administrator connector registry.
//
//	POST /api/v1/admin/connectors                 register one immutable policy
//	GET  /api/v1/admin/connectors                 the queryable registry
//	POST /api/v1/admin/connectors/{id}/revoke     revoke (never delete)
//
// These routes are admin-only and CSRF-protected by Server.authorize, exactly
// like the model registration routes. The response never carries a credential:
// only the deployer reference and the header *name* are policy content, and the
// service holds no value to leak. The policy body is sealed as canonical JCS
// bytes by internal/connectors; this file writes no SQL and does no HTTP egress.

import (
	"net/http"

	"orchara/platform/internal/connectors"
)

func (s *Server) requireConnectors(w http.ResponseWriter, r *http.Request) bool {
	if s.connectors == nil {
		writeError(w, r, http.StatusServiceUnavailable, "connector_registry_unconfigured",
			"the connector registry is not configured in this process")
		return false
	}
	return true
}

func (s *Server) writeConnectorFailure(w http.ResponseWriter, r *http.Request, err error) {
	if typed, ok := connectors.AsError(err); ok {
		var details map[string]any
		if typed.Field != "" {
			details = map[string]any{"field": typed.Field}
		}
		for key, value := range typed.Details {
			if details == nil {
				details = map[string]any{}
			}
			details[key] = value
		}
		writeErrorWithDetails(w, r, typed.Status, typed.Code, typed.Message, details)
		return
	}
	writeError(w, r, http.StatusInternalServerError, "internal_error",
		"the connector request could not be completed")
}

func (s *Server) handleRegisterConnector(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "admin")
	if !ok {
		return
	}
	if !s.requireConnectors(w, r) {
		return
	}
	var input connectors.Input
	if err := decodeJSON(w, r, &input); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	policy, err := s.connectors.Register(r.Context(), caller.PrincipalID, input)
	if err != nil {
		s.writeConnectorFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, policy)
}

type connectorListResponse struct {
	Items []connectors.Policy `json:"items"`
}

func (s *Server) handleListConnectors(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.authorize(w, r, "admin"); !ok {
		return
	}
	if !s.requireConnectors(w, r) {
		return
	}
	policies, err := s.connectors.List(r.Context())
	if err != nil {
		writeError(w, r, http.StatusInternalServerError, "internal_error",
			"the connector registry could not be read")
		return
	}
	writeJSON(w, http.StatusOK, connectorListResponse{Items: policies})
}

func (s *Server) handleRevokeConnector(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.authorize(w, r, "admin"); !ok {
		return
	}
	if !s.requireConnectors(w, r) {
		return
	}
	policy, err := s.connectors.Revoke(r.Context(), r.PathValue("connector_id"))
	if err != nil {
		s.writeConnectorFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, policy)
}
