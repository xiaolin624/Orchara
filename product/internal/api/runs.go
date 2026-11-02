package api

// F03/T029: the owner-only run list.
//
//	GET /api/v1/runs  ->  {items: Run[], next_cursor: string|null}
//
// The contract has no grant type on a Run: T027's getRun is owner-only
// (administrator included), and this list keeps exactly one rule. An
// administrator with no runs of its own therefore sees an empty list, not other
// principals' work. The owner predicate runs inside the list query, before the
// cursor and LIMIT, and the cursor is the shared opaque
// EncodeCursor/DecodeCursor pair (created_at, id) with DESC keyset order.

import (
	"errors"
	"net/http"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/identity"
)

// runListResponse is exactly the contract's list envelope for Run items.
type runListResponse struct {
	Items      []catalog.Run `json:"items"`
	NextCursor *string       `json:"next_cursor"`
}

func (s *Server) handleListRuns(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	// Scope before the query, exactly like every other public read: a write-only
	// key never learns whether the caller owns any run.
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
	items, nextCursor, err := s.catalog.ListRuns(
		r.Context(), caller.PrincipalID, query.Get("cursor"), limit,
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
		// The callers are authenticated principals, so this is only a defensive
		// path; the contract has no owner-specific 404 for a list.
		writeError(w, r, http.StatusNotFound, "resource_not_found", "no runs are visible")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	if items == nil {
		items = []catalog.Run{}
	}
	writeJSON(w, http.StatusOK, runListResponse{Items: items, NextCursor: nextCursor})
}
