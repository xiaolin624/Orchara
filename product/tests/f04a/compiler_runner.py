"""Subprocess runner: execute the F04A LangGraph proof under the P01 interpreter.

The pytest process imports the pure validator, but the graph proof needs
``langgraph`` from the P01 locked environment. This script is run with that
interpreter and prints one JSON object; it never imports Vue or touches PG.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER = HERE.parents[1] / "worker"
if str(WORKER) not in sys.path:
    sys.path.insert(0, str(WORKER))

from platform_worker.compiler import (  # noqa: E402
    LinearGraph,
    LinearGraphError,
    NodeOutcome,
    build_linear_graph,
    stub_adapters,
    validate,
)

CORPUS = HERE / "corpus" / "linear_f04a.json"
RUN_INPUT = {"file": "sales-2026.csv"}


class FailingPython:
    def invoke(self, node, context):
        return NodeOutcome.failed("sandbox_error", "python exited with status 2")


class WaitingAgent:
    def invoke(self, node, context):
        return NodeOutcome.waiting("approval:approval-1")


class BadOutputAgent:
    def invoke(self, node, context):
        return NodeOutcome.succeeded({"summary": 42})


def definitions() -> dict[str, dict]:
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    return {case["name"]: case["document"] for case in payload["cases"]}


def main() -> int:
    cases = definitions()
    valid = cases["valid-linear"]
    graph = build_linear_graph(valid)
    first = graph.run(RUN_INPUT, stub_adapters(), run_id="run-1")
    second = graph.run(RUN_INPUT, stub_adapters(), run_id="run-1")

    def adapters(overrides):
        mapping = dict(stub_adapters())
        mapping.update(overrides)
        return mapping

    failed = graph.run(RUN_INPUT, adapters({"python": FailingPython()}), run_id="run-2")
    waiting = graph.run(RUN_INPUT, adapters({"agent": WaitingAgent()}), run_id="run-3")
    bad_output = graph.run(RUN_INPUT, adapters({"agent": BadOutputAgent()}), run_id="run-4")

    refusals = {}
    for name in (
        "missing-end", "unknown-type", "cycle", "merge-branch", "fork-branch",
        "duplicate-edge", "extra-port", "wrong-order", "unsupported-node-type",
    ):
        try:
            LinearGraph(cases[name])
            refusals[name] = {"refused": False}
        except LinearGraphError as error:
            refusals[name] = {"refused": True, "code": error.code, "pointer": error.pointer}

    validation = {name: validate(document).as_wire() for name, document in cases.items()}
    print(json.dumps({
        "cwd": str(Path.cwd()),
        "first": first.as_dict(),
        "second": second.as_dict(),
        "deterministic": first.as_dict() == second.as_dict(),
        "failed": failed.as_dict(),
        "waiting": waiting.as_dict(),
        "bad_output": bad_output.as_dict(),
        "refusals": refusals,
        "validation": validation,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
