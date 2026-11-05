package api

// F13A/T052: the evaluation submission route.
//
//	POST /api/v1/applications/{application_id}/evaluations  -> 202 EvaluationJob
//
// This is the contract's evaluateDraft. It is the *only* public way to start an
// evaluation: the caller names a draft revision and an uploaded, sealed dataset
// artifact, and the platform decides everything else (which snapshot, which
// job, which runner). A caller can never attest its own verdict; the completion
// guard remains the only writer of a report.
//
// Authorization follows debugDraft (an evaluation compiles and executes the
// owner's draft, which is a submission action, not a read): the caller must own
// the application, a browser session must hold the creator role and an API key
// must carry application:write. The contract's evaluation reads piggyback on
// application:read; there is no evaluation-specific scope in this build, which
// is recorded as contract debt in the T052 report.

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/compiler"
	"orchara/platform/internal/objectstore"
)

// maxEvaluationRequestBody bounds the two-field body. It is deliberately tiny:
// the dataset travels as an artifact, never in the request.
const maxEvaluationRequestBody = 8 << 10

// readEvaluationRequest decodes the evaluateDraft envelope, keeping the unknown
// top-level keys so the service reports them through the located 422
// vocabulary.
func readEvaluationRequest(w http.ResponseWriter, r *http.Request) (catalog.EvaluationRequest, bool) {
	if r.Body == nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is missing")
		return catalog.EvaluationRequest{}, false
	}
	body := http.MaxBytesReader(w, r.Body, maxEvaluationRequestBody)
	decoder := json.NewDecoder(body)
	var fields map[string]json.RawMessage
	if err := decoder.Decode(&fields); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			writeError(w, r, http.StatusRequestEntityTooLarge, "payload_too_large",
				"the evaluation request exceeds the request size limit")
			return catalog.EvaluationRequest{}, false
		}
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return catalog.EvaluationRequest{}, false
	}
	if fields == nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body must be a JSON object")
		return catalog.EvaluationRequest{}, false
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body contains trailing data")
		return catalog.EvaluationRequest{}, false
	}
	request := catalog.EvaluationRequest{
		DraftRevision:     fields["draft_revision"],
		DatasetArtifactID: fields["dataset_artifact_id"],
	}
	for key := range fields {
		switch key {
		case "draft_revision", "dataset_artifact_id":
		default:
			request.UnknownFields = append(request.UnknownFields, key)
		}
	}
	return request, true
}

// handleSubmitEvaluation submits one evaluation and returns the queued job.
func (s *Server) handleSubmitEvaluation(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireApplicationWrite(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	values := r.Header.Values("Idempotency-Key")
	key := ""
	if len(values) == 1 {
		key = strings.TrimSpace(values[0])
	}
	if len(values) != 1 || key == "" {
		writeErrorWithDetails(w, r, http.StatusBadRequest, "idempotency_key_required",
			"the Idempotency-Key header is required and must be one non-empty value",
			map[string]any{"header": "Idempotency-Key"})
		return
	}
	request, ok := readEvaluationRequest(w, r)
	if !ok {
		return
	}
	submission, err := s.catalog.SubmitEvaluation(
		r.Context(),
		caller.PrincipalID,
		r.PathValue("application_id"),
		catalog.IdempotencyScope{
			PrincipalID: caller.PrincipalID,
			Method:      http.MethodPost,
			Path:        r.URL.Path,
			Key:         key,
		},
		request,
		s.objects,
		s.manifest,
	)
	var (
		validation *catalog.ValidationError
		revision   *catalog.RevisionConflictError
		refused    *catalog.ExecutionRefusedError
		dataset    *catalog.DatasetRefusedError
		conflict   *catalog.IdempotencyConflictError
		unready    *compiler.UnconfiguredError
	)
	switch {
	case errors.Is(err, catalog.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that application does not exist")
		return
	case errors.As(err, &revision):
		writeErrorWithDetails(w, r, http.StatusConflict, "revision_conflict",
			"the requested draft revision is not the current one; fetch the draft and retry with its revision",
			map[string]any{
				"requested_revision": revision.RequestedRevision,
				"current_revision":   revision.CurrentRevision,
			})
		return
	case errors.As(err, &conflict):
		writeErrorWithDetails(w, r, http.StatusConflict, "idempotency_conflict",
			"this Idempotency-Key was already used for a different request",
			map[string]any{"header": "Idempotency-Key"})
		return
	case errors.As(err, &dataset):
		// The dataset is structurally invalid: every problem is located at the
		// document pointer that caused it, so a curator can fix the case.
		located := make([]map[string]any, 0, len(dataset.Errors))
		for _, item := range dataset.Errors {
			located = append(located, map[string]any{
				"code": item.Code, "pointer": item.Pointer, "message": item.Message,
			})
		}
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"the evaluation dataset is not valid; fix the located errors and retry",
			map[string]any{"errors": located, "field": "dataset_artifact_id"})
		return
	case errors.As(err, &refused):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"the draft cannot be executed; fix the located errors and retry",
			map[string]any{"errors": refused.Errors})
		return
	case errors.As(err, &validation):
		status := http.StatusUnprocessableEntity
		code := "validation_failed"
		if validation.Field == "idempotency_key" {
			status = http.StatusBadRequest
			code = "idempotency_key_required"
		}
		writeErrorWithDetails(w, r, status, code, validation.Error(),
			map[string]any{"field": validation.Field})
		return
	case errors.As(err, &unready):
		writeError(w, r, http.StatusServiceUnavailable, "compiler_unconfigured", unready.Error())
		return
	case errors.Is(err, objectstore.ErrNotConfigured):
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the object store is not configured; the evaluation dataset cannot be read")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	if submission == nil || submission.Evaluation == nil {
		s.internalError(w, r, errors.New("evaluation submission produced no job"))
		return
	}
	writeJSON(w, http.StatusAccepted, submission.Evaluation)
}
