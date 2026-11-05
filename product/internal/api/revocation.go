package api

// F02B/T070: the version revocation surface and the run-entry guard read.
//
//	POST /api/v1/versions/{version_id}/revoke    revokeVersion (contract, 200 Accepted)
//	GET  /api/v1/versions/{version_id}/runnable  non-contract run-entry guard
//
// revokeVersion is administrator-only over a cookie session + CSRF (an API key
// has no roles, so a bearer credential is 403, exactly like the tool review
// endpoint). The contract has no Idempotency-Key for this operation: the natural
// `revoked_at IS NULL` CAS is the idempotency and a repeated call replays 200.
//
// The runnable read is the documented non-contract surface over
// `catalog.ResolveRunnableVersion` (ADR 0022): the authoritative, live-database
// answer a run entry (and T074's Request/Run creation) must use instead of a
// catalog projection. A stale index can only supply a candidate version id; this
// read refuses a revoked/archived/invisible one with a distinct 409/404.

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/identity"
)

const maxRevokeVersionBody = 8 << 10

// readRevokeVersionRequest decodes the contract body into raw members so the
// service can report one located validation error per bad field.
func readRevokeVersionRequest(w http.ResponseWriter, r *http.Request) (catalog.RevokeVersionRequest, bool) {
	if r.Body == nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is missing")
		return catalog.RevokeVersionRequest{}, false
	}
	body := http.MaxBytesReader(w, r.Body, maxRevokeVersionBody)
	decoder := json.NewDecoder(body)
	var fields map[string]json.RawMessage
	if err := decoder.Decode(&fields); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			writeError(w, r, http.StatusRequestEntityTooLarge, "payload_too_large",
				"the revocation request exceeds the request size limit")
			return catalog.RevokeVersionRequest{}, false
		}
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return catalog.RevokeVersionRequest{}, false
	}
	if fields == nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body must be a JSON object")
		return catalog.RevokeVersionRequest{}, false
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body contains trailing data")
		return catalog.RevokeVersionRequest{}, false
	}
	request := catalog.RevokeVersionRequest{
		Reason:       fields["reason"],
		StopExisting: fields["stop_existing"],
	}
	for key := range fields {
		switch key {
		case "reason", "stop_existing":
		default:
			request.UnknownFields = append(request.UnknownFields, key)
		}
	}
	return request, true
}

// writeRevocationFailure maps the catalog's typed refusals to the contract
// envelope. Unknown/foreign versions are one 404 so ids cannot be enumerated.
func (s *Server) writeRevocationFailure(w http.ResponseWriter, r *http.Request, err error) bool {
	var validation *catalog.ValidationError
	switch {
	case errors.Is(err, catalog.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that version does not exist")
		return true
	case errors.As(err, &validation):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			validation.Error(), map[string]any{"field": validation.Field})
		return true
	}
	return false
}

// handleRevokeVersion implements the contract's revokeVersion.
func (s *Server) handleRevokeVersion(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, identity.RoleAdmin)
	if !ok {
		return
	}
	request, ok := readRevokeVersionRequest(w, r)
	if !ok {
		return
	}
	revocation, err := s.catalog.RevokeVersion(
		r.Context(), caller.PrincipalID, r.PathValue("version_id"), request,
	)
	if err != nil {
		if s.writeRevocationFailure(w, r, err) {
			return
		}
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, acceptedResponse{ID: revocation.VersionID, State: revocation.State})
}

// handleResolveRunnableVersion is the run-entry guard: it answers with the
// immutable Version for a runnable version and with a distinct refusal otherwise.
func (s *Server) handleResolveRunnableVersion(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	version, err := s.catalog.ResolveRunnableVersion(
		r.Context(), caller.PrincipalID, r.PathValue("version_id"),
	)
	switch {
	case errors.Is(err, catalog.ErrVersionRevoked):
		writeError(w, r, http.StatusConflict, "version_revoked",
			"that application version is revoked and cannot start a new run")
		return
	case errors.Is(err, catalog.ErrVersionArchived):
		writeError(w, r, http.StatusConflict, "version_archived",
			"that application version belongs to an archived application")
		return
	case errors.Is(err, catalog.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that version does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, viewOfVersion(*version))
}
