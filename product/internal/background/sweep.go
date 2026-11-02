package background

import (
	"context"
	"database/sql"
	"fmt"
	"time"
)

// SweepExpired recovers leases whose deadline passed without a result.
//
// Recovery is the same rule as a retryable failure: the attempt is already
// counted at claim time, so an expired lease either returns the job to ready
// with exponential backoff or, when the third attempt expired, fails it with
// failure_class=infrastructure and error_code=lease_expired. It never discards
// work silently and never lets a stale attempt submit later (the token is
// cleared by the same UPDATE).
func (c *Controller) SweepExpired(ctx context.Context) (int, error) {
	tx, err := c.DB.BeginTx(ctx, nil)
	if err != nil {
		return 0, fmt.Errorf("starting the sweep transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	count, err := recoverExpiredTx(ctx, tx, c.RetryBackoff)
	if err != nil {
		return 0, err
	}
	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("committing the sweep transaction: %w", err)
	}
	return count, nil
}

// recoverExpiredTx performs one bounded sweep inside tx. The delay is computed
// in Go with RetryDelay, the same function Complete uses, so the observable
// backoff cannot drift between the two recovery paths.
func recoverExpiredTx(ctx context.Context, tx *sql.Tx, base time.Duration) (int, error) {
	if base <= 0 {
		base = DefaultRetryBackoff
	}
	rows, err := tx.QueryContext(ctx, `
		SELECT id::text, attempt_count
		  FROM platform.background_jobs
		 WHERE state = 'leased' AND lease_deadline < now()
		 ORDER BY lease_deadline
		   FOR UPDATE SKIP LOCKED
		 LIMIT $1`, sweepBatch)
	if err != nil {
		return 0, fmt.Errorf("finding expired leases: %w", err)
	}
	type expired struct {
		id       string
		attempts int
	}
	var pending []expired
	for rows.Next() {
		var entry expired
		if err := rows.Scan(&entry.id, &entry.attempts); err != nil {
			_ = rows.Close()
			return 0, fmt.Errorf("reading expired leases: %w", err)
		}
		pending = append(pending, entry)
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return 0, fmt.Errorf("reading expired leases: %w", err)
	}
	_ = rows.Close()

	recovered := 0
	for _, entry := range pending {
		var err error
		if entry.attempts >= MaxAttempts {
			_, err = tx.ExecContext(ctx, `
				UPDATE platform.background_jobs
				   SET state = 'failed',
				       lease_token = NULL,
				       lease_deadline = NULL,
				       worker_id = NULL,
				       error_code = $2,
				       failure_class = $3,
				       last_error_at = now(),
				       finished_at = now()
				 WHERE id = $1::uuid`, entry.id, CodeLeaseExpired, string(FailureInfrastructure))
		} else {
			_, err = tx.ExecContext(ctx, `
				UPDATE platform.background_jobs
				   SET state = 'ready',
				       lease_token = NULL,
				       lease_deadline = NULL,
				       worker_id = NULL,
				       error_code = $2,
				       failure_class = $3,
				       last_error_at = now(),
				       available_at = now() + make_interval(secs => $4::double precision)
				 WHERE id = $1::uuid`,
				entry.id, CodeLeaseExpired, string(FailureInfrastructure),
				RetryDelay(entry.attempts, base).Seconds(),
			)
		}
		if err != nil {
			return recovered, fmt.Errorf("recovering an expired lease: %w", err)
		}
		recovered++
	}
	return recovered, nil
}

// AttemptsExhausted reports whether no further attempt is allowed after
// attemptCount claims. It is exported so the maintenance loop can explain why a
// job it is about to abandon will be failed by the server.
func AttemptsExhausted(attemptCount int) bool { return attemptCount >= MaxAttempts }
