package api

import (
	"errors"
	"net/http"

	"orchara/platform/internal/catalog"
	"orchara/platform/internal/compiler"
	"orchara/platform/internal/objectstore"
)

// validationResponse is exactly the contract's validateApplication 200 body:
// valid plus errors[{node_id?, code, pointer, message}]. There is deliberately
// no manifest_sha256 member (the contract has no field for it); the server hash
// travels in the X-Manifest-SHA256 response header on a successful validation.
type validationResponse struct {
	Valid  bool                      `json:"valid"`
	Errors []catalog.ExecutableError `json:"errors"`
}

// handleValidateApplication compiles the current draft into its execution
// manifest and answers with located errors or the server-side hash.
//
// Access is owner-only (404 for everyone else, including read-grant holders):
// compiling is a submission action, not a read. A browser session must also hold
// the creator role (SECURITY §1); API keys need application:write. Browser
// sessions need the CSRF token any POST requires.
//
// Nothing execution-shaped is persisted here: validate creates no version,
// snapshot, run or report. It does seal the canonical manifest bytes once per
// (application, execution_revision, manifest_sha256) so T027/F02B can reuse
// exactly the bytes that were hashed; a repeated validate writes no new object.
func (s *Server) handleValidateApplication(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireApplicationWrite(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	outcome, located, err := s.catalog.ValidateExecution(
		r.Context(),
		caller.PrincipalID,
		r.PathValue("application_id"),
		s.objects,
		s.manifest,
		catalog.ExecutionOptions{},
	)
	var unconfigured *compiler.UnconfiguredError
	switch {
	case errors.Is(err, catalog.ErrNotVisible):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that application does not exist")
		return
	case errors.As(err, &unconfigured):
		// A missing worker image digest/runtime lock is a deployment condition,
		// not a draft error: the compiler never invents an input.
		writeError(w, r, http.StatusServiceUnavailable, "compiler_unconfigured", unconfigured.Error())
		return
	case errors.Is(err, objectstore.ErrNotConfigured):
		// A valid draft needs its canonical bytes sealed; without an object store
		// there is nowhere to put them, so validate fails closed rather than
		// returning a hash that cannot be re-verified later.
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the object store is not configured; the execution manifest bytes cannot be sealed")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	if len(located) > 0 {
		// The contract's error objects are the only in-band place for located
		// validation problems; they are not HTTP errors (the request succeeded).
		writeJSON(w, http.StatusOK, validationResponse{Valid: false, Errors: located})
		return
	}
	if outcome == nil || outcome.SHA256 == "" {
		// Defensive: a nil outcome with no error is a server bug, never a valid
		// manifest.
		s.internalError(w, r, errors.New("validate produced no manifest"))
		return
	}
	w.Header().Set("X-Manifest-SHA256", outcome.SHA256)
	writeJSON(w, http.StatusOK, validationResponse{Valid: true, Errors: []catalog.ExecutableError{}})
}
