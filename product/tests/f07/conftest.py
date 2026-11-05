"""F07 test package: the T075 fixed-sub-application and T078 child-Run stacks.

The stack is T029's real one (throwaway PostgreSQL migrated by ``cmd/admin``, the
compiled ``cmd/api`` over real HTTP, the real filesystem object store, the real
internal mTLS listener and the compiled supervisor binary). T075 needs no internal
mTLS listener; T078 does, because a child Run is only created through the contract
``spawnChild`` operation and executed by the real ``cmd/supervisor exec``.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

# T078's Python adapter tests import ``platform_worker.agents`` exactly as the
# worker process will; the package is not installed.
WORKER_ROOT = Path(__file__).resolve().parents[2] / "worker"
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

from support.fixtures import *  # noqa: E402,F401,F403
