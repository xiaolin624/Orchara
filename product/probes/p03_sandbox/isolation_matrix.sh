#!/usr/bin/env bash
# P03/T010: attack matrix for the isolation profile.
#
# `runsc do` is a testing convenience: it mounts the host filesystem read-only
# and runs as root, so a script can read any host file. User scripts therefore
# run from this minimal OCI bundle instead:
#   * own rootfs (busybox), readonly
#   * no host mount except the approved input directory, read-only
#   * uid/gid 65534, no capabilities, no_new_privs
#   * pid/network/ipc/uts/mount namespaces, 64 MiB memory limit
#
# Prints one JSON object so the host-side probe can assert on it.
set -uo pipefail

BUNDLE="${ORCHARA_SANDBOX_BUNDLE:-/opt/orchara-sandbox}"
ROOTFS="$BUNDLE/rootfs"
TENANT_A="${ORCHARA_SANDBOX_INPUT:-/work/tenant-a}"
TENANT_B="${ORCHARA_SANDBOX_FOREIGN:-/work/tenant-b}"
SANDBOX_ID="orchara-t010"

mkdir -p "$BUNDLE" "$TENANT_A" "$TENANT_B"
printf 'alice-data\n' > "$TENANT_A/input.csv"
printf 'bob-secret\n' > "$TENANT_B/input.csv"
chmod 600 "$TENANT_B/input.csv"

if [ ! -x "$BUNDLE/busybox" ]; then
  curl -fsSL -o "$BUNDLE/busybox" \
    https://busybox.net/downloads/binaries/1.35.0-x86_64-linux-musl/busybox || {
      echo '{"error": "cannot fetch busybox"}'; exit 1; }
  chmod +x "$BUNDLE/busybox"
fi

rm -rf "$ROOTFS"
mkdir -p "$ROOTFS"/{bin,proc,tmp,work/input}
cp "$BUNDLE/busybox" "$ROOTFS/bin/busybox"
for applet in sh cat ls echo id env mkdir rm sleep wc nc; do
  ln -sf /bin/busybox "$ROOTFS/bin/$applet"
done

cat > "$BUNDLE/config.json" <<JSON
{
  "ociVersion": "1.0.2",
  "process": {
    "terminal": false,
    "user": { "uid": 65534, "gid": 65534 },
    "args": ["/bin/sh", "-c", "true"],
    "env": ["PATH=/bin"],
    "cwd": "/",
    "noNewPrivileges": true,
    "capabilities": { "bounding": [], "effective": [], "permitted": [] }
  },
  "root": { "path": "rootfs", "readonly": true },
  "hostname": "sandbox",
  "mounts": [
    { "destination": "/proc", "type": "proc", "source": "proc" },
    { "destination": "/tmp", "type": "tmpfs", "source": "tmpfs", "options": ["nosuid", "nodev", "size=16m"] },
    { "destination": "/work/input", "type": "bind", "source": "$TENANT_A", "options": ["ro", "bind"] }
  ],
  "linux": {
    "namespaces": [
      { "type": "pid" }, { "type": "network" }, { "type": "ipc" },
      { "type": "uts" }, { "type": "mount" }
    ],
    "resources": { "memory": { "limit": 67108864 } }
  }
}
JSON

run_sandbox() {
  local cmd="$1"
  python3 - "$BUNDLE" "$cmd" <<'PY'
import json, sys, pathlib
bundle, cmd = sys.argv[1], sys.argv[2]
path = pathlib.Path(bundle) / "config.json"
config = json.loads(path.read_text())
config["process"]["args"] = ["/bin/sh", "-c", cmd]
path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
PY
  runsc --network=none delete "$SANDBOX_ID" >/dev/null 2>&1 || true
  ( cd "$BUNDLE" && runsc --network=none run --bundle "$BUNDLE" \
      --pid-file /tmp/orchara-t010.pid "$SANDBOX_ID" 2>&1 )
}

identity="$(run_sandbox 'id')"
approved="$(run_sandbox 'cat /work/input/input.csv 2>&1 || echo DENIED')"
foreign="$(run_sandbox 'cat /work/tenant-b/input.csv 2>&1 || echo DENIED')"
host_etc="$(run_sandbox 'cat /etc/hostname 2>&1 || echo DENIED')"
write_attempt="$(run_sandbox 'echo x > /work/input/blocked 2>&1 || echo DENIED')"
public_net="$(run_sandbox 'nc -w 3 -z 1.1.1.1 443 >/dev/null 2>&1 && echo CONNECTED || echo BLOCKED')"
host_net="$(run_sandbox 'nc -w 3 -z 127.0.0.1 55432 >/dev/null 2>&1 && echo CONNECTED || echo BLOCKED')"
proxy_net="$(run_sandbox 'HTTP_PROXY=http://127.0.0.1:3128 HTTPS_PROXY=http://127.0.0.1:3128 nc -w 3 -z 1.1.1.1 443 >/dev/null 2>&1 && echo CONNECTED || echo BLOCKED')"
sandbox_env="$(ORCHARA_TEST_SECRET=leakme run_sandbox 'env' | sort | tr '\n' '|')"

json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr '\n' ' '; }

cat <<JSON
{
  "profile": "minimal-oci-bundle",
  "identity": "$(json_escape "$identity")",
  "approved_input": "$(json_escape "$approved")",
  "foreign_input": "$(json_escape "$foreign")",
  "host_filesystem": "$(json_escape "$host_etc")",
  "write_attempt": "$(json_escape "$write_attempt")",
  "public_network": "$(json_escape "$public_net")",
  "host_network": "$(json_escape "$host_net")",
  "proxy_network": "$(json_escape "$proxy_net")",
  "sandbox_env": "$(json_escape "$sandbox_env")"
}
JSON
