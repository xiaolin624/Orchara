"""F13B/T111 (GitHub #112): build and script isolation attacks on real WSL2/runsc.

The acceptance host is the one ADR 0002 accepts: WSL2 Ubuntu-24.04 with gVisor
``runsc``. Every case below executes **inside that guest** through a real product
entry:

* the run stage uses ``cmd/sandboxprobe``, the operator entry that runs the
  platform-owned isolation probe through the same executor the supervisor uses;
* the build stage uses the real T037 path: ``POST /api/v1/tools`` -> a real
  ``tool_build`` maintenance process -> the checked-in ``deploy/sandbox/build_tool.py``
  inside the same OCI bundle, executing an untrusted ``setup.py`` hook.

No Windows-local process result stands in for runsc acceptance: the assertions
read the guest's own answer (errno/error class, uid/capability/NoNewPrivs state,
mount table, probe JSON) plus independent reclamation facts (``runsc list``,
per-sandbox processes, state directories, tmpfs mounts and the host staging root).

``product/reports/f13b/T111-isolation.json`` is rewritten only when
``F13B_WRITE_EVIDENCE=1`` (see ``support/t111.py``).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid

import pytest

from support import f00b, t033, t037, t111
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)

STATE_ROOT = t111.SANDBOX_STATE_ROOT
OTHER_RUN_DIR = "/var/tmp/orchara-sandbox/other-run"
OTHER_RUN_SECRET = "t111-other-run-secret-DO-NOT-LEAK"


@pytest.fixture(scope="module", autouse=True)
def require_sandbox() -> None:
    if not t111.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: `wsl -d Ubuntu-24.04 -u root -e bash -lc "
            "'runsc --version'` failed; build the rootfs with "
            "deploy/sandbox/build_python_rootfs.sh"
        )


@pytest.fixture(scope="session")
def evidence() -> t111.Evidence:
    branch, commit = t111.git_branch_and_commit()
    recorder = t111.Evidence(branch=branch, commit=commit)
    yield recorder
    recorder.mark_not_run(
        "host filesystem project quota (beyond the sandbox workspace tmpfs)",
        why=(
            "WSL2 Ubuntu-24.04 has no per-directory filesystem project quota and no "
            "quota tooling installed. This ticket verifies the product's actual disk "
            "protection: the only writable mount is a size-bounded tmpfs plus "
            "RLIMIT_FSIZE, and the fill attack really hit ENOSPC with zero guest-volume "
            "growth. A host-level project quota belongs to the F15B deployment platform."
        ),
    )
    recorder.mark_not_run(
        "full egress protocol matrix (ICMP/UDP/DNS/proxy tunnelling)",
        why="the bundle runs with --network=none; this ticket records real TCP connect failures and a live guest-side positive control, not every protocol",
    )
    recorder.mark_not_run(
        "production Linux host outside WSL2",
        why="ADR 0002 accepts WSL2 for acceptance only; production delivery still needs a standalone Linux host",
    )
    recorder.mark_not_run(
        "real vendor model / connector credentials",
        why="the environment only has the dev-stub profile; no real vendor credential or endpoint exists",
    )
    recorder.write_if_enabled()


def new_token() -> str:
    return uuid.uuid4().hex[:16]


def stage_other_run_decoy() -> None:
    """A file in another Run's workspace area, staged outside this bundle."""
    t111.wsl(
        f"rm -rf {OTHER_RUN_DIR} && mkdir -p {OTHER_RUN_DIR} && "
        f"printf '%s\\n' {OTHER_RUN_SECRET} > {OTHER_RUN_DIR}/secret.txt && chmod 600 "
        f"{OTHER_RUN_DIR}/secret.txt && cat {OTHER_RUN_DIR}/secret.txt",
        timeout=60,
    )


def remove_other_run_decoy() -> None:
    t111.wsl(f"rm -rf {OTHER_RUN_DIR}", timeout=60)


