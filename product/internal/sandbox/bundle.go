package sandbox

// F05/T033: the OCI bundle builder.
//
// The bundle is *built here*, in Go, not in the shell driver: the process args,
// the user, the namespaces, the resource limits and the mount list are a data
// structure unit tests can assert on without a Linux host, and a deployment
// cannot drift because a shell script and a Go builder disagree. The driver
// (deploy/sandbox/sandbox_exec.sh) copies this config into the bundle and runs
// runsc with it.
//
// Everything caller-supplied in the config is a path computed from the sandbox
// id and the deployment profile; nothing is request data. The only open values
// are the probe mode (a closed enum) and the numeric limits (clamped by Options).

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
)

// OCI config shapes (the subset runsc needs, spelled out so tests can read them).
type OCIConfig struct {
	OCIVersion  string            `json:"ociVersion"`
	Process     OCIProcess        `json:"process"`
	Root        OCIRoot           `json:"root"`
	Hostname    string            `json:"hostname"`
	Mounts      []OCIMount        `json:"mounts"`
	Linux       OCILinux          `json:"linux"`
	Annotations map[string]string `json:"annotations,omitempty"`
}

type OCIProcess struct {
	Terminal        bool        `json:"terminal"`
	User            OCIUser     `json:"user"`
	Args            []string    `json:"args"`
	Env             []string    `json:"env"`
	Cwd             string      `json:"cwd"`
	NoNewPrivileges bool        `json:"noNewPrivileges"`
	Capabilities    OCICaps     `json:"capabilities"`
	Rlimits         []OCIRlimit `json:"rlimits,omitempty"`
}

type OCIUser struct {
	UID uint32 `json:"uid"`
	GID uint32 `json:"gid"`
}

type OCICaps struct {
	Bounding    []string `json:"bounding"`
	Effective   []string `json:"effective"`
	Permitted   []string `json:"permitted"`
	Inheritable []string `json:"inheritable"`
	Ambient     []string `json:"ambient"`
}

type OCIRlimit struct {
	Type string `json:"type"`
	Soft uint64 `json:"soft"`
	Hard uint64 `json:"hard"`
}

type OCIRoot struct {
	Path     string `json:"path"`
	Readonly bool   `json:"readonly"`
}

type OCIMount struct {
	Destination string   `json:"destination"`
	Type        string   `json:"type"`
	Source      string   `json:"source"`
	Options     []string `json:"options,omitempty"`
}

type OCILinux struct {
	Namespaces []OCINamespace `json:"namespaces"`
	Resources  OCIResources   `json:"resources"`
}

type OCINamespace struct {
	Type string `json:"type"`
}

type OCIResources struct {
	Memory OCIMemory `json:"memory"`
	Pids   OCIPids   `json:"pids"`
}

type OCIMemory struct {
	Limit int64 `json:"limit"`
	Swap  int64 `json:"swap"`
}

type OCIPids struct {
	Limit int64 `json:"limit"`
}

// spec is the internal bundle input after validation.
type bundleSpec struct {
	SandboxID      string
	StateRoot      string
	Rootfs         string
	Program        Program
	ProbeMode      ProbeMode
	ProbeSeconds   int
	InputName      string
	MemoryMB       int
	PIDsLimit      int
	CPUSeconds     int
	FileSizeBytes  int64
	TimeoutSeconds int
}

// workspaceDir is the guest workspace of one sandbox.
func (s bundleSpec) workspaceDir() string {
	return joinGuest(s.StateRoot, s.SandboxID)
}

var probeModePattern = regexp.MustCompile(`^(isolation|slow|crash)$`)

