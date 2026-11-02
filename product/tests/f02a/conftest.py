"""T023 test package: import the shared real-stack fixtures.

The fixtures live in ``tests/support/fixtures.py`` (real PostgreSQL, real
compiled binaries, real HTTP). ``tests/support/f02a.py`` holds the T023-specific
helpers (draft/version requests, seeded published versions, scoped keys); this
conftest only puts the tests root on ``sys.path`` and binds the fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from support.fixtures import *  # noqa: E402,F401,F403
