#!/usr/bin/env python3
"""F05/T033 + F13B/T111: platform-owned isolation probe for the tool-debug sandbox.

This file is *not* a user script and never comes from a request. ``sandbox_exec.sh
probe`` copies it into the bundle read-only under ``/tool/probe.py`` and runs it
with the same rootfs, mounts, network namespace and limits as the preset sales
tool. It therefore answers, with real syscalls in the real sandbox:

* which identity the process has (uid/gid/hostname, capabilities, NoNewPrivs);
* whether it can read host files, host mount paths (``/mnt/...``), another Run's
  workspace staged outside the bundle and PID 1's environment (it must not);
* whether the readable environment contains deployment credentials (it must not);
* whether the mount table contains anything but the approved mounts, and which
  of them are read-only (``/tool``, ``/inputs``) versus writable (``/workspace``);
* whether it can write the read-only rootfs/tool/input mounts (it must not) while
  its own output directory accepts writes (it must);
* whether mounting, creating a user namespace, taking a capability, becoming
  root or clearing no_new_privs is refused (the runtime must refuse);
* whether it can open a network connection (it must not).

F13B/T111 extends the T033 document with those additional observations. The
extension is **observation only**: the bundle's uid, capabilities, namespaces,
mounts, argv and limits are built by ``internal/sandbox`` and are untouched.

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
import ctypes
import errno
import json
import os
import socket
import sys

#: A file staged by the driver next to (never inside) the bundle, another Run's
#: workspace area and the legacy P03 tenant path.
FOREIGN_PATHS = (
    "/var/tmp/orchara-sandbox/foreign/secret.txt",
    "/var/tmp/orchara-sandbox/other-run/secret.txt",
    "/work/tenant-b/input.csv",
)
HOST_FILES = (
    # The rootfs has an empty /etc on purpose: none of these exist inside it.
    "/etc/hostname",
    "/etc/shadow",
    "/etc/passwd",
    "/etc/os-release",
    "/root/.ssh/id_rsa",
    "/var/lib/postgresql/.pgpass",
)
# WSL mounts the Windows drives under /mnt; the bundle mounts none of them.
HOST_MOUNT_PATHS = (
    "/mnt/c/Windows/System32/drivers/etc/hosts",
    "/mnt/wsl",
    "/mnt/e/Orchara/product/contracts/database.sql",
)
# /proc is a fresh PID namespace, so /proc/1/environ is this probe's own OCI
# environment, never a host process; the probe reports it separately.
PID_NAMESPACE_FILE = "/proc/1/environ"
INPUT_FILES = ("/inputs/input.csv", "/inputs/input.xlsx", "/inputs/source.zip")
ROOTFS_WRITE_FILES = ("/usr/bin/python3.12", "/etc/passwd", "/etc/hostname")
TOOL_WRITE_FILES = ("/tool/probe.py", "/tool/build_tool.py")
NETWORK_TARGETS = (
    ("1.1.1.1", 443),
    ("8.8.8.8", 53),
    ("127.0.0.1", 18080),
    ("169.254.169.254", 80),
)

#: The five variables the OCI bundle builder sets; any credential-looking name
#: outside this set (or any ORCHARA_/PG/DSN/... prefix) is a deployment leak.
OCI_ENV_NAMES = frozenset(
    {
        "PATH",
        "PYTHONHOME",
        "PYTHONNOUSERSITE",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED",
    }
)
BENIGN_ENV_NAMES = frozenset({"HOSTNAME", "HOME", "PWD", "TMPDIR", "LANG", "LC_CTYPE", "TZ"})
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


def _error_fields(error: OSError) -> dict:
    number = error.errno if error.errno is not None else 0
    return {
        "error": error.__class__.__name__,
        "errno": number,
        "errno_name": errno.errorcode.get(number, str(number)),
    }


def _read(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return {"path": path, "read": True, "content_head": handle.read(64)}
    except OSError as error:
        return {"path": path, "read": False, **_error_fields(error)}


def _write(path: str, payload: bytes) -> dict:
    try:
        with open(path, "wb") as handle:
            handle.write(payload)
        return {"path": path, "wrote": True}
    except OSError as error:
        return {"path": path, "wrote": False, **_error_fields(error)}


def _connect(host: str, port: int) -> dict:
    try:
        with socket.create_connection((host, port), timeout=2):
            return {"target": host + ":" + str(port), "connected": True}
    except OSError as error:
        return {
            "target": host + ":" + str(port),
            "connected": False,
            **_error_fields(error),
        }


def _environment_names(names) -> dict:
    return {
        "names": sorted(names),
        "credential_prefixed": [
            name for name in sorted(names) if name.upper().startswith(CREDENTIAL_ENV_PREFIXES)
        ],
        "outside_oci_allowlist": [
            name
            for name in sorted(names)
            if name not in OCI_ENV_NAMES and name not in BENIGN_ENV_NAMES
        ],
    }


def _proc1_environ() -> dict:
    try:
        with open(PID_NAMESPACE_FILE, "rb") as handle:
            payload = handle.read(1 << 20)
    except OSError as error:
        return {"path": PID_NAMESPACE_FILE, "read": False, "error": error.__class__.__name__}
    names = []
    for entry in payload.split(b"\0"):
        if b"=" in entry:
            names.append(entry.split(b"=", 1)[0].decode("utf-8", "replace"))
    document = _environment_names(names)
    document.update({"path": PID_NAMESPACE_FILE, "read": True, "size_bytes": len(payload)})
    return document


def _status_fields() -> dict:
    wanted = {
        "Uid",
        "Gid",
        "CapInh",
        "CapPrm",
        "CapEff",
        "CapBnd",
        "CapAmb",
        "NoNewPrivs",
        "Seccomp",
    }
    fields: dict[str, str] = {}
    try:
        with open("/proc/self/status", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                name, _, value = line.partition(":")
                if name in wanted:
                    fields[name] = value.strip()
    except OSError:
        return fields
    return fields


def _status_keys() -> list[str]:
    """Every /proc/self/status field the sandbox runtime actually exposes.

    gVisor does not expose every host field (e.g. NoNewPrivs may be absent), so
    the probe records the real key set instead of assuming a Linux layout.
    """
    names: list[str] = []
    try:
        with open("/proc/self/status", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                name = line.partition(":")[0].strip()
                if name:
                    names.append(name)
    except OSError:
        return names
    return names


def _no_new_privs() -> dict:
    """PR_GET_NO_NEW_PRIVS (39): the runtime's own answer, not a status guess."""
    try:
        library = _libc()
        result = library.prctl(39, 0, 0, 0, 0)
    except BaseException as error:  # noqa: BLE001 - observation only
        return {"supported": False, "error_class": error.__class__.__name__}
    if result < 0:
        return {
            "supported": False,
            "errno": ctypes.get_errno(),
            "errno_name": errno.errorcode.get(ctypes.get_errno(), ""),
        }
    return {"supported": True, "value": int(result)}


