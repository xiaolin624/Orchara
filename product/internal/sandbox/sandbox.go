// Package sandbox runs the platform's fixed preset tool inside a minimal gVisor
// (runsc) OCI sandbox and reports exactly what the sandbox did.
//
// F05/T033 owns this package. The boundaries it holds:
//
//   - **A fixed program, never a user shell.** The OCI process args are built
//     here (sales_stats.py with four fixed flags, or the platform's isolation
//     probe in a test). No request value reaches an argv, an environment
//     variable or a mount option.
//   - **A read-only world.** The rootfs (a minimal Python build) is mounted
//     read-only, the tool script and the one authorized input file are read-only
//     bind mounts, the only writable mount is the output directory, and the
//     network namespace has no route (`--network=none`).
//   - **uid/gid 65534, no capabilities, no_new_privs.** pid/network/ipc/uts/
//     mount namespaces, memory/pids/CPU limits, a tmpfs /tmp and a fresh /proc.
//     A fixed command, never a user shell.
//   - **No orphan is an accepted outcome.** The execution unit runs in its own
//     process group and is killable. On success, timeout, cancel or failure the
//     driver deletes the sandbox and proves (runsc list, pid file, ps, workspace)
//     that nothing survived before this package reports any outcome.
//   - **The recorded digest is the execution identity.** The rootfs digest is
//     read from the build side (`<rootfs>.digest`, produced by
//     deploy/sandbox/build_python_rootfs.sh), reported back by the driver and
//     compared here; a mismatch is a refusal, not a warning.
//
// The driver itself is `deploy/sandbox/sandbox_exec.sh`: it stages the bundle,
// invokes runsc, collects the protocol files and performs the cleanup/verification
// in the guest. This package only builds the invocation, supervises it (heartbeat
// and kill), parses the driver's one JSON document and enforces the invariants.
package sandbox

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Program is the fixed program a sandbox may execute. It is a closed enum: a
// caller cannot name a command.
type Program string

const (
	// ProgramTool is the trusted preset sales-statistics tool.
	ProgramTool Program = "tool"
	// ProgramIsolationProbe is the platform-owned isolation probe used to prove
	// the sandbox boundaries in an experiment. It accepts no caller arguments
	// beyond a fixed mode enum.
	ProgramIsolationProbe Program = "probe"
	// ProgramBuildTool is F05/T037's checked-in build script. It receives the
	// submitted source zip and the canonical dependency lock as read-only inputs
	// and runs in the same minimal rootfs with no network and no host mounts.
	ProgramBuildTool Program = "build"
)

// ProbeMode is the fixed probe behaviour set.
type ProbeMode string

const (
	ProbeIsolation ProbeMode = "isolation"
	ProbeSlow      ProbeMode = "slow"
	ProbeCrash     ProbeMode = "crash"
)

// Defaults for the deployment profile. They are constants, not request values.
const (
	DefaultDistro     = "Ubuntu-24.04"
	DefaultStateRoot  = "/var/tmp/orchara-sandbox"
	DefaultRootfs     = "/opt/orchara-sandbox/python-rootfs"
	DefaultPython     = "/usr/bin/python3.12"
	DefaultMemoryMB   = 256
	DefaultPIDsLimit  = 64
	DefaultCPUSeconds = 60
	// The writable workspace is a quota-limited tmpfs; a single file is also
	// bounded by RLIMIT_FSIZE (SECURITY §2 disk limits).
	DefaultWorkspaceQuotaMB = 512
	MaxWorkspaceQuotaMB     = 4096
	DefaultFileSizeBytes    = 64 << 20
	MaxFileSizeBytes        = 1 << 30
	DefaultTimeout          = 60 * time.Second
	DefaultGracePeriod      = 60 * time.Second
	DefaultHeartbeatTick    = 5 * time.Second
	MaxMemoryMB             = 8192
	MaxTimeout              = 10 * time.Minute
)

// sandboxIDPattern is the only shape a sandbox id may have; it becomes a
// runsc container name and a directory name.
var sandboxIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,62}$`)

// inputNamePattern is the authorized input file name inside /inputs.
var inputNamePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,126}$`)

