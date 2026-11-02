"""T022 acceptance: GET /api/v1/background-jobs/{job_id} authorization and redaction.

The contract endpoint is owner-or-admin. Everyone else gets the same 404 as an
unknown id, so ids cannot be enumerated. The response is the contract's four
fields only: no owner, target, dedup key or expected facts leak.
"""

from __future__ import annotations

from support import f00b
from support import f02a as f02a_support
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
    t022_stack,
)


def _job_for(stack, owner_principal: str, *, dedup_key: str = "get-job") -> tuple[dict, f00b.SeededArtifact]:
    artifact = f00b.seed_artifact(stack.dbname, stack.object_dir, owner_principal)
    payload = f00b.enqueue_ok(
        stack,
        kind="artifact_finalize",
        target_id=artifact.id,
        owner_id=owner_principal,
        dedup_key=dedup_key + ":" + artifact.id,
        expected_revision=1,
    )
    return payload, artifact


def test_the_owner_other_users_and_an_admin_get_owner_or_404(t022_stack) -> None:
    admin = f00b.admin_session(t022_stack)
    owner = f00b.invite_and_activate(t022_stack, admin, "owner.user")
    other = f00b.invite_and_activate(t022_stack, admin, "other.user")
    payload, artifact = _job_for(t022_stack, owner.principal_id)

    owner_response = owner.client.get(f"/api/v1/background-jobs/{payload['id']}")
    assert owner_response.status == 200, owner_response.text
    body = owner_response.json
    assert set(body) == {"id", "kind", "state", "error_code"}, body
    assert body["id"] == payload["id"]
    assert body["kind"] == "artifact_finalize"
    assert body["state"] == "ready"
    assert body["error_code"] is None
    # Redaction: no owner/target/dedup/expected fact appears anywhere in the body.
    for secret in (
        owner.principal_id,
        artifact.id,
        "dedup_key",
        "owner_id",
        "target_id",
        "expected_sha256",
    ):
        assert secret not in owner_response.text, secret

    other_response = other.client.get(f"/api/v1/background-jobs/{payload['id']}")
    st.assert_error(other_response, 404, "resource_not_found")

    admin_response = admin.client.get(f"/api/v1/background-jobs/{payload['id']}")
    assert admin_response.status == 200, admin_response.text
    assert admin_response.json == body


def test_an_api_key_needs_the_read_scope(t022_stack) -> None:
    """A bearer key must carry the platform read scope, not just authenticate.

    Before this check an application:read key could read any owner-visible job.
    The scope is checked before the owner/admin decision (403 insufficient_scope
    for a key without it) and the ACL still decides the content afterwards (a
    scoped key that owns nothing gets the ordinary 404). A session caller is not
    scope-limited, which every other test in this file exercises.
    """
    admin = f00b.admin_session(t022_stack)
    service_id = f02a_support.create_service_account(
        admin, "t022-job-reader", admin.principal_id
    )
    _write_id, write_secret = f02a_support.create_api_key(
        admin, service_id, ["application:write"]
    )
    write_key = f02a_support.bearer_client(t022_stack, write_secret)

    payload, _artifact = _job_for(t022_stack, service_id, dedup_key="scope-job")
    st.assert_error(
        write_key.get(f"/api/v1/background-jobs/{payload['id']}"),
        403,
        "insufficient_scope",
    )

    _read_id, read_secret = f02a_support.create_api_key(
        admin, service_id, ["application:read"]
    )
    read_key = f02a_support.bearer_client(t022_stack, read_secret)
    response = read_key.get(f"/api/v1/background-jobs/{payload['id']}")
    assert response.status == 200, response.text
    assert set(response.json) == {"id", "kind", "state", "error_code"}

    # A scope is necessary, never sufficient: the same key owns nothing else.
    admin_job, _admin_artifact = _job_for(t022_stack, admin.principal_id, dedup_key="scope-admin-job")
    st.assert_error(
        read_key.get(f"/api/v1/background-jobs/{admin_job['id']}"),
        404,
        "resource_not_found",
    )


def test_an_unknown_or_malformed_id_is_the_same_404(t022_stack) -> None:
    owner = f00b.admin_session(t022_stack)
    st.assert_error(
        owner.client.get(f"/api/v1/background-jobs/{f00b.new_uuid()}"),
        404,
        "resource_not_found",
    )
    st.assert_error(
        owner.client.get("/api/v1/background-jobs/not-a-uuid"), 404, "resource_not_found"
    )


def test_an_unauthenticated_request_is_401(t022_stack) -> None:
    anonymous = t022_stack.client()
    response = anonymous.get(f"/api/v1/background-jobs/{f00b.new_uuid()}")
    st.assert_error(response, 401, "unauthenticated")


def test_a_failed_job_exposes_only_its_stable_error_code(t022_stack) -> None:
    admin = f00b.admin_session(t022_stack)
    owner = f00b.invite_and_activate(t022_stack, admin, "failed.owner")
    payload, _artifact = _job_for(t022_stack, owner.principal_id, dedup_key="failed-job")

    client = t022_stack.internal_client()
    lease = client.claim().json
    result = client.result(
        payload["id"], attempt_token=lease["attempt_token"], state="failed",
        error_code="source_deleted",
    )
    assert result.status == 200, result.text

    response = owner.client.get(f"/api/v1/background-jobs/{payload['id']}")
    assert response.status == 200, response.text
    assert response.json["state"] == "failed"
    assert response.json["error_code"] == "source_deleted"
    assert set(response.json) == {"id", "kind", "state", "error_code"}
