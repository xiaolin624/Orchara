"""T029 acceptance: the owner-only run list.

The list reuses the T027 Run projection and the shared opaque cursor. Runs have
no grant type, so "owner only" is one rule for the list and the detail read: an
administrator with no runs of its own sees an empty list, never another
principal's work.
"""

from __future__ import annotations

import uuid

from support import f00b, f02a, stack as st
from support import t029
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)

RUN_FIELDS = {
    "id",
    "state",
    "revision",
    "application_version_id",
    "parent_run_id",
    "output_artifact_id",
    "error_code",
    "kind",
    "execution_snapshot_id",
    "phase",
}


def assert_run_items(body: dict) -> None:
    assert set(body) == {"items", "next_cursor"}, body
    assert isinstance(body["items"], list), body
    for item in body["items"]:
        assert set(item) == RUN_FIELDS, item
        assert isinstance(item["id"], str) and uuid.UUID(item["id"]), item
        assert item["kind"] == "debug", item
        assert item["phase"] in {"idle", "executing", "waiting", "finalizing"}, item
        assert item["state"] in {
            "queued", "running", "waiting_approval", "waiting_children", "cancelling",
            "recovery_pending", "needs_reconciliation", "succeeded", "failed", "cancelled",
        }, item
    if body["next_cursor"] is not None:
        assert isinstance(body["next_cursor"], str) and body["next_cursor"], body


def db_order(dbname: str, owner_id: str) -> list[str]:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id FROM platform.runs "
        f"WHERE owner_id = {st.quote_literal(owner_id)}::uuid AND deleted_at IS NULL "
        "ORDER BY created_at DESC, id DESC",
    )
    return [row["id"] for row in rows]


def test_an_owner_sees_only_their_own_runs_in_stable_opaque_order(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    for index in range(3):
        t029.queued_run(t029_stack, owner, input_value={"text": "run-%d" % index})

    other = t029.minimal_owner(t029_stack, login="t029-other-owner")
    other_run = t029.queued_run(t029_stack, other, input_value={"text": "other"})

    expected = db_order(t029_stack.dbname, owner.creator.principal_id)
    assert len(expected) == 3, expected

    body = t029.list_runs(owner.creator).json
    assert_run_items(body)
    assert [item["id"] for item in body["items"]] == expected, body
    assert other_run["id"] not in [item["id"] for item in body["items"]]

    # The cursor is opaque: the next page continues strictly after the last row.
    first = t029.list_runs(owner.creator, limit=2)
    assert first.status == 200, first.text
    assert_run_items(first.json)
    assert len(first.json["items"]) == 2
    cursor = first.json["next_cursor"]
    assert cursor, first.json
    assert expected[0] not in cursor and expected[1] not in cursor, cursor

    second = t029.list_runs(owner.creator, cursor=cursor, limit=2)
    assert second.status == 200, second.text
    assert_run_items(second.json)
    assert [item["id"] for item in second.json["items"]] == [expected[2]], second.json
    assert second.json["next_cursor"] is None, second.json

    # A different principal sees their own run only, never owner's.
    other_body = t029.list_runs(other.creator).json
    assert_run_items(other_body)
    assert [item["id"] for item in other_body["items"]] == [other_run["id"]], other_body

    # The administrator is a normal owner here: no superuser read path.
    admin_body = t029.list_runs(owner.admin).json
    assert_run_items(admin_body)
    assert admin_body["items"] == [], admin_body
    # A second ordinary principal who owns nothing sees an empty list, not
    # somebody else's work.
    stranger = t029.invite_and_activate(
        t029_stack, owner.admin, "t029-stranger", roles=("user",)
    )
    stranger_body = t029.list_runs(stranger).json
    assert_run_items(stranger_body)
    assert stranger_body["items"] == [], stranger_body
    # The detail read keeps the same rule (T027): not visible is 404.
    denied = owner.admin.client.get(t029.run_path(expected[0]))
    assert denied.status == 404, denied.text


def test_limit_and_cursor_validation_follow_the_existing_list_conventions(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    t029.queued_run(t029_stack, owner)

    for limit in ("abc", "0", "-1", "101", "1.5"):
        response = owner.creator.client.get("/api/v1/runs?limit=" + limit)
        assert response.status == 422, (limit, response.text)
        error = response.json["error"]
        assert error["code"] == "validation_failed", (limit, error)
        assert error.get("details", {}).get("field") == "limit", (limit, error)

    missing = owner.creator.client.get("/api/v1/runs")
    assert missing.status == 200, missing.text
    assert_run_items(missing.json)

    bad_cursor = owner.creator.client.get("/api/v1/runs?cursor=not-a-cursor")
    assert bad_cursor.status == 400, bad_cursor.text
    assert bad_cursor.json["error"]["code"] == "invalid_cursor", bad_cursor.text


def test_an_api_key_needs_the_read_scope(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    t029.queued_run(t029_stack, owner)
    service_account = f02a.create_service_account(
        owner.admin, "t029 runs reader", owner.creator.principal_id
    )
    _key_id, write_only = f02a.create_api_key(owner.admin, service_account, ["application:write"])
    _read_key_id, read_secret = f02a.create_api_key(owner.admin, service_account, ["application:read"])

    base = t029_stack.server.base_url
    write_client = st.HttpClient(base)
    response = write_client.get(
        "/api/v1/runs", headers={"Authorization": "Bearer " + write_only}
    )
    assert response.status == 403, response.text
    assert response.json["error"]["code"] == "insufficient_scope", response.text

    read_client = st.HttpClient(base)
    response = read_client.get(
        "/api/v1/runs", headers={"Authorization": "Bearer " + read_secret}
    )
    assert response.status == 200, response.text
    # The service account owns no runs: a scoped key is not a superuser either.
    assert response.json["items"] == [], response.json
