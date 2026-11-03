"""F06 test package: import the shared real-stack fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

# The worker package is not installed; T041's adapter/agent tests import
# ``platform_worker.agents`` exactly as the future worker process will.
WORKER_ROOT = Path(__file__).resolve().parents[2] / "worker"
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

from support.fixtures import *  # noqa: E402,F401,F403
