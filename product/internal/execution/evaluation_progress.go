package execution

// F13A/T065: the trusted runner's per-case progress report.
//
// The collector decides one case at a time. Some decisions have no run at all
// (an unsupported target, a refused collection), and a run's state alone cannot
// express a verdict (a cancelled run with a failed case). The handler therefore
// reports every decision through this job-scoped service *as it happens*, so the
// progress read can move while the evaluation is still running.
//
// The handler never writes a platform row: this service re-proves the job lease
// (through the API's withJobData wrapper) and the evaluation owner's current
// access (loadEvaluationScope: the application still belongs to the owner and is
// not archived, the snapshot owner matches), then upserts the case row and
// recomputes the evaluation's counters in one transaction.
//
// Progress is a live convenience, not the verdict authority: the sealed report
// is the record of truth and the completion guard writes the final counters from
// it. A retried attempt that reports the same case again is idempotent (the
// primary key is (evaluation_id, case_id)); a later report overwrites the
// verdict and reason.

import (
	"context"
	"fmt"
	"strings"

	"orchara/platform/internal/background"
	"orchara/platform/internal/evaluation"
)

// MaxCaseReasonCodeLength bounds the reason code a progress report may carry. It
// matches the column's CHECK constraint (0035).
const MaxCaseReasonCodeLength = 128

// caseVerdicts is the runner's closed decision vocabulary.
var caseVerdicts = map[string]bool{
	evaluation.VerdictPassed:  true,
	evaluation.VerdictFailed:  true,
	evaluation.VerdictBlocked: true,
	evaluation.VerdictNotRun:  true,
}

// RecordCaseOutcome persists one case's decision through the job-scoped progress
// bridge and recomputes the evaluation's coarse counters.
func (e *EvaluationRuns) RecordCaseOutcome(
	ctx context.Context,
	job *background.Job,
	caseID string,
	ordinal int,
	verdict, reasonCode string,
) error {
	caseID = strings.TrimSpace(caseID)
	if caseID == "" || len(caseID) > evaluation.MaxCaseIDLength {
		return &background.ValidationError{
			Field: "case_id", Message: "case_id must be a non-empty case identifier",
		}
	}
	if ordinal < 0 {
		return &background.ValidationError{
			Field: "ordinal", Message: "ordinal must be zero or greater",
		}
	}
	verdict = strings.TrimSpace(verdict)
	if !caseVerdicts[verdict] {
		return &background.ValidationError{
			Field: "verdict", Message: "verdict must be passed, failed, blocked or not_run",
		}
	}
	reasonCode = strings.TrimSpace(reasonCode)
	if len(reasonCode) > MaxCaseReasonCodeLength {
		return &background.ValidationError{
			Field:   "reason_code",
			Message: fmt.Sprintf("reason_code must be at most %d characters", MaxCaseReasonCodeLength),
		}
	}

	// The ACL gate: the job must own this evaluation and the evaluation's owner
	// must still be allowed to reach the application/snapshot.
	if _, err := e.loadEvaluationScope(ctx, job); err != nil {
		return err
	}

	tx, err := e.DB.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("starting the evaluation progress transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	// A case row normally exists (the run bridge created it). The insert branch
	// only fires for a case the runner decided without ever creating a run: the
	// row then records the observed decision state ('blocked'); the verdict is
	// the authoritative progress fact either way.
	insertState := "blocked"
	if verdict != evaluation.VerdictBlocked {
		insertState = "pending"
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO platform.evaluation_case_runs
		       (evaluation_id, case_id, ordinal, state, verdict, reason_code)
		VALUES ($1::uuid, $2, $3, $4, $5, NULLIF($6, ''))
		ON CONFLICT (evaluation_id, case_id) DO UPDATE
		   SET verdict = EXCLUDED.verdict,
		       reason_code = EXCLUDED.reason_code,
		       updated_at = now()`,
		job.TargetID, caseID, ordinal, insertState, verdict, reasonCode,
	); err != nil {
		return fmt.Errorf("recording the evaluation case outcome: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.evaluation_jobs
		   SET progress_done = `+evaluationProgressDoneSQL+`,
		       progress_failed = `+evaluationProgressFailedSQL+`
		 WHERE id = $1::uuid`, job.TargetID,
	); err != nil {
		return fmt.Errorf("updating the evaluation progress: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("committing the evaluation progress: %w", err)
	}
	return nil
}
