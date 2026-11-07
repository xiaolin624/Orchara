"""T102 / F13B: deterministic builder for the frozen seven-category corpus.

This module is the human-authored source of truth for
``product/tests/f13b/corpus``. It defines:

* the committed/generated sales fixtures and their SHA-256 pins;
* the routing catalog snapshots and RAG source snapshots the independent truth
  is derived from;
* every case (case document + registry description: family, template, scenario,
  truth derivation, licence, required collection facts);
* the registry itself (file pins, schema pin, family/category tables).

``build()`` returns the exact bytes of the eight committed documents, so
``test_the_registry_and_case_documents_are_reproducible`` catches any hand edit
that did not go through this builder. Regenerate with::

    python tests/f13b/build_corpus.py --write

The corpus is declarative: no case is executed here and no product code, model,
database or sandbox is involved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCT_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
TOOLS = PRODUCT_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import f13b_corpus  # noqa: E402

TARGET_KIND = "debug_snapshot"

LICENSE = {
    "origin": "self_created",
    "license_id": "LicenseRef-Orchara-SelfCreated",
    "redistributable": True,
    "review": "not_applicable",
    "notes": (
        "Synthetic data written for this repository by the F13B/T102 builder; "
        "no third-party document, user record or benchmark task is included."
    ),
}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
GENERATED_FIXTURES = {
    "over_limit_csv": lambda: _over_limit_csv(),
    "invalid_encoding_csv": lambda: _invalid_encoding_csv(),
}


def _over_limit_csv() -> bytes:
    """10 001 unique orders: one more than the documented 10 000-row ceiling."""
    header = b"order_id,date,region,amount\n"
    rows = b"".join(
        f"o-{index:05d},2026-01-01,东区,1.00\n".encode("utf-8") for index in range(1, 10002)
    )
    return header + rows


def _invalid_encoding_csv() -> bytes:
    """A structurally valid CSV encoded as GB18030, not UTF-8."""
    text = "order_id,date,region,amount\nA1,2026-01-01,东区,10.00\n"
    return text.encode("gb18030")


FIXTURE_SPECS: dict[str, dict] = {
    "sales-dev-orders-csv": {
        "kind": "file",
        "path": "examples/sales/input.csv",
        "media_type": "text/csv",
        "description": "The committed three-order sales CSV example (T052/F13A example tree).",
    },
    "sales-dev-orders-xlsx": {
        "kind": "file",
        "path": "examples/sales/input.xlsx",
        "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "description": "The XLSX workbook equivalent to examples/sales/input.csv.",
    },
    "sales-dev-missing-column": {
        "kind": "file",
        "path": "product/tests/f13b/corpus/fixtures/sales_missing_column.csv",
        "media_type": "text/csv",
        "description": "A CSV whose header omits the amount column.",
    },
    "sales-dev-duplicate-order": {
        "kind": "file",
        "path": "product/tests/f13b/corpus/fixtures/sales_duplicate_order.csv",
        "media_type": "text/csv",
        "description": "A CSV that repeats one order id.",
    },
    "sales-dev-negative-amount": {
        "kind": "file",
        "path": "product/tests/f13b/corpus/fixtures/sales_negative_amount.csv",
        "media_type": "text/csv",
        "description": "A CSV with a negative amount.",
    },
    "sales-dev-formula-cell": {
        "kind": "file",
        "path": "product/tests/f13b/corpus/fixtures/sales_formula_cell.csv",
        "media_type": "text/csv",
        "description": "A CSV whose amount cell is a spreadsheet formula, not a number.",
    },
    "sales-dev-over-limit": {
        "kind": "generated",
        "generator": "tests/f13b/build_corpus.py:over_limit_csv",
        "media_type": "text/csv",
        "description": "Deterministically generated 10 001-row CSV (one row over the ceiling).",
    },
    "sales-frozen-boundary": {
        "kind": "file",
        "path": "product/tests/f13b/corpus/fixtures/sales_frozen_boundary.csv",
        "media_type": "text/csv",
        "description": "Four orders with large amounts, two decimals and CJK region names.",
    },
    "sales-frozen-empty": {
        "kind": "file",
        "path": "product/tests/f13b/corpus/fixtures/sales_frozen_empty.csv",
        "media_type": "text/csv",
        "description": "A header-only CSV with no data rows.",
    },
    "sales-frozen-encoding": {
        "kind": "generated",
        "generator": "tests/f13b/build_corpus.py:invalid_encoding_csv",
        "media_type": "text/csv",
        "description": "A structurally valid CSV encoded as GB18030 instead of UTF-8.",
    },
    "routing-dev-support-catalog-v3": {
        "kind": "logical",
        "description": "registry.routing_catalogs['support-dev-v3']",
    },
    "routing-frozen-operations-catalog-v7": {
        "kind": "logical",
        "description": "registry.routing_catalogs['operations-frozen-v7']",
    },
    "rag-dev-handbook-index-v1": {
        "kind": "logical",
        "description": "registry.rag_sources entries whose family is product-handbook",
    },
    "rag-frozen-policy-index-v2": {
        "kind": "logical",
        "description": "registry.rag_sources entries whose family is policy-portal",
    },
    "memory-dev-store-v3": {
        "kind": "logical",
        "description": "human-labeled personal-memory state machine for the dev split",
    },
    "memory-frozen-store-v3": {
        "kind": "logical",
        "description": "human-labeled personal-memory state machine for the frozen split",
    },
    "delegation-dev-runtime-v2": {
        "kind": "logical",
        "description": "human-labeled child Run/thread and idempotency ledger for the dev split",
    },
    "delegation-frozen-runtime-v5": {
        "kind": "logical",
        "description": "human-labeled parent-budget/slot/join ledger for the frozen split",
    },
    "approval-dev-ledger-v1": {
        "kind": "logical",
        "description": "human-labeled approval operation ledger for the dev split",
    },
    "approval-frozen-ledger-v1": {
        "kind": "logical",
        "description": "human-labeled approval operation ledger for the frozen split",
    },
    "lifecycle-dev-graphs-v1": {
        "kind": "logical",
        "description": "human-labeled recovery graph/checkpoint manifest for the dev split",
    },
    "lifecycle-frozen-graphs-v2": {
        "kind": "logical",
        "description": "human-labeled recovery graph/checkpoint manifest for the frozen split",
    },
}


def fixture_bytes(entry: dict) -> bytes:
    """The exact bytes a fixture entry names (committed file or deterministic generator)."""
    kind = entry.get("kind")
    if kind == "file":
        return (REPO_ROOT / entry["path"]).read_bytes()
    if kind == "generated":
        name = str(entry["generator"]).rsplit(":", 1)[-1]
        return GENERATED_FIXTURES[name]()
    raise ValueError(f"a {kind!r} fixture has no bytes: {entry!r}")


def fixture_entries() -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for fixture_id, spec in FIXTURE_SPECS.items():
        entry = dict(spec)
        if entry["kind"] in {"file", "generated"}:
            payload = fixture_bytes(entry)
            entry["sha256"] = hashlib.sha256(payload).hexdigest()
            entry["size_bytes"] = len(payload)
        entries[fixture_id] = entry
    return entries


# ---------------------------------------------------------------------------
# committed snapshots: routing catalogs and RAG sources
# ---------------------------------------------------------------------------
ROUTING_CATALOGS: dict[str, dict] = {
    "support-dev-v3": {
        "category": "routing",
        "split": "dev",
        "applications": [
            {
                "application_id": "support-knowledge",
                "family": "support",
                "capabilities": ["support.knowledge.search"],
                "granted_to": ["user-42"],
            },
            {
                "application_id": "support-triage-a",
                "family": "support",
                "capabilities": ["support.inbox.triage"],
                "granted_to": ["user-42"],
            },
            {
                "application_id": "support-triage-b",
                "family": "support",
                "capabilities": ["support.inbox.triage"],
                "granted_to": ["user-42"],
            },
            {
                "application_id": "support-billing",
                "family": "support",
                "capabilities": ["support.billing.refund"],
                "granted_to": ["user-43"],
            },
        ],
    },
    "operations-frozen-v7": {
        "category": "routing",
        "split": "frozen_test",
        "applications": [
            {
                "application_id": "operations-deploy",
                "family": "operations",
                "capabilities": ["operations.deploy.execute"],
                "granted_to": ["user-77"],
            },
            {
                "application_id": "operations-runbook",
                "family": "operations",
                "capabilities": ["operations.runbook.execute"],
                "granted_to": ["user-77"],
            },
            {
                "application_id": "operations-audit",
                "family": "operations",
                "capabilities": ["operations.audit.read"],
                "granted_to": ["user-42"],
            },
        ],
    },
}

RAG_SOURCES: dict[str, dict] = {
    "handbook-api-keys-p3": {
        "family": "product-handbook",
        "page": 3,
        "version": "2026.1",
        "status": "active",
        "text": "API keys expire after 90 days; rotate them before the expiry date.",
    },
    "handbook-export-p1": {
        "family": "product-handbook",
        "page": 1,
        "version": "2026.1",
        "status": "active",
        "text": "Exports are generated by the nightly maintenance job.",
    },
    "policy-retention-v1": {
        "family": "policy-portal",
        "page": 4,
        "version": "1.0",
        "status": "revoked",
        "text": "Audit log retention is 30 days.",
    },
    "policy-retention-v2": {
        "family": "policy-portal",
        "page": 4,
        "version": "2.0",
        "status": "active",
        "text": "Audit log retention is 90 days for all tenants.",
    },
}


# ---------------------------------------------------------------------------
# families: one split per family, one template per family
# ---------------------------------------------------------------------------
FAMILIES: dict[str, dict] = {
    "sales-file-summary": {
        "category": "sales",
        "split": "dev",
        "template": "summarize-sales-file-and-report-region-totals",
    },
    "sales-bad-input": {
        "category": "sales",
        "split": "dev",
        "template": "reject-sales-file-with-a-stable-structural-error",
    },
    "sales-frozen-boundary": {
        "category": "sales",
        "split": "frozen_test",
        "template": "summarize-sales-file-with-large-two-decimal-amounts",
    },
    "sales-frozen-empty": {
        "category": "sales",
        "split": "frozen_test",
        "template": "reject-sales-file-with-no-data-rows",
    },
    "sales-frozen-encoding": {
        "category": "sales",
        "split": "frozen_test",
        "template": "reject-sales-file-that-is-not-utf8",
    },
    "routing-dev-support-catalog": {
        "category": "routing",
        "split": "dev",
        "template": "route-support-request-over-the-support-catalog",
    },
    "routing-frozen-operations-catalog": {
        "category": "routing",
        "split": "frozen_test",
        "template": "route-operations-request-over-the-operations-catalog",
    },
    "rag-dev-handbook": {
        "category": "rag",
        "split": "dev",
        "template": "answer-from-the-product-handbook-with-source-citations",
    },
    "rag-frozen-policy-portal": {
        "category": "rag",
        "split": "frozen_test",
        "template": "answer-from-the-frozen-policy-portal-with-pinned-sources",
    },
    "memory-dev-confirmation": {
        "category": "memory",
        "split": "dev",
        "template": "confirm-reject-or-clarify-a-personal-memory-through-a-human-decision",
    },
    "memory-frozen-revision": {
        "category": "memory",
        "split": "frozen_test",
        "template": "modify-or-delete-a-stored-memory",
    },
    "memory-frozen-isolation": {
        "category": "memory",
        "split": "frozen_test",
        "template": "enforce-user-and-application-memory-isolation",
    },
    "delegation-dev-parallel-children": {
        "category": "delegation",
        "split": "dev",
        "template": "delegate-concurrent-requests-to-one-sub-application",
    },
    "delegation-frozen-limits": {
        "category": "delegation",
        "split": "frozen_test",
        "template": "delegate-under-parent-budget-slot-and-join-limits",
    },
    "approval-dev-human-approval": {
        "category": "approval_effects",
        "split": "dev",
        "template": "approve-one-external-write-after-a-human-decision",
    },
    "approval-frozen-lost-receipt": {
        "category": "approval_effects",
        "split": "frozen_test",
        "template": "reconcile-an-external-write-whose-receipt-was-lost",
    },
    "approval-frozen-cancel": {
        "category": "approval_effects",
        "split": "frozen_test",
        "template": "cancel-an-approval-before-its-external-write",
    },
    "approval-frozen-expiry": {
        "category": "approval_effects",
        "split": "frozen_test",
        "template": "reject-an-approval-that-expired-before-decision",
    },
    "lifecycle-dev-graph-restore": {
        "category": "lifecycle",
        "split": "dev",
        "template": "restore-a-published-version-from-its-frozen-graph",
    },
    "lifecycle-frozen-recovery": {
        "category": "lifecycle",
        "split": "frozen_test",
        "template": "recover-after-a-delete-and-a-redis-restart-without-leaking-inputs",
    },
}


# ---------------------------------------------------------------------------
# assertion constructors
# ---------------------------------------------------------------------------
def run_state(expected: str) -> dict:
    return {"kind": "run_state", "expected": expected}


def json_eq(pointer: str, expected) -> dict:
    return {"kind": "json_equals", "pointer": pointer, "expected": expected}


def decimal_eq(pointer: str, expected: str) -> dict:
    return {"kind": "decimal_equals", "pointer": pointer, "expected": expected}


def effect_count(capability: str, expected: int) -> dict:
    return {"kind": "effect_count", "capability": capability, "expected": expected,
            "source": "stub_ledger"}


def citations(allowed: list[str], require_one: bool) -> dict:
    return {"kind": "citation_sources", "allowed_source_ids": allowed,
            "require_at_least_one": require_one}


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------
def _case(
    *,
    case_id: str,
    family_id: str,
    input_obj: dict,
    assertions: list[dict],
    fixture_id: str,
    scenario: str,
    truth_derivation: str,
    truth_extra: dict | None = None,
) -> tuple[dict, dict]:
    family = FAMILIES[family_id]
    category = family["category"]
    provenance = {
        "origin": "self_created",
        "reference": f"tests/f13b/corpus/cases.{category}.json",
        "license_review": "not_applicable",
    }
    case = {
        "schema_version": "1.0",
        "case_id": case_id,
        "category": category,
        "family_id": family_id,
        "split": family["split"],
        "target": {"kind": TARGET_KIND, "id": f"snapshot-{family_id}"},
        "input": input_obj,
        "fixture_id": fixture_id,
        "assertions": assertions,
        "provenance": provenance,
    }
    required = (
        set(f13b_corpus.COLLECTION_FACTS)
        | {f13b_corpus.CATEGORY_FACTS[category]}
        | set(f13b_corpus.derive_required_facts(case))
    )
    truth = {
        "source": f13b_corpus.TRUTH_SOURCES[category],
        "derivation": truth_derivation,
    }
    if truth_extra:
        truth.update(truth_extra)
    meta = {
        "category": category,
        "family_id": family_id,
        "split": family["split"],
        "template": family["template"],
        "scenario": scenario,
        "truth": truth,
        "license": dict(LICENSE),
        "required_facts": sorted(required),
        "provenance": provenance,
        "assertion_kinds": sorted({item["kind"] for item in assertions}),
        "fixture_id": fixture_id,
    }
    return case, meta


def _task_input(fixtures: dict[str, dict], fixture_id: str) -> dict:
    entry = fixtures[fixture_id]
    return {"sha256": entry["sha256"], "media_type": entry["media_type"]}


def _cases(fixtures: dict[str, dict]) -> list[tuple[dict, dict]]:
    sales: list[tuple[dict, dict]] = [
        _case(
            case_id="sales-dev-csv-summary",
            family_id="sales-file-summary",
            input_obj={
                "source": "sales-dev-csv-summary",
                "task_input": _task_input(fixtures, "sales-dev-orders-csv"),
            },
            assertions=[
                run_state("succeeded"),
                decimal_eq("/summary/total", "250.00"),
                decimal_eq("/summary/regions/东区", "200.00"),
                decimal_eq("/summary/regions/西区", "50.00"),
                json_eq("/summary/order_count", 3),
                effect_count("test.send", 0),
            ],
            fixture_id="sales-dev-orders-csv",
            scenario=(
                "Happy path: sum a clean three-order sales CSV and report the per-region "
                "totals without touching a test side-effect capability."
            ),
            truth_derivation=(
                "tests/f13a/reference/sales_reference.py:summarize_csv recomputes "
                "/summary/total, /summary/regions/* and /summary/order_count from the "
                "committed CSV bytes with decimal.Decimal; no product sales tool is imported."
            ),
        ),
        _case(
            case_id="sales-dev-xlsx-summary",
            family_id="sales-file-summary",
            input_obj={
                "source": "sales-dev-xlsx-summary",
                "task_input": _task_input(fixtures, "sales-dev-orders-xlsx"),
            },
            assertions=[
                run_state("succeeded"),
                decimal_eq("/summary/total", "250.00"),
                decimal_eq("/summary/regions/东区", "200.00"),
                decimal_eq("/summary/regions/西区", "50.00"),
                json_eq("/summary/order_count", 3),
                effect_count("test.send", 0),
            ],
            fixture_id="sales-dev-orders-xlsx",
            scenario=(
                "Happy path: the equivalent XLSX workbook must produce exactly the same "
                "region totals as the CSV case."
            ),
            truth_derivation=(
                "tests/f13a/reference/sales_reference.py:summarize_xlsx reads the committed "
                "workbook with zipfile plus ElementTree and recomputes the same Decimal "
                "expectations as the CSV case; no product sales tool is imported."
            ),
        ),
        _case(
            case_id="sales-dev-missing-column",
            family_id="sales-bad-input",
            input_obj={
                "source": "sales-dev-missing-column",
                "task_input": _task_input(fixtures, "sales-dev-missing-column"),
                "expected_failure": {"error_code": "missing_column"},
            },
            assertions=[run_state("failed"), effect_count("test.send", 0)],
            fixture_id="sales-dev-missing-column",
            scenario=(
                "Reject a CSV whose header omits the amount column, with the stable "
                "business code missing_column and no external send."
            ),
            truth_derivation=(
                "sales_reference.summarize_csv independently raises "
                "FixtureRejected('missing_column') for the committed bytes because the "
                "header has fewer than the four required columns."
            ),
        ),
        _case(
            case_id="sales-dev-duplicate-order",
            family_id="sales-bad-input",
            input_obj={
                "source": "sales-dev-duplicate-order",
                "task_input": _task_input(fixtures, "sales-dev-duplicate-order"),
                "expected_failure": {"error_code": "duplicate_order_id"},
            },
            assertions=[run_state("failed"), effect_count("test.send", 0)],
            fixture_id="sales-dev-duplicate-order",
            scenario=(
                "Reject a CSV that repeats an order id instead of summing it twice, with "
                "the stable business code duplicate_order_id."
            ),
            truth_derivation=(
                "sales_reference.summarize_csv independently raises "
                "FixtureRejected('duplicate_order_id') because the second occurrence of "
                "the order id is seen twice."
            ),
        ),
        _case(
            case_id="sales-dev-negative-amount",
            family_id="sales-bad-input",
            input_obj={
                "source": "sales-dev-negative-amount",
                "task_input": _task_input(fixtures, "sales-dev-negative-amount"),
                "expected_failure": {"error_code": "negative_amount"},
            },
            assertions=[run_state("failed"), effect_count("test.send", 0)],
            fixture_id="sales-dev-negative-amount",
            scenario=(
                "Reject a CSV whose amount is negative, with the stable business code "
                "negative_amount instead of summing the bad value."
            ),
            truth_derivation=(
                "sales_reference.summarize_csv independently raises "
                "FixtureRejected('negative_amount') because Decimal('-5.00') is below zero."
            ),
        ),
        _case(
            case_id="sales-dev-formula-cell",
            family_id="sales-bad-input",
            input_obj={
                "source": "sales-dev-formula-cell",
                "task_input": _task_input(fixtures, "sales-dev-formula-cell"),
                "expected_failure": {"error_code": "formula_not_supported"},
            },
            assertions=[run_state("failed"), effect_count("test.send", 0)],
            fixture_id="sales-dev-formula-cell",
            scenario=(
                "Reject a CSV whose amount cell is a spreadsheet formula (a CSV injection "
                "shape), with the stable code formula_not_supported."
            ),
            truth_derivation=(
                "sales_reference.summarize_csv independently raises "
                "FixtureRejected('formula_not_supported') because the cell starts with '='."
            ),
        ),
        _case(
            case_id="sales-dev-over-limit",
            family_id="sales-bad-input",
            input_obj={
                "source": "sales-dev-over-limit",
                "task_input": _task_input(fixtures, "sales-dev-over-limit"),
                "expected_failure": {"error_code": "row_limit_exceeded"},
            },
            assertions=[run_state("failed"), effect_count("test.send", 0)],
            fixture_id="sales-dev-over-limit",
            scenario=(
                "Reject a generated CSV with 10 001 data rows, one more than the documented "
                "10 000-row ceiling, with the stable code row_limit_exceeded."
            ),
            truth_derivation=(
                "sales_reference.summarize_csv independently raises "
                "FixtureRejected('row_limit_exceeded') when the row counter reaches "
                "MAX_ROWS=10000 on the generated 10 001-row payload."
            ),
        ),
        _case(
            case_id="sales-frozen-boundary-decimals",
            family_id="sales-frozen-boundary",
            input_obj={
                "source": "sales-frozen-boundary-decimals",
                "task_input": _task_input(fixtures, "sales-frozen-boundary"),
            },
            assertions=[
                run_state("succeeded"),
                decimal_eq("/summary/total", "101234.65"),
                decimal_eq("/summary/regions/北区", "1234.65"),
                decimal_eq("/summary/regions/南区", "100000.00"),
                json_eq("/summary/order_count", 4),
                effect_count("test.send", 0),
            ],
            fixture_id="sales-frozen-boundary",
            scenario=(
                "Frozen boundary: sum four orders with large amounts, two decimal places "
                "and CJK region names, keeping exact decimal arithmetic."
            ),
            truth_derivation=(
                "sales_reference.summarize_csv recomputes the committed boundary CSV with "
                "Decimal: 1234.60 + 100000.00 + 0.05 + 0.00 = 101234.65 across 北区/南区."
            ),
        ),
        _case(
            case_id="sales-frozen-empty-file",
            family_id="sales-frozen-empty",
            input_obj={
                "source": "sales-frozen-empty-file",
                "task_input": _task_input(fixtures, "sales-frozen-empty"),
                "expected_failure": {"error_code": "empty_input"},
            },
            assertions=[run_state("failed"), effect_count("test.send", 0)],
            fixture_id="sales-frozen-empty",
            scenario=(
                "Frozen boundary: reject a header-only sales CSV with zero data rows as "
                "empty_input rather than reporting a 0.00 total."
            ),
            truth_derivation=(
                "sales_reference.summarize_csv independently raises "
                "FixtureRejected('empty_input') because the header parses but _summary "
                "receives no orders."
            ),
        ),
        _case(
            case_id="sales-frozen-invalid-encoding",
            family_id="sales-frozen-encoding",
            input_obj={
                "source": "sales-frozen-invalid-encoding",
                "task_input": _task_input(fixtures, "sales-frozen-encoding"),
                "expected_failure": {"error_code": "invalid_encoding"},
            },
            assertions=[run_state("failed"), effect_count("test.send", 0)],
            fixture_id="sales-frozen-encoding",
            scenario=(
                "Frozen boundary: reject a structurally valid CSV encoded as GB18030, not "
                "UTF-8, with the stable code invalid_encoding."
            ),
            truth_derivation=(
                "sales_reference.summarize_csv independently raises "
                "FixtureRejected('invalid_encoding') because the generated payload is not "
                "decodable as UTF-8."
            ),
        ),
    ]

    routing: list[tuple[dict, dict]] = [
        _case(
            case_id="routing-dev-unique-match",
            family_id="routing-dev-support-catalog",
            input_obj={
                "request": {
                    "text": "Find the onboarding knowledge article about invoice fields.",
                    "required_capability": "support.knowledge.search",
                    "requested_family": "support",
                },
                "catalog_version": "support-dev-v3",
                "requester": {"id": "user-42"},
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/routing/decision", "auto_execute"),
                json_eq("/routing/selected_application_id", "support-knowledge"),
                effect_count("test.send", 0),
            ],
            fixture_id="routing-dev-support-catalog-v3",
            scenario=(
                "Route a support knowledge request where exactly one published support "
                "application advertises the capability and the requester holds a grant."
            ),
            truth_derivation=(
                "Derived from registry.routing_catalogs[support-dev-v3]: the support family "
                "has exactly one application advertising support.knowledge.search that "
                "grants user-42, so the labeled acceptable handling is auto_execute."
            ),
            truth_extra={"condition": "unique_authorized_match"},
        ),
        _case(
            case_id="routing-dev-ambiguous-match",
            family_id="routing-dev-support-catalog",
            input_obj={
                "request": {
                    "text": "Triage this inbound support message.",
                    "required_capability": "support.inbox.triage",
                    "requested_family": "support",
                },
                "catalog_version": "support-dev-v3",
                "requester": {"id": "user-42"},
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/routing/decision", "clarify"),
                json_eq("/routing/selected_application_id", None),
                effect_count("test.send", 0),
            ],
            fixture_id="routing-dev-support-catalog-v3",
            scenario=(
                "Route a triage request where two authorized support applications advertise "
                "the same capability, so the user must choose instead of guessing."
            ),
            truth_derivation=(
                "Derived from registry.routing_catalogs[support-dev-v3]: two support "
                "applications advertising support.inbox.triage both grant user-42, so the "
                "human-labeled acceptable handling is clarify, never auto_execute."
            ),
            truth_extra={"condition": "ambiguous_authorized_matches"},
        ),
        _case(
            case_id="routing-dev-missing-input",
            family_id="routing-dev-support-catalog",
            input_obj={
                "request": {"text": "Route this request."},
                "catalog_version": "support-dev-v3",
                "requester": {"id": "user-42"},
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/routing/decision", "clarify"),
                json_eq("/routing/selected_application_id", None),
                effect_count("test.send", 0),
            ],
            fixture_id="routing-dev-support-catalog-v3",
            scenario=(
                "Route a request that names neither a required capability nor a requested "
                "application family, so the platform must ask instead of inventing one."
            ),
            truth_derivation=(
                "Derived from registry.routing_catalogs[support-dev-v3]: without a required "
                "capability and family the catalog cannot be filtered, so the human-labeled "
                "acceptable handling is clarify."
            ),
            truth_extra={"condition": "incomplete_request"},
        ),
        _case(
            case_id="routing-frozen-no-match",
            family_id="routing-frozen-operations-catalog",
            input_obj={
                "request": {
                    "text": "Run the quarterly operations billing audit.",
                    "required_capability": "operations.billing.audit",
                    "requested_family": "operations",
                },
                "catalog_version": "operations-frozen-v7",
                "requester": {"id": "user-77"},
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/routing/decision", "reject"),
                json_eq("/routing/selected_application_id", None),
                effect_count("test.send", 0),
            ],
            fixture_id="routing-frozen-operations-catalog-v7",
            scenario=(
                "Frozen catalog: reject an operations request for a capability no published "
                "application advertises, instead of routing to a near match."
            ),
            truth_derivation=(
                "Derived from registry.routing_catalogs[operations-frozen-v7]: the "
                "operations family exists but no application advertises "
                "operations.billing.audit, so the labeled acceptable handling is reject."
            ),
            truth_extra={"condition": "no_matching_application"},
        ),
        _case(
            case_id="routing-frozen-unseen-application",
            family_id="routing-frozen-operations-catalog",
            input_obj={
                "request": {
                    "text": "Open a security incident response task.",
                    "required_capability": "security.incident.respond",
                    "requested_family": "security-incident",
                },
                "catalog_version": "operations-frozen-v7",
                "requester": {"id": "user-77"},
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/routing/decision", "reject"),
                json_eq("/routing/selected_application_id", None),
                effect_count("test.send", 0),
            ],
            fixture_id="routing-frozen-operations-catalog-v7",
            scenario=(
                "Frozen catalog: a request for an application family that has never been "
                "published must be rejected, not mapped onto an operations application."
            ),
            truth_derivation=(
                "Derived from registry.routing_catalogs[operations-frozen-v7]: no "
                "application has family security-incident, so the human-labeled acceptable "
                "handling is reject (never a fuzzy match)."
            ),
            truth_extra={"condition": "unseen_application_family"},
        ),
        _case(
            case_id="routing-frozen-unauthorized-candidate",
            family_id="routing-frozen-operations-catalog",
            input_obj={
                "request": {
                    "text": "Execute the production runbook.",
                    "required_capability": "operations.runbook.execute",
                    "requested_family": "operations",
                },
                "catalog_version": "operations-frozen-v7",
                "requester": {"id": "user-42"},
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/routing/decision", "reject"),
                json_eq("/routing/selected_application_id", None),
                effect_count("test.send", 0),
            ],
            fixture_id="routing-frozen-operations-catalog-v7",
            scenario=(
                "Frozen catalog: the only capability match is an application the requester "
                "cannot use, so the request is rejected and no side effect may occur."
            ),
            truth_derivation=(
                "Derived from registry.routing_catalogs[operations-frozen-v7]: "
                "operations-runbook advertises operations.runbook.execute but grants only "
                "user-77, so no authorized candidate exists; the label is reject."
            ),
            truth_extra={"condition": "unauthorized_candidate_only"},
        ),
    ]

    rag: list[tuple[dict, dict]] = [
        _case(
            case_id="rag-dev-answered-with-citation",
            family_id="rag-dev-handbook",
            input_obj={
                "question": "How long do API keys last before they expire?",
                "required_source_id": "handbook-api-keys-p3",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/answer/status", "answered"),
                citations(["handbook-api-keys-p3"], True),
                effect_count("test.send", 0),
            ],
            fixture_id="rag-dev-handbook-index-v1",
            scenario=(
                "Answer an API-key lifetime question from the handbook and cite exactly the "
                "active source page that states it."
            ),
            truth_derivation=(
                "registry.rag_sources[handbook-api-keys-p3] is an active product-handbook "
                "page 3 snippet whose text contains '90 days'; the allowed citation set is "
                "that committed source id, not a model claim."
            ),
        ),
        _case(
            case_id="rag-dev-missing-target-chunk",
            family_id="rag-dev-handbook",
            input_obj={
                "question": "How long are exports kept before deletion?",
                "missing_source_id": "handbook-data-export-p7",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/answer/status", "abstained"),
                json_eq("/answer/contains_target_fact", False),
                citations(["handbook-export-p1"], False),
                effect_count("test.send", 0),
            ],
            fixture_id="rag-dev-handbook-index-v1",
            scenario=(
                "Ask a question whose target chunk is absent from the committed index, so "
                "the correct behavior is an explicit abstention, not an invented answer."
            ),
            truth_derivation=(
                "handbook-data-export-p7 is absent from registry.rag_sources, so no active "
                "source can support the requested fact; the labeled acceptable handling is "
                "answer.status=abstained."
            ),
        ),
        _case(
            case_id="rag-dev-fabricated-citation",
            family_id="rag-dev-handbook",
            input_obj={
                "question": "How long do API keys last before they expire?",
                "required_source_id": "handbook-api-keys-p3",
                "observed_citation": "handbook-api-keys-p9",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/answer/status", "answered"),
                json_eq("/citations/contains_unknown_source", False),
                citations(["handbook-api-keys-p3"], True),
                effect_count("test.send", 0),
            ],
            fixture_id="rag-dev-handbook-index-v1",
            scenario=(
                "Reject a fabricated citation: the answer may only cite handbook-api-keys-p3, "
                "and the hallucinated page id must not appear in the citation set."
            ),
            truth_derivation=(
                "handbook-api-keys-p9 is absent from registry.rag_sources while "
                "handbook-api-keys-p3 is active and contains the answer; the citation "
                "checker therefore allows only the committed source id."
            ),
        ),
        _case(
            case_id="rag-frozen-wrong-answer",
            family_id="rag-frozen-policy-portal",
            input_obj={
                "question": "How long is audit log retention?",
                "source_id": "policy-retention-v2",
                "correct_fact": "90 days",
                "observed_fact": "30 days",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/answer/contains_target_fact", False),
                citations(["policy-retention-v2"], True),
                effect_count("test.send", 0),
            ],
            fixture_id="rag-frozen-policy-index-v2",
            scenario=(
                "Frozen policy portal: the generated answer states the revoked policy's 30 "
                "days while the active source says 90 days, so the semantic fact check fails."
            ),
            truth_derivation=(
                "registry.rag_sources[policy-retention-v2] is active page 4 v2.0 and its "
                "text contains '90 days' but not '30 days'; the observed '30 days' comes "
                "from the revoked v1 document."
            ),
        ),
        _case(
            case_id="rag-frozen-revoked-source",
            family_id="rag-frozen-policy-portal",
            input_obj={
                "question": "How long is audit log retention?",
                "revoked_source_id": "policy-retention-v1",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/answer/status", "abstained"),
                json_eq("/retrieval/used_revoked_source", False),
                citations(["policy-retention-v2"], False),
                effect_count("test.send", 0),
            ],
            fixture_id="rag-frozen-policy-index-v2",
            scenario=(
                "Frozen policy portal: the only snippet matching the question is revoked, so "
                "the platform must abstain and must not cite revoked material."
            ),
            truth_derivation=(
                "registry.rag_sources[policy-retention-v1] has status revoked; only "
                "policy-retention-v2 is active, so a correct system with no active target "
                "chunk abstains rather than citing v1."
            ),
        ),
        _case(
            case_id="rag-frozen-stale-version",
            family_id="rag-frozen-policy-portal",
            input_obj={
                "question": "How long is audit log retention?",
                "old_source_id": "policy-retention-v1",
                "new_source_id": "policy-retention-v2",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/citations/used_old_version", False),
                citations(["policy-retention-v2"], True),
                effect_count("test.send", 0),
            ],
            fixture_id="rag-frozen-policy-index-v2",
            scenario=(
                "Frozen policy portal: cite the current policy version 2.0 and never the "
                "superseded revoked version 1.0."
            ),
            truth_derivation=(
                "registry.rag_sources pins policy-retention-v2 (version 2.0, active) over "
                "policy-retention-v1 (version 1.0, revoked); the allowed citation set is the "
                "active version only."
            ),
        ),
    ]

    memory: list[tuple[dict, dict]] = [
        _case(
            case_id="memory-dev-confirm-preference",
            family_id="memory-dev-confirmation",
            input_obj={
                "user_id": "user-42",
                "application_id": "app-personal-notes",
                "proposal": {"field": "beverage", "value": "espresso"},
                "decision": "confirm",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/memory/status", "confirmed"),
                json_eq("/memory/active_value", "espresso"),
                json_eq("/memory/revision", 1),
                effect_count("test.send", 0),
            ],
            fixture_id="memory-dev-store-v3",
            scenario=(
                "Confirm a proposed personal preference through a human decision and store "
                "it as revision 1."
            ),
            truth_derivation=(
                "Human-labeled platform state machine: a confirmed proposal becomes the "
                "single active value at revision 1; confirmation is a persisted decision, "
                "not a model claim."
            ),
        ),
        _case(
            case_id="memory-dev-reject-preference",
            family_id="memory-dev-confirmation",
            input_obj={
                "user_id": "user-42",
                "application_id": "app-personal-notes",
                "proposal": {"field": "beverage", "value": "espresso"},
                "decision": "reject",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/memory/status", "rejected"),
                json_eq("/memory/active_value", None),
                json_eq("/memory/revision", 0),
                effect_count("test.send", 0),
            ],
            fixture_id="memory-dev-store-v3",
            scenario=(
                "Reject a proposed personal preference: nothing becomes active and the "
                "revision stays at zero."
            ),
            truth_derivation=(
                "Human-labeled platform state machine: a rejected proposal leaves no active "
                "memory and creates no revision."
            ),
        ),
        _case(
            case_id="memory-dev-contradictory-preference",
            family_id="memory-dev-confirmation",
            input_obj={
                "user_id": "user-42",
                "application_id": "app-personal-notes",
                "existing": {"field": "beverage", "value": "espresso"},
                "proposal": {"field": "beverage", "value": "tea"},
                "decision": "confirm",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/memory/status", "needs_clarification"),
                json_eq("/memory/active_value", "espresso"),
                json_eq("/memory/revision", 1),
                effect_count("test.send", 0),
            ],
            fixture_id="memory-dev-store-v3",
            scenario=(
                "A confirmed proposal contradicts the already active preference, so the "
                "platform keeps the active value and asks for clarification."
            ),
            truth_derivation=(
                "Human-labeled platform state machine: contradictory proposals are not "
                "silently overwritten; status needs_clarification and the existing revision "
                "1 value remains active."
            ),
        ),
        _case(
            case_id="memory-frozen-modify-preference",
            family_id="memory-frozen-revision",
            input_obj={
                "user_id": "user-42",
                "application_id": "app-personal-notes",
                "proposal": {"field": "beverage", "value": "green tea"},
                "decision": "modify",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/memory/status", "confirmed"),
                json_eq("/memory/active_value", "green tea"),
                json_eq("/memory/revision", 2),
                effect_count("test.send", 0),
            ],
            fixture_id="memory-frozen-store-v3",
            scenario=(
                "Frozen store: modify an existing active preference, producing revision 2 "
                "with the new value active."
            ),
            truth_derivation=(
                "Human-labeled platform state machine: modifying an existing memory keeps "
                "one active value and increments the revision to 2; old text is not active."
            ),
        ),
        _case(
            case_id="memory-frozen-delete-preference",
            family_id="memory-frozen-revision",
            input_obj={
                "user_id": "user-42",
                "application_id": "app-personal-notes",
                "proposal": {"field": "beverage", "value": "green tea"},
                "decision": "delete",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/memory/status", "deleted"),
                json_eq("/memory/active_value", None),
                json_eq("/memory/revision", 3),
                effect_count("test.send", 0),
            ],
            fixture_id="memory-frozen-store-v3",
            scenario=(
                "Frozen store: delete the active preference, leaving no active value and "
                "recording revision 3."
            ),
            truth_derivation=(
                "Human-labeled platform state machine: deletion creates a revision but no "
                "active value; a deleted memory is never recalled as active."
            ),
        ),
        _case(
            case_id="memory-frozen-user-isolation",
            family_id="memory-frozen-isolation",
            input_obj={
                "user_id": "user-42",
                "other_user_id": "user-43",
                "application_id": "app-a",
                "other_application_id": "app-b",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/memory/visible_to_other_user", False),
                json_eq("/memory/visible_to_other_application", False),
                effect_count("test.send", 0),
            ],
            fixture_id="memory-frozen-store-v3",
            scenario=(
                "Frozen store: a stored memory must not be visible to another user or "
                "another application, even when both know the field name."
            ),
            truth_derivation=(
                "Human-labeled isolation invariant: memory rows are scoped by (user, "
                "application); the same field name read as user-43/app-b returns no value."
            ),
        ),
    ]

    delegation: list[tuple[dict, dict]] = [
        _case(
            case_id="delegation-dev-same-subapp-concurrency",
            family_id="delegation-dev-parallel-children",
            input_obj={
                "parent_run_id": "run-parent-1",
                "child_application_id": "app-child-a",
                "concurrent_requests": 2,
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/delegation/child_runs", 2),
                json_eq("/delegation/distinct_threads", True),
                effect_count("test.send", 0),
            ],
            fixture_id="delegation-dev-runtime-v2",
            scenario=(
                "Two concurrent delegations to the same child application must create two "
                "independent child Runs with distinct threads."
            ),
            truth_derivation=(
                "Human-labeled delegation ledger: each accepted delegation gets its own Run "
                "and thread; concurrent requests to one sub-application do not share state."
            ),
        ),
        _case(
            case_id="delegation-dev-duplicate-delivery",
            family_id="delegation-dev-parallel-children",
            input_obj={
                "parent_run_id": "run-parent-1",
                "child_application_id": "app-child-a",
                "idempotency_key": "idem-child-a-1",
                "delivery_count": 2,
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/delegation/child_runs", 1),
                effect_count("delegation.child_dispatch", 1),
            ],
            fixture_id="delegation-dev-runtime-v2",
            scenario=(
                "The same delegation is delivered twice with one idempotency key; only one "
                "child dispatch may reach the ledger."
            ),
            truth_derivation=(
                "Human-labeled idempotency ledger: delivery_count=2 with one idempotency "
                "key must produce child_runs=1 and exactly one stub_ledger dispatch; the "
                "second delivery is a replay, not a second effect."
            ),
        ),
        _case(
            case_id="delegation-dev-independent-threads",
            family_id="delegation-dev-parallel-children",
            input_obj={
                "parent_run_id": "run-parent-2",
                "child_application_id": "app-child-b",
                "concurrent_requests": 2,
                "nested": True,
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/delegation/child_runs", 2),
                json_eq("/delegation/distinct_threads", True),
                json_eq("/delegation/joined_once", True),
                effect_count("test.send", 0),
            ],
            fixture_id="delegation-dev-runtime-v2",
            scenario=(
                "Nested concurrent delegations to one child application join exactly once "
                "while keeping independent child threads."
            ),
            truth_derivation=(
                "Human-labeled delegation ledger: two nested child Runs have distinct "
                "threads and the parent join triggers exactly once, after both children."
            ),
        ),
        _case(
            case_id="delegation-frozen-parent-budget",
            family_id="delegation-frozen-limits",
            input_obj={
                "parent_run_id": "run-parent-3",
                "parent_budget": 1,
                "requested_children": 2,
                "child_application_id": "app-child-c",
            },
            assertions=[
                run_state("failed"),
                json_eq("/delegation/over_budget_rejected", True),
                effect_count("delegation.child_dispatch", 0),
            ],
            fixture_id="delegation-frozen-runtime-v5",
            scenario=(
                "Frozen limit: a parent with budget 1 requests two children, so the "
                "delegation is rejected before any child is dispatched."
            ),
            truth_derivation=(
                "Human-labeled quota ledger: requested_children=2 exceeds parent_budget=1, "
                "so no child dispatch may be recorded (expected effect count is 0, not 1)."
            ),
        ),
        _case(
            case_id="delegation-frozen-full-slot-wait",
            family_id="delegation-frozen-limits",
            input_obj={
                "parent_run_id": "run-parent-4",
                "slot_capacity": 1,
                "concurrent_parents": 2,
                "child_application_id": "app-child-c",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/delegation/waited_for_slot", True),
                json_eq("/delegation/child_runs", 2),
                effect_count("test.send", 0),
            ],
            fixture_id="delegation-frozen-runtime-v5",
            scenario=(
                "Frozen limit: with one child slot, the second parent waits instead of "
                "failing or deadlocking, and both children still run."
            ),
            truth_derivation=(
                "Human-labeled slot ledger: slot_capacity=1 with two concurrent parents "
                "records a wait for the second parent and two completed child Runs."
            ),
        ),
        _case(
            case_id="delegation-frozen-skipped-child-join",
            family_id="delegation-frozen-limits",
            input_obj={
                "parent_run_id": "run-parent-5",
                "children": [{"status": "skipped"}, {"status": "succeeded"}],
                "child_application_id": "app-child-c",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/delegation/join_count", 1),
                effect_count("delegation.child_dispatch", 1),
            ],
            fixture_id="delegation-frozen-runtime-v5",
            scenario=(
                "Frozen limit: a skipped child plus a succeeded child must join exactly once "
                "and dispatch only the child that actually ran."
            ),
            truth_derivation=(
                "Human-labeled join ledger: one skipped child is not dispatched and one "
                "succeeded child is; the parent join fires once with child_dispatch=1."
            ),
        ),
    ]

    approval: list[tuple[dict, dict]] = [
        _case(
            case_id="approval-dev-duplicate-approval",
            family_id="approval-dev-human-approval",
            input_obj={
                "proposal": {
                    "operation": "send_invoice",
                    "capability": "test.send",
                    "parameters": {"amount": "10.00"},
                },
                "decisions": [
                    {"decision": "approve", "actor": "user-42"},
                    {"decision": "approve", "actor": "user-42"},
                ],
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/approval/status", "approved"),
                effect_count("test.send", 1),
            ],
            fixture_id="approval-dev-ledger-v1",
            scenario=(
                "The same human approves the same write twice; the canonical parameter hash "
                "is unchanged, so the external send happens exactly once."
            ),
            truth_derivation=(
                "Human-labeled operation ledger: duplicate decisions with the same canonical "
                "parameter hash are a replay; the stub ledger records exactly one test.send."
            ),
        ),
        _case(
            case_id="approval-dev-parameter-change",
            family_id="approval-dev-human-approval",
            input_obj={
                "proposal": {
                    "operation": "send_invoice",
                    "capability": "test.send",
                    "parameters": {"amount": "10.00"},
                },
                "submitted_parameters": {"amount": "12.00"},
                "decision": "approve",
            },
            assertions=[
                run_state("failed"),
                json_eq("/approval/status", "rejected"),
                json_eq("/approval/reason", "parameter_hash_mismatch"),
                effect_count("test.send", 0),
            ],
            fixture_id="approval-dev-ledger-v1",
            scenario=(
                "The approved parameters change before submission, so the old approval must "
                "be refused and no external write may be sent."
            ),
            truth_derivation=(
                "Human-labeled operation ledger: the canonical parameter hash of "
                "{amount:12.00} differs from the approved {amount:10.00}, so the decision is "
                "rejected with effect count 0."
            ),
        ),
        _case(
            case_id="approval-dev-wrong-approver",
            family_id="approval-dev-human-approval",
            input_obj={
                "proposal": {
                    "operation": "send_invoice",
                    "capability": "test.send",
                    "parameters": {"amount": "10.00"},
                },
                "responsible_human": "user-42",
                "approver": "user-99",
                "decision": "approve",
            },
            assertions=[
                run_state("failed"),
                json_eq("/approval/status", "rejected"),
                json_eq("/approval/reason", "approver_not_responsible"),
                effect_count("test.send", 0),
            ],
            fixture_id="approval-dev-ledger-v1",
            scenario=(
                "A human who is not the responsible owner approves the write; the approval "
                "is rejected and the external capability is never invoked."
            ),
            truth_derivation=(
                "Human-labeled approval rule: only the responsible human may approve; "
                "user-99 is not user-42, so the operation is rejected with effect count 0."
            ),
        ),
        _case(
            case_id="approval-frozen-lost-receipt",
            family_id="approval-frozen-lost-receipt",
            input_obj={
                "proposal": {
                    "operation": "send_invoice",
                    "capability": "test.send",
                    "parameters": {"amount": "10.00"},
                },
                "write_succeeded": True,
                "receipt_lost": True,
            },
            assertions=[
                run_state("needs_reconciliation"),
                json_eq("/approval/status", "unknown"),
                effect_count("test.send", 1),
            ],
            fixture_id="approval-frozen-ledger-v1",
            scenario=(
                "The external write succeeded but its receipt was lost, so the run must be "
                "unknown and reconciled, with exactly one real write on the ledger."
            ),
            truth_derivation=(
                "Human-labeled operation ledger: write_succeeded=true and receipt_lost=true "
                "record one test.send; the platform state is needs_reconciliation/unknown, "
                "never succeeded, and no automatic resend is allowed."
            ),
        ),
        _case(
            case_id="approval-frozen-cancel",
            family_id="approval-frozen-cancel",
            input_obj={
                "proposal": {
                    "operation": "send_invoice",
                    "capability": "test.send",
                    "parameters": {"amount": "10.00"},
                },
                "decision": "cancel",
            },
            assertions=[
                run_state("cancelled"),
                json_eq("/approval/status", "cancelled"),
                effect_count("test.send", 0),
            ],
            fixture_id="approval-frozen-ledger-v1",
            scenario=(
                "Cancel an approval before the external write is sent: the run is cancelled "
                "and the capability is never invoked."
            ),
            truth_derivation=(
                "Human-labeled operation ledger: a cancel before dispatch terminates the "
                "operation as cancelled with zero test.send effects."
            ),
        ),
        _case(
            case_id="approval-frozen-expired",
            family_id="approval-frozen-expiry",
            input_obj={
                "proposal": {
                    "operation": "send_invoice",
                    "capability": "test.send",
                    "parameters": {"amount": "10.00"},
                },
                "decision": "approve",
                "expired": True,
            },
            assertions=[
                run_state("failed"),
                json_eq("/approval/status", "expired"),
                effect_count("test.send", 0),
            ],
            fixture_id="approval-frozen-ledger-v1",
            scenario=(
                "An approval decision arrives after the approval window expired; the stale "
                "decision is rejected and nothing is sent."
            ),
            truth_derivation=(
                "Human-labeled approval rule: expired=true means the decision is stale; the "
                "operation fails as expired with zero test.send effects."
            ),
        ),
    ]

    lifecycle: list[tuple[dict, dict]] = [
        _case(
            case_id="lifecycle-dev-old-graph-restore",
            family_id="lifecycle-dev-graph-restore",
            input_obj={
                "published_version": "v1",
                "mirror_present": True,
                "attempted_revision": "v2",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/recovery/graph_version", "v1"),
                json_eq("/recovery/used_mirror", True),
                effect_count("test.send", 0),
            ],
            fixture_id="lifecycle-dev-graphs-v1",
            scenario=(
                "Restore an old published version v1 from its frozen graph even after v2 "
                "was published, without substituting the newer graph."
            ),
            truth_derivation=(
                "Human-labeled DB/artifact invariant: the v1 mirror is present, so recovery "
                "loads graph_version v1 with used_mirror=true; the v2 revision is ignored."
            ),
        ),
        _case(
            case_id="lifecycle-dev-terminal-late-event",
            family_id="lifecycle-dev-graph-restore",
            input_obj={
                "event": "late_terminal_event",
                "event_state": "succeeded",
                "terminal_state": "succeeded",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/run/terminal_state_stable", True),
                json_eq("/recovery/resurrected", False),
                effect_count("test.send", 0),
            ],
            fixture_id="lifecycle-dev-graphs-v1",
            scenario=(
                "A late terminal event arrives after the run is already terminal; the state "
                "must stay monotonic and the run must not be resurrected."
            ),
            truth_derivation=(
                "Human-labeled state invariant: the run is already terminal at succeeded, so "
                "a late event leaves the state unchanged and resurrected=false."
            ),
        ),
        _case(
            case_id="lifecycle-dev-lease-expired-old-worker",
            family_id="lifecycle-dev-graph-restore",
            input_obj={
                "lease_expired": True,
                "old_worker_alive": True,
                "attempt_count": 1,
            },
            assertions=[
                run_state("recovery_pending"),
                json_eq("/recovery/second_execution", False),
                json_eq("/recovery/recovery_pending", True),
                effect_count("test.send", 0),
            ],
            fixture_id="lifecycle-dev-graphs-v1",
            scenario=(
                "The attempt lease expired while the old worker process is still alive; the "
                "platform must mark recovery_pending and must not start a second execution."
            ),
            truth_derivation=(
                "Human-labeled state invariant: an expired lease with a live worker is "
                "ambiguous, so recovery_pending=true and second_execution=false."
            ),
        ),
        _case(
            case_id="lifecycle-frozen-delete-then-recover",
            family_id="lifecycle-frozen-recovery",
            input_obj={
                "input_deleted": True,
                "restore_attempted": True,
                "deleted_object_id": "object-deleted-1",
            },
            assertions=[
                run_state("failed"),
                json_eq("/recovery/input_restored", False),
                json_eq("/recovery/refused_deleted_input", True),
                effect_count("test.send", 0),
            ],
            fixture_id="lifecycle-frozen-graphs-v2",
            scenario=(
                "Restore a run whose input object was deleted; the platform must refuse to "
                "restore deleted input and fail explicitly."
            ),
            truth_derivation=(
                "Human-labeled DB/artifact invariant: the input object is marked deleted, so "
                "restore fails with input_restored=false and refused_deleted_input=true."
            ),
        ),
        _case(
            case_id="lifecycle-frozen-redis-loss",
            family_id="lifecycle-frozen-recovery",
            input_obj={
                "redis_restarted": True,
                "jobs_lost_from_memory": True,
                "postgres_state": "running",
            },
            assertions=[
                run_state("succeeded"),
                json_eq("/recovery/events_replayed", True),
                json_eq("/recovery/state_source", "postgres"),
                effect_count("test.send", 0),
            ],
            fixture_id="lifecycle-frozen-graphs-v2",
            scenario=(
                "Redis restarts and loses its wake-up state; the run must replay events from "
                "PostgreSQL and finish without losing jobs or facts."
            ),
            truth_derivation=(
                "Human-labeled durability invariant: job and fact state live in PostgreSQL, "
                "so a Redis restart replays events and the state source is postgres."
            ),
        ),
        _case(
            case_id="lifecycle-frozen-deleted-input-reuse",
            family_id="lifecycle-frozen-recovery",
            input_obj={
                "input_deleted": True,
                "cached_reference": True,
                "cached_object_id": "object-deleted-2",
            },
            assertions=[
                run_state("failed"),
                json_eq("/recovery/deleted_input_reused", False),
                json_eq("/cache/served_deleted_body", False),
                effect_count("test.send", 0),
            ],
            fixture_id="lifecycle-frozen-graphs-v2",
            scenario=(
                "A cached reference points at a deleted input; the platform must not serve "
                "the deleted body or reuse it as fresh input."
            ),
            truth_derivation=(
                "Human-labeled cache invariant: deleted content must never be served from "
                "cache, so deleted_input_reused=false and served_deleted_body=false."
            ),
        ),
    ]

    return sales + routing + rag + memory + delegation + approval + lifecycle


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def _dump(payload) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def build() -> dict[str, bytes]:
    """The exact committed bytes of the seven case documents and the registry."""
    fixtures = fixture_entries()
    pairs = _cases(fixtures)

    # one family per split, one template per family; every case described once.
    case_ids = [case["case_id"] for case, _ in pairs]
    if len(case_ids) != len(set(case_ids)):
        raise AssertionError("the builder produced a duplicate case id")
    used_families = {case["family_id"] for case, _ in pairs}
    if used_families != set(FAMILIES):
        raise AssertionError(f"unused or unknown families: {used_families ^ set(FAMILIES)}")
    for family_id, family in FAMILIES.items():
        if family["category"] not in f13b_corpus.CATEGORIES:
            raise AssertionError(family_id)
        if family["split"] not in f13b_corpus.SPLITS:
            raise AssertionError(family_id)

    documents: dict[str, dict] = {
        category: {"cases": []} for category in f13b_corpus.CATEGORIES
    }
    registry_cases: dict[str, dict] = {}
    for case, meta in pairs:
        documents[case["category"]]["cases"].append(case)
        registry_cases[case["case_id"]] = meta

    files: dict[str, bytes] = {}
    file_pins: dict[str, dict] = {}
    for category in f13b_corpus.CATEGORIES:
        filename = f"cases.{category}.json"
        payload = _dump(documents[category])
        files[filename] = payload
        file_pins[filename] = {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "cases": len(documents[category]["cases"]),
            "category": category,
        }

    probe = f13b_corpus.Corpus(
        directory=CORPUS_DIR,
        schema={},
        registry={},
        files=documents,
    )
    summary = f13b_corpus.summarize(probe)
    schema_bytes = (PRODUCT_ROOT / "contracts" / "evaluation-case.schema.json").read_bytes()

    # F13B/T104: the frozen corpus references (does not absorb) the imported
    # tau2-bench subset, so the two builders stay independent and the T102
    # seven-category documents are not polluted with external tasks. The
    # imported subset pins this relationship the other way: its own registry
    # names the corpus registry by path, while this pin makes drift visible.
    external_registry_path = PRODUCT_ROOT / "tests" / "f13b" / "external" / "registry.json"
    if not external_registry_path.exists():
        raise FileNotFoundError(
            f"the T104 external import registry is missing: {external_registry_path}"
        )

    registry = {
        "schema": f13b_corpus.REGISTRY_SCHEMA,
        "generated_by": "tests/f13b/build_corpus.py",
        "schema_path": "product/contracts/evaluation-case.schema.json",
        "schema_sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "thresholds": {
            "near_duplicate_jaccard": f13b_corpus.NEAR_DUPLICATE_THRESHOLD,
            "near_duplicate_ngram": f13b_corpus.NEAR_DUPLICATE_NGRAM,
        },
        "collection": {
            "required_facts": list(f13b_corpus.COLLECTION_FACTS),
            "rule": (
                "A case may be scored only when every required fact was collected. An "
                "empty, skipped or incomplete case is not_run, never passed."
            ),
        },
        "files": file_pins,
        "families": {
            family_id: {
                "category": family["category"],
                "split": family["split"],
                "template": family["template"],
            }
            for family_id, family in sorted(FAMILIES.items())
        },
        "categories": summary,
        "cases": registry_cases,
        "fixtures": fixtures,
        "routing_catalogs": ROUTING_CATALOGS,
        "rag_sources": RAG_SOURCES,
        "external_imports": {
            "registry_path": "product/tests/f13b/external/registry.json",
            "registry_sha256": hashlib.sha256(
                external_registry_path.read_bytes()
            ).hexdigest(),
            "source": "sierra-research/tau2-bench@da0835f6c67baef58bdda18f2a3b037dfb3a1754",
            "source_commit": "da0835f6c67baef58bdda18f2a3b037dfb3a1754",
            "label": "adapted_external",
            "rule": (
                "Imported adapted cases are frozen_test only. Their families, "
                "normalized templates and near duplicates must not overlap this "
                "corpus's dev split, and their results are not released-benchmark "
                "scores; the external registry pins this relationship."
            ),
        },
    }
    files["registry.json"] = _dump(registry)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="rewrite the committed corpus")
    args = parser.parse_args(argv)
    built = build()
    if args.write:
        CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        for filename, payload in sorted(built.items()):
            (CORPUS_DIR / filename).write_bytes(payload)
            print(f"wrote {CORPUS_DIR / filename} ({len(payload)} bytes)")
        return 0
    drifted = [
        filename
        for filename, payload in sorted(built.items())
        if (CORPUS_DIR / filename).read_bytes() != payload
    ]
    if drifted:
        print("corpus drift: " + ", ".join(drifted))
        return 1
    print("corpus matches the builder")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
