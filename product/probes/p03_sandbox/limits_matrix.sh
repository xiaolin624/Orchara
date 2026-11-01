#!/usr/bin/env bash
# P03/T014: stop over-limit scripts and prove the resources are reclaimed.
#
# Every limit is applied by the *runtime from the OCI spec* (rlimits, tmpfs size,
# pids limit) or by the launcher (host timeout, output cap). None of them is
# embedded in the script under test — a fixture that limits itself proves nothing.
#
# Cases: timeout, disk, memory (RLIMIT_AS), cpu (RLIMIT_CPU), fork (pids),
# output (launcher byte cap).
set -uo pipefail

BUNDLE="${ORCHARA_SANDBOX_BUNDLE:-/opt/orchara-sandbox}"
SANDBOX_ID="orchara-t014"
INPUT_DIR="${ORCHARA_SANDBOX_INPUT:-/work/tenant-a}"
FOREIGN_DIR="${ORCHARA_SANDBOX_FOREIGN:-/work/tenant-b}"
OUTPUT_CAP_BYTES="${ORCHARA_OUTPUT_CAP:-65536}"

mkdir -p "$INPUT_DIR" "$FOREIGN_DIR"
printf 'alice-data\n' > "$INPUT_DIR/input.csv"

configure() {
  python3 - "$BUNDLE" "$1" "$2" "$3" <<'PY'
import json, sys, pathlib
bundle, cmd, pids, rlimits_json = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
path = pathlib.Path(bundle) / "config.json"
config = json.loads(path.read_text())
config["process"]["args"] = ["/bin/sh", "-c", cmd]
config["linux"].setdefault("resources", {})["pids"] = {"limit": pids}
rlimits = json.loads(rlimits_json)
if rlimits:
    config["process"]["rlimits"] = rlimits
else:
    config["process"].pop("rlimits", None)
path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
PY
}

sandbox_count() {
  runsc --network=none list --quiet 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' '
}

# run_case <name> <command> <host-timeout-seconds> <pids> <rlimits-json> [output-cap]
run_case() {
  local name="$1" cmd="$2" limit="$3" pids="${4:-256}" rlimits="${5:-[]}" output_cap="${6:-}"
  configure "$cmd" "$pids" "$rlimits"

  runsc --network=none delete --force "$SANDBOX_ID" >/dev/null 2>&1 || true
  local dir code bytes
  dir="$(mktemp -d)"

  if [ -n "$output_cap" ]; then
    # Launcher-side output cap: the sandbox writes to a pipe we truncate.
    ( cd "$BUNDLE" && timeout "$limit" runsc --network=none run --bundle "$BUNDLE" \
        --pid-file "$dir/pid" "$SANDBOX_ID" ) 2>"$dir/err" | head -c "$output_cap" >"$dir/out"
    code=$?
    bytes=$(wc -c < "$dir/out" | tr -d ' ')
  else
    ( cd "$BUNDLE" && timeout "$limit" runsc --network=none run --bundle "$BUNDLE" \
        --pid-file "$dir/pid" "$SANDBOX_ID" ) >"$dir/out" 2>&1
    code=$?
    bytes=$(wc -c < "$dir/out" | tr -d ' ')
  fi

  local before after
  before="$(sandbox_count)"
  runsc --network=none delete --force "$SANDBOX_ID" >/dev/null 2>&1 || true
  after="$(sandbox_count)"

  CASES+=("$(python3 - "$name" "$code" "$before" "$after" "$dir/out" "$bytes" <<'PY'
import json, sys, pathlib
name, code, before, after = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
path, bytes_written = sys.argv[5], int(sys.argv[6])
lines = pathlib.Path(path).read_text(errors="replace").strip().splitlines()[-2:]
# Truncate: an output-limit case intentionally produces a lot of bytes.
tail = " | ".join(lines)[:160]
print(json.dumps({
    "case": name,
    "exit_code": code,
    "stdout_bytes": bytes_written,
    "leftover_sandboxes_before_cleanup": before,
    "leftover_sandboxes_after_cleanup": after,
    "tail": tail,
}, ensure_ascii=False))
PY
)")
  rm -rf "$dir"
}

for applet in dd head tail tr seq sleep kill nc wc; do
  ln -sf /bin/busybox "$BUNDLE/rootfs/bin/$applet" 2>/dev/null || true
done

CASES=()
run_case timeout 'sleep 60' 4 256
run_case disk 'dd if=/dev/zero of=/tmp/big bs=1M count=40 2>&1 | tail -1' 20 256
# Launcher/runtime enforced, never inside the script:
run_case memory 's=$(head -c 80000000 /dev/zero | tr "\0" a); echo survived=${#s}' 30 256 \
  '[{"type":"RLIMIT_AS","soft":67108864,"hard":67108864}]'
run_case cpu 'i=0; while true; do i=$((i+1)); done' 30 256 \
  '[{"type":"RLIMIT_CPU","soft":2,"hard":2}]'
run_case fork 'i=0; while [ $i -lt 200 ]; do sleep 30 & i=$((i+1)); done; echo spawned=$i; wait' 12 256
run_case output 'head -c 5000000 /dev/zero | tr "\0" x' 20 256 '[]' "$OUTPUT_CAP_BYTES"

rm -rf "$INPUT_DIR" "$FOREIGN_DIR"
input_removed=false
[ ! -e "$INPUT_DIR" ] && input_removed=true

printf '{"cases": [%s], "output_cap_bytes": %s, "input_removed": %s, "final_sandboxes": %s}\n' \
  "$(IFS=,; echo "${CASES[*]}")" "$OUTPUT_CAP_BYTES" "$input_removed" "$(sandbox_count)"
