"""P03/T006: the sandbox policy refuses dangerous configuration.

The policy must refuse *before* runsc is invoked, because runsc itself will grant
host networking when asked. The integration tests need a Linux guest with a gVisor
runtime; they skip otherwise and never pretend to have run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
PROBE_ROOT = PRODUCT_ROOT / "probes" / "p03_sandbox"
sys.path.insert(0, str(PROBE_ROOT))

import sandbox_probe as probe  # noqa: E402


# --------------------------------------------------------------------------
# policy (no guest required)
# --------------------------------------------------------------------------
SAFE = probe.SandboxRequest(command="echo ok")


def test_safe_request_is_allowed() -> None:
    assert probe.validate(SAFE) == []


@pytest.mark.parametrize(
    ("request_", "needle"),
    [
        (probe.SandboxRequest(command="x", network="host"), "network"),
        (probe.SandboxRequest(command="x", network="loopback"), "network"),
        (probe.SandboxRequest(command="x", overlay=False), "overlay"),
        (probe.SandboxRequest(command="x", privileged=True), "privileged"),
        (probe.SandboxRequest(command="x", uid=0), "uid 0"),
        (probe.SandboxRequest(command="x", mounts=("/etc",)), "OCI bundle"),
        (probe.SandboxRequest(command="x", mounts=("/work/input.csv",)), "OCI bundle"),
        (probe.SandboxRequest(command="   "), "command"),
    ],
    ids=[
        "host-net",
        "loopback-net",
        "no-overlay",
        "privileged",
        "root",
        "bad-mount",
        "any-mount",
        "empty-cmd",
    ],
)
def test_dangerous_requests_are_refused(request_: probe.SandboxRequest, needle: str) -> None:
    reasons = probe.validate(request_)
    assert reasons, "dangerous request was allowed"
    assert any(needle in reason for reason in reasons), reasons


def test_every_reason_is_reported_not_just_the_first() -> None:
    request_ = probe.SandboxRequest(command="x", network="host", overlay=False, uid=0)
    reasons = probe.validate(request_)
    assert len(reasons) == 3, reasons


def test_mounts_are_refused_because_the_do_path_cannot_apply_them() -> None:
    """A mount list the launcher silently ignores would run the script unmounted."""
    request_ = probe.SandboxRequest(command="x", mounts=("/work/input.csv",))
    reasons = probe.validate(request_)
    assert reasons, "a mount request must not be silently accepted"
    assert "OCI bundle" in reasons[0]
    # and the built command really carries no mount flag
    assert "mount" not in probe.build_command(probe.SandboxRequest(command="x"))


def test_rejected_request_never_reaches_the_guest(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def spy(command: str, **_kwargs):
        called.append(command)
        raise AssertionError("run_wsl must not be called for a rejected request")

    monkeypatch.setattr(probe, "run_wsl", spy)
    with pytest.raises(probe.PolicyRejection) as excinfo:
        probe.run_sandbox(probe.SandboxRequest(command="x", network="host"))

    assert called == [], "the sandbox command was executed despite a policy violation"
    assert excinfo.value.reasons


def test_built_command_uses_the_validated_network_mode() -> None:
    command = probe.build_command(probe.SandboxRequest(command="echo hi"))
    assert "--network=none" in command
    assert "runsc" in command


# --------------------------------------------------------------------------
# integration: real guest
# --------------------------------------------------------------------------
def _guest_ready() -> bool:
    if shutil.which("wsl") is None:
        return False
    try:
        completed = probe.run_wsl("runsc --version", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


guest = pytest.mark.skipif(not _guest_ready(), reason="no Linux guest with a gVisor runtime")


@guest
def test_versions_report_linux_kernel_and_runtime() -> None:
    versions = probe.report_versions()
    assert versions["kernel"], "kernel version must be recorded"
    assert "runsc version" in versions["runtime"]
    assert versions["policy_version"] == probe.POLICY_VERSION
    assert versions["platform"]


@guest
def test_a_real_sandboxed_command_runs() -> None:
    result = probe.run_sandbox(probe.SandboxRequest(command="echo sandbox-ok"))
    assert result["exit_code"] == 0
    assert "sandbox-ok" in result["stdout"]
    assert "--network=none" in result["command"]


@guest
def test_full_probe_refuses_every_dangerous_config() -> None:
    report = probe.probe()
    assert report["all_refused"] is True, json.dumps(report["refusals"], indent=2)
    assert report["benign_run"]["exit_code"] == 0


# --------------------------------------------------------------------------
# T010: the isolation profile actually blocks the attacks
# --------------------------------------------------------------------------
def test_isolation_profile_script_declares_minimal_privileges() -> None:
    """Guard the committed profile: no host root mount, no capabilities, non-root."""
    text = (PROBE_ROOT / "isolation_matrix.sh").read_text(encoding="utf-8")
    assert '"uid": 65534' in text
    assert '"bounding": []' in text
    assert '"type": "network"' in text
    assert '"readonly": true' in text
    # the only bind mount must be the approved input directory
    assert '"destination": "/work/input"' in text
    assert '"destination": "/etc"' not in text
    assert '"source": "/"' not in text


@guest
def test_isolation_matrix_blocks_every_attack() -> None:
    matrix = probe.isolation_matrix()

    assert matrix["profile"] == "minimal-oci-bundle"
    assert matrix["identity"].startswith("uid=65534"), matrix["identity"]

    # the approved input is readable, and only it
    assert "alice-data" in matrix["approved_input"]
    assert "DENIED" in matrix["foreign_input"], matrix["foreign_input"]
    assert "bob-secret" not in matrix["foreign_input"]

    # the host filesystem is not mounted at all
    assert "DENIED" in matrix["host_filesystem"], matrix["host_filesystem"]

    # writes land only in the tmpfs
    assert "DENIED" in matrix["write_attempt"], matrix["write_attempt"]

    # Control: the same connectivity probe succeeds outside the sandbox, so
    # "blocked" below means blocked, not "the probe is broken".
    assert "CONNECTED" in matrix["control_network"], matrix["control_network"]

    # No egress from the sandbox: public, the live proxy endpoint, and a proxied
    # HTTP attempt all fail.
    for key in ("public_network", "host_network", "proxy_network"):
        assert "CONNECTED" not in matrix[key], f"{key} reached something: {matrix[key]}"
        assert "EXIT=1" in matrix[key], f"{key} did not report a failure: {matrix[key]}"

    # Host credentials never enter the sandbox.
    assert "ORCHARA_TEST_SECRET" not in matrix["sandbox_env"], matrix["sandbox_env"]


@guest
def test_host_environment_does_not_reach_the_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `env -i` launch: a host secret must not be visible, approved vars must be."""
    monkeypatch.setenv("ORCHARA_TEST_SECRET", "leakme")
    leak = probe.run_sandbox(probe.SandboxRequest(command="env"))
    assert "ORCHARA_TEST_SECRET" not in leak["stdout"], leak["stdout"]

    approved = probe.run_sandbox(
        probe.SandboxRequest(
            command="env",
            env=(("ORCHARA_APPROVED_TOKEN", "tok-123"),),
        )
    )
    assert "ORCHARA_APPROVED_TOKEN=tok-123" in approved["stdout"], approved["stdout"]


def test_a_disallowed_environment_variable_is_rejected_before_launch() -> None:
    reasons = probe.validate(
        probe.SandboxRequest(command="env", env=(("AWS_SECRET_ACCESS_KEY", "nope"),))
    )
    assert reasons, "an unapproved variable must not be passed through"
    assert "AWS_SECRET_ACCESS_KEY" in reasons[0]


def test_launch_command_is_wrapped_and_carries_only_approved_env() -> None:
    command = probe.build_command(
        probe.SandboxRequest(command="env", env=(("ORCHARA_APPROVED_TOKEN", "tok"),))
    )
    assert command.startswith("env -i "), command
    assert "ORCHARA_APPROVED_TOKEN=tok" in command
    assert "ORCHARA_TEST_SECRET" not in command


@guest
def test_proxy_reachability_is_what_is_being_tested() -> None:
    """The proxy claim is reachability, and the rootfs cannot route through one."""
    matrix = probe.isolation_matrix()
    assert "unreachable" in matrix["proxy_network"].lower() or "EXIT=1" in matrix["proxy_network"]
    assert "HTTP_PROXY" in matrix["proxy_note"]
