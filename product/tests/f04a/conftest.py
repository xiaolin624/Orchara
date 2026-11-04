"""F04A (T040) test package: shared paths and the locked P01 interpreter.

The Python compiler is executed here. Pure validation runs under whatever
interpreter runs pytest; the LangGraph execution proof runs under the P01 locked
environment, because that is where ``langgraph`` is installed. If that
interpreter is missing the graph test fails with a precise message instead of
silently skipping the only real graph test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TESTS_ROOT.parent
WORKER_ROOT = PRODUCT_ROOT / "worker"
CORPUS = Path(__file__).resolve().parent / "corpus" / "linear_f04a.json"

for entry in (TESTS_ROOT, WORKER_ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from support.fixtures import *  # noqa: E402,F401,F403

P01_CANDIDATES = (
    PRODUCT_ROOT / "probes" / "agent_min" / ".venv" / "Scripts" / "python.exe",
    PRODUCT_ROOT / "probes" / "agent_min" / ".venv" / "bin" / "python",
)


def p01_python() -> Path:
    """The P01 locked interpreter, or a precise failure (never a silent skip)."""
    import os

    override = os.environ.get("ORCHARA_P01_PYTHON")
    candidates = ([Path(override)] if override else []) + list(P01_CANDIDATES)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise pytest.fail(
        "the P01 locked interpreter (langgraph 1.2.4) is required to run the "
        f"F04A LangGraph proof; looked for {', '.join(str(p) for p in candidates)}. "
        "Set ORCHARA_P01_PYTHON to the probes/agent_min/.venv interpreter."
    )


@pytest.fixture(scope="session")
def graph_python() -> Path:
    return p01_python()


@pytest.fixture(scope="session")
def corpus() -> dict:
    import json

    return json.loads(CORPUS.read_text(encoding="utf-8"))
