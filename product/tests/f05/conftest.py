"""F05 test package: import the shared real-stack fixtures.

The fixtures live in ``tests/support/fixtures.py`` (real PostgreSQL, real
compiled binaries, real HTTP, real object store, real signed links and the real
maintenance process). ``tests/support/t028.py`` holds the T028-specific helpers
(users, upload requests, artifact rows, admission rows and object helpers); this
conftest only puts the tests root on ``sys.path`` and binds the fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from support.fixtures import *  # noqa: E402,F401,F403
