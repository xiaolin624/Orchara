package api

import (
	"errors"
	"net/http"

	"orchara/platform/internal/identity"
	"orchara/platform/internal/uuid"
)

// setRolesRequest is the contract body of PUT /admin/users/{id}/roles.
type setRolesRequest struct {
	Roles []string `json:"roles"`
}

// handleSetRoles replaces a human user's roles.
//
// A service principal is rejected as a validation error, not silently ignored:
// its authority comes from a scoped API key plus the object ACL (T020), and a
// role binding on it would be a permission model that no authorizer reads.
func (s *Server) handleSetRoles(w http.ResponseWriter, r *http.Request) {
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
	var request setRolesRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}

	update, err := s.service.SetRoles(r.Context(), caller.PrincipalID, principalID, request.Roles)
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

	writeJSON(w, http.StatusOK, acceptedResponse{ID: update.PrincipalID, State: "updated"})
}
