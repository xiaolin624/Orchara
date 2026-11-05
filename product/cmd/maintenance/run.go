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
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"

	"orchara/platform/internal/background"
	"orchara/platform/internal/background/handlers"
	"orchara/platform/internal/config"
	"orchara/platform/internal/embedding"
	"orchara/platform/internal/objectstore"
	"orchara/platform/internal/sandbox"
	"orchara/platform/internal/tools"
	"orchara/platform/internal/uuid"
)

// jobReport is one human/machine readable line per claimed job. Tests parse the
// JSON lines; the final line of a `run` is always the summary.
type jobReport struct {
	JobID      string `json:"job_id"`
	Kind       string `json:"kind"`
	Pool       string `json:"pool,omitempty"`
	Result     string `json:"result"` // succeeded|failed|retried|stale|cancelled
	State      string `json:"state,omitempty"`
	ErrorCode  string `json:"error_code,omitempty"`
	Heartbeats int    `json:"heartbeats"`
	DurationMS int64  `json:"duration_ms"`
}

// runSummary is the last JSON object a `run` prints. The counts are aggregated
// across every pool and worker slot.
type runSummary struct {
	Status    string `json:"status"`
	Claimed   int    `json:"claimed"`
	Succeeded int    `json:"succeeded"`
	Failed    int    `json:"failed"`
	Retried   int    `json:"retried"`
	Stale     int    `json:"stale"`
	Cancelled int    `json:"cancelled"`
	Pools     string `json:"pools,omitempty"`
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
	// Pool shape and handler waits. The environment supplies deployment
	// defaults (config.MaintenanceConfig); an explicitly passed flag overrides
	// it. Values are resolved in applyConfig before the process starts.
	systemIOSlots      int
	workSlots          int
	datasetWaitTimeout time.Duration
	evaluationPoll     time.Duration
	reportWaitTimeout  time.Duration
	// evaluationCaseTimeout bounds one evaluation case's run to a rest state
	// (F13A/T052). A timeout is recorded as not_run, never passed.
	evaluationCaseTimeout time.Duration
	// toolTimeout bounds one trusted preset-tool subprocess (T030).
	toolTimeout time.Duration
	// toolBuildTimeout bounds one sandboxed tool build (T037).
	toolBuildTimeout time.Duration
	// explicit remembers which flags the operator actually set, so "unset" uses
	// the validated environment default instead of a zero-value guess.
	explicit map[string]bool
}

