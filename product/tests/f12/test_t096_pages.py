"""T096 page-level acceptance: the committed run-state truth module and pages.

手段声明（硬性）: 本文件**不是 Playwright / 真实浏览器 DOM**。它用 Node 执行
``apps/web/src/assets/`` 里**已提交的真实字节**（``runstream.js``、``runs.js``、
``chat.js``），DOM/fetch 是最小手写 shim（先例见 ``tests/f08/test_postreview_pages.py``）。
它证明的是“真实脚本字节 + 真实请求形状 + 渲染文本”，不证明浏览器布局、媒体查询、
真实 EventSource 行为或物理网卡断线。

覆盖：
  * ``state``/``phase`` 的完整映射（枚举读自只读契约，不手写）；
  * ``cancelling`` 文案不得暗示“已撤销外部写”，``finalizing`` 不得显示成功/失败；
  * 410 ``event_cursor_expired``：页面真的按 ``details.snapshot_url`` 拉快照并重建，
    且**不静默续传**；
  * 父子运行等待、部分产物不伪装最终成功；
  * ``/chat`` 在 ``dispatched`` 后继续观察 Run（finalizing/cancelling/子等待）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from support import stack as st
from support import t096

WEB = st.WEB_DIR
NODE = shutil.which("node")

EXPIRED_BODY = {
    "error": {
        "code": "event_cursor_expired",
        "message": (
            "the replay cursor is older than the run-event retention window; "
            "fetch the current run snapshot"
        ),
        "request_id": "req-t096-page",
        "details": {"snapshot_url": "/api/v1/runs/11111111-2222-4333-8444-555555555555"},
    }
}

SNAPSHOT = {
    "id": "11111111-2222-4333-8444-555555555555",
    "state": "running",
    "phase": "finalizing",
    "kind": "debug",
    "revision": 4,
    "application_version_id": None,
    "parent_run_id": None,
    "output_artifact_id": None,
    "error_code": None,
    "execution_snapshot_id": "snap-t096",
}


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the page logic cannot be executed")
    return NODE


def _module_report(node_binary: str, tmp_path: Path) -> dict:
    states, phases = t096.contract_run_enums()
    script = t096.module_harness(
        WEB,
        expired_body=EXPIRED_BODY,
        snapshot=SNAPSHOT,
        child_items=[{"id": "child-1", "state": "queued", "output_artifact_id": None}],
        state_enum=states,
        phase_enum=phases,
    )
    return t096.run_node(node_binary, tmp_path, script, name="t096-runstream.js")


def test_every_contract_state_and_phase_has_an_explicit_ui_label(
    node_binary: str, tmp_path: Path
) -> None:
    report = _module_report(node_binary, tmp_path)
    states, phases = t096.contract_run_enums()
    assert set(report["state_labels"]) == set(states)
    assert set(report["phase_labels"]) == set(phases)
    for state, label in report["state_labels"].items():
        assert isinstance(label, str) and label.strip(), state
    for phase, label in report["phase_labels"].items():
        assert isinstance(label, str) and label.strip(), phase


def test_finalizing_is_not_a_terminal_success_or_failure(
    node_binary: str, tmp_path: Path
) -> None:
    truth = _module_report(node_binary, tmp_path)["truth"]
    finalizing = truth["finalizing"]
    assert finalizing["terminal"] is False, finalizing
    assert finalizing["isSuccess"] is False, finalizing
    assert finalizing["isFailure"] is False, finalizing
    assert "finalizing" in finalizing["label"], finalizing
    assert "退出证据" in finalizing["detail"], finalizing
    # The state stays running while the phase is finalizing; the page must not
    # claim an outcome before the finalizer commits one.
    assert "已成功" not in finalizing["label"] + finalizing["detail"]
    assert "已失败" not in finalizing["label"] + finalizing["detail"]
    succeeded = truth["succeeded"]
    assert succeeded["terminal"] is True and succeeded["isSuccess"] is True
    failed = truth["failed"]
    assert failed["terminal"] is True and failed["isFailure"] is True
    assert "deadline_exceeded" in failed["detail"], failed
    # Every other non-terminal state is explicitly not an outcome.
    for name in (
        "running",
        "queued",
        "waiting_children",
        "waiting_approval",
        "recovery_pending",
        "needs_reconciliation",
    ):
        assert truth[name]["terminal"] is False, (name, truth[name])
        assert truth[name]["isSuccess"] is False, (name, truth[name])
        assert truth[name]["isFailure"] is False, (name, truth[name])


def test_cancelling_copy_never_claims_external_writes_were_rolled_back(
    node_binary: str, tmp_path: Path
) -> None:
    report = _module_report(node_binary, tmp_path)
    cancelling = report["truth"]["cancelling"]
    assert cancelling["terminal"] is False and cancelling["isSuccess"] is False
    assert "正在停止" in cancelling["label"], cancelling
    text = json.dumps(cancelling, ensure_ascii=False)
    assert "已撤销" not in text, cancelling
    assert "已撤回" not in text, cancelling
    assert "不会撤回" in cancelling["externalWritesNote"], cancelling

    accepted = report["cancel_accepted"]
    assert "已接受取消意图" in accepted["cancelling"], accepted
    assert "不会撤回" in accepted["cancelling"], accepted
    assert "已撤销" not in accepted["cancelling"], accepted
    assert "needs_reconciliation" in accepted["needs_reconciliation"], accepted
    assert "已撤销" not in accepted["needs_reconciliation"], accepted
    # A committed cancelled state is "stopped", never "rolled back".
    assert "不表示撤回" in accepted["cancelled"], accepted
    assert "已撤销" not in accepted["cancelled"], accepted


def test_410_classifier_and_snapshot_rebuild_never_resume_silently(
    node_binary: str, tmp_path: Path
) -> None:
    report = _module_report(node_binary, tmp_path)
    failure = report["failure"]
    expired = failure["expired"]
    assert expired["retry"] is False, expired
    assert expired["kind"] == "cursor_expired", expired
    assert expired["snapshotPath"] == EXPIRED_BODY["error"]["details"]["snapshot_url"], expired
    assert "event_cursor_expired" in expired["text"], expired
    assert failure["expired_path"] == expired["snapshotPath"]
    # A server-supplied absolute URL or a traversal path is never followed.
    for name in ("foreign_url", "traversal"):
        assert "evil.example" not in failure[f"{name}_path"], failure
        assert ".." not in failure[f"{name}_path"], failure
        assert failure[f"{name}_path"] == "/api/v1/runs/fallback-run", failure
    assert failure["unauthorized"]["retry"] is False
    assert failure["unauthorized"]["kind"] == "auth"
    assert failure["gone"]["retry"] is False
    assert failure["gone"]["kind"] == "gone"
    assert failure["transient"]["retry"] is True
    assert failure["transient"]["kind"] == "transient"

    correction = report["correction"]
    assert correction["fetched_path"] == expired["snapshotPath"], correction
    assert correction["applied"] is True, correction
    assert correction["historyLost"] is True, correction
    # No silent continuation: the page rebuilds from the snapshot and drops the
    # expired cursor instead of requesting the lost window again.
    assert correction["resumeCursor"] == "", correction
    assert "快照" in correction["statusText"], correction
    assert "finalizing" in correction["statusText"] or "收尾" in correction["statusText"]
    assert "保留窗口" in correction["timelineNotice"], correction
    assert correction["run"]["id"] == SNAPSHOT["id"]
    assert correction["run"]["phase"] == "finalizing"
    assert correction["truth"]["isSuccess"] is False


def test_parent_child_waiting_and_partial_artifacts_are_explainable(
    node_binary: str, tmp_path: Path
) -> None:
    report = _module_report(node_binary, tmp_path)
    waiting = report["children"]["waiting"]
    assert waiting["total"] == 1 and waiting["waiting"] == 1, waiting
    assert waiting["parentMayShowSuccess"] is False, waiting
    assert "等待子任务" in waiting["line"], waiting
    assert "0/1" in waiting["line"], waiting
    assert waiting["rows"][0]["state"] == "queued", waiting

    all_done = report["children"]["all_done"]
    assert all_done["completed"] == 2, all_done
    # A failed/cancelled child is not a successful parent output.
    assert all_done["parentMayShowSuccess"] is False, all_done
    assert report["children"]["parent_success_but_child_running"][
        "parentMayShowSuccess"
    ] is False
    assert "没有独立子运行" in report["children"]["empty"]["line"]

    unfinished = report["artifacts"]["unfinished"]["items"][0]
    assert unfinished["ready"] is True, unfinished
    assert unfinished["finalOutput"] is False, unfinished
    assert "未完成运行产物" in unfinished["label"], unfinished
    final = report["artifacts"]["final"]
    assert final["items"][0]["finalOutput"] is True, final
    assert "最终产物" in final["items"][0]["label"], final
    assert final["items"][1]["finalOutput"] is False
    assert "中间产物" in final["items"][1]["label"], final
    not_ready = report["artifacts"]["not_ready"]["items"][0]
    assert not_ready["usableAsInput"] is False, not_ready
    assert not_ready["finalOutput"] is False, not_ready
    assert "不能作为任务输入" in not_ready["label"], not_ready


def test_dialogue_polling_and_copy_cover_the_real_run_states(
    node_binary: str, tmp_path: Path
) -> None:
    report = _module_report(node_binary, tmp_path)
    dialogue = report["dialogue"]
    assert "finalizing" in dialogue["finalizing"], dialogue
    assert "已处理" not in dialogue["finalizing"], dialogue
    assert "正在停止" in dialogue["cancelling"], dialogue
    assert "不会撤回" in dialogue["cancelling"], dialogue
    assert "已撤销" not in dialogue["cancelling"], dialogue
    assert "子任务" in dialogue["waiting_children"], dialogue
    assert "人工核对" in dialogue["needs_reconciliation"], dialogue
    assert "失败" not in dialogue["needs_reconciliation"], dialogue
    assert "已处理" in dialogue["succeeded"], dialogue
    assert "已取消" in dialogue["cancelled"], dialogue
    assert "正在处理" in dialogue["queued"], dialogue
    assert dialogue["none"] == "尚无处理应用"

    poll = report["poll"]
    assert poll["dispatched_running"] is True, poll
    assert poll["dispatched_finalizing"] is True, poll
    assert poll["dispatched_succeeded"] is False, poll
    assert poll["dispatched_failed"] is False, poll
    assert poll["dispatched_cancelled"] is False, poll
    assert poll["dispatched_without_run"] is True, poll
    assert poll["queued_first"] is True, poll
    assert poll["queued_at_cap"] is False, poll
    assert poll["request_cancelled"] is False, poll


def test_runs_page_fetches_the_snapshot_and_rebuilds_on_410(
    node_binary: str, tmp_path: Path
) -> None:
    run_id = SNAPSHOT["id"]
    script = t096.runs_page_harness(
        WEB, run_id=run_id, expired_body=EXPIRED_BODY, snapshot=SNAPSHOT
    )
    report = t096.run_node(node_binary, tmp_path, script, name="t096-runs-410.js")

    # The 410 stream is terminal for this timeline: exactly one event request,
    # never a silent reconnect from the expired cursor.
    assert report["events_requests"] == [f"/api/v1/runs/{run_id}/events"], report
    # The page really requests the snapshot before rebuilding the view.
    assert report["api_full_requests"] == [f"/api/v1/runs/{run_id}"], report
    assert report["detail_state"] == "running", report
    assert report["detail_phase"] == "finalizing", report
    assert "finalizing" in report["detail_note"] or "收尾" in report["detail_note"], report
    assert "410" in report["timeline_status"], report
    assert "快照" in report["timeline_status"], report
    assert report["timeline_items"], report
    assert "保留窗口" in report["timeline_items"][0], report
    assert report["timeline_snapshot_flag"] == "true", report


def test_chat_page_keeps_observing_the_run_after_dispatched(
    node_binary: str, tmp_path: Path
) -> None:
    report = t096.run_node(
        node_binary, tmp_path, t096.chat_harness(WEB), name="t096-chat.js"
    )
    assert report["has_should_poll_handling"] is True
    handling = report["handling"]
    assert "finalizing" in handling["finalizing"]["text"], handling
    assert "已处理" not in handling["finalizing"]["text"], handling
    assert "正在停止" in handling["cancelling"]["text"], handling
    assert "不会撤回" in handling["cancelling"]["text"], handling
    assert "子任务" in handling["waiting_children"]["text"], handling
    assert "失败" not in handling["needs_reconciliation"]["text"], handling
    assert "已处理" in handling["succeeded"]["text"], handling
    assert "正在处理" in handling["queued"]["text"], handling
    assert handling["none"]["text"] == "尚无处理应用"
    poll = report["poll"]
    assert poll["dispatched_running"] is True, poll
    assert poll["dispatched_finalizing"] is True, poll
    assert poll["dispatched_succeeded"] is False, poll
    assert poll["dispatched_failed"] is False, poll
    assert poll["queued_first"] is True, poll
    assert poll["queued_at_cap"] is False, poll
    assert poll["request_cancelled"] is False, poll
    # C6.05 (review-2): the page-local Request-only poll rule is deleted; the
    # shared shouldPoll (already asserted above through shouldPollHandling) owns
    # the dispatched observation.
    assert report["has_should_poll_request"] is False


def test_debug_page_long_runs_are_not_declared_stopped() -> None:
    """Source guard for the debug page's 60s budget (behaviour driver is F02A's).

    The debug page's poll loop is a closure inside ``initDebugPage``; F02A's
    harness exercises the shared ``OrcharaRunState.shouldPoll`` rule the loop now
    consumes (C6.05 review-2 removed the page-local poll helper). T096 changes the
    loop's else branch so a non-terminal Run keeps being observed at a 10s cadence
    instead of freezing the page. This guard pins that the budget stayed a cadence:
    the slow interval and its honest sentence exist, and the old "已停止轮询" dead
    end is gone.
    """
    source = (WEB / "assets" / "debug.js").read_text(encoding="utf-8")
    assert "DEBUG_SLOW_POLL_INTERVAL_MS = 10000" in source
    assert "改为每 10 秒继续观察" in source
    assert "已停止轮询（" not in source
    assert "Run 已进入终态 " in source


def test_file_readiness_vocabulary_is_explainable(node_binary: str, tmp_path: Path) -> None:
    """The attachments page names every real artifact state (DB CHECK enum)."""
    states = ["uploading", "scanning", "ready", "quarantined", "deleted"]
    report = t096.run_node(
        node_binary,
        tmp_path,
        t096.attachments_harness(WEB, states),
        name="t096-attachments.js",
    )
    assert report["terminal"]["ready"] is True
    assert report["terminal"]["quarantined"] is True
    assert report["terminal"]["deleted"] is True
    assert report["terminal"]["uploading"] is False
    assert report["terminal"]["scanning"] is False
    assert report["poll"]["scanning"] is True
    assert report["poll"]["deleted"] is False
    assert "上传" in report["note"]["uploading"], report
    assert "扫描" in report["note"]["scanning"], report
    assert "ready 之前不能作为任务输入" in report["note"]["scanning"], report
    assert "不可作为任务输入" in report["note"]["quarantined"], report
    assert "已删除" in report["note"]["deleted"], report
    assert "不能作为任务输入" in report["note"]["deleted"], report
    assert "可以下载" in report["note"]["ready"], report
