// Command sandbox-probe exercises the tool-debug sandbox profile without a Run.
//
// It is an operator/deployment verification entry, not a user surface: it runs the
// platform-owned isolation probe (or its deliberate slow/crash variants) through
// exactly the executor the supervisor uses, and prints the observed facts —
// identity, host/foreign file reads, read-only input, network reachability,
// timeout/crash cleanup and `runsc list`/pid/workspace proof.
//
// It exists because "the sandbox is configured correctly" is a claim that must be
// re-checkable on the deployment host, not only inside the test suite.
//
// Usage:
//
//	orchara-sandbox-probe --mode isolation|slow|crash [--seconds 30] [--timeout 5s]
//
// Environment: ORCHARA_PRODUCT_ROOT plus the ORCHARA_SANDBOX_* profile variables
// (see `cmd/supervisor exec --help`). Exits 0 when the sandbox ran and its
// cleanup was proven; a timeout is a reported outcome (exit 0) with
// "timed_out": true; a configuration/preflight failure exits 1.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"orchara/platform/internal/sandbox"
	"orchara/platform/internal/uuid"
)

func main() {
	flags := flag.NewFlagSet("sandbox-probe", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	mode := flags.String("mode", "isolation", "isolation|slow|crash")
	seconds := flags.Int("seconds", 30, "how long the slow probe sleeps")
	timeout := flags.Duration("timeout", 5*time.Second, "sandbox wall-clock limit")
	if err := flags.Parse(os.Args[1:]); err != nil {
		fail(err)
	}
	probeMode := sandbox.ProbeMode(*mode)
	switch probeMode {
	case sandbox.ProbeIsolation, sandbox.ProbeSlow, sandbox.ProbeCrash:
	default:
		fail(fmt.Errorf("unknown probe mode %q", *mode))
	}
	options, err := sandbox.OptionsFromEnv(os.Getenv("ORCHARA_PRODUCT_ROOT"))
	if err != nil {
		fail(err)
	}
	options.Timeout = *timeout
	executor, err := sandbox.NewExecutor(options)
	if err != nil {
		fail(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), *timeout+2*time.Minute)
	defer cancel()
	outcome, runErr := executor.Run(ctx, sandbox.Request{
		Program:         sandbox.ProgramIsolationProbe,
		ProbeMode:       probeMode,
		ProbeSeconds:    *seconds,
		Input:           []byte("probe-input\n"),
		ExecutionUnitID: "sandbox-probe-" + uuid.NewString()[:8],
	}, sandbox.Control{})

	var probe any
	if len(outcome.ProbeJSON) > 0 {
		_ = json.Unmarshal(outcome.ProbeJSON, &probe)
	}
	report := map[string]any{
		"status":        "observed",
		"probe_mode":    string(probeMode),
		"sandbox_id":    outcome.SandboxID,
		"rootfs_digest": outcome.RootfsDigest,
		"started":       outcome.SandboxStarted,
		"exit_code":     outcome.ExitCode,
		"timed_out":     outcome.TimedOut,
		"cancelled":     outcome.Cancelled,
		"duration_ms":   outcome.Duration.Milliseconds(),
		"cleanup":       outcome.Cleanup,
		"cleanup_ok":    outcome.CleanupVerified,
		"stdout":        strings.TrimSpace(string(outcome.Stdout)),
		"stderr":        strings.TrimSpace(string(outcome.Stderr)),
		"probe":         probe,
	}
	if runErr != nil {
		report["error"] = runErr.Error()
		if errors.Is(runErr, sandbox.ErrTimeout) {
			report["outcome"] = "timeout"
		} else {
			report["outcome"] = "failed"
		}
	} else {
		report["outcome"] = "ran"
	}
	_ = json.NewEncoder(os.Stdout).Encode(report)
	if runErr != nil && !errors.Is(runErr, sandbox.ErrTimeout) {
		os.Exit(1)
	}
}

func fail(err error) {
	_ = json.NewEncoder(os.Stdout).Encode(map[string]string{"status": "failed", "error": err.Error()})
	os.Exit(1)
}
