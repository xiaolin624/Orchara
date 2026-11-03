#!/usr/bin/env bash
# F05/T033: build the minimal Python rootfs the tool-debug sandbox runs.
#
# The rootfs is deliberately tiny and reproducible: the guest's python3.12
# interpreter, its stdlib (without tests/IDLE/Tk), the shared libraries ldd
# reports for the interpreter and every extension module, and the dynamic loader.
# It contains no package manager, no shell, no compiler and no host /etc.
#
# It is built once per deployment (or when the runtime changes) and its digest is
# recorded next to it:
#
#     <rootfs>.digest   sha256:<hex>   (the execution identity recorded in exit evidence)
#     <rootfs>.manifest mode size sha256 path, sorted (the digest input)
#     <rootfs>.meta.json builder/runtime facts
#
# The digest is what the executor records as the sandbox image identity; it is a
# statement about the exact bytes the sandbox will mount, not about a tag.
#
# Usage (inside the Linux guest, as root):
#   bash build_python_rootfs.sh [--rootfs /opt/orchara-sandbox/python-rootfs]
#
# Environment overrides (the executor reads the same names):
#   ORCHARA_SANDBOX_PYTHON_ROOTFS   default /opt/orchara-sandbox/python-rootfs
#   ORCHARA_SANDBOX_PYTHON_BIN      default /usr/bin/python3.12
#   ORCHARA_SANDBOX_STDLIB          default /usr/lib/python3.12
set -euo pipefail

ROOTFS="${ORCHARA_SANDBOX_PYTHON_ROOTFS:-/opt/orchara-sandbox/python-rootfs}"
PYTHON_BIN="${ORCHARA_SANDBOX_PYTHON_BIN:-/usr/bin/python3.12}"
STDLIB="${ORCHARA_SANDBOX_STDLIB:-/usr/lib/python3.12}"
BUILDER_VERSION="orchara.sandbox-rootfs/v1"

while [ $# -gt 0 ]; do
  case "$1" in
    --rootfs) ROOTFS="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "build_python_rootfs.sh must run as root inside the Linux guest" >&2
  exit 1
fi
if [ ! -x "$PYTHON_BIN" ]; then
  echo "python interpreter not found: $PYTHON_BIN" >&2
  exit 1
fi
if [ ! -d "$STDLIB" ]; then
  echo "python stdlib not found: $STDLIB" >&2
  exit 1
fi

ROOTFS="${ROOTFS%/}"
DIGEST_FILE="$ROOTFS.digest"
MANIFEST_FILE="$ROOTFS.manifest"
META_FILE="$ROOTFS.meta.json"

rm -rf "$ROOTFS" "$DIGEST_FILE" "$MANIFEST_FILE" "$META_FILE"
mkdir -p "$ROOTFS"/usr/bin "$ROOTFS"/usr/lib "$ROOTFS"/lib64 "$ROOTFS"/lib/x86_64-linux-gnu \
         "$ROOTFS"/tmp "$ROOTFS"/proc "$ROOTFS"/dev "$ROOTFS"/etc "$ROOTFS"/workspace \
         "$ROOTFS"/inputs "$ROOTFS"/tool

# The interpreter. No shell, no package manager, no host tools.
install -m 0755 "$PYTHON_BIN" "$ROOTFS/usr/bin/python3.12"

# The stdlib. Keep __pycache__ (it is part of the runtime bytes and makes imports
# fast in the sandbox), drop the parts a compute tool never needs.
cp -a "$STDLIB"/. "$ROOTFS/usr/lib/python3.12/"
rm -rf \
  "$ROOTFS/usr/lib/python3.12/test" \
  "$ROOTFS/usr/lib/python3.12/idlelib" \
  "$ROOTFS/usr/lib/python3.12/tkinter" \
  "$ROOTFS/usr/lib/python3.12/turtledemo" \
  "$ROOTFS/usr/lib/python3.12/ensurepip" \
  "$ROOTFS/usr/lib/python3.12/lib2to3" \
  "$ROOTFS/usr/lib/python3.12/pydoc_data" \
  "$ROOTFS/usr/lib/python3.12/site-packages" \
  "$ROOTFS/usr/lib/python3.12/dist-packages"

