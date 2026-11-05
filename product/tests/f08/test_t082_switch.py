"""F08/T082 (#83): a fixed-version switch is a direct dispatch, not fake routing.

The explicit ``application_version_id`` path must create the published Run in the
submit/advance transaction: zero ``route_request`` jobs, zero routing candidates,
zero routing decisions and zero control-model calls, while the request keeps the
same conversation slot, the same ordering and the same caller/permission rules. A
revoked or invisible version is refused by the existing
``catalog.ResolveRunnableVersion`` behaviour and creates no request and no Run.

Real rows are the oracle: every assertion below reads ``platform.requests``,
``platform.runs``, ``platform.background_jobs``, ``platform.request_routing_*`` and
``platform.platform_model_calls`` from the throwaway PostgreSQL 16 database.
"""

from __future__ import annotations

from support import f00b, f02a, f08, t070, t077, t082
from support import stack as st


def test_a_fixed_version_switch_creates_the_run_directly_and_keeps_the_queue(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    version_a = t077.publish_version(
        stack, owner.session, purpose="季度销售汇总", name="T082 应用 A")
    version_b = t077.publish_version(
        stack, owner.session, purpose="合同条款审阅", name="T082 应用 B")
    # A real second version of application B: the handling-version header must name
    # the exact version, not just the application.
    version_b2 = t077.publish_version(
        stack, owner.session, application_id=version_b.application_id,
        version_no=2, purpose="合同条款审阅（第二版）", name="T082 应用 B")
    version_c = t077.publish_version(
        stack, owner.session, purpose="发票明细核对", name="T082 应用 C")

    # --- first switch: nothing queued, the Run is created by the submit tx -----
    response_a = f08.submit_request(
        owner.session, conversation_id, "用应用 A 汇总第三季度销售",
        version_id=version_a.version_id, key=f00b.new_uuid(),
    )
    request_a = f08.request_of(response_a)
    assert request_a["state"] == "dispatched", request_a
    run_a = request_a["run_id"]
    assert run_a is not None
    assert response_a.header("X-Request-Application-Version-Id") == version_a.version_id
    assert response_a.header("X-Request-Application-Version-No") == "1"
    row_a = f08.request_row(stack.dbname, request_a["id"])
    assert row_a["requested_version_id"] == version_a.version_id, row_a
    run_a_row = t082.run_row(stack.dbname, run_a)
    assert run_a_row is not None
    assert run_a_row["application_version_id"] == version_a.version_id, run_a_row
    assert run_a_row["owner_id"] == owner.principal_id, run_a_row
    assert run_a_row["kind"] == "published", run_a_row
    # The whole point of the slice: a fixed version never asks the router anything.
    t082.assert_no_routing(stack.dbname, request_a["id"])
    assert len(f08.run_rows(stack.dbname, conversation_id)) == 1
    t082.assert_single_live_top_run(stack, conversation_id, expected_run_id=run_a)

    # --- second switch while A is in flight: it waits, it does not route -------
    response_b = f08.submit_request(
        owner.session, conversation_id, "换应用 B 审阅合同",
        version_id=version_b2.version_id, key=f00b.new_uuid(),
    )
    request_b = f08.request_of(response_b)
    assert request_b["state"] == "queued", request_b
    assert request_b["run_id"] is None, request_b
    # No handling version is known before the Run exists (UX: 尚未确定处理应用).
    assert response_b.header("X-Request-Application-Version-Id") is None
    row_b = f08.request_row(stack.dbname, request_b["id"])
    assert row_b["requested_version_id"] == version_b2.version_id, row_b
    t082.assert_no_routing(stack.dbname, request_b["id"])
    assert len(f08.run_rows(stack.dbname, conversation_id)) == 1
    t082.assert_single_live_top_run(stack, conversation_id, expected_run_id=run_a)

    # --- third switch behind B: the order is the row order, not whoever polls ---
    request_c = f08.request_of(f08.submit_request(
        owner.session, conversation_id, "再换应用 C 核对发票",
        version_id=version_c.version_id, key=f00b.new_uuid(),
    ))
    assert request_c["state"] == "queued", request_c
    ordered = [row["id"] for row in f08.request_rows(stack.dbname, conversation_id)]
    assert ordered == [request_a["id"], request_b["id"], request_c["id"]], ordered
    assert len(f08.run_rows(stack.dbname, conversation_id)) == 1
    t082.assert_single_live_top_run(stack, conversation_id, expected_run_id=run_a)

    # --- the previous Run terminates: request A stays dispatched, B advances ---
    cancelled = t082.run_cancel(owner.session, run_a)
    assert cancelled.status == 202, cancelled.text
    assert t082.run_row(stack.dbname, run_a)["state"] == "cancelled"
    assert f08.request_row(stack.dbname, request_a["id"])["state"] == "dispatched"
    assert f08.live_top_runs(stack.dbname, conversation_id) == []

    # Reading the queued request is the deterministic queue-advance seam (T074):
    # no timer and no unrelated write is needed.
    read_b = f08.get_request(owner.session, request_b["id"])
    assert read_b.status == 200, read_b.text
    assert read_b.json["state"] == "dispatched", read_b.json
    run_b = read_b.json["run_id"]
    assert run_b is not None
    assert read_b.header("X-Request-Application-Version-Id") == version_b2.version_id
    assert read_b.header("X-Request-Application-Version-No") == "2"
    run_b_row = t082.run_row(stack.dbname, run_b)
    assert run_b_row["application_version_id"] == version_b2.version_id, run_b_row
    assert run_b_row["owner_id"] == owner.principal_id, run_b_row
    t082.assert_no_routing(stack.dbname, request_b["id"])
    t082.assert_single_live_top_run(stack, conversation_id, expected_run_id=run_b)
    # The third switch is still queued: only one top Run may be live.
    row_c = f08.request_row(stack.dbname, request_c["id"])
    assert row_c["state"] == "queued", row_c
    assert row_c["run_id"] is None, row_c
    t082.assert_no_routing(stack.dbname, request_c["id"])
    assert len(f08.run_rows(stack.dbname, conversation_id)) == 2

    # --- the second Run terminates too: C advances to its fixed version --------
    assert t082.run_cancel(owner.session, run_b).status == 202
    assert t082.run_row(stack.dbname, run_b)["state"] == "cancelled"
    read_c = f08.get_request(owner.session, request_c["id"])
    assert read_c.status == 200, read_c.text
    assert read_c.json["state"] == "dispatched", read_c.json
    assert read_c.header("X-Request-Application-Version-Id") == version_c.version_id
    assert read_c.header("X-Request-Application-Version-No") == "1"
    run_c = read_c.json["run_id"]
    assert t082.run_row(stack.dbname, run_c)["application_version_id"] == version_c.version_id
    t082.assert_no_routing(stack.dbname, request_c["id"])
    t082.assert_single_live_top_run(stack, conversation_id, expected_run_id=run_c)
    assert len(f08.run_rows(stack.dbname, conversation_id)) == 3


def test_a_fixed_version_switch_keeps_the_callers_permissions(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    other = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    other_version = t077.publish_version(
        stack, other.session, purpose="供应商准入审核", name="T082 其他人的应用")

    # Invisible version: the same 404 as "does not exist", no request, no Run.
    refused = f08.submit_request(
        owner.session, conversation_id, "偷用别人的版本",
        version_id=other_version.version_id, key=f00b.new_uuid(),
    )
    st.assert_error(refused, 404, t082.ERROR_RESOURCE_NOT_FOUND)
    assert f08.request_rows(stack.dbname, conversation_id) == []
    assert f08.run_rows(stack.dbname, conversation_id) == []

    # A real read grant is the caller's permission: the switch is then allowed and
    # the Run is owned by the caller (not by the version's author).
    admin = f00b.admin_session(stack)
    grant_id = f02a.grant_read(admin, owner.principal_id, other_version.application_id)
    granted = f08.submit_request(
        owner.session, conversation_id, "有授权时用别人的版本",
        version_id=other_version.version_id, key=f00b.new_uuid(),
    )
    request = f08.request_of(granted)
    assert request["state"] == "dispatched", request
    assert granted.header("X-Request-Application-Version-Id") == other_version.version_id
    run = t082.run_row(stack.dbname, request["run_id"])
    assert run["owner_id"] == owner.principal_id, run
    assert run["application_version_id"] == other_version.version_id, run
    t082.assert_no_routing(stack.dbname, request["id"])

    # Revoking the grant closes the door again: the permission is checked at submit,
    # not inherited from the earlier Run.
    f02a.revoke_grant(admin, grant_id)
    after_revoke = f08.submit_request(
        owner.session, conversation_id, "授权已撤销",
        version_id=other_version.version_id, key=f00b.new_uuid(),
    )
    st.assert_error(after_revoke, 404, t082.ERROR_RESOURCE_NOT_FOUND)

    # A revoked version is a located 409 through ResolveRunnableVersion, and again
    # creates no request/Run (the request count stays at one).
    revoked_version = t077.publish_version(
        stack, owner.session, purpose="即将撤销的版本", name="T082 撤销应用")
    revoked = t070.revoke_version(admin, revoked_version.version_id)
    assert revoked.status == 200, revoked.text
    revoked_submit = f08.submit_request(
        owner.session, conversation_id, "用已撤销的版本",
        version_id=revoked_version.version_id, key=f00b.new_uuid(),
    )
    st.assert_error(revoked_submit, 409, t082.ERROR_VERSION_REVOKED)
    assert len(f08.request_rows(stack.dbname, conversation_id)) == 1
    assert len(f08.run_rows(stack.dbname, conversation_id)) == 1
