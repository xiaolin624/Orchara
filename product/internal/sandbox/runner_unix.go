//go:build !windows

package sandbox

import (
	"os/exec"
	"syscall"
	"time"
)

const procWaitDelay = 10 * time.Second

func configureProcessGroup(command *exec.Cmd) {
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}

// terminateProcessGroup signals the whole group (negative pid), so a shell or
// driver that spawned children cannot leave them behind.
func terminateProcessGroup(command *exec.Cmd) error {
	if command.Process == nil {
		return nil
	}
	if err := syscall.Kill(-command.Process.Pid, syscall.SIGKILL); err == nil {
		return nil
	}
	return command.Process.Kill()
}
