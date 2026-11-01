// Package supervisor decides what to do about runs whose worker stopped
// heartbeating.
//
// The rule this skeleton implements is deliberately conservative: a stale
// worker's runs are moved to `recovery_pending`, never restarted. Re-executing
// a run whose process might still be alive is the failure mode that produces
// double writes, so the supervisor only ever parks work for a later, evidence-
// based recovery decision.
//
// Only one supervisor may act at a time; that is enforced with a PostgreSQL
// advisory lock rather than in-process state.
package supervisor

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"
)

// LeaseKey is the advisory-lock key that serialises supervisor passes.
const LeaseKey int64 = 0x0C4A_2E5A // "orchara" markers; value is arbitrary but stable.

// ErrNoStore is returned when the supervisor was not given a store.
var ErrNoStore = errors.New("supervisor requires a store")

// Store is the persistence the sweep needs. Kept as an interface so the
// decision logic can be tested without a live PostgreSQL instance.
type Store interface {
	TryAcquireLease(ctx context.Context) (bool, error)
	ReleaseLease(ctx context.Context) error
	StaleWorkers(ctx context.Context, cutoff time.Time) ([]string, error)
	LiveWorkerCount(ctx context.Context, cutoff time.Time) (int, error)
	MarkRunsRecoveryPending(ctx context.Context, workerIDs []string) (int, error)
}

// Result is what one sweep did, and what it refused to do.
//
// It never restarts anything: a stale worker's runs are parked in
// `recovery_pending`, and restarting needs evidence that the old process and its
// writes drained (see probes/p04_drain).
type Result struct {
	Skipped       bool     `json:"skipped"`
	SkipReason    string   `json:"skip_reason,omitempty"`
	StaleWorkers  []string `json:"stale_workers"`
	LiveWorkers   int      `json:"live_workers"`
	RunsParked    int      `json:"runs_parked"`
	CutoffSeconds float64  `json:"cutoff_seconds"`
}

// Config tunes the staleness window.
type Config struct {
	StaleAfter time.Duration
}

// Supervisor performs sweep passes against a Store.
type Supervisor struct {
	store Store
	cfg   Config
	now   func() time.Time
}

// New builds a Supervisor.
func New(store Store, cfg Config) (*Supervisor, error) {
	if store == nil {
		return nil, ErrNoStore
	}
	if cfg.StaleAfter <= 0 {
		cfg.StaleAfter = 60 * time.Second
	}
	return &Supervisor{store: store, cfg: cfg, now: time.Now}, nil
}

// WithClock overrides the clock (tests only).
func (s *Supervisor) WithClock(now func() time.Time) *Supervisor {
	s.now = now
	return s
}

// Sweep runs one pass. It never restarts a run.
func (s *Supervisor) Sweep(ctx context.Context) (Result, error) {
	result := Result{
		StaleWorkers:  []string{},
		CutoffSeconds: s.cfg.StaleAfter.Seconds(),
	}

	acquired, err := s.store.TryAcquireLease(ctx)
	if err != nil {
		return result, fmt.Errorf("cannot acquire supervisor lease: %w", err)
	}
	if !acquired {
		result.Skipped = true
		result.SkipReason = "another supervisor holds the lease"
		return result, nil
	}
	defer func() {
		// Best effort: a failed release only delays the next pass.
		_ = s.store.ReleaseLease(ctx)
	}()

	cutoff := s.now().Add(-s.cfg.StaleAfter)
	stale, err := s.store.StaleWorkers(ctx, cutoff)
	if err != nil {
		return result, fmt.Errorf("cannot list stale workers: %w", err)
	}
	result.StaleWorkers = stale

	live, err := s.store.LiveWorkerCount(ctx, cutoff)
	if err != nil {
		return result, fmt.Errorf("cannot count live workers: %w", err)
	}
	result.LiveWorkers = live

	if len(stale) == 0 {
		return result, nil
	}

	parked, err := s.store.MarkRunsRecoveryPending(ctx, stale)
	if err != nil {
		return result, fmt.Errorf("cannot park runs: %w", err)
	}
	result.RunsParked = parked
	return result, nil
}

// PostgresStore is the real implementation.
type PostgresStore struct {
	db *sql.DB
}

// NewPostgresStore wraps a database handle.
func NewPostgresStore(db *sql.DB) *PostgresStore { return &PostgresStore{db: db} }

// TryAcquireLease takes a session-scoped advisory lock.
func (p *PostgresStore) TryAcquireLease(ctx context.Context) (bool, error) {
	var acquired bool
	err := p.db.QueryRowContext(ctx, "SELECT pg_try_advisory_lock($1)", LeaseKey).Scan(&acquired)
	return acquired, err
}

// ReleaseLease releases the advisory lock.
func (p *PostgresStore) ReleaseLease(ctx context.Context) error {
	_, err := p.db.ExecContext(ctx, "SELECT pg_advisory_unlock($1)", LeaseKey)
	return err
}

// StaleWorkers lists workers whose heartbeat is older than cutoff and which
// never recorded a clean stop.
func (p *PostgresStore) StaleWorkers(ctx context.Context, cutoff time.Time) ([]string, error) {
	rows, err := p.db.QueryContext(ctx, `
		SELECT worker_id FROM app.workers
		WHERE stopped_at IS NULL AND heartbeat_at < $1
		ORDER BY worker_id`, cutoff)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	workers := make([]string, 0, 4)
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		workers = append(workers, id)
	}
	return workers, rows.Err()
}

// LiveWorkerCount counts workers still heartbeating.
func (p *PostgresStore) LiveWorkerCount(ctx context.Context, cutoff time.Time) (int, error) {
	var count int
	err := p.db.QueryRowContext(ctx, `
		SELECT count(*) FROM app.workers
		WHERE stopped_at IS NULL AND heartbeat_at >= $1`, cutoff).Scan(&count)
	return count, err
}

// MarkRunsRecoveryPending parks running runs owned by the given workers.
func (p *PostgresStore) MarkRunsRecoveryPending(ctx context.Context, workerIDs []string) (int, error) {
	if len(workerIDs) == 0 {
		return 0, nil
	}
	rows, err := p.db.QueryContext(ctx, `
		UPDATE app.runs SET state = 'recovery_pending', updated_at = now()
		WHERE state = 'running' AND worker_id = ANY($1::text[])
		RETURNING run_id`, workerIDs)
	if err != nil {
		return 0, err
	}
	defer rows.Close()

	parked := 0
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return 0, err
		}
		parked++
	}
	return parked, rows.Err()
}
