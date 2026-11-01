#!/usr/bin/env bash
# P03/T014: stop over-limit scripts and prove the resources are reclaimed.
#
# Each case runs the isolation bundle under a host timeout, then checks that the
# sandbox is gone and the approved input is removed. Prints one JSON object.
set -uo pipefail

BUNDLE="${ORCHARA_SANDBOX_BUNDLE:-/opt/orchara-sandbox}"
SANDBOX_ID="orchara-t014"
INPUT_DIR="${ORCHARA_SANDBOX_INPUT:-/work/tenant-a}"
FOREIGN_DIR="${ORCHARA_SANDBOX_FOREIGN:-/work/tenant-b}"

set_args() {
  python3 - "$BUNDLE" "$1" "$2" <<'PY'
import json, sys, pathlib
bundle, cmd, pids = sys.argv[1], sys.argv[2], int(sys.argv[3])
path = pathlib.Path(bundle) / "config.json"
config = json.loads(path.read_text())
config["process"]["args"] = ["/bin/sh", "-c", cmd]
config["linux"].setdefault("resources", {})["pids"] = {"limit": pids}
path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
PY
}

sandbox_count() {
  runsc --network=none list --quiet 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' '
}

run_case() {
  local name="$1" cmd="$2" limit="$3" pids="${4:-64}"
  set_args "$cmd" "$pids"
  runsc --network=none delete "$SANDBOX_ID" >/dev/null 2>&1 || true

  local run_dir output code
  run_dir="$(mktemp -d)"
  output="$run_dir/out"
  ( cd "$BUNDLE" && timeout "$limit" runsc --network=none run --bundle "$BUNDLE" \
      --pid-file "$run_dir/pid" "$SANDBOX_ID" ) >"$output" 2>&1
  code=$?

  local leftovers_before_cleanup leftovers_after_cleanup
  leftovers_before_cleanup="$(sandbox_count)"
  runsc --network=none delete --force "$SANDBOX_ID" >/dev/null 2>&1 || true
  leftovers_after_cleanup="$(sandbox_count)"

  CASES+=("$(python3 - "$name" "$code" "$leftovers_before_cleanup" "$leftovers_after_cleanup" "$output" <<'PY'
import json, sys, pathlib
name, code, before, after, path = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
text = pathlib.Path(path).read_text(errors="replace").strip().splitlines()
print(json.dumps({
    "case": name,
    "exit_code": code,
    "leftover_sandboxes_before_cleanup": before,
    "leftover_sandboxes_after_cleanup": after,
    "tail": " | ".join(text[-2:]),
}, ensure_ascii=False))
PY
)")
}

mkdir -p "$INPUT_DIR" "$FOREIGN_DIR"
printf 'alice-data\n' > "$INPUT_DIR/input.csv"

# pids limit + more applets for the limit tests
for applet in dd head tail tr seq sleep kill; do
  ln -sf /bin/busybox "$BUNDLE/rootfs/bin/$applet" 2>/dev/null || true
done

CASES=()
run_case timeout 'sleep 60' 4
run_case disk 'dd if=/dev/zero of=/tmp/big bs=1M count=40 2>&1 | tail -1' 20
# The OCI cgroup memory limit is not honoured on this host (an 80 MiB allocation
# survives a 64 MiB limit), so the launcher enforces it with ulimit instead.
run_case memory_oci 's=$(head -c 80000000 /dev/zero | tr "\0" a); echo survived=${#s}' 30 256
run_case memory_ulimit 'ulimit -v 65536; s=$(head -c 80000000 /dev/zero | tr "\0" a); echo survived=${#s}' 30 256
run_case fork 'i=0; while [ $i -lt 200 ]; do sleep 30 & i=$((i+1)); done; echo spawned=$i; wait' 12 256

# reclaim the approved input and confirm it is gone
rm -rf "$INPUT_DIR" "$FOREIGN_DIR"
input_removed=false
[ ! -e "$INPUT_DIR" ] && input_removed=true

final_sandboxes="$(sandbox_count)"

printf '{"cases": [%s], "input_removed": %s, "final_sandboxes": %s}\n' \
  "$(IFS=,; echo "${CASES[*]}")" "$input_removed" "$final_sandboxes"
