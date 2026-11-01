"""Make the probe package importable from the product test suite."""

from __future__ import annotations

import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
PROBE_ROOT = PRODUCT_ROOT / "probes" / "agent_min"

if str(PROBE_ROOT) not in sys.path:
    sys.path.insert(0, str(PROBE_ROOT))
