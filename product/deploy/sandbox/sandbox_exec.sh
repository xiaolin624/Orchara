#!/usr/bin/env bash
# F05/T033: the tool-debug sandbox driver.
#
# One invocation stages an OCI bundle, runs the fixed program inside runsc
# (gVisor), collects the protocol outputs, then deletes the sandbox and verifies
# that neither a sandbox entry, a pid nor the workspace survived. It prints one
# JSON document; the host-side Go executor parses it and never trusts the
# script's own success for more than the facts it reports.
#
# The command executed in the sandbox is fixed here, in this checked-in file:
#
#   /usr/bin/python3.12 /tool/sales_stats.py --input /workspace/input.json \
#       --output /workspace/output.json --inputs-dir /inputs \
#       --artifacts-dir /workspace/artifacts
#
# No request value reaches the command line, the environment or a mount option:
# the arguments of this script are paths and enums chosen by the platform.
#
# Usage:
#   sandbox_exec.sh run     --sandbox-id ID --state-root DIR --rootfs DIR
#                           --tool PATH --input PATH --input-json PATH
#                           --input-name NAME --timeout SEC --memory-mb N
#                           --pids N --cpu-seconds N
#                           [--mode tool|probe|build] [--probe-mode isolation|slow|crash]
#                           [--probe-seconds N]
#   sandbox_exec.sh cleanup --sandbox-id ID --state-root DIR
set -uo pipefail

MODE=""
SANDBOX_ID=""
STATE_ROOT="/var/tmp/orchara-sandbox"
ROOTFS="/opt/orchara-sandbox/python-rootfs"
ROOTFS_DIGEST=""
TOOL=""
INPUT=""
INPUT_JSON=""
CONFIG=""
INPUT_NAME=""
TIMEOUT_SECONDS=60
MEMORY_MB=256
PIDS_LIMIT=64
CPU_SECONDS=30
WORKSPACE_QUOTA_MB=512
PROGRAM_MODE="tool"
PROBE_MODE="isolation"
PROBE_SECONDS=60

die() {
  python3 - "$1" <<'PY'
import json, sys
print(json.dumps({"ok": False, "error": sys.argv[1]}))
PY
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    run|cleanup) MODE="$1"; shift ;;
    --sandbox-id) SANDBOX_ID="${2:-}"; shift 2 ;;
    --state-root) STATE_ROOT="${2:-}"; shift 2 ;;
    --rootfs) ROOTFS="${2:-}"; shift 2 ;;
    --rootfs-digest) ROOTFS_DIGEST="${2:-}"; shift 2 ;;
    --tool) TOOL="${2:-}"; shift 2 ;;
    --input) INPUT="${2:-}"; shift 2 ;;
    --input-json) INPUT_JSON="${2:-}"; shift 2 ;;
    --config) CONFIG="${2:-}"; shift 2 ;;
    --input-name) INPUT_NAME="${2:-}"; shift 2 ;;
    --timeout) TIMEOUT_SECONDS="${2:-}"; shift 2 ;;
    --memory-mb) MEMORY_MB="${2:-}"; shift 2 ;;
    --pids) PIDS_LIMIT="${2:-}"; shift 2 ;;
    --cpu-seconds) CPU_SECONDS="${2:-}"; shift 2 ;;
    --workspace-quota-mb) WORKSPACE_QUOTA_MB="${2:-}"; shift 2 ;;
    --mode) PROGRAM_MODE="${2:-}"; shift 2 ;;
    --probe-mode) PROBE_MODE="${2:-}"; shift 2 ;;
    --probe-seconds) PROBE_SECONDS="${2:-}"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -n "$MODE" ] || die "a mode is required (run|cleanup)"
