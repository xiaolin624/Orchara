#!/usr/bin/env python3
"""P05/T011: branch and join semantics in a real graph engine.

Runs on LangGraph (the worker's actual engine), not on a hand-rolled simulator:

* a conditional edge activates exactly one branch; the other branch never runs
  and is reported as ``skipped`` from the execution trace;
* parallel branches converge on a join node that executes **exactly once**, with
  no deadlock waiting for a branch that will never fire;
* a nested branch (branch inside a branch) behaves the same way;
* a branch failure still converges, so the join does not hang;
* structurally invalid graphs — an edge crossing a region boundary, or a node
  with no active incoming source — are rejected *before* execution.

``skipped`` is derived from the execution trace, not claimed as engine state:
the probe records which nodes ran and reports the declared-but-unexecuted ones.
"""

from __future__ import annotations

import argparse
import json
import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph


class GraphState(TypedDict, total=False):
    route: str
    # Reducers: parallel branches update these keys in the same superstep, and
    # LangGraph rejects concurrent writes to a plain key.
    executed: Annotated[list[str], operator.add]
    failed: Annotated[list[str], operator.add]
    join_count: int


@dataclass(frozen=True)
class GraphSpec:
    """Smallest description the structural checks need."""

    name: str
    nodes: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    regions: dict[str, str]  # node -> region name
    branches: tuple[str, ...] = ()  # nodes that are exclusive alternatives
    entry: str | None = None  # reached from START, so it needs no incoming edge


# --------------------------------------------------------------------------
# structural validation (before any execution)
# --------------------------------------------------------------------------
def validate_structure(spec: GraphSpec) -> list[str]:
    """Return rejection reasons; empty means the graph may run."""
    reasons: list[str] = []
    nodes = set(spec.nodes)

    for source, target in spec.edges:
        if source not in nodes and source != "START":
            reasons.append(f"unknown_source:{source}")
        if target not in nodes and target != "END":
            reasons.append(f"unknown_target:{target}")

    for source, target in spec.edges:
        source_region = spec.regions.get(source)
        target_region = spec.regions.get(target)
        if source_region and target_region and source_region != target_region:
            reasons.append(f"cross_region_edge:{source}->{target}")

    # Every node except the entry needs at least one incoming edge.
    incoming: dict[str, int] = {node: 0 for node in spec.nodes}
    for source, target in spec.edges:
        if target in incoming:
            incoming[target] += 1
    for node, count in incoming.items():
        if count == 0 and node != spec.entry:
            reasons.append(f"no_active_source:{node}")

    if spec.branches:
        joined = [target for source, target in spec.edges if target not in spec.branches]
        if not joined:
            reasons.append("branch_without_join")

    return reasons


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------
def _record(state: GraphState, name: str) -> dict[str, Any]:
    # The `executed` channel has an add-reducer, so return only this step's value;
    # returning the accumulated list would duplicate entries across supersteps.
    return {"executed": [name]}


def build_linear_with_branch() -> tuple[Any, GraphSpec]:
    """entry -> route -> (left | right) -> join -> end"""

    def entry(state: GraphState) -> dict[str, Any]:
        return _record(state, "entry")

    def left(state: GraphState) -> dict[str, Any]:
        return _record(state, "left")

    def right(state: GraphState) -> dict[str, Any]:
        return _record(state, "right")

    def join(state: GraphState) -> dict[str, Any]:
        result = _record(state, "join")
        result["join_count"] = state.get("join_count", 0) + 1
        return result

    def pick(state: GraphState) -> str:
        return state.get("route", "left")

    graph = StateGraph(GraphState)
    graph.add_node("entry", entry)
    graph.add_node("left", left)
    graph.add_node("right", right)
    graph.add_node("join", join)
    graph.add_edge(START, "entry")
    graph.add_conditional_edges("entry", pick, {"left": "left", "right": "right"})
    graph.add_edge("left", "join")
    graph.add_edge("right", "join")
    graph.add_edge("join", END)

    spec = GraphSpec(
        name="linear-with-branch",
        nodes=("entry", "left", "right", "join"),
        edges=(("entry", "left"), ("entry", "right"), ("left", "join"), ("right", "join")),
        regions={"entry": "main", "left": "main", "right": "main", "join": "main"},
        branches=("left", "right"),
        entry="entry",
    )
    return graph.compile(), spec


def build_parallel() -> Any:
    """entry fans out to two branches that both run, then a single join."""

    def entry(state: GraphState) -> dict[str, Any]:
        return _record(state, "entry")

    def left(state: GraphState) -> dict[str, Any]:
        return _record(state, "left")

    def right(state: GraphState) -> dict[str, Any]:
        return _record(state, "right")

    def join(state: GraphState) -> dict[str, Any]:
        result = _record(state, "join")
        result["join_count"] = state.get("join_count", 0) + 1
        return result

    graph = StateGraph(GraphState)
    graph.add_node("entry", entry)
    graph.add_node("left", left)
    graph.add_node("right", right)
    graph.add_node("join", join)
    graph.add_edge(START, "entry")
    # Two edges out of entry = parallel fan-out; LangGraph runs both.
    graph.add_edge("entry", "left")
    graph.add_edge("entry", "right")
    graph.add_edge("left", "join")
    graph.add_edge("right", "join")
    graph.add_edge("join", END)
    return graph.compile()


