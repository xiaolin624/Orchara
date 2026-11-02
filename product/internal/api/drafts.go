package api

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/identity"
)

// createApplicationRequest is the contract body of POST /api/v1/applications.
type createApplicationRequest struct {
	Name string `json:"name"`
}

// draftView is exactly the contract's DraftInput. RawMessage keeps the stored
// jsonb text intact: the API never re-encodes a document it did not build.
type draftView struct {
	Definition     json.RawMessage `json:"definition"`
	Layout         json.RawMessage `json:"layout"`
	CapabilityCard json.RawMessage `json:"capability_card"`
}

// maxDraftRequestBytes is the request-body cap shared with decodeJSON. It is
// restated here because the draft body is decoded into raw JSON objects (to keep
// unknown-key handling in the service's validation order) instead of a struct.
const maxDraftRequestBytes = maxRequestBody

// handleCreateApplication creates an application and its revision=1 empty draft
// in one transaction. Session callers are authorized by the session; an API key
// additionally needs application:write (a read key must not be able to create).
func (s *Server) handleCreateApplication(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationWrite); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	var request createApplicationRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	application, err := s.catalog.CreateApplication(r.Context(), caller.PrincipalID, request.Name)
	if err != nil {
		writeValidationFailure(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, viewOfApplication(*application))
}

// handleGetDraft returns the draft and the strong ETag a later save must echo.
// Owner-or-grant can read; everything else is 404 (the endpoint cannot be used
// to enumerate ids). A read grant does not imply the write path.
func (s *Server) handleGetDraft(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	draft, err := s.catalog.GetDraft(r.Context(), caller.PrincipalID, r.PathValue("application_id"))
	switch {
	case errors.Is(err, catalog.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that application does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	// The ETag is the whole contract of this read; Cache-Control no-store comes
	// from writeJSON so a stale revision can never be served from a cache.
	w.Header().Set("ETag", draft.ETag())
	writeJSON(w, http.StatusOK, draftView{
		Definition:     draft.Definition,
		Layout:         draft.Layout,
		CapabilityCard: draft.CapabilityCard,
	})
}

// draftInput is the wire half of the draft PUT: the three known fields plus the
// unknown top-level keys, so unknown-key validation can run inside the service
// after ACL/If-Match instead of pre-empting them with a 400.
type draftInput struct {
	input   catalog.DraftInput
	unknown []string
}

// readDraftInput decodes the DraftInput envelope and records unknown top-level
// keys. Only genuinely malformed JSON (or an oversized body) is refused here;
// shape validation is the service's 422 so it happens after visibility and the
// If-Match precondition.
func readDraftInput(w http.ResponseWriter, r *http.Request) (draftInput, bool) {
	if r.Body == nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is missing")
		return draftInput{}, false
	}
	body := http.MaxBytesReader(w, r.Body, maxDraftRequestBytes)
	decoder := json.NewDecoder(body)
	var fields map[string]json.RawMessage
	if err := decoder.Decode(&fields); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			writeError(w, r, http.StatusRequestEntityTooLarge, "payload_too_large",
				"the draft body exceeds the request size limit")
			return draftInput{}, false
		}
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return draftInput{}, false
	}
	if fields == nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body must be a JSON object")
		return draftInput{}, false
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body contains trailing data")
		return draftInput{}, false
	}
	result := draftInput{
		input: catalog.DraftInput{
			Definition:     fields["definition"],
			Layout:         fields["layout"],
			CapabilityCard: fields["capability_card"],
		},
	}
	for key := range fields {
		switch key {
		case "definition", "layout", "capability_card":
		default:
			result.unknown = append(result.unknown, key)
		}
	}
	return result, true
}

// handleSaveDraft is the optimistic-concurrency write:
//
//	missing If-Match            -> 428 precondition_required
//	stale/garbled If-Match      -> 412 precondition_failed (+ current revision)
//	invalid DraftInput shape    -> 422 validation_failed
//	accepted                    -> 200 Accepted + the new strong ETag
//
// The DSL is deliberately not validated here: saving an invalid workflow is
// allowed, and refusing to debug/submit it belongs to T025/T027 (CheckExecutable
// is exposed and unit-tested now; those endpoints are not).
func (s *Server) handleSaveDraft(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationWrite); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	decoded, ok := readDraftInput(w, r)
	if !ok {
		return
	}
	ifMatch := r.Header.Get("If-Match")
	if len(r.Header.Values("If-Match")) > 1 {
		// Repeated If-Match values are not the single opaque token the contract
		// defines. Treat it as garbled rather than guessing which one is meant.
		ifMatch = "multiple-values"
	}
	decoded.input.UnknownFields = decoded.unknown
	result, err := s.catalog.SaveDraft(
		r.Context(), caller.PrincipalID, r.PathValue("application_id"), ifMatch, decoded.input,
	)
	var (
		required *catalog.PreconditionRequiredError
		stale    *catalog.PreconditionFailedError
	)
	switch {
	case errors.Is(err, catalog.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that application does not exist")
		return
	case errors.Is(err, catalog.ErrNotOwner):
		writeError(w, r, http.StatusForbidden, "forbidden",
			"only the owner may save this draft; a read grant is not a write grant")
		return
	case errors.As(err, &required):
		writeErrorWithDetails(w, r, http.StatusPreconditionRequired, "precondition_required",
			"If-Match is required to save a draft", map[string]any{"header": "If-Match"})
		return
	case errors.As(err, &stale):
		writeErrorWithDetails(w, r, http.StatusPreconditionFailed, "precondition_failed",
			"the draft has moved on; fetch it again and retry with the current ETag",
			map[string]any{
				"current_revision": stale.CurrentRevision,
				"current_etag":     stale.CurrentETag,
			})
		return
	case errors.As(err, new(*catalog.ValidationError)):
		writeValidationFailure(w, r, err)
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	w.Header().Set("ETag", catalog.FormatETag(result.Revision))
	writeJSON(w, http.StatusOK, acceptedResponse{ID: r.PathValue("application_id"), State: "saved"})
}
