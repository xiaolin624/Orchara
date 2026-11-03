"""F04A linear four-node defensive validator — Python parity copy of the Go authority.

The Go validator (``internal/workflow``) remains the publish authority. This module
re-validates the *F04A subset only* — a single linear chain
``start -> python -> agent -> end`` — before the Python runtime executes anything.
It deliberately does not implement F04B branch/region rules; those documents are
refused with the stable F04A reasons below even when the full ten-node Go
validator would accept them.

Shared-corpus contract: for documents that stay inside the F04A linear subset the
reason codes and JSON pointers are byte-for-byte the Go ones (``bad_document``,
``bad_id``, ``unknown_type``, ``bad_binding``, ``bad_schema``, ``bad_config``,
``bad_edge``, ``bad_policy``, ``duplicate_id``, ``unknown_endpoint``,
``start_end_count``, ``cycle``, ``unreachable_node``, ``cannot_reach_end``,
``merge_without_join``, ``bad_pointer``, ``unknown_ref``, ``non_ancestor_ref``,
``missing_value``). F04A-only refusals use these additional stable codes:
``unsupported_node_type``, ``linear_order``, ``not_linear_chain``,
``unsupported_port``, ``unsupported_subapp``, ``bad_output_mapping``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from . import jcs

# ---------------------------------------------------------------------------
# The focused rule subset (copied from contracts/workflow.schema.json +
# internal/workflow/rules.json; never read from probes/ at runtime).
# ---------------------------------------------------------------------------
DSL_VERSION = "1.0"
TOP_KEYS = ("dsl_version", "edges", "input_schema", "nodes", "output_schema", "policy")
NODE_KEYS = ("config", "id", "inputs", "output_schema", "type")
EDGE_REQUIRED_KEYS = ("source", "target")
EDGE_KEYS = ("port", "source", "target")
CONTRACT_NODE_TYPES = (
    "start", "end", "model", "agent", "python", "call_app", "retrieve",
    "condition", "join", "approval",
)
F04A_NODE_TYPES = ("start", "python", "agent", "end")
F04A_ORDER = ("start", "python", "agent", "end")
SCHEMA_KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "items", "enum",
    "const", "minimum", "maximum", "minLength", "maxLength", "minItems",
    "maxItems", "uniqueItems", "title", "description", "format",
})
PATTERNS = {
    "node_id": re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"),
    "ref": re.compile(r"^(input|node:[A-Za-z][A-Za-z0-9_-]*)#(/.*)?$"),
    "uuid": re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    ),
    "budget": re.compile(r"^[0-9]+(\.[0-9]{1,8})?$"),
}
MAX_SCHEMA_DEPTH = 8
MIN_NODES = 2
MAX_NODES = 100
MAX_EDGES = 400
CONFIG_SPECS: dict[str, dict[str, Any]] = {
    "start": {"required": (), "fields": {}},
    "end": {"required": (), "fields": {}},
    "python": {
        "required": ("tool_version",),
        "fields": {"tool_version": {"kind": "uuid"}},
    },
    "agent": {
        "required": (
            "instructions", "max_turns", "model_profile_version",
            "subapp_versions", "tool_versions",
        ),
        "fields": {
            "instructions": {"kind": "string"},
            "max_turns": {"kind": "integer", "min": 1, "max": 50},
            "model_profile_version": {"kind": "uuid"},
            "subapp_versions": {"kind": "uuid_array"},
            "tool_versions": {"kind": "uuid_array"},
        },
    },
}
POLICY_SPEC: dict[str, Any] = {
    "required": (
        "budget_limit", "currency", "max_active_seconds", "max_child_calls",
        "max_child_depth",
    ),
    "fields": {
        "budget_limit": {"kind": "budget"},
        "currency": {"kind": "string", "min_len": 3, "max_len": 3},
        "max_active_seconds": {"kind": "integer", "min": 1, "max": 3600},
        "max_child_calls": {"kind": "integer", "min": 0, "max": 12},
        "max_child_depth": {"kind": "integer", "min": 0, "max": 3},
    },
}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Problem:
    code: str
    pointer: str = ""

    def as_wire(self) -> dict[str, str]:
        return {"code": self.code, "pointer": self.pointer}


@dataclass(frozen=True)
class ValidationResult:
    input_sha256: str
    problems: tuple[Problem, ...] = ()
    data_canonical: str | None = None
    data_sha256: str | None = None

    @property
    def verdict(self) -> str:
        return "invalid" if self.problems else "valid"

    @property
    def reason(self) -> str | None:
        return self.problems[0].code if self.problems else None

    def reasons(self) -> list[str]:
        ordered: list[str] = []
        for problem in self.problems:
            if problem.code not in ordered:
                ordered.append(problem.code)
        return ordered

    def as_wire(self) -> dict[str, Any]:
        """The same wire shape as Go's ``workflow.WireResult``."""
        out: dict[str, Any] = {
            "verdict": self.verdict,
            "input_sha256": self.input_sha256,
        }
        if self.problems:
            out["reason"] = self.reason
            out["reasons"] = self.reasons()
            out["problems"] = [problem.as_wire() for problem in self.problems]
            return out
        out["data_sha256"] = self.data_sha256
        out["data_canonical"] = self.data_canonical
        return out