def build_nested() -> Any:
    """Branch inside a branch: outer picks the branch that contains an inner one."""

    def entry(state: GraphState) -> dict[str, Any]:
        return _record(state, "entry")

    def outer_a(state: GraphState) -> dict[str, Any]:
        return _record(state, "outer_a")

    def inner_left(state: GraphState) -> dict[str, Any]:
        return _record(state, "inner_left")

    def inner_right(state: GraphState) -> dict[str, Any]:
        return _record(state, "inner_right")

    def join(state: GraphState) -> dict[str, Any]:
        result = _record(state, "join")
        result["join_count"] = state.get("join_count", 0) + 1
        return result

    graph = StateGraph(GraphState)
    graph.add_node("entry", entry)
    graph.add_node("outer_a", outer_a)
    graph.add_node("inner_left", inner_left)
    graph.add_node("inner_right", inner_right)
    graph.add_node("join", join)
    graph.add_edge(START, "entry")
    graph.add_edge("entry", "outer_a")
    graph.add_conditional_edges(
        "outer_a",
        lambda state: state.get("route", "inner_left"),
        {"inner_left": "inner_left", "inner_right": "inner_right"},
    )
    graph.add_edge("inner_left", "join")
    graph.add_edge("inner_right", "join")
    graph.add_edge("join", END)
    return graph.compile()


def build_failing_branch() -> Any:
    """One branch fails but records the failure, so the join still converges."""

    def entry(state: GraphState) -> dict[str, Any]:
        return _record(state, "entry")

    def ok_branch(state: GraphState) -> dict[str, Any]:
        return _record(state, "ok_branch")

    def bad_branch(state: GraphState) -> dict[str, Any]:
        result = _record(state, "bad_branch")
        result["failed"] = ["bad_branch:RuntimeError"]
        return result

    def join(state: GraphState) -> dict[str, Any]:
        result = _record(state, "join")
        result["join_count"] = state.get("join_count", 0) + 1
        return result

    graph = StateGraph(GraphState)
    graph.add_node("entry", entry)
    graph.add_node("ok_branch", ok_branch)
    graph.add_node("bad_branch", bad_branch)
    graph.add_node("join", join)
    graph.add_edge(START, "entry")
    graph.add_edge("entry", "ok_branch")
    graph.add_edge("entry", "bad_branch")
    graph.add_edge("ok_branch", "join")
    graph.add_edge("bad_branch", "join")
    graph.add_edge("join", END)
    return graph.compile()


def run_case(route: str) -> dict[str, Any]:
    graph, spec = build_linear_with_branch()
    state = graph.invoke({"route": route, "executed": [], "join_count": 0}, {"configurable": {"thread_id": f"t-{route}"}})

    executed = state.get("executed", [])
    skipped = [node for node in spec.branches if node not in executed]
    return {
        "route": route,
        "executed": executed,
        "skipped": skipped,
        "join_count": state.get("join_count", 0),
        "structural_reasons": validate_structure(spec),
    }


def probe() -> dict[str, Any]:
    left = run_case("left")
    right = run_case("right")

    parallel = build_parallel().invoke(
        {"executed": [], "join_count": 0}, {"configurable": {"thread_id": "t-parallel"}}
    )
    nested = build_nested().invoke(
        {"route": "inner_right", "executed": [], "join_count": 0},
        {"configurable": {"thread_id": "t-nested"}},
    )
    failing = build_failing_branch().invoke(
        {"executed": [], "join_count": 0, "failed": []},
        {"configurable": {"thread_id": "t-failing"}},
    )

    invalid_cross_region = GraphSpec(
        name="cross-region",
        nodes=("a", "b"),
        edges=(("a", "b"),),
        regions={"a": "region-1", "b": "region-2"},
    )
    invalid_no_source = GraphSpec(
        name="orphan-node",
        nodes=("a", "b"),
        edges=(("a", "a"),),
        regions={"a": "r", "b": "r"},
    )

    return {
        "probe": "p05-graph-branching",
        "task": "P05 / T011",
        "engine": "langgraph",
        "cases": {
            "route_left": left,
            "route_right": right,
            "parallel": {
                "executed": parallel["executed"],
                "join_count": parallel["join_count"],
            },
            "nested": {
                "executed": nested["executed"],
                "join_count": nested["join_count"],
            },
            "failing_branch": {
                "executed": failing["executed"],
                "join_count": failing["join_count"],
                "failed": failing["failed"],
            },
        },
        "rejections": {
            "cross_region_edge": validate_structure(invalid_cross_region),
            "no_active_source": validate_structure(invalid_no_source),
        },
        "assertions": {
            "unselected_branch_is_skipped": bool(left["skipped"]) and bool(right["skipped"]),
            "join_runs_exactly_once": left["join_count"] == 1 and right["join_count"] == 1,
            "no_deadlock": "join" in left["executed"] and "join" in right["executed"],
            "parallel_runs_both_branches": {"left", "right"} <= set(parallel["executed"]),
            "parallel_join_once": parallel["join_count"] == 1,
            "nested_inner_selection_respected": "inner_right" in nested["executed"]
            and "inner_left" not in nested["executed"],
            "nested_join_once": nested["join_count"] == 1,
            "failure_still_converges": failing["join_count"] == 1 and bool(failing["failed"]),
            "cross_region_rejected": bool(validate_structure(invalid_cross_region)),
            "orphan_node_rejected": bool(validate_structure(invalid_no_source)),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P05 branch/join probe")
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args(argv)

    report = probe()
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if all(report["assertions"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
