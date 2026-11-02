"""T022 test package: import the shared real-stack fixtures.

``tests/support`` is a regular package, so the tests directory is put on
``sys.path`` once here. The fixtures themselves live in
``tests/support/fixtures.py``; importing them binds them into this conftest,
which is where pytest looks for fixture definitions.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from support.fixtures import *  # noqa: E402,F401,F403