// Errors callers map to stable run failure codes.
var (
	// ErrRootfsUnavailable: the built rootfs or its recorded digest is missing.
	ErrRootfsUnavailable = errors.New("the sandbox rootfs or its digest is unavailable")
	// ErrToolUnavailable: the fixed tool script is missing or not a file.
	ErrToolUnavailable = errors.New("the sandbox tool script is unavailable")
	// ErrRunnerUnavailable: the WSL/runsc driver cannot be invoked at all.
	ErrRunnerUnavailable = errors.New("the sandbox driver is unavailable")
	// ErrDriverReport: the driver's JSON document is absent/invalid.
	ErrDriverReport = errors.New("the sandbox driver reported an unusable result")
	// ErrSandboxRefused: the driver answered with its pre-start refusal document
	// (`{"ok":false,"error":...}`) and proved no execution unit was launched. No
	// orphan/cleanup assertion applies because nothing was created.
	ErrSandboxRefused = errors.New("the sandbox driver refused before launching a unit")
	// ErrTimeout: the sandbox exceeded its wall-clock limit and was killed.
	ErrTimeout = errors.New("the sandbox exceeded its per-run timeout")
	// ErrCancelled: the heartbeat reported cancel_requested; the unit was killed.
	ErrCancelled = errors.New("the sandbox was stopped because the run requested cancellation")
	// ErrHeartbeat: the attempt lease could not be renewed; the unit was killed.
	ErrHeartbeat = errors.New("the sandbox was stopped because the attempt lease was lost")
	// ErrOrphan: the driver or the post-kill cleanup proved that something
	// survived the execution.
	ErrOrphan = errors.New("the sandbox cleanup left evidence behind")
	// ErrDigestMismatch: the driver ran a rootfs whose digest is not the one the
	// platform recorded.
	ErrDigestMismatch = errors.New("the sandbox rootfs digest does not match the recorded execution identity")
)

// Options is the deployment profile of the sandbox. Every field has a documented
// default; a zero value is normalised, never silently trusted.
type Options struct {
	// Distro is the WSL distribution. Empty means "run bash directly on this
	// host" (a Linux execution host); the supported acceptance host here is WSL2.
	Distro string
	// StateRoot is the guest directory that holds one workspace per sandbox.
	StateRoot string
	// Rootfs is the built minimal Python rootfs *guest* path. Its recorded digest
	// (`<Rootfs>.digest`, produced by deploy/sandbox/build_python_rootfs.sh) is
	// checked inside the guest against ExpectedRootfsDigest.
	Rootfs string
	// ExpectedRootfsDigest is the deployment's recorded rootfs digest. It is
	// required: a sandbox that cannot state its image identity is refused rather
	// than run unrecorded.
	ExpectedRootfsDigest string
	// Python is the interpreter path inside the rootfs.
	Python string
	// ToolScript is the host path of the fixed preset tool script.
	ToolScript string
	// DriverScript is the host path of deploy/sandbox/sandbox_exec.sh.
	DriverScript string
	// ProbeScript is the host path of deploy/sandbox/isolation_probe.py.
	ProbeScript string
	// BuildScript is the host path of deploy/sandbox/build_tool.py (T037).
	BuildScript string
	// MemoryMB/PIDsLimit/CPUSeconds are the OCI resource limits.
	MemoryMB   int
	PIDsLimit  int
	CPUSeconds int
	// WorkspaceQuotaMB bounds the writable /workspace tmpfs (host volume
	// protection); FileSizeBytes bounds any single file via RLIMIT_FSIZE.
	WorkspaceQuotaMB int
	FileSizeBytes    int64
	// Timeout bounds one sandbox process; GracePeriod bounds the whole driver
	// invocation (staging + cleanup), which is longer on purpose.
	Timeout     time.Duration
	GracePeriod time.Duration
	// HeartbeatTick is how often the caller's heartbeat runs while the sandbox
	// executes.
	HeartbeatTick time.Duration
	// WorkspaceRoot is the host directory for the staged input.json/input file.
	// Empty means the OS temp directory.
	WorkspaceRoot string
}

