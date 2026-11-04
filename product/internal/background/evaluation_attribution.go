package background

// F13A/T062: the administrator attribution read.
//
// An evaluation report is only trustworthy if an administrator can check *who*
// produced it, *which* snapshot/manifest/dataset/compiler/worker it ran under,
// and *how* every case actually ended (including a cancelled or never-finished
// run). The contract reads deliberately hide all of that: GET
// /api/v1/evaluations/{id} carries only the six contract fields and the report
// read carries four. This file is the server half of the documented,
// administrator-only, non-contract read that exposes exactly the attribution
// facts (ADR 0013):
//
//	evaluation owner principal + login, application, snapshot, draft revision,
//	manifest/dataset hashes, compiler version, worker image digest, state-schema
//	version, the immutable report row, and per-case run states.
//
// It deliberately never reads or returns a sealed object key, a report body, a
// case input, an artifact download URL or another user's content. The
// authorization rule (administrator only) lives in the HTTP handler; this read
// model has no principal parameter because it is not reachable by a non-admin.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"orchara/platform/internal/evaluation"
	"orchara/platform/internal/uuid"
)

// EvaluationAttribution is one evaluation's administrator attribution facts.
type EvaluationAttribution struct {
	EvaluationID       string
	Kind               string
	State              string
	ErrorCode          *string
	BackgroundJobID    string
	OwnerID            string
	OwnerLogin         *string
	ApplicationID      *string
	SnapshotID         *string
	DraftRevision      *int64
	ManifestSHA256     *string
	DatasetArtifactID  string
	DatasetSHA256      *string
	CompilerVersion    *string
	WorkerImageDigest  *string
	StateSchemaVersion *string
	CancelRequestedAt  *time.Time
	Report             *AttributionReport
	Cases              []AttributionCase
	CreatedAt          time.Time
	FinishedAt         *time.Time
}

// AttributionReport is the immutable report row's attribution subset. It has no
// body: the sealed report bytes stay behind the artifact routes.
type AttributionReport struct {
	ID               string
	Status           string
	ManifestSHA256   string
	DatasetSHA256    string
	ResultArtifactID string
}

// AttributionCase is one case's platform run and its observed state.
type AttributionCase struct {
	CaseID        string
	Ordinal       int64
	CaseState     string
	ReasonCode    *string
	RunID         *string
	RunState      *string
	RunOwnerID    *string
	RunSnapshotID *string
	RunErrorCode  *string
	// Terminal is true when the run can no longer transition (succeeded, failed
	// or cancelled). Completed is true only for a run that finished executing
	// (succeeded or failed): it is the only state that can back a passed case.
	Terminal  bool
	Completed bool
	UpdatedAt time.Time
}

// GetEvaluationAttribution reads one evaluation's attribution facts for an
// administrator. An unknown (or malformed) evaluation id answers exactly like a
// missing row; the caller maps that to the standard 404.
func (c *Controller) GetEvaluationAttribution(
	ctx context.Context, evaluationID string,
) (*EvaluationAttribution, error) {
	if !uuid.Valid(evaluationID) {
		return nil, ErrEvaluationNotFound
	}
	var (
		attribution       EvaluationAttribution
		errorCode         sql.NullString
		ownerLogin        sql.NullString
		applicationID     sql.NullString
		snapshotID        sql.NullString
		draftRevision     sql.NullInt64
		manifestSHA256    sql.NullString
		datasetSHA256     sql.NullString
		compilerVersion   sql.NullString
		workerImageDigest sql.NullString
		stateSchema       sql.NullString
		cancelRequestedAt sql.NullTime
		reportID          sql.NullString
		finishedAt        sql.NullTime
	)
	err := c.DB.QueryRowContext(ctx, `
		SELECT ev.id::text, ev.kind, ev.state, ev.error_code, ev.background_job_id::text,
		       ev.owner_id::text, u.login,
		       ev.application_id::text, ev.snapshot_id::text,
		       s.draft_revision, s.manifest_sha256, s.compiler_version,
		       s.worker_image_digest, s.state_schema_version,
		       ev.dataset_artifact_id::text, a.sha256,
		       ev.cancel_requested_at, ev.report_id::text,
		       ev.created_at, ev.finished_at
		  FROM platform.evaluation_jobs AS ev
		  LEFT JOIN platform.users AS u ON u.principal_id = ev.owner_id
		  LEFT JOIN platform.execution_snapshots AS s ON s.id = ev.snapshot_id
		  LEFT JOIN platform.artifacts AS a ON a.id = ev.dataset_artifact_id
		 WHERE ev.id = $1::uuid`, evaluationID,
	).Scan(
		&attribution.EvaluationID, &attribution.Kind, &attribution.State, &errorCode,
		&attribution.BackgroundJobID, &attribution.OwnerID, &ownerLogin,
		&applicationID, &snapshotID,
		&draftRevision, &manifestSHA256, &compilerVersion,
		&workerImageDigest, &stateSchema,
		&attribution.DatasetArtifactID, &datasetSHA256,
		&cancelRequestedAt, &reportID,
		&attribution.CreatedAt, &finishedAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrEvaluationNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the evaluation attribution: %w", err)
	}
	attribution.ErrorCode = nullString(errorCode)
	attribution.OwnerLogin = nullString(ownerLogin)
	attribution.ApplicationID = nullString(applicationID)
	attribution.SnapshotID = nullString(snapshotID)
	if draftRevision.Valid {
		value := draftRevision.Int64
		attribution.DraftRevision = &value
	}
	attribution.ManifestSHA256 = nullString(manifestSHA256)
	attribution.DatasetSHA256 = nullString(datasetSHA256)
	attribution.CompilerVersion = nullString(compilerVersion)
	attribution.WorkerImageDigest = nullString(workerImageDigest)
	attribution.StateSchemaVersion = nullString(stateSchema)
	if cancelRequestedAt.Valid {
		value := cancelRequestedAt.Time
		attribution.CancelRequestedAt = &value
	}
	if finishedAt.Valid {
		value := finishedAt.Time
		attribution.FinishedAt = &value
	}

	if reportID.Valid && reportID.String != "" {
		report, err := c.evaluationAttributionReport(ctx, reportID.String)
		if err != nil {
			return nil, err
		}
		attribution.Report = report
	}
	cases, err := c.evaluationAttributionCases(ctx, evaluationID)
	if err != nil {
		return nil, err
	}
	attribution.Cases = cases
	return &attribution, nil
}

