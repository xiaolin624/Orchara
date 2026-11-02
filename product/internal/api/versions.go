package api

import (
	"encoding/json"
	"errors"
	"net/http"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/identity"
)

// versionView is exactly the contract's Version schema. It carries the published
// manifest and hash only; a draft body is never joined into this read model.
type versionView struct {
	ID             string          `json:"id"`
	ApplicationID  string          `json:"application_id"`
	VersionNo      int64           `json:"version_no"`
	ManifestSHA256 string          `json:"manifest_sha256"`
	Manifest       json.RawMessage `json:"manifest"`
}

type versionListResponse struct {
	Items      []versionView `json:"items"`
	NextCursor *string       `json:"next_cursor"`
}

func viewOfVersion(version catalog.Version) versionView {
	return versionView{
		ID:             version.ID,
		ApplicationID:  version.ApplicationID,
		VersionNo:      version.VersionNo,
		ManifestSHA256: version.ManifestSHA256,
		Manifest:       version.Manifest,
	}
}

// handleListVersions returns one ACL-filtered page of published versions.
//
// The ACL predicate is inside the same SQL WHERE as the cursor and LIMIT (see
// catalog.ListVersions); the application-level visibility check runs first so an
// invisible application is a 404 rather than an ambiguous empty page.
func (s *Server) handleListVersions(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	query := r.URL.Query()
	limit, err := catalog.ParseLimit(query.Get("limit"))
	if err != nil {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
		return
	}
	items, next, err := s.catalog.ListVersions(
		r.Context(), caller.PrincipalID, r.PathValue("application_id"),
		query.Get("cursor"), limit, s.policy.Clock(),
	)
	switch {
	case errors.Is(err, catalog.ErrInvalidCursor):
		writeError(w, r, http.StatusBadRequest, "invalid_cursor",
			"cursor is not a valid continuation token")
		return
	case errors.Is(err, catalog.ErrInvalidLimit):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
		return
	case errors.Is(err, catalog.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that application does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}

	views := make([]versionView, 0, len(items))
	for _, version := range items {
		views = append(views, viewOfVersion(version))
	}
	writeJSON(w, http.StatusOK, versionListResponse{Items: views, NextCursor: next})
}

// handleGetVersion returns one immutable published version, or 404 for both
// "unknown" and "not authorized" so ids cannot be enumerated.
func (s *Server) handleGetVersion(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	version, err := s.catalog.GetVersion(r.Context(), caller.PrincipalID, r.PathValue("version_id"))
	switch {
	case errors.Is(err, catalog.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that version does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, viewOfVersion(*version))
}
