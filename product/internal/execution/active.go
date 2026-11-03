package execution

// F03/T034: cross-attempt active-time accounting and the active-time deadline.
//
// One rule shapes this whole file:
//
//	projected active = runs.active_elapsed_ms
//	                 + (phase='executing' and a live, unsettled attempt
//	                    ? now() - attempt.started_at
//	                    : 0)
//
// The settled total is the authoritative history across attempts; the live
// interval is added (by the database clock, never by a Go wall clock) only while
// the run is actually executing. Waiting (approval/children), finalizing,
// cancelling, recovery and terminal do not accrue.
//
// Settlement happens in the same transaction as every transition out of
// executing (result proposal/finalizing, waiting, cancelling, exit, terminal,
// recovery). It locks nothing new: the caller already holds the run and attempt
// rows. The `active_settled_at IS NULL` predicate is the idempotency key, so a
// repeated finalize/exit never adds the same interval twice and a new attempt
// never resets the accumulated total.
//
// The deadline is a frozen Run fact (`max_active_ms`). NULL means "unknown / no
// deadline" and is never read as zero. At/over the limit the control intent is
// recorded once (cancel_requested_at + error_code='deadline_exceeded'); once the
// execution unit exits, the T031 finalizer commits failed(deadline_exceeded) with
// no output artifact, reusing the one finalizer rather than forking it.
//
// T036 will refine cancel priority and the cancelling state. T034's deadline path
// is deliberately the pre-cancel version: it records intent and lets the
// supervisor's exit evidence drive the single finalizer.

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"
)

// DeadlineErrorCode is the stable Run error_code for an active-time deadline and
// the 409 code the result endpoint answers once the limit is reached.
const DeadlineErrorCode = "deadline_exceeded"

// runActiveFacts is one Run's accounting facts, read under its row lock.
type runActiveFacts struct {
	ActiveElapsedMS   int64
	MaxActiveMS       sql.NullInt64
	Phase             string
	ErrorCode         sql.NullString
	CancelRequestedAt sql.NullTime
}

// DeadlineState is the decision shared by claim, heartbeat, result submission,
// exit and the finalizer. Over is true only when the limit is known and the
// projected total has reached it.
type DeadlineState struct {
	ActiveElapsedMS int64
	MaxActiveMS     sql.NullInt64
	ProjectedMS     int64
	Over            bool
}

// ActiveTime is the owner-facing projection rendered by GET run detail. A nil
// MaxActiveMS means the limit is unknown (no deadline); the header is then
// omitted instead of rendered as 0.
type ActiveTime struct {
	ActiveElapsedMS  int64
	MaxActiveMS      *int64
	DeadlineExceeded bool
}

// deadlineExceeded is the one comparison: a NULL limit is unknown, never zero.
func deadlineExceeded(maxActiveMS sql.NullInt64, projectedMS int64) bool {
	if !maxActiveMS.Valid {
		return false
	}
	return projectedMS >= maxActiveMS.Int64
}

// projectedActiveMS applies the phase rule. hasLive is false for a settled,
// exited, revoked or absent attempt; Go never inspects a timestamp here.
func projectedActiveMS(settledMS int64, phase string, liveMS int64, hasLive bool) int64 {
	if phase != "executing" || !hasLive || liveMS <= 0 {
		return settledMS
	}
	return settledMS + liveMS
}

// settleAttemptActiveTx settles every still-unsettled attempt of one Run once:
// active_ms = GREATEST(0, now-started_at), active_settled_at = now, and the same
// delta is added to runs.active_elapsed_ms. Returns the delta added (0 on a
// repeated call). Callers must already hold the Run and attempt row locks in the
// transaction that performs the transition out of executing.
func settleAttemptActiveTx(ctx context.Context, tx *sql.Tx, runID string) (int64, error) {
	return settleAttemptActiveEndingTx(ctx, tx, runID, nil)
}

