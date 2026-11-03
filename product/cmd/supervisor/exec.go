package main

// F05/T033: `cmd/supervisor exec --once`.
//
// One invocation = at most one claimed execution attempt. It never sweeps and
// never starts a second attempt: continuous supervision is F04A. The operator
// gets one JSON report on stdout (no attempt token, signed URL or DSN) and a
// non-zero exit only when the attempt itself could not be completed/committed.

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
	"os/signal"
	"strings"
	"syscall"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"

	"orchara/platform/internal/config"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/presetexec"
	"orchara/platform/internal/sandbox"
	"orchara/platform/internal/uuid"
)

const execUsage = `usage: orchara-supervisor exec --once [flags]

Claims at most one F03 execution attempt and runs the fixed preset python tool in
the gVisor sandbox. Prints one JSON report on stdout.

  --once                    required; one attempt per invocation (looping is F04A)
  --worker-id NAME          identity recorded on the claim (default: hostname)
  --execution-unit-id ID    fixed unit id (default: generated once per process)
  --help                    this text

Required environment (mTLS, no database credentials are printed):
  ORCHARA_INTERNAL_URL, ORCHARA_INTERNAL_CA,
  ORCHARA_SUPERVISOR_CLIENT_CERT/KEY, ORCHARA_WORKER_CLIENT_CERT/KEY,
  ORCHARA_SUPERVISOR_DSN   (read-only: input resolution and run state)

Sandbox environment:
  ORCHARA_WSL_DISTRO                default Ubuntu-24.04 (empty = run bash directly)
  ORCHARA_SANDBOX_PYTHON_ROOTFS     default /opt/orchara-sandbox/python-rootfs
  ORCHARA_SANDBOX_ROOTFS_DIGEST     the digest recorded by deploy/sandbox/build_python_rootfs.sh
  ORCHARA_SANDBOX_STATE_ROOT        default /var/tmp/orchara-sandbox
  ORCHARA_SANDBOX_TIMEOUT           default 60s (max 10m)
  ORCHARA_SANDBOX_MEMORY_MB         default 256
  ORCHARA_SANDBOX_PIDS              default 64
  ORCHARA_SANDBOX_CPU_SECONDS       default 60
  ORCHARA_SANDBOX_DRIVER            default <product root>/deploy/sandbox/sandbox_exec.sh
  ORCHARA_SANDBOX_PROBE             default <product root>/deploy/sandbox/isolation_probe.py
  ORCHARA_PRODUCT_ROOT              default .
  ORCHARA_BACKGROUND_HEARTBEAT_INTERVAL default 5s
  ORCHARA_EXEC_RESULT_WAIT / ORCHARA_EXEC_POLL_INTERVAL  finalize wait bounds
`

// execOptions is the parsed `exec` invocation.
type execOptions struct {
	once            bool
	workerID        string
	executionUnitID string
}