// runMaintenance is the `run` subcommand: a real maintenance client of the
// internal mTLS API. It is configuration-fail-closed: without the internal URL,
// client certificate, CA, object store or maintenance DSN it refuses to start.
//
// Since T026 the process runs two independent pools:
//
//	system_io  artifact_finalize only (default 2 slots)
//	work       every other compiled kind (default 1 slot)
//
// Finalizing a file is what unblocks a waiting evaluation's report, so it must
// never share the single work pool that a waiting handler occupies. A waiting
// evaluation heartbeats and polls the job-scoped artifact status; it does not
// hold a system_io slot.
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
	if err := options.applyConfig(cfg); err != nil {
		fmt.Fprintf(os.Stderr, "orchara-maintenance: %v\n\n%s", err, usage)
		os.Exit(2)
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

	sandboxOptions, err := sandbox.OptionsFromEnv(cfg.ProductRoot)
	if err != nil {
		failMaintenance(logger, fmt.Errorf("sandbox profile rejected: %w", err))
	}
	sandboxExecutor, err := sandbox.NewExecutor(sandboxOptions)
	if err != nil {
		failMaintenance(logger, fmt.Errorf("sandbox executor rejected: %w", err))
	}
	if sandboxOptions.ExpectedRootfsDigest == "" {
		logger.Printf("run: no ORCHARA_SANDBOX_ROOTFS_DIGEST; tool_build/tool debug runs fail closed")
	}
	runner := &runner{
		client:    client,
		scope:     newJobScope(client),
		store:     store,
		db:        db,
		heartbeat: cfg.HeartbeatInterval,
		options:   *options,
		logger:    logger,
		sandbox:   sandboxExecutor,
		tools: tools.New(tools.Options{
			PythonPath: cfg.PythonPath,
			ScriptPath: filepath.Join(cfg.ProductRoot, tools.SalesStatsScript),
			Timeout:    options.toolTimeout,
		}),
	}
	logger.Printf("run: worker=%s kinds=%v once=%t heartbeat=%s",
		options.workerID, options.kinds, options.once, cfg.HeartbeatInterval)
	logger.Printf("run: pools=%s",
		describePools(options.pools()))

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
	once := flags.Bool("once", false,
		"drain mode: keep claiming until every pool is idle, then exit")
	kindsFlag := flags.String("kinds", "", "comma-separated kinds with a compiled handler")
	pollInterval := flags.Duration("poll-interval", 2*time.Second, "wait between empty claims")
	workerID := flags.String("worker-id", "", "identity recorded on the leased job")
	systemIOSlots := flags.Int("system-io-slots", 0,
		"concurrent artifact_finalize workers (env ORCHARA_MAINTENANCE_SYSTEM_IO_SLOTS, default 2)")
	workSlots := flags.Int("work-slots", 0,
		"concurrent workers for every other kind (env ORCHARA_MAINTENANCE_WORK_SLOTS, default 1)")
	datasetWait := flags.Duration("dataset-wait-timeout", 0,
		"how long one evaluation attempt waits for its dataset (env ORCHARA_MAINTENANCE_DATASET_WAIT_TIMEOUT)")
	evaluationPoll := flags.Duration("evaluation-poll-interval", 0,
		"evaluation status-poll/heartbeat cadence (env ORCHARA_MAINTENANCE_EVALUATION_POLL_INTERVAL)")
	reportWait := flags.Duration("report-wait-timeout", 0,
		"how long one attempt waits for its report to be finalized (env ORCHARA_MAINTENANCE_REPORT_WAIT_TIMEOUT)")
	evaluationCase := flags.Duration("evaluation-case-timeout", 0,
		"how long one evaluation case's run may take to reach a rest state (env ORCHARA_MAINTENANCE_EVALUATION_CASE_TIMEOUT)")
	toolTimeout := flags.Duration("tool-timeout", 0,
		"how long one trusted preset-tool subprocess may run (env ORCHARA_MAINTENANCE_TOOL_TIMEOUT)")
	toolBuildTimeout := flags.Duration("tool-build-timeout", 0,
		"how long one sandboxed tool build may run (env ORCHARA_MAINTENANCE_TOOL_BUILD_TIMEOUT)")
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
	options := &runOptions{
		once:         *once,
		kinds:        kinds,
		pollInterval: *pollInterval,
		workerID:     id,
		explicit:     map[string]bool{},
	}
	flags.Visit(func(visited *flag.Flag) { options.explicit[visited.Name] = true })
	// Keep the raw flag values; applyConfig validates them together with the
	// environment defaults.
	options.systemIOSlots = *systemIOSlots
	options.workSlots = *workSlots
	options.datasetWaitTimeout = *datasetWait
	options.evaluationPoll = *evaluationPoll
	options.reportWaitTimeout = *reportWait
	options.evaluationCaseTimeout = *evaluationCase
	options.toolTimeout = *toolTimeout
	options.toolBuildTimeout = *toolBuildTimeout
	return options, nil
}

