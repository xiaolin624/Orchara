"""Independent cross-principal checks against the real stack.

The per-operation matrix in the gate uses random ids; this file additionally
creates real resources as one principal and asks another principal for them
through real HTTP. That is the "越权主体" boundary: an existing resource must be
indistinguishable from a missing one for a caller who does not own it.
"""

from __future__ import annotations

import uuid

import pytest

from support import stack as st

import f12_api_gate as gate_module


def _json(response) -> dict:
    assert response.status < 500, response.text
    return response.json or {}


def test_a_member_cannot_read_another_creators_resources(f12_gate: gate_module.Gate) -> None:
    creator = f12_gate.creator
    member = f12_gate.member
    assert creator is not None and member is not None
    fixtures = f12_gate.fixtures

    requests = [
        f"/api/v1/applications/{fixtures['application_id']}",
        f"/api/v1/applications/{fixtures['application_id']}/draft",
        f"/api/v1/applications/{fixtures['application_id']}/versions",
        f"/api/v1/runs/{fixtures['run_id']}",
        f"/api/v1/runs/{fixtures['run_id']}/artifacts",
        f"/api/v1/conversations/{fixtures['conversation_id']}",
        f"/api/v1/conversations/{fixtures['conversation_id']}/messages",
        f"/api/v1/memories/{fixtures['memory_id']}",
        f"/api/v1/knowledge-bases/{fixtures['knowledge_base_id']}",
        f"/api/v1/artifacts/{fixtures['artifact_id']}",
        f"/api/v1/artifacts/{fixtures['artifact_id']}/content",
        f"/api/v1/artifacts/{fixtures['artifact_id']}/download",
        f"/api/v1/deletions/{uuid.uuid4()}",
        f"/api/v1/background-jobs/{uuid.uuid4()}",
    ]
    for path in requests:
        response = member.client.get(path)
        assert response.status == 404, f"{path} leaked to another principal: {response.text}"
        body = response.json or {}
        assert body["error"]["code"], response.text
        assert body["error"]["request_id"], response.text


def test_lists_are_acl_filtered_for_another_principal(f12_gate: gate_module.Gate) -> None:
    member = f12_gate.member
    assert member is not None
    fixtures = f12_gate.fixtures
    applications = _json(member.client.get("/api/v1/applications?limit=100"))
    assert fixtures["application_id"] not in {item["id"] for item in applications["items"]}
    runs = _json(member.client.get("/api/v1/runs?limit=100"))
    assert fixtures["run_id"] not in {item["id"] for item in runs["items"]}
    conversations = _json(member.client.get("/api/v1/conversations?limit=100"))
    assert fixtures["conversation_id"] not in {item["id"] for item in conversations["items"]}
    memories = _json(member.client.get("/api/v1/memories?limit=100"))
    assert memories["items"] == []
    knowledge = _json(member.client.get("/api/v1/knowledge-bases?limit=100"))
    assert fixtures["knowledge_base_id"] not in {item["id"] for item in knowledge["items"]}


def test_an_administrator_still_cannot_read_personal_memory(f12_gate: gate_module.Gate) -> None:
    admin = f12_gate.admin
    assert admin is not None
    response = admin.client.get(f"/api/v1/memories/{f12_gate.fixtures['memory_id']}")
    assert response.status == 404, response.text


def test_creator_only_writes_refuse_a_plain_member(f12_gate: gate_module.Gate) -> None:
    member = f12_gate.member
    assert member is not None
    for path, body in (
        ("/api/v1/applications", {"name": "member attempt"}),
        (f"/api/v1/applications/{uuid.uuid4()}/draft", {}),
        (f"/api/v1/applications/{uuid.uuid4()}/validate", None),
    ):
        response = member.client.request(
            "PUT" if path.endswith("/draft") else "POST",
            path,
            body=body,
            csrf=member.csrf,
            headers={"If-Match": '"1"'} if path.endswith("/draft") else None,
        )
        assert response.status == 403, (path, response.status, response.text)
        assert response.json["error"]["code"] == "creator_role_required", response.text


def test_admin_only_operations_refuse_a_creator(f12_gate: gate_module.Gate) -> None:
    creator = f12_gate.creator
    assert creator is not None
    response = creator.client.post(
        "/api/v1/admin/users",
        body={"login": "f12-illegal-" + uuid.uuid4().hex[:6], "display_name": "X", "roles": ["user"]},
        csrf=creator.csrf,
    )
    assert response.status == 403, response.text
    assert response.json["error"]["code"] == "forbidden", response.text
    listing = creator.client.get("/api/v1/admin/credential-refs")
    assert listing.status == 403, listing.text


def test_a_read_scoped_api_key_cannot_write(f12_gate: gate_module.Gate) -> None:
    admin = f12_gate.admin
    creator = f12_gate.creator
    assert admin is not None and creator is not None
    account = admin.client.post(
        "/api/v1/admin/service-accounts",
        body={"display_name": "F12 key probe", "responsible_user_id": admin.principal_id},
        csrf=admin.csrf,
    )
    assert account.status == 201, account.text
    key = admin.client.post(
        "/api/v1/admin/api-keys",
        body={
            "principal_id": account.json["id"],
            "scopes": ["application:read"],
            "expires_at": (gate_module.dt.datetime.now(gate_module.dt.timezone.utc)
                           + gate_module.dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        csrf=admin.csrf,
    )
    assert key.status == 201, key.text
    secret = key.json.get("secret") or key.json.get("token") or key.json.get("api_key")
    assert secret, f"the one-time key secret is missing from {key.text}"
    bearer = st.HttpClient(f12_gate.base_url)
    read = bearer.request(
        "GET", "/api/v1/applications?limit=1", headers={"Authorization": f"Bearer {secret}"}
    )
    assert read.status == 200, read.text
    write = bearer.request(
        "POST",
        "/api/v1/applications",
        body={"name": "read key write attempt"},
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert write.status == 403, write.text
    assert write.json["error"]["code"] == "insufficient_scope", write.text


def test_an_unauthenticated_caller_gets_the_contract_401_for_every_family(
    f12_gate: gate_module.Gate,
) -> None:
    anonymous = gate_module.HttpClient(f12_gate.base_url)
    # Probe each family with the method the contract/route actually registers.
    # `/api/v1/admin/users` is POST-only: probing it with GET would measure the
    # mux 405, not the authentication boundary. The 401 (not 405) below is what
    # proves the unauthenticated caller is refused before any handler runs.
    requests = (
        ("GET", "/api/v1/applications"),
        ("GET", "/api/v1/runs"),
        ("GET", "/api/v1/conversations"),
        ("GET", "/api/v1/memories"),
        ("GET", "/api/v1/knowledge-bases"),
        ("POST", "/api/v1/artifacts/uploads"),
        ("GET", "/api/v1/approvals"),
        ("GET", "/api/v1/tools"),
        ("GET", "/api/v1/models"),
        ("POST", "/api/v1/admin/users"),
    )
    for method, path in requests:
        response = anonymous.request(method, path, body={"name": "x"} if method == "POST" else None)
        assert response.status == 401, (method, path, response.status, response.text)
        assert response.json["error"]["code"] in (
            "unauthenticated",
            "invalid_key",
        ), response.text
