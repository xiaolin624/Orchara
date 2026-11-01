#!/usr/bin/env python3
"""P05/T007 — restricted workflow-DSL validator, Python side.

This is the reference half of the dual-language validator. The Go half lives in
``../go`` and must produce byte-identical verdicts over the shared corpus in
``../corpus.json``. Both languages read the same rule tables from
``../rules.json``; nothing here is allowed to be a private convention.

What this validator checks, in the fixed order documented in ``../README.md``:

1. document shape and the restricted JSON Schema subset (schema keywords,
   per-type config whitelists, policy ranges);
2. identity and endpoints (duplicate ids, unknown endpoints, one start/one end);
3. DAG properties (cycle, reachability, merge-only-at-join);
4. structured regions (condition/join pairing, exit ports, cross-region edges,
   join source coverage);
5. references, pointers, dominance and missing values.

A document is rejected with the codes of the *first* stage that produced any
problem, in detection order. A valid document yields two different outputs:

* ``NormalizedWorkflow`` — the data (a canonical normal form plus JCS digest);
* ``WorkflowArtifact``  — the publishable descriptor (schema id + data digest
  + provenance) with its own digest.

The two are deliberately separate objects with separate digests, so provenance
cannot silently change the meaning of the data and vice versa.

Canonicalisation is imported from ``probes/jcs/python/jcs.py`` (P01/T003) rather
than reimplemented; ``../go/jcs.go`` is a port of the same RFC 8785 code and is
re-checked against the shared JCS vectors on every ``go test`` run.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
PROBE_ROOT = HERE.parent
PROBES_ROOT = PROBE_ROOT.parent
JCS_PYTHON = PROBES_ROOT / "jcs" / "python"
if str(JCS_PYTHON) not in sys.path:
    sys.path.insert(0, str(JCS_PYTHON))

import jcs  # noqa: E402  (path set up above)

__all__ = [
    "NormalizedWorkflow",
    "Problem",
    "Rules",
    "ValidationResult",
    "WorkflowArtifact",
    "check_corpus",
    "load_corpus",
    "main",
    "normalise",
    "publish",
    "run_corpus",
    "validate",
    "wire_result",
]

_TOP_KEYS = ("dsl_version", "edges", "input_schema", "nodes", "output_schema", "policy")
_NODE_KEYS = ("config", "id", "inputs", "output_schema", "type")
_EDGE_KEYS = ("port", "source", "target")
_CORPUS_KEYS = ("cases", "probe", "probe_version")
_CASE_KEYS = (
    "expected_canonical",
    "expected_hash",
    "input",
    "input_sha256",
    "name",
    "reason",
    "reasons",
    "source",
    "verdict",
)
_FORBIDDEN = object()
_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    """Public alias: the test re-derives corpus digests with it."""
    return _sha256_bytes(payload)


def _escape(token: str) -> str:
    """RFC 6901 token escaping, used only to build human/机器 locatable pointers."""
    return token.replace("~", "~0").replace("/", "~1")


def _unescape(token: str) -> str | None:
    out: list[str] = []
    index = 0
    while index < len(token):
        char = token[index]
        if char != "~":
            out.append(char)
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in "01":
            return None
        out.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(out)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_integer(value: Any) -> bool:
    if not _is_number(value):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return math.floor(value) == value


def _pattern(rules: "Rules", name: str) -> re.Pattern[str]:
    source = rules.raw["patterns"][name]
    compiled = _PATTERN_CACHE.get(source)
    if compiled is None:
        compiled = re.compile(source, re.ASCII)
        _PATTERN_CACHE[source] = compiled
    return compiled


def _matches(rules: "Rules", name: str, value: Any) -> bool:
    return isinstance(value, str) and _pattern(rules, name).fullmatch(value) is not None


# --------------------------------------------------------------------------
# rule tables
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Rules:
    """The shared rule tables (``../rules.json``) plus the digest of the file."""

    raw: dict[str, Any]
    sha256: str

    @classmethod
    def load(cls, path: str | Path) -> "Rules":
        payload = Path(path).read_bytes()
        return cls(json.loads(payload.decode("utf-8")), _sha256_bytes(payload))

    def codes(self) -> list[str]:
        return [entry["code"] for entry in self.raw["reasons"]]


# --------------------------------------------------------------------------
# data vs artifact
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Problem:
    """A rejection with a stable code and a JSON-Pointer-ish location."""

    code: str
    pointer: str

    def as_wire(self) -> dict[str, str]:
        return {"code": self.code, "pointer": self.pointer}


@dataclass(frozen=True)
class NormalizedWorkflow:
    """The *data*: canonical normal form of a validated document."""

    document: dict[str, Any]
    canonical: str
    sha256: str


@dataclass(frozen=True)
class WorkflowArtifact:
    """The *artifact*: what a publisher would register, provenance included."""

    descriptor: dict[str, Any]
    canonical: str
    sha256: str


@dataclass(frozen=True)
class ValidationResult:
    input_sha256: str
    problems: tuple[Problem, ...]
    data: NormalizedWorkflow | None = None
    artifact: WorkflowArtifact | None = None

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


class _Problems:
    """Ordered, de-duplicated problem list."""

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


# --------------------------------------------------------------------------
# graph context
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _Graph:
    nodes: tuple[dict[str, Any], ...]
    index: dict[str, int]
    adj: dict[str, tuple[str, ...]]
    radj: dict[str, tuple[str, ...]]
    topo: tuple[str, ...]
    start: str
    end: str
    dom: dict[str, frozenset[str]]
    pdom: dict[str, frozenset[str]]

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


def _build_graph(nodes: Iterable[dict[str, Any]], edges: Iterable[dict[str, Any]],
                 start: str, end: str) -> _Graph:
    node_list = tuple(nodes)
    index = {node["id"]: position for position, node in enumerate(node_list)}
    adjacency: dict[str, set[str]] = {node["id"]: set() for node in node_list}
    reverse: dict[str, set[str]] = {node["id"]: set() for node in node_list}
    for edge in edges:
        adjacency[edge["source"]].add(edge["target"])
        reverse[edge["target"]].add(edge["source"])
    # Kahn's algorithm, seeded and extended in document order.
    indegree = {node_id: len(reverse[node_id]) for node_id in index}
    queue = [node["id"] for node in node_list if indegree[node["id"]] == 0]
    topo: list[str] = []
    while queue:
        current = queue.pop(0)
        topo.append(current)
        for target in sorted(adjacency[current], key=index.__getitem__):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    dom: dict[str, frozenset[str]] = {}
    pdom: dict[str, frozenset[str]] = {}
    if len(topo) != len(node_list):
        # Cyclic: dominance is not defined. The caller reports ``cycle`` and
        # stops, so the empty tables are never read.
        return _Graph(
            nodes=node_list,
            index=index,
            adj={node_id: tuple(sorted(values, key=index.__getitem__)) for node_id, values in adjacency.items()},
            radj={node_id: tuple(sorted(values, key=index.__getitem__)) for node_id, values in reverse.items()},
            topo=tuple(topo),
            start=start,
            end=end,
            dom=dom,
            pdom=pdom,
        )
    for node_id in topo:
        parents = reverse[node_id]
        if not parents:
            dom[node_id] = frozenset({node_id})
            continue
        common: set[str] | None = None
        for parent in parents:
            common = set(dom[parent]) if common is None else common & dom[parent]
        dom[node_id] = frozenset(common | {node_id})  # type: ignore[operator]
    for node_id in reversed(topo):
        children = adjacency[node_id]
        if not children:
            pdom[node_id] = frozenset({node_id})
            continue
        common = None
        for child in children:
            common = set(pdom[child]) if common is None else common & pdom[child]
        pdom[node_id] = frozenset(common | {node_id})  # type: ignore[operator]
    return _Graph(
        nodes=node_list,
        index=index,
        adj={node_id: tuple(sorted(values, key=index.__getitem__)) for node_id, values in adjacency.items()},
        radj={node_id: tuple(sorted(values, key=index.__getitem__)) for node_id, values in reverse.items()},
        topo=tuple(topo),
        start=start,
        end=end,
        dom=dom,
        pdom=pdom,
    )


# --------------------------------------------------------------------------
# stage 1 — document shape and restricted schema
# --------------------------------------------------------------------------
def _stage_schema(document: Any, rules: Rules, problems: _Problems) -> bool:
    """Return True when the structural checks passed far enough to go on."""
    if not isinstance(document, dict):
        problems.add("bad_document", "")
        return False
    for key in sorted(document):
        if key not in _TOP_KEYS:
            problems.add("bad_document", "/" + _escape(key))
            return False
    for key in _TOP_KEYS:
        if key not in document:
            problems.add("bad_document", "")
            return False
    if document["dsl_version"] != rules.raw["dsl_version"]:
        problems.add("bad_document", "/dsl_version")
        return False
    if not isinstance(document["input_schema"], dict):
        problems.add("bad_document", "/input_schema")
        return False
    if not isinstance(document["output_schema"], dict):
        problems.add("bad_document", "/output_schema")
        return False
    limit = rules.raw["limit"]
    nodes = document["nodes"]
    if not isinstance(nodes, list):
        problems.add("bad_document", "/nodes")
        return False
    if not (limit["min_nodes"] <= len(nodes) <= limit["max_nodes"]):
        problems.add("bad_document", "/nodes")
        return False
    for position, node in enumerate(nodes):
        if not isinstance(node, dict):
            problems.add("bad_document", f"/nodes/{position}")
            return False
        for key in sorted(node):
            if key not in _NODE_KEYS:
                problems.add("bad_document", f"/nodes/{position}/{_escape(key)}")
                return False
        for key in _NODE_KEYS:
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
    if len(edges) > limit["max_edges"]:
        problems.add("bad_document", "/edges")
        return False
    for position, edge in enumerate(edges):
        if not isinstance(edge, dict):
            problems.add("bad_document", f"/edges/{position}")
            return False
        for key in rules.raw["edge_required_keys"]:
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
    for key in rules.raw["policy"]["required"]:
        if key not in policy:
            problems.add("bad_document", "/policy")
            return False

    # --- finer, per-field checks (all of them; the stage's codes are ordered) --
    for name in ("input_schema", "output_schema"):
        _check_schema(document[name], rules, "/" + name, 1, problems)
    for position, node in enumerate(nodes):
        _check_node(node, position, rules, problems)
    for position, edge in enumerate(edges):
        for key in sorted(edge):
            if key not in _EDGE_KEYS:
                problems.add("bad_edge", f"/edges/{position}/{_escape(key)}")
        if "port" in edge and not isinstance(edge["port"], str):
            problems.add("bad_edge", f"/edges/{position}/port")
    fields = rules.raw["policy"]["fields"]
    for name in sorted(policy):
        if name not in fields:
            problems.add("bad_policy", f"/policy/{_escape(name)}")
            continue
        _check_field(policy[name], fields[name], rules, f"/policy/{_escape(name)}",
                     problems, "bad_policy")
    return True


def _check_node(node: dict[str, Any], position: int, rules: Rules, problems: _Problems) -> None:
    at = f"/nodes/{position}"
    if not _matches(rules, "node_id", node["id"]):
        problems.add("bad_id", at)
    node_type = node["type"]
    if node_type not in rules.raw["node_types"]:
        problems.add("unknown_type", at)
        return  # the config/output whitelist is unknown, do not guess
    if node_type == "start" and node["inputs"]:
        problems.add("bad_binding", f"{at}/inputs")
    for name in sorted(node["inputs"]):
        binding = node["inputs"][name]
        if not _binding_is_valid(binding, rules):
            problems.add("bad_binding", f"{at}/inputs/{_escape(name)}")
    _check_schema(node["output_schema"], rules, f"{at}/output_schema", 1, problems)
    _check_config(node_type, node["config"], rules, at, problems)


def _binding_is_valid(binding: Any, rules: Rules) -> bool:
    if not isinstance(binding, dict):
        return False
    keys = set(binding)
    if keys == {"literal"}:
        return True
    if keys == {"ref"}:
        return _matches(rules, "ref", binding["ref"])
    return False


def _check_schema(schema: Any, rules: Rules, at: str, depth: int, problems: _Problems) -> None:
    keywords = set(rules.raw["schema"]["keywords"])
    max_depth = rules.raw["limit"]["max_schema_depth"]

    def walk(node: Any, pointer: str, level: int) -> None:
        if level > max_depth or not isinstance(node, dict):
            problems.add("bad_schema", pointer)
            return
        for key in sorted(node):
            if key not in keywords:
                problems.add("bad_schema", f"{pointer}/{_escape(key)}")
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
                    walk(properties[name], f"{pointer}/properties/{_escape(name)}", level + 1)
        if "items" in node:
            walk(node["items"], f"{pointer}/items", level + 1)
        if "additionalProperties" in node and not isinstance(node["additionalProperties"], bool):
            walk(node["additionalProperties"], f"{pointer}/additionalProperties", level + 1)
        if "required" in node:
            required = node["required"]
            if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
                problems.add("bad_schema", f"{pointer}/required")
        for key in ("minLength", "maxLength", "minItems", "maxItems"):
            if key in node and not _is_integer(node[key]):
                problems.add("bad_schema", f"{pointer}/{key}")
        for key in ("minimum", "maximum"):
            if key in node and not _is_number(node[key]):
                problems.add("bad_schema", f"{pointer}/{key}")
        if "uniqueItems" in node and not isinstance(node["uniqueItems"], bool):
            problems.add("bad_schema", f"{pointer}/uniqueItems")
        if "enum" in node and not isinstance(node["enum"], list):
            problems.add("bad_schema", f"{pointer}/enum")
        for key in ("title", "description", "format"):
            if key in node and not isinstance(node[key], str):
                problems.add("bad_schema", f"{pointer}/{key}")

    if not isinstance(schema, dict) or schema.get("type") != rules.raw["schema"]["root_type"]:
        problems.add("bad_schema", at)
        walk(schema, at, depth)
        return
    walk(schema, at, depth)


def _check_config(node_type: str, config: dict[str, Any], rules: Rules, at: str,
                  problems: _Problems) -> None:
    spec = rules.raw["config"][node_type]
    fields = spec["fields"]
    required = set(spec["required"])
    for name in sorted(set(config) | required):
        where = f"{at}/config"
        if name not in fields:
            problems.add("bad_config", where + "/" + _escape(name))
            continue
        if name not in config:
            problems.add("bad_config", where)
            continue
        kind = fields[name]["kind"]
        if kind == "condition_cases":
            _check_cases(config[name], rules, where + "/cases", problems)
            continue
        _check_field(config[name], fields[name], rules, where + "/" + _escape(name), problems,
                     "bad_config")


def _check_cases(cases: Any, rules: Rules, at: str, problems: _Problems) -> None:
    spec = rules.raw["condition"]
    if not isinstance(cases, list) or len(cases) < spec["min_cases"]:
        problems.add("bad_config", at)
        return
    required = set(spec["case_required_keys"])
    allowed = set(spec["case_keys"])
    for position, case in enumerate(cases):
        where = f"{at}/{position}"
        if not isinstance(case, dict):
            problems.add("bad_config", where)
            continue
        for key in sorted(case):
            if key not in allowed:
                problems.add("bad_config", f"{where}/{_escape(key)}")
        for key in spec["case_required_keys"]:
            if key not in case:
                problems.add("bad_config", where)
        if "input" in case and not isinstance(case["input"], str):
            problems.add("bad_config", f"{where}/input")
        if "port" in case and not isinstance(case["port"], str):
            problems.add("bad_config", f"{where}/port")
        if "op" in case and case["op"] not in spec["ops"]:
            problems.add("bad_config", f"{where}/op")


def _check_field(value: Any, field_spec: dict[str, Any], rules: Rules, at: str,
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
    if kind == "uuid":
        if not _matches(rules, "uuid", value):
            problems.add(code, at)
        return
    if kind == "budget":
        if not _matches(rules, "budget", value):
            problems.add(code, at)
        return
    if kind == "enum":
        if not isinstance(value, str) or value not in field_spec["values"]:
            problems.add(code, at)
        return
    if kind == "integer":
        if not _is_integer(value):
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
            if kind == "uuid_array":
                valid = _matches(rules, "uuid", item)
            else:
                valid = isinstance(item, str)
            if not valid:
                problems.add(code, at)
                return
            if item in seen:
                problems.add(code, at)
                return
            seen.append(item)
        return
    problems.add(code, at)


# --------------------------------------------------------------------------
# stage 2 — identity and endpoints
# --------------------------------------------------------------------------
def _stage_identity(document: dict[str, Any], rules: Rules, problems: _Problems) -> bool:
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


# --------------------------------------------------------------------------
# stage 3 — DAG properties
# --------------------------------------------------------------------------
def _stage_graph(document: dict[str, Any], rules: Rules, problems: _Problems) -> _Graph | None:
    nodes = document["nodes"]
    node_list = list(nodes)
    start = next(node["id"] for node in node_list if node["type"] == "start")
    end = next(node["id"] for node in node_list if node["type"] == "end")
    graph = _build_graph(node_list, document["edges"], start, end)
    if len(graph.topo) != len(node_list):
        problems.add("cycle", "")
        return None
    reachable: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        for target in graph.adj[current]:
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    reachable.add(start)
    for position, node in enumerate(node_list):
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
    for position, node in enumerate(node_list):
        if node["id"] not in to_end:
            problems.add("cannot_reach_end", f"/nodes/{position}")
    for position, node in enumerate(node_list):
        if len(graph.radj[node["id"]]) >= 2 and node["type"] != "join":
            problems.add("merge_without_join", f"/nodes/{position}")
    return graph


# --------------------------------------------------------------------------
# stage 4 — structured regions
# --------------------------------------------------------------------------
def _stage_regions(document: dict[str, Any], rules: Rules, graph: _Graph,
                   problems: _Problems) -> None:
    nodes = document["nodes"]
    index = graph.index

    # 4a: pairing. Structural only: a region is declared by a condition whose
    # join_id points at a join pointing back, or by a plain fork node whose
    # multiple exits are claimed by a join's fork_id. The entry/exit *dominance*
    # requirement is enforced edge by edge in 4c, which localises the offending
    # edge instead of rejecting a whole region.
    declared_forks = {node["config"]["fork_id"] for node in nodes if node["type"] == "join"}
    for position, node in enumerate(nodes):
        if node["type"] == "condition":
            join_id = index.get(node["config"]["join_id"])
            if (join_id is None or nodes[join_id]["type"] != "join"
                    or nodes[join_id]["config"]["fork_id"] != node["id"]):
                problems.add("invalid_region", f"/nodes/{position}/config/join_id")
    for position, node in enumerate(nodes):
        if node["type"] != "join":
            continue
        at = f"/nodes/{position}/config/fork_id"
        fork_id = node["config"]["fork_id"]
        fork = index.get(fork_id)
        if fork is None or fork_id == node["id"]:
            problems.add("invalid_region", at)
            continue
        fork_node = nodes[fork]
        if fork_node["type"] == "condition":
            if fork_node["config"]["join_id"] != node["id"]:
                problems.add("invalid_region", at)
        elif len(graph.adj[fork_id]) < 2:
            problems.add("invalid_region", at)
    for position, node in enumerate(nodes):
        if node["type"] in ("condition", "join"):
            continue
        if len(graph.adj[node["id"]]) >= 2 and node["id"] not in declared_forks:
            problems.add("invalid_region", f"/nodes/{position}")
    if problems:
        return

    regions = sorted((node["config"]["fork_id"], node["id"])
                     for node in nodes if node["type"] == "join")

    # 4b: exit ports
    for position, node in enumerate(nodes):
        if node["type"] != "condition":
            continue
        config = node["config"]
        allowed = {case["port"] for case in config["cases"]} | {config["default_port"]}
        actual: set[str] = set()
        for edge_position, edge in enumerate(document["edges"]):
            if edge["source"] != node["id"]:
                continue
            port = edge.get("port", "default")
            actual.add(port)
            if port not in allowed:
                problems.add("bad_port", f"/edges/{edge_position}")
        for _port in sorted(allowed - actual):
            problems.add("bad_port", f"/nodes/{position}/config/cases")
    for edge_position, edge in enumerate(document["edges"]):
        source = nodes[index[edge["source"]]]
        if source["type"] != "condition" and "port" in edge and edge["port"] != "default":
            problems.add("bad_port", f"/edges/{edge_position}")
    if problems:
        return

    # 4c: cross-region edges
    for fork_id, join_id in regions:
        inside = {node_id for node_id in index
                  if node_id != fork_id and node_id != join_id
                  and fork_id in graph.dom[node_id] and join_id in graph.pdom[node_id]}
        for edge_position, edge in enumerate(document["edges"]):
            source, target = edge["source"], edge["target"]
            source_inside = source in inside
            target_inside = target in inside
            illegal = False
            if source_inside and not target_inside and target != join_id:
                illegal = True
            elif target_inside and not source_inside and source != fork_id:
                illegal = True
            elif source == fork_id and target != join_id and not target_inside:
                illegal = True
            elif target == join_id and source != fork_id and not source_inside:
                illegal = True
            if illegal:
                problems.add("cross_region_edge", f"/edges/{edge_position}")
    if problems:
        return

    # 4d: join source coverage
    for position, node in enumerate(nodes):
        if node["type"] != "join":
            continue
        config = node["config"]
        required = list(config["required_sources"])
        optional = list(config["optional_sources"])
        declared = required + optional
        actual = sorted({edge["source"] for edge in document["edges"]
                         if edge["target"] == node["id"]})
        if (len(declared) != len(set(declared)) or set(required) & set(optional)
                or sorted(set(declared)) != actual):
            problems.add("bad_join_sources", f"/nodes/{position}/config/required_sources")


# --------------------------------------------------------------------------
# stage 5 — references, pointers, dominance, missing values
# --------------------------------------------------------------------------
def _pointer_is_declared(schema: Any, pointer: str) -> bool:
    """True when the JSON Pointer is reachable inside the declared schema."""
    if pointer == "":
        return True
    if not pointer.startswith("/"):
        return False
    current: Any = schema
    for token in pointer[1:].split("/"):
        decoded = _unescape(token)
        if decoded is None:
            return False
        nxt = _descend(current, decoded)
        if nxt is _FORBIDDEN:
            return False
        current = nxt
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
    if (schema.get("type") == "array" and token.isascii() and token.isdigit()):
        items = schema.get("items")
        return items if isinstance(items, dict) else {}
    if schema.get("type") in (None, "object"):
        return {}  # open object: absence cannot be proven statically
    return _FORBIDDEN


def _template_variables(template: str, rules: Rules) -> list[str]:
    found: list[str] = []
    for match in _pattern(rules, "template_variable").finditer(template):
        name = match.group(1)
        if name not in found:
            found.append(name)
    return found


def _stage_references(document: dict[str, Any], rules: Rules, graph: _Graph,
                      problems: _Problems) -> None:
    nodes = document["nodes"]
    index = graph.index

    # 5a: bindings
    for position, node in enumerate(nodes):
        for name in sorted(node["inputs"]):
            binding = node["inputs"][name]
            at = f"/nodes/{position}/inputs/{_escape(name)}"
            if "literal" in binding:
                continue
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
            elif target not in graph.dom[node["id"]]:
                # An ancestor on only one live path: the value is absent when
                # that path is skipped. Reject instead of inventing a null.
                problems.add("missing_value", at)

    # 5b: prompt templates may only read already bound inputs
    for position, node in enumerate(nodes):
        if node["type"] != "model":
            continue
        template = node["config"]["prompt_template"]
        for variable in _template_variables(template, rules):
            if variable not in node["inputs"]:
                problems.add("missing_value", f"/nodes/{position}/config/prompt_template")

    # 5c: every required application output must be mapped by end
    end_position = index[graph.end]
    required_outputs = document["output_schema"].get("required", [])
    for name in sorted(required_outputs):
        if name not in nodes[end_position]["inputs"]:
            problems.add("missing_value",
                         f"/nodes/{end_position}/inputs/{_escape(name)}")

    # 5d: condition cases compare already bound inputs, with statically legal ops
    spec = rules.raw["condition"]
    for position, node in enumerate(nodes):
        if node["type"] != "condition":
            continue
        for case_position, case in enumerate(node["config"]["cases"]):
            at = f"/nodes/{position}/config/cases/{case_position}"
            if case["input"] not in node["inputs"]:
                problems.add("missing_value", at)
                continue
            op = case["op"]
            if op in spec["valueless_ops"]:
                continue
            if "value" not in case:
                problems.add("missing_value", at)
                continue
            value = case["value"]
            if op in spec["numeric_ops"]:
                if not _is_number(value):
                    problems.add("type_mismatch", at)
                    continue
                binding = node["inputs"][case["input"]]
                if "literal" in binding and not _is_number(binding["literal"]):
                    problems.add("type_mismatch", at)
            elif op in spec["list_ops"] and not isinstance(value, list):
                problems.add("type_mismatch", at)


# --------------------------------------------------------------------------
# normalisation and publication
# --------------------------------------------------------------------------
_SET_VALUED_CONFIG = {
    "agent": ("subapp_versions", "tool_versions"),
    "join": ("optional_sources", "required_sources"),
    "retrieve": ("knowledge_base_ids",),
}


def normalise(document: dict[str, Any]) -> NormalizedWorkflow:
    """Return the canonical normal form of a *validated* document.

    Rules (identical in Go, documented in README.md):

    * ``nodes`` is ordered by node id;
    * every edge carries ``port`` (``"default"`` when the document omits it) and
      ``edges`` is ordered by (source, target, port);
    * set-valued config arrays are ordered.

    Nothing else moves. In particular no input binding is ever invented and no
    absent value is replaced by ``null``.
    """
    normal: dict[str, Any] = {key: copy.deepcopy(document[key]) for key in _TOP_KEYS}
    nodes: list[dict[str, Any]] = []
    for node in document["nodes"]:
        config = copy.deepcopy(node["config"])
        for field in _SET_VALUED_CONFIG.get(node["type"], ()):
            config[field] = sorted(config[field])
        nodes.append({
            "config": config,
            "id": node["id"],
            "inputs": copy.deepcopy(node["inputs"]),
            "output_schema": copy.deepcopy(node["output_schema"]),
            "type": node["type"],
        })
    nodes.sort(key=lambda item: item["id"])
    normal["nodes"] = nodes
    edges = [{
        "port": edge.get("port", "default"),
        "source": edge["source"],
        "target": edge["target"],
    } for edge in document["edges"]]
    edges.sort(key=lambda item: (item["source"], item["target"], item["port"]))
    normal["edges"] = edges
    canonical = jcs.canonicalize_value(normal)
    return NormalizedWorkflow(document=normal, canonical=canonical, sha256=_sha256_text(canonical))


def build_provenance(rules: Rules, *, input_sha256: str, data_sha256: str,
                     corpus_sha256: str, rules_sha256: str) -> dict[str, str]:
    return {
        "corpus_sha256": corpus_sha256,
        "data_sha256": data_sha256,
        "dsl_version": rules.raw["dsl_version"],
        "input_sha256": input_sha256,
        "probe": rules.raw["probe"],
        "probe_version": rules.raw["probe_version"],
        "rules_sha256": rules_sha256,
        "schema_id": rules.raw["schema_id"],
    }


def publish(data: NormalizedWorkflow, provenance: dict[str, str]) -> WorkflowArtifact:
    """Wrap *data* in the publishable descriptor. Provenance cannot touch data."""
    merged = dict(provenance)
    merged["data_sha256"] = data.sha256
    descriptor = {
        "data_sha256": data.sha256,
        "dsl_version": merged["dsl_version"],
        "provenance": merged,
        "schema_id": merged["schema_id"],
    }
    canonical = jcs.canonicalize_value(descriptor)
    return WorkflowArtifact(descriptor=descriptor, canonical=canonical,
                            sha256=_sha256_text(canonical))


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------
def validate(document: Any, rules: Rules, *, input_sha256: str | None = None,
             corpus_sha256: str = "", rules_sha256: str = "") -> ValidationResult:
    if input_sha256 is None:
        input_sha256 = _sha256_text(jcs.canonicalize_value(document))
    problems = _Problems()
    if not _stage_schema(document, rules, problems):
        return ValidationResult(input_sha256, tuple(problems.items))
    if problems:
        return ValidationResult(input_sha256, tuple(problems.items))
    if not _stage_identity(document, rules, problems):
        return ValidationResult(input_sha256, tuple(problems.items))
    graph = _stage_graph(document, rules, problems)
    if problems or graph is None:
        return ValidationResult(input_sha256, tuple(problems.items))
    _stage_regions(document, rules, graph, problems)
    if problems:
        return ValidationResult(input_sha256, tuple(problems.items))
    _stage_references(document, rules, graph, problems)
    if problems:
        return ValidationResult(input_sha256, tuple(problems.items))
    data = normalise(document)
    artifact = publish(data, build_provenance(
        rules,
        input_sha256=input_sha256,
        data_sha256=data.sha256,
        corpus_sha256=corpus_sha256,
        rules_sha256=rules_sha256,
    ))
    return ValidationResult(input_sha256, (), data, artifact)


def wire_result(result: ValidationResult, name: str | None = None) -> dict[str, Any]:
    """Wire form shared with the Go CLI; absent data stays absent, never null."""
    out: dict[str, Any] = {}
    if name is not None:
        out["name"] = name
    out["verdict"] = result.verdict
    out["input_sha256"] = result.input_sha256
    if result.problems:
        out["reason"] = result.reason
        out["reasons"] = result.reasons()
        out["problems"] = [problem.as_wire() for problem in result.problems]
        return out
    out["data_sha256"] = result.data.sha256  # type: ignore[union-attr]
    out["data_canonical"] = result.data.canonical  # type: ignore[union-attr]
    out["artifact_sha256"] = result.artifact.sha256  # type: ignore[union-attr]
    return out


def load_corpus(path: str | Path) -> tuple[dict[str, Any], bytes]:
    payload = Path(path).read_bytes()
    return json.loads(payload.decode("utf-8")), payload


def check_corpus(corpus: Any) -> list[str]:
    """Corpus invariants: every case pins a verdict, an input hash and — for
    invalid cases — an ordered reason list; valid cases pin the normal-form hash.
    ``null`` is never used as a stand-in for "absent"."""
    failures: list[str] = []
    if not isinstance(corpus, dict):
        return ["corpus is not an object"]
    for key in sorted(corpus):
        if key not in _CORPUS_KEYS:
            failures.append(f"unexpected corpus key {key!r}")
    for key in _CORPUS_KEYS:
        if key not in corpus:
            failures.append(f"missing corpus key {key!r}")
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases:
        return failures + ["corpus has no cases"]
    names: set[str] = set()
    for position, case in enumerate(cases):
        where = f"cases[{position}]"
        if not isinstance(case, dict):
            failures.append(f"{where}: not an object")
            continue
        for key in sorted(case):
            if key not in _CASE_KEYS:
                failures.append(f"{where}: unexpected key {key!r}")
        for key in ("name", "input", "verdict", "input_sha256"):
            if key not in case:
                failures.append(f"{where}: missing key {key!r}")
        for key, value in case.items():
            if value is None:
                failures.append(f"{where}.{key}: null is not a value here")
        name = case.get("name")
        if not isinstance(name, str) or not name:
            failures.append(f"{where}: bad name")
        elif name in names:
            failures.append(f"{where}: duplicate name {name!r}")
        else:
            names.add(name)
        verdict = case.get("verdict")
        if verdict == "valid":
            if "reason" in case or "reasons" in case:
                failures.append(f"{where}: a valid case must not pin a reason")
            if not isinstance(case.get("expected_hash"), str) or not case["expected_hash"]:
                failures.append(f"{where}: no expected_hash")
        elif verdict == "invalid":
            if "expected_hash" in case:
                failures.append(f"{where}: an invalid case has no normal form to hash")
            reasons = case.get("reasons")
            if not isinstance(case.get("reason"), str) or not case["reason"]:
                failures.append(f"{where}: no reason")
            if not isinstance(reasons, list) or not reasons or reasons[0] != case.get("reason"):
                failures.append(f"{where}: reasons must start with reason")
        else:
            failures.append(f"{where}: verdict must be valid or invalid")
    return failures


def run_corpus(corpus_path: str | Path, rules_path: str | Path | None = None) -> dict[str, Any]:
    corpus_path = Path(corpus_path)
    rules_path = Path(rules_path) if rules_path is not None else corpus_path.parent / "rules.json"
    corpus, corpus_bytes = load_corpus(corpus_path)
    rules = Rules.load(rules_path)
    failures = check_corpus(corpus)
    if failures:
        raise SystemExit("corpus is not well formed:\n  " + "\n  ".join(failures))
    corpus_sha256 = _sha256_bytes(corpus_bytes)
    cases: list[dict[str, Any]] = []
    for case in corpus["cases"]:
        result = validate(case["input"], rules, input_sha256=case.get("input_sha256"),
                          corpus_sha256=corpus_sha256, rules_sha256=rules.sha256)
        cases.append(wire_result(result, case["name"]))
    return {
        "cases": cases,
        "corpus_sha256": corpus_sha256,
        "probe_version": rules.raw["probe_version"],
        "rules_sha256": rules.sha256,
    }


def _check_mode(corpus: dict[str, Any], output: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    by_name = {case["name"]: case for case in corpus["cases"]}
    for result in output["cases"]:
        case = by_name[result["name"]]
        where = case["name"]
        if result["verdict"] != case["verdict"]:
            failures.append(f"{where}: verdict {result['verdict']} != {case['verdict']}")
            continue
        if result["input_sha256"] != case["input_sha256"]:
            failures.append(f"{where}: input hash differs from the pinned value")
        if case["verdict"] == "valid":
            if result["data_sha256"] != case["expected_hash"]:
                failures.append(f"{where}: normal-form hash differs from the pinned value")
            anchor = case.get("expected_canonical")
            if anchor is not None and result["data_canonical"] != anchor:
                failures.append(f"{where}: normal form differs from the pinned text")
        else:
            if result["reason"] != case["reason"]:
                failures.append(f"{where}: reason {result['reason']} != {case['reason']}")
            if result["reasons"] != case["reasons"]:
                failures.append(f"{where}: reasons {result['reasons']} != {case['reasons']}")
    return failures


def _write_stdout(text: str) -> None:
    """Write UTF-8 bytes regardless of the console/pipe locale."""
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the shared P05 workflow corpus (Python reference).",
    )
    parser.add_argument("--corpus", required=True, help="path to corpus.json")
    parser.add_argument("--rules", default=None, help="path to rules.json")
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero when a case disagrees with its pinned expectation")
    args = parser.parse_args(argv)

    corpus_path = Path(args.corpus)
    rules_path = Path(args.rules) if args.rules else corpus_path.parent / "rules.json"
    corpus, _corpus_bytes = load_corpus(corpus_path)
    rules = Rules.load(rules_path)
    failures = check_corpus(corpus)
    if failures:
        sys.stderr.write("corpus is not well formed:\n  " + "\n  ".join(failures) + "\n")
        return 1
    output = run_corpus(corpus_path, rules_path)
    if args.check:
        mismatches = _check_mode(corpus, output)
        if mismatches:
            sys.stderr.write("corpus disagreement:\n  " + "\n  ".join(mismatches) + "\n")
            return 1
        _write_stdout(f"OK: {len(output['cases'])} cases\n")
        return 0
    _write_stdout(jcs.canonicalize_value(output) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
