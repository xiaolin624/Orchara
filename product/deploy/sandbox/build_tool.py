#!/usr/bin/env python3.12
"""F05/T037: the sandboxed tool build.

This script is checked in, mounted read-only at /tool/build_tool.py and executed
by deploy/sandbox/sandbox_exec.sh inside the same minimal gVisor (runsc) bundle
T033 uses for tool debug. It is the *only* program a tool_build may run; no
request value reaches its argv.

What it does, all inside the sandbox (read-only rootfs/tool/inputs, writable
/workspace, no network, uid/gid 65534):

  1. reads the submitted source zip and the canonical dependency lock;
  2. safely extracts the zip under /workspace/work/src, repeating every
     admission check against the real bytes (entry count, total uncompressed
     bytes, compression ratio, absolute/`..`/backslash paths, symlinks,
     encrypted entries, unsupported compression, duplicates);
  3. refuses a non-empty dependency declaration with the safe
     `dependencies_unavailable` outcome: this minimal rootfs deliberately has no
     pip, no wheelhouse and no compiler, so a lock that needs packages cannot be
     installed and must never be reported as a fake success (F15A owns image
     builds with a locked wheelhouse);
  4. if the source has a setup.py, runs the untrusted install hook as
     `python setup.py build` in a subprocess with a stripped environment. This is
     the untrusted code SECURITY §2 warns about: it runs here, in the sandbox,
     never on the host;
  5. syntax-compiles every .py file (no execution) and produces:
        /workspace/artifacts/sbom.json   (file manifest + hashes, no deps)
        /workspace/artifacts/build_log   (capped, safe build log)
        /workspace/build.json            (machine-readable outcome)
  6. exits 0 on success and 3 on a business build failure. A crash/usage error is
     nonzero too; the Go handler requires a parseable build.json for a success.

The platform-side guard re-reads the sealed source and SBOM bytes and computes
the authoritative bundle digest. This script reports facts; it cannot make a
tool ready.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile

BUILD_VERSION = 1
MAX_ENTRIES = 512
MAX_UNCOMPRESSED = 64 << 20
MAX_RATIO = 100
MAX_LOG_BYTES = 256 * 1024
MAX_FILES_IN_DOCUMENT = 4096
SETUP_TIMEOUT_SECONDS = 180
ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
DEPENDENCY_KEYS = ("requirements", "packages", "dependencies")


class BuildFailure(Exception):
    """A safe, diagnosable business failure (never a traceback body)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def log_line(lines: list[str], text: str) -> None:
    lines.append(text)
    if len(lines) > 4000:
        del lines[:2000]


def capped_log(lines: list[str]) -> bytes:
    text = "\n".join(lines) + "\n"
    encoded = text.encode("utf-8", "replace")
    if len(encoded) > MAX_LOG_BYTES:
        encoded = encoded[-MAX_LOG_BYTES:]
        encoded = b"[log truncated: showing the tail]\n" + encoded
    return encoded


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def safe_name(name: str) -> bool:
    if not name or "\x00" in name or "\\" in name:
        return False
    if name.startswith("/"):
        return False
    trimmed = name[:-1] if name.endswith("/") else name
    if not trimmed:
        return False
    parts = trimmed.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return True


def read_lock(payload: bytes) -> dict:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BuildFailure("build_source_invalid", "dependency lock is not valid JSON") from error
    if not isinstance(value, dict):
        raise BuildFailure("build_source_invalid", "dependency lock must be a JSON object")
    return value


def declared_dependencies(lock: dict) -> list[str]:
    names: list[str] = []
    for key in DEPENDENCY_KEYS:
        value = lock.get(key)
        if value is None:
            continue
        if isinstance(value, list) and value:
            names.append(key)
        elif isinstance(value, dict) and value:
            names.append(key)
        elif isinstance(value, (str, int, float)) and str(value).strip():
            names.append(key)
    return names


