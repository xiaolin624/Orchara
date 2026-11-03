//go:build windows

package sandbox

import (
	"os/exec"
	"strconv"
	"syscall"
	"time"
)

// createNewProcessGroup is CREATE_NEW_PROCESS_GROUP from the Windows API. The
// WSL client and every child it starts belong to one group, so termination with
// taskkill /T cannot miss a descendant.
const createNewProcessGroup = 0x00000200

const procWaitDelay = 10 * time.Second

func configureProcessGroup(command *exec.Cmd) {
	command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: createNewProcessGroup}
}

// terminateProcessGroup kills the tree led by the WSL client. taskkill is the
// documented way to end a Windows process group; if it is unavailable the direct
// kill is still attempted.
func terminateProcessGroup(command *exec.Cmd) error {
	if command.Process == nil {
		return nil
	}
	killTask := exec.Command("taskkill", "/T", "/F", "/PID", strconv.Itoa(command.Process.Pid))
	_ = killTask.Run()
	return command.Process.Kill()
}
