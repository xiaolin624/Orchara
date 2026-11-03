package sandbox

// F05/T033: the executor.
//
// Run stages one execution, invokes the driver in its own process group,
// heartbeats while it runs (observing cancel_requested), kills it on
// timeout/cancel/lease loss, proves the sandbox is gone and parses the driver's
// one JSON document. An outcome is only reported when the cleanup facts say
// nothing survived; an unclean cleanup is never reported as success.

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"orchara/platform/internal/jcs"
)

// canonicalize is the RFC 8785 canonicalization the platform uses everywhere.
func canonicalize(payload []byte) ([]byte, error) {
	return jcs.CanonicalizeBytes(payload)
}

// HeartbeatState is what one control heartbeat observed.
type HeartbeatState struct {
	// CancelRequested means the run asked to stop; the executor kills the unit.
	CancelRequested bool
}

// Request is one sandbox execution.
type Request struct {
	// Program is the fixed program (tool or isolation probe).
	Program Program
	// ProbeMode/ProbeSeconds are only used by the isolation probe.
	ProbeMode    ProbeMode
	ProbeSeconds int
	// MediaType selects the fixed input file name (CSV/XLSX).
	MediaType string
	// Input is the authorized file's bytes; InputJSON is the tool's input document.
	Input           []byte
	InputJSON       []byte
	ExecutionUnitID string
	// Timeout overrides Options.Timeout for this execution (tests use short
	// values; the supervisor uses the deployment profile).
	Timeout time.Duration
}

// Control is the caller's supervision seam.
type Control struct {
	// Heartbeat renews the attempt lease and reports the run's cancel intent.
	// A nil heartbeat means "no lease to renew" (the isolation probe experiment).
	Heartbeat func(ctx context.Context) (HeartbeatState, error)
	// HeartbeatInterval overrides Options.HeartbeatTick.
	HeartbeatInterval time.Duration
}

// CleanupFacts is the driver's and executor's proof that nothing survived.
type CleanupFacts struct {
	SandboxListMatches int   `json:"sandbox_list_matches"`
	PID                int64 `json:"pid"`
	PIDAlive           bool  `json:"pid_alive"`
	PSMatches          int   `json:"ps_matches"`
	WorkspaceRemoved   bool  `json:"workspace_removed"`
}

// Clean reports whether no sandbox entry, pid, process or workspace survived.
func (c CleanupFacts) Clean() bool {
	return c.SandboxListMatches == 0 && !c.PIDAlive && c.PSMatches == 0 && c.WorkspaceRemoved
}

// Outcome is one finished sandbox execution.
type Outcome struct {
	SandboxID       string
	ExecutionUnitID string
	RootfsDigest    string
	SandboxStarted  bool
	Cancelled       bool
	TimedOut        bool
	ExitCode        int
	Duration        time.Duration
	StartedAt       time.Time
	Stdout          []byte
	Stderr          []byte
	OutputJSON      []byte
	ProbeJSON       []byte
	Artifacts       []Artifact
	Cleanup         CleanupFacts
	CleanupVerified bool
}

// Executor runs one sandbox per call. It is safe for concurrent use.
type Executor struct {
	options Options
	runner  Runner
}

// driverReport is the driver's JSON document. Field names match
// deploy/sandbox/sandbox_exec.sh exactly.
type driverReport struct {
	OK             bool    `json:"ok"`
	Error          string  `json:"error"`
	Mode           string  `json:"mode"`
	Program        string  `json:"program"`
	SandboxID      string  `json:"sandbox_id"`
	DurationMS     int64   `json:"duration_ms"`
	ExitCode       int     `json:"exit_code"`
	TimedOut       bool    `json:"timed_out"`
	TimeoutSeconds int     `json:"timeout_seconds"`
	RootfsDigest   string  `json:"rootfs_digest"`
	Stdout         string  `json:"stdout"`
	StdoutSize     int64   `json:"stdout_size"`
	Stderr         string  `json:"stderr"`
	StderrSize     int64   `json:"stderr_size"`
	OutputJSON     *string `json:"output_json"`
	ProbeJSON      *string `json:"probe_json"`
	Artifacts      []struct {
		Name string `json:"name"`
		Size int64  `json:"size"`
		B64  string `json:"b64"`
	} `json:"artifacts"`
	ArtifactError string `json:"artifact_error"`
	Cleanup       struct {
		SandboxListMatches int   `json:"sandbox_list_matches"`
		PID                int64 `json:"pid"`
		PIDAlive           bool  `json:"pid_alive"`
		PSMatches          int   `json:"ps_matches"`
		WorkspaceRemoved   bool  `json:"workspace_removed"`
	} `json:"cleanup"`
}

