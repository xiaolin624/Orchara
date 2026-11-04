package api

// F13A/T056: the per-case evaluation results read.
//
//	GET /api/v1/evaluation-reports/{report_id}/cases
//
// It is a documented non-contract extension (ADR 0017): the contract's Report has
// no per-case detail, and the sealed report document is the product's immutable
// per-case evidence. The read is owner-or-admin, an API key needs
// application:read, and every non-owner gets 404 — the same answer as a missing
// report, so a report id cannot be enumerated.
//
// The response carries artifact ids only; a caller downloads each one through the
// existing owner-only GET /api/v1/artifacts/{artifact_id}/download. No object key
// or signed link is ever part of this body.

import (
	"errors"
	"net/http"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/identity"
	"orchara/platform/internal/objectstore"
)

func (s *Server) handleGetEvaluationCases(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	results, err := s.catalog.EvaluationCaseResults(
		r.Context(), r.PathValue("report_id"), caller.PrincipalID, caller.HasRole("admin"), s.objects,
	)
	switch {
	case errors.Is(err, catalog.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found",
			"that evaluation report does not exist")
		return
	case errors.Is(err, objectstore.ErrNotConfigured):
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the object store is not configured; the sealed report cannot be read")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, results)
}
