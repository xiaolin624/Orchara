"""F04B/T091 acceptance: the ten-node workflow, two approvals and nested joins.

Every case goes through the product's real path: a real draft saved through the
public API, the real Go workflow compiler (``POST /validate`` and the debug
seal), the compiled ``cmd/supervisor exec --once`` in WSL2/runsc, the real
capability proxy against the documented dev-stub model provider, the real
worker-capacity node-approval route, the contract approval decision path, the one
F03/T035 resume transaction, the non-contract node-results surface and the
owner-only SSE replay.

What is proven here (T091 / GitHub #92):

* the checked-in ten-node fixture (all ten contract node types, nested
  condition/parallel regions, condition ports, join-by-source inputs, fixed model
  profile / tool version / child application version) saves without DSL
  validation, reloads byte-stably and validates with an ``X-Manifest-SHA256``;
* invalid variants (cross-region edge, bad port, bad join sources, type mismatch,
  cycle, capability keys the canvas never displays, unknown node type) are
  located by ``/validate`` and one is refused by a real debug submission with
  zero Runs/Jobs; saving the illegal variant still succeeds (save is not the
  validation gate);
* a real run with two human approvals waits twice, resumes twice in new
  processes, converges through a nested condition/parallel join and succeeds,
  with join-by-source values in the final output; completed nodes (and the joins)
  are reused instead of re-executed, approvals/resumes/operations are never
  duplicated and the budget is not reset;
* the optional pure-compute failure is folded into the join envelope as error
  data and the run still succeeds; a required failure fails fast and never joins;
  an optional agent capability failure is never downgraded;
* the run view can rebuild the node state from the durable node-results and the
  SSE replay after the run, and the run keeps using the frozen snapshot after the
  draft changes underneath it.

Not covered here (recorded as not_run in the report): browser DOM interaction,
real vendor models (only the dev-stub profile exists) and a cross-version
checkpoint restore (out of scope for this batch).
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest

from support import f02a, t025, t027, t032, t033, t035, t042, t083, t086, t087, t091
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)

TEN_NODE_TYPES = {
    "start", "end", "model", "agent", "python", "call_app", "retrieve", "condition", "join",
    "approval",
}


@pytest.fixture(scope="module")
def sandbox() -> None:
    if not t033.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: `wsl -d Ubuntu-24.04 -u root -e bash -lc "
            "'runsc --version'` failed; build the rootfs with "
            "deploy/sandbox/build_python_rootfs.sh"
        )


def creator():
    return t033.golden_owner


def saved_definition(stack, owner, definition: dict, layout: dict, *, name: str) -> tuple[str, str]:
    application = t025.create_application_or_fail(owner.creator, name)
    application_id = application["id"]
    reloaded = t025.get_draft(owner.creator, application_id)
    assert reloaded.status == 200, reloaded.text
    saved = t025.save_draft(
        owner.creator,
        application_id,
        reloaded.header("ETag"),
        definition=definition,
        layout=layout,
        capability_card=f02a.full_card(),
    )
    assert saved.status == 200, saved.text
    return application_id, saved.header("ETag")


def data_rows(items: list[dict], node_id: str) -> list[dict]:
    return [item for item in items if item["node_id"] == node_id and item["kind"] == "data"]


def assert_waiting_for(stack, run_id: str, node_id: str, *, resume_jobs: int) -> dict:
    state = t091.run_row(stack.dbname, run_id)
    assert state["state"] == "waiting_approval", state
    assert state["phase"] == "waiting", state
    waiting = t035.waiting_row(stack.dbname, run_id)
    assert waiting is not None and waiting["kind"] == t035.KIND_APPROVAL, waiting
    assert waiting["resume_job_id"] is None, waiting
    approval = t091.approval_for_node(stack.dbname, run_id, node_id)
    assert approval is not None and approval["state"] == "pending", (node_id, approval)
    assert len(t091.resume_jobs(stack.dbname, run_id)) == resume_jobs
    return approval


def assert_report(report: dict, outcome: str, *, error_code: str = "") -> None:
    assert report["status"] == "executed", report
    assert report["outcome"] == outcome, report
    assert report.get("error_code", "") == error_code, report


# ---------------------------------------------------------------------------
# 1. the ten-node fixture: save (not a gate) -> reload byte-stable -> validate
# ---------------------------------------------------------------------------
def test_the_ten_node_fixture_saves_reloads_byte_stably_and_validates(t033_stack) -> None:
    owner = t033.golden_owner(t033_stack)
    definition, layout, deps = t091.ten_node_definition(t033_stack, owner)
    assert t091.fixture_node_types(definition) == TEN_NODE_TYPES

    application_id, _etag = saved_definition(
        t033_stack, owner, definition, layout, name="t091 ten-node fixture"
    )

    first = t025.get_draft(owner.creator, application_id)
    assert first.status == 200, first.text
    reloaded = first.json
    assert reloaded["definition"] == definition, "the saved definition must not lose a key"
    assert reloaded["layout"] == layout
    # jsonb canonicalises key order on the first store; from then on every read is
    # byte-identical and a save of the reloaded document is byte-stable too.
    second = t025.get_draft(owner.creator, application_id)
    assert second.status == 200 and second.raw == first.raw
    resaved = t027.save_draft(owner.creator, application_id, second.header("ETag"),
                              definition=reloaded["definition"], layout=reloaded["layout"],
                              capability_card=reloaded["capability_card"])
    assert resaved.status == 200, resaved.text
    third = t025.get_draft(owner.creator, application_id)
    assert third.status == 200 and third.raw == first.raw

    # Every node keeps exactly the contract config set (no dropped/added key).
    for node in reloaded["definition"]["nodes"]:
        allow = {
            "start": set(),
            "end": set(),
            "python": {"tool_version"},
            "agent": {"model_profile_version", "instructions", "tool_versions",
                      "subapp_versions", "max_turns"},
            "model": {"model_profile_version", "prompt_template", "response_mode"},
            "retrieve": {"knowledge_base_ids", "top_k"},
            "call_app": {"application_version"},
            "condition": {"join_id", "default_port", "cases"},
            "join": {"mode", "fork_id", "required_sources", "optional_sources"},
            "approval": {"title", "expires_seconds"},
        }[node["type"]]
        assert set(node["config"]) == allow, (node["id"], node["type"], node["config"])

    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200, validation.text
    assert validation.json == {"valid": True, "errors": []}, validation.text
    manifest_sha256 = validation.header("X-Manifest-SHA256")
    assert manifest_sha256 and len(manifest_sha256) == 64, manifest_sha256

    # The fixed references are the real seeded rows, not look-alike fixtures.
    assert deps["child_version_id"] in json.dumps(reloaded["definition"])
    assert deps["knowledge_base_id"] in json.dumps(reloaded["definition"])

    # A real debug submission freezes the same bytes into the sealed manifest:
    # the child version joins the manifest's `subapp_versions` closure and the
    # ten node types / edge ports are the ones the canvas authored.
    artifact_id = t042.ready_csv(t033_stack, owner)
    revision = t027.draft_revision(owner.creator, application_id)
    submitted = t027.submit_debug(
        owner.creator,
        application_id,
        draft_revision=revision,
        input_value={"requires_approval": True, "task": "regional sales"},
        artifact_ids=[artifact_id],
    )
    assert submitted.status == 202, submitted.text
    snapshot = t027.snapshot_row(t033_stack.dbname, submitted.json["execution_snapshot_id"])
    manifest = snapshot["manifest"]
    assert manifest["subapp_versions"] == [deps["child_version_id"]], manifest["subapp_versions"]
    frozen_definition = manifest["definition"]
    assert {node["type"] for node in frozen_definition["nodes"]} == TEN_NODE_TYPES
    frozen_ports = {
        (edge["source"], edge["target"]): edge.get("port")
        for edge in frozen_definition["edges"]
        if edge["source"] == "choose"
    }
    assert frozen_ports == {("choose", "approve"): "yes", ("choose", "direct"): "no"}, frozen_ports


def test_saving_an_illegal_definition_is_allowed_but_validate_and_debug_refuse_it(
    t033_stack,
) -> None:
    owner = t033.golden_owner(t033_stack)
    definition, layout, _deps = t091.ten_node_definition(t033_stack, owner)
    application_id, etag = saved_definition(
        t033_stack, owner, definition, layout, name="t091 save is not the gate"
    )

    # An edge that leaves the nested parallel region without reaching its join:
    # the save endpoint accepts it (draft saving is not the validation gate)...
    illegal = json.loads(json.dumps(definition))
    illegal["nodes"].append({
        "id": "escapee",
        "type": "python",
        "inputs": {},
        "output_schema": {"type": "object"},
        "config": {"tool_version": t091.PRESET_TOOL_ID},
    })
    illegal["edges"].append({"source": "fork", "target": "escapee"})
    edge_index = len(illegal["edges"]) - 1
    illegal["edges"].append({"source": "escapee", "target": "join1"})
    saved = t025.save_draft(owner.creator, application_id, etag,
                            definition=illegal, layout=layout, capability_card=f02a.full_card())
    assert saved.status == 200, saved.text
    etag = saved.header("ETag")

    # ... and the real compiler locates it.
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200, validation.text
    assert validation.json["valid"] is False, validation.text
    errors = t025.validation_errors(validation)
    cross = [error for error in errors if error["code"] == "cross_region_edge"]
    assert len(cross) == 1, errors
    t025.assert_location(cross[0], code="cross_region_edge", pointer=f"/edges/{edge_index}")
    assert cross[0].get("node_id") == "fork", cross[0]
    assert "fork->escapee" in cross[0]["message"], cross[0]["message"]

    # A real debug submission is refused with the same location and creates no
    # execution identity at all.
    revision = t027.draft_revision(owner.creator, application_id)
    refused = t027.submit_debug(
        owner.creator, application_id, draft_revision=revision,
        input_value={"requires_approval": True, "task": "x"}, artifact_ids=[],
    )
    assert refused.status == 422, refused.text
    assert refused.json["error"]["code"] == "validation_failed", refused.text
    details = refused.json["error"]["details"]["errors"]
    matching = [error for error in details if error["code"] == "cross_region_edge"]
    assert matching and matching[0]["pointer"] == f"/edges/{edge_index}", details
    assert t091.count_runs_for_application(t033_stack.dbname, application_id) == 0
    assert t091.count_jobs_for_application(t033_stack.dbname, application_id) == 0


def test_each_illegal_ten_node_variant_is_located_with_the_expected_code(t033_stack) -> None:
    owner = t033.golden_owner(t033_stack)
    definition, layout, _deps = t091.ten_node_definition(t033_stack, owner)
    application_id, etag = saved_definition(
        t033_stack, owner, definition, layout, name="t091 illegal variants"
    )
    node_index = {
        node["id"]: index for index, node in enumerate(definition["nodes"])
    }

    variants = []

    bad_port = json.loads(json.dumps(definition))
    bad_port["edges"][1]["port"] = "maybe"
    variants.append(("bad_port", bad_port, "bad_port", "/edges/1"))

    bad_sources = json.loads(json.dumps(definition))
    bad_sources["nodes"][node_index["join1"]]["config"]["required_sources"] = ["join2"]
    variants.append((
        "bad_join_sources", bad_sources, "bad_join_sources",
        f"/nodes/{node_index['join1']}/config/required_sources",
    ))

    type_error = json.loads(json.dumps(definition))
    type_error["nodes"][node_index["choose"]]["config"]["cases"][0]["op"] = "gt"
    type_error["nodes"][node_index["choose"]]["config"]["cases"][0]["value"] = "true"
    variants.append((
        "type_mismatch", type_error, "type_mismatch",
        f"/nodes/{node_index['choose']}/config/cases/0",
    ))

    cycle = json.loads(json.dumps(definition))
    cycle["edges"].append({"source": "child", "target": "choose"})
    variants.append(("cycle", cycle, "cycle", None))

    injected = json.loads(json.dumps(definition))
    # A capability field the model panel never displays, and a raw credential:
    # the server must refuse both instead of trusting the canvas filter.
    injected["nodes"][node_index["direct"]]["config"]["response_schema"] = {"type": "object"}
    injected["nodes"][node_index["direct"]]["config"]["api_key"] = "sk-test-not-a-real-key"
    variants.append(("injected_capability", injected, "bad_config", None))

    unknown_type = json.loads(json.dumps(definition))
    unknown_type["nodes"][node_index["child"]]["type"] = "mystery"
    unknown_type["nodes"][node_index["child"]]["config"] = {"hidden_capability": True}
    variants.append(("unknown_type", unknown_type, "unknown_type", None))

    for name, document, code, pointer in variants:
        saved = t025.save_draft(owner.creator, application_id, etag,
                                definition=document, layout=layout, capability_card=f02a.full_card())
        assert saved.status == 200, (name, saved.text)
        etag = saved.header("ETag")
        validation = t025.validate(owner.creator, application_id)
        assert validation.status == 200, (name, validation.text)
        assert validation.json["valid"] is False, (name, validation.text)
        errors = t025.validation_errors(validation)
        matching = [error for error in errors if error["code"] == code]
        assert matching, (name, errors)
        if pointer is not None:
            assert matching[0]["pointer"] == pointer, (name, matching[0])
        if code == "bad_config":
            assert matching[0]["pointer"].startswith(
                f"/nodes/{node_index['direct']}/config"
            ), matching[0]

    # The last variant is a node type the palette never offers: the server still
    # rejects it even though the draft was saved.
    assert t091.count_runs_for_application(t033_stack.dbname, application_id) == 0


# ---------------------------------------------------------------------------
# 2. the acceptance run: two approvals, nested join, join-by-source values
# ---------------------------------------------------------------------------
def test_two_approvals_resume_the_same_run_through_the_nested_join(
    t033_stack, sandbox
) -> None:
    owner, application_id, run = t091.submit_acceptance_run(
        t033_stack, t091.two_approval_definition(), name="t091 two approvals"
    )

    # The same bytes the run is about to seal pass the publish validator, and the
    # validate manifest hash is the one the debug submission freezes.
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )
    manifest_sha256 = validation.header("X-Manifest-SHA256")
    assert manifest_sha256 and len(manifest_sha256) == 64, manifest_sha256
    run_row = t091.run_row(t033_stack.dbname, run["id"])
    assert run_row["manifest_sha256"] == manifest_sha256, run_row

    # --- first execution: parks at the first approval -----------------------
    completed, report = t091.exec_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert_report(report, "waiting_approval", error_code="approval_required")
    first = assert_waiting_for(t033_stack, run["id"], "approve1", resume_jobs=0)
    proposal = t087.proposal_document(t033_stack, first)
    assert proposal["proposal"]["subject"] == "order-42", proposal
    assert t091.approval_for_node(t033_stack.dbname, run["id"], "approve2") is None
    # Nothing beyond the waiting approval published a node data output.
    rows = t091.node_result_rows(t033_stack.dbname, run["id"])
    assert [row for row in rows if row["node_id"] in {"join1", "join2", "approve1"}] == []

    t091.decide_node(owner, t033_stack.dbname, run["id"], "approve1")
    assert len(t091.resume_jobs(t033_stack.dbname, run["id"])) == 1

    # --- second execution (new process): nested join, then second approval --
    completed, report = t091.exec_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert_report(report, "waiting_approval", error_code="approval_required")
    second = assert_waiting_for(t033_stack, run["id"], "approve2", resume_jobs=1)
    assert t091.approval_for_node(t033_stack.dbname, run["id"], "approve1")["state"] == (
        "approved"
    )

    # The fork/py/agent/join2/join1 part of the graph ran exactly once before the
    # second wait: one data row per node, no duplicate join firing.
    items = t042.node_results(owner.creator, run["id"])
    assert items.status == 200, items.text
    rows = items.json["items"]
    for node_id in ("start", "choose", "approve1", "fork", "py", "agent", "join2", "join1"):
        assert len(data_rows(rows, node_id)) <= 1, (node_id, rows)
    for node_id in ("join2", "join1"):
        assert len(data_rows(rows, node_id)) == 1, (node_id, rows)
    assert data_rows(rows, "direct") == []
    inner = t083.node_data_document(owner.creator, rows, "join2")["results"]
    assert inner["agent"]["status"] == "succeeded", inner
    assert re.fullmatch(r"[0-9a-f]{16}", inner["agent"]["value"]["text"]), inner
    assert inner["py"]["status"] == "succeeded", inner
    assert inner["py"]["value"]["total"] == "250.00", inner
    outer = t083.node_data_document(owner.creator, rows, "join1")["results"]
    assert outer["join2"]["status"] == "succeeded", outer
    assert outer["join2"]["value"]["results"] == inner, outer
    assert outer["direct"] == {"status": "skipped"}, outer
    condition = t083.node_data_document(owner.creator, rows, "choose")
    assert condition == {"port": "yes", "matched_case": 0}, condition

    t091.decide_node(owner, t033_stack.dbname, run["id"], "approve2")
    assert len(t091.resume_jobs(t033_stack.dbname, run["id"])) == 2

    # --- third execution (new process): model + end -------------------------
    completed, report = t091.exec_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert_report(report, "succeeded")
    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state

    # Exactly two approvals, two resumes, no replayed duplicate.
    approvals = t091.approval_rows(t033_stack.dbname, run_id=run["id"])
    assert len(approvals) == 2, approvals
    assert [row["node_key"] for row in approvals] == ["node:approve1", "node:approve2"]
    assert all(row["state"] == "approved" and row["operation_id"] is None for row in approvals)
    assert len(t091.resume_jobs(t033_stack.dbname, run["id"])) == 2

    # Completed nodes were reused on resume: one data row each, joins once.
    rows = t042.node_results(owner.creator, run["id"]).json["items"]
    assert len(data_rows(rows, "start")) <= 1, rows
    for node_id in ("choose", "approve1", "fork", "py", "agent", "join2",
                    "join1", "approve2", "summarise"):
        assert len(data_rows(rows, node_id)) == 1, (node_id, rows)
    for node_id in ("join1", "join2"):
        succeeded = [
            event
            for event in t042.node_events(t033_stack.dbname, run["id"])
            if event["payload"]["node_id"] == node_id
            and event["payload"]["state"] == "succeeded"
        ]
        # Resumed attempts re-publish the reused node's single succeeded fact (one
        # event per attempt), but the join never fires a second time: exactly one
        # data row above and never two succeeds in one attempt.
        attempt_numbers = [event["payload"]["attempt_no"] for event in succeeded]
        assert attempt_numbers == sorted(set(attempt_numbers)), (node_id, succeeded)
        assert len(attempt_numbers) == 2, (node_id, succeeded)

    # The final output reads the outer join envelope by source, the inner join
    # envelope, the second approval decision and the model output.
    final = t091.final_output_document(owner.creator, t033_stack.dbname, run["id"])
    assert set(final) == {"join1", "join2", "second_approval", "summary"}, final
    assert final["join1"] == outer, final
    assert final["join2"] == {"results": inner}, final
    assert final["second_approval"] == second["id"], final
    assert isinstance(final["summary"], str) and len(final["summary"]) == 16, final

    # One capability call per model/agent node across all three attempts; no
    # hidden retry and no duplicate on resume.
    operations = t091.operation_rows(t033_stack.dbname, run["id"])
    assert len(operations) == 2, operations
    assert sorted(operation["logical_key"] for operation in operations) == [
        "agent:agent:model:1", "model:summarise:1",
    ], operations
    attempts = t086.operation_attempts(t033_stack.dbname, run["id"])
    assert len(attempts) == 2, attempts
    assert all(attempt["attempt_no"] == 1 for attempt in attempts), attempts
    # Budget/recovery does not reset the run bounds.
    assert Decimal(t091.run_row(t033_stack.dbname, run["id"])["budget_limit"]) == Decimal("1.00")
    ledger = t091.usage_ledger_rows(t033_stack.dbname, run["id"])
    assert len(ledger) == 6, ledger
    assert len({row["entry_key"] for row in ledger}) == 6, ledger

    # Node state sequences: skipped branch stays skipped, both joins/end succeeded,
    # and the replay emits the same durable sequence over a fresh SSE connection.
    states = t091.node_states(t033_stack.dbname, run["id"])
    assert states["direct"] and set(states["direct"]) == {"skipped"}, states
    for node_id in ("join2", "join1", "approve2", "summarise", "end"):
        assert states[node_id][-1] == "succeeded", (node_id, states)
    assert t091.sse_node_states(t033_stack, owner.creator, run["id"]) == states

    # A fresh node-results read (what a reopened page does) rebuilds the same
    # table facts: condition port, both join envelopes, approval decisions.
    store = t042.node_results(owner.creator, run["id"]).json["items"]
    assert len(data_rows(store, "join1")) == 1
    assert len(data_rows(store, "join2")) == 1
    assert t083.node_data_document(owner.creator, store, "choose")["port"] == "yes"
    for node_id in ("approve1", "approve2"):
        decision = t083.node_data_document(owner.creator, store, node_id)
        assert decision["decision"] == "approved", decision
    assert t033.no_orphan_evidence()["runsc_list"] == []


# ---------------------------------------------------------------------------
# 3. optional failure folded into the join envelope; the run still succeeds
# ---------------------------------------------------------------------------
def test_optional_failure_is_folded_into_the_join_envelope_and_the_run_succeeds(
    t033_stack, sandbox
) -> None:
    definition = t091.two_approval_definition(
        py_optional=True, py_output_schema=t091.FAILING_OUTPUT_SCHEMA
    )
    owner, _application_id, run = t091.submit_acceptance_run(
        t033_stack, definition, name="t091 optional failure"
    )
    _completed, report = t091.exec_once(t033_stack)
    assert_report(report, "waiting_approval", error_code="approval_required")
    assert_waiting_for(t033_stack, run["id"], "approve1", resume_jobs=0)
    t091.decide_node(owner, t033_stack.dbname, run["id"], "approve1")

    _completed, report = t091.exec_once(t033_stack)
    assert_report(report, "waiting_approval", error_code="approval_required")
    assert_waiting_for(t033_stack, run["id"], "approve2", resume_jobs=1)
    t091.decide_node(owner, t033_stack.dbname, run["id"], "approve2")

    _completed, report = t091.exec_once(t033_stack)
    assert_report(report, "succeeded")
    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})

    rows = t042.node_results(owner.creator, run["id"]).json["items"]
    # The failed optional branch published no output of its own ...
    assert data_rows(rows, "py") == []
    # ... but the join recorded the explicit pure-compute error as error data.
    inner = t083.node_data_document(owner.creator, rows, "join2")["results"]
    assert inner["py"] == {"status": "failed", "error_code": "node_output_invalid"}, inner
    assert inner["agent"]["status"] == "succeeded", inner
    assert len(data_rows(rows, "join2")) == 1
    # Downstream sees the failed status as data, never as a successful value.
    final = t091.final_output_document(owner.creator, t033_stack.dbname, run["id"])
    assert final["join2"]["results"]["py"]["status"] == "failed", final
    assert "value" not in final["join2"]["results"]["py"], final
    assert final["join2"]["results"]["agent"]["status"] == "succeeded", final
    assert state["error_code"] is None, state
    assert t033.no_orphan_evidence()["runsc_list"] == []


# ---------------------------------------------------------------------------
# 4. required failure: fail-fast, never joins, end skipped
# ---------------------------------------------------------------------------
def test_required_failure_fails_fast_and_never_joins(t033_stack, sandbox) -> None:
    definition = t091.two_approval_definition(
        py_optional=False, py_output_schema=t091.FAILING_OUTPUT_SCHEMA
    )
    owner, _application_id, run = t091.submit_acceptance_run(
        t033_stack, definition, name="t091 required failure"
    )
    _completed, report = t091.exec_once(t033_stack)
    assert_report(report, "waiting_approval", error_code="approval_required")
    assert_waiting_for(t033_stack, run["id"], "approve1", resume_jobs=0)
    t091.decide_node(owner, t033_stack.dbname, run["id"], "approve1")

    completed, report = t091.exec_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert_report(report, "failed", error_code="node_output_invalid")
    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == "node_output_invalid", state
    assert state["output_artifact_id"] is None, state

    states = t091.node_states(t033_stack.dbname, run["id"])
    assert states["py"] == ["pending", "running", "failed"], states
    for node_id in ("join2", "join1", "approve2", "summarise", "end"):
        assert states[node_id] == ["skipped"], (node_id, states)
    rows = t091.node_result_rows(t033_stack.dbname, run["id"])
    for node_id in ("join2", "join1", "approve2", "summarise", "end"):
        assert [row for row in rows if row["node_id"] == node_id] == [], (node_id, rows)
    # The second approval was never created and no second resume exists; the one
    # consumed resume job (approve1's decision) is still a durable row.
    assert t091.approval_for_node(t033_stack.dbname, run["id"], "approve2") is None
    assert len(t091.approval_rows(t033_stack.dbname, run_id=run["id"])) == 1
    assert len(t091.resume_jobs(t033_stack.dbname, run["id"])) == 1
    assert t033.no_orphan_evidence()["runsc_list"] == []


# ---------------------------------------------------------------------------
# 5. an optional agent capability failure is never downgraded
# ---------------------------------------------------------------------------
def test_an_optional_agent_capability_failure_is_never_downgraded(
    t033_stack, sandbox
) -> None:
    definition = t091.two_approval_definition(agent_optional=True)
    owner, _application_id, run = t091.submit_acceptance_run(
        t033_stack, definition, name="t091 capability failure"
    )
    _completed, report = t091.exec_once(t033_stack)
    assert_report(report, "waiting_approval", error_code="approval_required")
    assert_waiting_for(t033_stack, run["id"], "approve1", resume_jobs=0)

    # Revoke the golden model profile between the approval and the resumed
    # attempt: the agent branch is optional, but a capability/authorization
    # failure is never absorbed into the join envelope.
    t091.revoke_golden_model(t033_stack.dbname)
    t091.decide_node(owner, t033_stack.dbname, run["id"], "approve1")

    completed, report = t091.exec_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert_report(report, "failed", error_code="model_profile_unavailable")
    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == "model_profile_unavailable", state
    assert state["output_artifact_id"] is None, state
    states = t091.node_states(t033_stack.dbname, run["id"])
    assert states["agent"] == ["pending", "running", "failed"], states
    for node_id in ("join2", "join1", "approve2", "summarise", "end"):
        assert states[node_id] == ["skipped"], (node_id, states)
    rows = t091.node_result_rows(t033_stack.dbname, run["id"])
    assert [row for row in rows if row["node_id"] in {"join2", "join1", "approve2"}] == []
    assert t033.no_orphan_evidence()["runsc_list"] == []


# ---------------------------------------------------------------------------
# 6. the run keeps using the frozen snapshot after the draft changes
# ---------------------------------------------------------------------------
def test_the_run_uses_the_frozen_snapshot_not_the_new_draft(t033_stack, sandbox) -> None:
    owner, application_id, run = t091.submit_acceptance_run(
        t033_stack, t091.two_approval_definition(), name="t091 frozen snapshot"
    )
    snapshot_before = t091.run_row(t033_stack.dbname, run["id"])
    frozen = t027.snapshot_row(t033_stack.dbname, snapshot_before["execution_snapshot_id"])
    assert snapshot_before["manifest_sha256"] == frozen["manifest_sha256"]
    draft_revision_before = frozen["draft_revision"]
    manifest_sha256_before = snapshot_before["manifest_sha256"]

    # Save a completely different draft after the run was sealed.
    replacement = t027.definition(
        [t027.start_node(), t027.end_node()],
        [{"source": "start", "target": "end"}],
    )
    revision = t025.get_draft(owner.creator, application_id)
    saved = t025.save_draft(owner.creator, application_id, revision.header("ETag"),
                            definition=replacement, layout={"version": 1, "positions": {}},
                            capability_card=f02a.full_card())
    assert saved.status == 200, saved.text
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json["valid"] is True, validation.text
    assert validation.header("X-Manifest-SHA256") != manifest_sha256_before

    # The run still executes the frozen two-approval definition: first wait, first
    # decision, second wait.
    _completed, report = t091.exec_once(t033_stack)
    assert_report(report, "waiting_approval", error_code="approval_required")
    assert_waiting_for(t033_stack, run["id"], "approve1", resume_jobs=0)
    t091.decide_node(owner, t033_stack.dbname, run["id"], "approve1")
    _completed, report = t091.exec_once(t033_stack)
    assert_report(report, "waiting_approval", error_code="approval_required")
    assert_waiting_for(t033_stack, run["id"], "approve2", resume_jobs=1)

    after = t091.run_row(t033_stack.dbname, run["id"])
    assert after["execution_snapshot_id"] == snapshot_before["execution_snapshot_id"]
    assert after["manifest_sha256"] == manifest_sha256_before
    frozen_after = t027.snapshot_row(t033_stack.dbname, after["execution_snapshot_id"])
    assert frozen_after["draft_revision"] == draft_revision_before
    assert frozen_after["manifest_sha256"] == manifest_sha256_before
    # The approval nodes exist because the frozen definition still has them; the
    # replacement draft (start -> end) never became the execution input.
    assert t091.approval_for_node(t033_stack.dbname, run["id"], "approve2") is not None
