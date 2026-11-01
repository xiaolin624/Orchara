// Command supervisor parks the runs of dead workers (F00 skeleton).
//
// One pass per invocation: take the supervisor lease, find workers that stopped
// heartbeating, move their running runs to `recovery_pending`, exit. It never
// restarts a run — restarting needs evidence that the old process and its writes
// drained, which is a later ticket.
package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"os"
	"strconv"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"

	"orchara/platform/internal/supervisor"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	result, err := run(ctx)
	if err != nil {
		// Machine-readable failure; never include the DSN.
		json.NewEncoder(os.Stdout).Encode(map[string]string{
			"status": "failed",
			"error":  err.Error(),
		})
		os.Exit(1)
	}
	json.NewEncoder(os.Stdout).Encode(result)
}

func run(ctx context.Context) (supervisor.Result, error) {
	dsn := os.Getenv("ORCHARA_SUPERVISOR_DSN")
	if dsn == "" {
		return supervisor.Result{}, errors.New("ORCHARA_SUPERVISOR_DSN is not set; refusing to run")
	}

	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return supervisor.Result{}, errors.New("cannot open the supervisor database handle")
	}
	defer db.Close()
	db.SetMaxOpenConns(1) // the advisory lock is session-scoped

	staleAfter := 60 * time.Second
	if raw := os.Getenv("ORCHARA_STALE_AFTER_SECONDS"); raw != "" {
		seconds, convErr := strconv.Atoi(raw)
		if convErr != nil || seconds <= 0 {
			return supervisor.Result{}, errors.New("ORCHARA_STALE_AFTER_SECONDS must be a positive integer")
		}
		staleAfter = time.Duration(seconds) * time.Second
	}

	svc, err := supervisor.New(supervisor.NewPostgresStore(db), supervisor.Config{StaleAfter: staleAfter})
	if err != nil {
		return supervisor.Result{}, err
	}
	return svc.Sweep(ctx)
}
