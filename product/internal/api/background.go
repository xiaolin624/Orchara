package api

import (
	"errors"
	"net/http"

	"orchara/platform/internal/background"
)

// backgroundJobView is exactly the contract's BackgroundJob schema. It is an
// explicit projection on purpose: owner_id, dedup_key, expected hashes and
// artifact references stay internal, so a GET cannot leak another user's
// identifiers or the job's input.
type backgroundJobView struct {
	ID        string  `json:"id"`
	Kind      string  `json:"kind"`
	State     string  `json:"state"`
	ErrorCode *string `json:"error_code"`
}

func viewOfJob(job *background.Job) backgroundJobView {
	return backgroundJobView{
		ID:        job.ID,
		Kind:      string(job.Kind),
		State:     string(job.State),
		ErrorCode: job.ErrorCode,
	}
}

// handleGetBackgroundJob returns one job to its owner or an administrator.
//
// Everyone else — including a caller who merely guesses an id — gets 404, the
// same answer as a missing row, so the endpoint cannot be used to enumerate
// jobs. The contract gives administrators the redacted diagnostic; F01's rule
// that "admin is not automatically a content reader" still holds because this
// projection carries no content at all.
func (s *Server) handleGetBackgroundJob(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	job, err := s.jobs.Get(
		r.Context(), r.PathValue("job_id"), caller.PrincipalID, caller.HasRole("admin"),
	)
	switch {
	case errors.Is(err, background.ErrNotFound):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that background job does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, viewOfJob(job))
}
