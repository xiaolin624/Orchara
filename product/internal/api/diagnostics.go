package api

// F11/T073: user-scoped diagnostic grants.
//
//	POST /api/v1/diagnostic-grants                    grantDiagnostics (contract)
//	DELETE /api/v1/diagnostic-grants/{grant_id}       revokeDiagnostics (contract)
//	GET  /api/v1/diagnostic-grants                    metadata-only list (non-contract)
//	GET  /api/v1/diagnostic-grants/{grant_id}/content grantee-only content door (non-contract)
//
// The two contract operations are cookie-session only and require CSRF, exactly
// like the personal-memory surface: a diagnostic grant is the user's own consent,
// so a service credential has no authority over it (403 personal_scope_forbidden,
// the same documented deviation as ADR 0009 §1). The run owner grants; the
// grantee must be a different, enabled administrator.
//
// The list and the content read are documented non-contract extensions (ADR
// 0028). The list returns ids, purposes, object ids and timestamps only. The
// content read is the only code path in the platform through which an
// administrator can read another user's run content, and it is audited before a
// byte is served.

import (
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"orchara/platform/internal/diagnostics"
	"orchara/platform/internal/identity"
)

// diagnosticGrantRequest is the contract body of POST /api/v1/diagnostic-grants.
// expires_at is parsed here so a malformed timestamp is a located 422 for the
// field instead of a generic JSON error.
type diagnosticGrantRequest struct {
	RunID     string   `json:"run_id"`
	GranteeID string   `json:"grantee_id"`
	Purposes  []string `json:"purposes"`
	ExpiresAt string   `json:"expires_at"`
}

// diagnosticGrantView is the metadata projection of one stored grant. It has no
// content field on purpose.
type diagnosticGrantView struct {
	ID          string   `json:"id"`
	State       string   `json:"state"`
	Direction   string   `json:"direction"`
	RunID       string   `json:"run_id"`
	OwnerID     string   `json:"owner_id"`
	GranteeID   string   `json:"grantee_id"`
	Purposes    []string `json:"purposes"`
	ObjectScope []string `json:"object_scope"`
	ExpiresAt   string   `json:"expires_at"`
	CreatedAt   string   `json:"created_at"`
	RevokedAt   *string  `json:"revoked_at"`
}

type diagnosticGrantList struct {
	Items []diagnosticGrantView `json:"items"`
}

func viewOfDiagnosticGrant(grant diagnostics.Grant) diagnosticGrantView {
	view := diagnosticGrantView{
		ID:          grant.ID,
		State:       grant.State,
		Direction:   grant.Direction,
		RunID:       grant.RunID,
		OwnerID:     grant.OwnerID,
		GranteeID:   grant.GranteeID,
		Purposes:    grant.Purposes,
		ObjectScope: grant.ObjectScope,
		ExpiresAt:   grant.ExpiresAt.UTC().Format(time.RFC3339),
		CreatedAt:   grant.CreatedAt.UTC().Format(time.RFC3339),
	}
	if view.Purposes == nil {
		view.Purposes = []string{}
	}
	if view.ObjectScope == nil {
		view.ObjectScope = []string{}
	}
	if grant.RevokedAt != nil {
		value := grant.RevokedAt.UTC().Format(time.RFC3339)
		view.RevokedAt = &value
	}
	return view
}

// requireHumanDiagnostics enforces the personal-consent boundary: only a human
// session may grant, revoke, list or read under a diagnostic grant. A service
// account (API key or service principal) is refused before any row is read.
func (s *Server) requireHumanDiagnostics(
	w http.ResponseWriter, r *http.Request, caller *identity.Identity,
) bool {
	if caller.Kind == "service" || caller.Method == identity.AuthMethodAPIKey {
		writeError(w, r, http.StatusForbidden, "personal_scope_forbidden",
			"diagnostic grants belong to human users; a service account cannot grant, revoke, list or read them")
		return false
	}
	return true
}

// handleCreateDiagnosticGrant implements the contract's grantDiagnostics.
func (s *Server) handleCreateDiagnosticGrant(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireHumanDiagnostics(w, r, caller) {
		return
	}
	var request diagnosticGrantRequest
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
	params := diagnostics.CreateParams{
		RunID:       request.RunID,
		GranteeID:   request.GranteeID,
		Purposes:    request.Purposes,
		ExpiresAt:   expiresAt,
		ObjectScope: diagnosticObjectScopeHeader(r.Header.Get("X-Diagnostic-Object-Scope")),
	}
	grant, err := s.diagnostics.Create(r.Context(), caller.PrincipalID, params)
	if err != nil {
		s.writeDiagnosticFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, acceptedResponse{ID: grant.ID, State: "granted"})
}

// diagnosticObjectScopeHeader parses the documented non-contract header. An
// absent/blank header means "derive the run's current objects once"; an explicit
// list is validated against the run inside the service.
func diagnosticObjectScopeHeader(raw string) []string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil
	}
	parts := strings.Split(raw, ",")
	scope := make([]string, 0, len(parts))
	for _, part := range parts {
		value := strings.TrimSpace(part)
		if value != "" {
			scope = append(scope, value)
		}
	}
	return scope
}

// handleRevokeDiagnosticGrant implements the contract's revokeDiagnostics.
// A second revoke is an idempotent 204 with no second audit row.
func (s *Server) handleRevokeDiagnosticGrant(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireHumanDiagnostics(w, r, caller) {
		return
	}
	if _, err := s.diagnostics.Revoke(
		r.Context(), caller.PrincipalID, r.PathValue("grant_id"),
	); err != nil {
		s.writeDiagnosticFailure(w, r, err)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusNoContent)
}