class _Problems:
    def __init__(self) -> None:
        self.items: list[Problem] = []

    def add(self, code: str, pointer: str = "") -> None:
        problem = Problem(code, pointer)
        if problem not in self.items:
            self.items.append(problem)

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)


# ---------------------------------------------------------------------------
# small helpers, identical semantics to the Go/P05 copies
# ---------------------------------------------------------------------------
def escape_pointer(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def unescape_pointer(token: str) -> str | None:
    out: list[str] = []
    index = 0
    while index < len(token):
        char = token[index]
        if char != "~":
            out.append(char)
            index += 1
            continue
        if index + 1 >= len(token):
            return None
        code = token[index + 1]
        if code == "0":
            out.append("~")
        elif code == "1":
            out.append("/")
        else:
            return None
        index += 2
    return "".join(out)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and value.is_integer()


def matches(name: str, value: Any) -> bool:
    return isinstance(value, str) and PATTERNS[name].match(value) is not None


# ---------------------------------------------------------------------------
# stage 1 — document shape, configs and restricted schemas
# ---------------------------------------------------------------------------
def _stage_schema(document: Any, problems: _Problems) -> bool:
    if not isinstance(document, dict):
        problems.add("bad_document", "")
        return False
    for key in sorted(document):
        if key not in TOP_KEYS:
            problems.add("bad_document", "/" + escape_pointer(key))
            return False
    for key in TOP_KEYS:
        if key not in document:
            problems.add("bad_document", "")
            return False
    if document["dsl_version"] != DSL_VERSION:
        problems.add("bad_document", "/dsl_version")
        return False
    if not isinstance(document["input_schema"], dict):
        problems.add("bad_document", "/input_schema")
        return False
    if not isinstance(document["output_schema"], dict):
        problems.add("bad_document", "/output_schema")
        return False
    nodes = document["nodes"]
    if not isinstance(nodes, list):
        problems.add("bad_document", "/nodes")
        return False
    if not (MIN_NODES <= len(nodes) <= MAX_NODES):
        problems.add("bad_document", "/nodes")
        return False
    for position, node in enumerate(nodes):
        if not isinstance(node, dict):
            problems.add("bad_document", f"/nodes/{position}")
            return False
        for key in sorted(node):
            if key not in NODE_KEYS:
                problems.add("bad_document", f"/nodes/{position}/{escape_pointer(key)}")
                return False
        for key in NODE_KEYS:
            if key not in node:
                problems.add("bad_document", f"/nodes/{position}")
                return False
        if not isinstance(node["id"], str) or not isinstance(node["type"], str):
            problems.add("bad_document", f"/nodes/{position}")
            return False
        if not isinstance(node["inputs"], dict) or not isinstance(node["config"], dict):
            problems.add("bad_document", f"/nodes/{position}")
            return False
        if not isinstance(node["output_schema"], dict):
            problems.add("bad_document", f"/nodes/{position}/output_schema")
            return False
    edges = document["edges"]
    if not isinstance(edges, list):
        problems.add("bad_document", "/edges")
        return False
    if len(edges) > MAX_EDGES:
        problems.add("bad_document", "/edges")
        return False
    for position, edge in enumerate(edges):
        if not isinstance(edge, dict):
            problems.add("bad_document", f"/edges/{position}")
            return False
        for key in EDGE_REQUIRED_KEYS:
            if key not in edge:
                problems.add("bad_document", f"/edges/{position}")
                return False
        if not isinstance(edge["source"], str) or not isinstance(edge["target"], str):
            problems.add("bad_document", f"/edges/{position}")
            return False
    policy = document["policy"]
    if not isinstance(policy, dict):
        problems.add("bad_document", "/policy")
        return False
    for key in POLICY_SPEC["required"]:
        if key not in policy:
            problems.add("bad_document", "/policy")
            return False

    for name in ("input_schema", "output_schema"):
        _check_schema(document[name], "/" + name, 1, problems)
    for position, node in enumerate(nodes):
        _check_node(node, position, problems)
    for position, edge in enumerate(edges):
        for key in sorted(edge):
            if key not in EDGE_KEYS:
                problems.add("bad_edge", f"/edges/{position}/{escape_pointer(key)}")
        if "port" in edge and not isinstance(edge["port"], str):
            problems.add("bad_edge", f"/edges/{position}/port")
    fields = POLICY_SPEC["fields"]
    for name in sorted(policy):
        if name not in fields:
            problems.add("bad_policy", f"/policy/{escape_pointer(name)}")
            continue
        _check_field(policy[name], fields[name], f"/policy/{escape_pointer(name)}",
                     problems, "bad_policy")
    return True


def _check_node(node: dict[str, Any], position: int, problems: _Problems) -> None:
    at = f"/nodes/{position}"
    if not matches("node_id", node["id"]):
        problems.add("bad_id", at)
    node_type = node["type"]
    if node_type not in CONTRACT_NODE_TYPES:
        problems.add("unknown_type", at)
        return
    if node_type not in F04A_NODE_TYPES:
        # Contract-legal, but F04B (branches/joins) or a later ticket owns it.
        problems.add("unsupported_node_type", at)
        return
    if node_type == "start" and node["inputs"]:
        problems.add("bad_binding", f"{at}/inputs")
    for name in sorted(node["inputs"]):
        if not _binding_is_valid(node["inputs"][name]):
            problems.add("bad_binding", f"{at}/inputs/{escape_pointer(name)}")
    _check_schema(node["output_schema"], f"{at}/output_schema", 1, problems)
    _check_config(node_type, node["config"], at, problems)


def _binding_is_valid(binding: Any) -> bool:
    if not isinstance(binding, dict):
        return False
    keys = set(binding)
    if keys == {"literal"}:
        return True
    if keys == {"ref"}:
        return matches("ref", binding["ref"])
    return False


def _check_schema(schema: Any, at: str, depth: int, problems: _Problems) -> None:
    def walk(node: Any, pointer: str, level: int) -> None:
        if level > MAX_SCHEMA_DEPTH or not isinstance(node, dict):
            problems.add("bad_schema", pointer)
            return
        for key in sorted(node):
            if key not in SCHEMA_KEYWORDS:
                problems.add("bad_schema", f"{pointer}/{escape_pointer(key)}")
        if "type" in node:
            declared = node["type"]
            if isinstance(declared, str):
                pass
            elif (isinstance(declared, list) and 1 <= len(declared) <= 2
                  and all(isinstance(item, str) for item in declared)
                  and (len(declared) == 1 or "null" in declared)):
                pass
            else:
                problems.add("bad_schema", f"{pointer}/type")
        properties = node.get("properties")
        if properties is not None:
            if not isinstance(properties, dict):
                problems.add("bad_schema", f"{pointer}/properties")
            else:
                for name in sorted(properties):
                    walk(properties[name], f"{pointer}/properties/{escape_pointer(name)}",
                         level + 1)
        if "items" in node:
            walk(node["items"], f"{pointer}/items", level + 1)
        if "additionalProperties" in node and not isinstance(node["additionalProperties"], bool):
            walk(node["additionalProperties"], f"{pointer}/additionalProperties", level + 1)
        if "required" in node:
            required = node["required"]
            if not isinstance(required, list) or not all(
                isinstance(item, str) for item in required
            ):
                problems.add("bad_schema", f"{pointer}/required")
        for key in ("minLength", "maxLength", "minItems", "maxItems"):
            if key in node and not is_integer(node[key]):
                problems.add("bad_schema", f"{pointer}/{key}")
        for key in ("minimum", "maximum"):
            if key in node and not is_number(node[key]):
                problems.add("bad_schema", f"{pointer}/{key}")
        if "uniqueItems" in node and not isinstance(node["uniqueItems"], bool):
            problems.add("bad_schema", f"{pointer}/uniqueItems")
        if "enum" in node and not isinstance(node["enum"], list):
            problems.add("bad_schema", f"{pointer}/enum")
        for key in ("title", "description", "format"):
            if key in node and not isinstance(node[key], str):
                problems.add("bad_schema", f"{pointer}/{key}")

    if not isinstance(schema, dict) or schema.get("type") != "object":
        problems.add("bad_schema", at)
        walk(schema, at, depth)
        return
    walk(schema, at, depth)


def _check_config(node_type: str, config: dict[str, Any], at: str,
                  problems: _Problems) -> None:
    spec = CONFIG_SPECS[node_type]
    fields = spec["fields"]
    required = set(spec["required"])
    for name in sorted(set(config) | required):
        where = f"{at}/config"
        if name not in fields:
            problems.add("bad_config", where + "/" + escape_pointer(name))
            continue
        if name not in config:
            problems.add("bad_config", where)
            continue
        _check_field(config[name], fields[name], where + "/" + escape_pointer(name),
                     problems, "bad_config")


def _check_field(value: Any, field_spec: dict[str, Any], at: str,
                 problems: _Problems, code: str) -> None:
    kind = field_spec["kind"]
    if kind == "string":
        if not isinstance(value, str):
            problems.add(code, at)
            return
        if "min_len" in field_spec and len(value) < field_spec["min_len"]:
            problems.add(code, at)
        if "max_len" in field_spec and len(value) > field_spec["max_len"]:
            problems.add(code, at)
        return
    if kind in ("uuid", "budget"):
        if not matches(kind, value):
            problems.add(code, at)
        return
    if kind == "enum":
        if not isinstance(value, str) or value not in field_spec["values"]:
            problems.add(code, at)
        return
    if kind == "integer":
        if not is_integer(value):
            problems.add(code, at)
            return
        if value < field_spec["min"] or value > field_spec["max"]:
            problems.add(code, at)
        return
    if kind in ("uuid_array", "string_array"):
        if not isinstance(value, list):
            problems.add(code, at)
            return
        minimum = field_spec.get("min_items", 0)
        if len(value) < minimum:
            problems.add(code, at)
        seen: list[Any] = []
        for item in value:
            valid = matches("uuid", item) if kind == "uuid_array" else isinstance(item, str)
            if not valid:
                problems.add(code, at)
                return
            if item in seen:
                problems.add(code, at)
                return
            seen.append(item)
        return
    problems.add(code, at)


# ---------------------------------------------------------------------------
# stage 2 — identity and endpoints
# ---------------------------------------------------------------------------
def _stage_identity(document: dict[str, Any], problems: _Problems) -> bool:
    nodes = document["nodes"]
    seen: set[str] = set()
    for position, node in enumerate(nodes):
        if node["id"] in seen:
            problems.add("duplicate_id", f"/nodes/{position}")
        seen.add(node["id"])
    for position, edge in enumerate(document["edges"]):
        if edge["source"] not in seen or edge["target"] not in seen:
            problems.add("unknown_endpoint", f"/edges/{position}")
    starts = [node for node in nodes if node["type"] == "start"]
    ends = [node for node in nodes if node["type"] == "end"]
    if len(starts) != 1 or len(ends) != 1:
        problems.add("start_end_count", "")
    return not problems


# ---------------------------------------------------------------------------
# stage 3 — DAG properties (the shared Go subset)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Graph:
    nodes: tuple[dict[str, Any], ...]
    index: dict[str, int]
    adj: dict[str, tuple[str, ...]]
    radj: dict[str, tuple[str, ...]]
    topo: tuple[str, ...]
    start: str
    end: str

    def ancestors(self, node_id: str) -> set[str]:
        seen: set[str] = set()
        pending = [node_id]
        while pending:
            current = pending.pop()
            for parent in self.radj[current]:
                if parent not in seen:
                    seen.add(parent)
                    pending.append(parent)
        return seen


def _build_graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]],
                 start: str, end: str) -> _Graph:
    index = {node["id"]: position for position, node in enumerate(nodes)}
    adjacency: dict[str, set[str]] = {node["id"]: set() for node in nodes}
    reverse: dict[str, set[str]] = {node["id"]: set() for node in nodes}
    for edge in edges:
        adjacency[edge["source"]].add(edge["target"])
        reverse[edge["target"]].add(edge["source"])
    indegree = {node_id: len(reverse[node_id]) for node_id in index}
    queue = [node["id"] for node in nodes if indegree[node["id"]] == 0]
    topo: list[str] = []
    while queue:
        current = queue.pop(0)
        topo.append(current)
        for target in sorted(adjacency[current], key=index.__getitem__):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return _Graph(
        nodes=tuple(nodes),
        index=index,
        adj={node_id: tuple(sorted(values, key=index.__getitem__))
             for node_id, values in adjacency.items()},
        radj={node_id: tuple(sorted(values, key=index.__getitem__))
              for node_id, values in reverse.items()},
        topo=tuple(topo),
        start=start,
        end=end,
    )


