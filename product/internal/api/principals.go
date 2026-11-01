package api

import (
	"errors"
	"net/http"

	"orchara/platform/internal/identity"
	"orchara/platform/internal/uuid"
)

// updatePrincipalRequest is the contract body of
// PATCH /api/v1/admin/principals/{principal_id}.
//
// disabled is a pointer on purpose: the Go zero value of bool is false, so a
// body without the field would otherwise silently enable the principal. A
// missing field is a typed 422 instead.
type updatePrincipalRequest struct {
	Disabled *bool  `json:"disabled"`
	Reason   string `json:"reason"`
}

// handleUpdatePrincipal disables or enables one principal.
//
// Order is the same as every other admin write: authorize (session + CSRF +
// admin role) before the path or body is interpreted, then the path shape, then
// the body. A malformed or unknown principal id is one 404 (no enumeration).
// The service owns the transaction: disable revokes sessions and API keys and
// bumps the policy epoch; enable clears disabled_at and bumps the epoch.
func (s *Server) handleUpdatePrincipal(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "admin")
	if !ok {
		return
	}
	principalID := r.PathValue("principal_id")
	if !uuid.Valid(principalID) {
		// A malformed id cannot name a principal; it is the same not-found as an
		// id that does not exist.
		writeError(w, r, http.StatusNotFound, "principal_not_found", "that principal does not exist")
		return
	}
	var request updatePrincipalRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	if request.Disabled == nil {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"disabled is required", map[string]any{"field": "disabled"})
		return
	}

	change, err := s.service.SetDisabled(
		r.Context(), caller.PrincipalID, principalID, *request.Disabled, request.Reason,
	)
	switch {
	case errors.Is(err, identity.ErrValidation):
		writeValidationFailure(w, r, err)
		return
	case errors.Is(err, identity.ErrLastAdmin):
		writeError(w, r, http.StatusConflict, "last_admin",
			"at least one enabled human administrator must remain")
		return
	case errors.Is(err, identity.ErrPrincipalNotFound):
		writeError(w, r, http.StatusNotFound, "principal_not_found", "that principal does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, acceptedResponse{ID: change.PrincipalID, State: change.State})
}
