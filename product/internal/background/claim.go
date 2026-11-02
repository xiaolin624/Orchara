package background

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"orchara/platform/internal/uuid"
)

// ClaimRequest is one maintenance client's availability announcement.
//
// WorkerID is recorded on the row for diagnostics. SupportedKinds is the set of
// handler kinds this process compiled in; the server intersects it with the
// whitelist and never accepts a kind the caller did not announce.
type ClaimRequest struct {
	WorkerID       string
	SupportedKinds []Kind
}

// Claim leases at most one ready job to the caller.
//
// The row is chosen with FOR UPDATE SKIP LOCKED, oldest available first, so two
// maintenance processes can never claim the same job. Before claiming, expired
// leases are recovered in the same transaction (an expired lease is an
// infrastructure failure: it returns to ready with backoff, or fails on the
// third attempt). A job is never handed out with a token from a previous
// attempt: every claim mints a fresh one.
//
// A nil job with a nil error means "no compatible ready job"; the HTTP layer
// answers 204.
func (c *Controller) Claim(ctx context.Context, request ClaimRequest) (*Job, error) {
	workerID := strings.TrimSpace(request.WorkerID)
	if workerID == "" {
		return nil, &ValidationError{Field: "worker_id", Message: "worker_id is required"}
	}
	if len(workerID) > 128 {
		return nil, &ValidationError{Field: "worker_id", Message: "worker_id must be at most 128 characters"}
	}
	for _, char := range workerID {
		if char < 0x20 || char == 0x7f {
			return nil, &ValidationError{Field: "worker_id", Message: "worker_id must be printable"}
		}
	}
	kindStrings := make([]string, 0, len(request.SupportedKinds))
	for _, kind := range request.SupportedKinds {
		kindStrings = append(kindStrings, string(kind))
	}
	kinds, err := parseKinds("supported_kinds", kindStrings)
	if err != nil {
		return nil, err
	}
	known := make([]string, 0, len(kinds))
	for _, kind := range kinds {
		known = append(known, string(kind))
	}

	tx, err := c.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("starting the claim transaction: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	if _, err := recoverExpiredTx(ctx, tx, c.RetryBackoff); err != nil {
		return nil, err
	}

	token := uuid.NewString()
	job, err := scanJob(tx.QueryRowContext(ctx, `
		UPDATE platform.background_jobs AS j
		   SET state = 'leased',
		       lease_token = $1::uuid,
		       lease_deadline = now() + make_interval(secs => $2::double precision),
		       worker_id = $3,
		       attempt_count = j.attempt_count + 1,
		       error_code = NULL,
		       last_error_at = NULL
		 WHERE j.id = (
		       SELECT c.id
		         FROM platform.background_jobs AS c
		        WHERE c.state = 'ready'
		          AND c.available_at <= now()
		          AND c.kind = ANY($4::text[])
		        ORDER BY c.available_at, c.id
		          FOR UPDATE SKIP LOCKED
		        LIMIT 1
		       )
		RETURNING `+selectJobColumns,
		token, LeaseTTL.Seconds(), workerID, known,
	))
	if errors.Is(err, sql.ErrNoRows) {
		if err := tx.Commit(); err != nil {
			return nil, fmt.Errorf("committing the claim transaction: %w", err)
		}
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("claiming a background job: %w", err)
	}
	// T026: leasing an evaluation job is what moves its business row to running.
	// A retry keeps it running (an attempt is still in flight).
	if err := markEvaluationRunning(ctx, tx, job); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("committing the claim transaction: %w", err)
	}
	return job, nil
}