def _stage_graph(document: dict[str, Any], problems: _Problems) -> _Graph | None:
    nodes = list(document["nodes"])
    start = next(node["id"] for node in nodes if node["type"] == "start")
    end = next(node["id"] for node in nodes if node["type"] == "end")
    graph = _build_graph(nodes, document["edges"], start, end)
    if len(graph.topo) != len(nodes):
        problems.add("cycle", "")
        return None
    reachable: set[str] = {start}
    pending = [start]
    while pending:
        current = pending.pop()
        for target in graph.adj[current]:
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    for position, node in enumerate(nodes):
        if node["id"] not in reachable:
            problems.add("unreachable_node", f"/nodes/{position}")
    to_end: set[str] = {end}
    pending = [end]
    while pending:
        current = pending.pop()
        for parent in graph.radj[current]:
            if parent not in to_end:
                to_end.add(parent)
                pending.append(parent)
    for position, node in enumerate(nodes):
        if node["id"] not in to_end:
            problems.add("cannot_reach_end", f"/nodes/{position}")
    for position, node in enumerate(nodes):
        if len(graph.radj[node["id"]]) >= 2 and node["type"] != "join":
            problems.add("merge_without_join", f"/nodes/{position}")
    return graph


# ---------------------------------------------------------------------------
# stage 4 — the F04A linear-four-node shape (refusals the Go authority allows
# but this runtime does not execute)
# ---------------------------------------------------------------------------
def _stage_linear(document: dict[str, Any], graph: _Graph, problems: _Problems) -> None:
    nodes = document["nodes"]
    edges = document["edges"]
    types = [node["type"] for node in nodes]
    if len(nodes) != 4 or tuple(types) != F04A_ORDER:
        expected = list(F04A_ORDER)
        position = next(
            (index for index in range(min(len(types), 4)) if types[index] != expected[index]),
            0,
        )
        problems.add("linear_order", f"/nodes/{position}")
        return
    expected_edges = [
        (nodes[0]["id"], nodes[1]["id"]),
        (nodes[1]["id"], nodes[2]["id"]),
        (nodes[2]["id"], nodes[3]["id"]),
    ]
    actual_edges = [(edge["source"], edge["target"]) for edge in edges]
    for position, edge in enumerate(edges):
        if position < len(expected_edges) and actual_edges[position] == expected_edges[position]:
            continue
        problems.add("not_linear_chain", f"/edges/{position}")
        return
    if len(edges) != len(expected_edges):
        problems.add("not_linear_chain", "/edges")
        return
    for position, edge in enumerate(edges):
        if edge.get("port", "") != "":
            problems.add("unsupported_port", f"/edges/{position}/port")
            return
    agent = nodes[2]
    if agent["config"].get("subapp_versions"):
        problems.add("unsupported_subapp", "/nodes/2/config/subapp_versions")
    end = nodes[3]
    declared = document["output_schema"].get("properties")
    declared_names = set(declared) if isinstance(declared, dict) else set()
    for name in sorted(end["inputs"]):
        if name not in declared_names:
            problems.add("bad_output_mapping",
                         f"/nodes/3/inputs/{escape_pointer(name)}")
    if len(graph.adj[nodes[0]["id"]]) != 1 or len(graph.adj[nodes[1]["id"]]) != 1 \
            or len(graph.adj[nodes[2]["id"]]) != 1:
        problems.add("not_linear_chain", "/edges")


