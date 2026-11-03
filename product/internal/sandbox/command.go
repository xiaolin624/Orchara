package sandbox

// F05/T033: the driver invocation.
//
// The host process runs `wsl -d <distro> -u root -e bash sandbox_exec.sh ...` (or
// the same script directly on a Linux execution host). This file builds that
// argv from the deployment profile and the staged paths, and converts host paths
// into the paths the guest can see. It contains no shell evaluation on the host:
// every element is an argv element.

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var windowsDrivePattern = regexp.MustCompile(`^([A-Za-z]):[\\/](.*)$`)

// GuestPath converts a host path into the path the guest sees.
//
//   - Windows drive paths become /mnt/<drive>/... (the standard WSL automount).
//   - \\wsl$\Distro\... and \\wsl.localhost\Distro\... become /...
//   - POSIX paths are returned unchanged (a Linux execution host).
//
// A host path the guest cannot see is an error: the driver must never be handed
// a path it will silently fail to read.
func GuestPath(hostPath string) (string, error) {
	trimmed := strings.TrimSpace(hostPath)
	if trimmed == "" {
		return "", fmt.Errorf("empty host path")
	}
	if match := windowsDrivePattern.FindStringSubmatch(trimmed); match != nil {
		rest := strings.ReplaceAll(match[2], `\`, "/")
		return "/mnt/" + strings.ToLower(match[1]) + "/" + rest, nil
	}
	lower := strings.ToLower(trimmed)
	for _, prefix := range []string{`\\wsl$\`, `\\wsl.localhost\`} {
		if strings.HasPrefix(lower, prefix) {
			rest := trimmed[len(prefix):]
			if slash := strings.IndexAny(rest, `\/`); slash >= 0 {
				return "/" + strings.ReplaceAll(rest[slash+1:], `\`, "/"), nil
			}
			return "", fmt.Errorf("wsl UNC path has no in-distribution path: %q", hostPath)
		}
	}
	if strings.HasPrefix(trimmed, "/") {
		return trimmed, nil
	}
	return "", fmt.Errorf("host path %q is not visible inside the guest", hostPath)
}

// Staged is the host-side staging of one execution.
type Staged struct {
	// SandboxID is the runsc container name and workspace directory name.
	SandboxID string
	// HostWorkspace is the host temp directory holding config/input documents.
	HostWorkspace string
	// ConfigPath/InputPath/InputJSONPath/ToolPath are the host paths written by
	// the executor (or, for the tool, the deployment's script path).
	ConfigPath    string
	InputPath     string
	InputJSONPath string
	ToolPath      string
}

// BuildRunCommand returns the argv that stages and runs one sandbox.
func BuildRunCommand(options Options, spec bundleSpec, staged Staged) ([]string, error) {
	driver := options.DriverScript
	if strings.TrimSpace(driver) == "" {
		return nil, fmt.Errorf("%w: no driver script configured", ErrRunnerUnavailable)
	}
	guestDriver, err := GuestPath(driver)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrRunnerUnavailable, err)
	}
	guestConfig, err := GuestPath(staged.ConfigPath)
	if err != nil {
		return nil, err
	}
	guestInput, err := GuestPath(staged.InputPath)
	if err != nil {
		return nil, err
	}
	guestInputJSON, err := GuestPath(staged.InputJSONPath)
	if err != nil {
		return nil, err
	}
	guestTool, err := GuestPath(staged.ToolPath)
	if err != nil {
		return nil, err
	}

	timeoutSeconds := spec.TimeoutSeconds
	if timeoutSeconds <= 0 {
		timeoutSeconds = int(options.Timeout / time.Second)
	}
	argv := wslPrefix(options, guestDriver, "run")
	argv = append(argv,
		"--sandbox-id", spec.SandboxID,
		"--state-root", spec.StateRoot,
		"--rootfs", spec.Rootfs,
		"--rootfs-digest", options.ExpectedRootfsDigest,
		"--tool", guestTool,
		"--input", guestInput,
		"--input-json", guestInputJSON,
		"--input-name", spec.InputName,
		"--config", guestConfig,
		"--timeout", strconv.Itoa(timeoutSeconds),
		"--memory-mb", strconv.Itoa(spec.MemoryMB),
		"--pids", strconv.Itoa(spec.PIDsLimit),
		"--cpu-seconds", strconv.Itoa(spec.CPUSeconds),
		"--workspace-quota-mb", strconv.Itoa(options.WorkspaceQuotaMB),
		"--mode", string(spec.Program),
	)
	if spec.Program == ProgramIsolationProbe {
		argv = append(argv,
			"--probe-mode", string(spec.ProbeMode),
			"--probe-seconds", strconv.Itoa(spec.ProbeSeconds),
		)
	}
	return argv, nil
}

// BuildCleanupCommand returns the argv that deletes the sandbox, kills a surviving
// pid, verifies `runsc list`/`ps` and removes the workspace.
func BuildCleanupCommand(options Options, sandboxID, stateRoot string) ([]string, error) {
	if strings.TrimSpace(options.DriverScript) == "" {
		return nil, fmt.Errorf("%w: no driver script configured", ErrRunnerUnavailable)
	}
	guestDriver, err := GuestPath(options.DriverScript)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrRunnerUnavailable, err)
	}
	argv := wslPrefix(options, guestDriver, "cleanup")
	argv = append(argv,
		"--sandbox-id", sandboxID,
		"--state-root", stateRoot,
	)
	return argv, nil
}

// wslPrefix returns the transport prefix plus the driver script and subcommand.
// Keep in sync with the driver's argument parser (`run|cleanup` first).
func wslPrefix(options Options, guestDriver, subcommand string) []string {
	argv := make([]string, 0, 8)
	if distro := strings.TrimSpace(options.Distro); distro != "" {
		argv = append(argv, "wsl", "-d", distro, "-u", "root", "-e", "bash")
	} else {
		argv = append(argv, "bash")
	}
	return append(argv, guestDriver, subcommand)
}

// SandboxTimeout returns the driver-visible timeout (at least one second; runsc
// needs a positive bound).
func SandboxTimeout(timeout time.Duration) time.Duration {
	if timeout < time.Second {
		return time.Second
	}
	return timeout
}
