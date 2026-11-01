#!/usr/bin/env python3
"""D00/T001 environment probe.

Produces the reproducible environment report required by ticket T001:
toolchain availability with the exact command that produced each version,
reachable services, host facts, capability classification, and an explicit
blocker list.

Design rules (from HANDOFF / BOOTSTRAP / T001 acceptance criteria):

* every version records the probe command, so a later operator can re-run it;
* no credential, token or environment-variable *value* is ever written;
  only presence flags for well-known provider variables;
* a capability that cannot be demonstrated is reported as unavailable or
  blocked -- never as "passed". Static validation never upgrades P0.
"""

from __future__ import annotations

import argparse
import json
import locale
import os
import platform
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "orchara.env-probe/v1"

# Provider variables we only ever report as present/absent.
_CREDENTIAL_ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "DASHSCOPE_API_KEY",
)

_SERVICE_PORTS = (
    ("postgres", 5432),
    ("redis", 6379),
    ("object_storage", 9000),
)


# --------------------------------------------------------------------------
# low-level probing
# --------------------------------------------------------------------------
def _decode(raw: bytes) -> str:
    """Decode tool output independently of the OS locale.

    Windows console tools are a mix: most emit the ANSI code page (GBK on this
    host), while `wsl.exe` emits UTF-16LE. Decoding with the locale crashes on
    the latter, so try UTF-8, then UTF-16LE when NUL bytes give it away, then
    the locale with replacement characters.
    """
    if not raw:
        return ""
    if raw.count(b"\x00") > len(raw) // 4:
        try:
            return raw.decode("utf-16-le", errors="replace")
        except Exception:  # pragma: no cover - defensive
            pass
    for encoding in ("utf-8", "cp936", locale.getpreferredencoding(False)):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _run(command: list[str], timeout: float = 25.0) -> tuple[int, str]:
    """Run a probe command, returning (returncode, combined output)."""
    try:
        completed = subprocess.run(  # noqa: S603 - commands are fixed literals
            command,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError as exc:  # pragma: no cover - platform specific
        return 126, str(exc)
    output = _decode(completed.stdout or b"") + _decode(completed.stderr or b"")
    return completed.returncode, output.strip()


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _probe_tool(name: str, command: list[str], *, version_hint: str = "") -> dict[str, Any]:
    """Probe one tool, recording the command used and the raw outcome."""
    rendered = " ".join(command)
    if not Path(command[0]).exists() and shutil.which(command[0]) is None:
        return {
            "name": name,
            "command": rendered,
            "available": False,
            "version": None,
            "error": "not found on PATH and not present at the recorded path",
        }
    code, output = _run(command)
    if code != 0:
        return {
            "name": name,
            "command": rendered,
            "available": False,
            "version": None,
            "error": _first_line(output) or f"exit code {code}",
        }
    version = _first_line(output)
    if version_hint and version_hint in output:
        for line in output.splitlines():
            if version_hint in line:
                version = line.strip()
                break
    return {
        "name": name,
        "command": rendered,
        "available": True,
        "version": version,
        "error": None,
    }


def _candidate_tool_paths(toolchain_dir: Path | None) -> list[dict[str, Any]]:
    """Tools we expect, including user-space toolchains under .toolchains/."""
    candidates: list[dict[str, Any]] = []
    exe = ".exe" if os.name == "nt" else ""

    toolchain_tools: list[tuple[str, Path, list[str]]] = []
    if toolchain_dir is not None:
        toolchain_tools = [
            ("go", toolchain_dir / "go" / "bin" / f"go{exe}", ["version"]),
            (
                "psql",
                toolchain_dir / "pgsql" / "bin" / f"psql{exe}",
                ["--version"],
            ),
        ]

    for name, path, args in toolchain_tools:
        if path.exists():
            candidates.append(_probe_tool(name, [str(path), *args]))

    # PATH-resolved tools first, so a system install wins the recorded version.
    npm_binary = "npm.cmd" if os.name == "nt" else "npm"
    path_tools = [
        ("git", ["git", "--version"]),
        ("python", [sys.executable, "--version"]),
        ("node", ["node", "--version"]),
        ("npm", [npm_binary, "--version"]),
        ("go", ["go", "version"]),
        ("docker", ["docker", "version", "--format", "{{.Server.Version}}"]),
        ("psql", ["psql", "--version"]),
        ("redis-server", ["redis-server", "--version"]),
        ("wsl", ["wsl.exe", "--version"]),
        ("nvidia-smi", ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"]),
    ]
    probed_names = {item["name"] for item in candidates}
    for name, command in path_tools:
        if name in probed_names:
            continue
        candidates.append(_probe_tool(name, command))
    return candidates


def _probe_service(name: str, port: int, timeout: float = 1.0) -> dict[str, Any]:
    """TCP-reachability probe. Reachability is not authentication."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        reachable = sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()
    return {
        "name": name,
        "probe": f"tcp connect 127.0.0.1:{port}",
        "port": port,
        "reachable": reachable,
    }


def _memory_gib() -> float | None:
    try:
        if os.name == "nt":
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemStatus()
            status.dwLength = ctypes.sizeof(_MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return round(status.ullTotalPhys / (1024**3), 1)
            return None
        page = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        return round(page * pages / (1024**3), 1)
    except Exception:  # pragma: no cover - best effort host fact
        return None


def _host_facts() -> dict[str, Any]:
    return {
        "os": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "memory_gib": _memory_gib(),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "cwd": str(Path.cwd()),
    }


# --------------------------------------------------------------------------
# capability classification
# --------------------------------------------------------------------------
def _classify(tools: list[dict[str, Any]], services: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {tool["name"]: tool for tool in tools}
    service_state = {svc["name"]: svc["reachable"] for svc in services}

    go = by_name.get("go", {})
    postgres_binary = by_name.get("psql", {}).get("available", False)
    node = by_name.get("node", {}).get("available", False)
    docker = by_name.get("docker", {}).get("available", False)
    wsl = by_name.get("wsl", {}).get("available", False)
    gpu = by_name.get("nvidia-smi", {}).get("available", False)
    gpu_info = _parse_gpu(by_name.get("nvidia-smi", {}).get("version") or "")

    return {
        "go_build": {
            "status": "available" if go.get("available") else "unavailable",
            "detail": go.get("version") if go.get("available") else "no Go toolchain resolved",
        },
        "node_build": {
            "status": "available" if node else "unavailable",
            "detail": by_name.get("node", {}).get("version"),
        },
        "postgres": {
            # A reachable server is what P02 needs; a client binary alone is not enough.
            "status": "available" if service_state.get("postgres") else "unavailable",
            "client_binary": postgres_binary,
            "reachable": service_state.get("postgres", False),
            "detail": "server reachable on 127.0.0.1:5432"
            if service_state.get("postgres")
            else "no PostgreSQL server accepting connections on 127.0.0.1:5432",
        },
        "redis": {
            "status": "available" if service_state.get("redis") else "unavailable",
            "detail": "server reachable on 127.0.0.1:6379"
            if service_state.get("redis")
            else "no Redis server accepting connections on 127.0.0.1:6379",
        },
        "container_runtime": {
            "status": "available" if docker else "unavailable",
            "detail": "docker daemon reachable" if docker else "no docker CLI/daemon",
        },
        "linux_host": {
            "status": "available" if platform.system() == "Linux" else "unavailable",
            "detail": "probing process runs on Linux"
            if platform.system() == "Linux"
            else (
                "a WSL distribution is registered but this probe did not run inside it; "
                "WSL presence is not Linux isolation acceptance"
            )
            if wsl
            else "no Linux host and no WSL distribution registered",
        },
        "linux_isolation": {
            # runsc/gVisor cannot be demonstrated here: no root, no Linux runtime install.
            "status": "unavailable",
            "detail": "no runsc/gVisor runtime could be started and verified; "
            "Windows/WSL results may not be recorded as isolation acceptance",
        },
        "gpu": {
            "status": "available" if gpu else "unavailable",
            "detail": gpu_info.get("name") or "no nvidia-smi GPU reported",
            "vram_gib": gpu_info.get("vram_gib"),
            "driver": gpu_info.get("driver"),
        },
    }


def _parse_gpu(text: str) -> dict[str, Any]:
    """Parse `nvidia-smi --query-gpu=name,memory.total,driver_version`.

    Example: ``NVIDIA GeForce RTX 3050 Laptop GPU, 4096 MiB, 546.33``
    """
    if not text or "," not in text:
        return {}
    parts = [part.strip() for part in text.splitlines()[0].split(",")]
    info: dict[str, Any] = {"name": parts[0]}
    for part in parts[1:]:
        if "MiB" in part:
            try:
                info["vram_gib"] = round(int(part.split()[0]) / 1024, 1)
            except ValueError:
                pass
        elif part:
            info["driver"] = part
    return info


def _blockers(capabilities: dict[str, Any]) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    if capabilities["linux_isolation"]["status"] != "available":
        blockers.append(
            {
                "id": "linux_isolation",
                "reason": "no Linux host with an installable sandbox runtime (runsc/gVisor) "
                "and root privileges was available for this probe",
                "unblocks": "P03 sandbox tickets (T006/T010/T014) and F13B isolation acceptance",
            }
        )
    if capabilities["gpu"]["status"] != "available":
        blockers.append(
            {
                "id": "gpu",
                "reason": "no GPU reported by nvidia-smi; LoRA training experiments cannot run",
                "unblocks": "F14 training tickets",
            }
        )
    present = [name for name in _CREDENTIAL_ENV_VARS if os.environ.get(name)]
    if not present:
        blockers.append(
            {
                "id": "model_credentials",
                "reason": "no approved model-provider credentials present in the environment; "
                "only stub-model paths may be reported as executed",
                "unblocks": "real-model profiles and provider-capacity experiments",
            }
        )
    if capabilities["postgres"]["status"] != "available":
        blockers.append(
            {
                "id": "postgres_server",
                "reason": "no reachable PostgreSQL server; storage tickets cannot be demonstrated",
                "unblocks": "P02 tickets",
            }
        )
    if capabilities["container_runtime"]["status"] != "available":
        blockers.append(
            {
                "id": "container_runtime",
                "reason": "no container runtime available; compose-based deployment is not verifiable",
                "unblocks": "F15A/F15B deployment tickets",
            }
        )
    return blockers


# --------------------------------------------------------------------------
# report assembly
# --------------------------------------------------------------------------
def default_toolchain_dir() -> Path:
    """Where user-space toolchains live.

    Deliberately *outside* the repository: the spec validator walks every
    Markdown file in the tree and resolves relative links, so a toolchain
    dropped inside the repo makes the contract validation fail with link
    errors that have nothing to do with the specification.
    """
    override = os.environ.get("ORCHARA_TOOLCHAINS")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "orchara-toolchains"


def build_report(toolchain_dir: Path | None, out_dir: Path) -> dict[str, Any]:
    """Probe the environment and write JSON + Markdown artifacts."""
    tools = _candidate_tool_paths(toolchain_dir)
    services = [_probe_service(name, port) for name, port in _SERVICE_PORTS]
    capabilities = _classify(tools, services)
    blockers = _blockers(capabilities)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task": "D00 / T001",
        "host": _host_facts(),
        "tools": tools,
        "services": services,
        "capabilities": capabilities,
        "blockers": blockers,
        "credential_env_present": sorted(
            name for name in _CREDENTIAL_ENV_VARS if os.environ.get(name)
        ),
        "probe_commands": {
            "note": "each tool entry records the exact command that produced its version",
            "rerun": "python product/tools/probe_environment.py --out-dir product/reports/p0",
        },
        "artifacts": {},
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "environment-probe.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path = out_dir / "environment-probe.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    report["artifacts"] = {"json": str(json_path), "markdown": str(md_path)}
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# D00 环境探测报告（自动生成）")
    lines.append("")
    lines.append(f"- schema: `{report['schema_version']}`")
    lines.append(f"- 生成时间(UTC): `{report['generated_at']}`")
    lines.append(f"- 任务: {report['task']}")
    lines.append("")
    lines.append("## 宿主")
    lines.append("")
    host = report["host"]
    lines.append(f"- OS: {host['os']} {host['release']} ({host['machine']})")
    lines.append(f"- CPU: {host['cpu_count']} cores, 内存: {host['memory_gib']} GiB")
    lines.append(f"- Python: {host['python_version']} @ `{host['python_executable']}`")
    lines.append("")
    lines.append("## 工具（每项含探测命令）")
    lines.append("")
    lines.append("| 工具 | 可用 | 版本/错误 | 探测命令 |")
    lines.append("|---|---|---|---|")
    for tool in report["tools"]:
        state = "yes" if tool["available"] else "NO"
        detail = tool["version"] if tool["available"] else tool["error"]
        detail = (detail or "").replace("|", "\\|")
        lines.append(f"| {tool['name']} | {state} | {detail} | `{tool['command']}` |")
    lines.append("")
    lines.append("## 服务可达性")
    lines.append("")
    lines.append("| 服务 | 端口 | 可达 | 探测命令 |")
    lines.append("|---|---|---|---|")
    for svc in report["services"]:
        lines.append(
            f"| {svc['name']} | {svc['port']} | {'yes' if svc['reachable'] else 'NO'} "
            f"| `{svc['probe']}` |"
        )
    lines.append("")
    lines.append("## 能力分类")
    lines.append("")
    for name, value in report["capabilities"].items():
        lines.append(f"- **{name}**: `{value['status']}` — {value['detail']}")
    lines.append("")
    lines.append("## 阻塞项")
    lines.append("")
    if report["blockers"]:
        lines.append("| id | 原因 | 解除后可推进 |")
        lines.append("|---|---|---|")
        for blocker in report["blockers"]:
            lines.append(
                f"| `{blocker['id']}` | {blocker['reason']} | {blocker['unblocks']} |"
            )
    else:
        lines.append("无。")
    lines.append("")
    lines.append("> 静态校验通过不等于平台可运行；本报告只记录探针事实，不改变 P0 状态。")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D00/T001 environment probe")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "reports" / "p0",
        help="directory for environment-probe.json/.md",
    )
    parser.add_argument(
        "--toolchain-dir",
        type=Path,
        default=default_toolchain_dir(),
        help="user-space toolchain directory to look in before PATH",
    )
    parser.add_argument("--print", action="store_true", help="print the rendered Markdown")
    args = parser.parse_args(argv)

    report = build_report(args.toolchain_dir, args.out_dir)
    if args.print:
        print(render_markdown(report))
    else:
        print(f"wrote {report['artifacts']['json']}")
        print(f"wrote {report['artifacts']['markdown']}")
        print(f"blockers: {', '.join(b['id'] for b in report['blockers']) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
