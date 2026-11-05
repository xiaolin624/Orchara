package api

// F02B/T068: the publication review HTTP surface.
//
//	POST /api/v1/applications/{application_id}/submit       submitApplication
//	POST /api/v1/reviews/{review_id}/decision               reviewPublication
//	GET  /api/v1/reviews                                    listPublicationReviews
//	GET  /api/v1/reviews/{review_id}                        getPublicationReview
//
// plus one documented non-contract creator read:
//
//	POST /api/v1/applications/{application_id}/publication-preview
//
// The contract's submit body names only the expected manifest hash and the
// evaluation report id; the server resolves the draft, the report, the frozen
// snapshot and the author-case gate itself. A publication review never exposes
// another user's content: the snapshot is the shared definition plus the sealed
// report's own case identifiers/categories.

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/compiler"
	"orchara/platform/internal/identity"
	"orchara/platform/internal/objectstore"
)

const maxPublicationRequestBody = 16 << 10

// publicationReviewResponse is the detail body in publication.go's view type;
// the API keeps the json shape there so the contract snapshot stays one object.

func readPublicationSubmitRequest(w http.ResponseWriter, r *http.Request) (catalog.SubmitPublicationRequest, bool) {
	if r.Body == nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is missing")
		return catalog.SubmitPublicationRequest{}, false
	}
	body := http.MaxBytesReader(w, r.Body, maxPublicationRequestBody)
	decoder := json.NewDecoder(body)
	var fields map[string]json.RawMessage
	if err := decoder.Decode(&fields); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			writeError(w, r, http.StatusRequestEntityTooLarge, "payload_too_large",
				"the publication submission exceeds the request size limit")
			return catalog.SubmitPublicationRequest{}, false
		}
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return catalog.SubmitPublicationRequest{}, false
	}
	if fields == nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body must be a JSON object")
		return catalog.SubmitPublicationRequest{}, false
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body contains trailing data")
		return catalog.SubmitPublicationRequest{}, false
	}
	request := catalog.SubmitPublicationRequest{
		ExpectedManifestSHA256: fields["expected_manifest_sha256"],
		EvaluationReportID:     fields["evaluation_report_id"],
	}
	for key := range fields {
		switch key {
		case "expected_manifest_sha256", "evaluation_report_id":
		default:
			request.UnknownFields = append(request.UnknownFields, key)
		}
	}
	return request, true
}

func readReviewDecisionRequest(w http.ResponseWriter, r *http.Request) (catalog.ReviewDecisionRequest, bool) {
	if r.Body == nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is missing")
		return catalog.ReviewDecisionRequest{}, false
	}
	body := http.MaxBytesReader(w, r.Body, maxPublicationRequestBody)
	decoder := json.NewDecoder(body)
	var fields map[string]json.RawMessage
	if err := decoder.Decode(&fields); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			writeError(w, r, http.StatusRequestEntityTooLarge, "payload_too_large",
				"the review decision exceeds the request size limit")
			return catalog.ReviewDecisionRequest{}, false
		}
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return catalog.ReviewDecisionRequest{}, false
	}
	if fields == nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body must be a JSON object")
		return catalog.ReviewDecisionRequest{}, false
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body contains trailing data")
		return catalog.ReviewDecisionRequest{}, false
	}
	request := catalog.ReviewDecisionRequest{
		Decision:       fields["decision"],
		ManifestSHA256: fields["manifest_sha256"],
		Reason:         fields["reason"],
	}
	for key := range fields {
		switch key {
		case "decision", "manifest_sha256", "reason":
		default:
			request.UnknownFields = append(request.UnknownFields, key)
		}
	}
	return request, true
}

// requireIdempotencyKey is the shared header rule for the two contract POSTs.
func requireIdempotencyKey(w http.ResponseWriter, r *http.Request) (string, bool) {
	values := r.Header.Values("Idempotency-Key")
	key := ""
	if len(values) == 1 {
		key = strings.TrimSpace(values[0])
	}
	if len(values) != 1 || key == "" {
		writeErrorWithDetails(w, r, http.StatusBadRequest, "idempotency_key_required",
			"the Idempotency-Key header is required and must be one non-empty value",
			map[string]any{"header": "Idempotency-Key"})
		return "", false
	}
	return key, true
}