// applyConfig resolves every unspecified knob from the validated environment
// configuration and rejects out-of-range explicit flags. A flag never silently
// overrides configuration with a zero value.
func (o *runOptions) applyConfig(cfg *config.MaintenanceConfig) error {
	resolveSlots := func(name string, explicit bool, value int, fallback int) (int, error) {
		if !explicit {
			return fallback, nil
		}
		if value < 1 || value > 32 {
			return 0, fmt.Errorf("--%s must be between 1 and 32", name)
		}
		return value, nil
	}
	var err error
	o.systemIOSlots, err = resolveSlots("system-io-slots", o.explicit["system-io-slots"],
		o.systemIOSlots, cfg.SystemIOSlots)
	if err != nil {
		return err
	}
	o.workSlots, err = resolveSlots("work-slots", o.explicit["work-slots"],
		o.workSlots, cfg.WorkSlots)
	if err != nil {
		return err
	}
	resolveDuration := func(name string, explicit bool, value time.Duration, fallback time.Duration) (time.Duration, error) {
		if !explicit {
			return fallback, nil
		}
		if value <= 0 {
			return 0, fmt.Errorf("--%s must be greater than zero", name)
		}
		return value, nil
	}
	o.datasetWaitTimeout, err = resolveDuration("dataset-wait-timeout",
		o.explicit["dataset-wait-timeout"], o.datasetWaitTimeout, cfg.DatasetWaitTimeout)
	if err != nil {
		return err
	}
	o.evaluationPoll, err = resolveDuration("evaluation-poll-interval",
		o.explicit["evaluation-poll-interval"], o.evaluationPoll, cfg.EvaluationPollInterval)
	if err != nil {
		return err
	}
	if o.evaluationPoll < 10*time.Millisecond {
		return errors.New("--evaluation-poll-interval must be at least 10ms")
	}
	o.reportWaitTimeout, err = resolveDuration("report-wait-timeout",
		o.explicit["report-wait-timeout"], o.reportWaitTimeout, cfg.ReportWaitTimeout)
	if err != nil {
		return err
	}
	o.evaluationCaseTimeout, err = resolveDuration("evaluation-case-timeout",
		o.explicit["evaluation-case-timeout"], o.evaluationCaseTimeout, cfg.EvaluationCaseTimeout)
	if err != nil {
		return err
	}
	o.toolTimeout, err = resolveDuration("tool-timeout",
		o.explicit["tool-timeout"], o.toolTimeout, cfg.ToolTimeout)
	if err != nil {
		return err
	}
	if o.toolTimeout > 10*time.Minute {
		return errors.New("--tool-timeout must be at most 10m")
	}
	o.toolBuildTimeout, err = resolveDuration("tool-build-timeout",
		o.explicit["tool-build-timeout"], o.toolBuildTimeout, cfg.ToolBuildTimeout)
	if err != nil {
		return err
	}
	if o.toolBuildTimeout > 10*time.Minute {
		return errors.New("--tool-build-timeout must be at most 10m")
	}
	return nil
}

func registryNames() []string {
	kinds := handlers.Registry.SupportedKinds()
	names := make([]string, 0, len(kinds))
	for _, kind := range kinds {
		names = append(names, string(kind))
	}
	return names
}

// ---------------------------------------------------------------------------
// pools
// ---------------------------------------------------------------------------

// systemIOKind reports whether a kind belongs to the dedicated system_io pool.
// Only artifact_finalize does: it is the work that unblocks waiting handlers.
func systemIOKind(kind background.Kind) bool {
	return kind == background.KindArtifactFinalize
}

// pool is one independent claim group with a fixed slot count.
type pool struct {
	name  string
	kinds []background.Kind
	slots int
}

// pools partitions the selected kinds. A process started with --kinds
// artifact_finalize therefore runs only the system_io pool, and one started with
// --kinds model_validate,application_evaluate runs only the work pool.
func (o *runOptions) pools() []pool {
	systemKinds := make([]background.Kind, 0, 1)
	workKinds := make([]background.Kind, 0, len(o.kinds))
	for _, kind := range o.kinds {
		if systemIOKind(kind) {
			systemKinds = append(systemKinds, kind)
		} else {
			workKinds = append(workKinds, kind)
		}
	}
	pools := make([]pool, 0, 2)
	if len(systemKinds) > 0 {
		pools = append(pools, pool{name: "system_io", kinds: systemKinds, slots: o.systemIOSlots})
	}
	if len(workKinds) > 0 {
		pools = append(pools, pool{name: "work", kinds: workKinds, slots: o.workSlots})
	}
	return pools
}

