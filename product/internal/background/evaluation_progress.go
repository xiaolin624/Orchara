package background

// F13A/T065: the async evaluation progress read.
//
// The contract's evaluation reads are the six-field EvaluationJob and the
// four-field Report; neither says how far an asynchronous evaluation has got,
// and the Report only exists once the run is over. This file is the server half
// of the documented non-contract read (ADR 0017):
//
//	GET /api/v1/evaluations/{evaluation_id}/progress
//
// It returns only counters that are honestly derivable from platform rows —
// never a percentage guessed from `state`:
//
//	total_cases      the dataset's case count, frozen at submission
//	completed_cases  cases with a runner verdict or a run in a rest state
//	failed_cases     cases the runner decided as failed
//	report_id        null until the completion guard records a report
//	report_status    the immutable report's own status once it exists
//	failure_class    the evaluation-level class below, never a retry guess
//	created_at/finished_at  the evaluation row's real timestamps
//
// The failure class is the distinction the ticket exists for:
//
//	business        a report exists with status=failed or not_run; the job and
//	                the evaluation succeeded, so the verdict is about the
//	                evaluated application (not_run is a report status, never a
//	                job state)
//	infrastructure  no report exists and the evaluation failed: the job failed,
//	                so the platform could not produce a verdict at all
//	null            still running, cancelled, or a passed report
//
// This evaluation-level class is deliberately not the background job's retry
// classification (`background_jobs.failure_class`): a deterministic platform
// refusal and a transient outage both leave the evaluation without a report,
// and both are "no verdict was produced" from the creator's point of view. The
// job's own class remains visible on its own contract read.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"orchara/platform/internal/uuid"
)

// Evaluation-level failure classes. They are the progress surface's vocabulary,
// not the retry vocabulary in job.go (both use the strings business and
// infrastructure, but they answer different questions: this one answers "is
// there a report verdict?", the job one answers "may this attempt be retried?").
const (
	EvaluationFailureBusiness       = "business"
	EvaluationFailureInfrastructure = "infrastructure"

	// reportStatusPassed is the only report status that is not a failure.
	reportStatusPassed = "passed"
)

// EvaluationProgress is one evaluation's honest progress projection.
type EvaluationProgress struct {
	EvaluationID string
	State        string
	TotalCases   int64
	Completed    int64
	Failed       int64
	ReportID     *string
	ReportStatus *string
	ErrorCode    *string
	FailureClass *string
	CreatedAt    time.Time
	FinishedAt   *time.Time
}

// GetEvaluationProgress reads one evaluation's progress for its owner or an
// administrator. Everyone else gets ErrEvaluationNotFound, the same answer as a
// missing row, so ids cannot be enumerated. The read never writes.
func (c *Controller) GetEvaluationProgress(
	ctx context.Context,
	evaluationID, principalID string,
	admin bool,
) (*EvaluationProgress, error) {
	if !uuid.Valid(evaluationID) || !uuid.Valid(principalID) {
		return nil, ErrEvaluationNotFound
	}
	var (
		progress     EvaluationProgress
		reportID     sql.NullString
		reportStatus sql.NullString
		errorCode    sql.NullString
		finishedAt   sql.NullTime
	)
	err := c.DB.QueryRowContext(ctx, `
		SELECT e.id::text, e.state, e.progress_total, e.progress_done, e.progress_failed,
		       e.report_id::text, r.status, e.error_code, e.created_at, e.finished_at
		  FROM platform.evaluation_jobs AS e
		  LEFT JOIN platform.evaluation_reports AS r ON r.id = e.report_id
		 WHERE e.id = $1::uuid
		   AND (e.owner_id = $2::uuid OR $3::boolean)`,
		evaluationID, principalID, admin,
	).Scan(
		&progress.EvaluationID, &progress.State, &progress.TotalCases,
		&progress.Completed, &progress.Failed,
		&reportID, &reportStatus, &errorCode,
		&progress.CreatedAt, &finishedAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrEvaluationNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the evaluation progress: %w", err)
	}
	progress.ReportID = nullString(reportID)
	progress.ReportStatus = nullString(reportStatus)
	progress.ErrorCode = nullString(errorCode)
	if finishedAt.Valid {
		value := finishedAt.Time
		progress.FinishedAt = &value
	}
	progress.FailureClass = evaluationFailureClass(progress.State, progress.ReportStatus)
	return &progress, nil
}

// evaluationFailureClass maps (evaluation state, report status) to the
// evaluation-level class described in the file header. It is a pure function so
// the rule is unit-testable without a database, and it never looks at a
// percentage, a clock or the job's retry class.
func evaluationFailureClass(state string, reportStatus *string) *string {
	if reportStatus != nil {
		if *reportStatus == reportStatusPassed {
			return nil
		}
		// failed or not_run: the job completed and produced a business verdict.
		value := EvaluationFailureBusiness
		return &value
	}
	if state == "failed" {
		// The evaluation failed without a report: no verdict was produced.
		value := EvaluationFailureInfrastructure
		return &value
	}
	return nil
}
