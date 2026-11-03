package main

// F03/T038: the operator surface of conservative recovery.
//
//	orchara-admin runs sweep-leases [--grace 60s] [--dry-run]
//	orchara-admin runs recover --run UUID --evidence FILE [--check-only] \
//	    [--actor-login LOGIN] [--grace 60s]
//
// Both commands print exactly one JSON object on stdout and never print a DSN, an
// attempt token or a sealed URL. Exit codes follow the rest of cmd/admin:
//
//	0  the command completed (swept/parks reported, recovery recorded or check-only passed)
//	1  a decided refusal (nothing written) or a database/object failure
//	2  command-line usage problem (missing/malformed flag, unreadable evidence file)
//
// sweep-leases only parks: it keeps the job held and the allocation taken, and it
// never requeues or swaps a token. recover is the only command that returns a
// parked Run to queued, and it requires audited operator evidence proving the old
// execution unit and its checkpoint writes are gone.

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	"orchara/platform/internal/config"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

const runsUsage = `usage: orchara-admin runs <command> [flags]

commands:
  sweep-leases   park running Runs whose live attempt lease expired beyond the grace window
  recover        prove the old execution unit is gone and re-queue exactly one job

sweep-leases:
  orchara-admin runs sweep-leases [--grace 60s] [--dry-run]

  A lease-expired Run is parked in recovery_pending with recovery_reason=lease_expired,
  its job stays held and its execution slot stays allocated. Nothing is requeued and
  no attempt token is swapped: claim selects only queued Runs, so a parked Run cannot
  get a second executor. --dry-run reports the same decisions and writes nothing.

recover:
  orchara-admin runs recover --run UUID --evidence FILE [--check-only] \
      [--actor-login LOGIN] [--grace 60s]

  Proves, against the real database and object store, that:
    * the Run is recovery_pending;
    * the old execution unit is not provably alive (heartbeat older than grace, or
      the supervisor already recorded the exit);
    * the operator evidence names the same execution_unit_id and carries process
      and sandbox destruction proof (closed shape, SHA-256 recorded);
    * every recorded checkpoint connection is absent from pg_stat_activity;
    * the frozen manifest artifact and the run input artifact are ready, not
      deleted, hash/size match, and compiler.VerifyManifestBytes passes;
    * no other live attempt/job exists.
  On success one transaction records the evidence, finalizes the old attempt,
  revokes its token, re-arms exactly one job (same kind, resume artifact kept),
  returns the Run to queued, clears the recovery columns, appends the run.status
  event and writes an audit row. --check-only performs the checks and writes
  nothing. A repeated command is a refusal (already_recovered), never a second
  attempt.

  The evidence file is a JSON object with exactly:
    {"evidence_kind":"operator_recovery","execution_unit_id":"...",
     "observed_at":"RFC3339","process":{"pid":123,"command":"ps ...",
     "result":"absent|terminated","observed":"..."},
     "sandbox":{"sandbox_id":"...","command":"runsc list",
     "result":"absent|deleted|stopped"},
     "db_drained":{"command":"...","result":"no_active_backends"},
     "notes":"..."}
  Only notes and db_drained are optional. Free text such as "it is dead" is refused.

  --actor-login must name an enabled human administrator (the default is the
  bootstrap administrator). ORCHARA_OBJECT_DIR is required: the sealed manifest
  and input bytes are re-verified before anything is re-queued.

  refusals (exit 1, JSON status=refused): run_not_found, not_recovery_pending,
  already_recovered, cancel_requested, old_execution_still_live,
  database_writes_in_flight, artifact_unavailable, input_deleted,
  manifest_hash_mismatch, manifest_invalid, input_hash_mismatch,
  execution_identity_mismatch, evidence_required, evidence_invalid,
  evidence_unit_mismatch, live_attempt_conflict, job_unavailable,
  needs_reconciliation, actor_not_found.
`

// runRuns dispatches the two recovery commands. It is registered as the `runs`
// command in main.go alongside migrate/bootstrap/handover.
func runRuns(args []string) error {
	if len(args) == 0 {
		return usageErrorf("missing runs command; expected 'sweep-leases' or 'recover'")
	}
	switch args[0] {
	case "sweep-leases":
		return runRunsSweepLeases(args[1:])
	case "recover":
		return runRunsRecover(args[1:])
	case "help", "-h", "--help":
		fmt.Fprint(os.Stdout, runsUsage)
		return nil
	default:
		return usageErrorf("unknown runs command %q; expected 'sweep-leases' or 'recover'", args[0])
	}
}

