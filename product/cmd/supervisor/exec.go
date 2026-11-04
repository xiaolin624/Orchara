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
	"sync/atomic"
	"syscall"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"

	"orchara/platform/internal/config"
	"orchara/platform/internal/execution"
	"orchara/platform/internal/memory"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/presetexec"
	"orchara/platform/internal/sandbox"
	"orchara/platform/internal/uuid"
)

// nodeEventSequence is the diagnostic local_sequence of this process's
// node.status events. It is monotonic for the process; the server's committed
// order is the request order, never this number.
var nodeEventSequence atomic.Int64

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

Optional environment:
  ORCHARA_OBJECT_DIR        sealed-object root for F09 personal-memory recall;
                            without it recall reports unavailable, never fake text

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

	// F09/T063: the recall source reads the sealed memory bodies from the same
	// object root the API writes them to. A missing root is not a startup failure
	// (every other node still runs); it makes recall fail closed as unavailable.
	var objects *objectstore.Store
	if strings.TrimSpace(cfg.ObjectDir) != "" {
		opened, openErr := objectstore.Open(cfg.ObjectDir)
		if openErr != nil {
			logger.Printf("personal memory recall has no object store: %v", openErr)
		} else {
			objects = opened
		}
	}
	recall := memory.NewRecallSource(db, objects)

	access := dbAccess{db: db}
	execClient := execClient{supClient: client, db: access}
	report := presetexec.Run(ctx, presetexec.Deps{
		Client:      execClient,
		Inputs:      access,
		Facts:       execClient,
		Memory:      recall,
		RunIdentity: access,
		Sandbox:     executor,
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

// RunScope implements presetexec.RunIdentity: the trusted owner+application of
// one run. The application is the frozen execution snapshot's, not a value the
// manifest names, so F09 recall can never read across applications or owners.
func (a dbAccess) RunScope(ctx context.Context, runID string) (presetexec.RunScope, error) {
	var scope presetexec.RunScope
	err := a.db.QueryRowContext(ctx, `
		SELECT r.owner_id::text, s.application_id::text
		  FROM platform.runs AS r
		  JOIN platform.execution_snapshots AS s ON s.id = r.execution_snapshot_id
		 WHERE r.id = $1::uuid`, runID).Scan(&scope.OwnerID, &scope.ApplicationID)
	if err != nil {
		return presetexec.RunScope{}, err
	}
	return scope, nil
}

// AttemptNumber is the dense run-local attempt ordinal of the live attempt. The
// contract has no attempt_no column (the attempt token is the credential), so the
// node.status payload's required attempt_no is derived from the trusted database
// identity instead of trusting a caller value.
func (a dbAccess) AttemptNumber(ctx context.Context, runID, token string) (int64, error) {
	var number int64
	err := a.db.QueryRowContext(ctx, `
		SELECT count(*)
		  FROM platform.run_attempts AS earlier
		  JOIN platform.run_attempts AS current
		    ON current.run_id = earlier.run_id
		 WHERE current.token = $2::uuid
		   AND current.run_id = $1::uuid
		   AND (earlier.started_at, earlier.token) <= (current.started_at, current.token)`,
		runID, token,
	).Scan(&number)
	if err != nil {
		return 0, err
	}
	if number < 1 {
		return 0, errors.New("the attempt has no recorded attempt number")
	}
	return number, nil
}

// execClient composes the protocol client with the trusted run-state read.
type execClient struct {
	*supClient
	db dbAccess
}

func (c execClient) RunState(ctx context.Context, runID string) (string, string, error) {
	return c.db.RunState(ctx, runID)
}

// SubmitNodeStatus implements presetexec.Client: one node.status transition over
// the worker mTLS capacity. The payload is exactly {node_id,state,attempt_no};
// the attempt number is read from the trusted database identity, never taken
// from the manifest or the environment.
func (c execClient) SubmitNodeStatus(
	ctx context.Context, runID, attemptToken, nodeID, state string,
) error {
	attemptNo, err := c.db.AttemptNumber(ctx, runID, attemptToken)
	if err != nil {
		return err
	}
	body := map[string]any{
		"attempt_token": attemptToken,
		"events": []map[string]any{{
			"event_id":       uuid.NewString(),
			"local_sequence": nodeEventSequence.Add(1),
			"type":           "node.status",
			"payload": map[string]any{
				"node_id": nodeID, "state": state, "attempt_no": attemptNo,
			},
		}},
	}
	_, err = c.post(ctx, c.worker, workerRole, runPath(runID, "/events"), body, nil)
	return err
}

// RecordNodeResults implements presetexec.Client: the per-node data/file/log
// provenance report. It carries no content; the server re-proves the live
// attempt and that every artifact is a ready node_output/log of this run.
func (c execClient) RecordNodeResults(
	ctx context.Context, runID, attemptToken string, results []presetexec.NodeResult,
) error {
	items := make([]map[string]string, 0, len(results))
	for _, result := range results {
		items = append(items, map[string]string{
			"node_id":     result.NodeID,
			"kind":        result.Kind,
			"artifact_id": result.ArtifactID,
			"label":       result.Label,
		})
	}
	body := map[string]any{"attempt_token": attemptToken, "results": items}
	_, err := c.post(ctx, c.worker, workerRole, runPath(runID, "/node-results"), body, nil)
	return err
}

// ModelProfile implements presetexec.Facts. It is the supervisor's trusted
// read-only model identity: only a passed, unrevoked profile may be spent on a
// model call.
func (c execClient) ModelProfile(
	ctx context.Context, modelProfileVersionID string,
) (presetexec.ModelFact, error) {
	var fact presetexec.ModelFact
	var revoked bool
	var configJSON string
	err := c.db.db.QueryRowContext(ctx, `
		SELECT id::text, validation_state, revoked_at IS NOT NULL, config::text
		  FROM platform.model_profile_versions WHERE id = $1::uuid`,
		modelProfileVersionID,
	).Scan(&fact.ID, &fact.ValidationState, &revoked, &configJSON)
	if err != nil {
		return presetexec.ModelFact{}, err
	}
	fact.Revoked = revoked
	// F06/T047: the declared budget is read from the profile config. Missing
	// fields stay zero; the executor applies its documented defaults and records
	// the resulting budget in the context trace.
	var config struct {
		ContextWindow   int64 `json:"context_window"`
		MaxOutputTokens int64 `json:"max_output_tokens"`
	}
	if configJSON != "" {
		_ = json.Unmarshal([]byte(configJSON), &config)
	}
	fact.ContextWindow = config.ContextWindow
	fact.MaxOutputTokens = config.MaxOutputTokens
	return fact, nil
}

// ToolFacts implements presetexec.Facts. Callable mirrors the T041 server rule:
// ready and either approved or (debug run) owned by the run's author. The
// capability server re-proves the same fact on invoke.
func (c execClient) ToolFacts(
	ctx context.Context, runID string, toolVersionIDs []string,
) ([]presetexec.ToolFact, error) {
	rows, err := c.db.db.QueryContext(ctx, `
		SELECT t.id::text, t.state,
		       (t.approved_at IS NOT NULL OR (r.kind = 'debug' AND t.owner_id = r.owner_id)),
		       t.input_schema::text
		  FROM platform.tool_versions AS t
		  JOIN platform.runs AS r ON r.id = $1::uuid
		 WHERE t.id::text = ANY($2::text[])`, runID, toolVersionIDs)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	facts := make([]presetexec.ToolFact, 0, len(toolVersionIDs))
	for rows.Next() {
		var fact presetexec.ToolFact
		var schema string
		if err := rows.Scan(&fact.ID, &fact.State, &fact.Callable, &schema); err != nil {
			return nil, err
		}
		fact.InputSchema = json.RawMessage(schema)
		facts = append(facts, fact)
	}
	return facts, rows.Err()
}
