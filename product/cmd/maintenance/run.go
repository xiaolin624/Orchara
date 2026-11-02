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
	"os/signal"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"

	"orchara/platform/internal/background"
	"orchara/platform/internal/background/handlers"
	"orchara/platform/internal/config"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/uuid"
)

// jobReport is one human/machine readable line per claimed job. Tests parse the
// JSON lines; the final line of a `run` is always the summary.
type jobReport struct {
	JobID      string `json:"job_id"`
	Kind       string `json:"kind"`
	Result     string `json:"result"` // succeeded|failed|retried|stale|cancelled
	State      string `json:"state,omitempty"`
	ErrorCode  string `json:"error_code,omitempty"`
	Heartbeats int    `json:"heartbeats"`
	DurationMS int64  `json:"duration_ms"`
}

// runSummary is the last JSON object a `run` prints.
type runSummary struct {
	Status    string `json:"status"`
	Claimed   int    `json:"claimed"`
	Succeeded int    `json:"succeeded"`
	Failed    int    `json:"failed"`
	Retried   int    `json:"retried"`
	Stale     int    `json:"stale"`
	Cancelled int    `json:"cancelled"`
	Note      string `json:"note"`
}

func (s *runSummary) record(report jobReport) {
	s.Claimed++
	switch report.Result {
	case "succeeded":
		s.Succeeded++
	case "failed":
		s.Failed++
	case "retried":
		s.Retried++
	case "stale":
		s.Stale++
	case "cancelled":
		s.Cancelled++
	}
}

type runOptions struct {
	once         bool
	kinds        []background.Kind
	pollInterval time.Duration
	workerID     string
}

// runMaintenance is the `run` subcommand: a real maintenance client of the
// internal mTLS API. It is configuration-fail-closed: without the internal URL,
// client certificate, CA, object store or maintenance DSN it refuses to start.
func runMaintenance(args []string) {
	options, err := parseRunOptions(args)
	if err != nil {
		fmt.Fprintf(os.Stderr, "orchara-maintenance: %v\n\n%s", err, usage)
		os.Exit(2)
	}
	logger := log.New(os.Stderr, "orchara-maintenance ", log.LstdFlags|log.LUTC)

	cfg, err := config.LoadMaintenance(config.FromEnv())
	if err != nil {
		failMaintenance(logger, err)
	}
	store, err := objectstore.Open(cfg.ObjectDir)
	if err != nil {
		failMaintenance(logger, fmt.Errorf("object store rejected: %w", err))
	}
	db, err := sql.Open("pgx", cfg.DBDSN)
	if err != nil {
		failMaintenance(logger, errors.New("cannot open the maintenance database handle"))
	}
	defer db.Close()
	db.SetMaxOpenConns(2)

	pingCtx, cancelPing := context.WithTimeout(context.Background(), 15*time.Second)
	err = db.PingContext(pingCtx)
	cancelPing()
	if err != nil {
		failMaintenance(logger, errors.New("maintenance cannot reach the database"))
	}

	client, err := newInternalClient(cfg)
	if err != nil {
		failMaintenance(logger, err)
	}

	runner := &runner{
		client:    client,
		store:     store,
		db:        db,
		heartbeat: cfg.HeartbeatInterval,
		options:   *options,
		logger:    logger,
	}
	logger.Printf("run: worker=%s kinds=%v once=%t heartbeat=%s",
		options.workerID, options.kinds, options.once, cfg.HeartbeatInterval)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	summary, err := runner.loop(ctx)
	if err != nil {
		failMaintenance(logger, err)
	}
	_ = json.NewEncoder(os.Stdout).Encode(summary)
}

func failMaintenance(logger *log.Logger, err error) {
	logger.Printf("failed: %v", err)
	// Machine-readable, and never the DSN or a certificate path.
	_ = json.NewEncoder(os.Stdout).Encode(map[string]string{
		"status": "failed",
		"error":  err.Error(),
	})
	os.Exit(1)
}

