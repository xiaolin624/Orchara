package api

// F13A/T065: the non-contract evaluation progress read.
//
//	GET /api/v1/evaluations/{evaluation_id}/progress
//
// It answers "how far has my asynchronous evaluation got, and why did it stop?"
// without widening the contract's six-field EvaluationJob. The authorization is
// exactly the contract evaluation read's: owner or administrator, everyone else
// 404 (never 403, so ids cannot be enumerated), and a scoped API key needs the
// platform's registered read scope before the ACL runs.
//
// The projection never carries a sealed object key, a report body, a case input
// or a percentage. `failure_class` is the T065 distinction (ADR 0018):
// business = a report failed/not_run while the job succeeded; infrastructure =
// the job failed and no report exists; null = running/cancelled/passed.

import (
	"errors"
	"net/http"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/identity"
)

type evaluationProgressView struct {
	EvaluationID   string  `json:"evaluation_id"`
	State          string  `json:"state"`
	ReportStatus   *string `json:"report_status"`
	ReportID       *string `json:"report_id"`
	TotalCases     int64   `json:"total_cases"`
	CompletedCases int64   `json:"completed_cases"`
	FailedCases    int64   `json:"failed_cases"`
	ErrorCode      *string `json:"error_code"`
	FailureClass   *string `json:"failure_class"`
	CreatedAt      string  `json:"created_at"`
	FinishedAt     *string `json:"finished_at"`
}

func viewOfEvaluationProgress(progress *background.EvaluationProgress) evaluationProgressView {
	view := evaluationProgressView{
		EvaluationID:   progress.EvaluationID,
		State:          progress.State,
		ReportStatus:   progress.ReportStatus,
		ReportID:       progress.ReportID,
		TotalCases:     progress.TotalCases,
		CompletedCases: progress.Completed,
		FailedCases:    progress.Failed,
		ErrorCode:      progress.ErrorCode,
		FailureClass:   progress.FailureClass,
		CreatedAt:      progress.CreatedAt.UTC().Format(time.RFC3339Nano),
	}
	if progress.FinishedAt != nil {
		formatted := progress.FinishedAt.UTC().Format(time.RFC3339Nano)
		view.FinishedAt = &formatted
	}
	return view
}

func (s *Server) handleGetEvaluationProgress(w http.ResponseWriter, r *http.Request) {
	caller, ok := s.authorize(w, r, "")
	if !ok {
		return
	}
	// The scope check runs before the ACL, exactly like the contract evaluation
	// read: a write-only key is refused as a credential, a read-scoped key that
	// does not own the evaluation still learns nothing (404).
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	progress, err := s.jobs.GetEvaluationProgress(
		r.Context(), r.PathValue("evaluation_id"),
		caller.PrincipalID, caller.HasRole("admin"),
	)
	switch {
	case errors.Is(err, background.ErrEvaluationNotFound):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that evaluation does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, viewOfEvaluationProgress(progress))
}

// internalEvaluationCaseOutcomeRequest is the runner's per-case progress report.
// It carries only the decision the runner already made; every platform fact
// (evaluation identity, owner, run, ordinal bound) is resolved or re-proved
// server-side.
type internalEvaluationCaseOutcomeRequest struct {
	AttemptToken string `json:"attempt_token"`
	CaseID       string `json:"case_id"`
	Ordinal      int    `json:"ordinal"`
	Verdict      string `json:"verdict"`
	ReasonCode   string `json:"reason_code"`
}

func (r internalEvaluationCaseOutcomeRequest) attemptToken() string { return r.AttemptToken }

// handleInternalEvaluationCaseOutcome records one case decision through the
// job-scoped progress bridge (F13A/T065). It is maintenance-capacity only and
// re-proves the live attempt token, exactly like the run bridge.
func (s *Server) handleInternalEvaluationCaseOutcome(w http.ResponseWriter, r *http.Request) {
	var request internalEvaluationCaseOutcomeRequest
	job, ok := s.withJobData(w, r, &request)
	if !ok {
		return
	}
	err := s.evaluationRuns.RecordCaseOutcome(
		r.Context(), job, request.CaseID, request.Ordinal, request.Verdict, request.ReasonCode,
	)
	if !s.writeEvaluationRunError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, map[string]bool{"recorded": true})
}