// normalized fills defaults and rejects values that would make the profile
// unsafe or undecidable.
func (o Options) normalized() (Options, error) {
	if o.Distro == "" {
		o.Distro = os.Getenv("ORCHARA_WSL_DISTRO")
	}
	if o.StateRoot == "" {
		o.StateRoot = os.Getenv("ORCHARA_SANDBOX_STATE_ROOT")
	}
	if o.StateRoot == "" {
		o.StateRoot = DefaultStateRoot
	}
	if o.Rootfs == "" {
		o.Rootfs = os.Getenv("ORCHARA_SANDBOX_PYTHON_ROOTFS")
	}
	if o.Rootfs == "" {
		o.Rootfs = DefaultRootfs
	}
	if o.ExpectedRootfsDigest == "" {
		o.ExpectedRootfsDigest = os.Getenv("ORCHARA_SANDBOX_ROOTFS_DIGEST")
	}
	if o.Python == "" {
		o.Python = DefaultPython
	}
	if o.DriverScript == "" {
		o.DriverScript = os.Getenv("ORCHARA_SANDBOX_DRIVER")
	}
	if o.ProbeScript == "" {
		o.ProbeScript = os.Getenv("ORCHARA_SANDBOX_PROBE")
	}
	if o.BuildScript == "" {
		o.BuildScript = os.Getenv("ORCHARA_SANDBOX_BUILD_TOOL")
	}
	if o.MemoryMB <= 0 {
		o.MemoryMB = DefaultMemoryMB
	}
	if o.MemoryMB > MaxMemoryMB {
		return Options{}, fmt.Errorf("sandbox memory limit must be at most %d MiB", MaxMemoryMB)
	}
	if o.PIDsLimit <= 0 {
		o.PIDsLimit = DefaultPIDsLimit
	}
	if o.CPUSeconds <= 0 {
		o.CPUSeconds = DefaultCPUSeconds
	}
	// SECURITY §2: an untrusted process must not be able to fill the host volume.
	// /workspace is a size-limited tmpfs and every file is additionally bounded
	// by RLIMIT_FSIZE; both are deployment knobs with safe defaults.
	if o.WorkspaceQuotaMB <= 0 {
		if raw := strings.TrimSpace(os.Getenv("ORCHARA_SANDBOX_WORKSPACE_QUOTA_MB")); raw != "" {
			value, err := strconv.Atoi(raw)
			if err != nil || value <= 0 || value > MaxWorkspaceQuotaMB {
				return Options{}, fmt.Errorf(
					"ORCHARA_SANDBOX_WORKSPACE_QUOTA_MB must be a positive integer at most %d",
					MaxWorkspaceQuotaMB,
				)
			}
			o.WorkspaceQuotaMB = value
		}
	}
	if o.WorkspaceQuotaMB <= 0 {
		o.WorkspaceQuotaMB = DefaultWorkspaceQuotaMB
	}
	if o.WorkspaceQuotaMB > MaxWorkspaceQuotaMB {
		return Options{}, fmt.Errorf("sandbox workspace quota must be at most %d MiB", MaxWorkspaceQuotaMB)
	}
	if o.FileSizeBytes <= 0 {
		o.FileSizeBytes = DefaultFileSizeBytes
	}
	if o.FileSizeBytes > MaxFileSizeBytes {
		return Options{}, fmt.Errorf("sandbox file-size limit must be at most %d bytes", MaxFileSizeBytes)
	}
	if o.Timeout <= 0 {
		o.Timeout = DefaultTimeout
	}
	if o.Timeout > MaxTimeout {
		return Options{}, fmt.Errorf("sandbox timeout must be at most %s", MaxTimeout)
	}
	if o.GracePeriod <= 0 {
		o.GracePeriod = DefaultGracePeriod
	}
	if o.HeartbeatTick <= 0 {
		o.HeartbeatTick = DefaultHeartbeatTick
	}
	if o.HeartbeatTick < 10*time.Millisecond {
		return Options{}, errors.New("sandbox heartbeat tick must be at least 10ms")
	}
	if strings.TrimSpace(o.Rootfs) == "" || strings.TrimSpace(o.StateRoot) == "" {
		return Options{}, ErrRootfsUnavailable
	}
	return o, nil
}

// NewExecutor builds the executor. A missing rootfs/digest is not a startup
// error: the executor fails closed per run with ErrRootfsUnavailable so a broken
// deployment produces an honest failed run instead of a refused process.
func NewExecutor(options Options) (*Executor, error) {
	normalized, err := options.normalized()
	if err != nil {
		return nil, err
	}
	return &Executor{options: normalized, runner: &execRunner{}}, nil
}