func describePools(pools []pool) string {
	parts := make([]string, 0, len(pools))
	for _, p := range pools {
		kinds := make([]string, 0, len(p.kinds))
		for _, kind := range p.kinds {
			kinds = append(kinds, string(kind))
		}
		parts = append(parts, fmt.Sprintf("%s[slots=%d kinds=%s]", p.name, p.slots, strings.Join(kinds, ",")))
	}
	return strings.Join(parts, " ")
}

// workerIDFor gives every slot its own identity so diagnostics can tell which
// pool/slot leased a job. The base id stays visible for T022-style startup
// assertions.
func workerIDFor(base, poolName string, slot int) string {
	id := base
	if len(id) > 80 {
		id = id[:80]
	}
	return fmt.Sprintf("%s:%s:%d", id, poolName, slot)
}

// ---------------------------------------------------------------------------
// runner
// ---------------------------------------------------------------------------

// runner owns one maintenance process's loops.
type runner struct {
	client    *internalClient
	scope     *jobScope
	store     *objectstore.Store
	db        *sql.DB
	heartbeat time.Duration
	options   runOptions
	logger    *log.Logger
	// tools is the trusted preset-tool executor built from deployment config.
	tools *tools.Executor
	// sandbox is the T033 gVisor executor used by the T037 tool_build handler.
	sandbox *sandbox.Executor

	summaryMu sync.Mutex
	summary   runSummary
	outputMu  sync.Mutex
}

// drainState makes `--once` a *drain*, not "first empty claim wins": the process
// exits only when every worker slot has claimed an empty queue at the same
// activity epoch, meaning no job started or finished anywhere since the last of
// them looked. Any accepted or finished job bumps the epoch and clears every
// empty mark, because a work handler's completeUpload creates a system_io job
// that must still run (and a finalize job may create more work in turn).
type drainState struct {
	mu        sync.Mutex
	total     int
	epoch     uint64
	emptyAt   map[string]uint64
	done      chan struct{}
	closeOnce sync.Once
}

func newDrainState(total int) *drainState {
	return &drainState{
		total:   total,
		emptyAt: make(map[string]uint64),
		done:    make(chan struct{}),
	}
}

// activity records a job start or finish: work may now exist for another pool.
func (d *drainState) activity() {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.epoch++
	d.emptyAt = make(map[string]uint64)
}

// markEmpty records that this slot saw no ready job at the current epoch and
// reports whether every slot has now done so (the drain is complete).
func (d *drainState) markEmpty(worker string) bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.emptyAt[worker] = d.epoch
	if len(d.emptyAt) < d.total {
		return false
	}
	for _, epoch := range d.emptyAt {
		if epoch != d.epoch {
			return false
		}
	}
	d.closeOnce.Do(func() { close(d.done) })
	return true
}

// loop starts one goroutine per slot per pool and waits for the signal or (in
// --once mode) for the drain to complete.
func (r *runner) loop(ctx context.Context) (runSummary, error) {
	r.setSummaryNote()
	pools := r.options.pools()
	if len(pools) == 0 {
		return r.summarySnapshot(), errors.New("no pool selected")
	}
	total := 0
	for _, p := range pools {
		total += p.slots
	}
	drain := newDrainState(total)

	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	// In drain mode the coordinator closes drain.done when every worker is idle
	// with no job in flight; all workers then stop through the cancelled context.
	// A worker never exits alone while another one may still create work.
	if r.options.once {
		go func() {
			select {
			case <-drain.done:
				cancel()
			case <-ctx.Done():
			}
		}()
	}

	var wg sync.WaitGroup
	errCh := make(chan error, 1)
	reportError := func(err error) {
		select {
		case errCh <- err:
		default:
		}
		cancel()
	}
	for _, currentPool := range pools {
		for slot := 0; slot < currentPool.slots; slot++ {
			wg.Add(1)
			go func(currentPool pool, slot int) {
				defer wg.Done()
				if err := r.worker(ctx, currentPool, slot, drain); err != nil {
					reportError(err)
				}
			}(currentPool, slot)
		}
	}
	wg.Wait()
	select {
	case err := <-errCh:
		return r.summarySnapshot(), err
	default:
		return r.summarySnapshot(), nil
	}
}

