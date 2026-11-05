"""F04B/T083: the LangGraph structured-region activation proof.

This module is **not** the production runtime. The production graph executor is
Go ``internal/presetexec`` (T083); this module compiles the same validated
condition/parallel/join fixtures into a real LangGraph ``StateGraph`` and records
which nodes LangGraph actually invoked.

What the proof pins, as ``WORKFLOW-SPEC §4`` and ``COMPILER-CONTRACT §4`` require:

* the activation set (nodes LangGraph invoked) and the skipped set (declared nodes
  never invoked) for condition true/default, parallel fork and nested regions;
* the join firing count, which must be exactly one per region activation;
* that an unselected branch never enters an all-of barrier: the graph finishes
  instead of waiting for a branch that was never activated.

It is deliberately narrow: only the structured-region shapes this build can
execute are compiled, and anything else is a typed refusal rather than a
silently different schedule. Node execution goes through the same
``NodeAdapter`` seam as ``compiler/graph.py``; the tests use recording stubs, so
nothing here represents a provider or sandbox call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Mapping, TypedDict

from .graph import (
    LinearGraphError,
    NodeAdapter,
    NodeContext,
    NodeOutcome,
    NodeRecord,
    _resolve_inputs,
)

SUCCEEDED = "succeeded"
SKIPPED = "skipped"
FAILED = "failed"

REGION_NODE_TYPES = {
    "start",
    "end",
    "python",
    "agent",
    "call_app",
    "condition",
    "join",
}

CONDITION_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "exists"}


class RegionGraphError(ValueError):
    """A stable refusal from the region compiler (code + location)."""

    def __init__(self, code: str, pointer: str = "", message: str = "") -> None:
        super().__init__(message or f"{code} at {pointer or '/'}")
        self.code = code
        self.pointer = pointer


@dataclass(frozen=True)
class RegionResult:
    status: str
    outputs: dict[str, dict[str, Any]]
    records: tuple[NodeRecord, ...]
    invoked: tuple[str, ...]
    skipped: tuple[str, ...]
    join_counts: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "outputs": self.outputs,
            "invoked": list(self.invoked),
            "skipped": list(self.skipped),
            "join_counts": self.join_counts,
            "records": [record.as_dict() for record in self.records],
        }


def condition_value_matches(test_case: Mapping[str, Any], bound: Any) -> bool:
    """The DSL's typed comparison, mirroring the Go executor's semantics.

    No coercion: ``eq`` compares JSON type and value (so ``True != "true"`` and
    ``1 != "1"``); numeric operators accept JSON numbers only; ``in`` requires a
    list; ``exists`` tests bound-and-non-null and takes no value.
    """
    operation = test_case.get("op")
    if operation == "exists":
        return bound is not None
    if "value" not in test_case:
        raise RegionGraphError("missing_value", "/config/cases",
                               f"the {operation} case has no value")
    expected = test_case["value"]
    if operation in ("eq", "ne"):
        equal = _json_equal(bound, expected)
        return not equal if operation == "ne" else equal
    if operation in ("gt", "gte", "lt", "lte"):
        if isinstance(bound, bool) or not isinstance(bound, (int, float)):
            raise RegionGraphError("condition_operand_invalid", "/config/cases",
                                   f"the {operation} comparison needs JSON numbers")
        if isinstance(expected, bool) or not isinstance(expected, (int, float)):
            raise RegionGraphError("condition_operand_invalid", "/config/cases",
                                   f"the {operation} comparison needs JSON numbers")
        if operation == "gt":
            return bound > expected
        if operation == "gte":
            return bound >= expected
        if operation == "lt":
            return bound < expected
        return bound <= expected
    if operation == "in":
        if not isinstance(expected, list):
            raise RegionGraphError("condition_operand_invalid", "/config/cases",
                                   "the in operator needs a JSON array")
        return any(_json_equal(bound, item) for item in expected)
    raise RegionGraphError("bad_config", "/config/cases", f"unknown op {operation}")


def selected_port(config: Mapping[str, Any], bindings: Mapping[str, Any]) -> str:
    """First matching case, else ``default_port``; exactly one port is selected."""
    for test_case in config.get("cases", []):
        name = test_case["input"]
        if name not in bindings:
            raise RegionGraphError("node_input_invalid", f"/inputs/{name}",
                                   "the condition case names unbound input")
        if condition_value_matches(test_case, bindings[name]):
            return str(test_case["port"])
    return str(config["default_port"])


def _json_equal(left: Any, right: Any) -> bool:
    # bool is a subclass of int in Python; JSON type equality must not treat
    # true as 1, so compare the JSON type first.
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def _merge_outputs(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None):
    return {**(left or {}), **(right or {})}


def _merge_lists(left: list | None, right: list | None):
    return [*(left or []), *(right or [])]


class _RegionState(TypedDict, total=False):
    run_inputs: dict[str, Any]
    outputs: Annotated[dict[str, dict[str, Any]], _merge_outputs]
    invoked: Annotated[list[str], _merge_lists]
    records: Annotated[list[dict[str, Any]], _merge_lists]
    ports: Annotated[dict[str, str], _merge_outputs]


class RegionGraph:
    """A compiled structured-region graph; build proves, run drives LangGraph."""

    def __init__(self, definition: Mapping[str, Any]) -> None:
        self.definition = dict(definition)
        self.nodes = [dict(node) for node in definition.get("nodes", [])]
        self.edges = [dict(edge) for edge in definition.get("edges", [])]
        self._prove()

    # -- structural proof ---------------------------------------------------
    def _prove(self) -> None:
        if not self.nodes:
            raise RegionGraphError("bad_document", "/nodes", "the definition has no nodes")
        by_id: dict[str, dict[str, Any]] = {}
        for position, node in enumerate(self.nodes):
            node_id = node.get("id")
            if not node_id or node_id in by_id:
                raise RegionGraphError("duplicate_id", f"/nodes/{position}")
            if node.get("type") not in REGION_NODE_TYPES:
                raise RegionGraphError("unknown_type", f"/nodes/{position}")
            by_id[node_id] = node
        self.by_id = by_id
        starts = [node for node in self.nodes if node["type"] == "start"]
        ends = [node for node in self.nodes if node["type"] == "end"]
        if len(starts) != 1 or len(ends) != 1:
            raise RegionGraphError("start_end_count", "/nodes")
        self.start_id = starts[0]["id"]
        self.end_id = ends[0]["id"]
        for position, edge in enumerate(self.edges):
            if edge.get("source") not in by_id or edge.get("target") not in by_id:
                raise RegionGraphError("unknown_endpoint", f"/edges/{position}")
        # Pair every condition with its join and prove each fork really forks.
        paired_join: dict[str, str] = {}
        for position, node in enumerate(self.nodes):
            if node["type"] != "join":
                continue
            config = node.get("config") or {}
            fork_id = config.get("fork_id")
            fork = by_id.get(fork_id)
            if fork is None or fork_id == node["id"]:
                raise RegionGraphError("invalid_region", f"/nodes/{position}/config/fork_id")
            if fork["type"] == "condition":
                if (fork.get("config") or {}).get("join_id") != node["id"]:
                    raise RegionGraphError("invalid_region", f"/nodes/{position}/config/fork_id")
            elif len(self._outgoing(fork_id)) < 2:
                raise RegionGraphError("invalid_region", f"/nodes/{position}/config/fork_id")
            if fork_id in paired_join:
                raise RegionGraphError("invalid_region", f"/nodes/{position}/config/fork_id")
            paired_join[fork_id] = node["id"]
        for position, node in enumerate(self.nodes):
            if node["type"] != "condition":
                continue
            join_id = (node.get("config") or {}).get("join_id")
            join = by_id.get(join_id)
            if join is None or join["type"] != "join" or paired_join.get(node["id"]) != join_id:
                raise RegionGraphError("invalid_region", f"/nodes/{position}/config/join_id")
            allowed = {
                test_case["port"] for test_case in (node.get("config") or {}).get("cases", [])
            }
            allowed.add((node.get("config") or {}).get("default_port"))
            for edge in self._outgoing(node["id"]):
                if edge.get("port", "default") not in allowed:
                    raise RegionGraphError("bad_port", f"/edges/{self._edge_index(edge)}")
        # Only a join may merge paths.
        for position, node in enumerate(self.nodes):
            if node["type"] == "join":
                continue
            incoming = [edge for edge in self.edges if edge["target"] == node["id"]]
            if node["id"] != self.start_id and len(incoming) > 1:
                raise RegionGraphError("merge_without_join", f"/nodes/{position}")
        # Simple cycle check: Kahn's algorithm.
        remaining = {node["id"]: len(self._incoming(node["id"])) for node in self.nodes}
        ready = [node_id for node_id, degree in remaining.items() if degree == 0]
        seen = 0
        while ready:
            node_id = ready.pop()
            seen += 1
            for edge in self._outgoing(node_id):
                remaining[edge["target"]] -= 1
                if remaining[edge["target"]] == 0:
                    ready.append(edge["target"])
        if seen != len(self.nodes):
            raise RegionGraphError("cycle", "")

    def _outgoing(self, node_id: str) -> list[dict[str, Any]]:
        return [edge for edge in self.edges if edge["source"] == node_id]

    def _incoming(self, node_id: str) -> list[dict[str, Any]]:
        return [edge for edge in self.edges if edge["target"] == node_id]

    def _edge_index(self, edge: Mapping[str, Any]) -> int:
        for position, candidate in enumerate(self.edges):
            if candidate is edge:
                return position
        return self.edges.index(dict(edge))

    # -- execution ----------------------------------------------------------
    def run(
        self,
        run_inputs: Mapping[str, Any] | None,
        adapters: Mapping[str, NodeAdapter],
        *,
        run_id: str = "run",
    ) -> RegionResult:
        from langgraph.graph import END, START, StateGraph  # lazy import

        inputs = dict(run_inputs or {})
        graph = StateGraph(_RegionState)
        for node in self.nodes:
            graph.add_node(node["id"], self._node_function(node, adapters, run_id))
        graph.add_edge(START, self.start_id)
        conditional: dict[str, dict[str, str]] = {}
        added: set[tuple[str, str]] = set()
        for edge in self.edges:
            source = self.by_id[edge["source"]]
            if source["type"] == "condition":
                port = edge.get("port", "default")
                mapping = conditional.setdefault(source["id"], {})
                if port in mapping and mapping[port] != edge["target"]:
                    raise RegionGraphError("bad_port", f"/edges/{self._edge_index(edge)}",
                                           "two branch targets claim one port")
                mapping[port] = edge["target"]
                continue
            key = (edge["source"], edge["target"])
            if key in added:
                continue
            added.add(key)
            graph.add_edge(edge["source"], edge["target"])
        for condition in self.nodes:
            if condition["type"] != "condition":
                continue
            mapping = conditional.get(condition["id"], {})
            graph.add_conditional_edges(
                condition["id"], self._route_function(str(condition["id"])), mapping
            )
        for node in self.nodes:
            if node["type"] == "end":
                graph.add_edge(node["id"], END)
        compiled = graph.compile()
        final = compiled.invoke({
            "run_inputs": inputs,
            "outputs": {},
            "invoked": [],
            "records": [],
            "ports": {},
        })
        records = tuple(
            NodeRecord(
                node_id=record["node_id"],
                node_type=record["node_type"],
                status=record["status"],
                inputs=record["inputs"],
                output=record.get("output"),
                code=record.get("code", ""),
                message=record.get("message", ""),
            )
            for record in final.get("records", [])
        )
        invoked = tuple(final.get("invoked", []))
        invoked_set = set(invoked)
        skipped = tuple(node["id"] for node in self.nodes if node["id"] not in invoked_set)
        join_counts: dict[str, int] = {}
        for node in self.nodes:
            if node["type"] == "join":
                join_counts[node["id"]] = sum(1 for record in records if record.node_id == node["id"])
        return RegionResult(
            status=final.get("status", SUCCEEDED),
            outputs=dict(final.get("outputs", {})),
            records=records,
            invoked=invoked,
            skipped=skipped,
            join_counts=join_counts,
        )

    def _route_function(self, condition_id: str):
        def route(state: _RegionState) -> str:
            return state.get("ports", {}).get(condition_id, "")

        return route

    def _node_function(
        self,
        node: dict[str, Any],
        adapters: Mapping[str, NodeAdapter],
        run_id: str,
    ):
        node_type = node["type"]

        def record(state: _RegionState, output: dict[str, Any] | None = None,
                   ) -> dict[str, Any]:
            entry = NodeRecord(
                node_id=node["id"],
                node_type=node_type,
                status=SUCCEEDED,
                inputs={},
                output=output,
            )
            update: dict[str, Any] = {
                "invoked": [node["id"]],
                "records": [entry.as_dict()],
            }
            if output is not None:
                update["outputs"] = {node["id"]: output}
            return update

        def invoke(state: _RegionState) -> dict[str, Any]:
            outputs = state.get("outputs", {})
            if node_type == "start":
                return record(state, dict(state.get("run_inputs", {})))
            if node_type == "condition":
                bindings = _resolve_inputs(node, state.get("run_inputs", {}), outputs)
                port = selected_port(node.get("config") or {}, bindings)
                update = record(state, {"port": port})
                update["ports"] = {node["id"]: port}
                return update
            if node_type == "join":
                sources = [edge["source"] for edge in self._incoming(node["id"])]
                results: dict[str, Any] = {}
                for source in sources:
                    if source in outputs:
                        results[source] = {"status": SUCCEEDED, "value": outputs[source]}
                    else:
                        results[source] = {"status": SKIPPED}
                return record(state, {"results": results})
            if node_type == "end":
                bindings = _resolve_inputs(node, state.get("run_inputs", {}), outputs)
                return record(state, dict(bindings))
            adapters_for = adapters.get(node["id"]) or adapters.get(node_type)
            if adapters_for is None or not hasattr(adapters_for, "invoke"):
                raise RegionGraphError("missing_adapter", f"/nodes/{node['id']}")
            bindings = _resolve_inputs(node, state.get("run_inputs", {}), outputs)
            context = NodeContext(
                run_id=run_id,
                node_id=node["id"],
                invocation_path=(node["id"],),
                inputs=bindings,
                output_schema=node.get("output_schema") or {},
            )
            outcome = adapters_for.invoke(node, context)
            if not isinstance(outcome, NodeOutcome):
                raise RegionGraphError("bad_node_outcome", f"/nodes/{node['id']}")
            if outcome.status != SUCCEEDED:
                raise RegionGraphError(outcome.code or "node_failed", f"/nodes/{node['id']}")
            return record(state, dict(outcome.output or {}))

        return invoke


def build_region_graph(definition: Mapping[str, Any]) -> RegionGraph:
    return RegionGraph(definition)


def run_region_graph(
    definition: Mapping[str, Any],
    run_inputs: Mapping[str, Any] | None,
    adapters: Mapping[str, NodeAdapter],
    *,
    run_id: str = "run",
) -> RegionResult:
    return RegionGraph(definition).run(run_inputs, adapters, run_id=run_id)