// Run executes one sandbox. A non-nil error means the platform could not trust
// the execution (timeout/cancel/orphan/driver/invalid protocol); the returned
// Outcome still carries every fact that was observed (including cleanup).
func (e *Executor) Run(ctx context.Context, request Request, control Control) (Outcome, error) {
	if e == nil {
		return Outcome{}, errors.New("sandbox executor is nil")
	}
	options := e.options
	timeout := request.Timeout
	if timeout <= 0 {
		timeout = options.Timeout
	}
	if timeout < 100*time.Millisecond || timeout > MaxTimeout {
		return Outcome{}, fmt.Errorf("sandbox timeout must be between 100ms and %s", MaxTimeout)
	}
	switch request.Program {
	case ProgramTool, ProgramIsolationProbe, ProgramBuildTool:
	default:
		return Outcome{}, fmt.Errorf("unknown sandbox program %q", request.Program)
	}
	if len(request.Input) == 0 {
		return Outcome{}, errors.New("sandbox input is empty")
	}
	if (request.Program == ProgramTool || request.Program == ProgramBuildTool) && len(request.InputJSON) == 0 {
		return Outcome{}, errors.New("sandbox input document is missing")
	}
	if request.Program == ProgramTool {
		if _, err := inputFileName(request.MediaType); err != nil {
			return Outcome{}, err
		}
	}

	// Fail closed when the deployment's image identity cannot be stated.
	digest, err := ParseRecordedDigest(options.ExpectedRootfsDigest)
	if err != nil {
		return Outcome{}, err
	}
	toolPath, err := options.toolPathFor(request.Program)
	if err != nil {
		return Outcome{}, err
	}
	if err := VerifyTool(toolPath); err != nil {
		return Outcome{}, err
	}
	if _, err := options.driverPath(); err != nil {
		return Outcome{}, err
	}

	sandboxID, err := NewSandboxID(request.ExecutionUnitID)
	if err != nil {
		return Outcome{}, err
	}
	hostWorkspace, err := options.workspacePath()
	if err != nil {
		return Outcome{}, fmt.Errorf("creating the sandbox staging directory: %w", err)
	}
	defer func() { _ = os.RemoveAll(hostWorkspace) }()

	inputName := "input.csv"
	switch request.Program {
	case ProgramTool:
		inputName, err = inputFileName(request.MediaType)
		if err != nil {
			return Outcome{}, err
		}
	case ProgramBuildTool:
		// The build's two mounts are fixed names: the source zip and the
		// canonical dependency lock (staged as input.json on the host and copied
		// to lock.json by the driver).
		inputName = "source.zip"
	}
	configPath := filepath.Join(hostWorkspace, "config.json")
	inputPath := filepath.Join(hostWorkspace, inputName)
	inputJSONPath := filepath.Join(hostWorkspace, "input.json")
	spec := bundleSpec{
		SandboxID:      sandboxID,
		StateRoot:      options.StateRoot,
		Rootfs:         options.Rootfs,
		Program:        request.Program,
		ProbeMode:      request.ProbeMode,
		ProbeSeconds:   request.ProbeSeconds,
		InputName:      inputName,
		MemoryMB:       options.MemoryMB,
		PIDsLimit:      options.PIDsLimit,
		CPUSeconds:     options.CPUSeconds,
		TimeoutSeconds: int(SandboxTimeout(timeout) / time.Second),
	}
	configBytes, err := buildOCI(spec)
	if err != nil {
		return Outcome{}, fmt.Errorf("building the sandbox bundle: %w", err)
	}
	if err := writeStagedFile(configPath, configBytes); err != nil {
		return Outcome{}, err
	}
	if err := writeStagedFile(inputPath, request.Input); err != nil {
		return Outcome{}, err
	}
	if err := writeStagedFile(inputJSONPath, request.InputJSON); err != nil {
		return Outcome{}, err
	}

	runCommand, err := BuildRunCommand(options, spec, Staged{
		SandboxID:     sandboxID,
		HostWorkspace: hostWorkspace,
		ConfigPath:    configPath,
		InputPath:     inputPath,
		InputJSONPath: inputJSONPath,
		ToolPath:      toolPath,
	})
	if err != nil {
		return Outcome{}, err
	}

	outcome := Outcome{
		SandboxID:       sandboxID,
		ExecutionUnitID: request.ExecutionUnitID,
		RootfsDigest:    digest,
		StartedAt:       time.Now().UTC(),
	}

	// The first heartbeat happens before the sandbox starts: it proves the token
	// still works and honours a cancel that arrived while we were staging.
	if control.Heartbeat != nil {
		state, heartbeatErr := control.Heartbeat(ctx)
		if heartbeatErr != nil {
			return outcome, fmt.Errorf("%w: %v", ErrHeartbeat, heartbeatErr)
		}
		if state.CancelRequested {
			return outcome, ErrCancelled
		}
	}

	// The unit's process is supervised: a control failure or a cancellation kills
	// it, then the cleanup proof runs under a fresh context.
	runCtx, cancelRun := context.WithCancel(ctx)
	runCtx, cancelDeadline := context.WithTimeout(runCtx, timeout+options.GracePeriod)
	defer cancelDeadline()
	var (
		controlMu       sync.Mutex
		controlErr      error
		cancelRequested bool
		tickerDone      = make(chan struct{})
	)
	interval := control.HeartbeatInterval
	if interval <= 0 {
		interval = options.HeartbeatTick
	}
	if control.Heartbeat != nil {
		go func() {
			defer close(tickerDone)
			ticker := time.NewTicker(interval)
			defer ticker.Stop()
			for {
				select {
				case <-runCtx.Done():
					return
				case <-ticker.C:
					state, heartbeatErr := control.Heartbeat(runCtx)
					if heartbeatErr != nil {
						controlMu.Lock()
						if controlErr == nil {
							controlErr = heartbeatErr
						}
						controlMu.Unlock()
						cancelRun()
						return
					}
					if state.CancelRequested {
						controlMu.Lock()
						cancelRequested = true
						controlMu.Unlock()
						cancelRun()
						return
					}
				}
			}
		}()
	} else {
		close(tickerDone)
	}

	result, runErr := e.runner.Run(runCtx, runCommand)
	cancelRun()
	<-tickerDone
	outcome.SandboxStarted = true
	outcome.Duration = time.Duration(0)

	controlMu.Lock()
	observedControlErr := controlErr
	observedCancel := cancelRequested
	controlMu.Unlock()

	report, reportErr := parseDriverReport(result.Stdout)
	if reportErr == nil {
		outcome.Duration = time.Duration(report.DurationMS) * time.Millisecond
		outcome.ExitCode = report.ExitCode
		outcome.TimedOut = report.TimedOut
		outcome.Stdout = decodeB64(report.Stdout)
		outcome.Stderr = decodeB64(report.Stderr)
		outcome.OutputJSON = decodeB64Ptr(report.OutputJSON)
		outcome.ProbeJSON = decodeB64Ptr(report.ProbeJSON)
		for _, artifact := range report.Artifacts {
			outcome.Artifacts = append(outcome.Artifacts, Artifact{
				Name:    artifact.Name,
				Content: decodeB64(artifact.B64),
			})
		}
		outcome.Cleanup = cleanupOf(report)
		outcome.CleanupVerified = report.OK && outcome.Cleanup.Clean()
	}

	// Decide whether the unit was stopped by us. A late cancel after the tool
	// already exited still counts: its result must not be published as success
	// (EXECUTION §6), and the sandbox is already gone.
	stopped := observedCancel || observedControlErr != nil || errors.Is(runCtx.Err(), context.DeadlineExceeded)
	if stopped {
		outcome.Cancelled = observedCancel
		// The driver may have been killed before it could print its report; run
		// the cleanup entry point so the orphan proof is independent of it.
		cleanupFacts, cleanupErr := e.cleanup(ctx, options, sandboxID)
		outcome.Cleanup = cleanupFacts
		outcome.CleanupVerified = cleanupFacts.Clean()
		switch {
		case observedCancel:
			return outcome, ErrCancelled
		case observedControlErr != nil:
			return outcome, fmt.Errorf("%w: %v", ErrHeartbeat, observedControlErr)
		case cleanupErr != nil:
			return outcome, cleanupErr
		default:
			return outcome, ErrTimeout
		}
	}
	if runErr != nil {
		_, _ = e.cleanup(ctx, options, sandboxID)
		return outcome, fmt.Errorf("%w: %v", ErrRunnerUnavailable, runErr)
	}
	if reportErr != nil {
		cleanupFacts, _ := e.cleanup(ctx, options, sandboxID)
		outcome.Cleanup = cleanupFacts
		outcome.CleanupVerified = cleanupFacts.Clean()
		return outcome, reportErr
	}
	if report.RootfsDigest != digest {
		return outcome, fmt.Errorf("%w: driver=%q recorded=%q", ErrDigestMismatch, report.RootfsDigest, digest)
	}
	if !outcome.Cleanup.Clean() {
		return outcome, fmt.Errorf("%w: %+v", ErrOrphan, outcome.Cleanup)
	}
	outcome.CleanupVerified = true
	if report.TimedOut {
		return outcome, ErrTimeout
	}
	if report.ArtifactError != "" {
		return outcome, fmt.Errorf("%w: %s", ErrOutputInvalid, report.ArtifactError)
	}
	return outcome, nil
}

