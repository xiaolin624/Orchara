package api

// F02A/T027: private debug submission and the owner-only run read.
//
//	POST /api/v1/applications/{application_id}/debug-runs   -> 202 Run
//	GET  /api/v1/runs/{run_id}                             -> 200 Run
//
// The submission freezes one draft revision into a private execution snapshot
// and queues a debug run; the actual claim/execute/events/results belong to F03.
// The handler below is only the contract boundary: authentication, CSRF/scope,
// request decoding, located error envelopes and the documented
// X-Manifest-SHA256 extension (the Run schema has no field for the fixed hash).

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/compiler"
	"orchara/platform/internal/identity"
	"orchara/platform/internal/objectstore"
)

// readDebugRequest decodes the debugDraft envelope: the three known fields plus
// the unknown top-level keys, so unknown-key validation runs inside the service
// with the same 422 vocabulary as the other body problems.
func readDebugRequest(w http.ResponseWriter, r *http.Request) (catalog.DebugRequest, bool) {
	if r.Body == nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is missing")
		return catalog.DebugRequest{}, false
	}
	body := http.MaxBytesReader(w, r.Body, maxRequestBody)
	decoder := json.NewDecoder(body)
	var fields map[string]json.RawMessage
	if err := decoder.Decode(&fields); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			writeError(w, r, http.StatusRequestEntityTooLarge, "payload_too_large",
				"the debug request exceeds the request size limit")
			return catalog.DebugRequest{}, false
		}
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return catalog.DebugRequest{}, false
	}
	if fields == nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body must be a JSON object")
		return catalog.DebugRequest{}, false
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body contains trailing data")
		return catalog.DebugRequest{}, false
	}
	request := catalog.DebugRequest{
		DraftRevision: fields["draft_revision"],
		Input:         fields["input"],
		ArtifactIDs:   fields["artifact_ids"],
	}
	for key := range fields {
		switch key {
		case "draft_revision", "input", "artifact_ids":
		default:
			request.UnknownFields = append(request.UnknownFields, key)
		}
	}
	return request, true
}

// handleDebugDraft submits one private debug run.
//
// Access is owner-only (404 for everyone else, including administrators and
// read-grant holders): debugging compiles and executes a draft, which is a
// submission action rather than a read. A browser session must also hold the
// creator role (SECURITY §1: an administrator must be a creator as well); API
// keys need application:write. Browser sessions need the CSRF token any POST
// requires.
//
// The Idempotency-Key header is required by the contract. The same key with the
// same canonical request replays the stored 202 body; the same key with a
// different request is 409 idempotency_conflict; a missing key is 400.
func (s *Server) handleDebugDraft(w http.ResponseWriter, r *http.Request) {
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
		// A repeated header is ambiguous, and an empty key cannot identify an
		// operation; both are refused before the body is read or any row exists.
		writeErrorWithDetails(w, r, http.StatusBadRequest, "idempotency_key_required",
			"the Idempotency-Key header is required and must be one non-empty value",
			map[string]any{"header": "Idempotency-Key"})
		return
	}
	request, ok := readDebugRequest(w, r)
	if !ok {
		return
	}
	submission, err := s.catalog.SubmitDebug(
		r.Context(),
		caller.PrincipalID,
		r.PathValue("application_id"),
		catalog.IdempotencyScope{
			PrincipalID: caller.PrincipalID,
			Method:      http.MethodPost,
			// The concrete path is part of the receipt's primary key, so the same
			// key cannot be borrowed across applications.
			Path: r.URL.Path,
			Key:  key,
		},
		request,
		s.objects,
		s.manifest,
	)
	var (
		validation *catalog.ValidationError
		revision   *catalog.RevisionConflictError
		refused    *catalog.ExecutionRefusedError
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
	case errors.As(err, &refused):
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"the draft cannot be executed; fix the located errors and retry",
			map[string]any{"errors": refused.Errors})
		return
	case errors.As(err, &validation):
		status := http.StatusUnprocessableEntity
		code := "validation_failed"
		if validation.Field == "idempotency_key" {
			// Defensive: the header check above normally catches this first.
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
			"the object store is not configured; the debug input cannot be sealed")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	if submission == nil || submission.Run == nil {
		s.internalError(w, r, errors.New("debug submission produced no run"))
		return
	}
	// The Run schema has no field for the frozen manifest hash; the documented
	// X-Manifest-SHA256 extension carries it (ADR 0004 §26/§38). The body stays
	// exactly the contract's Run.
	w.Header().Set("X-Manifest-SHA256", submission.Run.ManifestSHA256)
	writeJSON(w, http.StatusAccepted, submission.Run)
}

// handleGetRun returns one run to its owner. A run has no grant type in this
// batch: every other principal (administrator included) gets 404, and an
// unknown/malformed/deleted id is indistinguishable from one the caller may not
// read.
func (s *Server) handleGetRun(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	run, err := s.catalog.GetRun(r.Context(), caller.PrincipalID, r.PathValue("run_id"))
	switch {
	case errors.Is(err, catalog.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that run does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	w.Header().Set("X-Manifest-SHA256", run.ManifestSHA256)
	// F03/T029: when an attempt is live, expose the claimed-at/lease-deadline
	// facts so the owner can see execution reality instead of a fabricated
	// progress bar. The attempt token is a credential and is deliberately not
	// exposed; the two timestamps are non-secret bookkeeping.
	if lease, err := s.execution.LiveLease(r.Context(), run.ID); err != nil {
		s.internalError(w, r, err)
		return
	} else if lease != nil {
		w.Header().Set("X-Run-Attempt-Started-At", lease.StartedAt.UTC().Format(time.RFC3339Nano))
		w.Header().Set("X-Run-Lease-Deadline", lease.LeaseDeadline.UTC().Format(time.RFC3339Nano))
	}
	writeJSON(w, http.StatusOK, run)
}