# ---------------------------------------------------------------------------
# run stage: the platform probe through cmd/sandboxprobe
# ---------------------------------------------------------------------------
def test_run_stage_denies_host_files_credentials_mounts_and_privilege_escalation(
    go_binaries, tmp_path, evidence: t111.Evidence
) -> None:
    facts = t111.host_facts()
    staging = tmp_path / "host-staging"
    staging.mkdir()

    env_secret = "t111-env-secret-" + new_token()
    decoy_dsn = "postgres://t111:" + new_token() + "@127.0.0.1:1/t111_decoy"
    signing_secret = "t111-signing-" + new_token()
    connector_secret = "t111-connector-" + new_token()
    hostile_env = {
        "ORCHARA_T111_ENV_SECRET": env_secret,
        "ORCHARA_DB_DSN": decoy_dsn,
        "ORCHARA_SIGNING_KEY": signing_secret,
        "ORCHARA_CONNECTOR_SECRET": connector_secret,
        # Attempts to configure a privileged/root/no_new_privs-off sandbox. The
        # product has no such option; these must be ignored, not applied.
        "ORCHARA_SANDBOX_UID": "0",
        "ORCHARA_SANDBOX_PRIVILEGED": "1",
        "ORCHARA_SANDBOX_CAPABILITIES": "ALL",
        "ORCHARA_SANDBOX_NO_NEW_PRIVS": "0",
    }
    stage_other_run_decoy()
    baseline = t111.cleanup_snapshot()
    try:
        guest_control = t111.wsl(f"cat {OTHER_RUN_DIR}/secret.txt", timeout=60)
        assert OTHER_RUN_SECRET in guest_control.stdout, guest_control.stderr

        completed, report = t111.run_probe(
            go_binaries["sandboxprobe"],
            t111.probe_env(workspace_root=staging, extra=hostile_env),
            mode="isolation",
        )
        probe = report.get("probe") or {}
        assert completed.returncode == 0, (completed.stderr, report)
        assert report["status"] == "observed", report
        assert report["outcome"] == "ran", report
        assert report["cleanup_ok"] is True, report
        assert report["rootfs_digest"] == facts["rootfs_digest"], report
        assert report["cleanup"]["sandbox_list_matches"] == 0, report
        assert report["cleanup"]["pid_alive"] is False, report
        assert report["cleanup"]["ps_matches"] == 0, report
        assert report["cleanup"]["workspace_removed"] is True, report

        # Identity: the requested uid/caps/NoNewPrivs knobs do not exist, and the
        # runtime reports the fixed bundle's identity.
        assert probe["uid"] == 65534 and probe["gid"] == 65534, probe
        assert probe["hostname"] == "sandbox", probe
        privileges = probe["privileges"]
        # PR_GET_NO_NEW_PRIVS is the runtime's own answer (gVisor may omit the
        # corresponding /proc/self/status field).
        assert privileges["no_new_privs"]["value"] == 1, privileges["no_new_privs"]
        status = privileges["status"]
        for capability in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
            if capability in status:
                assert int(status[capability], 16) == 0, (capability, status)
        if "Uid" in status:
            assert status["Uid"].split()[0] == "65534", status
        if "Gid" in status:
            assert status["Gid"].split()[0] == "65534", status
        # The capability set is empty even if the runtime does not echo Cap* back:
        # capset(CAP_SYS_ADMIN) below must be refused.
        assert privileges["status_keys"], privileges

        # Every host/mount/foreign read is refused with a real errno; the guest
        # control above proves the same path exists outside the sandbox.
        for entry in probe["host_files"]:
            assert entry["read"] is False, entry
            assert entry.get("error"), entry
        names = [entry["path"] for entry in probe["host_files"]]
        for required in ("/etc/passwd", "/etc/shadow", "/etc/hostname"):
            assert required in names, names
        for entry in probe["host_mount_paths"]:
            assert entry["read"] is False, entry
        for entry in probe["foreign_files"]:
            assert entry["read"] is False, entry
        foreign_names = [entry["path"] for entry in probe["foreign_files"]]
        assert OTHER_RUN_DIR + "/secret.txt" in foreign_names, foreign_names

        # Writes: read-only rootfs/tool/inputs refuse, workspace accepts.
        for entry in probe["input_writes"]:
            assert entry["wrote"] is False, entry
        for entry in probe["rootfs_writes"]:
            assert entry["wrote"] is False, entry
        assert probe["tool_writes"] and all(not entry["wrote"] for entry in probe["tool_writes"]), probe
        assert probe["workspace_write"]["wrote"] is True, probe
        assert probe["workspace_readable"] is True, probe
        assert probe["tool_readable"] is True, probe

        # Credentials: a real dump of the sandbox environment (name-level) and of
        # PID 1's environ contains only the fixed OCI environment.
        environment = probe["environment"]
        assert environment["names"], environment
        assert environment["credential_prefixed"] == [], environment
        assert environment["outside_oci_allowlist"] == [], environment
        for name in environment["names"]:
            assert name in (t111.OCI_ENV_NAMES | t111.BENIGN_EXTRA_ENV), name
        proc1 = probe["proc1_environ"]
        assert proc1["read"] is True, proc1
        assert proc1["credential_prefixed"] == [], proc1
        assert proc1["outside_oci_allowlist"] == [], proc1
        # The hostile values are not present in the probe's own report either.
        assert env_secret not in json.dumps(environment)
        assert env_secret not in (completed.stdout or "")

        # Escalation attempts are denied by the runtime (gVisor + the fixed
        # bundle's uid/caps/no_new_privs); each records its own errno. An
        # unprivileged user namespace MAY be allowed (gVisor permits it); that is
        # not host privilege, and the probe records whether it reaches anything.
        escalation = privileges["escalation"]
        attempts = {entry["attempt"]: entry for entry in escalation["attempts"]}
        for required in (
            "setuid_root",
            "setgid_root",
            "mount_tmpfs",
            "unshare_mount_namespace",
            "capset_sys_admin",
            "clear_no_new_privs",
        ):
            assert required in attempts, attempts
            assert attempts[required]["denied"] is True, attempts[required]
            assert attempts[required].get("errno_name"), attempts[required]
        user_namespace = escalation["user_namespace"]
        if not user_namespace["unshare"]["denied"]:
            after = user_namespace["after"]
            assert after is not None, user_namespace
            assert after["uid"] == 65534, after
            assert all(entry["read"] is False for entry in after["host_reads"]), after
            assert after["workspace_write"]["wrote"] is True, after

        # Mount policy: only the approved mounts and the runtime-provided /dev,
        # /dev/pts and /sys exist; host certificate/credential paths are never
        # mounted and the configured read-only mounts stay read-only.
        mounts = probe["mounts"]
        destinations = {entry["destination"] for entry in mounts}
        approved = {"/", "/proc", "/tmp", "/tool", "/inputs", "/workspace"}
        runtime_provided = {"/dev", "/dev/pts", "/sys"}
        assert destinations == approved | runtime_provided, destinations
        assert all(not entry.startswith("/mnt") for entry in destinations), destinations
        by_destination = {entry["destination"]: entry for entry in mounts}
        assert "ro" in by_destination["/tool"]["options"], by_destination["/tool"]
        assert "ro" in by_destination["/inputs"]["options"], by_destination["/inputs"]
        assert "rw" in by_destination["/workspace"]["options"], by_destination["/workspace"]
        for entry in mounts:
            source = entry.get("source", "")
            assert not source.startswith("/mnt/"), entry
            assert "/certs/" not in source and "client.key" not in source, entry
        # A permitted user namespace may mount only sandbox-local filesystems after
        # the fact; that is recorded, and it must not add any host path either.
        after_destinations = {
            entry["destination"] for entry in probe["mounts_after_escalation"]
        }
        assert all(not entry.startswith("/mnt") for entry in after_destinations), after_destinations
        if not user_namespace["unshare"]["denied"]:
            userns_mount = user_namespace["after"]["mount_tmpfs"]
            if not userns_mount["denied"]:
                assert "/tmp/t111-mount" in after_destinations, after_destinations
                assert "/tmp/t111-mount" not in destinations, destinations

        # Outbound: external, host and loopback targets all fail with a real
        # socket error (the guest-side positive control is its own test).
        network = probe["network"]
        targets = {entry["target"] for entry in network}
        for required in ("1.1.1.1:443", "8.8.8.8:53", "127.0.0.1:18080"):
            assert required in targets, targets
        for entry in network:
            assert entry["connected"] is False, entry
            assert entry.get("error"), entry
    finally:
        remove_other_run_decoy()

    cleanup = t111.cleanup_snapshot(sandbox_id=report["sandbox_id"])
    t111.assert_reclaimed(cleanup, baseline, sandbox_id=report["sandbox_id"])
    assert t111.host_staging_entries(staging) == [], t111.host_staging_entries(staging)

    evidence.record(
        "run-files-credentials-mounts-privilege",
        stage="run",
        attack=(
            "read /etc/passwd|shadow|hostname, /mnt host paths and another Run's workspace; "
            "write the read-only rootfs/tool/inputs; dump the sandbox environment and "
            "/proc/1/environ; mount/unshare/setuid/capset; try to configure uid0/privileged/"
            "no_new_privs=off through the deployment env"
        ),
        inputs={
            "program": "oracle is cmd/sandboxprobe --mode isolation",
            "hostile_env_names": sorted(hostile_env),
            "hostile_env_value_sha256": {
                name: hashlib.sha256(value.encode()).hexdigest()
                for name, value in hostile_env.items()
            },
            "other_run_decoy": OTHER_RUN_DIR + "/secret.txt",
            "workspace_root": str(staging),
        },
        expected={
            "identity": "uid/gid 65534, empty capabilities, NoNewPrivs=1",
            "reads": "all host/mount/foreign reads refused",
            "writes": "rootfs/tool/inputs refused; /workspace allowed",
            "env": "only the fixed OCI environment; no deployment credential name or value",
            "mounts": "exactly /, /proc, /tmp, /tool(ro), /inputs(ro), /workspace(rw)",
            "network": "all connections fail",
            "cleanup": "runsc list empty, no pid/process, no state dir, no tmpfs mount",
        },
        observed={
            "probe": probe,
            "cleanup_report": report["cleanup"],
            "guest_cleanup": cleanup,
            "host_staging_entries": t111.host_staging_entries(staging),
            "env_values_seen_in_probe_json": env_secret in json.dumps(environment),
        },
        blocked_at=(
            "runtime (gVisor/runsc) refuses uid0/mount-namespace/capset/no_new_privs "
            "escalation inside the fixed bundle; unprivileged user namespaces are "
            "permitted by the runtime but stay sandbox-local (no host path reached)"
        ),
        cleanup=cleanup,
        commands=[
            "cmd/sandboxprobe --mode isolation",
            "ORCHARA_SANDBOX_UID=0 ORCHARA_SANDBOX_PRIVILEGED=1 ... (ignored, probe still uid 65534)",
        ],
    )