// cleanup invokes the driver's cleanup entry point and returns its facts.
func (e *Executor) cleanup(parent context.Context, options Options, sandboxID string) (CleanupFacts, error) {
	cleanupCtx, cancel := context.WithTimeout(contextWithoutCancel(parent), 60*time.Second)
	defer cancel()
	command, err := BuildCleanupCommand(options, sandboxID, options.StateRoot)
	if err != nil {
		return CleanupFacts{}, err
	}
	result, err := e.runner.Run(cleanupCtx, command)
	if err != nil {
		return CleanupFacts{}, fmt.Errorf("%w: cleanup driver failed: %v", ErrOrphan, err)
	}
	event, err := parseCleanupReport(result.Stdout)
	if err != nil {
		return CleanupFacts{}, err
	}
	if !event.Clean() {
		return event, fmt.Errorf("%w: %+v", ErrOrphan, event)
	}
	return event, nil
}

// parseDriverReport strictly decodes the driver's single JSON document.
func parseDriverReport(stdout []byte) (*driverReport, error) {
	trimmed := strings.TrimSpace(string(stdout))
	if trimmed == "" {
		return nil, fmt.Errorf("%w: the driver printed nothing", ErrDriverReport)
	}
	decoder := json.NewDecoder(strings.NewReader(trimmed))
	var report driverReport
	if err := decoder.Decode(&report); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrDriverReport, err)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return nil, fmt.Errorf("%w: trailing data", ErrDriverReport)
	}
	if report.Mode != "run" {
		return nil, fmt.Errorf("%w: unexpected mode %q", ErrDriverReport, report.Mode)
	}
	if report.SandboxID == "" {
		return nil, fmt.Errorf("%w: no sandbox id", ErrDriverReport)
	}
	return &report, nil
}