// writePublicationFailure maps the catalog's typed refusals to the contract
// envelope. It is one function so submit/decision/preview cannot drift.
func (s *Server) writePublicationFailure(w http.ResponseWriter, r *http.Request, err error) bool {
	var (
		validation   *catalog.ValidationError
		revision     *catalog.RevisionConflictError
		refused      *catalog.ExecutionRefusedError
		report       *catalog.ReportRefusedError
		changed      *catalog.ManifestChangedError
		conflict     *catalog.IdempotencyConflictError
		reviewState  *catalog.ReviewStateError
		hashMismatch *catalog.ReviewHashMismatchError
		dependency   *catalog.FrozenDependencyError
		archived     *catalog.ApplicationArchivedError
		unready      *compiler.UnconfiguredError
	)
	switch {
	case errors.Is(err, catalog.ErrNotVisible), errors.Is(err, catalog.ErrReviewNotVisible),
		errors.Is(err, catalog.ErrReportNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found",
			"that application, review or report does not exist")
		return true
	case errors.As(err, &validation):
		status := http.StatusUnprocessableEntity
		code := "validation_failed"
		if validation.Field == "idempotency_key" {
			status = http.StatusBadRequest
			code = "idempotency_key_required"
		}
		writeErrorWithDetails(w, r, status, code, validation.Error(),
			map[string]any{"field": validation.Field})
		return true
	case errors.As(err, &revision):
		writeErrorWithDetails(w, r, http.StatusConflict, "revision_conflict",
			"the requested draft revision is not the current one; fetch the draft and retry",
			map[string]any{
				"requested_revision": revision.RequestedRevision,
				"current_revision":   revision.CurrentRevision,
			})
		return true
	case errors.As(err, &conflict):
		writeErrorWithDetails(w, r, http.StatusConflict, "idempotency_conflict",
			"this Idempotency-Key was already used for a different request",
			map[string]any{"header": "Idempotency-Key"})
		return true
	case errors.As(err, &changed):
		details := map[string]any{}
		if changed.Expected != "" {
			details["expected_manifest_sha256"] = changed.Expected
		}
		if changed.Current != "" {
			details["current_manifest_sha256"] = changed.Current
		}
		if changed.CurrentExecutionRevision > 0 {
			details["current_execution_revision"] = changed.CurrentExecutionRevision
		}
		writeErrorWithDetails(w, r, http.StatusConflict, "manifest_mismatch", changed.Error(), details)
		return true
	case errors.As(err, &hashMismatch):
		writeErrorWithDetails(w, r, http.StatusConflict, "manifest_mismatch",
			hashMismatch.Error(), map[string]any{
				"reviewed_manifest_sha256": hashMismatch.Reviewed,
				"provided_manifest_sha256": hashMismatch.Provided,
			})
		return true
	case errors.As(err, &reviewState):
		writeErrorWithDetails(w, r, http.StatusConflict, "review_conflict",
			reviewState.Error(), map[string]any{"state": reviewState.State})
		return true
	case errors.As(err, &dependency):
		writeErrorWithDetails(w, r, http.StatusConflict, "dependency_unavailable",
			dependency.Error(), map[string]any{"errors": dependency.Errors})
		return true
	case errors.As(err, &archived):
		writeError(w, r, http.StatusConflict, "application_archived", archived.Error())
		return true
	case errors.As(err, &refused):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"the draft cannot be executed; fix the located errors and retry",
			map[string]any{"errors": refused.Errors})
		return true
	case errors.As(err, &report):
		details := report.Details
		if details == nil {
			details = map[string]any{}
		}
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, report.Code, report.Message, details)
		return true
	case errors.As(err, &unready):
		writeError(w, r, http.StatusServiceUnavailable, "compiler_unconfigured", unready.Error())
		return true
	case errors.Is(err, objectstore.ErrNotConfigured):
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the object store is not configured; publication evidence cannot be sealed or read")
		return true
	}
	return false
}