[[ "$SANDBOX_ID" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || die "invalid sandbox id"
STATE_ROOT="${STATE_ROOT%/}"
ROOTFS="${ROOTFS%/}"
WORKSPACE="$STATE_ROOT/$SANDBOX_ID"
PID_FILE="$WORKSPACE/runsc.pid"

RUNSC="${ORCHARA_SANDBOX_RUNSC:-runsc}"
command -v "$RUNSC" >/dev/null 2>&1 || die "runsc is not installed in the guest"

# ---------------------------------------------------------------------------
# cleanup: delete the sandbox, prove nothing survived, remove the workspace
# ---------------------------------------------------------------------------
cleanup() {
  "$RUNSC" --network=none delete --force "$SANDBOX_ID" >/dev/null 2>&1
  local list_matches pid=""
  list_matches="$("$RUNSC" --network=none list 2>/dev/null | grep -c -- "$SANDBOX_ID" || true)"
  if [ -f "$PID_FILE" ]; then
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  fi
  local pid_alive=false
  if [ -n "$pid" ] && [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
    sleep 0.2
    if kill -0 "$pid" 2>/dev/null; then pid_alive=true; fi
  fi
  local ps_matches
  ps_matches="$(ps -eo args 2>/dev/null | grep -c -E "[r]unsc.*$SANDBOX_ID" || true)"
  # SECURITY §2: the writable workspace is a size-limited tmpfs, never the host
  # volume. Unmount it before deleting the directory so the quota cannot leak
  # into the next run and `rm -rf` actually removes the staging tree.
  local workspace_unmounted=true
  if grep -q " $WORKSPACE/output " /proc/mounts 2>/dev/null; then
    umount "$WORKSPACE/output" 2>/dev/null || umount -l "$WORKSPACE/output" 2>/dev/null || true
    if grep -q " $WORKSPACE/output " /proc/mounts 2>/dev/null; then workspace_unmounted=false; fi
  fi
  local workspace_removed=false
  if [ "${KEEP_WORKSPACE:-false}" != "true" ]; then
    rm -rf "$WORKSPACE" 2>/dev/null || true
    if [ ! -e "$WORKSPACE" ]; then workspace_removed=true; fi
  fi
  # The decoy file staged outside the bundle is removed once the probe has run;
  # the state root then contains no leftover of this execution.
  rm -rf "$STATE_ROOT/foreign" 2>/dev/null || true
  CLEANUP_LIST="$list_matches"
  CLEANUP_PID_ALIVE="$pid_alive"
  CLEANUP_PS="$ps_matches"
  CLEANUP_WORKSPACE="$workspace_removed"
  CLEANUP_UNMOUNT="$workspace_unmounted"
  CLEANUP_PID="${pid:-0}"
}

emit_cleanup_only() {
  python3 - "$SANDBOX_ID" "$CLEANUP_LIST" "$CLEANUP_PID" "$CLEANUP_PID_ALIVE" "$CLEANUP_PS" "$CLEANUP_WORKSPACE" "$CLEANUP_UNMOUNT" "$WORKSPACE_QUOTA_MB" <<'PY'
import json, sys
(sandbox_id, list_matches, pid, pid_alive, ps_matches, workspace_removed,
 workspace_unmounted, quota_mb) = sys.argv[1:]
clean = (int(list_matches) == 0 and pid_alive == "false" and int(ps_matches) == 0
         and workspace_removed == "true" and workspace_unmounted == "true")
print(json.dumps({
    "ok": clean,
    "mode": "cleanup",
    "sandbox_id": sandbox_id,
    "workspace_quota_mb": int(quota_mb),
    "cleanup": {
        "sandbox_list_matches": int(list_matches),
        # A non-zero pid is the proof that a process was actually launched: the
        # pid file is written by runsc only after it starts the sandbox.
        "pid": int(pid) if pid.isdigit() else 0,
        "pid_alive": pid_alive == "true",
        "ps_matches": int(ps_matches),
        "workspace_removed": workspace_removed == "true",
        "workspace_unmounted": workspace_unmounted == "true",
    },
}))
PY
}

if [ "$MODE" = "cleanup" ]; then
  cleanup
  emit_cleanup_only
  exit 0
fi

# ---------------------------------------------------------------------------
# run: stage, execute, collect, clean up
# ---------------------------------------------------------------------------
[ -f "$ROOTFS" ] && die "rootfs is a file" 
[ -d "$ROOTFS" ] || die "rootfs is missing: $ROOTFS"
[ -f "$ROOTFS.digest" ] || die "rootfs digest is missing: $ROOTFS.digest"
[[ "$ROOTFS_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || die "the recorded rootfs digest is missing or invalid"
ACTUAL_DIGEST="$(tr -d '[:space:]' < "$ROOTFS.digest")"
if [ "$ACTUAL_DIGEST" != "$ROOTFS_DIGEST" ]; then
  die "rootfs digest mismatch: recorded=$ROOTFS_DIGEST actual=$ACTUAL_DIGEST"
fi
[ -f "$TOOL" ] || die "tool file is missing"
[ -f "$INPUT" ] || die "input file is missing"
[ -f "$INPUT_JSON" ] || die "input document is missing"
[[ "$INPUT_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,126}$ ]] || die "invalid input file name"
[[ "$TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || die "invalid timeout"
[[ "$MEMORY_MB" =~ ^[0-9]+$ ]] || die "invalid memory limit"
[[ "$PIDS_LIMIT" =~ ^[0-9]+$ ]] || die "invalid pids limit"
[[ "$CPU_SECONDS" =~ ^[0-9]+$ ]] || die "invalid cpu limit"
[[ "$WORKSPACE_QUOTA_MB" =~ ^[0-9]+$ ]] || die "invalid workspace quota"
if [ "$WORKSPACE_QUOTA_MB" -lt 1 ] || [ "$WORKSPACE_QUOTA_MB" -gt 4096 ]; then
  die "workspace quota must be between 1 and 4096 MiB"
fi
[[ "$PROGRAM_MODE" =~ ^(tool|probe|build)$ ]] || die "invalid program mode"
[[ "$PROBE_MODE" =~ ^(isolation|slow|crash)$ ]] || die "invalid probe mode"

mkdir -p "$STATE_ROOT/foreign" 2>/dev/null || true
printf 'foreign-secret\n' > "$STATE_ROOT/foreign/secret.txt" 2>/dev/null || true

rm -rf "$WORKSPACE"
mkdir -p "$WORKSPACE"/tool "$WORKSPACE"/inputs "$WORKSPACE"/output "$WORKSPACE"/bundle \
         "$WORKSPACE"/cwd
# SECURITY §2: the only writable mount is a size-limited tmpfs, not the host
# volume. A process that keeps writing hits ENOSPC at WORKSPACE_QUOTA_MB instead
# of filling the guest disk; RLIMIT_FSIZE additionally bounds each file.
mount -t tmpfs -o "size=${WORKSPACE_QUOTA_MB}m,nosuid,nodev,mode=0755" tmpfs "$WORKSPACE/output" \
  || die "cannot mount the quota-limited workspace"
mkdir -p "$WORKSPACE/output/artifacts"
if [ "$PROGRAM_MODE" = "tool" ]; then
  cp "$TOOL" "$WORKSPACE/tool/sales_stats.py"
  chmod 0444 "$WORKSPACE/tool/sales_stats.py"
elif [ "$PROGRAM_MODE" = "probe" ]; then
  cp "$TOOL" "$WORKSPACE/tool/probe.py"
  chmod 0444 "$WORKSPACE/tool/probe.py"
else
  # F05/T037: the checked-in build script. Its argv is fixed in the OCI config
  # built by internal/sandbox; this driver only stages the two read-only inputs.
  cp "$TOOL" "$WORKSPACE/tool/build_tool.py"
  chmod 0444 "$WORKSPACE/tool/build_tool.py"
fi
cp "$INPUT" "$WORKSPACE/inputs/$INPUT_NAME"
chmod 0444 "$WORKSPACE/inputs/$INPUT_NAME"
if [ "$PROGRAM_MODE" = "build" ]; then
  cp "$INPUT_JSON" "$WORKSPACE/inputs/lock.json"
  chmod 0444 "$WORKSPACE/inputs/lock.json"
else
  cp "$INPUT_JSON" "$WORKSPACE/output/input.json"
  chmod 0444 "$WORKSPACE/output/input.json"
fi
chown -R 65534:65534 "$WORKSPACE/output"
chmod 0755 "$WORKSPACE" "$WORKSPACE/inputs" "$WORKSPACE/tool" "$WORKSPACE/bundle" "$WORKSPACE/cwd"
chmod 0755 "$WORKSPACE/output" "$WORKSPACE/output/artifacts"

# The OCI config is built by internal/sandbox (Go) and staged next to the input;
# the driver never invents process args, users, namespaces or limits.
CONFIG_PATH="$WORKSPACE/bundle/config.json"
[ -f "$CONFIG" ] || die "the OCI config was not staged"
cp "$CONFIG" "$CONFIG_PATH"
[ -f "$CONFIG_PATH" ] || die "cannot stage the OCI config"

START_MS="$(date +%s%3N)"
( cd "$WORKSPACE/bundle" && timeout --kill-after=2 --signal=KILL "$TIMEOUT_SECONDS" \
    "$RUNSC" --network=none run --bundle "$WORKSPACE/bundle" \
    --pid-file "$PID_FILE" "$SANDBOX_ID" ) >"$WORKSPACE/stdout" 2>"$WORKSPACE/stderr"
EXIT_CODE=$?
END_MS="$(date +%s%3N)"
DURATION_MS="$((END_MS - START_MS))"
TIMED_OUT=false
if [ "$EXIT_CODE" = "124" ] || [ "$EXIT_CODE" = "137" ]; then TIMED_OUT=true; fi

# Copy the protocol outputs out of the workspace *before* cleanup, so deleting
# the workspace cannot hide what the sandbox actually wrote.
COLLECT="$(mktemp -d "$STATE_ROOT/.collect-$SANDBOX_ID.XXXXXX")"
mkdir -p "$COLLECT/artifacts"
[ -f "$WORKSPACE/output/output.json" ] && cp "$WORKSPACE/output/output.json" "$COLLECT/output.json"
[ -f "$WORKSPACE/output/probe.json" ] && cp "$WORKSPACE/output/probe.json" "$COLLECT/probe.json"
[ -f "$WORKSPACE/output/build.json" ] && cp "$WORKSPACE/output/build.json" "$COLLECT/output.json"
[ -f "$WORKSPACE/stdout" ] && cp "$WORKSPACE/stdout" "$COLLECT/stdout"
[ -f "$WORKSPACE/stderr" ] && cp "$WORKSPACE/stderr" "$COLLECT/stderr"
if [ -d "$WORKSPACE/output/artifacts" ]; then
  find "$WORKSPACE/output/artifacts" -maxdepth 1 -type f ! -type l -print0 2>/dev/null |
    while IFS= read -r -d '' file; do cp "$file" "$COLLECT/artifacts/$(basename "$file")" 2>/dev/null || true; done
fi

cleanup

OUTPUT_JSON="$COLLECT/output.json"
PROBE_JSON="$COLLECT/probe.json"
MAX_ARTIFACT_BYTES=10485760

python3 - \
  "$SANDBOX_ID" "$COLLECT" "$ROOTFS" "$EXIT_CODE" "$TIMED_OUT" "$DURATION_MS" \
  "$CLEANUP_LIST" "$CLEANUP_PID" "$CLEANUP_PID_ALIVE" "$CLEANUP_PS" "$CLEANUP_WORKSPACE" \
  "$CLEANUP_UNMOUNT" "$WORKSPACE_QUOTA_MB" \
  "$OUTPUT_JSON" "$PROBE_JSON" "$MAX_ARTIFACT_BYTES" "$PROGRAM_MODE" "$TIMEOUT_SECONDS" <<'PY'
import base64, json, os, sys
(
    sandbox_id, workspace, rootfs, exit_code, timed_out, duration_ms,
    cleanup_list, cleanup_pid, cleanup_pid_alive, cleanup_ps, cleanup_workspace,
    cleanup_unmounted, workspace_quota_mb,
    output_json, probe_json, max_artifact_bytes, program_mode, timeout_seconds,
) = sys.argv[1:]


def read_b64(path, limit):
    try:
        with open(path, "rb") as handle:
            payload = handle.read(limit + 1)
        if len(payload) > limit:
            return None, len(payload), "too_large"
        return base64.b64encode(payload).decode("ascii"), len(payload), ""
    except OSError as error:
        return None, 0, error.__class__.__name__


def read_text(path, limit=65536):
    try:
        with open(path, "rb") as handle:
            payload = handle.read(limit)
        return base64.b64encode(payload).decode("ascii"), len(payload)
    except OSError:
        return None, 0


artifacts = []
artifact_error = ""
artifacts_dir = os.path.join(workspace, "artifacts")
try:
    names = sorted(os.listdir(artifacts_dir))
except OSError:
    names = []
for name in names:
    if len(artifacts) >= 16:
        artifact_error = "count_exceeded"
        break
    if not name or name != os.path.basename(name) or name in (".", ".."):
        artifact_error = "invalid_name"
        continue
    path = os.path.join(artifacts_dir, name)
    if os.path.islink(path) or not os.path.isfile(path):
        artifact_error = "not_regular"
        continue
    payload, size, error = read_b64(path, int(max_artifact_bytes))
    if error:
        artifact_error = error
        continue
    artifacts.append({"name": name, "size": size, "b64": payload})

output_b64, output_size, output_error = read_b64(output_json, 1048576)
probe_b64, probe_size, probe_error = read_b64(probe_json, 1048576)
stdout_b64, stdout_size = read_text(os.path.join(workspace, "stdout"))
stderr_b64, stderr_size = read_text(os.path.join(workspace, "stderr"))
try:
    with open(os.path.join(rootfs + ".digest"), "r", encoding="ascii") as handle:
        rootfs_digest = handle.read().strip()
except OSError:
    rootfs_digest = ""

clean = (
    int(cleanup_list) == 0
    and cleanup_pid_alive == "false"
    and int(cleanup_ps) == 0
    and cleanup_workspace == "true"
    and cleanup_unmounted == "true"
)
document = {
    "ok": clean,
    "mode": "run",
    "program": program_mode,
    "sandbox_id": sandbox_id,
    "duration_ms": int(duration_ms),
    "exit_code": int(exit_code),
    "timed_out": timed_out == "true",
    "timeout_seconds": int(timeout_seconds),
    "workspace_quota_mb": int(workspace_quota_mb),
    "rootfs_digest": rootfs_digest,
    "stdout": stdout_b64,
    "stdout_size": stdout_size,
    "stderr": stderr_b64,
    "stderr_size": stderr_size,
    "output_json": output_b64,
    "output_json_size": output_size,
    "output_json_error": output_error,
    "probe_json": probe_b64,
    "probe_json_size": probe_size,
    "probe_json_error": probe_error,
    "artifacts": artifacts,
    "artifact_error": artifact_error,
    "cleanup": {
        "sandbox_list_matches": int(cleanup_list),
        "pid": int(cleanup_pid) if cleanup_pid.isdigit() else 0,
        "pid_alive": cleanup_pid_alive == "true",
        "ps_matches": int(cleanup_ps),
        "workspace_removed": cleanup_workspace == "true",
        "workspace_unmounted": cleanup_unmounted == "true",
    },
}
if not clean:
    document["error"] = "sandbox cleanup left evidence"
print(json.dumps(document))
PY
rm -rf "$COLLECT"