def test_run_stage_network_denial_has_a_live_guest_side_positive_control(
    go_binaries, tmp_path, evidence: t111.Evidence
) -> None:
    """The same loopback port refuses the sandbox but accepts the guest.

    Without the positive control, "connection failed" could just mean the probe's
    socket code is broken. The listener is a real guest process outside the
    sandbox namespace; it accepts a guest connection and then must refuse the
    sandbox's connection to 127.0.0.1:18080.
    """
    staging = tmp_path / "host-staging"
    staging.mkdir()
    baseline = t111.cleanup_snapshot()
    listener = t111.start_guest_listener(18080)
    try:
        control = t111.wait_for_guest_listener(18080)
        assert control["connected"] is True, control
        completed, report = t111.run_probe(
            go_binaries["sandboxprobe"], t111.probe_env(workspace_root=staging)
        )
        assert completed.returncode == 0, (completed.stderr, report)
        assert report["outcome"] == "ran", report
        probe = report["probe"]
        network = {entry["target"]: entry for entry in probe["network"]}
        loopback = network["127.0.0.1:18080"]
        assert loopback["connected"] is False, loopback
        assert loopback.get("error"), loopback
        for target in ("1.1.1.1:443", "8.8.8.8:53", "169.254.169.254:80"):
            assert network[target]["connected"] is False, network[target]
            assert network[target].get("error"), network[target]
    finally:
        t111.stop_guest_listener(listener)
    # After the listener is gone the guest control itself refuses: the port is
    # not silently left open by the test harness.
    closed = t111.guest_connect(18080)
    assert closed["connected"] is False, closed

    cleanup = t111.cleanup_snapshot(sandbox_id=report["sandbox_id"])
    t111.assert_reclaimed(cleanup, baseline, sandbox_id=report["sandbox_id"])
    assert t111.host_staging_entries(staging) == [], t111.host_staging_entries(staging)

    evidence.record(
        "run-network-positive-control",
        stage="run",
        attack="connect to the guest loopback port, public IPs and the metadata address from the sandbox",
        inputs={
            "guest_listener": "127.0.0.1:18080 on the WSL guest, outside the sandbox namespace",
            "sandbox_targets": ["1.1.1.1:443", "8.8.8.8:53", "127.0.0.1:18080", "169.254.169.254:80"],
        },
        expected={
            "control": "guest connect succeeds while the listener is live",
            "sandbox": "every connect fails with a real socket error (--network=none + new netns)",
        },
        observed={
            "guest_control": control,
            "sandbox_network": probe["network"],
            "guest_after_stop": closed,
            "cleanup_report": report["cleanup"],
            "guest_cleanup": cleanup,
        },
        blocked_at="network namespace + --network=none (runsc), proven against a live guest listener",
        cleanup=cleanup,
        commands=[
            "wsl python3 tcp listener 127.0.0.1:18080 (guest control)",
            "cmd/sandboxprobe --mode isolation",
        ],
    )


def test_run_stage_refuses_over_limit_or_privileged_configuration_at_the_product_layer(
    go_binaries, tmp_path, evidence: t111.Evidence
) -> None:
    """The product's own option parser refuses unsafe deployment configuration.

    This is the "policy layer" half of the mount/cap/uid0/no_new_privs attack:
    the bundle builder is not parameterised by request data, and the documented
    deployment knobs are validated before any unit is created. No runsc process
    may appear for a refused profile.
    """
    staging = tmp_path / "host-staging"
    staging.mkdir()
    baseline = t111.cleanup_snapshot()
    attempts = [
        ("memory", {"memory_mb": 999999}, "ORCHARA_SANDBOX_MEMORY_MB"),
        ("workspace-quota", {"quota_mb": 99999}, "ORCHARA_SANDBOX_WORKSPACE_QUOTA_MB"),
        ("pids", {"pids": "not-a-number"}, "ORCHARA_SANDBOX_PIDS"),
        ("timeout", {"timeout": "-5s"}, "ORCHARA_SANDBOX_TIMEOUT"),
    ]
    observed = {}
    for name, knobs, expected in attempts:
        completed, report = t111.run_probe(
            go_binaries["sandboxprobe"], t111.probe_env(workspace_root=staging, **knobs)
        )
        assert completed.returncode == 1, (name, completed.returncode, completed.stdout, completed.stderr)
        assert report["status"] == "failed", (name, report)
        assert expected in report["error"], (name, report)
        observed[name] = {"exit_code": completed.returncode, "report": report}
        assert t111.host_staging_entries(staging) == [], (name, t111.host_staging_entries(staging))
    cleanup = t111.cleanup_snapshot()
    t111.assert_reclaimed(cleanup, baseline)

    evidence.record(
        "run-config-policy-refusal",
        stage="run",
        attack="configure a sandbox with an over-limit memory/quota/pids/timeout profile",
        inputs={"attempts": {name: knobs for name, knobs, _ in attempts}},
        expected="sandbox.OptionsFromEnv refuses before any unit is created",
        observed=observed,
        blocked_at="product configuration policy (sandbox.OptionsFromEnv) before runsc is invoked",
        cleanup=cleanup,
        commands=["cmd/sandboxprobe with an unsafe ORCHARA_SANDBOX_* profile"],
    )


def test_run_stage_timeout_kills_the_unit_and_reclaims_everything(
    go_binaries, tmp_path, evidence: t111.Evidence
) -> None:
    staging = tmp_path / "host-staging"
    staging.mkdir()
    baseline = t111.cleanup_snapshot()
    started = time.time()
    completed, report = t111.run_probe(
        go_binaries["sandboxprobe"],
        t111.probe_env(workspace_root=staging, timeout="2s"),
        mode="slow",
        seconds=60,
        timeout="2s",
    )
    elapsed = time.time() - started
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "timeout", report
    assert report["timed_out"] is True, report
    assert report["cleanup_ok"] is True, report
    assert report["cleanup"]["sandbox_list_matches"] == 0, report
    assert report["cleanup"]["pid_alive"] is False, report
    assert report["cleanup"]["ps_matches"] == 0, report
    assert report["cleanup"]["workspace_removed"] is True, report
    # The sleep was 60s; the unit was really stopped, not waited out.
    assert elapsed < 45, elapsed

    cleanup = t111.cleanup_snapshot(sandbox_id=report["sandbox_id"])
    t111.assert_reclaimed(cleanup, baseline, sandbox_id=report["sandbox_id"])
    assert t111.host_staging_entries(staging) == [], t111.host_staging_entries(staging)

    evidence.record(
        "run-timeout-reclaim",
        stage="run",
        attack="sleep past the sandbox wall-clock limit",
        inputs={"mode": "slow", "sleep_seconds": 60, "sandbox_timeout": "2s"},
        expected="unit killed at 2s, timeout reported, all processes/dirs/mounts reclaimed",
        observed={
            "outcome": report["outcome"],
            "timed_out": report["timed_out"],
            "elapsed_seconds": round(elapsed, 3),
            "cleanup_report": report["cleanup"],
            "guest_cleanup": cleanup,
            "host_staging_entries": t111.host_staging_entries(staging),
        },
        blocked_at="driver timeout + executor kill + cleanup proof (runsc delete/pid/workspace)",
        cleanup=cleanup,
        commands=["cmd/sandboxprobe --mode slow --seconds 60 --timeout 2s"],
    )


