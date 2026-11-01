#!/usr/bin/env python3
"""P03/T006 probe: a gVisor sandbox that refuses dangerous configuration.

The runtime lives in a Linux guest (WSL2 Ubuntu 24.04, kernel 6.6.87.2, user
decision: WSL2 counts as the acceptance host). This script runs on the host and
drives it through `wsl -u root`.

Two things are demonstrated:

* a real gVisor sandbox executes a command, and the report records the Linux,
  kernel, runtime and policy versions that made it possible;
* the platform's policy layer refuses host networking, a disabled overlay,
  privileged mode, uid 0 and unapproved mounts **before** runsc is invoked.

That second point matters because runsc itself will happily grant host
networking (verified: `runsc --network=host` shows the host's interfaces), so
the refusal has to live in our code.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

POLICY_VERSION = "orchara.sandbox-policy/v1"
DEFAULT_DISTRO = os.environ.get("ORCHARA_WSL_DISTRO", "Ubuntu-24.04")
ALLOWED_NETWORKS = ("none",)
# Where an approved input may be mounted inside the sandbox.
APPROVED_MOUNT_ROOT = "/work"


class PolicyRejection(RuntimeError):
    """Raised when a sandbox request violates the policy."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass(frozen=True)
class SandboxRequest:
    """What the caller asked for. Defaults are the safe ones."""

    command: str = "true"
    network: str = "none"
    overlay: bool = True
    privileged: bool = False
    uid: int = 65534
    mounts: tuple[str, ...] = ()


def validate(request: SandboxRequest) -> list[str]:
    """Return the reasons this request must be refused (empty when allowed)."""
    reasons: list[str] = []

    if request.network not in ALLOWED_NETWORKS:
        reasons.append(
            f"network {request.network!r} is not allowed; only {ALLOWED_NETWORKS} is permitted"
        )
    if not request.overlay:
        reasons.append(
            "overlay=false would give the sandbox write access to the host filesystem"
        )
    if request.privileged:
        reasons.append("privileged mode is not available to user scripts")
    if request.uid == 0:
        reasons.append("running as uid 0 inside the sandbox is not allowed")
    for mount in request.mounts:
        normalised = mount.replace("\\", "/")
        if not normalised.startswith(f"{APPROVED_MOUNT_ROOT}/"):
            reasons.append(
                f"mount {mount!r} is outside the approved root {APPROVED_MOUNT_ROOT}/"
            )

    if not request.command.strip():
        reasons.append("command must not be empty")
    return reasons


