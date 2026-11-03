#!/usr/bin/env python3
"""F05/T033: platform-owned isolation probe for the tool-debug sandbox.

This file is *not* a user script and never comes from a request. ``sandbox_exec.sh
probe`` copies it into the bundle read-only under ``/tool/probe.py`` and runs it
with the same rootfs, mounts, network namespace and limits as the preset sales
tool. It therefore answers, with real syscalls in the real sandbox:

* which identity the process has (uid/gid/hostname);
* whether it can read the host's ``/etc/hostname`` (it must not);
* whether it can read a foreign file staged outside the bundle (it must not);
* whether it can write the read-only input mount (it must not);
* whether it can write its own output directory (it must);
* whether it can open a network connection (it must not).

It is a probe entry, not the product execution path: the product path runs the
fixed preset tool (``worker/platform_worker/nodes/sales_stats.py``) with the same
bundle. ``slow``/``crash`` exist so a test can prove timeout and failure cleanup
without pretending the preset tool crashed.

Modes:
  isolation   emit the isolation evidence document (exit 0)
  slow        sleep --seconds (default 60), then exit 0
  crash       exit 7 without writing output
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys

FOREIGN_PATHS = (
    # A file staged by the driver next to (never inside) the bundle.
    "/var/tmp/orchara-sandbox/foreign/secret.txt",
    "/work/tenant-b/input.csv",
)
HOST_FILES = ("/etc/hostname", "/etc/shadow")
# /proc is a fresh PID namespace, so /proc/1/cmdline is this probe, never a host
# process; the probe reports it separately as the namespace's own identity.
PID_NAMESPACE_FILE = "/proc/1/cmdline"
INPUT_FILES = ("/inputs/input.csv", "/inputs/input.xlsx")
NETWORK_TARGETS = (("1.1.1.1", 443), ("127.0.0.1", 18080))


def _read(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return {"path": path, "read": True, "content_head": handle.read(64)}
    except OSError as error:
        return {"path": path, "read": False, "error": error.__class__.__name__}


def _write(path: str, payload: bytes) -> dict:
    try:
        with open(path, "wb") as handle:
            handle.write(payload)
        return {"path": path, "wrote": True}
    except OSError as error:
        return {"path": path, "wrote": False, "error": error.__class__.__name__}


def _connect(host: str, port: int) -> dict:
    try:
        with socket.create_connection((host, port), timeout=2):
            return {"target": host + ":" + str(port), "connected": True}
    except OSError as error:
        return {"target": host + ":" + str(port), "connected": False, "error": error.__class__.__name__}


def isolation() -> dict:
    document = {
        "probe": "orchara.sandbox-isolation/v1",
        "uid": os.getuid(),
        "gid": os.getgid(),
        "hostname": socket.gethostname(),
        "host_files": [_read(path) for path in HOST_FILES],
        "pid_namespace_pid1": _read(PID_NAMESPACE_FILE),
        "foreign_files": [_read(path) for path in FOREIGN_PATHS],
        "input_writes": [
            _write(path, b"x") for path in INPUT_FILES if os.path.exists(path)
        ],
        "tool_readable": _read("/tool/probe.py")["read"],
        "network": [_connect(host, port) for host, port in NETWORK_TARGETS],
        "workspace_write": _write("/workspace/probe-output.txt", b"probe-output\n"),
    }
    # The output directory is the only writable mount; writing there is the
    # control that proves the probe itself can persist its evidence.
    document["workspace_readable"] = _read("/workspace/probe-output.txt")["read"]
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="isolation_probe.py")
    parser.add_argument("--mode", choices=("isolation", "slow", "crash"), required=True)
    parser.add_argument("--seconds", type=int, default=60)
    arguments = parser.parse_args(argv)
    if arguments.mode == "slow":
        import time

        time.sleep(max(1, arguments.seconds))
        return 0
    if arguments.mode == "crash":
        print("sandbox probe: deliberate crash", file=sys.stderr)
        return 7
    document = isolation()
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with open("/workspace/probe.json", "w", encoding="utf-8") as handle:
            handle.write(payload)
    except OSError:
        pass
    sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
