"""Real supervisor execution and finalize-worker lifecycle.

Reports describe process outcomes; committed Run facts are read separately.
"""
from __future__ import annotations

import json
import subprocess

from support import f00b, stack as st
from support.runtime_stack import Stack

PRODUCT_ROOT = st.PRODUCT_ROOT
SANDBOX_DISTRO = "Ubuntu-24.04"
SANDBOX_ROOTFS = "/opt/orchara-sandbox/python-rootfs"
SANDBOX_STATE_ROOT = "/var/tmp/orchara-sandbox"
_FINALIZE_JOB = "artifact_finalize"
_digest_cache: str | None = None


class FinalizeWorker:
    """A real `cmd/maintenance run --kinds artifact_finalize` process."""

    def __init__(self, stack: Stack) -> None:
        self.stack = stack
        self.process: subprocess.Popen[str] | None = None
        self.output = ""

    def __enter__(self) -> "FinalizeWorker":
        env = f00b.maintenance_env(
            self.stack, heartbeat_interval="50ms", extra={"ORCHARA_BACKGROUND_RETRY_BACKOFF": "50ms"}
        )
        self.process = subprocess.Popen(
            [
                str(self.stack.binaries["maintenance"]),
                "run",
                "--kinds",
                _FINALIZE_JOB,
                "--system-io-slots",
                "1",
                "--poll-interval",
                "50ms",
            ],
            cwd=PRODUCT_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        return self

    def __exit__(self, *_exc) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            stdout, _stderr = self.process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            stdout, _stderr = self.process.communicate(timeout=10)
        self.output = stdout or ""



def rootfs_digest() -> str:
    """The digest recorded by deploy/sandbox/build_python_rootfs.sh (guest read)."""
    global _digest_cache
    if _digest_cache is None:
        completed = subprocess.run(
            ["wsl", "-d", SANDBOX_DISTRO, "-u", "root", "-e", "bash", "-lc",
             f"cat {SANDBOX_ROOTFS}.digest"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        if completed.returncode != 0 or not completed.stdout.strip().startswith("sha256:"):
            raise RuntimeError(
                "the sandbox rootfs digest is unavailable; build it with "
                f"deploy/sandbox/build_python_rootfs.sh: {completed.stderr.strip()}"
            )
        _digest_cache = completed.stdout.strip()
    return _digest_cache



def supervisor_exec_env(
    stack: Stack,
    *,
    digest: str | None = None,
    timeout: str = "60s",
    heartbeat: str = "100ms",
    result_wait: str = "60s",
    poll: str = "50ms",
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    certificates = stack.certificates
    supervisor = certificates.client("supervisor")
    worker = certificates.client("worker")
    env = st.admin_env(stack.dsn)
    env.pop("ORCHARA_DB_DSN", None)
    env.update(
        {
            "ORCHARA_INTERNAL_URL": certificates.internal_url,
            "ORCHARA_INTERNAL_CA": str(certificates.ca.cert),
            "ORCHARA_SUPERVISOR_CLIENT_CERT": str(supervisor.cert),
            "ORCHARA_SUPERVISOR_CLIENT_KEY": str(supervisor.key),
            "ORCHARA_WORKER_CLIENT_CERT": str(worker.cert),
            "ORCHARA_WORKER_CLIENT_KEY": str(worker.key),
            "ORCHARA_SUPERVISOR_DSN": stack.dsn,
            "ORCHARA_BACKGROUND_HEARTBEAT_INTERVAL": heartbeat,
            "ORCHARA_EXEC_RESULT_WAIT": result_wait,
            "ORCHARA_EXEC_POLL_INTERVAL": poll,
            "ORCHARA_PRODUCT_ROOT": str(PRODUCT_ROOT),
            "ORCHARA_WSL_DISTRO": SANDBOX_DISTRO,
            "ORCHARA_SANDBOX_PYTHON_ROOTFS": SANDBOX_ROOTFS,
            "ORCHARA_SANDBOX_ROOTFS_DIGEST": digest if digest is not None else rootfs_digest(),
            "ORCHARA_SANDBOX_STATE_ROOT": SANDBOX_STATE_ROOT,
            "ORCHARA_SANDBOX_TIMEOUT": timeout,
        }
    )
    if extra:
        env.update(extra)
    return env



def run_supervisor_exec(
    stack: Stack,
    env: dict[str, str],
    *args: str,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(stack.binaries["supervisor"]), "exec", "--once", *args],
        cwd=PRODUCT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )



def exec_report(completed: subprocess.CompletedProcess[str]) -> dict:
    """The last JSON object stdout carried; the executor prints exactly one."""
    lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    assert lines, f"exec printed nothing; stderr={completed.stderr}"
    report = json.loads(lines[-1])
    assert isinstance(report, dict), report
    return report



def execute_once(stack, *, env_extra: dict[str, str] | None = None):
    """Run one claimed attempt with the real finalize worker and supervisor binary."""
    env = supervisor_exec_env(stack)
    if env_extra:
        env.update(env_extra)
    with FinalizeWorker(stack):
        completed = run_supervisor_exec(stack, env)
    return completed, exec_report(completed)