def run_wsl(command: str, distro: str = DEFAULT_DISTRO, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a shell command as root inside the Linux guest."""
    return subprocess.run(
        ["wsl", "-d", distro, "-u", "root", "-e", "bash", "-lc", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def _parse_runsc_version(output: str) -> dict[str, str]:
    info = {"runtime": "", "spec": "", "platform": ""}
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("runsc version"):
            info["runtime"] = stripped
        elif stripped.startswith("spec:"):
            info["spec"] = stripped.split(":", 1)[1].strip()
    match = re.search(r"platform:\s*([a-z]+)", output)
    if match:
        info["platform"] = match.group(1)
    return info


def report_versions(distro: str = DEFAULT_DISTRO) -> dict[str, Any]:
    """Collect the Linux/kernel/runtime/policy facts the ticket asks for."""
    completed = run_wsl(
        "uname -sr; echo '---'; runsc --version 2>&1; echo '---'; "
        "command -v runsc; echo '---'; nproc; echo '---'; cat /proc/self/status | grep -E '^Cap(Eff|Prm)'",
        distro=distro,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"cannot probe the guest: {completed.stderr.strip()}")

    sections = [section.strip() for section in completed.stdout.split("---")]
    kernel = sections[0].splitlines()[0] if sections and sections[0] else ""
    runtime_output = sections[1] if len(sections) > 1 else ""
    runsc_path = sections[2].strip() if len(sections) > 2 else ""
    capabilities = sections[4].strip() if len(sections) > 4 else ""

    parsed = _parse_runsc_version(runtime_output)
    return {
        "guest_distro": distro,
        "linux": kernel.split()[0] if kernel else "",
        "kernel": kernel.split(" ", 1)[1] if " " in kernel else "",
        "runtime": parsed["runtime"],
        "spec": parsed["spec"],
        "platform": parsed["platform"] or "default",
        "runsc_path": runsc_path,
        "capabilities": capabilities,
        "policy_version": POLICY_VERSION,
        "notes": "guest is WSL2; the maintainer accepted WSL2 as the acceptance host",
    }


def build_command(request: SandboxRequest) -> str:
    """Translate a validated request into a runsc invocation."""
    parts = ["runsc", f"--network={request.network}"]
    if not request.overlay:
        parts.append("--overlay=false")
    parts.append("do")
    if request.uid != 65534:
        parts.append(f"--uid={request.uid}")
    parts.append(f"sh -c {json.dumps(request.command)}")
    return " ".join(parts)


def run_sandbox(request: SandboxRequest, distro: str = DEFAULT_DISTRO) -> dict[str, Any]:
    """Validate, then execute. A rejected request never reaches runsc."""
    reasons = validate(request)
    if reasons:
        raise PolicyRejection(reasons)

    command = build_command(request)
    completed = run_wsl(command, distro=distro)
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "network": request.network,
        "policy_version": POLICY_VERSION,
    }


def probe(distro: str = DEFAULT_DISTRO) -> dict[str, Any]:
    """The ticket's evidence bundle: versions, a real run, and refusals."""
    versions = report_versions(distro)

    benign = run_sandbox(SandboxRequest(command="echo sandbox-ok"), distro=distro)

    refusals: dict[str, list[str]] = {}
    attempts = {
        "host_network": SandboxRequest(command="echo x", network="host"),
        "disabled_overlay": SandboxRequest(command="echo x", overlay=False),
        "privileged": SandboxRequest(command="echo x", privileged=True),
        "root_inside": SandboxRequest(command="echo x", uid=0),
        "unapproved_mount": SandboxRequest(command="echo x", mounts=("/etc",)),
    }
    for name, request in attempts.items():
        try:
            reasons = validate(request)
            refusals[name] = reasons
            if not reasons:  # pragma: no cover - would be a policy hole
                refusals[name] = ["NOT REFUSED"]
        except Exception as exc:  # pragma: no cover - defensive
            refusals[name] = [f"error: {exc}"]

    return {
        "probe": "p03-sandbox",
        "task": "P03 / T006",
        "versions": versions,
        "benign_run": benign,
        "refusals": refusals,
        "all_refused": all(reasons for reasons in refusals.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P03 gVisor sandbox probe")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("versions")
    sub.add_parser("probe")
    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("--cmd", required=True)
    run_cmd.add_argument("--network", default="none")
    run_cmd.add_argument("--uid", type=int, default=65534)
    validate_cmd = sub.add_parser("validate")
    validate_cmd.add_argument("--network", default="none")
    validate_cmd.add_argument("--overlay", default="true")
    validate_cmd.add_argument("--privileged", action="store_true")
    validate_cmd.add_argument("--uid", type=int, default=65534)
    args = parser.parse_args(argv)

    try:
        if args.command == "versions":
            payload = report_versions()
        elif args.command == "probe":
            payload = probe()
        elif args.command == "run":
            payload = run_sandbox(SandboxRequest(command=args.cmd, network=args.network, uid=args.uid))
        else:
            request = SandboxRequest(
                network=args.network,
                overlay=args.overlay.lower() != "false",
                privileged=args.privileged,
                uid=args.uid,
            )
            reasons = validate(request)
            payload = {"allowed": not reasons, "reasons": reasons}
    except PolicyRejection as exc:
        print(json.dumps({"error": "policy_rejection", "reasons": exc.reasons}, ensure_ascii=False))
        return 2

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
