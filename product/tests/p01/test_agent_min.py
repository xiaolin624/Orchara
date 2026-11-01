"""P01/T002 acceptance tests: locked deps + authorized-tool agent.

Acceptance criteria covered:
  * the authorized tool set is explicit and enforced;
  * one minimal call returns a structured result;
  * hidden retries and unexpected tool calls are captured, not swallowed;
  * exact versions and checksums are traceable (hash lock + installed match).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
PROBE_ROOT = PRODUCT_ROOT / "probes" / "agent_min"
LOCK_FILE = PROBE_ROOT / "requirements.lock.txt"
VENV_PYTHON = PROBE_ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

from agent_min.__main__ import build_report  # noqa: E402
from agent_min.model import Action, ScriptedModel  # noqa: E402
from agent_min.runner import run_agent  # noqa: E402
from agent_min.tools import ToolRegistry, make_tools  # noqa: E402

FIXTURES = PROBE_ROOT / "fixtures"


# --------------------------------------------------------------------------
# control plane
# --------------------------------------------------------------------------
def test_happy_path_returns_structured_result() -> None:
    registry = ToolRegistry(make_tools(FIXTURES))
    model = ScriptedModel(
        [
            Action.tool("read_dataset", {"name": "sales"}),
            Action.tool("sum_amounts", {"rows": [{"amount": "120.50"}, {"amount": "79.50"}]}),
            Action.final("done"),
        ]
    )
    result = run_agent(model, registry)

    assert result.status == "completed"
    assert result.tool_calls == 2
    assert result.structured == {"total": "200.00", "count": 2}
    # Decimal amounts must stay strings, never floats.
    assert isinstance(result.structured["total"], str)


def test_only_allowlisted_tools_are_runnable() -> None:
    registry = ToolRegistry(make_tools(FIXTURES))
    assert registry.allowed_names == ["read_dataset", "sum_amounts"]

    model = ScriptedModel(
        [
            Action.tool("delete_records", {"table": "requests"}),
            Action.final("unreachable"),
        ]
    )
    result = run_agent(model, registry)

    assert result.status == "failed"
    assert result.unexpected_calls, "rejected attempt must be recorded"
    assert result.unexpected_calls[0]["name"] == "delete_records"
    assert "delete_records" not in result.allowed_tools
    # the run must stop at the unauthorized call, not continue past it
    assert result.invoked_tools == []


def test_hidden_retry_is_detected() -> None:
    registry = ToolRegistry(make_tools(FIXTURES))
    payload = {"rows": [{"amount": "10.00"}]}
    model = ScriptedModel(
        [
            Action.tool("sum_amounts", payload),
            Action.tool("sum_amounts", payload),
            Action.final("done"),
        ]
    )
    result = run_agent(model, registry)

    assert result.tool_calls == 2
    assert result.retries == {"sum_amounts": 1}
    assert result.as_dict()["hidden_retry_detected"] is True


def test_bad_tool_input_fails_the_run_honestly() -> None:
    registry = ToolRegistry(make_tools(FIXTURES))
    model = ScriptedModel(
        [Action.tool("sum_amounts", {"rows": [{"amount": 120.5}]}), Action.final("unreachable")]
    )
    result = run_agent(model, registry)

    assert result.status == "failed"
    assert any("TypeError" in failure for failure in result.failures)
    assert result.final is None


def test_step_budget_is_enforced() -> None:
    registry = ToolRegistry(make_tools(FIXTURES))
    model = ScriptedModel(
        [Action.tool("read_dataset", {"name": "sales"}) for _ in range(10)]
    )
    result = run_agent(model, registry, max_steps=3)

    assert result.status == "failed"
    assert any("step budget" in failure for failure in result.failures)


def test_probe_is_deterministic() -> None:
    first = build_report()
    second = build_report()
    assert first["scenarios"] == second["scenarios"]
    assert first["assertions"]["happy_call_count"] == 2


def test_all_probe_assertions_hold() -> None:
    assertions = build_report()["assertions"]
    for name, value in assertions.items():
        if isinstance(value, bool):
            assert value, f"probe assertion failed: {name}"


# --------------------------------------------------------------------------
# locked dependency evidence
# --------------------------------------------------------------------------
def test_lock_file_pins_every_package_with_a_hash() -> None:
    assert LOCK_FILE.exists(), "requirements.lock.txt must be generated from a real resolution"
    text = LOCK_FILE.read_text(encoding="utf-8")
    pins = re.findall(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", text, flags=re.MULTILINE)
    hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", text)

    assert len(pins) >= 40, f"expected the resolved graph, got {len(pins)} pins"
    assert len(hashes) == len(pins), "every pin must carry exactly one sha256 hash"

    pinned = {name.lower() for name, _ in pins}
    for required in ("langgraph", "langgraph-checkpoint-postgres", "pydantic", "jsonschema", "psycopg"):
        assert required in pinned, f"{required} missing from the lock"


@pytest.mark.skipif(not VENV_PYTHON.exists(), reason="probe venv not created in this environment")
def test_installed_versions_match_the_lock() -> None:
    """The lock is only meaningful if a clean install matches it exactly."""
    text = LOCK_FILE.read_text(encoding="utf-8")
    expected = {
        name.lower(): version
        for name, version in re.findall(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", text, flags=re.MULTILINE)
    }
    script = (
        "import json, re, sys\n"
        "from importlib import metadata\n"
        "names = json.loads(sys.argv[1])\n"
        "out = {}\n"
        "for n in names:\n"
        "    try:\n"
        "        out[n] = metadata.version(n)\n"
        "    except metadata.PackageNotFoundError:\n"
        "        out[n] = None\n"
        "print(json.dumps(out))\n"
    )
    completed = subprocess.run(
        [str(VENV_PYTHON), "-c", script, json.dumps(sorted(expected))],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    installed = json.loads(completed.stdout)
    mismatched = {
        name: (expected[name], installed.get(name))
        for name in expected
        if installed.get(name) != expected[name]
    }
    assert not mismatched, f"installed versions differ from lock: {mismatched}"
