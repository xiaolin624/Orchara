#!/usr/bin/env bash
# P01/T004: rebuild the core probe in a second, clean environment.
#
# This script is the re-runnable entry point for the "second environment"
# evidence. It installs the *same* hash-locked dependency set into an isolated
# target directory (no sudo, no virtualenv) and reruns the probe.
#
# Usage, from the repository root inside the second environment:
#   bash product/tools/second_env_probe.sh
#
# Recorded environments: WSL Ubuntu-24.04, Python 3.12.3, Linux 6.6.87.2.
set -euo pipefail

ROOT="${ORCHARA_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
PROBE_DIR="$ROOT/product/probes/agent_min"
LOCK="$PROBE_DIR/requirements.lock.txt"
TARGET="${ORCHARA_PROBE_TARGET:-/tmp/orchara-p01deps}"
OUT="$ROOT/product/reports/p01/agent-min-probe-second-env.json"

if [ ! -f "$LOCK" ]; then
  echo "cannot find $LOCK; set ORCHARA_ROOT to the repository root" >&2
  exit 1
fi

echo "host:   $(uname -srm)"
echo "python: $(python3 --version)"
echo "target: $TARGET"

if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "pip is missing; bootstrapping it without sudo"
  curl -sSL -o /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py
  # PEP 668 blocks user installs on Ubuntu; this touches ~/.local only.
  python3 /tmp/get-pip.py --user --break-system-packages --quiet
fi

python3 -m pip install --quiet --require-hashes --target "$TARGET" -r "$LOCK"
echo "locked install: ok"

cd "$PROBE_DIR"
PYTHONPATH="$TARGET" ORCHARA_PROFILE="dev-stub-second-env" \
  python3 -m agent_min --quiet --out "$OUT"

python3 - "$OUT" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
bools = {k: v for k, v in payload["assertions"].items() if isinstance(v, bool)}
print("assertions:", json.dumps(bools))
print("all true:", all(bools.values()))
print("packages:", json.dumps({k: v for k, v in payload["packages"].items() if v}))
print("host:", payload["host"]["platform"])
if not all(bools.values()):
    raise SystemExit(1)
PY