// handleListDiagnosticGrants is the documented non-contract metadata read: the
// owner sees their grants, the grantee sees grants naming them. Content never
// appears here.
func (s *Server) handleListDiagnosticGrants(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireHumanDiagnostics(w, r, caller) {
		return
	}
	grants, err := s.diagnostics.List(r.Context(), caller.PrincipalID)
	if err != nil {
		s.writeDiagnosticFailure(w, r, err)
		return
	}
	views := make([]diagnosticGrantView, 0, len(grants))
	for _, grant := range grants {
		views = append(views, viewOfDiagnosticGrant(grant))
	}
	writeJSON(w, http.StatusOK, diagnosticGrantList{Items: views})
}

// handleDiagnosticContent is the grantee-only content door. Every dimension of
// the grant is re-checked in one transaction and the content-read audit row is
// committed before the first byte is served; a refusal is itself audited with a
// distinct code. The response is an attachment stream, never a JSON body.
func (s *Server) handleDiagnosticContent(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if !s.requireHumanDiagnostics(w, r, caller) {
		return
	}
	if !caller.HasRole(identity.RoleAdmin) {
		writeError(w, r, http.StatusForbidden, "forbidden",
			"admin role is required to read diagnostic content")
		return
	}
	query := r.URL.Query()
	read, err := s.diagnostics.ReadContent(
		r.Context(),
		caller.PrincipalID,
		diagnostics.ReadRequest{
			GrantID: r.PathValue("grant_id"),
			Object:  query.Get("object"),
			Purpose: query.Get("purpose"),
		},
		RequestID(r.Context()),
	)
	if err != nil {
		s.writeDiagnosticFailure(w, r, err)
		return
	}
	defer func() { _ = read.Reader.Close() }()

	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Length", strconv.FormatInt(read.Size, 10))
	w.Header().Set("Content-Disposition", "attachment")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Cache-Control", "no-store")
	// The grant id is echoed so an operator can line the response up with the
	// audit row; it is an id, never content.
	w.Header().Set("X-Diagnostic-Grant", read.GrantID)
	w.WriteHeader(http.StatusOK)
	if _, err := io.Copy(w, read.Reader); err != nil {
		// The status and leading bytes are already on the wire; the read was
		// audited as served before any byte moved. Log only the transport error.
		s.logger.Printf(
			"request_id=%s diagnostic content stream interrupted: %v",
			RequestID(r.Context()), err,
		)
	}
}

// writeDiagnosticFailure maps the diagnostics service errors onto the contract
// envelope. Each refusal reason has its own stable code (ADR 0028), and no
// message or detail value echoes the request's purpose, object or content.
func (s *Server) writeDiagnosticFailure(w http.ResponseWriter, r *http.Request, err error) {
	if validation, ok := diagnostics.AsValidation(err); ok {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			validation.Error(), map[string]any{"field": validation.Field})
		return
	}
	switch {
	case errors.Is(err, diagnostics.ErrNotFound):
		writeError(w, r, http.StatusNotFound, "resource_not_found",
			"that diagnostic grant does not exist")
	case errors.Is(err, diagnostics.ErrGranteeNotFound):
		writeError(w, r, http.StatusNotFound, "principal_not_found",
			"that principal does not exist")
	case errors.Is(err, diagnostics.ErrGranteeDisabled):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"the principal is disabled", map[string]any{"field": "grantee_id"})
	case errors.Is(err, diagnostics.ErrGranteeNotAdmin):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"the grantee must be an administrator", map[string]any{"field": "grantee_id"})
	case errors.Is(err, diagnostics.ErrRunNotGrantable):
		writeError(w, r, http.StatusNotFound, "resource_not_found",
			"that run cannot be granted")
	case errors.Is(err, diagnostics.ErrGrantRevoked):
		writeError(w, r, http.StatusForbidden, "diagnostic_grant_revoked",
			"this diagnostic grant was revoked")
	case errors.Is(err, diagnostics.ErrGrantExpired):
		writeError(w, r, http.StatusForbidden, "diagnostic_grant_expired",
			"this diagnostic grant has expired")
	case errors.Is(err, diagnostics.ErrRunDeleted):
		writeError(w, r, http.StatusForbidden, "diagnostic_run_deleted",
			"the run was deleted; a diagnostic grant never outlives its tombstone")
	case errors.Is(err, diagnostics.ErrPurposeDenied):
		writeError(w, r, http.StatusForbidden, "diagnostic_purpose_denied",
			"the requested purpose is not covered by this diagnostic grant")
	case errors.Is(err, diagnostics.ErrScopeDenied):
		writeError(w, r, http.StatusForbidden, "diagnostic_scope_denied",
			"the requested object is outside this diagnostic grant's object scope")
	case errors.Is(err, diagnostics.ErrContentUnavailable):
		writeError(w, r, http.StatusNotFound, "diagnostic_content_unavailable",
			"the diagnostic content is not available")
	case errors.Is(err, diagnostics.ErrContentNotReady):
		writeError(w, r, http.StatusConflict, "diagnostic_content_not_ready",
			"the diagnostic content is not ready to be read")
	case errors.Is(err, diagnostics.ErrObjectUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the object store is not configured on this server")
	default:
		s.internalError(w, r, err)
	}
}