copy_library() {
  local library="$1" destination
  case "$library" in
    /lib/x86_64-linux-gnu/*) destination="$ROOTFS/lib/x86_64-linux-gnu/${library#/lib/x86_64-linux-gnu/}" ;;
    /usr/lib/x86_64-linux-gnu/*) destination="$ROOTFS/usr/lib/x86_64-linux-gnu/${library#/usr/lib/x86_64-linux-gnu/}" ;;
    /lib64/*) destination="$ROOTFS/lib64/${library#/lib64/}" ;;
    /*) destination="$ROOTFS$library" ;;
    *) return 0 ;;
  esac
  mkdir -p "$(dirname "$destination")"
  # -L: copy the resolved library, never a symlink out of the rootfs.
  cp -L "$library" "$destination"
}

# The dynamic loader is not listed as a "=>" dependency by ldd.
LOADER="$(readlink -f /lib64/ld-linux-x86-64.so.2)"
install -m 0755 "$LOADER" "$ROOTFS/lib64/ld-linux-x86-64.so.2"

# Every shared library the interpreter and the extension modules need.
RUNTIME_ELVES=("$PYTHON_BIN")
while IFS= read -r module; do
  RUNTIME_ELVES+=("$module")
done < <(find "$STDLIB/lib-dynload" -name '*.so' -type f 2>/dev/null | sort)
for elf in "${RUNTIME_ELVES[@]}"; do
  while IFS= read -r library; do
    copy_library "$library"
  done < <(ldd "$elf" 2>/dev/null | awk '/=> \//{print $3} /^[[:space:]]*\/lib/{print $1}' | sort -u)
done

# A fixed set of device nodes the OCI runtime may bind; runsc provides the
# standard ones, so the rootfs only needs the directory taxonomy. /etc is kept
# empty on purpose: the sandbox must not see any host file (the isolation probe
# asserts that /etc/hostname is not readable).

# Digest: mode + size + sha256 + relative path of every regular file, sorted.
# The manifest itself is written before hashing so the digest covers the exact
# bytes of the files, never mtimes or ownership.
(
  cd "$ROOTFS"
  find . -type f -print0 | sort -z | while IFS= read -r -d '' file; do
    printf '%s %s %s %s\n' \
      "$(stat -c '%a' "$file")" "$(stat -c '%s' "$file")" "$(sha256sum "$file" | cut -d' ' -f1)" "${file#./}"
  done
) > "$MANIFEST_FILE"
DIGEST="sha256:$(sha256sum "$MANIFEST_FILE" | cut -d' ' -f1)"
printf '%s\n' "$DIGEST" > "$DIGEST_FILE"

python3 - "$META_FILE" "$DIGEST" "$BUILDER_VERSION" "$PYTHON_BIN" "$STDLIB" \
  "$(sha256sum "$PYTHON_BIN" | cut -d' ' -f1)" "$(uname -sr)" \
  "$(runsc --version 2>/dev/null | head -1 || true)" "$(find "$ROOTFS" -type f | wc -l | tr -d ' ')" \
  "$(du -sk "$ROOTFS" | cut -f1)" <<'PY'
import json, sys
path, digest, builder, python_bin, stdlib, python_sha, kernel, runsc, files, kib = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump({
        "builder": builder,
        "digest": digest,
        "python_bin": python_bin,
        "python_sha256": python_sha,
        "stdlib": stdlib,
        "kernel": kernel,
        "runsc": runsc.strip(),
        "file_count": int(files),
        "size_kib": int(kib),
    }, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

echo "rootfs built: $ROOTFS"
echo "digest: $DIGEST"
echo "files: $(find "$ROOTFS" -type f | wc -l | tr -d ' ')  size: $(du -sh "$ROOTFS" | cut -f1)"
