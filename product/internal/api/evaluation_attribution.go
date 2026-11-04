package api

// F13A/T062: the administrator-only evaluation attribution read.
//
//	GET /api/v1/admin/evaluations/{evaluation_id}/attribution
//
// The contract's two evaluation reads are deliberately minimal (the six-field
// EvaluationJob and the four-field Report), so an administrator cannot check the
// account that produced a report, the cancellation outcome of a case, or the
// snapshot/manifest/dataset/compiler/worker identity behind it. T056 owns the
// per-case user read; this ticket adds a documented non-contract administrator
// surface (ADR 0014) instead of widening a contract projection.
//
// It is administrator-only: a non-admin caller gets 403 before any lookup, so
// the refusal cannot be used to probe for an evaluation's existence. The
// projection never contains a sealed object key, a report body, case input
// content or an artifact download URL.

import (
	"errors"
	"net/http"
	"time"

	"orchara/platform/internal/background"
	"orchara/platform/internal/identity"
)

type attributionOwnerView struct {
	PrincipalID string  `json:"principal_id"`
	Login       *string `json:"login"`
}

type attributionReportView struct {
	ID               string `json:"id"`
	Status           string `json:"status"`
	ManifestSHA256   string `json:"manifest_sha256"`
	DatasetSHA256    string `json:"dataset_sha256"`
	ResultArtifactID string `json:"result_artifact_id"`
}

type attributionCaseView struct {
	CaseID        string  `json:"case_id"`
	Ordinal       int64   `json:"ordinal"`
	CaseState     string  `json:"case_state"`
	ReasonCode    *string `json:"reason_code"`
	RunID         *string `json:"run_id"`
	RunState      *string `json:"run_state"`
	RunOwnerID    *string `json:"run_owner_id"`
	RunSnapshotID *string `json:"run_snapshot_id"`
	RunErrorCode  *string `json:"run_error_code"`
	Terminal      bool    `json:"terminal"`
	Completed     bool    `json:"completed"`
	UpdatedAt     string  `json:"updated_at"`
}

type evaluationAttributionView struct {
	EvaluationID       string                 `json:"evaluation_id"`
	Kind               string                 `json:"kind"`
	State              string                 `json:"state"`
	ErrorCode          *string                `json:"error_code"`
	BackgroundJobID    string                 `json:"background_job_id"`
	Owner              attributionOwnerView   `json:"owner"`
	ApplicationID      *string                `json:"application_id"`
	SnapshotID         *string                `json:"snapshot_id"`
	DraftRevision      *int64                 `json:"draft_revision"`
	ManifestSHA256     *string                `json:"manifest_sha256"`
	DatasetArtifactID  string                 `json:"dataset_artifact_id"`
	DatasetSHA256      *string                `json:"dataset_sha256"`
	CompilerVersion    *string                `json:"compiler_version"`
	WorkerImageDigest  *string                `json:"worker_image_digest"`
	StateSchemaVersion *string                `json:"state_schema_version"`
	CancelRequestedAt  *string                `json:"cancel_requested_at"`
	Report             *attributionReportView `json:"report"`
	Cases              []attributionCaseView  `json:"cases"`
	CreatedAt          string                 `json:"created_at"`
	FinishedAt         *string                `json:"finished_at"`
}

func formatOptionalTime(value *time.Time) *string {
	if value == nil {
		return nil
	}
	formatted := value.UTC().Format(time.RFC3339Nano)
	return &formatted
}

func viewOfAttribution(attribution *background.EvaluationAttribution) evaluationAttributionView {
	view := evaluationAttributionView{
		EvaluationID:       attribution.EvaluationID,
		Kind:               attribution.Kind,
		State:              attribution.State,
		ErrorCode:          attribution.ErrorCode,
		BackgroundJobID:    attribution.BackgroundJobID,
		Owner:              attributionOwnerView{PrincipalID: attribution.OwnerID, Login: attribution.OwnerLogin},
		ApplicationID:      attribution.ApplicationID,
		SnapshotID:         attribution.SnapshotID,
		DraftRevision:      attribution.DraftRevision,
		ManifestSHA256:     attribution.ManifestSHA256,
		DatasetArtifactID:  attribution.DatasetArtifactID,
		DatasetSHA256:      attribution.DatasetSHA256,
		CompilerVersion:    attribution.CompilerVersion,
		WorkerImageDigest:  attribution.WorkerImageDigest,
		StateSchemaVersion: attribution.StateSchemaVersion,
		CancelRequestedAt:  formatOptionalTime(attribution.CancelRequestedAt),
		CreatedAt:          attribution.CreatedAt.UTC().Format(time.RFC3339Nano),
		FinishedAt:         formatOptionalTime(attribution.FinishedAt),
		Cases:              make([]attributionCaseView, 0, len(attribution.Cases)),
	}
	if attribution.Report != nil {
		view.Report = &attributionReportView{
			ID:               attribution.Report.ID,
			Status:           attribution.Report.Status,
			ManifestSHA256:   attribution.Report.ManifestSHA256,
			DatasetSHA256:    attribution.Report.DatasetSHA256,
			ResultArtifactID: attribution.Report.ResultArtifactID,
		}
	}
	for _, item := range attribution.Cases {
		view.Cases = append(view.Cases, attributionCaseView{
			CaseID:        item.CaseID,
			Ordinal:       item.Ordinal,
			CaseState:     item.CaseState,
			ReasonCode:    item.ReasonCode,
			RunID:         item.RunID,
			RunState:      item.RunState,
			RunOwnerID:    item.RunOwnerID,
			RunSnapshotID: item.RunSnapshotID,
			RunErrorCode:  item.RunErrorCode,
			Terminal:      item.Terminal,
			Completed:     item.Completed,
			UpdatedAt:     item.UpdatedAt.UTC().Format(time.RFC3339Nano),
		})
	}
	return view
}

func (s *Server) handleGetEvaluationAttribution(w http.ResponseWriter, r *http.Request) {
	// The role check runs before any lookup, so a non-admin learns nothing about
	// whether the evaluation exists.
	caller, ok := s.authorize(w, r, identity.RoleAdmin)
	if !ok {
		return
	}
	if failure := requireScope(caller, identity.ScopeApplicationRead); failure != nil {
		writeError(w, r, failure.Status, failure.Code, failure.Message)
		return
	}
	attribution, err := s.jobs.GetEvaluationAttribution(r.Context(), r.PathValue("evaluation_id"))
	switch {
	case errors.Is(err, background.ErrEvaluationNotFound):
		writeError(w, r, http.StatusNotFound, "resource_not_found", "that evaluation does not exist")
		return
	case err != nil:
		s.internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, viewOfAttribution(attribution))
}