// runRunsSweepLeases parks expired leases and prints one JSON report.
func runRunsSweepLeases(args []string) error {
	flags := flag.NewFlagSet("runs sweep-leases", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	grace := flags.Duration("grace", 0,
		"grace after the lease deadline before a Run is parked (default 60s)")
	dryRun := flags.Bool("dry-run", false, "report what would be parked without writing")
	if err := flags.Parse(args); err != nil {
		return usageErrorf("%v", err)
	}
	if flags.NArg() > 0 {
		return usageErrorf("unexpected arguments: %s", strings.Join(flags.Args(), " "))
	}
	if *grace < 0 || *grace > execution.MaxRecoveryGrace {
		return usageErrorf("--grace must be between 0 and %s", execution.MaxRecoveryGrace)
	}

	cfg, err := config.LoadDatabase(config.FromEnv())
	if err != nil {
		return err
	}
	db, err := openDatabase(cfg.DBDSN)
	if err != nil {
		return err
	}
	defer db.Close()

	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		// The DSN is not echoed: it carries the password.
		return errors.New("cannot reach the database")
	}

	service := execution.New(db, nil, nil, "", 0)
	report, err := service.SweepExpiredLeases(ctx, execution.LeaseSweepRequest{
		Grace:  *grace,
		DryRun: *dryRun,
	})
	if err != nil {
		_ = printJSONLine(map[string]any{
			"status": "error",
			"code":   "lease_sweep_failed",
			"error":  err.Error(),
		})
		return err
	}
	return printJSONLine(report)
}

// runRunsRecover proves a parked Run may be re-queued and performs the one
// transaction. A refusal is a JSON report on stdout plus exit 1; nothing was
// written.
func runRunsRecover(args []string) error {
	flags := flag.NewFlagSet("runs recover", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	runID := flags.String("run", "", "the parked Run id")
	evidencePath := flags.String("evidence", "", "operator evidence JSON file")
	checkOnly := flags.Bool("check-only", false, "run every check and write nothing")
	actorLogin := flags.String("actor-login", "",
		"login of the administrator recorded as the evidence author (default: bootstrap admin)")
	grace := flags.Duration("grace", 0,
		"grace after the last heartbeat before the old execution is considered not alive (default 60s)")
	if err := flags.Parse(args); err != nil {
		return usageErrorf("%v", err)
	}
	if flags.NArg() > 0 {
		return usageErrorf("unexpected arguments: %s", strings.Join(flags.Args(), " "))
	}

	run := strings.TrimSpace(*runID)
	if run == "" {
		return usageErrorf("--run is required")
	}
	if !uuid.Valid(run) {
		return usageErrorf("--run must be a UUID")
	}
	var evidence []byte
	if path := strings.TrimSpace(*evidencePath); path != "" {
		raw, err := os.ReadFile(path)
		if err != nil {
			return usageErrorf("cannot read --evidence: %v", err)
		}
		evidence = raw
	}
	if *grace < 0 || *grace > execution.MaxRecoveryGrace {
		return usageErrorf("--grace must be between 0 and %s", execution.MaxRecoveryGrace)
	}

	cfg, err := config.LoadDatabase(config.FromEnv())
	if err != nil {
		return err
	}
	db, err := openDatabase(cfg.DBDSN)
	if err != nil {
		return err
	}
	defer db.Close()

	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		return errors.New("cannot reach the database")
	}

	objectDir, _ := config.FromEnv()("ORCHARA_OBJECT_DIR")
	objects := (*objectstore.Store)(nil)
	if strings.TrimSpace(objectDir) != "" {
		objects, err = objectstore.Open(objectDir)
		if err != nil {
			_ = printJSONLine(map[string]any{
				"status": "refused",
				"code":   execution.RecoveryCodeArtifactUnavailable,
				"message": "the object store rejected ORCHARA_OBJECT_DIR: " + err.Error() +
					"; the sealed manifest/input cannot be verified",
			})
			return errors.New("object store rejected ORCHARA_OBJECT_DIR")
		}
	}

	service := execution.New(db, objects, nil, "", 0)
	report, err := service.Recover(ctx, execution.RecoveryRequest{
		RunID:       run,
		EvidenceRaw: evidence,
		ActorLogin:  strings.TrimSpace(*actorLogin),
		CheckOnly:   *checkOnly,
		Grace:       *grace,
	})
	var refusal *execution.RecoveryRefusal
	switch {
	case err == nil:
		return printJSONLine(report)
	case errors.As(err, &refusal):
		report.Status = "refused"
		report.Code = refusal.Code
		report.Message = refusal.Message
		if encodeErr := printJSONLine(report); encodeErr != nil {
			return encodeErr
		}
		return fmt.Errorf("recover refused: %s", refusal.Code)
	default:
		report.Status = "error"
		report.Code = "recover_failed"
		report.Message = err.Error()
		_ = printJSONLine(report)
		return err
	}
}

// printJSONLine writes one compact JSON object to stdout. It never receives a
// credential: the report types deliberately carry only ids, codes and times.
func printJSONLine(payload any) error {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("encoding the command result: %w", err)
	}
	fmt.Fprintln(os.Stdout, string(encoded))
	return nil
}
