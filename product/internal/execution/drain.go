package execution

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
)

// checkDatabaseDrainedTx is the exit guard's database half: STATE-MACHINES §2
// says a Run may only enter a terminal state after the old execution's process,
// sandbox **and in-flight database writes** are proven gone. The worker reports
// its checkpoint connections on every heartbeat; this function re-checks them
// against pg_stat_activity instead of trusting the supervisor's self-asserted
// `db_writes_drained` flag.
//
// A connection is "not drained" when its backend is still active or holds an
// open transaction. An idle session (state='idle') has no write in flight and
// does not block finalization; a backend that has already disappeared does not
// either. When no connection was ever recorded the check is a no-op.
//
// attemptToken selects the exact attempt; an empty token means the latest
// attempt of the Run (the finalizer's defensive re-check).
func checkDatabaseDrainedTx(
	ctx context.Context, tx *sql.Tx, runID, attemptToken string,
) error {
	var (
		raw []byte
		err error
	)
	if attemptToken != "" {
		err = tx.QueryRowContext(ctx, `
			SELECT checkpoint_connections
			  FROM platform.run_attempts
			 WHERE token = $1::uuid
			   AND run_id = $2::uuid`,
			attemptToken, runID,
		).Scan(&raw)
	} else {
		err = tx.QueryRowContext(ctx, `
			SELECT checkpoint_connections
			  FROM platform.run_attempts
			 WHERE run_id = $1::uuid
			 ORDER BY started_at DESC
			 LIMIT 1`,
			runID,
		).Scan(&raw)
	}
	switch {
	case errors.Is(err, sql.ErrNoRows):
		return nil
	case err != nil:
		return fmt.Errorf("reading the checkpoint connections: %w", err)
	}
	if len(raw) == 0 {
		return nil
	}
	var connections []checkpointConnection
	if err := json.Unmarshal(raw, &connections); err != nil {
		// The heartbeat validator only stores an array of objects it produced;
		// an unreadable value is an internal defect. Refusing to finalize is the
		// safe side: the operator can still recover through the evidence path.
		return fmt.Errorf("decoding the checkpoint connections: %w", err)
	}
	for _, connection := range connections {
		if connection.PID == nil || connection.BackendStart == nil {
			// The heartbeat schema allows partial diagnostics; only a fully named
			// backend can be checked for in-flight writes.
			continue
		}
		var writing bool
		if err := tx.QueryRowContext(ctx, `
			SELECT EXISTS (
			    SELECT 1
			      FROM pg_stat_activity
			     WHERE pid = $1
			       AND backend_start = $2::timestamptz
			       AND state IN ('active', 'idle in transaction',
			                     'idle in transaction (aborted)', 'fastpath function call')
			)`, *connection.PID, *connection.BackendStart,
		).Scan(&writing); err != nil {
			return fmt.Errorf("checking the checkpoint connection drain: %w", err)
		}
		if writing {
			return ErrDatabaseWritesInFlight
		}
	}
	return nil
}