// handleSubmitApplication implements the contract's submitApplication.
func (s *Server) handleSubmitApplication(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireApplicationWrite(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	key, ok := requireIdempotencyKey(w, r)
	if !ok {
		return
	}
	request, ok := readPublicationSubmitRequest(w, r)
	if !ok {
		return
	}
	submission, err := s.catalog.SubmitPublication(
		r.Context(), caller.PrincipalID, r.PathValue("application_id"),
		catalog.IdempotencyScope{
			PrincipalID: caller.PrincipalID,
			Method:      http.MethodPost,
			Path:        r.URL.Path,
			Key:         key,
		},
		request, s.objects, s.manifest,
	)
	if err != nil {
		if s.writePublicationFailure(w, r, err) {
			return
		}
		s.internalError(w, r, err)
		return
	}
	if submission == nil || submission.Review == nil {
		s.internalError(w, r, errors.New("publication submission produced no review"))
		return
	}
	// The contract's Accepted schema is exactly {id, state}: the internal view
	// carries the application/hash for service callers, never for this body.
	writeJSON(w, http.StatusAccepted, struct {
		ID    string `json:"id"`
		State string `json:"state"`
	}{ID: submission.Review.ID, State: submission.Review.State})
}

// handlePreviewPublication is the documented non-contract creator read: the same
// shared-definition projection a submission freezes, so the creator can see the
// purpose card, model destinations, tool read/write/network, pinned
// sub-versions and the manifest hash before submitting. It creates no review.
func (s *Server) handlePreviewPublication(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireApplicationWrite(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	content, err := s.catalog.PreviewPublication(
		r.Context(), caller.PrincipalID, r.PathValue("application_id"),
		s.objects, s.manifest,
	)
	if err != nil {
		if s.writePublicationFailure(w, r, err) {
			return
		}
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, content)
}

// handleReviewDecision implements the contract's reviewPublication. The admin
// role is required before the body is read; an API key has no roles, so a bearer
// credential is refused exactly like the tool review endpoint.
func (s *Server) handleReviewDecision(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, identity.RoleAdmin)
	if !ok {
		return
	}
	key, ok := requireIdempotencyKey(w, r)
	if !ok {
		return
	}
	request, ok := readReviewDecisionRequest(w, r)
	if !ok {
		return
	}
	decision, err := s.catalog.ReviewPublication(
		r.Context(), caller.PrincipalID, r.PathValue("review_id"),
		catalog.IdempotencyScope{
			PrincipalID: caller.PrincipalID,
			Method:      http.MethodPost,
			Path:        r.URL.Path,
			Key:         key,
		},
		request, s.objects,
	)
	if err != nil {
		if s.writePublicationFailure(w, r, err) {
			return
		}
		s.internalError(w, r, err)
		return
	}
	if decision == nil {
		s.internalError(w, r, errors.New("publication decision produced no result"))
		return
	}
	writeJSON(w, http.StatusOK, decision)
}

// handleListPublicationReviews returns the caller's visible review page: an
// administrator sees all, a creator sees only their own submissions and the
// reviews of applications they own.
func (s *Server) handleListPublicationReviews(w http.ResponseWriter, r *http.Request) {
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
	items, next, err := s.catalog.ListReviews(
		r.Context(), caller.PrincipalID, caller.HasRole(identity.RoleAdmin),
		query.Get("cursor"), limit,
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
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, struct {
		Items      []catalog.PublicationReviewView `json:"items"`
		NextCursor *string                         `json:"next_cursor"`
	}{Items: items, NextCursor: next})
}

// handleGetPublicationReview returns one review's frozen snapshot. Unknown and
// foreign reviews are one 404 so ids cannot be enumerated.
func (s *Server) handleGetPublicationReview(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	detail, err := s.catalog.GetReview(
		r.Context(), caller.PrincipalID, r.PathValue("review_id"),
		caller.HasRole(identity.RoleAdmin),
	)
	switch {
	case errors.Is(err, catalog.ErrReviewNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that publication review does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, detail)
}
