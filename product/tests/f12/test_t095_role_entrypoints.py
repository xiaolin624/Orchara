"""T095 (GitHub #96): the server is the authority behind every hidden entry.

This module is the executable form of the report's
``入口 -> 后端端点 -> 负例主体 -> 实际状态码`` table. It talks to the real API
over a real socket (see ``t095_support.py``); nothing is imported from a handler
and nothing is a stub.

The two directions of AC1 are covered separately:

* the positive direction (the navigation is role-rendered) is a page-behaviour
  test in ``test_t095_page_roles.py`` because it is HTML/JS;
* the negative direction is here: calling the endpoint behind a hidden entry
  still answers 401/403 for a principal that lacks the capability.

AC2 (list/detail/pagination consistency) and AC3 (disable / role update /
grant revocation are visible on the next request) also run against this stack.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

import t095_support as support95
from support import f00b
from t095_support import t095_resources, t095_stack  # noqa: F401 - imported pytest fixtures

ENTRY_ROWS = support95.ENTRY_TABLE
ENTRY_IDS = [row.entry for row in ENTRY_ROWS]


@pytest.mark.parametrize("row", ENTRY_ROWS, ids=ENTRY_IDS)
def test_each_hidden_entry_is_refused_by_the_server(t095_resources, row) -> None:
    """The endpoint behind a hidden UI entry must refuse the wrong principal.

    This is the "隐藏入口不能代替鉴权" direction A: the page may stop rendering
    the link, but the protection is the server check, and it is observed here by
    calling exactly the request the hidden link would have sent.
    """
    response = support95.call_entry(t095_resources, row)
    support95.assert_refused(response, row.status, row.code)


def test_the_entry_table_covers_admin_creator_and_personal_entries() -> None:
    """The table is not a convenient subset; it names all three capability families."""
    families = {
        "admin": [row for row in ENTRY_ROWS if row.entry.startswith("管理台·")],
        "creator": [row for row in ENTRY_ROWS if row.entry.startswith("创作·")],
        "personal": [row for row in ENTRY_ROWS if row.entry.startswith("个人数据·")],
        "anonymous": [row for row in ENTRY_ROWS if row.entry.startswith("未认证·")],
    }
    assert len(families["admin"]) == 8, [row.entry for row in families["admin"]]
    assert len(families["creator"]) == 7, [row.entry for row in families["creator"]]
    assert len(families["personal"]) == 3, [row.entry for row in families["personal"]]
    assert len(families["anonymous"]) == 2, [row.entry for row in families["anonymous"]]
    assert len(ENTRY_ROWS) == 20

    admin_entries = {row.entry for row in families["admin"]}
    for required in (
        "管理台·邀请用户",
        "管理台·创建授权",
        "管理台·修改角色",
        "管理台·创建服务账号",
        "管理台·创建 API key",
        "管理台·禁用主体",
    ):
        assert required in admin_entries, required
    creator_entries = {row.entry for row in families["creator"]}
    for required in (
        "创作·新建应用",
        "创作·保存草稿",
        "创作·校验应用",
        "创作·私有调试",
        "创作·提交发布",
    ):
        assert required in creator_entries, required


def test_a_disabled_principal_loses_access_on_the_next_request(t095_resources) -> None:
    """AC3 server truth: disable revokes every credential on the next request.

    The contract's *stable codes* are what the UI branches on, and they differ by
    credential on purpose (verified here, not assumed):

    * a browser session is revoked in the same transaction, so the resolver
      reports ``401 session_revoked`` before it can report the principal state;
    * the disabled principal's API key is revoked in the same transaction, so the
      next key request is ``401 invalid_key`` (key lookup runs before the
      principal-state check -- the ``403 principal_disabled`` branch is therefore
      defensive for the normal disable flow, and the UI test exercises it with a
      server-shaped 403 to prove the page handles it);
    * a *login attempt* by the disabled account reports ``403 principal_disabled``,
      which is the copy the login page explains.

    Enabling the account clears ``disabled_at`` but does not resurrect the revoked
    session or key.
    """
    resources = t095_resources
    before = resources.revoked.client.get("/api/v1/auth/me")
    assert before.status == 200, before.text

    disabled = resources.admin.client.request(
        "PATCH",
        f"/api/v1/admin/principals/{resources.revoked.principal_id}",
        body={"disabled": True, "reason": "T095 revocation demonstration"},
        csrf=resources.admin.csrf,
    )
    assert disabled.status == 200, disabled.text
    assert disabled.json["state"] == "disabled", disabled.text

    me = resources.revoked.client.get("/api/v1/auth/me")
    support95.assert_refused(me, 401, "session_revoked")
    applications = resources.revoked.client.get("/api/v1/applications")
    support95.assert_refused(applications, 401, "session_revoked")

    login = resources.stack.client().post(
        "/api/v1/auth/login",
        body={"login": "t095.revoked", "password": f00b.USER_PASSWORD},
    )
    support95.assert_refused(login, 403, "principal_disabled")

    # A service principal's key is revoked by the same disable transaction.
    key_client = resources.stack.client()
    key_before = key_client.get(
        "/api/v1/applications",
        headers={"Authorization": "Bearer " + resources.disabled_service_key},
    )
    assert key_before.status == 200, key_before.text
    service_disabled = resources.admin.client.request(
        "PATCH",
        f"/api/v1/admin/principals/{resources.disabled_service_principal_id}",
        body={"disabled": True, "reason": "T095 disabled service demonstration"},
        csrf=resources.admin.csrf,
    )
    assert service_disabled.status == 200, service_disabled.text
    key_after = key_client.get(
        "/api/v1/applications",
        headers={"Authorization": "Bearer " + resources.disabled_service_key},
    )
    support95.assert_refused(key_after, 401, "invalid_key")

    # Enabling clears disabled_at, but the revoked session and key stay revoked.
    enabled = resources.admin.client.request(
        "PATCH",
        f"/api/v1/admin/principals/{resources.revoked.principal_id}",
        body={"disabled": False, "reason": "T095 revocation demonstration"},
        csrf=resources.admin.csrf,
    )
    assert enabled.status == 200, enabled.text
    stale_cookie = resources.revoked.client.get("/api/v1/auth/me")
    assert stale_cookie.status in (401, 403), stale_cookie.text

    service_enabled = resources.admin.client.request(
        "PATCH",
        f"/api/v1/admin/principals/{resources.disabled_service_principal_id}",
        body={"disabled": False, "reason": "T095 disabled service demonstration"},
        csrf=resources.admin.csrf,
    )
    assert service_enabled.status == 200, service_enabled.text
    stale_key = key_client.get(
        "/api/v1/applications",
        headers={"Authorization": "Bearer " + resources.disabled_service_key},
    )
    assert stale_key.status in (401, 403), stale_key.text

    fresh = resources.stack.client().post(
        "/api/v1/auth/login",
        body={"login": "t095.revoked", "password": f00b.USER_PASSWORD},
    )
    assert fresh.status == 200, fresh.text


def test_a_role_update_is_visible_on_the_next_request(t095_resources) -> None:
    """AC3: the server re-reads roles per request, so the UI can re-read identity.

    The same live session is refused, then allowed, then refused again as the
    admin replaces its roles -- no re-login and no server-side role cache.
    """
    resources = t095_resources
    session = resources.promoted

    me = session.client.get("/api/v1/auth/me")
    assert me.status == 200, me.text
    assert me.json["roles"] == ["user"], me.text

    refused = session.client.post(
        "/api/v1/applications", body={"name": "t095 before promotion"}, csrf=session.csrf
    )
    support95.assert_refused(refused, 403, "creator_role_required")

    promoted = resources.admin.client.request(
        "PUT",
        f"/api/v1/admin/users/{resources.promoted_principal_id}/roles",
        body={"roles": ["user", "creator"]},
        csrf=resources.admin.csrf,
    )
    assert promoted.status == 200, promoted.text
    assert promoted.json["state"] == "updated", promoted.text

    me = session.client.get("/api/v1/auth/me")
    assert me.status == 200, me.text
    assert "creator" in me.json["roles"], me.text

    created = session.client.post(
        "/api/v1/applications", body={"name": "t095 after promotion"}, csrf=session.csrf
    )
    assert created.status == 201, created.text

    demoted = resources.admin.client.request(
        "PUT",
        f"/api/v1/admin/users/{resources.promoted_principal_id}/roles",
        body={"roles": ["user"]},
        csrf=resources.admin.csrf,
    )
    assert demoted.status == 200, demoted.text

    me = session.client.get("/api/v1/auth/me")
    assert me.status == 200, me.text
    assert me.json["roles"] == ["user"], me.text

    refused = session.client.post(
        "/api/v1/applications", body={"name": "t095 after demotion"}, csrf=session.csrf
    )
    support95.assert_refused(refused, 403, "creator_role_required")


def test_revoking_a_read_grant_hides_the_resource_on_the_next_read(t095_resources) -> None:
    """AC3: after a grant is revoked, the next read is 404 and the item disappears."""
    resources = t095_resources
    path = f"/api/v1/applications/{resources.app_ids[1]}"

    before = resources.granted.client.get(path)
    assert before.status == 200, before.text

    revoked = resources.admin.client.request(
        "DELETE",
        f"/api/v1/admin/grants/{resources.granted_grant_id}",
        csrf=resources.admin.csrf,
    )
    assert revoked.status == 204, revoked.text

    after = resources.granted.client.get(path)
    # The contract codes the "invisible" resource as resource_not_found: missing
    # and unauthorized are deliberately indistinguishable (no id enumeration).
    support95.assert_refused(after, 404, "resource_not_found")

    listing = resources.granted.client.get("/api/v1/applications")
    assert listing.status == 200, listing.text
    ids = [item["id"] for item in listing.json["items"]]
    assert resources.app_ids[1] not in ids, listing.text


def test_the_application_list_paginates_and_the_detail_matches_the_list_item(
    t095_resources,
) -> None:
    """AC2: the list is ``items`` + opaque ``next_cursor``; detail is not a re-used row.

    The two applications owned by ``creator`` make the cursor real: page one and
    page two must be disjoint, and the single-resource read must return the same
    projection as the list item (the contract defines one ``Application`` shape).
    """
    resources = t095_resources
    client = resources.creator.client

    first_page = client.get("/api/v1/applications?limit=1")
    assert first_page.status == 200, first_page.text
    first_body = first_page.json
    assert len(first_body["items"]) == 1, first_body
    assert first_body["next_cursor"], first_body
    item = first_body["items"][0]
    assert set(item) == {"id", "name", "active_version_id"}, item

    second_page = client.get(
        "/api/v1/applications?limit=1&cursor="
        + quote(str(first_body["next_cursor"]), safe="")
    )
    assert second_page.status == 200, second_page.text
    second_body = second_page.json
    assert len(second_body["items"]) == 1, second_body
    assert second_body["items"][0]["id"] != item["id"], second_body
    assert second_body["next_cursor"] is None, second_body

    detail = client.get(f"/api/v1/applications/{item['id']}")
    assert detail.status == 200, detail.text
    assert detail.json == item, (detail.json, item)

    for bad_limit in ("101", "0", "abc"):
        rejected = client.get("/api/v1/applications?limit=" + bad_limit)
        assert rejected.status == 422, (bad_limit, rejected.text)
        error = rejected.json["error"]
        assert error["code"] == "validation_failed", (bad_limit, rejected.text)
        assert error.get("details", {}).get("field") == "limit", (bad_limit, rejected.text)