def extract_source(payload: bytes, destination: str, lines: list[str]) -> list[dict]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as error:
        raise BuildFailure("build_source_invalid", "the source artifact is not a zip archive") from error

    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            raise BuildFailure("build_source_invalid", "the source archive has too many entries")
        total_expected = 0
        total_compressed = 0
        regular = 0
        seen: set[str] = set()
        for entry in entries:
            if not safe_name(entry.filename):
                raise BuildFailure("build_source_invalid", "the source archive contains an unsafe path")
            if entry.flag_bits & 0x1:
                raise BuildFailure("build_source_invalid", "the source archive contains an encrypted entry")
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise BuildFailure("build_source_invalid", "the source archive contains a symlink")
            if entry.compress_type not in ALLOWED_COMPRESSION:
                raise BuildFailure("build_source_invalid", "the source archive uses an unsupported compression")
            if entry.is_dir():
                continue
            name = entry.filename
            if name in seen:
                raise BuildFailure("build_source_invalid", "the source archive contains duplicate paths")
            seen.add(name)
            total_expected += entry.file_size
            total_compressed += entry.compress_size
            if total_expected > MAX_UNCOMPRESSED:
                raise BuildFailure("build_source_invalid", "the source archive exceeds the uncompressed limit")
            regular += 1
            target = os.path.realpath(os.path.join(destination, name))
            if not target.startswith(os.path.realpath(destination) + os.sep):
                raise BuildFailure("build_source_invalid", "the source archive escapes the work directory")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            written = 0
            try:
                with archive.open(entry, "r") as reader:
                    with open(target, "xb") as handle:
                        while True:
                            chunk = reader.read(1 << 16)
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > MAX_UNCOMPRESSED:
                                raise BuildFailure(
                                    "build_source_invalid", "the source archive exceeds the uncompressed limit"
                                )
                            handle.write(chunk)
            except FileExistsError as error:
                raise BuildFailure("build_source_invalid", "the source archive contains duplicate paths") from error
            except (zipfile.BadZipFile, OSError) as error:
                raise BuildFailure("build_source_invalid", "the source archive could not be extracted") from error
            os.chmod(target, 0o644)
            if written != entry.file_size:
                # A header that lies about its size is not a source bundle this
                # build can trust.
                raise BuildFailure("build_source_invalid", "the source archive size does not match its headers")
        if regular == 0:
            raise BuildFailure("build_source_invalid", "the source archive contains no files")
        if total_expected and (total_compressed <= 0 or total_expected // total_compressed > MAX_RATIO):
            raise BuildFailure("build_source_invalid", "the source archive compression ratio is unsafe")
    log_line(lines, f"extracted {regular} source files")
    return source_manifest(destination)


def source_manifest(root: str) -> list[dict]:
    files: list[dict] = []
    for directory, _dirs, names in os.walk(root):
        for name in sorted(names):
            path = os.path.join(directory, name)
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                with open(path, "rb") as handle:
                    payload = handle.read(MAX_UNCOMPRESSED + 1)
            except OSError as error:
                raise BuildFailure("build_source_invalid", "a source file could not be read") from error
            if len(payload) > MAX_UNCOMPRESSED:
                raise BuildFailure("build_source_invalid", "a source file exceeds the size limit")
            files.append(
                {"path": relative, "size_bytes": len(payload), "sha256": sha256_hex(payload)}
            )
            if len(files) > MAX_FILES_IN_DOCUMENT:
                raise BuildFailure("build_source_invalid", "the source archive has too many files")
    files.sort(key=lambda item: item["path"])
    return files


def compile_sources(root: str, files: list[dict], lines: list[str]) -> None:
    for item in files:
        if not item["path"].endswith(".py"):
            continue
        path = os.path.join(root, item["path"])
        try:
            with open(path, "rb") as handle:
                source = handle.read()
            compile(source, item["path"], "exec")
        except (OSError, SyntaxError, ValueError) as error:
            message = getattr(error, "msg", None) or error.__class__.__name__
            raise BuildFailure("build_failed", f"Python source {item['path']} is invalid: {message}") from error
    log_line(lines, "syntax-compiled every .py source file")


def run_setup_hook(root: str, lines: list[str]) -> None:
    setup = os.path.join(root, "setup.py")
    if not os.path.isfile(setup):
        log_line(lines, "no setup.py present; no install/build hook to run")
        return
    work = os.path.dirname(root)
    home = os.path.join(work, "hook-home")
    tmp = os.path.join(work, "hook-tmp")
    os.makedirs(home, exist_ok=True)
    os.makedirs(tmp, exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": home,
        "TMPDIR": tmp,
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "LANG": "C",
        "LC_ALL": "C",
    }
    log_line(lines, "running the untrusted setup.py build hook in the sandbox")
    try:
        completed = subprocess.run(
            [sys.executable, "setup.py", "build"],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=SETUP_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        output = (error.output or b"")[-MAX_LOG_BYTES:]
        log_line(lines, "setup.py build timed out")
        log_line(lines, output.decode("utf-8", "replace"))
        raise BuildFailure("build_failed", "the setup.py build hook timed out") from error
    output = (completed.stdout or b"")[-MAX_LOG_BYTES:]
    log_line(lines, output.decode("utf-8", "replace"))
    if completed.returncode != 0:
        raise BuildFailure(
            "build_failed", f"the setup.py build hook exited with code {completed.returncode}"
        )
    log_line(lines, "setup.py build hook completed")


def write_outputs(
    root: str,
    files: list[dict],
    *,
    status: str,
    error_code: str,
    message: str,
    source_sha: str,
    lock_sha: str,
    lines: list[str],
    artifacts: str,
) -> None:
    sbom = {
        "sbom_version": 1,
        "generator": "orchara.tool-build/v1",
        "source_sha256": source_sha,
        "dependency_lock_sha256": lock_sha,
        "python": {"version": sys.version.split()[0], "implementation": sys.implementation.name},
        "dependencies": [],
        "files": files,
    }
    os.makedirs(artifacts, exist_ok=True)
    with open(os.path.join(artifacts, "sbom.json"), "w", encoding="utf-8") as handle:
        json.dump(sbom, handle, indent=2, sort_keys=True)
        handle.write("\n")
    log_line(lines, f"build status={status} error_code={error_code or 'none'}")
    with open(os.path.join(artifacts, "build_log"), "wb") as handle:
        handle.write(capped_log(lines))


def write_document(
    document_path: str,
    *,
    status: str,
    error_code: str,
    message: str,
    source_sha: str,
    lock_sha: str,
    files: list[dict],
    lines: list[str],
) -> None:
    document = {
        "build_version": BUILD_VERSION,
        "status": status,
        "error_code": error_code,
        "message": message,
        "source_sha256": source_sha,
        "dependency_lock_sha256": lock_sha,
        "file_count": len(files),
        "files": files[:MAX_FILES_IN_DOCUMENT],
        "log_tail": "\n".join(lines[-32:]),
    }
    with open(document_path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")


def parse_arguments(argv: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    index = 0
    flags = {
        "--source": "source_path",
        "--lock": "lock_path",
        "--output": "output_path",
        "--log": "log_path",
        "--artifacts-dir": "artifacts_dir",
        "--work": "work_dir",
    }
    while index < len(argv):
        flag = argv[index]
        if flag not in flags or index + 1 >= len(argv):
            raise BuildFailure("build_source_invalid", "the build received an unknown or incomplete argument")
        values[flags[flag]] = argv[index + 1]
        index += 2
    for name in flags.values():
        if not values.get(name):
            raise BuildFailure("build_source_invalid", "the build arguments are incomplete")
    return values


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    source_path = arguments["source_path"]
    lock_path = arguments["lock_path"]
    output_path = arguments["output_path"]
    artifacts_dir = arguments["artifacts_dir"]
    work_dir = arguments["work_dir"]

    lines: list[str] = ["orchara tool build v1", "sandbox: gVisor/runsc, uid 65534, no network"]
    source_sha = ""
    lock_sha = ""
    try:
        with open(source_path, "rb") as handle:
            source = handle.read(MAX_UNCOMPRESSED + 1)
        if not source or len(source) > MAX_UNCOMPRESSED:
            raise BuildFailure("build_source_invalid", "the source artifact is empty or too large")
        with open(lock_path, "rb") as handle:
            lock_bytes = handle.read(1 << 20)
        source_sha = sha256_hex(source)
        lock_sha = sha256_hex(lock_bytes)
        lock = read_lock(lock_bytes)

        dependencies = declared_dependencies(lock)
        if dependencies:
            raise BuildFailure(
                "dependencies_unavailable",
                "the dependency lock declares packages but this isolated build has no local "
                "wheelhouse or package manager; install from a locked wheelhouse in the image build (F15A)",
            )

        os.makedirs(work_dir, exist_ok=True)
        root = os.path.join(work_dir, "src")
        if os.path.exists(root):
            shutil.rmtree(root)
        os.makedirs(root, exist_ok=True)
        files = extract_source(source, root, lines)
        compile_sources(root, files, lines)
        run_setup_hook(root, lines)
        write_outputs(
            root,
            files,
            status="ok",
            error_code="",
            message="build completed",
            source_sha=source_sha,
            lock_sha=lock_sha,
            lines=lines,
            artifacts=artifacts_dir,
        )
        write_document(
            output_path,
            status="ok",
            error_code="",
            message="build completed",
            source_sha=source_sha,
            lock_sha=lock_sha,
            files=files,
            lines=lines,
        )
        return 0
    except BuildFailure as failure:
        # A business build failure is a safe, diagnosable document: the handler
        # uploads the capped log and fails the job with this code. Never print a
        # traceback (it can contain sandbox paths, though no host content).
        lines.append(f"build failed: {failure.code}: {failure.message}")
        try:
            os.makedirs(artifacts_dir, exist_ok=True)
            with open(os.path.join(artifacts_dir, "build_log"), "wb") as handle:
                handle.write(capped_log(lines))
            write_document(
                output_path,
                status="failed",
                error_code=failure.code,
                message=failure.message,
                source_sha=source_sha,
                lock_sha=lock_sha,
                files=[],
                lines=lines,
            )
        except Exception:  # noqa: BLE001 - the exit code is the primary signal
            pass
        print(f"build failed: {failure.code}", file=sys.stderr)
        return 3
    except Exception as error:  # noqa: BLE001 - never leak a traceback as a log
        print(f"build crashed: {error.__class__.__name__}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
