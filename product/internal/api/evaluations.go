package api

// F00B/T026 evaluation reads and the job-scoped evaluation binding endpoint.
//
// Two public reads are implemented here:
//
//	GET /api/v1/evaluations/{evaluation_id}        -> contract EvaluationJob
//	GET /api/v1/evaluation-reports/{report_id}     -> contract Report
//
// Both are owner-or-admin: the owner of the evaluation (and, for a report, the
// owner of the evaluation that produced it) or an administrator gets 200, and
// every other principal gets 404 so the ids cannot be enumerated. The
// projections carry only contract fields: no dataset id, candidate id, object
// key, report body or artifact path is exposed by either read.
//
// The third handler is an internal, job-scoped *read* added by T026:
//
//	POST /internal/v1/background/jobs/{job_id}/evaluation
//
// The contract's BackgroundLease has no place for the bound manifest hash the
// handler must put in its report, and the alternative (a handler reading the
// evaluation table with the maintenance DSN) would move the server's binding
// decision into the handler. The endpoint is authenticated exactly like the
// other job-scoped T024 endpoints (maintenance role + live attempt token), is
// not a public path, and returns only the facts of the caller's own evaluation.
// It is recorded as a non-contract extension in the T026 report/ADR.

import (
	"errors"
	"net/http"

	"orchara/platform/internal/background"
)

type evaluationJobView struct {
	ID              string  `json:"id"`
	Kind            string  `json:"kind"`
	State           string  `json:"state"`
	BackgroundJobID string  `json:"background_job_id"`
	ReportID        *string `json:"report_id"`
	ErrorCode       *string `json:"error_code"`
}

func viewOfEvaluation(evaluation *background.EvaluationJob) evaluationJobView {
	return evaluationJobView{
		ID:              evaluation.ID,
		Kind:            evaluation.Kind,
		State:           evaluation.State,
		BackgroundJobID: evaluation.BackgroundJobID,
		ReportID:        evaluation.ReportID,
		ErrorCode:       evaluation.ErrorCode,
	}
}

type evaluationReportView struct {
	ID               string `json:"id"`
	Status           string `json:"status"`
	ManifestSHA256   string `json:"manifest_sha256"`
	ResultArtifactID string `json:"result_artifact_id"`
}

func viewOfEvaluationReport(report *background.EvaluationReport) evaluationReportView {
	return evaluationReportView{
		ID:               report.ID,
		Status:           report.Status,
		ManifestSHA256:   report.ManifestSHA256,
		ResultArtifactID: report.ResultArtifactID,
	}
}

func (s *Server) handleGetEvaluation(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	evaluation, err := s.jobs.GetEvaluation(
		r.Context(), r.PathValue("evaluation_id"), caller.PrincipalID, caller.HasRole("admin"),
	)
	switch {
	case errors.Is(err, background.ErrEvaluationNotFound):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that evaluation does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, viewOfEvaluation(evaluation))
}

func (s *Server) handleGetEvaluationReport(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	report, err := s.jobs.GetEvaluationReport(
		r.Context(), r.PathValue("report_id"), caller.PrincipalID, caller.HasRole("admin"),
	)
	switch {
	case errors.Is(err, background.ErrEvaluationNotFound):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that evaluation report does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, viewOfEvaluationReport(report))
}

type internalEvaluationBindingRequest struct {
	AttemptToken string `json:"attempt_token"`
}

type internalEvaluationBindingResponse struct {
	EvaluationID      string `json:"evaluation_id"`
	Kind              string `json:"kind"`
	DatasetArtifactID string `json:"dataset_artifact_id"`
	ManifestSHA256    string `json:"manifest_sha256"`
}

func (s *Server) handleInternalEvaluationBinding(w http.ResponseWriter, r *http.Request) {
	if !requireInternalRole(w, r, InternalRoleMaintenance) {
		return
	}
	var request internalEvaluationBindingRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_json", "request body is not valid JSON")
		return
	}
	job, ok := s.requireJobAttempt(w, r, request.AttemptToken)
	if !ok {
		return
	}
	binding, err := s.jobs.ResolveEvaluationBinding(r.Context(), job)
	switch {
	case errors.Is(err, background.ErrEvaluationNotApplicable):
		writeError(w, r, http.StatusUnprocessableEntity, "evaluation_not_applicable",
			"this background job is not an evaluation")
		return
	case errors.Is(err, background.ErrEvaluationNotFound):
		writeError(w, r, http.StatusNotFound, "evaluation_not_found",
			"that background job does not own an evaluation")
		return
	case errors.Is(err, background.ErrEvaluationBindingUnavailable):
		writeError(w, r, http.StatusConflict, "evaluation_binding_unavailable",
			"the evaluation's bound manifest cannot be resolved in this deployment")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, internalEvaluationBindingResponse{
		EvaluationID:      binding.EvaluationID,
		Kind:              binding.Kind,
		DatasetArtifactID: binding.DatasetArtifactID,
		ManifestSHA256:    binding.ManifestSHA256,
	})
}
