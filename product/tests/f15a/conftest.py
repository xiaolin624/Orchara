"""T053/F15A: make the object-store probe package importable from the suite.

The probe is a standalone product deliverable (``probes/f15a_objectstore``), not a
platform package, so the tests add that directory to ``sys.path`` exactly the way
``tests/p01`` does for the agent-min probe. The fixture servers are real
subprocesses; nothing here imports the product's Go code.
"""

from __future__ import annotations

import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
PROBE_ROOT = PRODUCT_ROOT / "probes" / "f15a_objectstore"
FIXTURES_ROOT = PROBE_ROOT / "fixtures"

for path in (PROBE_ROOT, FIXTURES_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
