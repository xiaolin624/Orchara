"""F13B/T111 helpers: real WSL2/runsc facts, product-path driver and evidence.

T111 re-runs the build/script isolation attacks on the **formal Linux acceptance
host** accepted by ADR 0002: WSL2 Ubuntu-24.04 with gVisor ``runsc``. Nothing in
this module runs the attack code itself; it only drives the real product entry
points (``cmd/sandboxprobe`` and the T037 ``tool_build`` maintenance path) and
reads facts back from the guest.

The helpers deliberately return *facts*, not verdicts: the test file asserts the
verdicts, this module records the raw host/kernel/runsc/rootfs identity, the
cleanup proof (``runsc list``, per-sandbox pid/ps, state directories and tmpfs
mounts) and the evidence JSON written only when ``F13B_WRITE_EVIDENCE=1``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import time
from pathlib import Path

from support import stack as st
from support import t037

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = PRODUCT_ROOT / "reports" / "f13b" / "T111-isolation.json"

#: The complete attack set the acceptance evidence must contain. A run that
#: records only a subset (or records one attack with ``verdict="failed"``) can
#: never be reported as ``passed``: the check below is what keeps the committed
#: JSON an acceptance record instead of "whatever the last partial run wrote".
EXPECTED_CASE_IDS = (
    "run-files-credentials-mounts-privilege",
    "run-network-positive-control",
    "run-config-policy-refusal",
    "run-timeout-reclaim",
    "build-hostile-setup-hook",
    "build-fork-limit",
    "build-memory-limit",
    "build-disk-quota",
    "build-timeout-reclaim",
    "end-to-end-tool-run",
)

WSL_DISTRO = "Ubuntu-24.04"
SANDBOX_STATE_ROOT = "/var/tmp/orchara-sandbox"
SANDBOX_ROOTFS = "/opt/orchara-sandbox/python-rootfs"

#: The five variables the OCI bundle builder sets, plus the benign names a
#: minimal Linux environment may add. Anything else in the sandbox is a leak.
OCI_ENV_NAMES = frozenset(
    {
        "PATH",
        "PYTHONHOME",
        "PYTHONNOUSERSITE",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED",
    }
)
BENIGN_EXTRA_ENV = frozenset({"HOSTNAME", "HOME", "PWD", "TMPDIR", "LC_CTYPE", "LANG", "TZ"})
#: Name prefixes that would mean a host credential/deployment variable leaked in.
CREDENTIAL_ENV_PREFIXES = (
    "ORCHARA_",
    "PG",
    "DATABASE_URL",
    "DSN",
    "AWS_",
    "AZURE_",
    "GOOGLE_",
    "GITHUB_",
    "SSH_",
    "CONNECTOR_",
    "SIGNING_",
)

_HOST_FACTS: dict | None = None


# ---------------------------------------------------------------------------
# guest transport
# ---------------------------------------------------------------------------
def wsl(command: str, *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    """Run one bounded command in the real WSL2 guest as root."""
    return subprocess.run(
        ["wsl", "-d", WSL_DISTRO, "-u", "root", "-e", "bash", "-lc", command],
        cwd=str(PRODUCT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def wsl_text(command: str, *, timeout: int = 180) -> str:
    completed = wsl(command, timeout=timeout)
    assert completed.returncode == 0, (command, completed.stderr)
    return completed.stdout


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# host identity (WSL2 kernel + runsc + rootfs digest)
# ---------------------------------------------------------------------------
def host_facts(*, refresh: bool = False) -> dict:
    """The authoritative acceptance-host identity, read from the guest.

    ``host`` is deliberately spelled "WSL2 ... (not a standalone Linux server)"
    so no report can mistake it for a production Linux host (ADR 0002).
    """
    global _HOST_FACTS
    if _HOST_FACTS is not None and not refresh:
        return _HOST_FACTS
    version_lines = [line.strip() for line in wsl_text("runsc --version").splitlines() if line.strip()]
    meta = json.loads(wsl_text(f"cat {SANDBOX_ROOTFS}.meta.json"))
    facts = {
        "host": f"WSL2 {WSL_DISTRO} (accepted test host per ADR 0002; not a standalone Linux server)",
        "distro": WSL_DISTRO,
        "kernel": wsl_text("uname -sr").strip(),
        "runsc_version": version_lines[0] if version_lines else "",
        "runsc_spec": next((line for line in version_lines[1:] if line.lower().startswith("spec")), ""),
        "rootfs_path": SANDBOX_ROOTFS,
        "rootfs_digest": meta["digest"],
        "rootfs_file_count": meta.get("file_count"),
        "rootfs_size_kib": meta.get("size_kib"),
        "rootfs_python_sha256": meta.get("python_sha256"),
        "rootfs_builder": meta.get("builder"),
        "rootfs_kernel": meta.get("kernel"),
        "rootfs_runsc": meta.get("runsc"),
        "captured_at": utc_now(),
    }
    _HOST_FACTS = facts
    return facts


def sandbox_available() -> bool:
    completed = wsl("runsc --version", timeout=60)
    return completed.returncode == 0 and "runsc version" in completed.stdout


# ---------------------------------------------------------------------------
# host <-> guest paths
# ---------------------------------------------------------------------------
def guest_path(host_path: str | Path) -> str:
    """The path the WSL guest sees for a Windows host path.

    Mirrors ``internal/sandbox.GuestPath``; a host path the guest cannot see is
    an error rather than a silently unreadable decoy.
    """
    path = Path(host_path).resolve()
    drive = path.drive
    if not drive or len(drive) != 2 or drive[1] != ":":
        raise ValueError(f"not a Windows drive path: {host_path}")
    rest = str(path)[len(drive) :].replace("\\", "/").lstrip("/")
    return f"/mnt/{drive[0].lower()}/{rest}"


# ---------------------------------------------------------------------------
# cleanup truth (no exit code stands in for real reclamation)
# ---------------------------------------------------------------------------
def cleanup_snapshot(
    *,
    sandbox_id: str | None = None,
    state_root: str = SANDBOX_STATE_ROOT,
    extra_commands: dict[str, str] | None = None,
) -> dict:
    """One guest read of every reclamation fact.

    Sections are delimited so one WSL round-trip returns the independent truth:
    ``runsc list``, any runsc process, the state-root entries, the state-root
    tmpfs mounts and, when known, the per-sandbox pid/workspace.
    """
    root = state_root.rstrip("/")
    # Match the process *name* (comm) instead of its argv: the WSL wrapper shell's
    # own command line contains the string "runsc" and would otherwise match.
    runsc_ps = (
        "ps -eo pid,comm,args 2>/dev/null | "
        "awk '$2 == \"runsc\" || $2 == \"timeout\" {print}' || true"
    )
    lines = [
        "echo '@@@LIST'",
        "runsc --network=none list 2>/dev/null || true",
        "echo '@@@PS'",
        runsc_ps,
        "echo '@@@STATE'",
        f"ls -1 {root} 2>/dev/null || true",
        "echo '@@@STATE_EXISTS'",
        f"if [ -e {root} ]; then echo yes; else echo no; fi",
        "echo '@@@MOUNTS'",
        f"grep -E ' {root}(/| )' /proc/mounts 2>/dev/null || true",
        "echo '@@@END'",
    ]
    if sandbox_id:
        lines[6:6] = [
            "echo '@@@IDPS'",
            f'{runsc_ps} | grep -- {sandbox_id} || true',
            "echo '@@@IDDIR'",
            f"if [ -e {root}/{sandbox_id} ]; then echo yes; else echo no; fi",
        ]
    for name, command in (extra_commands or {}).items():
        lines.append(f"echo '@@@EXTRA:{name}'")
        lines.append(command)
    completed = wsl("\n".join(lines), timeout=120)
    assert completed.returncode == 0, completed.stderr
    sections: dict[str, list[str]] = {}
    current = ""
    for line in completed.stdout.splitlines():
        if line.startswith("@@@"):
            current = line[3:]
            sections.setdefault(current, [])
        elif current:
            sections[current].append(line)
    runsc_list_raw = [line.strip() for line in sections.get("LIST", []) if line.strip()]
    # ``runsc list`` always prints a header row; only rows are sandbox entries.
    runsc_list = [
        line
        for line in runsc_list_raw
        if not line.startswith("ID") and "STATUS" not in line
    ]
    runsc_processes = [line.strip() for line in sections.get("PS", []) if line.strip()]
    state_entries = [line.strip() for line in sections.get("STATE", []) if line.strip()]
    state_mounts = [line.strip() for line in sections.get("MOUNTS", []) if line.strip()]
    facts = {
        "runsc_list": runsc_list,
        "runsc_list_raw": runsc_list_raw,
        "runsc_processes": runsc_processes,
        "state_root_entries": state_entries,
        "state_root_exists": (sections.get("STATE_EXISTS") or [""])[0].strip() == "yes",
        "state_root_mounts": state_mounts,
        "list_empty": not runsc_list,
        "ps_empty": not runsc_processes,
        "dirs_empty": not state_entries,
        "mounts_empty": not state_mounts,
    }
    if sandbox_id:
        facts["sandbox_id_processes"] = [
            line.strip() for line in sections.get("IDPS", []) if line.strip()
        ]
        facts["sandbox_id_dir_exists"] = (sections.get("IDDIR") or [""])[0].strip() == "yes"
        facts["sandbox_id_clean"] = (
            not facts["sandbox_id_processes"] and not facts["sandbox_id_dir_exists"]
        )
    extras = {}
    for name in extra_commands or {}:
        extras[name] = [line.strip() for line in sections.get(f"EXTRA:{name}", []) if line.strip()]
    if extras:
        facts["extras"] = extras
    return facts


def host_staging_entries(root: str | Path) -> list[str]:
    """The host staging root is empty after every unit (no leaked workspace)."""
    directory = Path(root)
    if not directory.exists():
        return []
    return sorted(child.name for child in directory.iterdir())


def assert_reclaimed(cleanup: dict, baseline: dict, *, sandbox_id: str | None = None) -> None:
    """Assert a run added no sandbox entry, process, directory or tmpfs mount.

    The assertions are scoped to *this* run so concurrently running suites on a
    shared guest cannot make a correct reclaim look dirty: the run's own sandbox
    id must be absent from ``runsc list``, ``ps`` and ``/proc/mounts``, and it
    must add no sandbox/directory beyond the pre-run baseline. Global emptiness
    is recorded as evidence but only asserted when the baseline was empty.
    """
    before_ids = {line.split()[0] for line in (baseline.get("runsc_list") or []) if line.split()}
    after_ids = {line.split()[0] for line in (cleanup.get("runsc_list") or []) if line.split()}
    assert after_ids <= before_ids, {
        "new_sandboxes": sorted(after_ids - before_ids),
        "cleanup": cleanup,
    }
    before_dirs = set(baseline.get("state_root_entries") or [])
    after_dirs = set(cleanup.get("state_root_entries") or [])
    assert after_dirs <= before_dirs, {
        "new_state_entries": sorted(after_dirs - before_dirs),
        "cleanup": cleanup,
    }
    if sandbox_id:
        assert sandbox_id not in after_ids, cleanup
        assert cleanup["sandbox_id_clean"] is True, cleanup
        assert cleanup["sandbox_id_dir_exists"] is False, cleanup
        assert all(
            sandbox_id not in line for line in (cleanup.get("runsc_processes") or [])
        ), cleanup
        assert all(
            sandbox_id not in line for line in (cleanup.get("state_root_mounts") or [])
        ), cleanup
    if not before_ids:
        # The host was idle before the run: then no sandbox at all may remain.
        assert cleanup["list_empty"] is True, cleanup
        assert cleanup["ps_empty"] is True, cleanup
    if not before_dirs:
        assert cleanup["mounts_empty"] is True, cleanup


def filesystem_bytes(path: str = "/") -> dict:
    """Guest filesystem usage in bytes (disk-fill reclamation truth)."""
    output = wsl_text(f"df -PB1 {path} | tail -1")
    fields = output.split()
    return {"path": path, "used_bytes": int(fields[2]), "available_bytes": int(fields[3])}


# ---------------------------------------------------------------------------
# guest-side positive control for the network isolation claim
# ---------------------------------------------------------------------------
def guest_connect(port: int, *, timeout: float = 3.0) -> dict:
    """A guest (outside the sandbox) TCP connect attempt against loopback."""
    script = (
        "import socket\n"
        "sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        f"sock.settimeout({timeout})\n"
        "try:\n"
        f"    sock.connect(('127.0.0.1', {port}))\n"
        "    print('CONNECTED')\n"
        "except OSError as error:\n"
        "    print('DENIED ' + error.__class__.__name__)\n"
    )
    completed = wsl(f"python3 -c {quote_shell(script)}", timeout=60)
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "connected": "CONNECTED" in completed.stdout,
    }


def quote_shell(value: str) -> str:
    """Single-quote one shell word (the script is fixed test text)."""
    return "'" + value.replace("'", "'\\''") + "'"


def start_guest_listener(port: int = 18080, *, marker: str = "t111-listener") -> subprocess.Popen[str]:
    """A real TCP listener on the guest loopback, outside the sandbox.

    It proves the detection path works: the same port that refuses connections
    from inside the sandbox accepts one from the guest netns. The marker makes
    the cleanup deterministic even if the WSL client is killed.
    """
    script = (
        "# " + marker + "\n"
        "import socket, sys, time\n"
        "server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        f"server.bind(('127.0.0.1', {port}))\n"
        "server.listen(8)\n"
        "print('T111_LISTENER_READY', flush=True)\n"
        "deadline = time.time() + 300\n"
        "while time.time() < deadline:\n"
        "    server.settimeout(0.5)\n"
        "    try:\n"
        "        client, _ = server.accept()\n"
        "        client.sendall(b't111-ok')\n"
        "        client.close()\n"
        "    except socket.timeout:\n"
        "        continue\n"
        "    except OSError:\n"
        "        break\n"
    )
    return subprocess.Popen(
        ["wsl", "-d", WSL_DISTRO, "-u", "root", "-e", "python3", "-c", script],
        cwd=str(PRODUCT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
    )


def wait_for_guest_listener(port: int = 18080, *, timeout: float = 15.0) -> dict:
    """Poll until the guest listener answers; returns the successful control."""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = guest_connect(port)
        if last["connected"]:
            return last
        time.sleep(0.2)
    raise AssertionError(f"the guest listener on 127.0.0.1:{port} never came up: {last}")


def stop_guest_listener(process: subprocess.Popen[str] | None) -> None:
    """Kill the WSL client and the guest process by marker, then verify closed."""
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=10)
    try:
        wsl("pkill -f 't111-listener' || true", timeout=30)
    except Exception:  # noqa: BLE001 - cleanup must not mask the assertion
        pass


# ---------------------------------------------------------------------------
# cmd/sandboxprobe (the real product entry the operator uses)
# ---------------------------------------------------------------------------
def probe_env(
    *,
    workspace_root: str | Path | None = None,
    timeout: str = "60s",
    memory_mb: int | str | None = None,
    pids: int | str | None = None,
    cpu_seconds: int | str | None = None,
    quota_mb: int | str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """A controlled host environment for one sandbox-probe execution.

    Every inherited ``ORCHARA_*`` variable is dropped first so the only platform
    variables are the ones the probe needs; the credential attacks then add
    their decoys explicitly (see the test file).
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("ORCHARA_")
    }
    env.update(
        {
            "ORCHARA_PRODUCT_ROOT": str(PRODUCT_ROOT),
            "ORCHARA_WSL_DISTRO": WSL_DISTRO,
            "ORCHARA_SANDBOX_PYTHON_ROOTFS": SANDBOX_ROOTFS,
            "ORCHARA_SANDBOX_ROOTFS_DIGEST": host_facts()["rootfs_digest"],
            "ORCHARA_SANDBOX_STATE_ROOT": SANDBOX_STATE_ROOT,
            "ORCHARA_SANDBOX_TIMEOUT": timeout,
        }
    )
    if workspace_root is not None:
        env["ORCHARA_SANDBOX_WORKSPACE_ROOT"] = str(workspace_root)
    for name, value in (
        ("ORCHARA_SANDBOX_MEMORY_MB", memory_mb),
        ("ORCHARA_SANDBOX_PIDS", pids),
        ("ORCHARA_SANDBOX_CPU_SECONDS", cpu_seconds),
        ("ORCHARA_SANDBOX_WORKSPACE_QUOTA_MB", quota_mb),
    ):
        if value is not None:
            env[name] = str(value)
    if extra:
        env.update(extra)
    return env