// OptionsFromEnv builds the deployment profile from the documented environment.
// It is shared by the supervisor's `exec` mode and the operator probe so the two
// cannot drift into different sandbox configurations.
//
// The recorded rootfs digest is required for an execution but not for parsing:
// an empty digest yields a profile whose runs fail closed with a specific code.
func OptionsFromEnv(productRoot string) (Options, error) {
	if strings.TrimSpace(productRoot) == "" {
		productRoot = "."
	}
	options := Options{
		Distro:               envOr("ORCHARA_WSL_DISTRO", DefaultDistro),
		StateRoot:            envOr("ORCHARA_SANDBOX_STATE_ROOT", DefaultStateRoot),
		Rootfs:               envOr("ORCHARA_SANDBOX_PYTHON_ROOTFS", DefaultRootfs),
		ExpectedRootfsDigest: strings.TrimSpace(os.Getenv("ORCHARA_SANDBOX_ROOTFS_DIGEST")),
		// The host staging root is where the per-attempt workspace is created and
		// removed. Empty keeps the OS temp directory; a deployment that pins it
		// must give the same value to cmd/maintenance copies (F11/T072).
		WorkspaceRoot: strings.TrimSpace(os.Getenv("ORCHARA_SANDBOX_WORKSPACE_ROOT")),
		ToolScript: envOr("ORCHARA_SANDBOX_TOOL",
			filepath.Join(productRoot, "worker", "platform_worker", "nodes", "sales_stats.py")),
		ProbeScript: envOr("ORCHARA_SANDBOX_PROBE",
			filepath.Join(productRoot, "deploy", "sandbox", "isolation_probe.py")),
		BuildScript: envOr("ORCHARA_SANDBOX_BUILD_TOOL",
			filepath.Join(productRoot, "deploy", "sandbox", "build_tool.py")),
		DriverScript: envOr("ORCHARA_SANDBOX_DRIVER",
			filepath.Join(productRoot, "deploy", "sandbox", "sandbox_exec.sh")),
	}
	if raw := strings.TrimSpace(os.Getenv("ORCHARA_SANDBOX_TIMEOUT")); raw != "" {
		timeout, err := time.ParseDuration(raw)
		if err != nil || timeout <= 0 {
			return Options{}, errors.New("ORCHARA_SANDBOX_TIMEOUT must be a positive duration")
		}
		options.Timeout = timeout
	}
	if raw := strings.TrimSpace(os.Getenv("ORCHARA_SANDBOX_MEMORY_MB")); raw != "" {
		value, err := strconv.Atoi(raw)
		if err != nil || value <= 0 || value > MaxMemoryMB {
			return Options{}, errors.New("ORCHARA_SANDBOX_MEMORY_MB is not a valid limit")
		}
		options.MemoryMB = value
	}
	if raw := strings.TrimSpace(os.Getenv("ORCHARA_SANDBOX_PIDS")); raw != "" {
		value, err := strconv.Atoi(raw)
		if err != nil || value <= 0 {
			return Options{}, errors.New("ORCHARA_SANDBOX_PIDS is not a valid limit")
		}
		options.PIDsLimit = value
	}
	if raw := strings.TrimSpace(os.Getenv("ORCHARA_SANDBOX_CPU_SECONDS")); raw != "" {
		value, err := strconv.Atoi(raw)
		if err != nil || value <= 0 {
			return Options{}, errors.New("ORCHARA_SANDBOX_CPU_SECONDS is not a valid limit")
		}
		options.CPUSeconds = value
	}
	return options.normalized()
}

func envOr(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

// WithRunner replaces the process runner (tests use a fake; production never does).
func (e *Executor) WithRunner(runner Runner) *Executor {
	if runner != nil {
		e.runner = runner
	}
	return e
}

// ParseRecordedDigest validates a digest recorded by the rootfs builder (or by
// the deployment that installed it). It is the execution identity the driver
// verifies against `<rootfs>.digest` inside the guest; the executor never has to
// reach the guest filesystem from the host to check it.
func ParseRecordedDigest(value string) (string, error) {
	digest := strings.TrimSpace(value)
	if !IsDigest(digest) {
		return "", fmt.Errorf("%w: %q is not a sha256 digest", ErrRootfsUnavailable, value)
	}
	return digest, nil
}

// IsDigest reports whether value is `sha256:` plus 64 lowercase hex characters.
func IsDigest(value string) bool {
	const prefix = "sha256:"
	if !strings.HasPrefix(value, prefix) || len(value) != len(prefix)+64 {
		return false
	}
	for _, character := range value[len(prefix):] {
		if (character < '0' || character > '9') && (character < 'a' || character > 'f') {
			return false
		}
	}
	return true
}

// VerifyTool proves the fixed tool script exists and is a regular file.
func VerifyTool(path string) error {
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		return fmt.Errorf("%w: %s", ErrToolUnavailable, path)
	}
	return nil
}