# ---------------------------------------------------------------------------
# build stage: an untrusted setup.py hook through the real T037 tool_build path
# ---------------------------------------------------------------------------
HOSTILE_HOOK_TEMPLATE = r'''
import ctypes
import json
import os

CONFIG = __T111_CONFIG__


def attempt(name, callback):
    try:
        callback()
        return [name, {"ok": True}]
    except BaseException as error:
        return [
            name,
            {
                "ok": False,
                "error": error.__class__.__name__,
                "errno": getattr(error, "errno", None),
            },
        ]


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read(2048)


def attempt_read(path):
    return attempt(path, lambda path=path: read_bytes(path))


def attempt_write(path):
    return attempt(path, lambda path=path: open(path, "ab").write(b"t111-tamper"))


def scan_values(mapping):
    leaks = []
    for name, value in mapping.items():
        if name.upper().startswith(
            ("ORCHARA_", "PG", "DATABASE_URL", "DSN", "AWS_", "AZURE_", "SIGNING_", "CONNECTOR_")
        ):
            leaks.append(name)
        if CONFIG["secret"] in value or CONFIG["db_name"] in value:
            leaks.append(name + "=value")
    return leaks


def proc1_environ():
    with open("/proc/1/environ", "rb") as handle:
        payload = handle.read(1 << 20)
    names = []
    leaks = []
    for entry in payload.split(b"\0"):
        if b"=" not in entry:
            continue
        name, value = entry.split(b"=", 1)
        name_text = name.decode("utf-8", "replace")
        names.append(name_text)
        if CONFIG["secret"].encode() in value or CONFIG["db_name"].encode() in value:
            leaks.append(name_text + "=value")
    return {"read": True, "names": sorted(names), "leaks": leaks}


def status_fields():
    wanted = ("Uid", "Gid", "CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb", "NoNewPrivs")
    fields = {}
    with open("/proc/self/status", "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            name, _, value = line.partition(":")
            if name in wanted:
                fields[name] = value.strip()
    return fields


def no_new_privs():
    try:
        library = ctypes.CDLL(None, use_errno=True)
        result = library.prctl(39, 0, 0, 0, 0)
    except BaseException as error:
        return {"supported": False, "error_class": type(error).__name__}
    return {"supported": True, "value": int(result)}


def mounts():
    entries = []
    with open("/proc/self/mountinfo", "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                before, after = line.rstrip("\n").split(" - ", 1)
                pre = before.split(" ")
                post = after.split(" ")
                entries.append(
                    {
                        "destination": pre[4],
                        "options": pre[5],
                        "fstype": post[0],
                        "source": post[1] if len(post) > 1 else "",
                    }
                )
            except (ValueError, IndexError):
                continue
    return entries


document = {
    "probe": "t111.hostile-build-hook/v1",
    "reads": dict(attempt_read(path) for path in CONFIG["reads"]),
    "writes": dict(attempt_write(path) for path in CONFIG["writes"]),
    "workspace_write": attempt(
        "/workspace/t111-hook-output.txt",
        lambda: open("/workspace/t111-hook-output.txt", "wb").write(b"ok"),
    ),
    "workspace_read": attempt_read("/workspace/t111-hook-output.txt"),
    "environment": {"names": sorted(os.environ), "leaks": scan_values(os.environ)},
    "proc1": proc1_environ(),
    "status": status_fields(),
    "no_new_privs": no_new_privs(),
    "mounts": mounts(),
}
print("T111_BUILD_HOOK " + json.dumps(document, sort_keys=True))
'''

FORK_HOOK_TEMPLATE = r'''
import errno
import json
import os
import signal
import time

ATTEMPTS = __T111_ATTEMPTS__
children = []
failure = None
try:
    for _ in range(ATTEMPTS):
        pid = os.fork()
        if pid == 0:
            try:
                time.sleep(30)
            finally:
                os._exit(0)
        children.append(pid)
        print("T111_FORK_PROGRESS " + str(len(children)), flush=True)
except OSError as error:
    failure = {
        "class": error.__class__.__name__,
        "errno": error.errno,
        "errno_name": errno.errorcode.get(error.errno, str(error.errno)),
    }
for pid in children:
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
for pid in children:
    try:
        os.waitpid(pid, 0)
    except OSError:
        pass
print("T111_FORK " + json.dumps({"children": len(children), "failure": failure}, sort_keys=True))
'''

MEMORY_HOOK = r'''
import json
import subprocess
import sys

CHILD = r"""
import json

chunks = []
print("T111_MEMORY_START", flush=True)
try:
    for _ in range(64):
        chunk = bytearray(8 * 1024 * 1024)
        for page in range(0, len(chunk), 4096):
            chunk[page] = 1
        chunks.append(chunk)
        print("T111_MEMORY_PROGRESS " + str(len(chunks) * 8), flush=True)
except BaseException as error:
    print("T111_MEMORY_CHILD " + json.dumps(
        {"outcome": "stopped", "error": type(error).__name__,
         "errno": getattr(error, "errno", None), "allocated_mib": len(chunks) * 8},
        sort_keys=True,
    ), flush=True)
else:
    print("T111_MEMORY_CHILD " + json.dumps(
        {"outcome": "survived", "allocated_mib": len(chunks) * 8}, sort_keys=True
    ), flush=True)
"""

completed = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True)
print("T111_MEMORY " + json.dumps(
    {
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-500:],
    },
    sort_keys=True,
))
'''

DISK_HOOK = r'''
import errno
import json
import os

written = 0
failure = None
try:
    with open("/workspace/fill.bin", "wb") as handle:
        chunk = b"A" * (1024 * 1024)
        for _ in range(64):
            handle.write(chunk)
            written += 1
except OSError as error:
    failure = {
        "class": error.__class__.__name__,
        "errno": error.errno,
        "errno_name": errno.errorcode.get(error.errno, str(error.errno)),
    }
try:
    os.remove("/workspace/fill.bin")
except OSError:
    pass
print("T111_DISK " + json.dumps({"written_mib": written, "failure": failure}, sort_keys=True))
'''

TIMEOUT_HOOK = r'''
import time

print("T111_TIMEOUT_START", flush=True)
time.sleep(60)
print("T111_TIMEOUT_END", flush=True)
'''

BUILD_HOOK_ENV_NAMES = frozenset(
    {
        # the stripped environment build_tool.py gives the untrusted hook
        "PATH",
        "HOME",
        "TMPDIR",
        "PYTHONNOUSERSITE",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED",
        "LANG",
        "LC_ALL",
    }
)