func (r *runner) setSummaryNote() {
	pools := r.options.pools()
	parts := make([]string, 0, len(pools))
	for _, p := range pools {
		parts = append(parts, p.name)
	}
	r.summaryMu.Lock()
	r.summary = runSummary{
		Status: "ok",
		Pools:  strings.Join(parts, ","),
		Note:   "pg polling only; no wakeup bus configured",
	}
	r.summaryMu.Unlock()
}

// worker is one claim slot of one pool.
func (r *runner) worker(ctx context.Context, currentPool pool, slot int, drain *drainState) error {
	workerID := workerIDFor(r.options.workerID, currentPool.name, slot)
	kinds := make([]string, 0, len(currentPool.kinds))
	for _, kind := range currentPool.kinds {
		kinds = append(kinds, string(kind))
	}
	slotID := currentPool.name + "/" + strconv.Itoa(slot)
	for {
		if ctx.Err() != nil {
			return nil
		}
		job, err := r.client.claim(ctx, workerID, kinds)
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return err
		}
		if job == nil {
			if !r.options.once {
				select {
				case <-ctx.Done():
					return nil
				case <-time.After(r.options.pollInterval):
				}
				continue
			}
			if drain.markEmpty(slotID) {
				return nil
			}
			// Another pool/slot has not yet seen the same empty queue (and may
			// create work for this one); retry after the poll interval.
			select {
			case <-ctx.Done():
				return nil
			case <-time.After(r.options.pollInterval):
			}
			continue
		}
		drain.activity()
		report, execErr := r.execute(ctx, job)
		report.Pool = currentPool.name
		drain.activity()
		if execErr != nil {
			if ctx.Err() != nil {
				return nil
			}
			return execErr
		}
		r.record(report)
		r.emit(report)
	}
}

func (r *runner) record(report jobReport) {
	r.summaryMu.Lock()
	defer r.summaryMu.Unlock()
	r.summary.record(report)
}

func (r *runner) emit(report jobReport) {
	r.outputMu.Lock()
	defer r.outputMu.Unlock()
	_ = json.NewEncoder(os.Stdout).Encode(report)
}

func (r *runner) summarySnapshot() runSummary {
	r.summaryMu.Lock()
	defer r.summaryMu.Unlock()
	return r.summary
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
						heartbeatDone <- err
						return
					}
					if ctx.Err() != nil {
						// execute cancelled lease renewal because the handler
						// already finished; an in-flight heartbeat then fails
						// with context.Canceled. That is not a lease loss, and
						// treating it as one would throw away a result the
						// server would have accepted.
						heartbeatDone <- nil
						return
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
			Scope:        r.scope,
			Store:        r.store,
			DB:           r.db,
			Heartbeat: func(heartbeatCtx context.Context) error {
				return r.client.heartbeat(heartbeatCtx, job.JobID, job.AttemptToken)
			},
			Settings: background.HandlerSettings{
				DatasetWaitTimeout:     r.options.datasetWaitTimeout,
				EvaluationPollInterval: r.options.evaluationPoll,
				ReportWaitTimeout:      r.options.reportWaitTimeout,
				EvaluationCaseTimeout:  r.options.evaluationCaseTimeout,
				ToolBuildTimeout:       r.options.toolBuildTimeout,
			},
			Embedder: embedding.FromEnv(),
			Tools:    r.tools,
			Sandbox:  r.sandbox,
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