# ---------------------------------------------------------------------------
# stage 5 — references and required output mapping
# ---------------------------------------------------------------------------
_FORBIDDEN = object()


def _pointer_is_declared(schema: Any, pointer: str) -> bool:
    if pointer == "":
        return True
    if not pointer.startswith("/"):
        return False
    current = schema
    for token in pointer[1:].split("/"):
        decoded = unescape_pointer(token)
        if decoded is None:
            return False
        current = _descend(current, decoded)
        if current is _FORBIDDEN:
            return False
    return True


def _descend(schema: Any, token: str) -> Any:
    if not isinstance(schema, dict):
        return _FORBIDDEN
    properties = schema.get("properties")
    if isinstance(properties, dict) and token in properties:
        return properties[token]
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        return additional
    if additional is False:
        return _FORBIDDEN
    if schema.get("type") == "array" and token.isascii() and token.isdigit():
        items = schema.get("items")
        return items if isinstance(items, dict) else {}
    if schema.get("type") in (None, "object"):
        return {}
    return _FORBIDDEN


def _stage_references(document: dict[str, Any], graph: _Graph,
                      problems: _Problems) -> None:
    nodes = document["nodes"]
    index = graph.index
    for position, node in enumerate(nodes):
        for name in sorted(node["inputs"]):
            binding = node["inputs"][name]
            at = f"/nodes/{position}/inputs/{escape_pointer(name)}"
            reference = binding["ref"]
            head, _separator, pointer = reference.partition("#")
            if head == "input":
                if not _pointer_is_declared(document["input_schema"], pointer):
                    problems.add("bad_pointer", at)
                continue
            target = head[len("node:"):]
            target_position = index.get(target)
            if target_position is None:
                problems.add("unknown_ref", at)
                continue
            if not _pointer_is_declared(nodes[target_position]["output_schema"], pointer):
                problems.add("bad_pointer", at)
                continue
            if target == node["id"] or target not in graph.ancestors(node["id"]):
                problems.add("non_ancestor_ref", at)
    # Every required application output must be mapped by end.
    end_position = index[graph.end]
    required_outputs = document["output_schema"].get("required", [])
    for name in sorted(required_outputs):
        if name not in nodes[end_position]["inputs"]:
            problems.add("missing_value",
                         f"/nodes/{end_position}/inputs/{escape_pointer(name)}")