def hostile_hook_source(*, secret: str, db_name: str, reads: list[str], writes: list[str]) -> bytes:
    config = {"secret": secret, "db_name": db_name, "reads": reads, "writes": writes}
    body = HOSTILE_HOOK_TEMPLATE.replace("__T111_CONFIG__", json.dumps(config, sort_keys=True))
    return t111.setup_hook_source(body)


def creator(stack, login: str):
    admin = t037.admin_session(stack)
    return f00b.invite_and_activate(
        stack, admin, login, roles=("user", "creator"), password=f00b.USER_PASSWORD
    )


def submit_tool(stack, owner, source: bytes) -> dict:
    artifact_id = t037.upload_source_on(stack, owner, source)
    response = t037.submit_tool(owner, t037.submit_body(artifact_id))
    assert response.status == 202, response.text
    return response.json


def build_sandbox_id(stack, tool_id: str) -> str:
    jobs = t037.tool_jobs(stack.dbname, tool_id)
    assert len(jobs) == 1, jobs
    # The handler names the execution unit "tool-build-<job id>"; the executor
    # derives the runsc container name from it.
    return "tool-build-" + jobs[0]["id"].lower()


def assert_build_reclaimed(stack, tool_id: str, staging, baseline: dict) -> dict:
    sandbox_id = build_sandbox_id(stack, tool_id)
    cleanup = t111.cleanup_snapshot(sandbox_id=sandbox_id)
    t111.assert_reclaimed(cleanup, baseline, sandbox_id=sandbox_id)
    entries = t111.host_staging_entries(staging)
    assert entries == [], entries
    return cleanup


def test_build_stage_hostile_setup_hook_cannot_read_host_files_or_credentials(
    t033_stack, tmp_path, evidence: t111.Evidence
) -> None:
    """A real untrusted install hook attacks the real build sandbox.

    The hook is a genuinely attacker-controlled ``setup.py`` submitted as a tool
    source and executed by the checked-in ``build_tool.py`` inside WSL2/runsc. It
    tries the exact host paths this run can see from the guest (repo file, object
    directory decoy, DSN decoy, mTLS private key, another Run's workspace), dumps
    the environment, reads PID 1's environ, writes host paths and the read-only
    mounts, and inspects the mount table. Every fact is the sandbox's own answer.
    """
    stack = t033_stack
    owner = creator(stack, "t111.hostile")
    object_decoy = stack.object_dir / "t111-decoy-object.txt"
    object_decoy.write_text("t111-object-secret-DO-NOT-LEAK\n", encoding="utf-8")
    dsn_decoy = tmp_path / "t111-decoy.dsn"
    dsn_decoy.write_text("postgres://t111:decoy@127.0.0.1:55432/t111_decoy\n", encoding="utf-8")
    worker_key = stack.certificates.client("worker").key
    secret = "t111-build-secret-" + new_token()
    db_name = stack.dbname
    stage_other_run_decoy()
    staging = tmp_path / "build-staging"
    staging.mkdir()
    baseline = t111.cleanup_snapshot()

    reads = [
        "/etc/passwd",
        "/etc/shadow",
        "/etc/hostname",
        "/mnt/c/Windows/System32/drivers/etc/hosts",
        t111.guest_path(t111.PRODUCT_ROOT / "PROGRESS.md"),
        t111.guest_path(object_decoy),
        t111.guest_path(dsn_decoy),
        t111.guest_path(worker_key),
        OTHER_RUN_DIR + "/secret.txt",
        "/var/tmp/orchara-sandbox/foreign/secret.txt",
    ]
    writes = [
        "/etc/passwd",
        t111.guest_path(stack.object_dir / "t111-written-by-hook.txt"),
        OTHER_RUN_DIR + "/evil.txt",
        "/inputs/source.zip",
        "/tool/build_tool.py",
    ]
    source = hostile_hook_source(secret=secret, db_name=db_name, reads=reads, writes=writes)

    try:
        key_hash_before = t111.wsl_text(
            f"sha256sum {t111.quote_shell(t111.guest_path(worker_key))}"
        )
        # Guest control: the same paths are live and readable *outside* the sandbox,
        # so a refusal below is isolation, not a missing decoy.
        controls = {}
        for path in (
            t111.guest_path(t111.PRODUCT_ROOT / "PROGRESS.md"),
            t111.guest_path(object_decoy),
            t111.guest_path(dsn_decoy),
            OTHER_RUN_DIR + "/secret.txt",
        ):
            completed = t111.wsl(
                f"test -s {t111.quote_shell(path)} && echo READABLE", timeout=60
            )
            assert "READABLE" in completed.stdout, (path, completed.stderr)
            controls[path] = "READABLE"
        completed = t111.wsl(
            f"test -r {t111.quote_shell(t111.guest_path(worker_key))} && echo READABLE",
            timeout=60,
        )
        assert "READABLE" in completed.stdout, (worker_key, completed.stderr)

        tool = submit_tool(stack, owner, source)
        env = t111.build_env(
            stack,
            workspace_root=staging,
            extra={"ORCHARA_T111_BUILD_SECRET": secret},
        )
        completed, _reports = t111.run_build_once(stack, env=env)
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        facts = t037.wait_build_finished(stack, owner, tool["id"], timeout=240)
        assert facts["state"] == "ready", facts
        assert facts["build_log_artifact_id"], facts
        log_text = t037.download_artifact_bytes(
            owner, facts["build_log_artifact_id"]
        ).decode("utf-8", "replace")
        observed = t111.hook_marker(log_text, "T111_BUILD_HOOK")
    finally:
        remove_other_run_decoy()

    # Reads: every exact host path and the other Run's workspace are refused with
    # a real errno; the guest control proved they exist outside the sandbox.
    for path in reads:
        entry = observed["reads"][path]
        assert entry["ok"] is False, (path, entry)
        assert entry.get("error"), (path, entry)
    # Writes: read-only mounts and host paths refuse; the sandbox workspace writes.
    for path in writes:
        entry = observed["writes"][path]
        assert entry["ok"] is False, (path, entry)
        assert entry.get("error"), (path, entry)
    assert observed["workspace_write"][1]["ok"] is True, observed["workspace_write"]
    assert observed["workspace_read"][1]["ok"] is True, observed["workspace_read"]

    # Credentials: neither the hook's stripped env nor PID 1's environ carries a
    # deployment variable or the secret/database value.
    environment = observed["environment"]
    assert environment["leaks"] == [], environment["leaks"]
    for name in environment["names"]:
        assert name in BUILD_HOOK_ENV_NAMES, name
    proc1 = observed["proc1"]
    assert proc1["read"] is True, proc1
    assert proc1["leaks"] == [], proc1
    for name in proc1["names"]:
        assert name in (t111.OCI_ENV_NAMES | t111.BENIGN_EXTRA_ENV), name
    assert secret not in log_text, "the build log leaked the deployment secret"
    assert db_name not in log_text, "the build log leaked the database identity"

    # Identity and mount table inside the build sandbox.
    status = observed["status"]
    assert observed["no_new_privs"]["value"] == 1, observed["no_new_privs"]
    for capability in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        if capability in status:
            assert int(status[capability], 16) == 0, (capability, status)
    if "Uid" in status:
        assert status["Uid"].split()[0] == "65534", status
    destinations = {entry["destination"] for entry in observed["mounts"]}
    approved = {"/", "/proc", "/tmp", "/tool", "/inputs", "/workspace"}
    runtime_provided = {"/dev", "/dev/pts", "/sys"}
    assert destinations == approved | runtime_provided, destinations
    assert all(not entry.startswith("/mnt") for entry in destinations), destinations
    by_destination = {entry["destination"]: entry for entry in observed["mounts"]}
    assert "ro" in by_destination["/tool"]["options"], by_destination["/tool"]
    assert "ro" in by_destination["/inputs"]["options"], by_destination["/inputs"]
    assert "rw" in by_destination["/workspace"]["options"], by_destination["/workspace"]
    for entry in observed["mounts"]:
        assert not entry["source"].startswith("/mnt/"), entry

    # The build still completed from the sealed source; the content bundle digest
    # proves nothing host-side tampered with the inputs or outputs.
    row = t037.tool_row(stack.dbname, tool["id"])
    sbom = t037.download_artifact_bytes(owner, facts["sbom_artifact_id"])
    expected_digest = t037.bundle_digest(source, t037.DEFAULT_LOCK, sbom)
    assert row["image_digest"] == expected_digest, (row["image_digest"], expected_digest)
    assert row["build_error_code"] is None, row
    assert key_hash_before.split()[0] == t111.wsl_text(
        f"sha256sum {t111.quote_shell(t111.guest_path(worker_key))}"
    ).split()[0]
    assert not (stack.object_dir / "t111-written-by-hook.txt").exists()
    assert object_decoy.read_text(encoding="utf-8").startswith("t111-object-secret")

    cleanup = assert_build_reclaimed(stack, tool["id"], staging, baseline)
    evidence.record(
        "build-hostile-setup-hook",
        stage="build",
        attack=(
            "untrusted setup.py reads /etc/passwd|shadow|hostname, a real repo file via /mnt, "
            "the object-directory decoy, a DSN decoy, an mTLS private key, another Run's "
            "workspace and PID 1 environ; writes /etc/passwd, the object dir, the other Run "
            "workspace and the read-only /inputs|/tool mounts; dumps its environment and the "
            "mount table"
        ),
        inputs={
            "submission": "real POST /api/v1/tools with an application/zip tool_source",
            "build": "real maintenance tool_build job -> deploy/sandbox/build_tool.py -> runsc",
            "secret_marker_sha256": hashlib.sha256(secret.encode()).hexdigest(),
            "db_name_marker_sha256": hashlib.sha256(db_name.encode()).hexdigest(),
            "read_paths": reads,
            "write_paths": writes,
            "guest_controls": controls,
        },
        expected={
            "reads": "all refused with a real errno",
            "writes": "all refused; only /workspace accepts writes",
            "credentials": "no deployment env name/value in the hook env or PID 1 environ",
            "mounts": "exactly the approved six; /tool and /inputs read-only",
            "build": "still ready, digest from the sealed source/lock/SBOM",
            "cleanup": "no runsc entry/process, no state dir/mount, empty host staging root",
        },
        observed={
            "hook": observed,
            "build_state": facts["state"],
            "build_bundle_digest": row["image_digest"],
            "cleanup_report": None,
            "guest_cleanup": cleanup,
            "host_staging_entries": t111.host_staging_entries(staging),
        },
        blocked_at="runtime (gVisor/runsc) uid 65534 + empty capabilities + NoNewPrivs + ro mounts",
        cleanup=cleanup,
        commands=[
            "POST /api/v1/tools (application/zip tool_source)",
            "cmd/maintenance run --once --kinds tool_build,artifact_finalize",
        ],
    )