// settleAttemptActiveUntilTx settles a still-unsettled attempt as of an explicit
// provable activity end instead of the wall clock. The lease sweep uses it to
// settle at `lease_deadline` (the last moment the platform could prove the unit
// was executing); charging the parked window would corrupt T034's accumulated
// active time and could fail the next claim with deadline_exceeded.
func settleAttemptActiveUntilTx(
	ctx context.Context, tx *sql.Tx, runID string, until time.Time,
) (int64, error) {
	if until.IsZero() {
		return settleAttemptActiveTx(ctx, tx, runID)
	}
	value := until.UTC()
	return settleAttemptActiveEndingTx(ctx, tx, runID, &value)
}

func settleAttemptActiveEndingTx(
	ctx context.Context, tx *sql.Tx, runID string, until *time.Time,
) (int64, error) {
	rows, err := tx.QueryContext(ctx, `
		UPDATE platform.run_attempts
		   SET active_ms = GREATEST(
		           0,
		           floor(extract(epoch FROM (COALESCE($2::timestamptz, now()) - started_at)) * 1000)::bigint
		       ),
		       active_settled_at = now()
		 WHERE run_id = $1::uuid
		   AND active_settled_at IS NULL
		RETURNING active_ms`,
		runID, until,
	)
	if err != nil {
		return 0, fmt.Errorf("settling the attempt active time: %w", err)
	}
	var delta int64
	for rows.Next() {
		var activeMS int64
		if err := rows.Scan(&activeMS); err != nil {
			rows.Close()
			return 0, fmt.Errorf("reading the settled attempt active time: %w", err)
		}
		delta += activeMS
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return 0, fmt.Errorf("settling the attempt active time: %w", err)
	}
	rows.Close()
	if delta == 0 {
		return 0, nil
	}
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET active_elapsed_ms = active_elapsed_ms + $2
		 WHERE id = $1::uuid`,
		runID, delta,
	); err != nil {
		return 0, fmt.Errorf("adding the settled attempt active time to the run: %w", err)
	}
	return delta, nil
}

// lockRunActiveFactsTx locks the Run and reads its accounting facts. A missing or
// deleted Run is ErrRunNotFound; callers on the execution protocol map that to
// their own 404/409 vocabulary.
func lockRunActiveFactsTx(
	ctx context.Context, tx *sql.Tx, runID string,
) (*runActiveFacts, error) {
	var facts runActiveFacts
	err := tx.QueryRowContext(ctx, `
		SELECT active_elapsed_ms, max_active_ms, phase, error_code, cancel_requested_at
		  FROM platform.runs
		 WHERE id = $1::uuid
		   AND deleted_at IS NULL
		 FOR UPDATE`,
		runID,
	).Scan(
		&facts.ActiveElapsedMS, &facts.MaxActiveMS, &facts.Phase,
		&facts.ErrorCode, &facts.CancelRequestedAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrRunNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("reading the run active-time facts: %w", err)
	}
	return &facts, nil
}

// liveAttemptActiveMSTx is the database-clock length of the live, unsettled
// attempt of one Run (0/false when there is none). A settled attempt is already
// included in runs.active_elapsed_ms and must not be added again.
func liveAttemptActiveMSTx(
	ctx context.Context, tx *sql.Tx, runID string,
) (int64, bool, error) {
	var liveMS int64
	err := tx.QueryRowContext(ctx, `
		SELECT GREATEST(
		           0,
		           floor(extract(epoch FROM (now() - started_at)) * 1000)::bigint
		       )
		  FROM platform.run_attempts
		 WHERE run_id = $1::uuid
		   AND exited_at IS NULL
		   AND revoked_at IS NULL
		   AND active_settled_at IS NULL
		 ORDER BY started_at DESC
		 LIMIT 1`,
		runID,
	).Scan(&liveMS)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, false, nil
	}
	if err != nil {
		return 0, false, fmt.Errorf("reading the live attempt active time: %w", err)
	}
	return liveMS, true, nil
}

// deadlineStateOf builds the decision from already-locked facts.
func deadlineStateOf(facts *runActiveFacts, liveMS int64, hasLive bool) DeadlineState {
	projected := projectedActiveMS(facts.ActiveElapsedMS, facts.Phase, liveMS, hasLive)
	return DeadlineState{
		ActiveElapsedMS: facts.ActiveElapsedMS,
		MaxActiveMS:     facts.MaxActiveMS,
		ProjectedMS:     projected,
		Over:            deadlineExceeded(facts.MaxActiveMS, projected),
	}
}

// markDeadlineIntentTx records the pre-cancel deadline intent exactly once:
// cancel_requested_at keeps its first value (COALESCE) and error_code is raised
// to deadline_exceeded only when no failure reason is already stored. The caller
// already holds the Run row lock.
func markDeadlineIntentTx(ctx context.Context, tx *sql.Tx, runID string) error {
	if _, err := tx.ExecContext(ctx, `
		UPDATE platform.runs
		   SET cancel_requested_at = COALESCE(cancel_requested_at, now()),
		       error_code = CASE WHEN error_code IS NULL THEN $2 ELSE error_code END
		 WHERE id = $1::uuid`,
		runID, DeadlineErrorCode,
	); err != nil {
		return fmt.Errorf("recording the active-time deadline intent: %w", err)
	}
	return nil
}

// checkDeadlineTx reads the Run under its lock, applies the phase rule with the
// database clock and records the intent when at/over. It does not settle (the
// live interval keeps accruing until a transition out of executing); settlement
// callers use settleAttemptActiveTx first.
func checkDeadlineTx(ctx context.Context, tx *sql.Tx, runID string) (DeadlineState, error) {
	facts, err := lockRunActiveFactsTx(ctx, tx, runID)
	if err != nil {
		return DeadlineState{}, err
	}
	liveMS, hasLive, err := liveAttemptActiveMSTx(ctx, tx, runID)
	if err != nil {
		return DeadlineState{}, err
	}
	state := deadlineStateOf(facts, liveMS, hasLive)
	if state.Over {
		if err := markDeadlineIntentTx(ctx, tx, runID); err != nil {
			return DeadlineState{}, err
		}
	}
	return state, nil
}

// ActiveTime reads the owner-facing active time of one Run without locking or
// writing. Unknown/deleted runs return nil so the caller keeps its own 404 rule.
func activeTimeFacts(ctx context.Context, db *sql.DB, runID string) (*ActiveTime, error) {
	var (
		activeElapsedMS int64
		maxActiveMS     sql.NullInt64
		phase           string
		errorCode       sql.NullString
		liveMS          int64
		hasLive         bool
	)
	err := db.QueryRowContext(ctx, `
		SELECT r.active_elapsed_ms, r.max_active_ms, r.phase, r.error_code,
		       COALESCE(live.ms, 0) AS live_ms,
		       live.ms IS NOT NULL AS has_live
		  FROM platform.runs AS r
		  LEFT JOIN LATERAL (
		      SELECT GREATEST(
		                 0,
		                 floor(extract(epoch FROM (now() - a.started_at)) * 1000)::bigint
		             ) AS ms
		        FROM platform.run_attempts AS a
		       WHERE a.run_id = r.id
		         AND a.exited_at IS NULL
		         AND a.revoked_at IS NULL
		         AND a.active_settled_at IS NULL
		       ORDER BY a.started_at DESC
		       LIMIT 1
		  ) AS live ON true
		 WHERE r.id = $1::uuid
		   AND r.deleted_at IS NULL`,
		runID,
	).Scan(&activeElapsedMS, &maxActiveMS, &phase, &errorCode, &liveMS, &hasLive)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading the run active time: %w", err)
	}
	projected := projectedActiveMS(activeElapsedMS, phase, liveMS, hasLive)
	active := &ActiveTime{
		ActiveElapsedMS: projected,
		DeadlineExceeded: errorCode.Valid && errorCode.String == DeadlineErrorCode ||
			deadlineExceeded(maxActiveMS, projected),
	}
	if maxActiveMS.Valid {
		max := maxActiveMS.Int64
		active.MaxActiveMS = &max
	}
	return active, nil
}

// ActiveTime returns the owner-facing accumulated/projected active time for one
// Run. It is the non-contract Run-detail extension (the contract Run schema is
// additionalProperties:false and has no active-time fields).
func (s *Service) ActiveTime(ctx context.Context, runID string) (*ActiveTime, error) {
	return activeTimeFacts(ctx, s.DB, runID)
}
