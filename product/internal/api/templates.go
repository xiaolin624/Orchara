package api

// F04A/T049: the non-contract "instantiate a template into my own application"
// bridge.
//
// This is deliberately not a publication endpoint and not a contract route. It
// copies one server-controlled source draft into a new application owned by the
// authenticated creator; everything that follows (validate, debug-run, node
// results, report download) is the existing F02A/F03/F04A path. Creating a
// shared immutable application version with a review and audit is F02B, and no
// part of this handler pretends otherwise.
//
// Authorization:
//
//   - the caller must be an authenticated creator (requireApplicationWrite);
//   - a template is readable by any creator: it is a server-controlled seed, not
//     another principal's private draft;
//   - an unknown template id is a 404 with the same shape as a malformed one, so
//     the endpoint cannot be used to enumerate templates.

import (
	"errors"
	"net/http"

	"orchara/platform/internal/templates"
)

func (s *Server) handleInstantiateTemplate(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	if failure := requireApplicationWrite(caller); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}

	service := templates.New(s.catalog.DB)
	instance, err := service.Instantiate(r.Context(), caller.PrincipalID, r.PathValue("template_id"))
	switch {
	case errors.Is(err, templates.ErrTemplateNotFound):
		writeError(w, r, http.StatusNotFound, "template_not_found",
			"that application template does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, instance)
}
