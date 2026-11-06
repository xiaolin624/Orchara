"""F04B (T083) test package: shared paths and the real-stack fixtures.

The package is new (`tests/f04b/`); it reuses the shared support modules and
fixtures exactly like `tests/f04a/` does. Nothing here starts a browser: the
canvas pure modules run under Node, the definition paths and the graph-runtime
condition/join cases run over the real API/PostgreSQL stack, and the LangGraph
region proof drives the Python compiler directly (it puts its own probe and
`platform_worker` paths on `sys.path`).
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TESTS_ROOT.parent

for entry in (TESTS_ROOT, PRODUCT_ROOT / "worker"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from support.fixtures import *  # noqa: E402,F401,F403
