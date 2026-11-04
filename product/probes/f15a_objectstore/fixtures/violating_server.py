#!/usr/bin/env python3
"""A deliberately violating local S3 fixture: one broken rule per flag.

``--violate <assertion-id>`` breaks exactly the rule that assertion measures, so a
test can require that assertion to fail while the rest still pass. ``--violate
all`` breaks every rule at once. This is the probe's discrimination proof, not
evidence that any real S3 implementation is compatible.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixture_server  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(
        fixture_server.main("F15A violating S3 fixture (discrimination proof only)")
    )
