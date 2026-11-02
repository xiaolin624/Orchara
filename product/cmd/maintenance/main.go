// Command maintenance performs one maintenance pass and exits (F00 skeleton).
//
// Subcommands:
//
//	(none)  connectivity/visibility pass, one JSON report, exit 0
//	run     F00B/T022 maintenance client: claim a fixed kind, heartbeat,
//	        execute the compiled handler, submit the result
//
// The no-argument behaviour is deliberately unchanged: it proves the maintenance
// identity can reach the database and read the state the worker writes. The
// `run` subcommand is the background-job controller client and talks only to the
// internal mTLS API; it never invents work.
package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
)

const usage = `usage: orchara-maintenance [run]

  (no argument)  connectivity and visibility pass; prints one JSON report
  run            claim and execute background jobs over the internal mTLS API

run flags:
  --once                 claim until there is no ready job, then exit
  --kinds a,b            restrict to these kinds (must have a compiled handler)
  --poll-interval 2s     wait between empty claims (not used with --once)
  --worker-id NAME       identity recorded on the leased job
`

type report struct {
	Status    string `json:"status"`
	Database  string `json:"database"`
	Role      string `json:"role"`
	WorkTable string `json:"work_table,omitempty"`
	Pending   int    `json:"pending"`
	Note      string `json:"note"`
}

func main() {
	if len(os.Args) == 1 {
		connectivityPass()
		return
	}
	switch os.Args[1] {
	case "run":
		runMaintenance(os.Args[2:])
	case "help", "-h", "--help":
		fmt.Fprint(os.Stdout, usage)
	default:
		fmt.Fprintf(os.Stderr, "orchara-maintenance: unknown command %q\n\n%s", os.Args[1], usage)
		os.Exit(2)
	}
}

// connectivityPass is the F00 behaviour, unchanged.
func connectivityPass() {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	result, err := run(ctx)
	if err != nil {
		// Emit a machine-readable failure; never include the DSN.
		json.NewEncoder(os.Stdout).Encode(map[string]string{
			"status": "failed",
			"error":  err.Error(),
		})
		os.Exit(1)
	}
	json.NewEncoder(os.Stdout).Encode(result)
}

func run(ctx context.Context) (*report, error) {
	dsn := os.Getenv("ORCHARA_MAINTENANCE_DSN")
	if dsn == "" {
		return nil, errors.New("ORCHARA_MAINTENANCE_DSN is not set; refusing to run")
	}
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, errors.New("cannot open the maintenance database handle")
	}
	defer db.Close()

	var database, role string
	if err := db.QueryRowContext(ctx, "SELECT current_database(), current_user").Scan(&database, &role); err != nil {
		return nil, fmt.Errorf("maintenance cannot query the database: %w", err)
	}

	result := &report{
		Status:   "ok",
		Database: database,
		Role:     role,
		Note:     "skeleton: connectivity and visibility only; no retention work yet",
	}

	// Count the runtime's outbox when the probe schema is present, so the pass
	// reports something real instead of a constant.
	var exists bool
	if err := db.QueryRowContext(ctx,
		"SELECT to_regclass('app.operations') IS NOT NULL").Scan(&exists); err != nil {
		return nil, fmt.Errorf("cannot inspect the work table: %w", err)
	}
	if exists {
		result.WorkTable = "app.operations"
		if err := db.QueryRowContext(ctx, "SELECT count(*) FROM app.operations").Scan(&result.Pending); err != nil {
			return nil, fmt.Errorf("cannot count pending work: %w", err)
		}
	}
	return result, nil
}
