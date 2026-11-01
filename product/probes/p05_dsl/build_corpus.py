#!/usr/bin/env python3
"""Author ``corpus.json`` — the shared T007 golden corpus.

The corpus is *generated* so that every fixture is reproducible from this file
and so that the pinned hashes are never hand-maintained. It is not, however,
produced by a second validator: the documents, the expected verdicts, the
expected reason codes and the precedence probes are written here by hand, and
only the digests are filled in by running the Python reference over them. The
Go implementation then has to reproduce those digests independently
(``go test ./...`` and ``product/tests/p05/test_dsl_parity.py``).

Cases are derived from the real contract fixtures in ``examples/`` (sales,
parallel, branch-approval, invalid/cycle) plus spec-derived documents
(``COMPILER-CONTRACT.md`` section 2/4 corpus list: linear, condition, parallel,
nested, skip). Defects are single, named mutations of those documents so a
reviewer can see exactly what each negative case rejects.

Run from the probe directory::

    python build_corpus.py            # rewrite corpus.json
    python build_corpus.py --check    # fail if corpus.json is stale
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBES = HERE.parent
REPO = PROBES.parents[1]
EXAMPLES = REPO / "examples"

sys.path.insert(0, str(HERE / "python"))
import validate  # noqa: E402

POLICY = {
    "max_active_seconds": 900,
    "max_child_depth": 3,
    "max_child_calls": 12,
    "budget_limit": "1.00",
    "currency": "USD",
}

MODEL_CONFIG = {
    "model_profile_version": "22222222-2222-4222-8222-222222222222",
    "prompt_template": "{{task}}",
    "response_mode": "text",
}


def _model_config(template: str) -> dict:
    config = dict(MODEL_CONFIG)
    config["prompt_template"] = template
    return config


def canonical_sha256(document: object) -> str:
    import jcs

    text = jcs.canonicalize_value(document)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_example(relative: str) -> dict:
    return json.loads((EXAMPLES / relative).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# base documents
# --------------------------------------------------------------------------
SALES = load_example("sales/workflow.json")
PARALLEL = load_example("parallel/workflow.json")
APPROVAL = load_example("branch-approval/workflow.json")
CYCLE = load_example("invalid/cycle.json")

MINIMAL = {
    "dsl_version": "1.0",
    "input_schema": {"type": "object"},
    "output_schema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
    "nodes": [
        {"id": "start", "type": "start", "inputs": {}, "output_schema": {"type": "object"},
         "config": {}},
        {"id": "say", "type": "model", "inputs": {"task": {"literal": "hello"}},
         "output_schema": {"type": "object"}, "config": _model_config("{{task}}")},
        {"id": "end", "type": "end", "inputs": {"text": {"ref": "node:say#/text"}},
         "output_schema": {"type": "object"}, "config": {}},
    ],
    "edges": [
        {"source": "start", "target": "say"},
        {"source": "say", "target": "end"},
    ],
    "policy": dict(POLICY),
}

# Hand-written canonical normal form of MINIMAL: nodes ordered by id, every edge
# carrying its materialised "default" port, keys in JCS order. Authored from the
# documented normalisation rules, not produced by the code under test.
MINIMAL_CANONICAL = (
    '{"dsl_version":"1.0","edges":[{"port":"default","source":"say","target":"end"},'
    '{"port":"default","source":"start","target":"say"}],"input_schema":{"type":"object"},'
    '"nodes":[{"config":{},"id":"end","inputs":{"text":{"ref":"node:say#/text"}},'
    '"output_schema":{"type":"object"},"type":"end"},{"config":'
    '{"model_profile_version":"22222222-2222-4222-8222-222222222222",'
    '"prompt_template":"{{task}}","response_mode":"text"},"id":"say",'
    '"inputs":{"task":{"literal":"hello"}},"output_schema":{"type":"object"},'
    '"type":"model"},{"config":{},"id":"start","inputs":{},'
    '"output_schema":{"type":"object"},"type":"start"}],"output_schema":'
    '{"additionalProperties":false,"properties":{"text":{"type":"string"}},'
    '"required":["text"],"type":"object"},"policy":{"budget_limit":"1.00",'
    '"currency":"USD","max_active_seconds":900,"max_child_calls":12,'
    '"max_child_depth":3}}'
)

NESTED = {
    "dsl_version": "1.0",
    "input_schema": {
        "type": "object",
        "properties": {"flag": {"type": "boolean"}},
        "required": ["flag"],
        "additionalProperties": False,
    },
    "output_schema": {
        "type": "object",
        "properties": {"results": {"type": "object"}},
        "required": ["results"],
        "additionalProperties": False,
    },
    "nodes": [
        {"id": "start", "type": "start", "inputs": {}, "output_schema": {"type": "object"},
         "config": {}},
        {
            "id": "choose",
            "type": "condition",
            "inputs": {"flag": {"ref": "input#/flag"}},
            "config": {
                "join_id": "outer_join",
                "cases": [{"input": "flag", "op": "eq", "value": True, "port": "yes"}],
                "default_port": "no",
            },
            "output_schema": {"type": "object"},
        },
        {
            "id": "prep",
            "type": "model",
            "inputs": {"task": {"literal": "拆分"}},
            "config": _model_config("{{task}}"),
            "output_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        {
            "id": "left",
            "type": "model",
            "inputs": {"part": {"ref": "node:prep#/text"}},
            "config": _model_config("{{part}}"),
            "output_schema": {"type": "object"},
        },
        {
            "id": "right",
            "type": "model",
            "inputs": {"part": {"ref": "node:prep#/text"}},
            "config": _model_config("{{part}}"),
            "output_schema": {"type": "object"},
        },
        {
            "id": "inner_join",
            "type": "join",
            "inputs": {},
            "config": {"mode": "all_active", "fork_id": "prep",
                       "required_sources": ["left", "right"], "optional_sources": []},
            "output_schema": {"type": "object"},
        },
        {
            "id": "direct",
            "type": "model",
            "inputs": {"task": {"literal": "直接输出"}},
            "config": _model_config("{{task}}"),
            "output_schema": {"type": "object"},
        },
        {
            "id": "outer_join",
            "type": "join",
            "inputs": {},
            "config": {"mode": "all_active", "fork_id": "choose",
                       "required_sources": ["inner_join", "direct"],
                       "optional_sources": []},
            "output_schema": {
                "type": "object",
                "properties": {"results": {"type": "object"}},
                "required": ["results"],
                "additionalProperties": False,
            },
        },
        {
            "id": "end",
            "type": "end",
            "inputs": {"results": {"ref": "node:outer_join#/results"}},
            "output_schema": {"type": "object"},
            "config": {},
        },
    ],
    "edges": [
        {"source": "start", "target": "choose"},
        {"source": "choose", "target": "prep", "port": "yes"},
        {"source": "choose", "target": "direct", "port": "no"},
        {"source": "prep", "target": "left"},
        {"source": "prep", "target": "right"},
        {"source": "left", "target": "inner_join"},
        {"source": "right", "target": "inner_join"},
        {"source": "inner_join", "target": "outer_join"},
        {"source": "direct", "target": "outer_join"},
        {"source": "outer_join", "target": "end"},
    ],
    "policy": dict(POLICY),
}


def node_index(document: dict, node_id: str) -> int:
    for position, node in enumerate(document["nodes"]):
        if node["id"] == node_id:
            return position
    raise KeyError(node_id)


def node(document: dict, node_id: str) -> dict:
    return document["nodes"][node_index(document, node_id)]


def mutated(base: dict, *changes) -> dict:
    """Deep-copy *base* and apply ``(callable)`` mutations in order."""
    document = copy.deepcopy(base)
    for change in changes:
        change(document)
    return document


def edge_index(document: dict, source: str, target: str, port: str | None = None) -> int:
    for position, edge in enumerate(document["edges"]):
        if edge["source"] == source and edge["target"] == target:
            if port is None or edge.get("port", "default") == port:
                return position
    raise KeyError((source, target, port))


def add_dead_end(document: dict) -> None:
    document["nodes"].append({
        "id": "dead",
        "type": "model",
        "inputs": {"task": {"literal": "never reached"}},
        "config": _model_config("{{task}}"),
        "output_schema": {"type": "object"},
    })
    document["edges"].append({"source": "start", "target": "dead"})


def add_escaping_branch(document: dict) -> None:
    """A third branch leaves the inner parallel fork but bypasses its join."""
    document["nodes"].append({
        "id": "escapee",
        "type": "model",
        "inputs": {"part": {"ref": "node:prep#/text"}},
        "config": _model_config("{{part}}"),
        "output_schema": {"type": "object"},
    })
    document["edges"].append({"source": "prep", "target": "escapee"})
    document["edges"].append({"source": "escapee", "target": "outer_join"})


# --------------------------------------------------------------------------
# cases
# --------------------------------------------------------------------------
VALID_CASES = [
    {
        "name": "anchor-minimal-linear",
        "source": "authored: three-node linear graph, normal form pinned as text",
        "document": MINIMAL,
        "expected_canonical": MINIMAL_CANONICAL,
    },
    {
        "name": "linear-sales",
        "source": "examples/sales/workflow.json",
        "document": SALES,
    },
    {
        "name": "linear-sales-nodes-shuffled",
        "source": "examples/sales/workflow.json with nodes/edges reordered",
        "document": mutated(
            SALES,
            lambda d: d["nodes"].reverse(),
            lambda d: d["edges"].reverse(),
        ),
        "same_hash_as": "linear-sales",
    },
    {
        "name": "parallel-two-branch",
        "source": "examples/parallel/workflow.json",
        "document": PARALLEL,
    },
    {
        "name": "parallel-optional-source",
        "source": "examples/parallel/workflow.json, right branch declared optional",
        "document": mutated(
            PARALLEL,
            lambda d: node(d, "join")["config"].update(
                {"required_sources": ["left"], "optional_sources": ["right"]}),
        ),
    },
    {
        "name": "condition-branch-approval",
        "source": "examples/branch-approval/workflow.json",
        "document": APPROVAL,
    },
    {
        "name": "condition-nested-parallel",
        "source": "authored: condition region containing a parallel fork/join (spec section 4 nesting)",
        "document": NESTED,
    },
]

INVALID_CASES = [
    # --- stage 1: document shape and restricted schema ---------------------
    {
        "name": "unknown-node-type",
        "source": "examples/sales/workflow.json, stats.type := script",
        "document": mutated(SALES, lambda d: node(d, "stats").update({"type": "script"})),
        "reason": "unknown_type",
    },
    {
        "name": "bad-node-id",
        "source": "examples/sales/workflow.json, stats.id := 1stats",
        "document": mutated(SALES, lambda d: node(d, "stats").update({"id": "1stats"})),
        "reason": "bad_id",
    },
    {
        "name": "bad-config-missing-field",
        "source": "examples/sales/workflow.json, stats.config := {}",
        "document": mutated(SALES, lambda d: node(d, "stats").update({"config": {}})),
        "reason": "bad_config",
    },
    {
        "name": "bad-config-extra-field",
        "source": "examples/sales/workflow.json, analysis.config.temperature := 0.2",
        "document": mutated(
            SALES, lambda d: node(d, "analysis")["config"].update({"temperature": 0.2})),
        "reason": "bad_config",
    },
    {
        "name": "bad-binding-expression",
        "source": "examples/sales/workflow.json, stats.inputs.file := {expr: ...}",
        "document": mutated(
            SALES, lambda d: node(d, "stats")["inputs"].update({"file": {"expr": "$.file"}})),
        "reason": "bad_binding",
    },
    {
        "name": "start-inputs-forbidden",
        "source": "examples/sales/workflow.json, start.inputs := {x: literal 1}",
        "document": mutated(
            SALES, lambda d: node(d, "start").update({"inputs": {"x": {"literal": 1}}})),
        "reason": "bad_binding",
    },
    {
        "name": "node-output-schema-not-object",
        "source": "examples/sales/workflow.json, stats.output_schema.type := array",
        "document": mutated(
            SALES, lambda d: node(d, "stats").update({"output_schema": {"type": "array"}})),
        "reason": "bad_schema",
    },
    {
        "name": "bad-edge-extra-key",
        "source": "examples/sales/workflow.json, stats->analysis edge gains a label",
        "document": mutated(
            SALES,
            lambda d: d["edges"][edge_index(d, "stats", "analysis")].update({"label": "x"}),
        ),
        "reason": "bad_edge",
    },
    {
        "name": "bad-policy-budget-limit",
        "source": "examples/sales/workflow.json, policy.budget_limit := 1.000000000",
        "document": mutated(
            SALES, lambda d: d["policy"].update({"budget_limit": "1.000000000"})),
        "reason": "bad_policy",
    },
    {
        "name": "null-inputs-is-not-empty-inputs",
        "source": "examples/sales/workflow.json, start.inputs := null",
        "document": mutated(SALES, lambda d: node(d, "start").update({"inputs": None})),
        "reason": "bad_document",
    },
    # --- stage 2: identity and endpoints ----------------------------------
    {
        "name": "duplicate-node-id",
        "source": "examples/sales/workflow.json, analysis.id := stats",
        "document": mutated(SALES, lambda d: node(d, "analysis").update({"id": "stats"})),
        "reason": "duplicate_id",
    },
    {
        "name": "unknown-edge-endpoint",
        "source": "examples/sales/workflow.json, analysis->end retargeted to ghost",
        "document": mutated(
            SALES,
            lambda d: d["edges"][edge_index(d, "analysis", "end")].update({"target": "ghost"})),
        "reason": "unknown_endpoint",
    },
    {
        "name": "missing-end-node",
        "source": "examples/sales/workflow.json without the end node and its edge",
        "document": mutated(
            SALES,
            lambda d: d["nodes"].pop(node_index(d, "end")),
            lambda d: d["edges"].pop(edge_index(d, "analysis", "end")),
        ),
        "reason": "start_end_count",
    },
    # --- stage 3: DAG properties ------------------------------------------
    {
        "name": "cycle",
        "source": "examples/invalid/cycle.json",
        "document": CYCLE,
        "reason": "cycle",
    },
    {
        "name": "unreachable-node",
        "source": "examples/sales/workflow.json plus an orphan model node",
        "document": mutated(
            SALES,
            lambda d: d["nodes"].append({
                "id": "lonely",
                "type": "model",
                "inputs": {"task": {"literal": "orphan"}},
                "config": _model_config("{{task}}"),
                "output_schema": {"type": "object"},
            }),
        ),
        "reason": "unreachable_node",
        "reasons": ["unreachable_node", "cannot_reach_end"],
    },
    {
        "name": "cannot-reach-end",
        "source": "examples/sales/workflow.json plus a dead end fed by start",
        "document": mutated(SALES, add_dead_end),
        "reason": "cannot_reach_end",
    },
    {
        "name": "merge-without-join",
        "source": "examples/sales/workflow.json, extra stats->end edge merges two paths at end",
        "document": mutated(SALES, lambda d: d["edges"].append(
            {"source": "stats", "target": "end"})),
        "reason": "merge_without_join",
    },
    # --- stage 4: structured regions --------------------------------------
    {
        "name": "join-fork-not-a-fork",
        "source": "examples/branch-approval/workflow.json, join.config.fork_id := direct",
        "document": mutated(
            APPROVAL,
            lambda d: node(d, "join")["config"].update({"fork_id": "direct"}),
        ),
        "reason": "invalid_region",
    },
    {
        "name": "condition-port-not-declared",
        "source": "examples/branch-approval/workflow.json, choose->approve port := oops",
        "document": mutated(
            APPROVAL,
            lambda d: d["edges"][edge_index(d, "choose", "approve")].update({"port": "oops"}),
        ),
        "reason": "bad_port",
    },
    {
        "name": "condition-branch-escapes-outer-join",
        "source": ("condition-nested-parallel plus a third parallel branch from prep that "
                   "skips the inner join and feeds the outer join directly"),
        "document": mutated(NESTED, add_escaping_branch),
        "reason": "cross_region_edge",
    },
    {
        "name": "join-sources-mismatch",
        "source": "examples/branch-approval/workflow.json, required_sources drops direct",
        "document": mutated(
            APPROVAL,
            lambda d: node(d, "join")["config"].update({"required_sources": ["approve"]}),
        ),
        "reason": "bad_join_sources",
    },
    # --- stage 5: references, dominance and missing values ----------------
    {
        "name": "condition-case-unbound-input",
        "source": "examples/branch-approval/workflow.json, case.input := never_bound",
        "document": mutated(
            APPROVAL,
            lambda d: node(d, "choose")["config"]["cases"][0].update({"input": "never_bound"}),
        ),
        "reason": "missing_value",
    },
    {
        "name": "condition-case-missing-value",
        "source": "examples/branch-approval/workflow.json, case loses its comparison value",
        "document": mutated(
            APPROVAL,
            lambda d: node(d, "choose")["config"]["cases"][0].pop("value"),
        ),
        "reason": "missing_value",
    },
    {
        "name": "end-missing-required-output",
        "source": "examples/sales/workflow.json, end no longer maps required summary",
        "document": mutated(
            SALES, lambda d: node(d, "end")["inputs"].pop("summary")),
        "reason": "missing_value",
    },
    {
        "name": "skipped-branch-reference",
        "source": "examples/branch-approval/workflow.json, end reads the conditional approve branch",
        "document": mutated(
            APPROVAL,
            lambda d: node(d, "end")["inputs"].update({"approval": {"ref": "node:approve#"}}),
        ),
        "reason": "missing_value",
    },
    {
        "name": "unbound-template-variable",
        "source": "examples/parallel/workflow.json, left template gains {{extra}}",
        "document": mutated(
            PARALLEL,
            lambda d: node(d, "left")["config"].update(
                {"prompt_template": "{{task}} {{extra}}"}),
        ),
        "reason": "missing_value",
    },
    {
        "name": "non-ancestor-reference",
        "source": "examples/parallel/workflow.json, left reads sibling branch right",
        "document": mutated(
            PARALLEL, lambda d: node(d, "left")["inputs"].update({"peek": {"ref": "node:right#"}})),
        "reason": "non_ancestor_ref",
    },
    {
        "name": "unknown-node-reference",
        "source": "examples/sales/workflow.json, stats.inputs.file := node:ghost#",
        "document": mutated(
            SALES, lambda d: node(d, "stats")["inputs"].update({"file": {"ref": "node:ghost#"}})),
        "reason": "unknown_ref",
    },
    {
        "name": "undeclared-pointer-field",
        "source": "examples/sales/workflow.json, analysis reads node:stats#/nope",
        "document": mutated(
            SALES,
            lambda d: node(d, "analysis")["inputs"].update({"stats": {"ref": "node:stats#/nope"}}),
        ),
        "reason": "bad_pointer",
    },
    {
        "name": "numeric-op-on-string-literal",
        "source": "examples/branch-approval/workflow.json, gt against a string literal",
        "document": mutated(
            APPROVAL,
            lambda d: node(d, "choose").update({"inputs": {"level": {"literal": "high"}}}),
            lambda d: node(d, "choose")["config"]["cases"][0].update(
                {"input": "level", "op": "gt", "value": 3}),
        ),
        "reason": "type_mismatch",
    },
    # --- precedence probes -----------------------------------------------
    {
        "name": "precedence-cycle-over-missing-value",
        "source": "examples/invalid/cycle.json with the missing summary mapping",
        "document": mutated(CYCLE, lambda d: node(d, "end")["inputs"].pop("summary")),
        "reason": "cycle",
    },
    {
        "name": "precedence-schema-over-duplicate-id",
        "source": "examples/sales/workflow.json with both an unknown type and a duplicate id",
        "document": mutated(
            SALES,
            lambda d: node(d, "stats").update({"type": "script"}),
            lambda d: node(d, "analysis").update({"id": "stats"}),
        ),
        "reason": "unknown_type",
    },
    {
        "name": "precedence-port-over-join-sources",
        "source": "examples/branch-approval/workflow.json with a bad port and short sources",
        "document": mutated(
            APPROVAL,
            lambda d: d["edges"][edge_index(d, "choose", "approve")].update({"port": "oops"}),
            lambda d: node(d, "join")["config"].update({"required_sources": ["approve"]}),
        ),
        "reason": "bad_port",
    },
]


def build_corpus() -> dict:
    rules = validate.Rules.load(HERE / "rules.json")
    cases: list[dict] = []
    for entry in VALID_CASES + INVALID_CASES:
        document = copy.deepcopy(entry["document"])
        result = validate.validate(document, rules)
        record: dict = {"name": entry["name"]}
        if "source" in entry:
            record["source"] = entry["source"]
        record["verdict"] = result.verdict
        record["input"] = document
        record["input_sha256"] = canonical_sha256(document)
        if result.verdict == "valid":
            record["expected_hash"] = result.data.sha256
            anchor = entry.get("expected_canonical")
            if anchor is not None:
                if result.data.canonical != anchor:
                    raise SystemExit(
                        f"{entry['name']}: normal form drifted from the hand-written anchor\n"
                        f"  expected: {anchor}\n  produced: {result.data.canonical}"
                    )
                record["expected_canonical"] = anchor
                if hashlib.sha256(anchor.encode("utf-8")).hexdigest() != record["expected_hash"]:
                    raise SystemExit(f"{entry['name']}: anchor digest mismatch")
        else:
            record["reason"] = result.reason
            record["reasons"] = result.reasons()
            if entry.get("reason") != result.reason:
                raise SystemExit(
                    f"{entry['name']}: reason {result.reason!r} != authored {entry.get('reason')!r}")
            if "reasons" in entry and entry["reasons"] != result.reasons():
                raise SystemExit(
                    f"{entry['name']}: reasons {result.reasons()!r} != authored {entry['reasons']!r}")
        cases.append(record)

    # invariant: reordering the node/edge arrays cannot change the data digest
    by_name = {case["name"]: case for case in cases}
    if (by_name["linear-sales-nodes-shuffled"]["expected_hash"]
            != by_name["linear-sales"]["expected_hash"]):
        raise SystemExit("normalisation is not order independent")

    corpus = {
        "probe": rules.raw["probe"],
        "probe_version": rules.raw["probe_version"],
        "cases": cases,
    }
    failures = validate.check_corpus(corpus)
    if failures:
        raise SystemExit("generated corpus is not well formed:\n  " + "\n  ".join(failures))
    return corpus


def render(corpus: dict) -> str:
    return json.dumps(corpus, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the shared P05 corpus")
    parser.add_argument("--check", action="store_true",
                        help="only verify that corpus.json is up to date")
    args = parser.parse_args(argv)
    text = render(build_corpus())
    target = HERE / "corpus.json"
    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != text:
            print("corpus.json is stale; run python build_corpus.py", file=sys.stderr)
            return 1
        print(f"OK: {target.name} is up to date ({len(text)} bytes)")
        return 0
    target.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {target} ({len(text)} bytes, {len(json.loads(text)['cases'])} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
