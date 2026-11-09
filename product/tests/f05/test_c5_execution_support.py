"""Real process cleanup when acceptance orchestration fails."""

from dataclasses import replace

import pytest

from support.execution_support import FinalizeWorker


def test_finalize_worker_reaps_the_real_process_when_the_body_raises(t033_stack):
    worker = FinalizeWorker(t033_stack)
    with pytest.raises(RuntimeError, match="interrupted acceptance"):
        with worker:
            assert worker.process is not None
            assert worker.process.poll() is None
            raise RuntimeError("interrupted acceptance")
    assert worker.process is not None
    assert worker.process.poll() is not None


def test_finalize_worker_startup_failure_does_not_leave_a_process(t033_stack, tmp_path):
    broken = replace(
        t033_stack,
        binaries={**t033_stack.binaries, "maintenance": tmp_path / "missing-maintenance.exe"},
    )
    worker = FinalizeWorker(broken)
    with pytest.raises(FileNotFoundError):
        with worker:
            pytest.fail("a missing executable cannot enter the process scope")
    assert worker.process is None
