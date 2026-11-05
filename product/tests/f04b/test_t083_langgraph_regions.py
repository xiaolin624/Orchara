"""F04B/T083 (P2): the real-LangGraph activation/skip/join-count proof.

The production runtime is the Go executor; this case proves the *compiler* claim
on a real ``langgraph`` StateGraph:

* condition true / default selects one branch and skips the other;
* a parallel fork activates both branches and the join fires exactly once;
* nested condition regions converge at their own join first;
* no unselected branch is waited for (the compiled graph finishes under a
  timeout instead of dead-waiting at an all-of barrier).

The same fixtures are first validated by the independent Python reference
validator (``probes/p05_dsl/python/validate.py``), the one the Go validator is
compared against in ``tests/p05/test_dsl_parity.py`` — so this case reads the
activation proof on a definition both compilers already accepted.
"""

from __future__ import annotations

import concurrent.futures
import json
import sys
from pathlib import Path

from support import t083

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
PROBE = PRODUCT_ROOT / "probes" / "p05_dsl"
WORKER_ROOT = PRODUCT_ROOT / "worker"
if str(PROBE / "python") not in sys.path:
    sys.path.insert(0, str(PROBE / "python"))
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

import validate  # noqa: E402 - the independent P05 reference validator

from platform_worker.compiler.graph import NodeOutcome  # noqa: E402
from platform_worker.compiler.regions import (  # noqa: E402
    RegionGraph,
    RegionGraphError,
)

RULES = validate.Rules.load(PROBE / "rules.json")


class RecordingAdapter:
    """A stub adapter that records the node it executed, never a provider call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def invoke(self, node, context):
        self.calls.append(node["id"])
        return NodeOutcome.succeeded({"node": node["id"], "task": context.inputs.get("task")})


def proof_definition(definition: dict) -> dict:
    """The same topology with literal-only branch inputs.

    The acceptance fixtures bind the real task_input artifact because the Go
    executor runs the fixed tool; the LangGraph proof only compiles activation, so
    the branch inputs are literals and no artifact is resolved here.
    """
    cloned = json.loads(json.dumps(definition))
    for node in cloned["nodes"]:
        if node["type"] in ("python", "agent", "call_app", "model"):
            node["inputs"] = {"task": {"literal": node["id"]}}
        if node["type"] == "condition":
            node["inputs"] = {
                name: {"ref": f"input#/{name}"} for name in node.get("inputs", {})
            }
    return cloned


def run_compiled(definition: dict, run_inputs: dict, *, timeout: float = 20.0):
    adapter = RecordingAdapter()
    adapters = {"python": adapter, "agent": adapter, "call_app": adapter}
    graph = RegionGraph(definition)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(graph.run, run_inputs, adapters)
        result = future.result(timeout=timeout)
    return result, adapter


def assert_reference_valid(definition: dict) -> None:
    result = validate.validate(definition, RULES)
    assert result.problems == (), (definition.get("nodes", [{}])[0], result.problems)


def end_results(result) -> dict:
    return result.outputs["end"]["results"]


def test_reference_validator_accepts_every_proof_fixture() -> None:
    for definition in (
        t083.branch_definition(),
        t083.parallel_definition(),
        t083.nested_definition(),
    ):
        assert_reference_valid(proof_definition(definition))


def test_langgraph_condition_true_activates_one_branch_and_skips_the_other() -> None:
    definition = proof_definition(t083.branch_definition())
    result, adapters = run_compiled(definition, {"requires_approval": True})
    assert result.status == "succeeded", result.as_dict()
    assert set(result.invoked) == {"start", "choose", "left", "join", "end"}, result.invoked
    assert result.skipped == ("right",), result.skipped
    assert adapters.calls == ["left"], adapters.calls
    assert result.join_counts == {"join": 1}, result.join_counts

    results = end_results(result)
    assert results["left"]["status"] == "succeeded", results
    assert results["left"]["value"] == {"node": "left", "task": "left"}, results
    # A skipped branch has no value and is not a failure.
    assert results["right"] == {"status": "skipped"}, results


def test_langgraph_condition_default_port_activates_the_no_branch() -> None:
    definition = proof_definition(t083.branch_definition())
    result, adapters = run_compiled(definition, {"requires_approval": False})
    assert result.status == "succeeded", result.as_dict()
    assert set(result.invoked) == {"start", "choose", "right", "join", "end"}, result.invoked
    assert result.skipped == ("left",), result.skipped
    assert adapters.calls == ["right"], adapters.calls
    assert result.join_counts == {"join": 1}, result.join_counts
    results = end_results(result)
    assert results["left"] == {"status": "skipped"}, results
    assert results["right"]["status"] == "succeeded", results


def test_langgraph_parallel_fork_activates_both_branches_and_joins_once() -> None:
    definition = proof_definition(t083.parallel_definition())
    result, adapters = run_compiled(definition, {})
    assert result.status == "succeeded", result.as_dict()
    assert set(result.invoked) == {"start", "left", "right", "join", "end"}, result.invoked
    assert result.skipped == (), result.skipped
    assert sorted(adapters.calls) == ["left", "right"], adapters.calls
    assert result.join_counts == {"join": 1}, result.join_counts
    results = end_results(result)
    for source in ("left", "right"):
        assert results[source]["status"] == "succeeded", results
        assert results[source]["value"] == {"node": source, "task": source}, results


def test_langgraph_nested_regions_converge_at_each_join_once() -> None:
    definition = proof_definition(t083.nested_definition())
    result, adapters = run_compiled(definition, {"mode": "deep"})
    assert result.status == "succeeded", result.as_dict()
    assert set(result.invoked) == {
        "start", "choose", "choose2", "deep", "join2", "join", "end",
    }, result.invoked
    assert set(result.skipped) == {"shallow", "direct"}, result.skipped
    assert adapters.calls == ["deep"], adapters.calls
    assert result.join_counts == {"join2": 1, "join": 1}, result.join_counts

    inner = result.outputs["join2"]
    assert inner["results"]["deep"]["status"] == "succeeded", inner
    assert inner["results"]["shallow"] == {"status": "skipped"}, inner
    outer = end_results(result)
    assert outer["join2"]["status"] == "succeeded", outer
    assert outer["join2"]["value"] == inner, "the nested region converges before the parent"
    assert outer["direct"] == {"status": "skipped"}, outer


def test_langgraph_unselected_branch_never_blocks_the_join() -> None:
    """The condition case finishes under a timeout: no all-of barrier dead-wait.

    LangGraph is asked for its result with a hard timeout; if the unselected branch
    were part of an unconditional barrier the compiled graph would never finish and
    the future would time out.
    """
    definition = proof_definition(t083.branch_definition())
    result, _adapters = run_compiled(definition, {"requires_approval": True}, timeout=20.0)
    assert result.status == "succeeded"
    assert result.join_counts == {"join": 1}


def test_region_compiler_refuses_an_unpaired_join() -> None:
    definition = proof_definition(t083.branch_definition())
    for node in definition["nodes"]:
        if node["id"] == "join":
            node["config"]["fork_id"] = "left"
    try:
        RegionGraph(definition)
    except RegionGraphError as error:
        assert error.code == "invalid_region", error.code
    else:  # pragma: no cover - the refusal must fire
        raise AssertionError("an unpaired join was accepted by the region compiler")
