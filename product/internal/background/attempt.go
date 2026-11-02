package background

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"orchara/platform/internal/uuid"
)

// CheckAttempt proves that attemptToken is the live lease of jobID without
// changing it.
//
// The job-scoped artifact and model-call endpoints use this instead of
// Heartbeat: reading a fixture or minting an upload URL must not extend a lease
// (a handler that stopped working should not keep a job alive by polling). The
// answers are the same shape as every other control-plane refusal:
//
//	ErrNotFound     — the job does not exist (internal caller, so 404 is honest)
//	ErrStaleAttempt — not leased, another token, or the lease already expired
func (c *Controller) CheckAttempt(ctx context.Context, jobID, attemptToken string) (*Job, error) {
	if !uuid.Valid(jobID) {
		return nil, ErrNotFound
	}
	if !uuid.Valid(attemptToken) {
		return nil, &ValidationError{Field: "attempt_token", Message: "attempt_token must be a UUID"}
	}
	job, err := scanJob(c.DB.QueryRowContext(ctx, `
		SELECT `+selectJobColumns+`
		  FROM platform.background_jobs
		 WHERE id = $1::uuid`, jobID))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the background job: %w", err)
	}
	var dbNow sql.NullTime
	if err := c.DB.QueryRowContext(ctx, `SELECT now()`).Scan(&dbNow); err != nil {
		return nil, fmt.Errorf("reading the database clock: %w", err)
	}
	live := job.State == StateLeased && job.LeaseToken != nil &&
		*job.LeaseToken == attemptToken && job.LeaseDeadline != nil &&
		job.LeaseDeadline.After(dbNow.Time)
	if !live {
		return nil, ErrStaleAttempt
	}
	return job, nil
}
