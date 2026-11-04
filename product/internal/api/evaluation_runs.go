package api

// F13A/T052: the job-scoped evaluation-run bridge.
//
// The maintenance handler that runs an evaluation may not read or write platform
// business tables, and a maintenance certificate has no right to create a user's
// run. Two internal endpoints close exactly that gap (ADR 0006):
//
//	POST /internal/v1/background/jobs/{job_id}/evaluation-runs
//	  {attempt_token, case_id, input}            -> {run_id, created}
//	POST /internal/v1/background/jobs/{job_id}/evaluation-runs/{run_id}/facts
//	  {attempt_token}                            -> the independent truth
//	POST /internal/v1/background/jobs/{job_id}/evaluation-runs/{run_id}/interactions
//	  {attempt_token, interaction}                -> {run_id, run_state}
//
// They are maintenance-capacity only (mTLS role header + live attempt token, the
// same checks as every other job-scoped route), they act as the *evaluation's
// owner*, and they re-prove the owner's authorization on every call. The handler
// never names a run: the server creates it and returns its id.

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"orchara/platform/internal/background"
	"orchara/platform/internal/evaluation"
	"orchara/platform/internal/execution"
)

type internalEvaluationRunRequest struct {
	AttemptToken string          `json:"attempt_token"`
	CaseID       string          `json:"case_id"`
	Input        json.RawMessage `json:"input"`
}

func (r internalEvaluationRunRequest) attemptToken() string { return r.AttemptToken }

type internalEvaluationRunFactsRequest struct {
	AttemptToken string `json:"attempt_token"`
}

func (r internalEvaluationRunFactsRequest) attemptToken() string { return r.AttemptToken }

type internalEvaluationRunInteractionRequest struct {
	AttemptToken string `json:"attempt_token"`
	Interaction  string `json:"interaction"`
}

func (r internalEvaluationRunInteractionRequest) attemptToken() string { return r.AttemptToken }

type internalEvaluationRunResponse struct {
	RunID   string `json:"run_id"`
	Created bool   `json:"created"`
}

type internalEvaluationRunFactsResponse struct {
	RunID              string                   `json:"run_id"`
	State              string                   `json:"state"`
	ErrorCode          string                   `json:"error_code"`
	OutputArtifactID   *string                  `json:"output_artifact_id"`
	OutputSHA256       string                   `json:"output_sha256"`
	Output             json.RawMessage          `json:"output"`
	OutputCollected    bool                     `json:"output_collected"`
	EffectCounts       map[string]int64         `json:"effect_counts"`
	EffectsCollected   bool                     `json:"effects_collected"`
	Citations          []string                 `json:"citations"`
	CitationsCollected bool                     `json:"citations_collected"`
	Provenance         evaluation.RunProvenance `json:"provenance"`
	StartedAt          string                   `json:"started_at"`
	FinishedAt         string                   `json:"finished_at"`
}

func (s *Server) writeEvaluationRunError(w http.ResponseWriter, r *http.Request, err error) bool {
	switch {
	case err == nil:
		return true
	case errors.Is(err, background.ErrEvaluationNotFound):
		writeError(w, r, http.StatusNotFound, "evaluation_not_found",
			"that background job does not own an evaluation")
	case errors.Is(err, background.ErrEvaluationNotApplicable):
		writeError(w, r, http.StatusUnprocessableEntity, "evaluation_not_applicable",
			"this background job is not an application evaluation")
	case errors.Is(err, background.ErrEvaluationBindingUnavailable):
		writeError(w, r, http.StatusConflict, "evaluation_binding_unavailable",
			"the evaluation's bound manifest cannot be resolved in this deployment")
	case errors.Is(err, background.ErrObjectStoreUnavailable):
		writeError(w, r, http.StatusServiceUnavailable, "object_store_unavailable",
			"the object store is not configured")
	case errors.Is(err, execution.ErrEvaluationNotVisible):
		writeError(w, r, http.StatusConflict, "evaluation_not_visible",
			"the evaluation's owner can no longer access this application; the case cannot be collected")
	case errors.Is(err, execution.ErrEvaluationRunNotFound):
		writeError(w, r, http.StatusNotFound, "evaluation_run_not_found",
			"that run does not belong to this evaluation")
	default:
		var validation *background.ValidationError
		if errors.As(err, &validation) {
			writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
				validation.Error(), map[string]any{"field": validation.Field})
			return false
		}
		s.internalError(w, r, err)
	}
	return false
}

func (s *Server) handleInternalEvaluationRun(w http.ResponseWriter, r *http.Request) {
	var request internalEvaluationRunRequest
	job, ok := s.withJobData(w, r, &request)
	if !ok {
		return
	}
	ref, err := s.evaluationRuns.Create(r.Context(), job, request.CaseID, request.Input)
	if !s.writeEvaluationRunError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, internalEvaluationRunResponse{
		RunID: ref.RunID, Created: ref.Created,
	})
}

func (s *Server) handleInternalEvaluationRunFacts(w http.ResponseWriter, r *http.Request) {
	var request internalEvaluationRunFactsRequest
	job, ok := s.withJobData(w, r, &request)
	if !ok {
		return
	}
	facts, err := s.evaluationRuns.Facts(r.Context(), job, r.PathValue("run_id"))
	if !s.writeEvaluationRunError(w, r, err) {
		return
	}
	response := internalEvaluationRunFactsResponse{
		RunID:              facts.RunID,
		State:              facts.State,
		ErrorCode:          facts.ErrorCode,
		OutputArtifactID:   facts.OutputArtifactID,
		OutputSHA256:       facts.OutputSHA256,
		OutputCollected:    facts.OutputCollected,
		EffectCounts:       facts.Effects,
		EffectsCollected:   facts.EffectsCollected,
		Citations:          facts.Citations,
		CitationsCollected: facts.CitationsCollected,
		Provenance:         facts.Provenance,
		StartedAt:          evaluation.FormatTime(facts.StartedAt),
		FinishedAt:         evaluation.FormatTime(facts.FinishedAt),
	}
	if facts.OutputCollected && len(facts.Output) > 0 {
		response.Output = json.RawMessage(facts.Output)
	}
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) handleInternalEvaluationRunInteraction(w http.ResponseWriter, r *http.Request) {
	var request internalEvaluationRunInteractionRequest
	job, ok := s.withJobData(w, r, &request)
	if !ok {
		return
	}
	kind := strings.TrimSpace(request.Interaction)
	if !evaluation.InteractionKinds[kind] {
		writeErrorWithDetails(w, r, http.StatusUnprocessableEntity, "validation_failed",
			"this build performs exactly one predefined interaction, cancel",
			map[string]any{"field": "interaction"})
		return
	}
	ownerID, err := s.evaluationRuns.CaseRunOwner(r.Context(), job, r.PathValue("run_id"))
	if !s.writeEvaluationRunError(w, r, err) {
		return
	}
	// The real owner-facing cancel. It is the same transition a user's
	// POST /api/v1/runs/{id}/cancel performs, so a cancelled case's state is
	// the platform's own fact and not a runner convention.
	outcome, err := s.execution.Cancel(r.Context(), ownerID, r.PathValue("run_id"))
	if !s.writeEvaluationRunError(w, r, err) {
		return
	}
	state, err := s.evaluationRuns.RunState(r.Context(), job, outcome.RunID)
	if !s.writeEvaluationRunError(w, r, err) {
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{
		"run_id": outcome.RunID, "run_state": state,
	})
}
