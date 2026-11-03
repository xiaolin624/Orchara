"""T029 test package: import the shared real-stack fixtures.

The fixtures live in ``tests/support/fixtures.py`` (real PostgreSQL, real
compiled binaries, real HTTP/mTLS); ``tests/support/t029.py`` holds the F03
helpers. This conftest only puts the tests root on ``sys.path`` and binds the
fixtures, exactly like the F00B/F02A/F05 packages.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from support.fixtures import *  # noqa: E402,F401,F403