// NewSandboxID derives a runsc-safe sandbox id for one execution unit. It is
// lower-case alphanumerics and '-' only, so it is safe as a container name and
// a directory name.
func NewSandboxID(executionUnitID string) (string, error) {
	cleaned := strings.ToLower(strings.TrimSpace(executionUnitID))
	var builder strings.Builder
	for _, character := range cleaned {
		switch {
		case character >= 'a' && character <= 'z', character >= '0' && character <= '9':
			builder.WriteRune(character)
		case character == '-' || character == '_' || character == ':' || character == '.' || character == '/':
			builder.WriteRune('-')
		}
	}
	candidate := strings.Trim(builder.String(), "-")
	if len(candidate) > 48 {
		candidate = candidate[:48]
	}
	if !sandboxIDPattern.MatchString(candidate) {
		return "", errors.New("cannot derive a sandbox id from the execution unit id")
	}
	return candidate, nil
}

// workspacePath is the host temp path for one staging directory.
//
// The sandbox id is part of the directory name (F11/T072): the workspace is
// removed per attempt in the same process, but a crashed attempt leaves a
// leftover behind, and the cleanup pass can only attribute a leftover to the
// deleted run through the attempt's execution unit id -> sandbox id. Do not
// shorten the name to a random suffix.
func (o Options) workspacePath(sandboxID string) (string, error) {
	return os.MkdirTemp(o.WorkspaceRoot, "orchara-sandbox-"+sandboxID+"-")
}

// toolPathFor returns the host path of the fixed program the request names.
func (o Options) toolPathFor(program Program) (string, error) {
	switch program {
	case ProgramTool:
		if strings.TrimSpace(o.ToolScript) == "" {
			return "", fmt.Errorf("%w: no tool script configured", ErrToolUnavailable)
		}
		return o.ToolScript, nil
	case ProgramIsolationProbe:
		if strings.TrimSpace(o.ProbeScript) == "" {
			return "", fmt.Errorf("%w: no probe script configured", ErrToolUnavailable)
		}
		return o.ProbeScript, nil
	case ProgramBuildTool:
		if strings.TrimSpace(o.BuildScript) == "" {
			return "", fmt.Errorf("%w: no build script configured", ErrToolUnavailable)
		}
		return o.BuildScript, nil
	default:
		return "", fmt.Errorf("%w: unknown program %q", ErrToolUnavailable, program)
	}
}

// toolNameInSandbox is the fixed name the program gets inside /tool. It is not
// derived from a request field.
func toolNameInSandbox(program Program) (string, error) {
	switch program {
	case ProgramTool:
		return "sales_stats.py", nil
	case ProgramIsolationProbe:
		return "probe.py", nil
	case ProgramBuildTool:
		return "build_tool.py", nil
	default:
		return "", fmt.Errorf("%w: unknown program %q", ErrToolUnavailable, program)
	}
}

// InputNameForMediaType maps a supported input media type onto the fixed file
// name the tool protocol uses. The supervisor needs it to write input.json; the
// mapping is still the sandbox's (one source of truth).
func InputNameForMediaType(mediaType string) (string, error) {
	return inputFileName(mediaType)
}

// inputFileName maps the artifact media type onto the fixed input file name. The
// tool protocol derives the parser from the extension, so the name is fixed by
// the (already validated) media type, never by the caller.
func inputFileName(mediaType string) (string, error) {
	switch strings.ToLower(strings.TrimSpace(mediaType)) {
	case "text/csv":
		return "input.csv", nil
	case "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
		return "input.xlsx", nil
	default:
		return "", fmt.Errorf("unsupported sandbox input media type %q", mediaType)
	}
}

// driverPath returns the host path of the driver script.
func (o Options) driverPath() (string, error) {
	if strings.TrimSpace(o.DriverScript) == "" {
		return "", fmt.Errorf("%w: no driver script configured", ErrRunnerUnavailable)
	}
	if err := VerifyTool(o.DriverScript); err != nil {
		return "", fmt.Errorf("%w: %v", ErrRunnerUnavailable, err)
	}
	return o.DriverScript, nil
}

// joinGuest joins guest path segments with '/'.
func joinGuest(parts ...string) string {
	return filepath.ToSlash(strings.TrimSuffix(strings.Join(parts, "/"), "/"))
}

// contextWithoutCancel is a tiny helper for cleanup after a cancelled context:
// the caller's cancellation must not prevent the orphan proof.
func contextWithoutCancel(ctx context.Context) context.Context {
	return context.WithoutCancel(ctx)
}