def run_probe(
    binary: Path,
    env: dict[str, str],
    *,
    mode: str = "isolation",
    seconds: int | None = None,
    timeout: str | None = None,
    subprocess_timeout: int = 300,
) -> tuple[subprocess.CompletedProcess[str], dict]:
    """Run ``cmd/sandboxprobe`` once and parse its single JSON report."""
    argv = [str(binary), "--mode", mode]
    if seconds is not None:
        argv.extend(["--seconds", str(seconds)])
    if timeout is not None:
        argv.extend(["--timeout", timeout])
    completed = subprocess.run(
        argv,
        cwd=str(PRODUCT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=subprocess_timeout,
        stdin=subprocess.DEVNULL,
    )
    lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    report: dict = {}
    if lines:
        try:
            report = json.loads(lines[-1])
        except json.JSONDecodeError:
            report = {}
    return completed, report


# ---------------------------------------------------------------------------
# T037 build path with the documented sandbox limits
# ---------------------------------------------------------------------------
def build_env(
    stack,
    *,
    sandbox_timeout: str = "5m",
    workspace_root: str | Path | None = None,
    memory_mb: int | None = None,
    pids: int | None = None,
    cpu_seconds: int | None = None,
    quota_mb: int | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """The real maintenance environment plus explicit sandbox limits.

    ``ORCHARA_SANDBOX_*`` are the documented deployment knobs read by
    ``sandbox.OptionsFromEnv`` in the same process that runs the tool_build job;
    the limits therefore reach the real OCI bundle, not the untrusted hook.
    """
    env = t037.build_env(stack, timeout=sandbox_timeout)
    if workspace_root is not None:
        env["ORCHARA_SANDBOX_WORKSPACE_ROOT"] = str(workspace_root)
    for name, value in (
        ("ORCHARA_SANDBOX_MEMORY_MB", memory_mb),
        ("ORCHARA_SANDBOX_PIDS", pids),
        ("ORCHARA_SANDBOX_CPU_SECONDS", cpu_seconds),
        ("ORCHARA_SANDBOX_WORKSPACE_QUOTA_MB", quota_mb),
    ):
        if value is not None:
            env[name] = str(value)
    if extra:
        env.update(extra)
    return env


def hook_marker(text: str, marker: str) -> dict:
    """The last ``marker {json}`` line the untrusted hook printed into the log."""
    index = text.rfind(marker)
    assert index >= 0, f"{marker} missing from the build log: {text[-2000:]}"
    remainder = text[index + len(marker) :]
    line = remainder.splitlines()[0].strip()
    return json.loads(line)


def setup_hook_source(body: str) -> bytes:
    return t037.setup_py_source(body)


def run_build_once(
    stack,
    *,
    env: dict[str, str] | None = None,
    tool_build_timeout: str | None = None,
    subprocess_timeout: int = 600,
) -> tuple[subprocess.CompletedProcess[str], list[dict]]:
    """One real maintenance drain with an optional per-run build timeout.

    ``--tool-build-timeout`` bounds the *sandbox* for this one build (it becomes
    ``sandbox.Request.Timeout``); ``ORCHARA_SANDBOX_TIMEOUT`` alone would be
    overridden by the handler. Everything else is T037's real build path.
    """
    from support import f00b

    argv = t037.build_args()
    if tool_build_timeout is not None:
        index = argv.index("--tool-build-timeout")
        argv[index + 1] = tool_build_timeout
    completed = subprocess.run(
        [str(stack.binaries["maintenance"]), *argv],
        cwd=str(PRODUCT_ROOT),
        env=env or t037.build_env(stack),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=subprocess_timeout,
        stdin=subprocess.DEVNULL,
    )
    return completed, f00b.parse_json_lines(completed.stdout)


# ---------------------------------------------------------------------------
# evidence recorder
# ---------------------------------------------------------------------------
class Evidence:
    """Accumulate one record per attack and write the JSON on request only.

    ``F13B_WRITE_EVIDENCE=1`` is the same trigger every F13B report uses: a normal
    gate run must not rewrite the checked-in evidence file.
    """

    def __init__(self, *, branch: str | None = None, commit: str | None = None) -> None:
        self.cases: list[dict] = []
        self.blocked: list[dict] = []
        self.not_run: list[dict] = []
        self.branch = branch
        self.commit = commit
        self.environment = host_facts()

    def record(
        self,
        case_id: str,
        *,
        stage: str,
        attack: str,
        inputs: dict,
        expected: dict,
        observed: dict,
        verdict: str = "passed",
        blocked_at: str | None = None,
        cleanup: dict | None = None,
        commands: list[str] | None = None,
    ) -> dict:
        record = {
            "id": case_id,
            "stage": stage,
            "attack": attack,
            "verdict": verdict,
            "blocked_at": blocked_at,
            "recorded_at": utc_now(),
            "inputs": inputs,
            "expected": expected,
            "observed": observed,
            "cleanup": cleanup,
            "commands": list(commands or []),
            "host": {
                "host": self.environment["host"],
                "kernel": self.environment["kernel"],
                "runsc_version": self.environment["runsc_version"],
                "rootfs_digest": self.environment["rootfs_digest"],
            },
        }
        self.cases.append(record)
        return record

    def block(self, case_id: str, *, stage: str, attack: str, missing: str) -> None:
        self.blocked.append(
            {
                "id": case_id,
                "stage": stage,
                "attack": attack,
                "status": "blocked",
                "missing": missing,
                "recorded_at": utc_now(),
            }
        )

    def mark_not_run(self, item: str, *, why: str) -> None:
        self.not_run.append({"item": item, "why": why})

    def document(self) -> dict:
        produced = sorted(case["id"] for case in self.cases)
        missing = sorted(set(EXPECTED_CASE_IDS) - set(produced))
        unexpected = sorted(set(produced) - set(EXPECTED_CASE_IDS))
        failed = sorted(
            case["id"] for case in self.cases if case.get("verdict") != "passed"
        )
        return {
            "ticket": "T111",
            "issue": 112,
            "title": "re-run build and script isolation attacks on the formal Linux sandbox host",
            "status": self._status(),
            "branch": self.branch,
            "commit": self.commit,
            "generated_at": utc_now(),
            "environment": {
                "profile": "dev-stub",
                "note": (
                    "Every record below was executed inside WSL2 Ubuntu-24.04 with "
                    "gVisor runsc. Windows-local process results are not used as "
                    "runsc acceptance anywhere in this report (ADR 0002)."
                ),
                **self.environment,
            },
            "cases": self.cases,
            "blocked": self.blocked,
            "not_run": self.not_run,
            "expected_cases": list(EXPECTED_CASE_IDS),
            "produced_cases": produced,
            "missing_cases": missing,
            "unexpected_cases": unexpected,
            "failed_cases": failed,
            "complete": not missing and not unexpected and not self.blocked,
        }

    def _status(self) -> str:
        """Aggregate the case set; a partial or failed run is never ``passed``.

        ``passed`` requires every expected case exactly once, every verdict
        ``passed`` and no blocked item. A failed case makes the whole run
        ``failed``; a missing/unexpected/blocked case makes it ``partial``.
        """
        produced = [case["id"] for case in self.cases]
        missing = set(EXPECTED_CASE_IDS) - set(produced)
        unexpected = set(produced) - set(EXPECTED_CASE_IDS)
        duplicates = {case_id for case_id in produced if produced.count(case_id) > 1}
        failed = [case for case in self.cases if case.get("verdict") != "passed"]
        if failed:
            return "failed"
        if self.blocked or missing or unexpected or duplicates:
            return "partial"
        return "passed"

    def write_if_enabled(self) -> Path | None:
        """Write the evidence only when the run covers the complete case set.

        A teardown, a single-test run or a partially executed matrix must not
        overwrite the committed acceptance record with a subset. The incomplete
        document stays in memory (and is visible through ``document()``); only a
        complete run is persisted.
        """
        if os.environ.get("F13B_WRITE_EVIDENCE") != "1":
            return None
        document = self.document()
        if not document["complete"] or document["status"] != "passed":
            return None
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_PATH.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return EVIDENCE_PATH


def git_branch_and_commit() -> tuple[str | None, str | None]:
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=PRODUCT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PRODUCT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        return branch or None, commit or None
    except Exception:  # noqa: BLE001 - evidence only
        return None, None
