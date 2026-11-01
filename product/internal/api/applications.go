package api

import (
	"errors"
	"net/http"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/identity"
)

// applicationView is exactly the contract's Application schema.
type applicationView struct {
	ID              string  `json:"id"`
	Name            string  `json:"name"`
	ActiveVersionID *string `json:"active_version_id"`
}

// applicationListResponse is exactly the contract's list envelope.
type applicationListResponse struct {
	Items      []applicationView `json:"items"`
	NextCursor *string           `json:"next_cursor"`
}

func viewOfApplication(application catalog.Application) applicationView {
	return applicationView{
		ID:              application.ID,
		Name:            application.Name,
		ActiveVersionID: application.ActiveVersionID,
	}
}

// handleListApplications returns the applications the caller may read.
//
// The ACL filter runs inside the list query, before the cursor and LIMIT: an
// invisible row can neither consume a page slot nor create an empty page. The
// caller's roles are not consulted — an admin without a grant and without
// ownership sees exactly what anyone else would see (nothing).
func (s *Server) handleListApplications(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	// Scope before ACL: a key without application:read is refused even when the
	// service principal holds a read grant. A session is not scope-limited.
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
	items, nextCursor, err := s.catalog.ListVisible(
		r.Context(), caller.PrincipalID, query.Get("cursor"), limit,
	)
	switch {
	case errors.Is(err, catalog.ErrInvalidCursor):
		writeError(w, r, http.StatusBadRequest, "invalid_cursor", "cursor is not a valid continuation token")
		return
	case errors.Is(err, catalog.ErrInvalidLimit):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"limit must be an integer between 1 and 100", map[string]any{"field": "limit"})
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}

	views := make([]applicationView, 0, len(items))
	for _, application := range items {
		views = append(views, viewOfApplication(application))
	}
	writeJSON(w, http.StatusOK, applicationListResponse{Items: views, NextCursor: nextCursor})
}

// handleGetApplication returns one application, or 404 for both "missing" and
// "not authorized" so the endpoint cannot be used to enumerate ids.
func (s *Server) handleGetApplication(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	// Scope before ACL, and before the catalog is called at all: a write-scoped
	// key never learns whether the application exists.
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	application, err := s.catalog.GetVisible(r.Context(), caller.PrincipalID, r.PathValue("application_id"))
	switch {
	case errors.Is(err, catalog.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that application does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, viewOfApplication(*application))
}
