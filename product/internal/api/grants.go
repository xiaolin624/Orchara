package api

import (
	"errors"
	"net/http"
	"strings"
	"time"

	"orchara/platform/internal/policy"
)

// grantRequest is the contract body of POST /api/v1/admin/grants. expires_at is
// kept as a string until it has been parsed, so a malformed timestamp is a
// typed 422 for the field instead of a generic JSON error.
type grantRequest struct {
	PrincipalID  string  `json:"principal_id"`
	ResourceType string  `json:"resource_type"`
	ResourceID   string  `json:"resource_id"`
	Action       string  `json:"action"`
	ExpiresAt    *string `json:"expires_at"`
}

// grantParams validates the shape of one grant request and returns the service
// parameters. It is a pure function (no database, no request) so the validation
// matrix can be tested directly.
func grantParams(request grantRequest, now time.Time) (policy.GrantParams, error) {
	params := policy.GrantParams{
		PrincipalID:  strings.TrimSpace(request.PrincipalID),
		ResourceType: strings.TrimSpace(request.ResourceType),
		ResourceID:   strings.TrimSpace(request.ResourceID),
		Action:       strings.TrimSpace(request.Action),
	}
	if request.ExpiresAt != nil {
		expiresAt, err := time.Parse(time.RFC3339, strings.TrimSpace(*request.ExpiresAt))
		if err != nil {
			return params, &policy.ValidationError{
				Field:   "expires_at",
				Message: "expires_at must be an RFC3339 timestamp",
			}
		}
		parsed := expiresAt.UTC()
		params.ExpiresAt = &parsed
	}
	if err := policy.ValidateGrant(params, now); err != nil {
		return params, err
	}
	return params, nil
}

// handleGrant creates one resource grant.
//
// Order is fixed: authorize (session, CSRF, admin role) before the body is read,
// so a forged request cannot even be parsed, and then the grant is written,
// epoch-bumped and audited in one transaction.
func (s *Server) handleGrant(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "admin")
	if !ok {
		return
	}
	var request grantRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	params, err := grantParams(request, s.policy.Clock())
	if err != nil {
		writeValidationFailure(w, r, err)
		return
	}

	result, err := s.policy.Grant(r.Context(), caller.PrincipalID, params)
	switch {
	case errors.Is(err, policy.ErrValidation):
		writeValidationFailure(w, r, err)
		return
	case errors.Is(err, policy.ErrPrincipalNotFound):
		writeError(w, r, http.StatusNotFound, "principal_not_found", "that principal does not exist")
		return
	case errors.Is(err, policy.ErrPrincipalDisabled):
		// The grantee exists but is disabled: a semantic problem with the body,
		// reported against the field that named it.
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"the principal is disabled", map[string]any{"field": "principal_id"})
		return
	case errors.Is(err, policy.ErrResourceNotFound):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that resource does not exist")
		return
	case errors.Is(err, policy.ErrGrantExists):
		writeError(w, r, http.StatusConflict, "grant_exists", "that grant already exists")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}

	writeJSON(w, http.StatusCreated, acceptedResponse{ID: result.ID, State: "granted"})
}

// handleRevokeGrant deletes one grant and makes the deletion visible on the next
// decision by bumping the policy epoch in the same transaction.
func (s *Server) handleRevokeGrant(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "admin")
	if !ok {
		return
	}
	if _, err := s.policy.Revoke(r.Context(), caller.PrincipalID, r.PathValue("grant_id")); err != nil {
		if errors.Is(err, policy.ErrGrantNotFound) {
			writeError(w, r, http.StatusNotFound, "grant_not_found", "that grant does not exist")
			return
		}
		s.internalError(w, r, err)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusNoContent)
}

// writeValidationFailure writes the one 422 shape all handlers use, including
// the offending field when the error is typed.
func writeValidationFailure(w http.ResponseWriter, r *http.Request, err error) {
	writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed", err.Error(),
		validationDetails(err))
}