def _unescape_mount_field(value: str) -> str:
    replacements = {"\\040": " ", "\\011": "\t", "\\012": "\n", "\\134": "\\"}
    for encoded, decoded in replacements.items():
        value = value.replace(encoded, decoded)
    return value


def _mounts() -> list[dict]:
    entries: list[dict] = []
    try:
        with open("/proc/self/mountinfo", "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return entries
    for line in lines:
        try:
            before, after = line.rstrip("\n").split(" - ", 1)
            pre = before.split(" ")
            post = after.split(" ")
            entries.append(
                {
                    "destination": _unescape_mount_field(pre[4]),
                    "options": pre[5],
                    "fstype": post[0],
                    "source": _unescape_mount_field(post[1]) if len(post) > 1 else "",
                    "super_options": post[2] if len(post) > 2 else "",
                }
            )
        except (ValueError, IndexError):
            continue
    return entries


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def _attempt(name: str, callback) -> dict:
    try:
        callback()
    except OSError as error:
        number = error.errno if error.errno is not None else 0
        return {
            "attempt": name,
            "denied": True,
            "errno": number,
            "errno_name": errno.errorcode.get(number, str(number)),
            "error_class": error.__class__.__name__,
        }
    except BaseException as error:  # noqa: BLE001 - a failed escalation must be reported
        return {
            "attempt": name,
            "denied": False,
            "result": "unexpected_error",
            "error_class": error.__class__.__name__,
        }
    return {"attempt": name, "denied": False, "result": "succeeded"}


def _mount_tmpfs() -> None:
    library = _libc()
    os.makedirs("/tmp/t111-mount", exist_ok=True)
    result = library.mount(b"tmpfs", b"/tmp/t111-mount", b"tmpfs", 0, None)
    if result != 0:
        raise OSError(ctypes.get_errno(), "mount")


def _mount_namespace() -> None:
    library = _libc()
    result = library.unshare(0x00020000)  # CLONE_NEWNS without a user namespace
    if result != 0:
        raise OSError(ctypes.get_errno(), "unshare")


def _unshare_user_namespace() -> None:
    library = _libc()
    # CLONE_NEWUSER | CLONE_NEWNS
    result = library.unshare(0x10000000 | 0x00020000)
    if result != 0:
        raise OSError(ctypes.get_errno(), "unshare")


def _clear_no_new_privs() -> None:
    library = _libc()
    result = library.prctl(38, 0, 0, 0, 0)  # PR_SET_NO_NEW_PRIVS = 38
    if result != 0:
        raise OSError(ctypes.get_errno(), "prctl")


class _CapHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _CapData(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


def _capset_sys_admin() -> None:
    library = _libc()
    library.syscall.restype = ctypes.c_long
    header = _CapHeader(0x20080522, 0)
    data = (_CapData * 2)()
    data[0].effective = 1 << 21  # CAP_SYS_ADMIN
    data[0].permitted = 1 << 21
    result = library.syscall(126, ctypes.byref(header), ctypes.byref(data))  # SYS_capset
    if result != 0:
        raise OSError(ctypes.get_errno(), "capset")


def _escalation() -> dict:
    """Every privilege/namespace escalation attempt with the runtime's answer.

    ``attempts`` are the operations that must be refused. The user-namespace
    attempt is recorded separately because gVisor may allow an *unprivileged*
    user namespace: that is not a host privilege (there is no host mount and the
    bundle has no capabilities), so when it succeeds the probe also records what
    the new namespace can and cannot reach.
    """
    attempts = [
        _attempt("setuid_root", lambda: os.setuid(0)),
        _attempt("setgid_root", lambda: os.setgid(0)),
        _attempt("mount_tmpfs", _mount_tmpfs),
        _attempt("unshare_mount_namespace", _mount_namespace),
        _attempt("capset_sys_admin", _capset_sys_admin),
        _attempt("clear_no_new_privs", _clear_no_new_privs),
    ]
    unshare = _attempt("unshare_user_namespace", _unshare_user_namespace)
    after = None
    if not unshare["denied"]:
        after = {
            "uid": os.getuid(),
            "gid": os.getgid(),
            "mount_tmpfs": _attempt("mount_tmpfs_after_unshare", _mount_tmpfs),
            "host_reads": [
                _read(path)
                for path in (
                    "/etc/passwd",
                    "/mnt/c/Windows/System32/drivers/etc/hosts",
                    "/var/tmp/orchara-sandbox/other-run/secret.txt",
                )
            ],
            "workspace_write": _write("/workspace/userns-output.txt", b"userns\n"),
            # Last: it may change the effective uid inside the new namespace.
            "setuid_root": _attempt("setuid_root_after_unshare", lambda: os.setuid(0)),
        }
    return {"attempts": attempts, "user_namespace": {"unshare": unshare, "after": after}}


def isolation() -> dict:
    # The mount table used for the policy assertion is read BEFORE any escalation
    # attempt: a permitted user namespace can add a sandbox-local mount afterwards,
    # which is recorded separately rather than polluting the policy evidence.
    mounts_before = _mounts()
    privileges = {
        "status": _status_fields(),
        "status_keys": _status_keys(),
        "no_new_privs": _no_new_privs(),
        "escalation": _escalation(),
    }
    document = {
        "probe": "orchara.sandbox-isolation/v1",
        # F13B/T111 adds observation fields only; the v1 schema stays additive so
        # the T033 consumer keeps working unchanged.
        "probe_extensions": ["f13b.t111"],
        "uid": os.getuid(),
        "gid": os.getgid(),
        "hostname": socket.gethostname(),
        "host_files": [_read(path) for path in HOST_FILES],
        "host_mount_paths": [_read(path) for path in HOST_MOUNT_PATHS],
        "pid_namespace_pid1": _read("/proc/1/cmdline"),
        "foreign_files": [_read(path) for path in FOREIGN_PATHS],
        "input_writes": [
            _write(path, b"x") for path in INPUT_FILES if os.path.exists(path)
        ],
        "rootfs_writes": [_write(path, b"x") for path in ROOTFS_WRITE_FILES],
        "tool_writes": [_write(path, b"x") for path in TOOL_WRITE_FILES],
        "tool_readable": _read("/tool/probe.py")["read"],
        "network": [_connect(host, port) for host, port in NETWORK_TARGETS],
        "workspace_write": _write("/workspace/probe-output.txt", b"probe-output\n"),
        "environment": _environment_names(os.environ),
        "proc1_environ": _proc1_environ(),
        "privileges": privileges,
        "mounts": mounts_before,
        "mounts_after_escalation": _mounts(),
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
