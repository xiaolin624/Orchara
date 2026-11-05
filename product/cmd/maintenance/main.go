// Command maintenance performs one maintenance pass and exits (F00 skeleton).
//
// Subcommands:
//
//	(none)  connectivity/visibility pass, one JSON report, exit 0
//	run     F00B/T022 maintenance client: claim fixed kinds, heartbeat,
//	        execute the compiled handlers, submit results
//	sweep   F00B/T026 object-store orphan cleanup
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

const usage = `usage: orchara-maintenance [run|sweep]

  (no argument)  connectivity and visibility pass; prints one JSON report
  run            claim and execute background jobs over the internal mTLS API
  sweep          delete provably-unreferenced staging and sealed objects

run flags:
  --once                 drain every pool until idle, then exit
  --kinds a,b            restrict to these kinds (must have a compiled handler)
  --poll-interval 2s     wait between empty claims (not used with --once)
  --worker-id NAME       base identity recorded on the leased job
  --system-io-slots N    concurrent artifact_finalize workers (default 2)
  --work-slots N         concurrent workers for every other kind (default 1)
  --dataset-wait-timeout D   how long one evaluation attempt waits for its dataset
  --evaluation-poll-interval D   evaluation status-poll/heartbeat cadence
  --report-wait-timeout D    how long one attempt waits for its report finalize
  --tool-timeout D       how long one trusted preset-tool subprocess may run
  --delete-content-timeout D   how long one physical content purge attempt may run

  Every pool/flag default can also come from the environment:
  ORCHARA_MAINTENANCE_SYSTEM_IO_SLOTS, ORCHARA_MAINTENANCE_WORK_SLOTS,
  ORCHARA_MAINTENANCE_DATASET_WAIT_TIMEOUT,
  ORCHARA_MAINTENANCE_EVALUATION_POLL_INTERVAL,
  ORCHARA_MAINTENANCE_REPORT_WAIT_TIMEOUT,
  ORCHARA_MAINTENANCE_TOOL_TIMEOUT,
  ORCHARA_MAINTENANCE_DELETE_CONTENT_TIMEOUT. The preset tool's interpreter and
  product root are ORCHARA_PYTHON and ORCHARA_PRODUCT_ROOT.

sweep flags:
  --orphan-age 1h   required; only delete objects at least this old
  --dry-run         report what would be deleted, delete nothing

  sweep needs ORCHARA_MAINTENANCE_DSN and ORCHARA_OBJECT_DIR (not the internal
  TLS client configuration): it reads references from PostgreSQL and deletes
  unreferenced staging objects plus sealed objects that no artifact row, run
  artifact, knowledge chunk or memory body names and that are older than both
  --orphan-age and the 10-minute sealed-write grace. Unreferenced sealed objects
  younger than the grace are reported as sealed_deferred, never guessed.
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
	case "sweep":
		runSweep(os.Args[2:])
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
