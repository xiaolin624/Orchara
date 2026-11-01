package api

import (
	"errors"
	"net/http"
	"strings"
	"time"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/identity"
)

// createServiceAccountRequest is the contract body of
// POST /api/v1/admin/service-accounts.
type createServiceAccountRequest struct {
	DisplayName       string `json:"display_name"`
	ResponsibleUserID string `json:"responsible_user_id"`
}

// createAPIKeyRequest is the contract body of POST /api/v1/admin/api-keys.
//
// expires_at stays a string until it has been parsed, so a malformed timestamp
// is a typed 422 for the field instead of a generic JSON error.
type createAPIKeyRequest struct {
	PrincipalID string   `json:"principal_id"`
	Scopes      []string `json:"scopes"`
	ExpiresAt   string   `json:"expires_at"`
}

// apiKeyResponse is the one-time creation response: the secret is returned once
// and never stored, logged or audited.
type apiKeyResponse struct {
	ID     string `json:"id"`
	Secret string `json:"secret"`
}

// memoryListResponse is the contract's memory list envelope. F01 has no memory
// store yet (F09 owns it), so a human caller gets an honest empty result.
type memoryListResponse struct {
	Items      []any   `json:"items"`
	NextCursor *string `json:"next_cursor"`
}

// handleCreateServiceAccount creates a service principal owned by a named human.
//
// Order is fixed: authorize (session, CSRF, admin role) before the body is read,
// so a forged request cannot even be parsed. A service key cannot reach this
// endpoint: it has no roles, so the role check refuses it with 403 forbidden.
func (s *Server) handleCreateServiceAccount(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "admin")
	if !ok {
		return
	}
	var request createServiceAccountRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	principalID, err := s.service.CreateServiceAccount(
		r.Context(), caller.PrincipalID, request.DisplayName, request.ResponsibleUserID,
	)
	switch {
	case errors.Is(err, identity.ErrValidation):
		writeValidationFailure(w, r, err)
		return
	case errors.Is(err, identity.ErrPrincipalNotFound):
		writeError(w, r, http.StatusNotFound, "principal_not_found", "that principal does not exist")
		return
	case errors.Is(err, identity.ErrPrincipalDisabled):
		// The named responsible human exists but is disabled: a semantic
		// problem with the body, reported against the field that named it.
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"the responsible user is disabled", map[string]any{"field": "responsible_user_id"})
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, acceptedResponse{ID: principalID, State: "active"})
}

// handleCreateAPIKey issues one scoped key for a service principal. The secret
// is in the response only; nothing else in the platform ever sees it.
func (s *Server) handleCreateAPIKey(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "admin")
	if !ok {
		return
	}
	var request createAPIKeyRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	expiresAt, err := time.Parse(time.RFC3339, strings.TrimSpace(request.ExpiresAt))
	if err != nil {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"expires_at must be an RFC3339 timestamp", map[string]any{"field": "expires_at"})
		return
	}
	created, err := s.service.CreateAPIKey(
		r.Context(), caller.PrincipalID, request.PrincipalID, request.Scopes, expiresAt.UTC(),
	)
	switch {
	case errors.Is(err, identity.ErrPersonalScope):
		// A service account must never borrow its responsible user's personal
		// data; the refusal is its own contract code, not a generic 422.
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "personal_scope_forbidden",
			err.Error(), map[string]any{"field": "scopes"})
		return
	case errors.Is(err, identity.ErrValidation):
		writeValidationFailure(w, r, err)
		return
	case errors.Is(err, identity.ErrPrincipalNotFound):
		writeError(w, r, http.StatusNotFound, "principal_not_found", "that principal does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, apiKeyResponse{ID: created.ID, Secret: created.Secret})
}

// handleRevokeAPIKey revokes one key. It is idempotent for an existing key and
// a typed 404 for an unknown one.
func (s *Server) handleRevokeAPIKey(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "admin")
	if !ok {
		return
	}
	if _, err := s.service.RevokeAPIKey(r.Context(), caller.PrincipalID, r.PathValue("key_id")); err != nil {
		if errors.Is(err, identity.ErrAPIKeyNotFound) {
			writeError(w, r, http.StatusNotFound, "key_not_found", "that API key does not exist")
			return
		}
		s.internalError(w, r, err)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusNoContent)
}

// handleListMemories is the F01 honesty boundary for personal memory.
//
// F09 owns the memory store. Until then a human caller gets an authorized,
// empty list (items [], next_cursor null) rather than fabricated data or a 501.
// A service credential is refused before the limit is even parsed and before any
// storage could be touched: personal long-term memory belongs to humans only
// (IMPLEMENTATION-DETAILS §1). The check covers both a service principal with a
// session credential and any API-key credential.
func (s *Server) handleListMemories(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if caller.Kind == "service" || caller.Method == identity.AuthMethodAPIKey {
		writeError(w, r, http.StatusForbidden, "personal_scope_forbidden",
			"personal memory belongs to human users; a service account cannot read it")
		return
	}
	query := r.URL.Query()
	// A present-but-blank limit is a malformed value, not an omitted one.
	if query.Has("limit") && strings.TrimSpace(query.Get("limit")) == "" {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
		return
	}
	if _, err := catalog.ParseLimit(query.Get("limit")); err != nil {
		// Only limit is meaningful in F01; cursor and application_id are
		// accepted by the contract but ignored until F09 defines the store.
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
		return
	}
	writeJSON(w, http.StatusOK, memoryListResponse{Items: []any{}, NextCursor: nil})
}
