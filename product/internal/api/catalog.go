package api

// F08/T077: the authorized purpose catalog search.
//
// `GET /api/v1/catalog/search` is a documented non-contract extension (ADR 0026).
// The contract's `listApplications` response is `additionalProperties:false` and
// has no search member, so the search lives on its own path and leaves the
// contract operation byte-identical. The response is:
//
//	{
//	  "items": [{application_id, application_name, version_id, version_no,
//	             manifest_sha256, capability_card}],
//	  "next_cursor": "..."|null,
//	  "retrieval": {source, stale, index_state, projection_epoch,
//	                rechecked, rejected_by_recheck}
//	}
//
// The `retrieval` block is how the page can honestly say "回退检索（索引落后）"
// instead of pretending a fallback answer came from the projection.

import (
	"errors"
	"net/http"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/identity"
)

// catalogSearchItem is one non-contract search result. `capability_card` is the
// exact six-member card read back from the authoritative version.
type catalogSearchItem struct {
	ApplicationID   string `json:"application_id"`
	ApplicationName string `json:"application_name"`
	VersionID       string `json:"version_id"`
	VersionNo       int64  `json:"version_no"`
	ManifestSHA256  string `json:"manifest_sha256"`
	CapabilityCard  any    `json:"capability_card"`
}

// catalogSearchResponse is the non-contract envelope. It deliberately mirrors the
// list endpoint's items/next_cursor shape so clients need no second pagination
// convention.
type catalogSearchResponse struct {
	Items      []catalogSearchItem      `json:"items"`
	NextCursor *string                  `json:"next_cursor"`
	Retrieval  catalog.CatalogRetrieval `json:"retrieval"`
}

func (s *Server) handleCatalogSearch(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	// The catalog is an application read: an API key without application:read is
	// refused before any catalog row is looked at, exactly like listApplications.
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
	page, err := s.catalog.SearchAuthorized(
		r.Context(), caller.PrincipalID, query.Get("q"), query.Get("cursor"),
		limit, s.policy.Clock(),
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
	case errors.Is(err, catalog.ErrSearchQueryTooLong):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			catalog.ErrSearchQueryTooLong.Error(), map[string]any{"field": "q"})
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}

	items := make([]catalogSearchItem, 0, len(page.Items))
	for _, hit := range page.Items {
		items = append(items, catalogSearchItem{
			ApplicationID:   hit.ApplicationID,
			ApplicationName: hit.ApplicationName,
			VersionID:       hit.VersionID,
			VersionNo:       hit.VersionNo,
			ManifestSHA256:  hit.ManifestSHA256,
			CapabilityCard:  hit.CapabilityCard,
		})
	}
	writeJSON(w, http.StatusOK, catalogSearchResponse{
		Items: items, NextCursor: page.NextCursor, Retrieval: page.Retrieval,
	})
}
