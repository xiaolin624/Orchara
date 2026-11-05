#!/usr/bin/env python3
"""Thin entry point for the T066 isolated experiment (see mem0exp/runner.py)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mem0exp.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