// parseCleanupReport decodes the cleanup entry point's JSON document.
func parseCleanupReport(stdout []byte) (CleanupFacts, error) {
	trimmed := strings.TrimSpace(string(stdout))
	if trimmed == "" {
		return CleanupFacts{}, fmt.Errorf("%w: cleanup printed nothing", ErrDriverReport)
	}
	var report struct {
		OK        bool   `json:"ok"`
		Mode      string `json:"mode"`
		SandboxID string `json:"sandbox_id"`
		Cleanup   struct {
			SandboxListMatches int  `json:"sandbox_list_matches"`
			PIDAlive           bool `json:"pid_alive"`
			PSMatches          int  `json:"ps_matches"`
			WorkspaceRemoved   bool `json:"workspace_removed"`
		} `json:"cleanup"`
	}
	if err := json.Unmarshal([]byte(trimmed), &report); err != nil {
		return CleanupFacts{}, fmt.Errorf("%w: %v", ErrDriverReport, err)
	}
	if report.Mode != "cleanup" {
		return CleanupFacts{}, fmt.Errorf("%w: unexpected cleanup mode %q", ErrDriverReport, report.Mode)
	}
	return CleanupFacts{
		SandboxListMatches: report.Cleanup.SandboxListMatches,
		PIDAlive:           report.Cleanup.PIDAlive,
		PSMatches:          report.Cleanup.PSMatches,
		WorkspaceRemoved:   report.Cleanup.WorkspaceRemoved,
	}, nil
}

func cleanupOf(report *driverReport) CleanupFacts {
	return CleanupFacts{
		SandboxListMatches: report.Cleanup.SandboxListMatches,
		PID:                report.Cleanup.PID,
		PIDAlive:           report.Cleanup.PIDAlive,
		PSMatches:          report.Cleanup.PSMatches,
		WorkspaceRemoved:   report.Cleanup.WorkspaceRemoved,
	}
}

func decodeB64(value string) []byte {
	if value == "" {
		return nil
	}
	decoded, err := base64.StdEncoding.DecodeString(value)
	if err != nil {
		return nil
	}
	return decoded
}

func decodeB64Ptr(value *string) []byte {
	if value == nil {
		return nil
	}
	return decodeB64(*value)
}

// writeStagedFile writes a staging file with owner-only permissions.
func writeStagedFile(path string, payload []byte) error {
	if err := os.WriteFile(path, payload, 0o600); err != nil {
		return fmt.Errorf("writing the sandbox staging file: %w", err)
	}
	return nil
}

// CollectedArtifacts turns the driver report's artifacts into a name->bytes map
// for AdmitArtifacts. Kept separate so a fake report and the real one go through
// the same validation.
func CollectedArtifacts(outcome Outcome) map[string][]byte {
	collected := make(map[string][]byte, len(outcome.Artifacts))
	for _, artifact := range outcome.Artifacts {
		collected[artifact.Name] = artifact.Content
	}
	return collected
}