func parseExecOptions(args []string) (*execOptions, error) {
	flags := flag.NewFlagSet("exec", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	once := flags.Bool("once", false, "run at most one attempt and exit")
	workerID := flags.String("worker-id", "", "identity recorded on the claim")
	unitID := flags.String("execution-unit-id", "", "fixed execution unit id")
	if err := flags.Parse(args); err != nil {
		return nil, err
	}
	if flags.NArg() > 0 {
		return nil, fmt.Errorf("unexpected arguments: %s", strings.Join(flags.Args(), " "))
	}
	if !*once {
		return nil, errors.New("exec requires --once; continuous supervision belongs to F04A")
	}
	id := strings.TrimSpace(*workerID)
	if id == "" {
		hostname, err := os.Hostname()
		if err != nil || hostname == "" {
			hostname = "supervisor"
		}
		id = hostname
	}
	if len(id) > 128 {
		return nil, errors.New("--worker-id must be at most 128 characters")
	}
	unit := strings.TrimSpace(*unitID)
	if unit == "" {
		unit = fmt.Sprintf("exec-%s-%d-%s", id, os.Getpid(), uuid.NewString()[:8])
	}
	if len(unit) > 128 {
		return nil, errors.New("--execution-unit-id must be at most 128 characters")
	}
	return &execOptions{once: true, workerID: id, executionUnitID: unit}, nil
}

func runExec(args []string) {
	logger := log.New(os.Stderr, "orchara-supervisor ", log.LstdFlags|log.LUTC)
	options, err := parseExecOptions(args)
	if err != nil {
		fmt.Fprintf(os.Stderr, "orchara-supervisor: %v\n\n%s", err, execUsage)
		os.Exit(2)
	}
	cfg, err := config.LoadSupervisorExec(config.FromEnv())
	if err != nil {
		failExec(logger, err)
	}
	db, err := sql.Open("pgx", cfg.DBDSN)
	if err != nil {
		failExec(logger, errors.New("cannot open the supervisor database handle"))
	}
	defer db.Close()
	db.SetMaxOpenConns(2)
	pingCtx, cancelPing := context.WithTimeout(context.Background(), 15*time.Second)
	err = db.PingContext(pingCtx)
	cancelPing()
	if err != nil {
		failExec(logger, errors.New("the supervisor cannot reach the database"))
	}

	client, err := newSupClient(cfg)
	if err != nil {
		failExec(logger, err)
	}
	sandboxOptions, err := sandbox.OptionsFromEnv(os.Getenv("ORCHARA_PRODUCT_ROOT"))
	if err != nil {
		failExec(logger, err)
	}
	executor, err := sandbox.NewExecutor(sandboxOptions)
	if err != nil {
		failExec(logger, err)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	report := presetexec.Run(ctx, presetexec.Deps{
		Client:  execClient{supClient: client, db: dbAccess{db: db}},
		Inputs:  dbAccess{db: db},
		Sandbox: executor,
		Options: presetexec.Options{
			WorkerID:        options.workerID,
			ExecutionUnitID: options.executionUnitID,
			ResultWait:      cfg.ResultWait,
			PollInterval:    cfg.PollInterval,
			HeartbeatTick:   cfg.Heartbeat,
		},
		Logger: logger,
	})
	_ = json.NewEncoder(os.Stdout).Encode(report)
	logger.Printf("exec: status=%s outcome=%s run=%s code=%s",
		report.Status, report.Outcome, report.RunID, report.ErrorCode)
	if report.Status == "failed" {
		os.Exit(1)
	}
}

func failExec(logger *log.Logger, err error) {
	logger.Printf("failed: %v", err)
	// Machine readable and never a token, URL or DSN.
	_ = json.NewEncoder(os.Stdout).Encode(map[string]string{
		"status": "failed",
		"error":  err.Error(),
	})
	os.Exit(1)
}

// dbAccess is the supervisor's trusted read-only identity: it resolves the
// claimed run's linked input artifacts and reads the committed run state for the
// operator report. It is not a write path and it never moves artifact bytes (the
// signed link does that).
type dbAccess struct {
	db *sql.DB
}

func (a dbAccess) Inputs(ctx context.Context, runID string) ([]presetexec.InputArtifact, error) {
	entries, err := execution.ListRunInputArtifacts(ctx, a.db, runID)
	if err != nil {
		return nil, err
	}
	inputs := make([]presetexec.InputArtifact, 0, len(entries))
	for _, entry := range entries {
		inputs = append(inputs, presetexec.InputArtifact{
			ID:        entry.ID,
			Purpose:   entry.Purpose,
			MediaType: entry.MediaType,
			SizeBytes: entry.SizeBytes,
			SHA256:    entry.SHA256,
		})
	}
	return inputs, nil
}

func (a dbAccess) RunState(ctx context.Context, runID string) (string, string, error) {
	var state string
	var errorCode sql.NullString
	err := a.db.QueryRowContext(ctx,
		"SELECT state, error_code FROM platform.runs WHERE id = $1::uuid", runID,
	).Scan(&state, &errorCode)
	if err != nil {
		return "", "", err
	}
	return state, errorCode.String, nil
}

// execClient composes the protocol client with the trusted run-state read.
type execClient struct {
	*supClient
	db dbAccess
}

func (c execClient) RunState(ctx context.Context, runID string) (string, string, error) {
	return c.db.RunState(ctx, runID)
}