func parseRunOptions(args []string) (*runOptions, error) {
	flags := flag.NewFlagSet("run", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	once := flags.Bool("once", false, "claim until there is no ready job, then exit")
	kindsFlag := flags.String("kinds", "", "comma-separated kinds with a compiled handler")
	pollInterval := flags.Duration("poll-interval", 2*time.Second, "wait between empty claims")
	workerID := flags.String("worker-id", "", "identity recorded on the leased job")
	if err := flags.Parse(args); err != nil {
		return nil, err
	}
	if flags.NArg() > 0 {
		return nil, fmt.Errorf("unexpected arguments: %s", strings.Join(flags.Args(), " "))
	}
	if *pollInterval <= 0 {
		return nil, errors.New("--poll-interval must be greater than zero")
	}

	var kinds []background.Kind
	seen := make(map[background.Kind]bool)
	add := func(kind background.Kind) error {
		if !background.KnownKind(string(kind)) {
			return fmt.Errorf("unknown kind %q", kind)
		}
		if _, ok := handlers.Registry.Lookup(kind); !ok {
			return fmt.Errorf("no compiled handler for kind %q; this build handles: %s",
				kind, strings.Join(registryNames(), ", "))
		}
		if !seen[kind] {
			seen[kind] = true
			kinds = append(kinds, kind)
		}
		return nil
	}
	raw := strings.TrimSpace(*kindsFlag)
	if raw == "" {
		for _, kind := range handlers.Registry.SupportedKinds() {
			if err := add(kind); err != nil {
				return nil, err
			}
		}
	} else {
		for _, candidate := range strings.Split(raw, ",") {
			if err := add(background.Kind(strings.TrimSpace(candidate))); err != nil {
				return nil, err
			}
		}
	}
	if len(kinds) == 0 {
		return nil, errors.New("no kinds selected")
	}

	id := strings.TrimSpace(*workerID)
	if id == "" {
		hostname, err := os.Hostname()
		if err != nil || hostname == "" {
			hostname = "maintenance"
		}
		id = fmt.Sprintf("%s-%d-%s", hostname, os.Getpid(), uuid.NewString()[:8])
	}
	if len(id) > 128 {
		return nil, errors.New("--worker-id must be at most 128 characters")
	}
	return &runOptions{
		once:         *once,
		kinds:        kinds,
		pollInterval: *pollInterval,
		workerID:     id,
	}, nil
}

func registryNames() []string {
	kinds := handlers.Registry.SupportedKinds()
	names := make([]string, 0, len(kinds))
	for _, kind := range kinds {
		names = append(names, string(kind))
	}
	return names
}

// runner owns one maintenance process's loop.
type runner struct {
	client    *internalClient
	store     *objectstore.Store
	db        *sql.DB
	heartbeat time.Duration
	options   runOptions
	logger    *log.Logger
	summary   runSummary
}

// loop claims until told to stop (continuous mode) or until a claim is empty
// (--once). Polling PostgreSQL through the claim endpoint is the only wakeup
// mechanism: there is no Redis or other wakeup bus in this batch, and a lost
// wakeup can therefore never lose work.
func (r *runner) loop(ctx context.Context) (runSummary, error) {
	r.summary = runSummary{
		Status: "ok",
		Note:   "pg polling only; no wakeup bus configured",
	}
	kinds := make([]string, 0, len(r.options.kinds))
	for _, kind := range r.options.kinds {
		kinds = append(kinds, string(kind))
	}
	for {
		if ctx.Err() != nil {
			return r.summary, nil
		}
		job, err := r.client.claim(ctx, r.options.workerID, kinds)
		if err != nil {
			if ctx.Err() != nil {
				return r.summary, nil
			}
			return r.summary, err
		}
		if job == nil {
			if r.options.once {
				return r.summary, nil
			}
			select {
			case <-ctx.Done():
				return r.summary, nil
			case <-time.After(r.options.pollInterval):
			}
			continue
		}
		report, err := r.execute(ctx, job)
		if err != nil {
			return r.summary, err
		}
		r.summary.record(report)
		_ = json.NewEncoder(os.Stdout).Encode(report)
	}
}

// execute heartbeats, runs the compiled handler, and submits the outcome.
//
// The first heartbeat is sent immediately after the claim (then every configured
// interval): it proves the freshly issued token works before any work starts,
// and it is the observable heartbeat path in the T022 tests. A heartbeat failure
// cancels the handler context; a stale token is reported as "stale" and no result
// is submitted, because the server has already recovered or reassigned the job.
func (r *runner) execute(parent context.Context, job *lease) (jobReport, error) {
	started := time.Now()
	report := jobReport{JobID: job.JobID, Kind: job.Kind}
	finish := func(result string) jobReport {
		report.Result = result
		report.DurationMS = time.Since(started).Milliseconds()
		return report
	}

	var heartbeats atomic.Int64
	if err := r.client.heartbeat(parent, job.JobID, job.AttemptToken); err != nil {
		if isAPICode(err, background.CodeStaleAttempt) {
			return finish("stale"), nil
		}
		return report, err
	}
	heartbeats.Add(1)

	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	heartbeatDone := make(chan error, 1)
	go func() {
		ticker := time.NewTicker(r.heartbeat)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				heartbeatDone <- nil
				return
			case <-ticker.C:
				if err := r.client.heartbeat(ctx, job.JobID, job.AttemptToken); err != nil {
					if isAPICode(err, background.CodeStaleAttempt) {
						// The lease moved on: stop the handler instead of
						// letting it finish work whose result cannot be applied.
						cancel()
					}
					heartbeatDone <- err
					return
				}
				heartbeats.Add(1)
			}
		}
	}()

	handler, ok := handlers.Registry.Lookup(background.Kind(job.Kind))
	var outcome background.Outcome
	if !ok {
		outcome = background.Failed(background.CodeTransientInternal)
	} else {
		outcome = handler(ctx, background.HandlerWork{
			Job:          jobModel(job),
			AttemptToken: job.AttemptToken,
			Store:        r.store,
			DB:           r.db,
			Heartbeat: func(heartbeatCtx context.Context) error {
				return r.client.heartbeat(heartbeatCtx, job.JobID, job.AttemptToken)
			},
		})
	}
	cancel()
	heartbeatErr := <-heartbeatDone
	report.Heartbeats = int(heartbeats.Load())
	if heartbeatErr != nil {
		if isAPICode(heartbeatErr, background.CodeStaleAttempt) {
			return finish("stale"), nil
		}
		return report, heartbeatErr
	}

	submitted, err := r.client.submitResult(
		parent, job.JobID, job.AttemptToken, uuid.NewString(), outcome,
	)
	if err != nil {
		if isAPICode(err, background.CodeStaleAttempt) {
			return finish("stale"), nil
		}
		return report, err
	}
	report.State = submitted.State
	if submitted.ErrorCode != nil {
		report.ErrorCode = *submitted.ErrorCode
	}
	switch submitted.State {
	case string(background.StateSucceeded):
		return finish("succeeded"), nil
	case string(background.StateFailed):
		return finish("failed"), nil
	case string(background.StateReady):
		return finish("retried"), nil
	case string(background.StateCancelled):
		return finish("cancelled"), nil
	default:
		return finish("unknown"), nil
	}
}

// jobModel projects the contract lease onto the controller's internal job model.
// The handler only needs identity, kind, target and the expected source facts.
func jobModel(leased *lease) *background.Job {
	job := &background.Job{
		ID:               leased.JobID,
		Kind:             background.Kind(leased.Kind),
		TargetID:         leased.TargetID,
		ExpectedRevision: leased.ExpectedRevision,
		InputArtifactID:  leased.InputArtifactID,
	}
	token := leased.AttemptToken
	job.LeaseToken = &token
	return job
}
