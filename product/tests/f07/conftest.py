"""F07 test package: the T075 fixed-sub-application stack.

The stack is T025's real one (throwaway PostgreSQL migrated by ``cmd/admin``,
the compiled ``cmd/api`` over real HTTP, the real filesystem object store and the
real compiler inputs). T075 needs no internal mTLS listener and no maintenance
process: it validates and freezes a fixed child version, it never executes a
child run (that is T078).
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from support.fixtures import *  # noqa: E402,F401,F403
