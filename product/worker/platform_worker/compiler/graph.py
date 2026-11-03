"""The F04A linear LangGraph: ``start -> python -> agent -> end`` only.

This is deliberately not a scheduler. The graph is built from the *declared*
linear edges of an already validated F04A definition; any branch, join, extra
port or unknown node is refused before LangGraph is constructed. The production
run runtime for F04A remains the Go supervisor (T042/T043) — this module proves
the declared data flow with an injected ``NodeAdapter`` seam and is executed by
the Python side of the compiler (stub adapters first, real adapters later).

The ``langgraph`` import is lazy so the validation tests and the Node parity
tooling can import this module under an interpreter without LangGraph installed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, TypedDict

from .validate import F04A_ORDER, Problem, validate

SUCCEEDED = "succeeded"
FAILED = "failed"
WAITING = "waiting"
NEEDS_RECONCILIATION = "needs_reconciliation"
OUTPUT_MISMATCH = "output_mismatch"
MISSING_VALUE = "missing_value"
BAD_OUTCOME = "bad_node_outcome"


class LinearGraphError(ValueError):
    """A stable refusal from the linear graph builder (code + location)."""

    def __init__(self, code: str, pointer: str = "", message: str = "") -> None:
        super().__init__(message or f"{code} at {pointer or '/'}")
        self.code = code
        self.pointer = pointer


@dataclass(frozen=True)
class NodeContext:
    """Everything one node invocation may observe; never a provider object."""

    run_id: str
    node_id: str
    invocation_path: tuple[str, ...]
    inputs: dict[str, Any]
    output_schema: dict[str, Any]


@dataclass(frozen=True)
class NodeOutcome:
    """The normalized result seam. Adapters never return provider objects."""

    status: str
    output: dict[str, Any] | None = None
    code: str = ""
    message: str = ""
    ref: str = ""
    operation: dict[str, Any] | None = None

    @classmethod
    def succeeded(cls, output: Any) -> "NodeOutcome":
        if not isinstance(output, dict):
            return cls(FAILED, code=BAD_OUTCOME,
                       message="a node output must be a JSON object")
        return cls(SUCCEEDED, output=dict(output))

    @classmethod
    def failed(cls, code: str, message: str) -> "NodeOutcome":
        return cls(FAILED, code=code, message=message)

    @classmethod
    def waiting(cls, ref: str) -> "NodeOutcome":
        return cls(WAITING, ref=ref)

    @classmethod
    def needs_reconciliation(cls, operation: Mapping[str, Any]) -> "NodeOutcome":
        return cls(NEEDS_RECONCILIATION, operation=dict(operation))


class NodeAdapter(Protocol):
    """The injected execution seam; the stub and the future real adapters match."""

    def invoke(self, node: dict[str, Any], context: NodeContext) -> NodeOutcome:
        ...


@dataclass(frozen=True)
class NodeRecord:
    node_id: str
    node_type: str
    status: str
    inputs: dict[str, Any]
    output: dict[str, Any] | None = None
    code: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "status": self.status,
            "inputs": self.inputs,
        }
        if self.output is not None:
            record["output"] = self.output
        if self.code:
            record["code"] = self.code
        if self.message:
            record["message"] = self.message
        return record


@dataclass(frozen=True)
class RunResult:
    status: str
    outputs: dict[str, Any]
    records: tuple[NodeRecord, ...] = ()
    failure_code: str = ""
    failure_node_id: str = ""
    message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == SUCCEEDED

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "outputs": self.outputs,
            "records": [record.as_dict() for record in self.records],
        }
        if self.failure_code:
            payload["failure_code"] = self.failure_code
        if self.failure_node_id:
            payload["failure_node_id"] = self.failure_node_id
        if self.message:
            payload["message"] = self.message
        return payload


class _RunState(TypedDict, total=False):
    run_inputs: dict[str, Any]
    outputs: dict[str, dict[str, Any]]
    records: list[dict[str, Any]]
    status: str
    failure_code: str
    failure_node_id: str
    message: str


def _read_pointer(source: Any, pointer: str) -> Any:
    if pointer == "":
        return source
    if not pointer.startswith("/"):
        raise LinearGraphError(MISSING_VALUE, pointer, "a ref pointer must start with /")
    current = source
    for token in pointer[1:].split("/"):
        decoded = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and decoded in current:
            current = current[decoded]
            continue
        if isinstance(current, list) and decoded.isdigit() and int(decoded) < len(current):
            current = current[int(decoded)]
            continue
        raise LinearGraphError(MISSING_VALUE, pointer,
                               f"the ref pointer {pointer} is absent at run time")
    return current


def _resolve_inputs(node: dict[str, Any], run_inputs: dict[str, Any],
                    outputs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for name, binding in node["inputs"].items():
        if "literal" in binding:
            resolved[name] = binding["literal"]
            continue
        head, _separator, pointer = binding["ref"].partition("#")
        if head == "input":
            source: Any = run_inputs
        else:
            target = head[len("node:"):]
            if target not in outputs:
                raise LinearGraphError(MISSING_VALUE, f"/nodes/{node['id']}/inputs/{name}",
                                       f"upstream node {target} has no output")
            source = outputs[target]
        resolved[name] = _read_pointer(source, pointer)
    return resolved


def _check_schema_value(schema: Any, value: Any, pointer: str) -> list[Problem]:
    """A small runtime check of the declared restricted object schema."""
    problems: list[Problem] = []
    if not isinstance(schema, dict):
        return problems
    declared = schema.get("type")
    if declared == "object":
        if not isinstance(value, dict):
            return [Problem("type_mismatch", pointer)]
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        required = required if isinstance(required, list) else []
        for name in required:
            if name not in value:
                problems.append(Problem("missing_value", f"{pointer}/{name}"))
        additional = schema.get("additionalProperties")
        for name, item in value.items():
            if name in properties:
                problems.extend(_check_schema_value(properties[name], item,
                                                    f"{pointer}/{name}"))
            elif additional is False:
                problems.append(Problem("bad_output", f"{pointer}/{name}"))
        return problems
    if declared == "array":
        if not isinstance(value, list):
            return [Problem("type_mismatch", pointer)]
        items = schema.get("items")
        for position, item in enumerate(value):
            problems.extend(_check_schema_value(items, item, f"{pointer}/{position}"))
        return problems
    if declared == "string" and not isinstance(value, str):
        return [Problem("type_mismatch", pointer)]
    if declared == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        return [Problem("type_mismatch", pointer)]
    if declared == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return [Problem("type_mismatch", pointer)]
    if declared == "boolean" and not isinstance(value, bool):
        return [Problem("type_mismatch", pointer)]
    if declared == "null" and value is not None:
        return [Problem("type_mismatch", pointer)]
    return problems


class LinearGraph:
    """A compiled F04A chain: build validates, run drives the LangGraph."""

    def __init__(self, definition: dict[str, Any]) -> None:
        result = validate(definition)
        if result.problems:
            first = result.problems[0]
            raise LinearGraphError(first.code, first.pointer,
                                   "the definition is not an executable F04A linear graph")
        self.definition = definition
        self.nodes = list(definition["nodes"])
        self.edges = list(definition["edges"])
        self.order = [node["id"] for node in self.nodes]
        types = [node["type"] for node in self.nodes]
        if tuple(types) != F04A_ORDER:
            raise LinearGraphError("linear_order", "/nodes/0")
        expected = [
            (self.order[0], self.order[1]),
            (self.order[1], self.order[2]),
            (self.order[2], self.order[3]),
        ]
        if [(edge["source"], edge["target"]) for edge in self.edges] != expected:
            raise LinearGraphError("not_linear_chain", "/edges/0")
        for position, edge in enumerate(self.edges):
            if edge.get("port", "") != "":
                raise LinearGraphError("unsupported_port", f"/edges/{position}/port")

    # -- adapter lookup -----------------------------------------------------
    def _adapter_for(self, node: dict[str, Any],
                     adapters: Mapping[str, NodeAdapter]) -> NodeAdapter:
        candidate = adapters.get(node["id"]) or adapters.get(node["type"])
        if candidate is None or not hasattr(candidate, "invoke"):
            raise LinearGraphError(
                "missing_adapter", f"/nodes/{node['id']}",
                f"no NodeAdapter for node {node['id']} ({node['type']})",
            )
        return candidate

    # -- execution ----------------------------------------------------------
    def run(self, run_inputs: Mapping[str, Any] | None, adapters: Mapping[str, NodeAdapter],
            *, run_id: str = "run") -> RunResult:
        """Invoke the chain in declared order through a LangGraph StateGraph."""
        from langgraph.graph import END, START, StateGraph  # lazy: see module doc

        inputs = dict(run_inputs or {})
        graph = StateGraph(_RunState)

        for node in self.nodes:
            graph.add_node(node["id"], self._node_function(node, adapters, run_id))
        graph.add_edge(START, self.order[0])
        for edge in self.edges:
            graph.add_edge(edge["source"], edge["target"])
        graph.add_edge(self.order[-1], END)
        compiled = graph.compile()
        final = compiled.invoke({
            "run_inputs": inputs,
            "outputs": {},
            "records": [],
            "status": "running",
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
        end_output = final.get("outputs", {}).get(self.order[-1], {})
        if final.get("status") == "running":
            final["status"] = SUCCEEDED
        return RunResult(
            status=final.get("status", SUCCEEDED),
            outputs=end_output if final.get("status") == SUCCEEDED else {},
            records=records,
            failure_code=final.get("failure_code", ""),
            failure_node_id=final.get("failure_node_id", ""),
            message=final.get("message", ""),
        )

    def _node_function(self, node: dict[str, Any],
                       adapters: Mapping[str, NodeAdapter],
                       run_id: str):
        def invoke(state: _RunState) -> dict[str, Any]:
            if state.get("status", "running") != "running":
                return {}
            outputs = dict(state.get("outputs", {}))
            records = list(state.get("records", []))
            if node["type"] == "start":
                resolved = dict(state.get("run_inputs", {}))
            else:
                try:
                    resolved = _resolve_inputs(node, state.get("run_inputs", {}), outputs)
                except LinearGraphError as error:
                    records.append(NodeRecord(
                        node_id=node["id"], node_type=node["type"], status=FAILED,
                        inputs={}, code=error.code, message=str(error),
                    ).as_dict())
                    return {
                        "records": records, "status": FAILED,
                        "failure_code": error.code, "failure_node_id": node["id"],
                        "message": str(error),
                    }
            context = NodeContext(
                run_id=run_id,
                node_id=node["id"],
                invocation_path=tuple(self.order[: self.order.index(node["id"]) + 1]),
                inputs=resolved,
                output_schema=node["output_schema"],
            )
            outcome = self._adapter_for(node, adapters).invoke(node, context)
            if not isinstance(outcome, NodeOutcome):
                outcome = NodeOutcome(
                    FAILED, code=BAD_OUTCOME,
                    message=f"{node['id']} adapter returned {type(outcome).__name__}",
                )
            record = NodeRecord(
                node_id=node["id"], node_type=node["type"], status=outcome.status,
                inputs=resolved, output=outcome.output, code=outcome.code,
                message=outcome.message,
            )
            records.append(record.as_dict())
            if outcome.status == SUCCEEDED:
                # The end node's output *is* the application output object; it is
                # checked against the document's output_schema below, not against
                # the end node's own (usually empty) output_schema.
                problems = [] if node["type"] == "end" else _check_schema_value(
                    node["output_schema"], outcome.output, "",
                )
                if problems:
                    problem = problems[0]
                    records[-1] = NodeRecord(
                        node_id=node["id"], node_type=node["type"], status=FAILED,
                        inputs=resolved, output=outcome.output,
                        code=OUTPUT_MISMATCH,
                        message=f"{problem.code} at {problem.pointer or '/'}",
                    ).as_dict()
                    return {
                        "records": records, "outputs": outputs, "status": FAILED,
                        "failure_code": OUTPUT_MISMATCH, "failure_node_id": node["id"],
                    }
                outputs[node["id"]] = dict(outcome.output or {})
                update: dict[str, Any] = {"records": records, "outputs": outputs}
                if node["type"] == "end":
                    end_problems = _check_schema_value(
                        self.definition["output_schema"], outputs[node["id"]], "",
                    )
                    if end_problems:
                        problem = end_problems[0]
                        update["status"] = FAILED
                        update["failure_code"] = OUTPUT_MISMATCH
                        update["failure_node_id"] = node["id"]
                        update["message"] = (
                            f"end output does not match output_schema: {problem.code} "
                            f"at {problem.pointer or '/'}"
                        )
                return update
            if outcome.status in (WAITING, NEEDS_RECONCILIATION):
                return {
                    "records": records, "outputs": outputs, "status": outcome.status,
                    "failure_code": outcome.code or outcome.status,
                    "failure_node_id": node["id"],
                    "message": outcome.message or outcome.ref,
                }
            return {
                "records": records, "outputs": outputs, "status": FAILED,
                "failure_code": outcome.code or "node_failed",
                "failure_node_id": node["id"], "message": outcome.message,
            }

        return invoke


def build_linear_graph(definition: dict[str, Any]) -> LinearGraph:
    return LinearGraph(definition)


def run_linear_graph(definition: dict[str, Any], run_inputs: Mapping[str, Any],
                     adapters: Mapping[str, NodeAdapter], *, run_id: str = "run") -> RunResult:
    return LinearGraph(definition).run(run_inputs, adapters, run_id=run_id)


def dump_result(result: RunResult) -> str:
    return json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True)