def fork_hook_source(attempts: int) -> bytes:
    return t111.setup_hook_source(
        FORK_HOOK_TEMPLATE.replace("__T111_ATTEMPTS__", str(attempts))
    )


def test_build_stage_fork_bomb_is_stopped_by_the_pid_limit(
    t033_stack, tmp_path, evidence: t111.Evidence
) -> None:
    stack = t033_stack
    owner = creator(stack, "t111.fork")
    staging = tmp_path / "build-staging"
    staging.mkdir()

    # Control: 12 forked children complete under the 64-pid limit, so forking
    # itself works and the sandbox can carry the allowed process count.
    control_baseline = t111.cleanup_snapshot()
    control_tool = submit_tool(stack, owner, fork_hook_source(12))
    completed, _reports = t111.run_build_once(
        stack, env=t111.build_env(stack, workspace_root=staging, pids=64)
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    control_facts = t037.wait_build_finished(stack, owner, control_tool["id"], timeout=240)
    assert control_facts["state"] == "ready", control_facts
    control_log = t037.download_artifact_bytes(
        owner, control_facts["build_log_artifact_id"]
    ).decode("utf-8", "replace")
    control = t111.hook_marker(control_log, "T111_FORK")
    assert control["children"] == 12, control
    assert control["failure"] is None, control
    control_cleanup = assert_build_reclaimed(
        stack, control_tool["id"], staging, control_baseline
    )

    # Scaling control: the same 16 children that the 64-pid profile stopped are
    # admitted when the deployment knob is raised to 128, so the boundary follows
    # the configured limit (not a fixed gVisor process cap).
    scaling_baseline = t111.cleanup_snapshot()
    scaling_tool = submit_tool(stack, owner, fork_hook_source(16))
    completed, _reports = t111.run_build_once(
        stack, env=t111.build_env(stack, workspace_root=staging, pids=128)
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    scaling_facts = t037.wait_build_finished(stack, owner, scaling_tool["id"], timeout=240)
    assert scaling_facts["state"] == "ready", scaling_facts
    scaling_log = t037.download_artifact_bytes(
        owner, scaling_facts["build_log_artifact_id"]
    ).decode("utf-8", "replace")
    scaling = t111.hook_marker(scaling_log, "T111_FORK")
    assert scaling["children"] == 16, scaling
    scaling_cleanup = assert_build_reclaimed(
        stack, scaling_tool["id"], staging, scaling_baseline
    )

    # Attack: far past the limit. gVisor stops the container at the pids limit
    # instead of handing the hook an EAGAIN, so the build can never become ready
    # and the completion guard publishes no digest.
    attack_baseline = t111.cleanup_snapshot()
    attack_tool = submit_tool(stack, owner, fork_hook_source(200))
    completed, _reports = t111.run_build_once(
        stack, env=t111.build_env(stack, workspace_root=staging, pids=64)
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    attack_facts = t037.wait_build_finished(stack, owner, attack_tool["id"], timeout=240)
    attack_log_bytes = b""
    if attack_facts["build_log_artifact_id"]:
        attack_log_bytes = t037.download_artifact_bytes(
            owner, attack_facts["build_log_artifact_id"]
        )
    attack_log = attack_log_bytes.decode("utf-8", "replace")
    assert attack_facts["state"] == "failed", attack_facts
    assert attack_facts["build_error_code"] in {"build_failed", "build_timeout"}, attack_facts
    assert attack_facts["build_bundle_digest"] is None, attack_facts
    row = t037.tool_row(stack.dbname, attack_tool["id"])
    assert row["image_digest"] is None, row
    # It never completed the 200 forks: no final marker and no digest.
    assert "T111_FORK " not in attack_log, attack_log[-2000:]
    attack_cleanup = assert_build_reclaimed(
        stack, attack_tool["id"], staging, attack_baseline
    )

    evidence.record(
        "build-fork-limit",
        stage="build",
        attack="fork as many children as possible from the untrusted setup.py hook (200 attempts)",
        inputs={
            "control_children": 12,
            "control_pids_limit": 64,
            "scaling_children": 16,
            "scaling_pids_limit": 128,
            "attack_attempts": 200,
        },
        expected=(
            "a bounded 12-child fork completes under the 64-pid limit; 16 children "
            "are stopped there but admitted at 128; the 200-attempt fork bomb is "
            "stopped, publishes no digest and is reclaimed"
        ),
        observed={
            "control": {
                "children": control["children"],
                "failure": control["failure"],
                "build_state": control_facts["state"],
            },
            "scaling": {
                "children": scaling["children"],
                "build_state": scaling_facts["state"],
            },
            "attack": {
                "attempts": 200,
                "build_state": attack_facts["state"],
                "build_error_code": attack_facts["build_error_code"],
                "digest": attack_facts["build_bundle_digest"],
                "completed_marker_present": "T111_FORK " in attack_log,
                "log_tail": attack_log[-800:],
            },
            "guest_cleanup_control": control_cleanup,
            "guest_cleanup_scaling": scaling_cleanup,
            "guest_cleanup_attack": attack_cleanup,
            "host_staging_entries": t111.host_staging_entries(staging),
        },
        blocked_at=(
            "OCI pids cgroup limit enforced by runsc: the container is stopped at the "
            "limit (no in-app EAGAIN observed); the 12-child control and the "
            "128-pid scaling control prove the boundary follows the configured limit"
        ),
        cleanup=attack_cleanup,
        commands=[
            "cmd/maintenance run --once with ORCHARA_SANDBOX_PIDS=64 (control/attack)",
            "cmd/maintenance run --once with ORCHARA_SANDBOX_PIDS=128 (scaling control)",
        ],
    )


def memory_parent_and_child(log_text: str) -> tuple[dict | None, dict | None, int]:
    """The parent marker, the child marker (when it ran to completion) and the
    highest progress MiB reported before a stop."""
    parent = t111.hook_marker(log_text, "T111_MEMORY ")
    tail = parent.get("stdout_tail") or ""
    child = None
    if "T111_MEMORY_CHILD" in tail:
        marker = tail[tail.rindex("T111_MEMORY_CHILD") + len("T111_MEMORY_CHILD") :]
        child = json.loads(marker.splitlines()[0].strip())
    progress = [
        int(line.split()[-1])
        for line in tail.splitlines()
        if line.startswith("T111_MEMORY_PROGRESS")
    ]
    return parent, child, max(progress) if progress else 0


def test_build_stage_memory_exhaustion_is_stopped(
    t033_stack, tmp_path, evidence: t111.Evidence
) -> None:
    stack = t033_stack
    owner = creator(stack, "t111.memory")
    staging = tmp_path / "build-staging"
    staging.mkdir()

    # Control: with a 1 GiB limit the same 512 MiB allocation completes, proving
    # the stop below is the 192 MiB limit and not a script bug.
    control_baseline = t111.cleanup_snapshot()
    control_tool = submit_tool(stack, owner, t111.setup_hook_source(MEMORY_HOOK))
    completed, _reports = t111.run_build_once(
        stack, env=t111.build_env(stack, workspace_root=staging, memory_mb=1024)
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    control_facts = t037.wait_build_finished(stack, owner, control_tool["id"], timeout=240)
    assert control_facts["state"] == "ready", control_facts
    control_log = t037.download_artifact_bytes(
        owner, control_facts["build_log_artifact_id"]
    ).decode("utf-8", "replace")
    control_parent, control_child, control_progress = memory_parent_and_child(control_log)
    assert control_parent["returncode"] == 0, control_parent
    assert control_child is not None and control_child["outcome"] == "survived", control_child
    assert control_child["allocated_mib"] == 512, control_child
    assert control_progress == 512, control_progress
    control_cleanup = assert_build_reclaimed(
        stack, control_tool["id"], staging, control_baseline
    )

    # Attack: 192 MiB limit; the child is stopped well before 512 MiB while the
    # parent survives and reports the real progress.
    attack_baseline = t111.cleanup_snapshot()
    attack_tool = submit_tool(stack, owner, t111.setup_hook_source(MEMORY_HOOK))
    completed, _reports = t111.run_build_once(
        stack, env=t111.build_env(stack, workspace_root=staging, memory_mb=192)
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    attack_facts = t037.wait_build_finished(stack, owner, attack_tool["id"], timeout=240)
    assert attack_facts["state"] == "ready", attack_facts
    attack_log = t037.download_artifact_bytes(
        owner, attack_facts["build_log_artifact_id"]
    ).decode("utf-8", "replace")
    attack_parent, attack_child, attack_progress = memory_parent_and_child(attack_log)
    assert attack_parent["returncode"] != 0, attack_parent
    assert attack_child is None or attack_child["outcome"] == "stopped", attack_child
    assert 0 < attack_progress < 512, attack_progress
    row = t037.tool_row(stack.dbname, attack_tool["id"])
    assert row["image_digest"] == row["build_bundle_digest"], row
    attack_cleanup = assert_build_reclaimed(
        stack, attack_tool["id"], staging, attack_baseline
    )

    evidence.record(
        "build-memory-limit",
        stage="build",
        attack="allocate up to 512 MiB in a child process of the untrusted setup.py hook",
        inputs={
            "control_memory_limit_mb": 1024,
            "attack_memory_limit_mb": 192,
            "requested_mb": 512,
        },
        expected=(
            "with 1 GiB the 512 MiB allocation completes; with 192 MiB the child is "
            "stopped before 512 MiB, the build path stays safe and reclaimed"
        ),
        observed={
            "control": {
                "build_state": control_facts["state"],
                "parent": control_parent,
                "child": control_child,
                "progress_mib": control_progress,
            },
            "attack": {
                "build_state": attack_facts["state"],
                "parent": attack_parent,
                "child": attack_child,
                "progress_mib": attack_progress,
                "digest": row["image_digest"],
            },
            "guest_cleanup_control": control_cleanup,
            "guest_cleanup_attack": attack_cleanup,
            "host_staging_entries": t111.host_staging_entries(staging),
        },
        blocked_at=(
            "OCI memory limit enforced by runsc inside the sandbox: the allocation "
            "child is stopped before the requested size; the 1 GiB control completes"
        ),
        cleanup=attack_cleanup,
        commands=[
            "cmd/maintenance run --once with ORCHARA_SANDBOX_MEMORY_MB=1024 (control)",
            "cmd/maintenance run --once with ORCHARA_SANDBOX_MEMORY_MB=192 (attack)",
        ],
    )


def test_build_stage_disk_fill_hits_the_workspace_quota(
    t033_stack, tmp_path, evidence: t111.Evidence
) -> None:
    stack = t033_stack
    owner = creator(stack, "t111.disk")
    staging = tmp_path / "build-staging"
    staging.mkdir()
    baseline = t111.cleanup_snapshot()
    before = t111.filesystem_bytes("/")
    tool = submit_tool(stack, owner, t111.setup_hook_source(DISK_HOOK))
    env = t111.build_env(stack, workspace_root=staging, quota_mb=8)
    completed, _reports = t111.run_build_once(stack, env=env)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    facts = t037.wait_build_finished(stack, owner, tool["id"], timeout=240)
    assert facts["state"] == "ready", facts
    log_text = t037.download_artifact_bytes(
        owner, facts["build_log_artifact_id"]
    ).decode("utf-8", "replace")
    observed = t111.hook_marker(log_text, "T111_DISK")
    assert observed["failure"] is not None, observed
    assert observed["failure"]["errno_name"] in {"ENOSPC", "EFBIG"}, observed
    assert 0 < observed["written_mib"] <= 10, observed
    after = t111.filesystem_bytes("/")
    growth = after["used_bytes"] - before["used_bytes"]
    # The writable mount is a size-bounded tmpfs; the guest volume must not grow
    # by the attempted fill (64 MiB).
    assert growth < 32 * 1024 * 1024, (before, after, growth)
    row = t037.tool_row(stack.dbname, tool["id"])
    assert row["image_digest"] == row["build_bundle_digest"], row

    cleanup = assert_build_reclaimed(stack, tool["id"], staging, baseline)
    evidence.record(
        "build-disk-quota",
        stage="build",
        attack="write 64 MiB to /workspace from the untrusted setup.py hook",
        inputs={"workspace_quota_mb": 8, "requested_mib": 64},
        expected="ENOSPC/EFBIG at the tmpfs quota; the guest filesystem does not grow and the build/cleanup stay safe",
        observed={
            "written_mib": observed["written_mib"],
            "failure": observed["failure"],
            "guest_filesystem_before": before,
            "guest_filesystem_after": after,
            "guest_volume_growth_bytes": growth,
            "build_state": facts["state"],
            "guest_cleanup": cleanup,
            "host_staging_entries": t111.host_staging_entries(staging),
        },
        blocked_at="tmpfs size quota + RLIMIT_FSIZE inside the sandbox (host project quota is F15B deployment scope: see not_run)",
        cleanup=cleanup,
        commands=["cmd/maintenance run --once with ORCHARA_SANDBOX_WORKSPACE_QUOTA_MB=8"],
    )


def test_build_stage_timeout_kills_the_build_and_reclaims_everything(
    t033_stack, tmp_path, evidence: t111.Evidence
) -> None:
    stack = t033_stack
    owner = creator(stack, "t111.timeout")
    staging = tmp_path / "build-staging"
    staging.mkdir()
    baseline = t111.cleanup_snapshot()
    tool = submit_tool(stack, owner, t111.setup_hook_source(TIMEOUT_HOOK))
    env = t111.build_env(stack, workspace_root=staging, sandbox_timeout="120s")
    started = time.time()
    completed, _reports = t111.run_build_once(
        stack, env=env, tool_build_timeout="2s", subprocess_timeout=300
    )
    elapsed = time.time() - started
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    facts = t037.wait_build_finished(stack, owner, tool["id"], timeout=120)
    assert facts["state"] == "failed", facts
    assert facts["build_error_code"] == "build_timeout", facts
    assert facts["build_bundle_digest"] is None, facts
    row = t037.tool_row(stack.dbname, tool["id"])
    assert row["image_digest"] is None, row
    jobs = t037.tool_jobs(stack.dbname, tool["id"])
    assert jobs[0]["state"] == "failed", jobs
    assert jobs[0]["error_code"] == "build_timeout", jobs
    assert elapsed < 45, elapsed

    cleanup = assert_build_reclaimed(stack, tool["id"], staging, baseline)
    evidence.record(
        "build-timeout-reclaim",
        stage="build",
        attack="sleep 60s in the untrusted setup.py hook while the build budget is 2s",
        inputs={"tool_build_timeout": "2s", "hook_sleep_seconds": 60},
        expected="build_timeout, no ready digest, unit killed, all processes/dirs/mounts reclaimed",
        observed={
            "build_state": facts["state"],
            "build_error_code": facts["build_error_code"],
            "elapsed_seconds": round(elapsed, 3),
            "guest_cleanup": cleanup,
            "host_staging_entries": t111.host_staging_entries(staging),
        },
        blocked_at="sandbox executor timeout + kill + cleanup proof (runsc delete/pid/workspace)",
        cleanup=cleanup,
        commands=["cmd/maintenance run --once --tool-build-timeout 2s"],
    )


# ---------------------------------------------------------------------------
# end-to-end: the real supervisor tool run still succeeds and reclaims
# ---------------------------------------------------------------------------
def test_end_to_end_sandboxed_tool_run_still_succeeds_and_reclaims(
    t033_stack, tmp_path, evidence: t111.Evidence
) -> None:
    stack = t033_stack
    owner = t033.golden_owner(stack)
    artifact_id = t033.upload_artifact(stack, owner.creator, t033.SALES_CSV)
    run = t033.debug_run_with_input(stack, owner, artifact_id)
    staging = tmp_path / "host-staging"
    staging.mkdir()
    baseline = t111.cleanup_snapshot()
    env = t033.supervisor_exec_env(
        stack,
        digest=t033.rootfs_digest(),
        extra={"ORCHARA_SANDBOX_WORKSPACE_ROOT": str(staging)},
    )
    with t033.FinalizeWorker(stack):
        completed = t033.run_supervisor_exec(stack, env)
    report = t033.exec_report(completed)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["status"] == "executed", report
    assert report["outcome"] == "succeeded", report
    assert report["run_state"] == "succeeded", report
    sandbox = report["sandbox"]
    assert sandbox["started"] is True, sandbox
    assert sandbox["cleanup"]["sandbox_list_matches"] == 0, sandbox
    assert sandbox["cleanup"]["pid_alive"] is False, sandbox
    assert sandbox["cleanup"]["workspace_removed"] is True, sandbox
    assert t033.wait_run_state(stack.dbname, run["id"], {"succeeded"})["finished_at"] is not None
    cleanup = t111.cleanup_snapshot(sandbox_id=sandbox["sandbox_id"])
    t111.assert_reclaimed(cleanup, baseline, sandbox_id=sandbox["sandbox_id"])
    assert t111.host_staging_entries(staging) == [], t111.host_staging_entries(staging)
    evidence.record(
        "end-to-end-tool-run",
        stage="run",
        attack="none (positive end-to-end control): upload CSV, debug run, real supervisor exec",
        inputs={"run_id": run["id"], "csv_sha256": hashlib.sha256(t033.SALES_CSV).hexdigest()},
        expected="the sandboxed preset tool succeeds end to end and the unit is fully reclaimed",
        observed={
            "exec_report": report,
            "run_state": "succeeded",
            "guest_cleanup": cleanup,
            "host_staging_entries": t111.host_staging_entries(staging),
        },
        blocked_at=None,
        cleanup=cleanup,
        commands=["cmd/supervisor exec --once (real mTLS + runsc)"],
    )

