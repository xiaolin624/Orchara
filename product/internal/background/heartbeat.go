package background

import (
	"context"
	"fmt"

	"orchara/platform/internal/uuid"
)

// Heartbeat renews the lease of one attempt.
//
// It succeeds only while the job is leased, the token is the current one and the
// lease has not already expired; otherwise ErrStaleAttempt (409) and nothing is
// changed. A heartbeat can never resurrect a job whose lease was recovered.
func (c *Controller) Heartbeat(ctx context.Context, jobID, attemptToken string) error {
	if !uuid.Valid(jobID) {
		return ErrNotFound
	}
	if !uuid.Valid(attemptToken) {
		return &ValidationError{Field: "attempt_token", Message: "attempt_token must be a UUID"}
	}
	var renewed, exists bool
	err := c.DB.QueryRowContext(ctx, `
		WITH renewed AS (
		    UPDATE platform.background_jobs
		       SET lease_deadline = now() + make_interval(secs => $3::double precision)
		     WHERE id = $1::uuid
		       AND state = 'leased'
		       AND lease_token = $2::uuid
		       AND lease_deadline > now()
		    RETURNING 1
		)
		SELECT (SELECT count(*) FROM renewed) > 0,
		       EXISTS (SELECT 1 FROM platform.background_jobs WHERE id = $1::uuid)`,
		jobID, attemptToken, LeaseTTL.Seconds(),
	).Scan(&renewed, &exists)
	if err != nil {
		return fmt.Errorf("renewing the background job lease: %w", err)
	}
	if renewed {
		return nil
	}
	if !exists {
		return ErrNotFound
	}
	return ErrStaleAttempt
}