# ---------------------------------------------------------------------------
# normalisation and public entry point
# ---------------------------------------------------------------------------
def normalise(document: dict[str, Any]) -> tuple[str, str]:
    canonical = jcs.canonicalize_value(document)
    return canonical, jcs.sha256_hex(canonical.encode("utf-8"))


def validate(document: Any, *, input_sha256: str | None = None) -> ValidationResult:
    """Validate a definition against the F04A linear subset.

    ``document`` is the parsed JSON object (not text). The result mirrors the Go
    authority's wire shape: verdict plus (code, pointer) problems, or the
    canonical data hash when valid.
    """
    if input_sha256 is None:
        try:
            canonical_input = jcs.canonicalize_value(document)
            input_sha256 = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()
        except jcs.JCSError:
            input_sha256 = ""
    problems = _Problems()
    if not _stage_schema(document, problems) or problems:
        return ValidationResult(input_sha256=input_sha256, problems=tuple(problems.items))
    if not _stage_identity(document, problems):
        return ValidationResult(input_sha256=input_sha256, problems=tuple(problems.items))
    graph = _stage_graph(document, problems)
    if problems or graph is None:
        return ValidationResult(input_sha256=input_sha256, problems=tuple(problems.items))
    _stage_linear(document, graph, problems)
    if problems:
        return ValidationResult(input_sha256=input_sha256, problems=tuple(problems.items))
    _stage_references(document, graph, problems)
    if problems:
        return ValidationResult(input_sha256=input_sha256, problems=tuple(problems.items))
    canonical, digest = normalise(document)
    return ValidationResult(
        input_sha256=input_sha256,
        data_canonical=canonical,
        data_sha256=digest,
    )


def validate_definition(document: Any) -> ValidationResult:
    """Alias used by the graph builder and the worker entry point."""
    return validate(document)


def problems_of(document: Any) -> list[Problem]:
    return list(validate(document).problems)
