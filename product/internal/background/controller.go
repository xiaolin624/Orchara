package background

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgconn"

	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// Lease and retry parameters fixed by BACKGROUND-JOBS.md §1 and the F00B plan.
const (
	// LeaseTTL is how long a claim owns a job. The maintenance loop heartbeats
	// every HeartbeatInterval (5s), so two heartbeats may be lost before the
	// lease expires.
	LeaseTTL = 30 * time.Second
	// MaxAttempts is the retry ceiling for retryable (infrastructure) failures.
	MaxAttempts = 3
	// DefaultRetryBackoff is the base delay before the second attempt; the base
	// is configurable with ORCHARA_BACKGROUND_RETRY_BACKOFF (tests use a small
	// value to keep retries deterministic without a long wall-clock wait).
	DefaultRetryBackoff = 5 * time.Second
	// maxRetryBackoff caps a computed delay so a large base cannot park a job
	// for days.
	maxRetryBackoff = time.Hour

	sweepBatch = 100
)

// Controller owns the background-job state machine over one database handle and
// the object store used by the completion guard.
type Controller struct {
	DB *sql.DB
	// Store is required for artifact_finalize activation. When nil, a completion
	// that would activate a sealed object is refused (fail closed) with
	// ErrObjectStoreUnavailable; nothing is changed.
	Store *objectstore.Store
	// RetryBackoff is the base delay for the first retry; RetryDelay doubles it
	// per attempt. Values <= 0 fall back to DefaultRetryBackoff.
	RetryBackoff time.Duration
	// Notifier is the best-effort wakeup hook called after Enqueue commits. It is
	// never required for delivery (claim polls PostgreSQL); nil means NopNotifier.
	Notifier Notifier
	// QueueAdvancer releases a conversation slot after a terminal route_request
	// failure (F08/T079). It is injected by the process that owns the request
	// aggregate so this package does not import internal/requests (which imports
	// this package). Nil means the completion guard cannot advance the queue; the
	// request is still failed, never left in routing.
	QueueAdvancer func(ctx context.Context, tx *sql.Tx, conversationID string) (string, error)
}

// New builds a Controller. store may be nil only in processes that never
// activate artifacts; the completion guard handles that case fail-closed.
// notifier may be nil: a process without a wakeup transport installs the no-op
// notifier, and losing every notification is a supported state.
func New(
	db *sql.DB,
	store *objectstore.Store,
	retryBackoff time.Duration,
	notifier Notifier,
) *Controller {
	if retryBackoff <= 0 {
		retryBackoff = DefaultRetryBackoff
	}
	if notifier == nil {
		notifier = NopNotifier{}
	}
	return &Controller{DB: db, Store: store, RetryBackoff: retryBackoff, Notifier: notifier}
}

// RetryDelay is the deterministic exponential backoff for an attempt that just
// failed: base * 2^(attempt-1), capped at one hour. attemptCount is the claim's
// attempt_count (so the first failure computes base * 2^0).
func RetryDelay(attemptCount int, base time.Duration) time.Duration {
	if base <= 0 {
		base = DefaultRetryBackoff
	}
	if attemptCount < 1 {
		attemptCount = 1
	}
	delay := base
	for i := 1; i < attemptCount; i++ {
		delay *= 2
		if delay >= maxRetryBackoff {
			return maxRetryBackoff
		}
	}
	if delay > maxRetryBackoff {
		return maxRetryBackoff
	}
	return delay
}

// validateUUID trims raw and reports whether it is a UUID; it returns the
// trimmed value so comparisons use the canonical spelling.
func validateUUID(field, raw string) (string, error) {
	trimmed := strings.TrimSpace(raw)
	if !uuid.Valid(trimmed) {
		return "", &ValidationError{Field: field, Message: field + " must be a UUID"}
	}
	return trimmed, nil
}

// isUniqueViolation reports whether err is PostgreSQL's 23505 on constraint.
// Constraint names are constants in this package, never user input.
func isUniqueViolation(err error, constraint string) bool {
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != "23505" {
		return false
	}
	return constraint == "" || pgErr.ConstraintName == constraint
}
