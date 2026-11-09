"""F08/T084 (#85) routing evaluator: real catalog, real requests, recorded outcomes.

This is the executable half of the frozen routing corpus. Every case is submitted
through the real REST surface by a second principal and routed by the real
``route_request`` maintenance handler against a real published catalog; every
outcome is read back from the authoritative PostgreSQL rows. Nothing is mocked:

* catalog — the sales version is published through the real T068
  ``submit`` -> review decision path in the calling test; the other five visible
  fixture versions and the hidden payroll version are real compiler manifests
  published with the documented F08/T077 version-row shape. The report records
  which application came from which publication path.
* requests — the second user's real session, cookie+CSRF HTTP, ``Idempotency-Key``.
* routing — the compiled ``cmd/maintenance`` process and the real control-model
  ledger. The control model is the dev stub (``stub: true``); the server, not the
  stub, owns candidate authorization, the input check and the decision policy.
* readings — ``platform.requests``, ``platform.request_routing_candidates``,
  ``platform.request_routing_decisions``, ``platform.interactions`` and
  ``platform.runs``.

Metrics computed here (never hand-written):

* **false auto execution** — a case the corpus says must not auto-dispatch (any
  ``awaiting_*`` or ``failed`` expectation) whose real request created a Run.
* **clarification completion** — for every case that actually reached
  ``awaiting_input`` / ``awaiting_selection``, whether a real reply with an
  artifact / candidate made the same request dispatch.
* **candidate recall / no-leak** — the authorized candidate snapshot versus the
  corpus expectation, including the empty set for no-match and unseen cases.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from support import execution_support as execution
from support import f00b, f02a, f08, t033, t069, t077, t079
from support import stack as st
from f08.routing import corpus

REPORT_SCHEMA = "orchara.t084-routing-eval/1"

# The catalog of the frozen corpus (registry.json catalogs.base). Every fixture
# card is fully specified: the compiler default card would make every fixture a
# candidate for every message (it reuses 'aggregation' / 'monthly report').
FIXTURE_APPS = {
    "forecast": {
        "name": "T084 forecast",
        "purpose": "收入趋势预测",
        "includes": ["趋势预测"],
        "excludes": ["外部写入"],
        "required_inputs": ["历史收入数据"],
        "expected_outputs": ["趋势说明"],
        "examples": ["季度收入变化", "收入预测说明"],
    },
    "inventory": {
        "name": "T084 inventory",
        "purpose": "库存流水核对",
        "includes": ["库存核对"],
        "excludes": ["外部写入"],
        "required_inputs": ["库存流水文件"],
        "expected_outputs": ["核对结果"],
        "examples": ["缺货检查", "补货建议"],
    },
    "invoice": {
        "name": "T084 invoice",
        "purpose": "invoice reconciliation",
        "includes": ["invoice matching"],
        "excludes": ["external writes"],
        "required_inputs": ["invoice pdf"],
        "expected_outputs": ["reconciliation result"],
        "examples": ["invoice export", "invoice batch"],
    },
    "alpha": {
        "name": "T084 alpha",
        "purpose": "合同条款审阅",
        "includes": ["合同审阅"],
        "excludes": ["外部写入"],
        "required_inputs": [],
        "expected_outputs": ["审阅意见"],
        "examples": ["条款检查", "条款对照"],
    },
    "beta": {
        "name": "T084 beta",
        "purpose": "合同风险审阅",
        "includes": ["风险审阅"],
        "excludes": ["外部写入"],
        "required_inputs": [],
        "expected_outputs": ["风险提示"],
        "examples": ["风险检查", "风险标注"],
    },
    "payroll": {
        "name": "T084 payroll (hidden)",
        "purpose": "生成本月工资单",
        "includes": ["工资核算"],
        "excludes": ["外部写入"],
        "required_inputs": ["员工名单"],
        "expected_outputs": ["工资单"],
        "examples": ["月度工资", "年度结算"],
    },
}
VISIBLE_KEYS = ("sales", "forecast", "inventory", "invoice", "alpha", "beta")
HIDDEN_KEY = "payroll"


@dataclass
class PublishedApp:
    key: str
    application_id: str
    version_id: str
    version_no: int
    card: dict
    publication: str

    def as_fact(self) -> dict:
        return {
            "key": self.key,
            "application_id": self.application_id,
            "version_id": self.version_id,
            "version_no": self.version_no,
            "purpose": self.card.get("purpose", ""),
            "publication": self.publication,
        }


def sales_application_from_review(stack, published, *, version_id: str, review_id: str,
                                  log_path: Path) -> PublishedApp:
    """Record the sales version that the real T068 paths already published."""
    return PublishedApp(
        key="sales",
        application_id=published["application_id"],
        version_id=version_id,
        version_no=1,
        card=published["card"],
        publication="t068_real:" + review_id,
    )


def publish_fixture_catalog(stack, publisher_session) -> dict[str, PublishedApp]:
    """Publish the five visible fixture apps and the hidden payroll app.

    The sales app is not in this dict: it is published by the real T068 path in
    the evaluator test and merged into the catalog by :func:`build_catalog`.
    """
    apps: dict[str, PublishedApp] = {}
    for key, spec in FIXTURE_APPS.items():
        version = t077.publish_version(
            stack,
            publisher_session,
            name=spec["name"],
            purpose=spec["purpose"],
            includes=list(spec["includes"]),
            excludes=list(spec["excludes"]),
            required_inputs=list(spec["required_inputs"]),
            expected_outputs=list(spec["expected_outputs"]),
            examples=list(spec["examples"]),
        )
        apps[key] = PublishedApp(
            key=key,
            application_id=version.application_id,
            version_id=version.version_id,
            version_no=version.version_no,
            card=dict(version.card),
            publication="t077_fixture",
        )
    return apps


def build_catalog(stack, admin, second_principal_id: str, publisher_session,
                  sales: PublishedApp) -> dict[str, PublishedApp]:
    """Publish the fixture apps, grant the second user read on the visible ones.

    The payroll app is published by the same creator and deliberately receives no
    grant: for the second user it is an existing-but-unreadable application.
    """
    catalog = publish_fixture_catalog(stack, publisher_session)
    catalog["sales"] = sales
    for key in VISIBLE_KEYS:
        assert key in catalog, key
        f02a.grant_read(admin, second_principal_id, catalog[key].application_id)
    return catalog


def seed_route_control(stack, publisher_principal_id: str) -> dict:
    return t079.seed_route_control_model(stack, publisher_principal_id)


def version_to_key(catalog: dict[str, PublishedApp]) -> dict[str, str]:
    return {app.version_id: key for key, app in catalog.items()}


def _message_for(case: corpus.Case, catalog: dict[str, PublishedApp]) -> str:
    if "{{payroll_version_id}}" in case.message:
        return case.message.replace("{{payroll_version_id}}", catalog[HIDDEN_KEY].version_id)
    return case.message


def _interaction_row(stack, request_id: str) -> dict | None:
    return t079.pending_interaction_row(stack.dbname, request_id)


def _run_rows_for_request(stack, request_id: str) -> list[dict]:
    rows = st.sql_rows(
        stack.dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, state, "
        "application_version_id::text AS application_version_id "
        "FROM platform.runs "
        f"WHERE request_id = {st.quote_literal(request_id)}::uuid ORDER BY created_at, id",
    )
    return rows


def _classify(row: dict) -> str:
    state = row["state"]
    if state == "dispatched":
        return "auto_dispatch"
    if state == "awaiting_selection":
        return "awaiting_selection"
    if state == "awaiting_input":
        return "awaiting_input"
    if state == "failed":
        return "failed"
    return "unexpected:" + state


def _complete_clarification(stack, second, catalog, request_id: str, case: corpus.Case,
                            actual_decision: str, candidates: list[str],
                            attachment_id: str) -> dict:
    """Answer the real pending interaction and route again; report what happened."""
    interaction = _interaction_row(stack, request_id)
    assert interaction is not None, f"{case.case_id}: no pending interaction"
    outcome: dict = {"attempted": False, "replied": False, "completed": False,
                     "reply_state": None, "error_code": None, "run_id": None,
                     "chosen": None}
    outcome["attempted"] = True
    keys = version_to_key(catalog)
    if actual_decision == "awaiting_selection":
        preferred = [key for key in case.expected_candidates if key in candidates]
        chosen = preferred[0] if preferred else candidates[0]
        chosen_version = catalog[chosen].version_id
        body = {"interaction_id": interaction["id"], "candidate_version_id": chosen_version}
    else:
        chosen = case.expected["application"] or (candidates[0] if candidates else None)
        body = {"interaction_id": interaction["id"], "artifact_ids": [attachment_id]}
    outcome["chosen"] = chosen
    reply = t079.reply(second.session, request_id, body)
    outcome["reply_state"] = reply.json.get("state") if reply.status == 202 else None
    if reply.status != 202:
        outcome["error_code"] = _error_code(reply)
        return outcome
    outcome["replied"] = True
    t079.route_once(stack)
    row = t079.request_state(stack.dbname, request_id)
    outcome["state"] = row["state"]
    outcome["error_code"] = row["error_code"]
    runs = _run_rows_for_request(stack, request_id)
    outcome["run_id"] = runs[0]["id"] if runs else None
    expected_version = catalog[chosen].version_id if chosen in catalog else None
    outcome["completed"] = bool(
        row["state"] == "dispatched"
        and runs
        and runs[0]["owner_id"] == second.principal_id
        and (expected_version is None or runs[0]["application_version_id"] == expected_version)
    )
    return outcome


def _error_code(response) -> str | None:
    try:
        body = response.json
    except Exception:  # noqa: BLE001 - a non-JSON body has no code
        return None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"].get("code")
    return None


def run_case(stack, second, catalog: dict[str, PublishedApp], case: corpus.Case,
             attachment_id: str) -> dict:
    """Submit, route and record exactly one corpus case through the real surface."""
    keys = version_to_key(catalog)
    started = time.monotonic()
    conversation = f08.conversation(second.session)
    message = _message_for(case, catalog)
    artifact_ids = [attachment_id] if case.attachments else []
    submitted = f08.submit_request(
        second.session, conversation, message, artifact_ids=artifact_ids,
        key=f00b.new_uuid(),
    )
    request = f08.request_of(submitted)
    route = t079.route_once(stack)
    assert route.completed.returncode == 0, route.stdout
    row = t079.request_state(stack.dbname, request["id"])
    decision = _classify(row)
    candidate_rows = t079.candidate_rows(stack.dbname, request["id"])
    actual_candidates = [keys.get(item["application_version_id"], "unknown")
                         for item in candidate_rows]
    interactions = t079.interaction_rows(stack.dbname, request["id"])
    run_rows = _run_rows_for_request(stack, request["id"])
    record = {
        "case_id": case.case_id,
        "cell": case.category,
        "family_id": case.family_id,
        "split": case.split,
        "message": message,
        "attachments": [attachment["kind"] for attachment in case.attachments],
        "expected": case.expected,
        "actual": {
            "decision": decision,
            "state": row["state"],
            "error_code": row["error_code"],
            "candidates": actual_candidates,
            "candidate_version_ids": [
                item["application_version_id"] for item in candidate_rows],
            "candidate_ranks": [item["rank"] for item in candidate_rows],
            "interaction_kinds": [item["kind"] for item in interactions],
            "run_id": run_rows[0]["id"] if run_rows else None,
            "run_owner_matches_second_user": (
                run_rows[0]["owner_id"] == second.principal_id if run_rows else None
            ),
            "run_version_matches_expected": (
                run_rows[0]["application_version_id"]
                == catalog[case.expected["application"]].version_id
                if run_rows and case.expected["application"] in catalog else None
            ),
        },
        "decision_matches": False,
        "candidate_set_matches": set(actual_candidates) == set(case.expected["candidates"]),
        "candidate_recall": _recall(case.expected["candidates"], actual_candidates),
        "false_auto_execution": False,
        "clarification_required": False,
        "clarification_completed": None,
        "final_file_ok": None,
        "elapsed_ms": 0,
    }

    expected = case.expected
    if decision == expected["decision"]:
        if decision == "failed":
            record["decision_matches"] = row["error_code"] == expected["no_run_reason"]
        elif decision == "awaiting_selection":
            record["decision_matches"] = "selection" in record["actual"]["interaction_kinds"]
        elif decision == "awaiting_input":
            record["decision_matches"] = "input" in record["actual"]["interaction_kinds"]
        else:
            record["decision_matches"] = bool(
                run_rows
                and run_rows[0]["owner_id"] == second.principal_id
                and case.expected["application"] in catalog
                and run_rows[0]["application_version_id"]
                == catalog[case.expected["application"]].version_id
            )
    if decision == "auto_dispatch" and expected["decision"] != "auto_dispatch":
        record["false_auto_execution"] = True

    if decision in ("awaiting_input", "awaiting_selection"):
        record["clarification_required"] = True
        completion = _complete_clarification(
            stack, second, catalog, request["id"], case, decision,
            list(actual_candidates), attachment_id,
        )
        record["clarification"] = completion
        record["clarification_completed"] = completion["completed"]

    record["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return record


def _recall(expected: list[str], actual: list[str]) -> float | None:
    if not expected:
        return None
    matched = len(set(expected) & set(actual))
    return matched / len(expected)


def aggregate(records: list[dict]) -> dict:
    """Compute the reported rates from the real per-case records."""
    by_split: dict[str, dict] = {}
    for split in corpus.SPLITS:
        cases = [record for record in records if record["split"] == split]
        no_auto = [record for record in cases if record["expected"]["decision"] != "auto_dispatch"]
        false_auto = [record for record in no_auto if record["false_auto_execution"]]
        clarification = [record for record in cases if record["clarification_required"]]
        completed = [record for record in clarification if record["clarification_completed"]]
        recalls = [record["candidate_recall"] for record in cases
                   if record["candidate_recall"] is not None]
        no_candidate = [record for record in cases if not record["expected"]["candidates"]]
        leaks = [record["case_id"] for record in no_candidate
                 if record["actual"]["candidates"]]
        by_cell: dict[str, dict] = {}
        for cell in corpus.CELLS:
            cell_cases = [record for record in cases if record["cell"] == cell]
            by_cell[cell] = {
                "cases": len(cell_cases),
                "decision_matches": sum(1 for record in cell_cases if record["decision_matches"]),
                "false_auto_executions": sum(
                    1 for record in cell_cases if record["false_auto_execution"]),
                "clarification_required": sum(
                    1 for record in cell_cases if record["clarification_required"]),
                "clarification_completed": sum(
                    1 for record in cell_cases if record["clarification_completed"]),
            }
        by_split[split] = {
            "cases": len(cases),
            "families": len({record["family_id"] for record in cases}),
            "decision_accuracy": _rate(
                sum(1 for record in cases if record["decision_matches"]), len(cases)),
            "false_auto_executions": len(false_auto),
            "false_auto_execution_rate": _rate(len(false_auto), len(no_auto)),
            "false_auto_execution_basis": f"{len(false_auto)}/{len(no_auto)} cases that must not auto-execute",
            "clarification_required": len(clarification),
            "clarification_completed": len(completed),
            "clarification_completion_rate": _rate(len(completed), len(clarification)),
            "candidate_recall": (
                round(sum(recalls) / len(recalls), 6) if recalls else None),
            "candidate_recall_cases": len(recalls),
            "no_candidate_leaks": leaks,
            "runs_created": sum(1 for record in cases if record["actual"]["run_id"]),
            "by_cell": by_cell,
        }
    return {
        "splits": by_split,
        "total_cases": len(records),
        "false_auto_executions_total": sum(
            1 for record in records if record["false_auto_execution"]),
        "clarification_completion_total": {
            "required": sum(1 for record in records if record["clarification_required"]),
            "completed": sum(1 for record in records if record["clarification_completed"]),
        },
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def report_document(*, stack, results: dict[str, list[dict]], catalog: dict[str, PublishedApp],
                    publication: dict, environment: dict, journey: dict | None = None) -> dict:
    registry = corpus.load_registry()
    cases = corpus.load_cases()
    document = {
        "schema": REPORT_SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "environment": environment,
        "corpus": {
            "corpus_version": registry["corpus_version"],
            "frozen_at": registry["frozen_at"],
            "registry_sha256": corpus.sha256_file(corpus.REGISTRY),
            "dataset_digest": corpus.dataset_digest(cases),
            "case_count": len(cases),
            "cells": list(corpus.CELLS),
            "splits": list(corpus.SPLITS),
            "families": registry["families"],
        },
        "catalog": {key: app.as_fact() for key, app in catalog.items()},
        "publication": publication,
        "results": results,
        "summary": aggregate([record for split in corpus.SPLITS for record in results[split]]),
    }
    if journey is not None:
        document["journey"] = journey
    return document


def write_report(document: dict, *, default_path: Path) -> Path:
    """Write the machine-readable result next to the raw evidence.

    The pytest run always writes into its own tmp dir; the evidence run sets
    ``T084_EVAL_REPORT`` so the exact same bytes land under ``reports/f04b/``.
    """
    path = Path(os.environ.get("T084_EVAL_REPORT", str(default_path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def upload_sales_csv(stack, session) -> tuple[str, bytes]:
    content = (corpus.DATASETS / "inputs" / "sales_sample.csv").read_bytes()
    artifact_id = t069.upload_ready_artifact(
        stack, session, content, purpose="task_input", media_type="text/csv")
    return artifact_id, content


def make_request_input(*, message: str) -> dict:
    """The documented published-run input JSON for the sales chain.

    The execution entry seals the chat message bytes as the Run input, and the
    preset chain's start node requires a JSON object, so the journey carries the
    task document itself. The routing corpus (which never executes) uses natural
    language.
    """
    return {"source": "sales_sample.csv", "message": message}


def supervisor_report(stack, *, heartbeat: str = "100ms") -> dict:
    env = execution.supervisor_exec_env(stack, heartbeat=heartbeat)
    with execution.FinalizeWorker(stack):
        completed = execution.run_supervisor_exec(stack, env)
    return execution.exec_report(completed)


__all__ = [
    "REPORT_SCHEMA",
    "PublishedApp",
    "FIXTURE_APPS",
    "VISIBLE_KEYS",
    "HIDDEN_KEY",
    "build_catalog",
    "publish_fixture_catalog",
    "seed_route_control",
    "run_case",
    "aggregate",
    "report_document",
    "write_report",
    "upload_sales_csv",
    "make_request_input",
    "supervisor_report",
    "sales_application_from_review",
    "version_to_key",
]
