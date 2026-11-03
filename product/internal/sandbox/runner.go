package sandbox

// F05/T033: the process runner.
//
// The sandbox execution unit must be killable as one unit. On Windows the WSL
// client is started in a new process group and a cancellation terminates the
// group with taskkill /T; on Linux the child gets its own process group and a
// cancellation signals the group. Go's WaitDelay then bounds the wait for the
// pipe readers so a cancelled call cannot hang the supervisor.

import (
	"bytes"
	"context"
	"errors"
	"os/exec"
)

// CommandResult is one finished runner invocation.
type CommandResult struct {
	Stdout   []byte
	Stderr   []byte
	ExitCode int
}

// Runner executes one argv and returns its streams. It is an interface so the
// executor's protocol handling (report parsing, cleanup verification, timeout and
// cancel) can be tested with a fake, without pretending runsc ran.
type Runner interface {
	Run(ctx context.Context, argv []string) (CommandResult, error)
}

// maxCapturedOutput bounds what the runner keeps in memory; the driver itself
// caps its report, and a broken driver must not exhaust the supervisor.
const maxCapturedOutput = 16 << 20

// execRunner is the real runner.
type execRunner struct{}

func (r *execRunner) Run(ctx context.Context, argv []string) (CommandResult, error) {
	if len(argv) == 0 {
		return CommandResult{}, errors.New("empty command")
	}
	command := exec.CommandContext(ctx, argv[0], argv[1:]...)
	var stdout, stderr limitedBuffer
	stdout.limit = maxCapturedOutput
	stderr.limit = maxCapturedOutput
	command.Stdout = &stdout
	command.Stderr = &stderr
	command.Stdin = nil
	// A cancelled command kills the whole unit, not only the WSL client.
	configureProcessGroup(command)
	command.Cancel = func() error {
		return terminateProcessGroup(command)
	}
	command.WaitDelay = procWaitDelay
	err := command.Run()
	result := CommandResult{Stdout: stdout.Bytes(), Stderr: stderr.Bytes(), ExitCode: 0}
	if err != nil {
		var exitError *exec.ExitError
		if errors.As(err, &exitError) {
			result.ExitCode = exitError.ExitCode()
			return result, nil
		}
		return result, err
	}
	return result, nil
}

// limitedBuffer keeps the first limit bytes and then discards.
type limitedBuffer struct {
	bytes.Buffer
	limit int
}

func (b *limitedBuffer) Write(payload []byte) (int, error) {
	remaining := b.limit - b.Len()
	if remaining <= 0 {
		return len(payload), nil
	}
	if len(payload) > remaining {
		if _, err := b.Buffer.Write(payload[:remaining]); err != nil {
			return 0, err
		}
		return len(payload), nil
	}
	if _, err := b.Buffer.Write(payload); err != nil {
		return 0, err
	}
	return len(payload), nil
}
