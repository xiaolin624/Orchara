"""T096 real-stack acceptance: interrupted / finalizing / cancelling / waiting Runs.

Every assertion in this file reads the **real** stack: a throwaway PostgreSQL
database migrated by the real ``cmd/admin`` chain, the compiled ``cmd/api`` on a
loopback socket, the real internal mTLS listener (claim / result / exit), real
object bytes, and the real SSE endpoint. The page/chat text is produced by Node
executing the **committed** ``runstream.js``/``runs.js``/``chat.js`` bytes with
the HTTP bodies this stack actually returned (see ``tests/support/t096.py``).

No Playwright / real browser DOM: the page function is evaluated with a minimal
shim. No physical network disconnect: a disconnect is a closed HTTP connection
and the 410 is a real response from an API process whose
``ORCHARA_RUN_EVENT_RETENTION`` was shortened to 50ms.

Coverage map to the brief's "两件别人没做的事":
  * the real 410 ``event_cursor_expired`` + ``details.snapshot_url`` branch is
    exercised (T093 recorded it ``not_run``) and the page logic is fed the real
    envelope, proving it fetches the snapshot and rebuilds instead of just
    printing the URL;
  * the ``finalizing`` window (result proposed, exit evidence not yet sent) is
    constructed with the supervisor's two-step result/exit interface, and the
    page is asserted to show "收尾中" rather than succeeded/failed.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from support import stack as st
from support import t029, t032, t033, t036, t075, t078, t081, t096
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    t033_stack,
)

NODE = shutil.which("node")


def evidence(label: str, payload: dict) -> None:
    """Record one raw observation for the report when T096 evidence is enabled.

    ``T096_EVIDENCE=1`` prints; ``T096_EVIDENCE_FILE=<path>`` appends UTF-8 JSON
    lines directly (a PowerShell pipeline would re-encode the Chinese page text).
    """
    if os.environ.get("T096_EVIDENCE") != "1" and not os.environ.get(
        "T096_EVIDENCE_FILE"
    ):
        return
    line = (
        "T096-EVIDENCE " + label + " " +
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
    target = os.environ.get("T096_EVIDENCE_FILE")
    if target:
        with open(target, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
    else:
        print(line)


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the committed page bytes cannot be executed")
    return NODE


# ---------------------------------------------------------------------------
# small real-HTTP read helpers
# ---------------------------------------------------------------------------
def run_detail(session, run_id: str) -> dict:
    response = session.client.get(t029.run_path(run_id))
    assert response.status == 200, response.text
    return response.json


def wait_run_detail(session, run_id: str, states: set[str], *, timeout: float = 20.0) -> dict:
    last: dict = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        last = run_detail(session, run_id)
        if last.get("state") in states:
            return last
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never reached {states}: {last}")


def children_body(session, parent_id: str) -> dict:
    response = session.client.get(f"/api/v1/runs/{parent_id}/children")
    assert response.status == 200, response.text
    return response.json


def artifacts_body(session, run_id: str) -> dict:
    response = t033.list_run_artifacts(session, run_id)
    assert response.status == 200, response.text
    return response.json


def claimed_lease(stack, owner, *, execution_unit_id: str) -> dict:
    run = t032.queued_run(stack, owner)
    response = t029.execution_client(stack, "supervisor").claim_job(
        worker_id="t096-supervisor", execution_unit_id=execution_unit_id
    )
    assert response.status == 200, response.text
    lease = response.json
    assert lease["run_id"] == run["id"], (lease, run)
    return lease


# ---------------------------------------------------------------------------
# 0. the committed module is actually served and loaded before the page scripts
# ---------------------------------------------------------------------------
def test_the_runstream_module_is_served_and_loaded_before_page_scripts(t033_stack) -> None:
    asset = t033_stack.server.http.get("/assets/runstream.js")
    assert asset.status == 200, asset.text
    assert "javascript" in asset.header("Content-Type"), asset.header("Content-Type")
    committed = (st.WEB_DIR / "assets" / "runstream.js").read_text(encoding="utf-8")
    assert asset.text == committed, "the served bytes differ from the committed bytes"

    marker = '<script src="/assets/runstream.js"></script>'
    pages = {
        "/runs": "runs.js",
        "/chat": "chat.js",
        f"/apps/{t029.new_uuid()}/debug": "debug.js",
    }
    for path, script in pages.items():
        body = t033_stack.server.http.get(path).text
        assert marker in body, f"{path} does not load runstream.js"
        assert body.index(marker) < body.index(f'/assets/{script}"'), path


# ---------------------------------------------------------------------------
# 1. real SSE frames, Last-Event-ID replay and the page's seq dedupe
# ---------------------------------------------------------------------------
def test_real_sse_frames_replay_and_page_dedupe(t033_stack, node_binary, tmp_path) -> None:
    owner = t029.golden_owner(t033_stack)
    lease = claimed_lease(t033_stack, owner, execution_unit_id="unit-t096-sse")
    run_id = lease["run_id"]

    stream = t032.open_sse(t033_stack, owner.creator, run_id)
    try:
        assert stream.status == 200, stream.status
        assert "text/event-stream" in stream.headers.get("Content-Type"), stream.headers
        assert stream.headers.get("Cache-Control") == "no-store", stream.headers
        frames = t032.read_frames(stream, max_frames=2)
    finally:
        stream.close()
    assert [frame["id"] for frame in frames] == ["1", "2"], frames
    assert [frame["event"] for frame in frames] == ["run.status", "run.status"], frames
    first = t032.frame_envelope(frames[0])
    second = t032.frame_envelope(frames[1])
    t032.assert_envelope(first, run_id=run_id, seq=1, event_type="run.status")
    t032.assert_envelope(second, run_id=run_id, seq=2, event_type="run.status")
    assert first["payload"] == {"state": "queued"}, first
    assert second["payload"] == {"state": "running"}, second
    # The contract's run.status payload has no phase (event.schema.json:57-79).
    assert "phase" not in first["payload"] and "phase" not in second["payload"]

    # Reconnect with Last-Event-ID=1: the server replays only seq 2. A replay of
    # an already-seen seq must be a no-op in the page's own dedupe.
    stream = t032.open_sse(t033_stack, owner.creator, run_id, last_event_id=1)
    try:
        replay = t032.read_frames(stream, max_frames=1)
    finally:
        stream.close()
    assert [frame["id"] for frame in replay] == ["2"], replay

    combined = frames + replay + frames  # a duplicated replay, exactly what a reconnect can produce
    deduped = t096.run_node(
        node_binary,
        tmp_path,
        t096.dedupe_harness(st.WEB_DIR, combined),
        name="t096-dedupe.js",
    )
    assert deduped["applied"] == 2, deduped
    assert deduped["seqs"] == [1, 2], deduped
    assert deduped["types"] == ["run.status", "run.status"], deduped
    assert deduped["states"] == ["queued", "running"], deduped
    assert deduped["cursor"] == "2", deduped
    assert deduped["terminal"] == "", deduped


# ---------------------------------------------------------------------------
# 2. the real 410 retention branch -> the page fetches the snapshot and rebuilds
# ---------------------------------------------------------------------------
def test_real_410_drives_the_page_snapshot_rebuild(
    t033_stack, api_server, node_binary, tmp_path
) -> None:
    owner = t029.golden_owner(t033_stack)
    run = t032.queued_run(t033_stack, owner)
    run_id = run["id"]

    # A second API process over the same database with a test-sized retention
    # window: the knob is deployment configuration, never a per-request value.
    expiring = api_server(
        t033_stack.dsn,
        extra_env={
            "ORCHARA_RUN_EVENT_RETENTION": "50ms",
            "ORCHARA_RUN_EVENT_POLL_INTERVAL": "20ms",
            "ORCHARA_RUN_EVENT_HEARTBEAT_INTERVAL": "50ms",
        },
    )
    client = st.HttpClient(expiring.base_url)
    cookie = {"Cookie": f"{st.SESSION_COOKIE}={owner.creator.cookie}"}
    time.sleep(0.2)
    expired = client.get(
        t032.events_path(run_id), headers={**cookie, "Last-Event-ID": "0"}
    )
    assert expired.status == 410, expired.text
    body = expired.json
    assert body["error"]["code"] == "event_cursor_expired", body
    assert body["error"]["details"] == {"snapshot_url": t029.run_path(run_id)}, body

    # The snapshot URL the server named really resolves to the owner's Run.
    snapshot_response = client.get(body["error"]["details"]["snapshot_url"], headers=cookie)
    assert snapshot_response.status == 200, snapshot_response.text
    snapshot = snapshot_response.json
    assert snapshot["id"] == run_id and snapshot["state"] == "queued", snapshot
    evidence(
        "410",
        {
            "retention": "50ms",
            "status": expired.status,
            "body": body,
            "snapshot_url_status": snapshot_response.status,
            "snapshot": {
                key: snapshot[key]
                for key in ("id", "state", "phase", "revision", "output_artifact_id")
            },
        },
    )

    states, phases = t096.contract_run_enums()
    report = t096.run_node(
        node_binary,
        tmp_path,
        t096.module_harness(
            st.WEB_DIR,
            expired_body=body,
            snapshot=snapshot,
            child_items=[],
            state_enum=states,
            phase_enum=phases,
        ),
        name="t096-real-410.js",
    )
    failure = report["failure"]["expired"]
    assert failure["kind"] == "cursor_expired" and failure["retry"] is False, failure
    assert failure["snapshotPath"] == t029.run_path(run_id), failure
    correction = report["correction"]
    # The page really fetched the snapshot path (not just printed the URL).
    assert correction["fetched_path"] == t029.run_path(run_id), correction
    assert correction["applied"] is True, correction
    assert correction["historyLost"] is True, correction
    assert correction["resumeCursor"] == "", correction
    assert correction["run"]["id"] == run_id, correction
    assert correction["run"]["state"] == "queued", correction
    assert "快照" in correction["statusText"], correction


# ---------------------------------------------------------------------------
# 3. finalizing: result proposed, exit evidence not yet sent
# ---------------------------------------------------------------------------
def test_finalizing_window_is_phase_only_and_not_an_outcome(
    t033_stack, node_binary, tmp_path
) -> None:
    owner = t029.golden_owner(t033_stack)
    lease = claimed_lease(t033_stack, owner, execution_unit_id="unit-t096-finalizing")
    run_id = lease["run_id"]
    worker = t029.execution_client(t033_stack, "worker")
    proposed = worker.submit_result(
        run_id,
        attempt_token=lease["attempt_token"],
        suggested_state="failed",
        error_code="tool_crashed",
    )
    assert proposed.status == 200, proposed.text

    # The real snapshot: state=running, phase=finalizing, no committed outcome.
    snapshot = run_detail(owner.creator, run_id)
    assert snapshot["state"] == "running", snapshot
    assert snapshot["phase"] == "finalizing", snapshot
    assert snapshot["output_artifact_id"] is None, snapshot
    assert snapshot["error_code"] is None, snapshot

    # The SSE event that announced the proposal carries state=running and no
    # phase: the page must re-read the Run snapshot to see "finalizing".
    stream = t032.open_sse(t033_stack, owner.creator, run_id, last_event_id=2)
    try:
        frames = t032.read_frames(stream, max_frames=1)
    finally:
        stream.close()
    assert [frame["id"] for frame in frames] == ["3"], frames
    envelope = t032.frame_envelope(frames[0])
    assert envelope["payload"] == {"state": "running"}, envelope
    assert "phase" not in envelope["payload"], envelope

    page = t096.run_node(
        node_binary, tmp_path, t096.truth_harness(st.WEB_DIR, snapshot),
        name="t096-finalizing.js",
    )
    truth = page["truth"]
    assert truth["terminal"] is False, truth
    assert truth["isSuccess"] is False and truth["isFailure"] is False, truth
    assert "finalizing" in truth["label"], truth
    assert "退出证据" in truth["detail"], truth
    assert "finalizing" in page["runs_state_note"], page["runs_state_note"]
    assert page["runs_lifecycle"]["current"] == "finalizing", page["runs_lifecycle"]
    assert "finalizing" in page["chat_handling"]["text"], page["chat_handling"]
    assert "已处理" not in page["chat_handling"]["text"], page["chat_handling"]
    assert page["chat_polls_after_dispatch"] is True, page
    evidence(
        "finalizing",
        {
            "snapshot": {
                key: snapshot[key]
                for key in ("id", "state", "phase", "revision", "output_artifact_id", "error_code")
            },
            "sse_frame": {
                "id": frames[0]["id"],
                "event": frames[0]["event"],
                "payload": envelope["payload"],
                "has_phase": "phase" in envelope["payload"],
            },
            "page": {
                "truth_label": truth["label"],
                "terminal": truth["terminal"],
                "is_success": truth["isSuccess"],
                "is_failure": truth["isFailure"],
                "runs_state_note": page["runs_state_note"],
                "lifecycle_current": page["runs_lifecycle"]["current"],
                "chat_handling": page["chat_handling"]["text"],
            },
        },
    )

    # Exit evidence completes the one finalizer: now it really is a terminal
    # failed state (phase idle), and the page says so.
    exited = t029.execution_client(t033_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-t096-finalizing",
        evidence=t029.exit_evidence(lease),
    )
    assert exited.status == 200, exited.text
    terminal = wait_run_detail(owner.creator, run_id, {"failed"})
    assert terminal["phase"] == "idle", terminal
    final_page = t096.run_node(
        node_binary, tmp_path, t096.truth_harness(st.WEB_DIR, terminal),
        name="t096-finalizing-terminal.js",
    )
    assert final_page["truth"]["terminal"] is True, final_page
    assert final_page["truth"]["isFailure"] is True, final_page
    assert "tool_crashed" in final_page["truth"]["detail"], final_page


# ---------------------------------------------------------------------------
# 4. cancelling: intent recorded, execution unit not stopped yet
# ---------------------------------------------------------------------------
def test_cancelling_copy_never_claims_the_external_write_was_rolled_back(
    t033_stack, node_binary, tmp_path
) -> None:
    owner = t029.golden_owner(t033_stack)
    lease = claimed_lease(t033_stack, owner, execution_unit_id="unit-t096-cancel")
    run_id = lease["run_id"]

    accepted = t036.cancel_run(owner.creator, run_id)
    assert accepted.status == 202, accepted.text
    assert accepted.json["state"] == "cancelling", accepted.json
    # The phase enum has no cancelling member: the phase is kept.
    assert accepted.json["phase"] == "executing", accepted.json

    snapshot = run_detail(owner.creator, run_id)
    assert snapshot["state"] == "cancelling", snapshot
    assert snapshot["phase"] == "executing", snapshot
    assert snapshot["output_artifact_id"] is None, snapshot

    stream = t032.open_sse(t033_stack, owner.creator, run_id, last_event_id=2)
    try:
        frames = t032.read_frames(stream, max_frames=1)
    finally:
        stream.close()
    assert [frame["id"] for frame in frames] == ["3"], frames
    envelope = t032.frame_envelope(frames[0])
    assert envelope["payload"] == {"state": "cancelling"}, envelope
    assert "phase" not in envelope["payload"], envelope

    page = t096.run_node(
        node_binary, tmp_path, t096.truth_harness(st.WEB_DIR, snapshot),
        name="t096-cancelling.js",
    )
    truth = page["truth"]
    assert truth["terminal"] is False and truth["isSuccess"] is False, truth
    assert "正在停止" in truth["label"], truth
    assert "不会撤回" in truth["externalWritesNote"], truth
    assert "已撤销" not in json.dumps(page, ensure_ascii=False), page
    assert "正在停止" in page["runs_state_note"], page["runs_state_note"]
    assert "已撤销" not in page["runs_cancel_panel"]["line"], page["runs_cancel_panel"]
    assert "正在停止" in page["cancel_accepted"], page["cancel_accepted"]
    assert "不会撤回" in page["cancel_accepted"], page["cancel_accepted"]
    assert "正在停止" in page["chat_handling"]["text"], page["chat_handling"]
    assert "不会撤回" in page["chat_handling"]["text"], page["chat_handling"]
    evidence(
        "cancelling",
        {
            "cancel_status": accepted.status,
            "snapshot": {
                key: snapshot[key]
                for key in ("id", "state", "phase", "revision", "output_artifact_id")
            },
            "sse_frame": {
                "id": frames[0]["id"],
                "payload": envelope["payload"],
                "has_phase": "phase" in envelope["payload"],
            },
            "page": {
                "truth_label": truth["label"],
                "terminal": truth["terminal"],
                "external_writes_note": truth["externalWritesNote"],
                "runs_state_note": page["runs_state_note"],
                "runs_cancel_panel": page["runs_cancel_panel"]["line"],
                "cancel_accepted": page["cancel_accepted"],
                "chat_handling": page["chat_handling"]["text"],
            },
        },
    )

    # The supervisor's exit is what lets the finalizer commit cancelled.
    exited = t029.execution_client(t033_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-t096-cancel",
        evidence=t029.exit_evidence(lease, exit_code=137),
    )
    assert exited.status == 200, exited.text
    terminal = wait_run_detail(owner.creator, run_id, {"cancelled"})
    assert terminal["phase"] == "idle", terminal
    final_page = t096.run_node(
        node_binary, tmp_path, t096.truth_harness(st.WEB_DIR, terminal),
        name="t096-cancelled.js",
    )
    assert final_page["truth"]["terminal"] is True
    assert final_page["truth"]["isSuccess"] is False
    assert "已撤回" in final_page["truth"]["externalWritesNote"], final_page["truth"]
    assert "已撤销" not in json.dumps(final_page, ensure_ascii=False), final_page


# ---------------------------------------------------------------------------
# 5. parent waiting_children: N/M, per-child state, and no early success
# ---------------------------------------------------------------------------
def test_parent_waiting_children_is_explainable_and_never_shows_success(
    t033_stack, node_binary, tmp_path
) -> None:
    _admin, creator = t075.creator(t033_stack)
    child_version = t078.create_child_version(t033_stack, creator, version_no=1)
    definition = t078.parent_definition([child_version["id"]])
    application_id = t081.parent_application(t033_stack, creator, definition)
    waiting = t081.waiting_parent(
        t033_stack, creator, application_id, child_version["id"]
    )
    parent_id = waiting["parent"]["id"]
    child_id = waiting["child"]["id"]

    parent = run_detail(creator, parent_id)
    assert parent["state"] == "waiting_children", parent
    assert parent["phase"] == "waiting", parent
    assert parent["output_artifact_id"] is None, parent
    listing = children_body(creator, parent_id)
    assert set(listing) == {"items", "next_cursor", "generated_at"}, listing
    assert listing["next_cursor"] is None, listing
    assert [item["id"] for item in listing["items"]] == [child_id], listing
    assert listing["items"][0]["state"] == "queued", listing

    summary = t096.run_node(
        node_binary,
        tmp_path,
        t096.children_harness(st.WEB_DIR, listing["items"], parent),
        name="t096-children-waiting.js",
    )
    assert summary["total"] == 1 and summary["waiting"] == 1, summary
    assert summary["completed"] == 0, summary
    assert summary["parentMayShowSuccess"] is False, summary
    assert "等待子任务" in summary["line"], summary
    assert "0/1" in summary["line"], summary
    assert summary["rows"][0]["state"] == "queued", summary
    evidence(
        "children-waiting",
        {
            "parent": {
                key: parent[key] for key in ("id", "state", "phase", "output_artifact_id")
            },
            "children": children_body(creator, parent_id),
            "summary": summary,
        },
    )

    # The real child executes through the real supervisor (start -> end, no
    # sandbox). The parent's waiting decision then resolves and the parent is
    # re-queued; the child list refresh must show the committed child outcome
    # while the parent still must not read as success.
    report, _completed = t078.execute_once(t033_stack)
    assert report["status"] == "executed", report
    assert report["outcome"] == "succeeded", report
    assert report["run_id"] == child_id, report
    t081.wait_run_state(t033_stack.dbname, parent_id, {"queued"})

    refreshed = children_body(creator, parent_id)
    assert [item["state"] for item in refreshed["items"]] == ["succeeded"], refreshed
    assert refreshed["items"][0]["output_artifact_id"], refreshed
    parent_after = run_detail(creator, parent_id)
    assert parent_after["state"] == "queued", parent_after
    summary_after = t096.run_node(
        node_binary,
        tmp_path,
        t096.children_harness(st.WEB_DIR, refreshed["items"], parent_after),
        name="t096-children-done.js",
    )
    assert summary_after["completed"] == 1 and summary_after["waiting"] == 0, summary_after
    assert "1/1" in summary_after["line"], summary_after
    # A re-queued parent is not a success: the page must wait for its own
    # committed terminal state.
    assert summary_after["parentMayShowSuccess"] is False, summary_after
    evidence(
        "children-done",
        {
            "child_exec_report": {
                key: report[key] for key in ("status", "outcome", "run_id")
            },
            "parent": {
                key: parent_after[key] for key in ("id", "state", "phase")
            },
            "children": refreshed,
            "summary": summary_after,
        },
    )


# ---------------------------------------------------------------------------
# 6. artifact readiness: a ready artifact is not automatically the final output
# ---------------------------------------------------------------------------
def test_ready_artifact_is_not_the_final_output_before_the_run_terminates(
    t033_stack, node_binary, tmp_path
) -> None:
    owner = t029.golden_owner(t033_stack)
    lease = claimed_lease(t033_stack, owner, execution_unit_id="unit-t096-artifact")
    run_id = lease["run_id"]
    artifact_client = t033.artifact_client(t033_stack)
    artifact_id, _sha = t033.upload_run_artifact(
        t033_stack,
        artifact_client,
        lease,
        content=b'{"result":"ok"}',
        purpose="node_output",
        relation="output",
    )

    before = run_detail(owner.creator, run_id)
    assert before["state"] == "running", before
    assert before["output_artifact_id"] is None, before
    listing_before = artifacts_body(owner.creator, run_id)
    assert [item["id"] for item in listing_before["items"]] == [artifact_id], listing_before
    readout_before = t096.run_node(
        node_binary,
        tmp_path,
        t096.artifact_harness(st.WEB_DIR, listing_before, before),
        name="t096-artifact-unfinished.js",
    )
    item_before = readout_before["items"][0]
    assert item_before["ready"] is True, item_before
    assert item_before["finalOutput"] is False, item_before
    assert "未完成运行产物" in item_before["label"], item_before
    evidence(
        "artifact-unfinished",
        {
            "run": {
                key: before[key]
                for key in ("id", "state", "phase", "output_artifact_id")
            },
            "artifacts": listing_before,
            "readout": item_before,
        },
    )

    # A succeeded result names the artifact; the exit completes the finalizer,
    # which writes output_artifact_id. Only then is it presented as final.
    worker = t029.execution_client(t033_stack, "worker")
    proposed = worker.submit_result(
        run_id,
        attempt_token=lease["attempt_token"],
        suggested_state="succeeded",
        output_artifact_id=artifact_id,
    )
    assert proposed.status == 200, proposed.text
    exited = t029.execution_client(t033_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-t096-artifact",
        evidence=t029.exit_evidence(lease),
    )
    assert exited.status == 200, exited.text
    terminal = wait_run_detail(owner.creator, run_id, {"succeeded"})
    assert terminal["output_artifact_id"] == artifact_id, terminal
    listing_after = artifacts_body(owner.creator, run_id)
    readout_after = t096.run_node(
        node_binary,
        tmp_path,
        t096.artifact_harness(st.WEB_DIR, listing_after, terminal),
        name="t096-artifact-final.js",
    )
    item_after = readout_after["items"][0]
    assert item_after["finalOutput"] is True, item_after
    assert "最终产物" in item_after["label"], item_after
    evidence(
        "artifact-final",
        {
            "run": {
                key: terminal[key]
                for key in ("id", "state", "phase", "output_artifact_id")
            },
            "artifacts": listing_after,
            "readout": item_after,
        },
    )