// buildOCI validates the spec and returns the canonical OCI config bytes.
func buildOCI(spec bundleSpec) ([]byte, error) {
	if !sandboxIDPattern.MatchString(spec.SandboxID) {
		return nil, fmt.Errorf("invalid sandbox id %q", spec.SandboxID)
	}
	if !strings.HasPrefix(spec.StateRoot, "/") {
		return nil, fmt.Errorf("state root must be an absolute guest path")
	}
	if !strings.HasPrefix(spec.Rootfs, "/") {
		return nil, fmt.Errorf("rootfs must be an absolute guest path")
	}
	if !inputNamePattern.MatchString(spec.InputName) {
		return nil, fmt.Errorf("invalid input file name %q", spec.InputName)
	}
	if spec.MemoryMB <= 0 || spec.PIDsLimit <= 0 || spec.CPUSeconds <= 0 {
		return nil, fmt.Errorf("sandbox resource limits must be positive")
	}
	if spec.FileSizeBytes <= 0 {
		spec.FileSizeBytes = DefaultFileSizeBytes
	}
	workspace := spec.workspaceDir()

	var args []string
	switch spec.Program {
	case ProgramTool:
		// Re-spelled from the fixed prefix so a test can assert the exact argv.
		args = []string{
			"/usr/bin/python3.12", "/tool/sales_stats.py",
			"--input", "/workspace/input.json",
			"--output", "/workspace/output.json",
			"--inputs-dir", "/inputs",
			"--artifacts-dir", "/workspace/artifacts",
		}
	case ProgramBuildTool:
		// Fixed argv: the source zip and the canonical lock are the only inputs,
		// and both mount names are constants. No user value reaches this argv.
		args = []string{
			"/usr/bin/python3.12", "/tool/build_tool.py",
			"--source", "/inputs/source.zip",
			"--lock", "/inputs/lock.json",
			"--output", "/workspace/build.json",
			"--log", "/workspace/build.log",
			"--artifacts-dir", "/workspace/artifacts",
			"--work", "/workspace/work",
		}
	case ProgramIsolationProbe:
		mode := string(spec.ProbeMode)
		if !probeModePattern.MatchString(mode) {
			return nil, fmt.Errorf("invalid probe mode %q", mode)
		}
		seconds := spec.ProbeSeconds
		if seconds <= 0 {
			seconds = 60
		}
		args = []string{
			"/usr/bin/python3.12", "/tool/probe.py",
			"--mode", mode,
			"--seconds", fmt.Sprintf("%d", seconds),
		}
	default:
		return nil, fmt.Errorf("unknown sandbox program %q", spec.Program)
	}

	config := OCIConfig{
		OCIVersion: "1.0.2",
		Process: OCIProcess{
			Terminal: false,
			User:     OCIUser{UID: 65534, GID: 65534},
			Args:     args,
			Env: []string{
				"PATH=/usr/bin:/bin",
				"PYTHONHOME=/usr",
				"PYTHONNOUSERSITE=1",
				"PYTHONDONTWRITEBYTECODE=1",
				"PYTHONHASHSEED=0",
			},
			Cwd:             "/workspace",
			NoNewPrivileges: true,
			Capabilities: OCICaps{
				Bounding: []string{}, Effective: []string{}, Permitted: []string{},
				Inheritable: []string{}, Ambient: []string{},
			},
			Rlimits: []OCIRlimit{
				{Type: "RLIMIT_CPU", Soft: uint64(spec.CPUSeconds), Hard: uint64(spec.CPUSeconds)},
				{Type: "RLIMIT_NOFILE", Soft: 256, Hard: 256},
				// SECURITY §2: no single file may exceed the configured bound, even
				// inside the quota-limited workspace tmpfs.
				{Type: "RLIMIT_FSIZE", Soft: uint64(spec.FileSizeBytes), Hard: uint64(spec.FileSizeBytes)},
			},
		},
		Root:     OCIRoot{Path: spec.Rootfs, Readonly: true},
		Hostname: "sandbox",
		Mounts: []OCIMount{
			{Destination: "/proc", Type: "proc", Source: "proc"},
			{Destination: "/tmp", Type: "tmpfs", Source: "tmpfs",
				Options: []string{"nosuid", "nodev", "noexec", "size=16m"}},
			{Destination: "/tool", Type: "bind", Source: joinGuest(workspace, "tool"),
				Options: []string{"ro", "bind"}},
			{Destination: "/inputs", Type: "bind", Source: joinGuest(workspace, "inputs"),
				Options: []string{"ro", "bind"}},
			{Destination: "/workspace", Type: "bind", Source: joinGuest(workspace, "output"),
				Options: []string{"rw", "bind"}},
		},
		Linux: OCILinux{
			Namespaces: []OCINamespace{
				{Type: "pid"}, {Type: "network"}, {Type: "ipc"}, {Type: "uts"}, {Type: "mount"},
			},
			Resources: OCIResources{
				Memory: OCIMemory{
					Limit: int64(spec.MemoryMB) * 1024 * 1024,
					Swap:  int64(spec.MemoryMB) * 1024 * 1024,
				},
				Pids: OCIPids{Limit: int64(spec.PIDsLimit)},
			},
		},
	}
	return json.MarshalIndent(config, "", "  ")
}
