#!/usr/bin/env python3
"""Locate the Go toolchain and its caches.

The probes and tests used to spell out `E:\\orchara-toolchains\\...` in five
different files. That contradicts the "clean environment, same verdict" claim, so
the fallback now lives here and every caller goes through it:

* `ORCHARA_GO` — explicit interpreter/compiler path
* `PATH` — a normal installation wins
* the user-space toolchain directory from `ORCHARA_TOOLCHAINS` (default
  `E:/orchara-toolchains`), which is where D00 put it on this machine

The caches follow the same rule, so a machine without the fallback directory
still builds (as long as Go is on PATH and the caches are writable).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def toolchains_root() -> Path:
    return Path(os.environ.get("ORCHARA_TOOLCHAINS", r"E:\orchara-toolchains"))


def go_binary() -> str:
    """Path to the Go binary, or the bare name when nothing else is known."""
    explicit = os.environ.get("ORCHARA_GO")
    if explicit and Path(explicit).exists():
        return explicit

    on_path = shutil.which("go")
    if on_path:
        return on_path

    fallback = toolchains_root() / "go" / "bin" / ("go.exe" if sys.platform == "win32" else "go")
    return str(fallback) if fallback.exists() else "go"


def go_caches() -> dict[str, str]:
    """Environment variables Go needs when its caches are not in the default place."""
    env: dict[str, str] = {}
    root = toolchains_root()
    for key, name in (("GOCACHE", "gocache"), ("GOMODCACHE", "gomodcache")):
        if os.environ.get(key):
            continue
        candidate = root / name
        if candidate.exists():
            env[key] = str(candidate)
    return env


def go_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """A process environment with the caches filled in."""
    env = dict(base if base is not None else os.environ)
    env.update(go_caches())
    return env
