// Command maintenance copies: the F11/T072 copy-class cleanup pass.
//
// This is the operator/scheduler surface for the four F11/T072 scopes of the
// T069 deletion jobs (checkpoints/summaries, sandbox temp, third-party history,
// backup catalog). It is deliberately not a `delete_content` background handler:
// the checkpoint schema is out of band, the sandbox staging directory is host
// filesystem state owned by the supervisor, and third-party history / backup
// media are outside the database — none of those are reachable through the
// object-store handler T071 owns. The pass updates the *same* deletion job row
// (remove from remaining_scopes, append to completed_scopes, ledger append in one
// transaction) and never registers a second handler kind.
//
// One JSON line per recorded copy-class outcome, then one summary line:
//
//	{"job_id":...,"scope":"checkpoints","copy_class":"checkpoint","outcome":"cleared","reason":"..."}
//	{"status":"ok","jobs":1,...}
//
// It prints ids/codes/reasons only; a checkpoint payload or a body never reaches
// stdout. Exit 0 means the pass ran (per-scope failures are recorded on the job
// and are part of the report); exit 1 means the pass itself could not run; exit 2
// is a usage error.
package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"strings"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"

	"orchara/platform/internal/config"
	"orchara/platform/internal/deletion"
)

func runCopies(args []string) {
	options, err := parseCopiesOptions(args)
	if err != nil {
		fmt.Fprintf(os.Stderr, "orchara-maintenance: %v\n\n%s", err, usage)
		os.Exit(2)
	}
	logger := log.New(os.Stderr, "orchara-maintenance ", log.LstdFlags|log.LUTC)
	cfg, err := config.LoadCopyCleanup(config.FromEnv())
	if err != nil {
		failMaintenance(logger, err)
	}
	db, err := sql.Open("pgx", cfg.DBDSN)
	if err != nil {
		failMaintenance(logger, errors.New("cannot open the maintenance database handle"))
	}
	defer db.Close()
	db.SetMaxOpenConns(2)

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		failMaintenance(logger, errors.New("maintenance cannot reach the database"))
	}

	cleaner := &deletion.CopyCleaner{
		DB:                   db,
		SandboxRoot:          options.sandboxRoot,
		SandboxAge:           options.sandboxAge,
		ThirdPartyComponents: options.thirdParty,
		Limit:                options.limit,
	}
	report, outcomes, err := cleaner.Run(ctx)
	if err != nil {
		failMaintenance(logger, err)
	}
	for _, outcome := range outcomes {
		line := map[string]any{
			"job_id":     outcome.JobID,
			"scope":      string(outcome.Scope),
			"copy_class": outcome.CopyClass,
			"outcome":    outcome.Outcome,
			"reason":     outcome.Reason,
		}
		if outcome.FailureErr != "" {
			line["error_code"] = outcome.FailureErr
		}
		_ = json.NewEncoder(os.Stdout).Encode(line)
	}
	_ = json.NewEncoder(os.Stdout).Encode(report)
}

type copiesOptions struct {
	sandboxRoot string
	sandboxAge  time.Duration
	thirdParty  []string
	limit       int
}

func parseCopiesOptions(args []string) (*copiesOptions, error) {
	flags := flag.NewFlagSet("copies", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	defaultRoot := strings.TrimSpace(os.Getenv("ORCHARA_SANDBOX_WORKSPACE_ROOT"))
	if defaultRoot == "" {
		defaultRoot = os.TempDir()
	}
	sandboxRoot := flags.String("sandbox-temp-root", defaultRoot,
		"host directory the supervisor stages sandbox workspaces in (its OS temp dir by default)")
	sandboxAge := flags.Duration("sandbox-age", time.Hour,
		"only remove sandbox leftovers at least this old")
	thirdParty := flags.String("third-party-components", "",
		"comma-separated third-party memory/RAG components this deployment runs (default: none, the T066 do_not_adopt decision)")
	limit := flags.Int("limit", 100, "maximum deletion jobs handled by this pass")
	if err := flags.Parse(args); err != nil {
		return nil, err
	}
	if flags.NArg() > 0 {
		return nil, fmt.Errorf("unexpected arguments: %s", strings.Join(flags.Args(), " "))
	}
	if *sandboxAge < 0 {
		return nil, errors.New("--sandbox-age must not be negative")
	}
	if *limit < 1 {
		return nil, errors.New("--limit must be positive")
	}
	components := make([]string, 0, 2)
	for _, component := range strings.Split(*thirdParty, ",") {
		if trimmed := strings.TrimSpace(component); trimmed != "" {
			components = append(components, trimmed)
		}
	}
	if strings.TrimSpace(*sandboxRoot) == "" {
		return nil, errors.New("--sandbox-temp-root must not be blank")
	}
	return &copiesOptions{
		sandboxRoot: strings.TrimSpace(*sandboxRoot),
		sandboxAge:  *sandboxAge,
		thirdParty:  components,
		limit:       *limit,
	}, nil
}
