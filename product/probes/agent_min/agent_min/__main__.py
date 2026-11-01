#!/usr/bin/env python3
"""Run the P01 minimal-agent probe and emit a structured result.

Three scenarios are executed against the same authorized tool set:

``happy``         two authorized calls, then a final answer;
``unauthorized``  the model asks for a tool that is not on the allowlist;
``retry``         the same call is replayed, which must be reported as a retry.

Everything is a stub: no provider is contacted, so nothing here is evidence of
model quality or production capacity.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

import jsonschema

from .model import Action, ScriptedModel
from .runner import run_agent
from .tools import make_tools

PROBE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = PROBE_ROOT / "fixtures"

TRACKED_PACKAGES = ("langgraph", "langgraph-checkpoint-postgres", "pydantic", "jsonschema", "psycopg")


def _package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in TRACKED_PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:  # pragma: no cover - env dependent
            versions[name] = None
    return versions


def _registry():
    tools = make_tools(FIXTURE_DIR)
    return tools


def scenario_happy():
    from .tools import ToolRegistry

    registry = ToolRegistry(_registry())
    model = ScriptedModel(
        [
            Action.tool("read_dataset", {"name": "sales"}),
            Action.tool(
                "sum_amounts",
                {"rows": [{"amount": "120.50"}, {"amount": "79.50"}]},
            ),
            Action.final("total computed"),
        ]
    )
    return run_agent(model, registry)


def scenario_unauthorized():
    from .tools import ToolRegistry

    registry = ToolRegistry(_registry())
    model = ScriptedModel(
        [
            Action.tool("delete_records", {"table": "requests"}),
            Action.final("should never be reached"),
        ]
    )
    return run_agent(model, registry)


def scenario_retry():
    from .tools import ToolRegistry

    registry = ToolRegistry(_registry())
    payload = {"rows": [{"amount": "10.00"}]}
    model = ScriptedModel(
        [
            Action.tool("sum_amounts", payload),
            Action.tool("sum_amounts", payload),
            Action.final("computed twice"),
        ]
    )
    return run_agent(model, registry)


def scenario_bad_input():
    from .tools import ToolRegistry

    registry = ToolRegistry(_registry())
    model = ScriptedModel(
        [
            # float instead of decimal string must be rejected by the tool
            Action.tool("sum_amounts", {"rows": [{"amount": 120.5}]}),
            Action.final("unreachable"),
        ]
    )
    return run_agent(model, registry)


SCENARIOS = {
    "happy": scenario_happy,
    "unauthorized": scenario_unauthorized,
    "retry": scenario_retry,
    "bad_input": scenario_bad_input,
}


REPORT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "probe",
        "task",
        "stub",
        "profile",
        "command",
        "host",
        "python",
        "packages",
        "authorized_tools",
        "scenarios",
        "assertions",
    ],
    "properties": {
        "probe": {"type": "string"},
        "task": {"type": "string"},
        "stub": {"const": True},
        "profile": {"type": "string"},
        "command": {"type": "string"},
        "host": {"type": "object"},
        "python": {"type": "string", "pattern": r"^\d+\.\d+"},
        "packages": {"type": "object"},
        "authorized_tools": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "scenarios": {
            "type": "object",
            "required": ["happy", "unauthorized", "retry", "bad_input"],
            "additionalProperties": {
                "type": "object",
                "required": ["status", "tool_calls", "allowed_tools", "hidden_retry_detected"],
                "properties": {
                    "status": {"enum": ["completed", "failed"]},
                    "tool_calls": {"type": "integer", "minimum": 0},
                    "allowed_tools": {"type": "array", "items": {"type": "string"}},
                    "hidden_retry_detected": {"type": "boolean"},
                },
            },
        },
        "assertions": {"type": "object", "minProperties": 1},
    },
}


def validate_report(report: dict[str, Any]) -> None:
    """Validate the probe report against its schema (locked `jsonschema`).

    The probe exists to show the locked dependency set actually runs; validating
    its own output with a locked library is part of that, not decoration.
    """
    jsonschema.validate(instance=report, schema=REPORT_SCHEMA)


def build_report() -> dict[str, Any]:
    scenarios = {name: func().as_dict() for name, func in SCENARIOS.items()}
    report = {
        "probe": "p01-agent-min",
        "task": "P01 / T002",
        "stub": True,
        "note": "No model provider was contacted; control-plane behaviour only.",
        # Environment profile is part of the evidence: a report without host and
        # command cannot be reproduced by another deployer.
        "profile": os.environ.get("ORCHARA_PROFILE", "dev-stub"),
        "command": "python -m agent_min",
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "node": platform.node(),
        },
        "python": platform.python_version(),
        "packages": _package_versions(),
        "authorized_tools": sorted(_registry()),
        "scenarios": scenarios,
        "assertions": {
            "happy_completed": scenarios["happy"]["status"] == "completed",
            "happy_invoked_subset_of_allowlist": set(scenarios["happy"]["invoked_tools"]).issubset(
                set(scenarios["happy"]["allowed_tools"])
            ),
            "happy_call_count": scenarios["happy"]["tool_calls"],
            "unauthorized_rejected": bool(scenarios["unauthorized"]["unexpected_calls"]),
            "unauthorized_run_failed": scenarios["unauthorized"]["status"] == "failed",
            "retry_detected": scenarios["retry"]["hidden_retry_detected"],
            "bad_input_failed": scenarios["bad_input"]["status"] == "failed",
        },
    }
    validate_report(report)
    return report


def append_stub_ledger(report: dict[str, Any], ledger_path: Path) -> None:
    """Record a stub run in a separate, append-only ledger.

    Stub results must be distinguishable from real ones long after the fact, so
    each run appends one line naming the profile and a digest of the report.
    """
    import hashlib
    from datetime import datetime, timezone

    entry = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "probe": report["probe"],
        "profile": report["profile"],
        "stub": True,
        "python": report["python"],
        "host_platform": report["host"]["platform"],
        "report_digest": hashlib.sha256(
            json.dumps(report, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P01 minimal-agent probe")
    parser.add_argument("--out", type=Path, default=None, help="write JSON report here")
    parser.add_argument(
        "--ledger",
        type=Path,
        default=PROBE_ROOT.parents[1] / "reports" / "p01" / "stub-ledger.jsonl",
        help="append-only stub ledger to record this run in",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = build_report()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        if report["stub"]:
            append_stub_ledger(report, args.ledger)
    if not args.quiet:
        json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    return 0 if all(
        (
            report["assertions"]["happy_completed"],
            report["assertions"]["happy_invoked_subset_of_allowlist"],
            report["assertions"]["unauthorized_rejected"],
            report["assertions"]["unauthorized_run_failed"],
            report["assertions"]["retry_detected"],
            report["assertions"]["bad_input_failed"],
        )
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