func (c *Controller) evaluationAttributionReport(
	ctx context.Context, reportID string,
) (*AttributionReport, error) {
	var report AttributionReport
	err := c.DB.QueryRowContext(ctx, `
		SELECT id::text, status, manifest_sha256, dataset_sha256, result_artifact_id::text
		  FROM platform.evaluation_reports
		 WHERE id = $1::uuid`, reportID,
	).Scan(&report.ID, &report.Status, &report.ManifestSHA256, &report.DatasetSHA256,
		&report.ResultArtifactID)
	if errors.Is(err, sql.ErrNoRows) {
		// The evaluation points at a report row that does not exist: an integrity
		// failure, never something to paper over with an empty projection.
		return nil, fmt.Errorf("the evaluation's report row is missing")
	}
	if err != nil {
		return nil, fmt.Errorf("reading the evaluation report attribution: %w", err)
	}
	return &report, nil
}

func (c *Controller) evaluationAttributionCases(
	ctx context.Context, evaluationID string,
) ([]AttributionCase, error) {
	rows, err := c.DB.QueryContext(ctx, `
		SELECT cr.case_id, cr.ordinal, cr.state, cr.reason_code,
		       cr.run_id::text, r.state, r.owner_id::text,
		       r.execution_snapshot_id::text, r.error_code, cr.updated_at
		  FROM platform.evaluation_case_runs AS cr
		  LEFT JOIN platform.runs AS r ON r.id = cr.run_id
		 WHERE cr.evaluation_id = $1::uuid
		 ORDER BY cr.ordinal, cr.case_id`, evaluationID)
	if err != nil {
		return nil, fmt.Errorf("reading the evaluation case attribution: %w", err)
	}
	defer rows.Close()
	cases := make([]AttributionCase, 0)
	for rows.Next() {
		var (
			item        AttributionCase
			reasonCode  sql.NullString
			runID       sql.NullString
			runState    sql.NullString
			runOwner    sql.NullString
			runSnapshot sql.NullString
			runError    sql.NullString
		)
		if err := rows.Scan(
			&item.CaseID, &item.Ordinal, &item.CaseState, &reasonCode,
			&runID, &runState, &runOwner, &runSnapshot, &runError, &item.UpdatedAt,
		); err != nil {
			return nil, fmt.Errorf("reading the evaluation case attribution: %w", err)
		}
		item.ReasonCode = nullString(reasonCode)
		item.RunID = nullString(runID)
		item.RunState = nullString(runState)
		item.RunOwnerID = nullString(runOwner)
		item.RunSnapshotID = nullString(runSnapshot)
		item.RunErrorCode = nullString(runError)
		item.Terminal = item.RunState != nil && evaluation.TerminalRunState(*item.RunState)
		item.Completed = item.RunState != nil && evaluation.CompletedRunState(*item.RunState)
		cases = append(cases, item)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("reading the evaluation case attribution: %w", err)
	}
	return cases, nil
}
