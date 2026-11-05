#!/usr/bin/env python3
"""A compliant local S3 fixture: every rule the probe asserts is upheld.

Not acceptance evidence. It exists so the probe can be shown to *pass* something
that actually enforces the policy, before the same probe is pointed at a real
candidate (which this machine does not have).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixture_server  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(fixture_server.main("F15A compliant S3 fixture (not a candidate)"))
