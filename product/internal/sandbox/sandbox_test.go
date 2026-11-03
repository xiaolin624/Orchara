package sandbox

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// testProfile builds a deployment profile whose host files exist in a temp dir,
// so the executor's preflight passes without WSL. The rootfs is a guest path and
// the digest is the deployment-recorded identity; the driver verifies it inside
// the guest, which a fake runner cannot do, so the profile carries it explicitly.
func testProfile(t *testing.T) (Options, string) {
	t.Helper()
	dir := t.TempDir()
	digest := "sha256:" + strings.Repeat("ab", 32)
	tool := filepath.Join(dir, "sales_stats.py")
	if err := os.WriteFile(tool, []byte("#!/usr/bin/env python3\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	probe := filepath.Join(dir, "isolation_probe.py")
	if err := os.WriteFile(probe, []byte("#!/usr/bin/env python3\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	driver := filepath.Join(dir, "sandbox_exec.sh")
	if err := os.WriteFile(driver, []byte("#!/usr/bin/env bash\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return Options{
		Distro:               "Ubuntu-24.04",
		StateRoot:            "/var/tmp/orchara-sandbox",
		Rootfs:               "/opt/orchara-sandbox/python-rootfs",
		ExpectedRootfsDigest: digest,
		ToolScript:           tool,
		ProbeScript:          probe,
		DriverScript:         driver,
		MemoryMB:             256,
		PIDsLimit:            64,
		CPUSeconds:           30,
		Timeout:              2 * time.Second,
		GracePeriod:          2 * time.Second,
		HeartbeatTick:        20 * time.Millisecond,
		WorkspaceRoot:        dir,
	}, digest
}

func toolRequest() Request {
	return Request{
		Program:         ProgramTool,
		MediaType:       "text/csv",
		Input:           []byte("order_id,date,region,amount\no-1,2026-01-02,east,120.50\n"),
		InputJSON:       []byte(`{"tool":"sales_stats","file":"input.csv","artifact":{"media_type":"text/csv"}}`),
		ExecutionUnitID: "unit-t033-test-1",
	}
}

func validReport(t *testing.T, digest string) []byte {
	t.Helper()
	document := map[string]any{
		"ok":              true,
		"mode":            "run",
		"program":         "tool",
		"sandbox_id":      "unit-t033-test-1",
		"duration_ms":     123,
		"exit_code":       0,
		"timed_out":       false,
		"timeout_seconds": 2,
		"rootfs_digest":   digest,
		"stdout":          base64.StdEncoding.EncodeToString([]byte("")),
		"stderr":          base64.StdEncoding.EncodeToString([]byte("")),
		"output_json":     base64.StdEncoding.EncodeToString([]byte(`{"data":{"total":"120.50"},"artifacts":["summary.json"]}`)),
		"artifacts": []map[string]any{
			{
				"name": "summary.json",
				"size": 15,
				"b64": base64.StdEncoding.EncodeToString(
					[]byte(`{"total":"120.50"}`),
				),
			},
		},
		"artifact_error": "",
		"cleanup": map[string]any{
			"sandbox_list_matches": 0,
			"pid":                  4242,
			"pid_alive":            false,
			"ps_matches":           0,
			"workspace_removed":    true,
		},
	}
	payload, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	return payload
}

func cleanupReport() []byte {
	return []byte(`{"ok":true,"mode":"cleanup","sandbox_id":"unit-t033-test-1",` +
		`"cleanup":{"sandbox_list_matches":0,"pid_alive":false,"ps_matches":0,"workspace_removed":true}}`)
}

// fakeRunner records every invocation and answers from a queue of handlers.
type fakeRunner struct {
	mu       sync.Mutex
	calls    [][]string
	handlers []func(ctx context.Context, argv []string) (CommandResult, error)
}

func (f *fakeRunner) Run(ctx context.Context, argv []string) (CommandResult, error) {
	f.mu.Lock()
	f.calls = append(f.calls, append([]string(nil), argv...))
	var handler func(context.Context, []string) (CommandResult, error)
	if len(f.handlers) > 0 {
		handler = f.handlers[0]
		f.handlers = f.handlers[1:]
	}
	f.mu.Unlock()
	if handler != nil {
		return handler(ctx, argv)
	}
	return CommandResult{Stdout: cleanupReport()}, nil
}

func (f *fakeRunner) callsSnapshot() [][]string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([][]string(nil), f.calls...)
}

func TestBuildOCIFixesTheSandboxProfile(t *testing.T) {
	digestSpec := bundleSpec{
		SandboxID: "unit-1", StateRoot: "/var/tmp/orchara-sandbox",
		Rootfs: "/opt/orchara-sandbox/python-rootfs", Program: ProgramTool,
		InputName: "input.csv", MemoryMB: 256, PIDsLimit: 64, CPUSeconds: 30,
	}
	payload, err := buildOCI(digestSpec)
	if err != nil {
		t.Fatalf("buildOCI: %v", err)
	}
	var config OCIConfig
	if err := json.Unmarshal(payload, &config); err != nil {
		t.Fatalf("config is not JSON: %v", err)
	}
	wantArgs := []string{
		"/usr/bin/python3.12", "/tool/sales_stats.py",
		"--input", "/workspace/input.json",
		"--output", "/workspace/output.json",
		"--inputs-dir", "/inputs",
		"--artifacts-dir", "/workspace/artifacts",
	}
	if strings.Join(config.Process.Args, " ") != strings.Join(wantArgs, " ") {
		t.Fatalf("process args = %v, want the fixed tool argv", config.Process.Args)
	}
	if config.Process.User.UID != 65534 || config.Process.User.GID != 65534 {
		t.Fatalf("process user = %d:%d, want 65534:65534", config.Process.User.UID, config.Process.User.GID)
	}
	if !config.Process.NoNewPrivileges {
		t.Fatal("noNewPrivileges must be true")
	}
	for name, list := range map[string][]string{
		"bounding": config.Process.Capabilities.Bounding, "effective": config.Process.Capabilities.Effective,
		"permitted": config.Process.Capabilities.Permitted, "ambient": config.Process.Capabilities.Ambient,
	} {
		if len(list) != 0 {
			t.Fatalf("capability %s must be empty, got %v", name, list)
		}
	}
	if !config.Root.Readonly || config.Root.Path != digestSpec.Rootfs {
		t.Fatalf("root = %+v, want a read-only rootfs", config.Root)
	}
	namespaces := map[string]bool{}
	for _, namespace := range config.Linux.Namespaces {
		namespaces[namespace.Type] = true
	}
	for _, required := range []string{"pid", "network", "ipc", "uts", "mount"} {
		if !namespaces[required] {
			t.Fatalf("namespace %q is missing: %v", required, namespaces)
		}
	}
	if got := config.Linux.Resources.Memory.Limit; got != 256*1024*1024 {
		t.Fatalf("memory limit = %d, want 256MiB", got)
	}
	if got := config.Linux.Resources.Pids.Limit; got != 64 {
		t.Fatalf("pids limit = %d, want 64", got)
	}
	mounts := map[string]OCIMount{}
	for _, mount := range config.Mounts {
		mounts[mount.Destination] = mount
	}
	if _, ok := mounts["/proc"]; !ok {
		t.Fatal("the sandbox needs a fresh /proc")
	}
	if mounts["/tool"].Type != "bind" || !hasOption(mounts["/tool"].Options, "ro") {
		t.Fatalf("/tool must be a read-only bind mount: %+v", mounts["/tool"])
	}
	if mounts["/inputs"].Type != "bind" || !hasOption(mounts["/inputs"].Options, "ro") {
		t.Fatalf("/inputs must be a read-only bind mount: %+v", mounts["/inputs"])
	}
	if !hasOption(mounts["/workspace"].Options, "rw") {
		t.Fatalf("/workspace must be the one writable mount: %+v", mounts["/workspace"])
	}
	if mounts["/workspace"].Source != "/var/tmp/orchara-sandbox/unit-1/output" {
		t.Fatalf("/workspace source = %q", mounts["/workspace"].Source)
	}
	if len(mounts) != 5 {
		t.Fatalf("unexpected mounts: %v", config.Mounts)
	}
	for _, env := range config.Process.Env {
		if strings.Contains(strings.ToUpper(env), "SECRET") || strings.Contains(env, "ORCHARA_") {
			t.Fatalf("sandbox env must not inherit deployment secrets: %q", env)
		}
	}
}

func TestBuildOCIRejectsSandboxEscapes(t *testing.T) {
	base := bundleSpec{
		SandboxID: "unit-1", StateRoot: "/var/tmp/x", Rootfs: "/opt/rootfs",
		Program: ProgramTool, InputName: "input.csv", MemoryMB: 1, PIDsLimit: 1, CPUSeconds: 1,
	}
	cases := map[string]func(*bundleSpec){
		"sandbox id with slash": func(s *bundleSpec) { s.SandboxID = "../escape" },
		"relative state root":   func(s *bundleSpec) { s.StateRoot = "tmp/x" },
		"relative rootfs":       func(s *bundleSpec) { s.Rootfs = "rootfs" },
		"input name traversal":  func(s *bundleSpec) { s.InputName = "../etc/passwd" },
		"zero memory":           func(s *bundleSpec) { s.MemoryMB = 0 },
		"zero pids":             func(s *bundleSpec) { s.PIDsLimit = 0 },
		"zero cpu":              func(s *bundleSpec) { s.CPUSeconds = 0 },
	}
	for name, mutate := range cases {
		spec := base
		mutate(&spec)
		if _, err := buildOCI(spec); err == nil {
			t.Fatalf("%s: buildOCI accepted an unsafe spec", name)
		}
	}
	probe := base
	probe.Program = ProgramIsolationProbe
	probe.ProbeMode = "shell"
	if _, err := buildOCI(probe); err == nil {
		t.Fatal("an unknown probe mode must be refused")
	}
}

func TestGuestPathConversion(t *testing.T) {
	cases := map[string]string{
		`E:\Orchara\product\worker\sales_stats.py`: "/mnt/e/Orchara/product/worker/sales_stats.py",
		`C:/Users/x/input.json`:                    "/mnt/c/Users/x/input.json",
		`\\wsl$\Ubuntu-24.04\opt\orchara\rootfs`:   "/opt/orchara/rootfs",
		"/opt/orchara-sandbox/python-rootfs":       "/opt/orchara-sandbox/python-rootfs",
	}
	for input, want := range cases {
		got, err := GuestPath(input)
		if err != nil {
			t.Fatalf("GuestPath(%q): %v", input, err)
		}
		if got != want {
			t.Fatalf("GuestPath(%q) = %q, want %q", input, got, want)
		}
	}
	if _, err := GuestPath("relative/path"); err == nil {
		t.Fatal("a guest-invisible relative path must be refused")
	}
}

func TestBuildRunCommandIsAFixedArgv(t *testing.T) {
	options, _ := testProfile(t)
	spec := bundleSpec{
		SandboxID: "unit-t033-test-1", StateRoot: options.StateRoot, Rootfs: options.Rootfs,
		Program: ProgramTool, InputName: "input.csv", MemoryMB: 256, PIDsLimit: 64, CPUSeconds: 30,
	}
	command, err := BuildRunCommand(options, spec, Staged{
		SandboxID: "unit-t033-test-1", HostWorkspace: "/tmp/staging",
		ConfigPath:    filepath.Join(options.WorkspaceRoot, "config.json"),
		InputPath:     filepath.Join(options.WorkspaceRoot, "input.csv"),
		InputJSONPath: filepath.Join(options.WorkspaceRoot, "input.json"),
		ToolPath:      options.ToolScript,
	})
	if err != nil {
		t.Fatalf("BuildRunCommand: %v", err)
	}
	if command[0] != "wsl" || command[1] != "-d" || command[2] != "Ubuntu-24.04" {
		t.Fatalf("command does not start the WSL transport: %v", command[:4])
	}
	if command[len(command)-len(command)] == "" {
		t.Fatal("unreachable")
	}
	joined := strings.Join(command, " ")
	for _, required := range []string{
		"sandbox_exec.sh run", "--sandbox-id unit-t033-test-1",
		"--state-root /var/tmp/orchara-sandbox", "--mode tool",
		"--timeout 2", "--memory-mb 256", "--pids 64", "--cpu-seconds 30",
	} {
		if !strings.Contains(joined, required) {
			t.Fatalf("command %q is missing %q", joined, required)
		}
	}
	if strings.Contains(joined, "sh -c") || strings.Contains(joined, "&&") {
		t.Fatalf("the host must not build a shell command: %q", joined)
	}
	cleanup, err := BuildCleanupCommand(options, "unit-t033-test-1", options.StateRoot)
	if err != nil {
		t.Fatalf("BuildCleanupCommand: %v", err)
	}
	if !strings.Contains(strings.Join(cleanup, " "), "sandbox_exec.sh cleanup") {
		t.Fatalf("cleanup command = %v", cleanup)
	}
}

func TestRecordedDigestIsAFailClosedIdentity(t *testing.T) {
	_, digest := testProfile(t)
	got, err := ParseRecordedDigest("  " + digest + "\n")
	if err != nil || got != digest {
		t.Fatalf("ParseRecordedDigest = %q, %v; want %q", got, err, digest)
	}
	for name, value := range map[string]string{
		"empty":        "",
		"no prefix":    strings.Repeat("ab", 32),
		"short":        "sha256:abc",
		"uppercase":    "sha256:" + strings.Repeat("AB", 32),
		"extra suffix": digest + "x",
	} {
		if _, err := ParseRecordedDigest(value); !errors.Is(err, ErrRootfsUnavailable) {
			t.Fatalf("%s: error = %v, want ErrRootfsUnavailable", name, err)
		}
	}
}

func TestParseOutputDocumentIsStrict(t *testing.T) {
	data, names, err := ParseOutputDocument([]byte(
		`{"data":{"total":"250.00"},"artifacts":["summary.json","summary.csv"]}`))
	if err != nil {
		t.Fatalf("valid output rejected: %v", err)
	}
	if string(data) != `{"total":"250.00"}` || len(names) != 2 {
		t.Fatalf("parsed data=%s names=%v", data, names)
	}
	refused := map[string]string{
		"unknown field":     `{"data":{},"artifacts":[],"extra":true}`,
		"data not object":   `{"data":[1,2],"artifacts":[]}`,
		"artifact escape":   `{"data":{},"artifacts":["../secret"]}`,
		"artifact absolute": `{"data":{},"artifacts":["/etc/passwd"]}`,
		"duplicate":         `{"data":{},"artifacts":["a.json","a.json"]}`,
		"trailing data":     `{"data":{},"artifacts":[]} {}`,
	}
	for name, payload := range refused {
		if _, _, err := ParseOutputDocument([]byte(payload)); err == nil {
			t.Fatalf("%s: output was accepted", name)
		}
	}
}

func TestAdmitArtifactsMatchesListedNamesExactly(t *testing.T) {
	collected := map[string][]byte{"summary.json": []byte(`{"total":"1.00"}`), "secret.txt": []byte("x")}
	if _, err := AdmitArtifacts([]string{"summary.json"}, collected); err == nil {
		t.Fatal("an unlisted collected file must be refused")
	}
	if _, err := AdmitArtifacts([]string{"summary.json", "missing.csv"}, map[string][]byte{
		"summary.json": []byte("{}"), "missing.csv": nil,
	}); err == nil {
		t.Fatal("an empty artifact must be refused")
	}
	admitted, err := AdmitArtifacts([]string{"summary.json"},
		map[string][]byte{"summary.json": []byte(`{"total":"1.00"}`)})
	if err != nil || len(admitted) != 1 || admitted[0].SHA256 == "" {
		t.Fatalf("admitted = %+v, err = %v", admitted, err)
	}
}

func TestExecutorSucceedsOnlyWhenCleanupIsProven(t *testing.T) {
	options, digest := testProfile(t)
	runner := &fakeRunner{handlers: []func(context.Context, []string) (CommandResult, error){
		func(context.Context, []string) (CommandResult, error) {
			return CommandResult{Stdout: validReport(t, digest)}, nil
		},
	}}
	executor, err := NewExecutor(options)
	if err != nil {
		t.Fatal(err)
	}
	executor.WithRunner(runner)
	outcome, runErr := executor.Run(context.Background(), toolRequest(), Control{})
	if runErr != nil {
		t.Fatalf("Run: %v (outcome %+v)", runErr, outcome)
	}
	if !outcome.CleanupVerified || !outcome.Cleanup.Clean() {
		t.Fatalf("cleanup was not proven: %+v", outcome.Cleanup)
	}
	if outcome.ExitCode != 0 || outcome.RootfsDigest != digest || len(outcome.Artifacts) != 1 {
		t.Fatalf("outcome = %+v", outcome)
	}
	if len(outcome.OutputJSON) == 0 {
		t.Fatal("output.json bytes were not carried")
	}
	calls := runner.callsSnapshot()
	if len(calls) != 1 {
		t.Fatalf("expected exactly one driver invocation, got %d", len(calls))
	}
	if !strings.Contains(strings.Join(calls[0], " "), "sandbox_exec.sh run") {
		t.Fatalf("the fake runner did not receive the run command: %v", calls[0])
	}
}

func TestExecutorRefusesAnOrphan(t *testing.T) {
	options, digest := testProfile(t)
	orphan := map[string]any{}
	if err := json.Unmarshal(validReport(t, digest), &orphan); err != nil {
		t.Fatal(err)
	}
	orphan["ok"] = false
	orphan["cleanup"] = map[string]any{
		"sandbox_list_matches": 0, "pid": 42, "pid_alive": true,
		"ps_matches": 1, "workspace_removed": false,
	}
	payload, _ := json.Marshal(orphan)
	runner := &fakeRunner{handlers: []func(context.Context, []string) (CommandResult, error){
		func(context.Context, []string) (CommandResult, error) {
			return CommandResult{Stdout: payload}, nil
		},
	}}
	executor, _ := NewExecutor(options)
	executor.WithRunner(runner)
	outcome, runErr := executor.Run(context.Background(), toolRequest(), Control{})
	if !errors.Is(runErr, ErrOrphan) {
		t.Fatalf("Run error = %v, want ErrOrphan (outcome %+v)", runErr, outcome)
	}
	if outcome.CleanupVerified {
		t.Fatal("an orphan must never be reported as verified")
	}
}

func TestExecutorRefusesAWrongRootfsDigest(t *testing.T) {
	options, _ := testProfile(t)
	runner := &fakeRunner{handlers: []func(context.Context, []string) (CommandResult, error){
		func(context.Context, []string) (CommandResult, error) {
			return CommandResult{Stdout: validReport(t, "sha256:"+strings.Repeat("cd", 32))}, nil
		},
	}}
	executor, _ := NewExecutor(options)
	executor.WithRunner(runner)
	if _, runErr := executor.Run(context.Background(), toolRequest(), Control{}); !errors.Is(runErr, ErrDigestMismatch) {
		t.Fatalf("Run error = %v, want ErrDigestMismatch", runErr)
	}
}

func TestExecutorTimeoutKillsAndRunsTheCleanupProof(t *testing.T) {
	options, _ := testProfile(t)
	options.Timeout = 100 * time.Millisecond
	runner := &fakeRunner{handlers: []func(context.Context, []string) (CommandResult, error){
		// The driver ran longer than the profile allows: the executor kills it.
		func(ctx context.Context, _ []string) (CommandResult, error) {
			<-ctx.Done()
			return CommandResult{Stderr: []byte("killed")}, ctx.Err()
		},
		// Cleanup entry point: the orphan proof.
		func(context.Context, []string) (CommandResult, error) {
			return CommandResult{Stdout: cleanupReport()}, nil
		},
	}}
	executor, _ := NewExecutor(options)
	executor.WithRunner(runner)
	outcome, runErr := executor.Run(context.Background(), toolRequest(), Control{})
	if !errors.Is(runErr, ErrTimeout) {
		t.Fatalf("Run error = %v, want ErrTimeout (outcome %+v)", runErr, outcome)
	}
	if !outcome.CleanupVerified || !outcome.Cleanup.Clean() {
		t.Fatalf("timeout cleanup was not proven: %+v", outcome.Cleanup)
	}
	calls := runner.callsSnapshot()
	if len(calls) != 2 || !strings.Contains(strings.Join(calls[1], " "), "sandbox_exec.sh cleanup") {
		t.Fatalf("expected a cleanup invocation after the kill, got %v", calls)
	}
}

func TestExecutorCancelFromHeartbeatKillsTheUnit(t *testing.T) {
	options, digest := testProfile(t)
	runner := &fakeRunner{handlers: []func(context.Context, []string) (CommandResult, error){
		func(ctx context.Context, _ []string) (CommandResult, error) {
			<-ctx.Done() // cancelled by the heartbeat
			return CommandResult{}, ctx.Err()
		},
		func(context.Context, []string) (CommandResult, error) {
			return CommandResult{Stdout: cleanupReport()}, nil
		},
	}}
	executor, _ := NewExecutor(options)
	executor.WithRunner(runner)
	heartbeats := 0
	control := Control{HeartbeatInterval: 10 * time.Millisecond,
		Heartbeat: func(context.Context) (HeartbeatState, error) {
			heartbeats++
			return HeartbeatState{CancelRequested: heartbeats >= 3}, nil
		}}
	outcome, runErr := executor.Run(context.Background(), toolRequest(), control)
	if !errors.Is(runErr, ErrCancelled) {
		t.Fatalf("Run error = %v, want ErrCancelled (outcome %+v)", runErr, outcome)
	}
	if !outcome.Cancelled || !outcome.CleanupVerified {
		t.Fatalf("cancel outcome = %+v", outcome)
	}
	_ = digest
}

func TestExecutorCancelBeforeStartNeverLaunchesTheSandbox(t *testing.T) {
	options, _ := testProfile(t)
	runner := &fakeRunner{}
	executor, _ := NewExecutor(options)
	executor.WithRunner(runner)
	control := Control{Heartbeat: func(context.Context) (HeartbeatState, error) {
		return HeartbeatState{CancelRequested: true}, nil
	}}
	outcome, runErr := executor.Run(context.Background(), toolRequest(), control)
	if !errors.Is(runErr, ErrCancelled) {
		t.Fatalf("Run error = %v, want ErrCancelled", runErr)
	}
	if outcome.SandboxStarted || len(runner.callsSnapshot()) != 0 {
		t.Fatalf("cancel-before-start launched the driver: %+v", outcome)
	}
}

func TestExecutorLostLeaseStopsTheUnit(t *testing.T) {
	options, _ := testProfile(t)
	runner := &fakeRunner{handlers: []func(context.Context, []string) (CommandResult, error){
		func(ctx context.Context, _ []string) (CommandResult, error) {
			<-ctx.Done()
			return CommandResult{}, ctx.Err()
		},
		func(context.Context, []string) (CommandResult, error) {
			return CommandResult{Stdout: cleanupReport()}, nil
		},
	}}
	executor, _ := NewExecutor(options)
	executor.WithRunner(runner)
	control := Control{HeartbeatInterval: 10 * time.Millisecond,
		Heartbeat: func(context.Context) (HeartbeatState, error) {
			return HeartbeatState{}, errors.New("stale_attempt")
		}}
	if _, runErr := executor.Run(context.Background(), toolRequest(), control); !errors.Is(runErr, ErrHeartbeat) {
		t.Fatalf("Run error = %v, want ErrHeartbeat", runErr)
	}
}

func TestIsolationProbeUsesTheClosedModeEnum(t *testing.T) {
	options, _ := testProfile(t)
	spec := bundleSpec{
		SandboxID: "unit-probe", StateRoot: options.StateRoot, Rootfs: options.Rootfs,
		Program: ProgramIsolationProbe, ProbeMode: ProbeSlow, ProbeSeconds: 3,
		InputName: "input.csv", MemoryMB: 64, PIDsLimit: 32, CPUSeconds: 10,
	}
	payload, err := buildOCI(spec)
	if err != nil {
		t.Fatalf("buildOCI: %v", err)
	}
	var config OCIConfig
	if err := json.Unmarshal(payload, &config); err != nil {
		t.Fatal(err)
	}
	want := []string{"/usr/bin/python3.12", "/tool/probe.py", "--mode", "slow", "--seconds", "3"}
	if strings.Join(config.Process.Args, " ") != strings.Join(want, " ") {
		t.Fatalf("probe args = %v", config.Process.Args)
	}
}

func hasOption(options []string, want string) bool {
	for _, option := range options {
		if option == want {
			return true
		}
	}
	return false
}
