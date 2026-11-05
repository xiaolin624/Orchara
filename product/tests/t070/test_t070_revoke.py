"""T070 revokeVersion: an administrator revokes one published version.

Real HTTP (session cookie + CSRF, the compiled API) against real PostgreSQL, real
compiler-sealed manifest bytes and real outbox/audit/run tables. The seeded
published rows are schema-legal snapshots of the T068 approval output; T074 owns
the real published-run creation path.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from support import f00b, t025, t027, t042, t068, t070
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _published_version(stack, *, login: str, version_no: int = 1):
    """One owner application, its real sealed manifest and a seeded version."""
    owner = t070.sales_owner(stack, login=login)
    sealed = t070.sealed_manifest(stack, owner.creator, owner.application_id)
    version = t070.seed_version(
        stack,
        owner_id=owner.creator.principal_id,
        application_id=owner.application_id,
        sealed=sealed,
        version_no=version_no,
    )
    t070.activate_version(stack.dbname, owner.application_id, version["id"])
    return owner, version


def test_revoke_requires_an_admin_session_and_csrf(t070_stack) -> None:
    owner, version = _published_version(t070_stack, login="t070-auth")
    admin = t070.admin_session(t070_stack)
    path = t070.revoke_path(version["id"])

    anonymous = st.HttpClient(t070_stack.server.base_url)
    unauthenticated = anonymous.post(path, body={"reason": "x", "stop_existing": False})
    st.assert_error(unauthenticated, 401, "unauthenticated")

    # A creator session (no admin role) is 403; a missing CSRF token is 403 first.
    creator = t070.revoke_version(owner.creator, version["id"])
    assert creator.status == 403, creator.text
    assert creator.json["error"]["code"] == "forbidden", creator.text
    no_csrf = t070.revoke_version(admin, version["id"], csrf=False)
    assert no_csrf.status == 403, no_csrf.text
    assert no_csrf.json["error"]["code"] == "csrf_invalid", no_csrf.text

    # The body is exactly {reason, stop_existing}; every violation is located.
    cases = {
        "reason": {"stop_existing": False},
        "stop_existing": {"reason": "a real reason"},
    }
    for field, body in cases.items():
        response = t070.revoke_version(admin, version["id"], body=body)
        assert response.status == 422, response.text
        assert response.json["error"]["details"]["field"] == field, response.text
    blank = t070.revoke_version(admin, version["id"], body={"reason": "   ", "stop_existing": False})
    assert blank.status == 422, blank.text
    assert blank.json["error"]["details"]["field"] == "reason", blank.text
    wrong_type = t070.revoke_version(
        admin, version["id"], body={"reason": "x", "stop_existing": "yes"}
    )
    assert wrong_type.status == 422, wrong_type.text
    assert wrong_type.json["error"]["details"]["field"] == "stop_existing", wrong_type.text
    unknown = t070.revoke_version(
        admin, version["id"], body={"reason": "x", "stop_existing": False, "extra": 1}
    )
    assert unknown.status == 422, unknown.text
    assert unknown.json["error"]["details"]["field"] == "extra", unknown.text

    # Nothing above changed the version.
    assert t070.version_row(t070_stack.dbname, version["id"])["revoked_at"] is None

    missing = t070.revoke_version(admin, str(uuid.uuid4()))
    st.assert_error(missing, 404, "resource_not_found")


def test_revoke_returns_the_accepted_body_and_is_idempotent(t070_stack) -> None:
    owner, version = _published_version(t070_stack, login="t070-idem")
    admin = t070.admin_session(t070_stack)
    application_id = owner.application_id
    manifest_sha = version["manifest_sha256"]

    first = t070.revoke_version(
        admin, version["id"], reason="the purpose card does not describe the shipped behaviour",
    )
    assert first.status == 200, first.text
    assert first.json == {"id": version["id"], "state": "revoked"}, first.json

    row = t070.version_row(t070_stack.dbname, version["id"])
    assert row["revoked_at"], row
    assert row["revoked_by"] == admin.principal_id, row
    assert row["revoke_reason"] == "the purpose card does not describe the shipped behaviour", row
    # The immutable columns are untouched.
    assert row["manifest_sha256"] == manifest_sha, row
    assert json.loads(row["manifest"]) == version["manifest"], row
    assert row["manifest_artifact_id"] == version["manifest_artifact_id"], row
    assert row["version_no"] == 1, row

    outbox = t070.outbox_rows(t070_stack.dbname, version["id"])
    assert len(outbox) == 1, outbox
    payload = json.loads(outbox[0]["payload"])
    assert outbox[0]["kind"] == "catalog_index", outbox
    assert payload["op"] == "revoke", payload
    assert payload["application_id"] == application_id, payload
    assert payload["version_id"] == version["id"], payload
    assert payload["version_no"] == 1, payload
    assert payload["manifest_sha256"] == manifest_sha, payload
    assert payload["active_version_id"] is None, payload
    assert payload["previous_active_version_id"] == version["id"], payload
    assert outbox[0]["delivered_at"] is None and outbox[0]["attempts"] == 0, outbox

    audit = t070.audit_rows(t070_stack.dbname, version["id"])
    assert [entry["action"] for entry in audit] == ["version.revoked"], audit
    metadata = json.loads(audit[0]["metadata"])
    assert metadata["previous_active_version_id"] == version["id"], metadata
    assert metadata["stop_existing"] is False, metadata
    assert metadata["application_id"] == application_id, metadata
    assert metadata["manifest_sha256"] == manifest_sha, metadata

    # The revoked version is invisible: the contract's Version has no state field,
    # so the read model omits it and the detail answers 404.
    fetched = owner.creator.client.get(f"/api/v1/versions/{version['id']}")
    assert fetched.status == 404, fetched.text
    assert fetched.json["error"]["code"] == "resource_not_found", fetched.text
    listed = owner.creator.client.get(f"/api/v1/applications/{application_id}/versions")
    assert listed.status == 200, listed.text
    assert listed.json["items"] == [], listed.json

    # A second call is idempotent: same 200 body, no second audit/outbox row, and
    # no rewriting of the recorded reason.
    again = t070.revoke_version(
        admin, version["id"], reason="second reason must not win", stop_existing=True,
    )
    assert again.status == 200, again.text
    assert again.json == {"id": version["id"], "state": "revoked"}, again.json
    assert t070.version_row(t070_stack.dbname, version["id"])["revoke_reason"] == (
        "the purpose card does not describe the shipped behaviour"
    )
    assert len(t070.outbox_rows(t070_stack.dbname, version["id"])) == 1
    assert t070.audit_actions(t070_stack.dbname, version["id"]) == ["version.revoked"]

    # A second call never retroactively cancels runs created afterwards.
    later = t070.seed_published_run(
        t070_stack, owner_id=owner.creator.principal_id, version=version
    )
    third = t070.revoke_version(admin, version["id"], stop_existing=True)
    assert third.status == 200, third.text
    untouched = t070.run_row(t070_stack.dbname, later["id"])
    assert untouched["cancel_requested_at"] is None, untouched
    assert t070.run_events(t070_stack.dbname, later["id"]) == []


def test_revocation_nulls_the_pointer_without_replacing_the_active_version(t070_stack) -> None:
    owner = t070.sales_owner(t070_stack, login="t070-pointer")
    first = t070.sealed_manifest(t070_stack, owner.creator, owner.application_id)
    version_one = t070.seed_version(
        t070_stack, owner_id=owner.creator.principal_id,
        application_id=owner.application_id, sealed=first, version_no=1,
    )
    # A second publication with different executable content is a different hash.
    t070.save_capability_card(owner.creator, owner.application_id, purpose="revoked sales analysis")
    second = t070.sealed_manifest(t070_stack, owner.creator, owner.application_id)
    assert second["manifest_sha256"] != first["manifest_sha256"]
    version_two = t070.seed_version(
        t070_stack, owner_id=owner.creator.principal_id,
        application_id=owner.application_id, sealed=second, version_no=2,
    )
    t070.activate_version(t070_stack.dbname, owner.application_id, version_two["id"])
    admin = t070.admin_session(t070_stack)

    revoked = t070.revoke_version(admin, version_two["id"], reason="content is unsafe")
    assert revoked.status == 200, revoked.text

    application = t070.application_row(t070_stack.dbname, owner.application_id)
    assert application["active_version_id"] is None, application
    assert t070.version_row(t070_stack.dbname, version_one["id"])["revoked_at"] is None
    # The older version is not silently promoted, not revoked, and not the active one.
    read = owner.creator.client.get(f"/api/v1/applications/{owner.application_id}")
    assert read.status == 200, read.text
    assert read.json["active_version_id"] is None, read.text
    listed = owner.creator.client.get(f"/api/v1/applications/{owner.application_id}/versions")
    assert listed.status == 200, listed.text
    assert [item["id"] for item in listed.json["items"]] == [version_one["id"]], listed.text
    assert listed.json["items"][0]["version_no"] == 1, listed.text
    old = owner.creator.client.get(f"/api/v1/versions/{version_one['id']}")
    assert old.status == 200, old.text

    payload = json.loads(t070.outbox_rows(t070_stack.dbname, version_two["id"])[0]["payload"])
    assert payload["previous_active_version_id"] == version_two["id"], payload
    assert payload["active_version_id"] is None, payload
    metadata = json.loads(
        t070.audit_rows(t070_stack.dbname, version_two["id"])[0]["metadata"]
    )
    assert metadata["previous_active_version_id"] == version_two["id"], metadata


def test_stop_existing_records_a_cancel_intent_and_a_version_revoked_event(
    t070_stack, tmp_path: Path
) -> None:
    owner, version = _published_version(t070_stack, login="t070-stop")
    admin = t070.admin_session(t070_stack)
    live = t070.seed_published_run(
        t070_stack, owner_id=owner.creator.principal_id, version=version, state="running"
    )
    done = t070.seed_published_run(
        t070_stack, owner_id=owner.creator.principal_id, version=version, state="succeeded"
    )

    response = t070.revoke_version(
        admin, version["id"], reason="a revoked version must stop running",
        stop_existing=True,
    )
    assert response.status == 200, response.text

    run = t070.run_row(t070_stack.dbname, live["id"])
    assert run["cancel_requested_at"], run
    assert run["error_code"] == "version_revoked", run
    # The revoke transaction never writes a terminal state; the worker/cancel
    # machinery owns the transition.
    assert run["state"] == "running", run
    assert run["finished_at"] is None, run

    events = t070.run_events(t070_stack.dbname, live["id"])
    assert len(events) == 1, events
    assert events[0]["type"] == "run.status", events
    event_payload = json.loads(events[0]["payload"])
    assert event_payload == {"state": "running", "error_code": "version_revoked"}, event_payload

    terminal = t070.run_row(t070_stack.dbname, done["id"])
    assert terminal["cancel_requested_at"] is None, terminal
    assert t070.run_events(t070_stack.dbname, done["id"]) == []

    metadata = json.loads(t070.audit_rows(t070_stack.dbname, version["id"])[0]["metadata"])
    assert metadata["stop_existing"] is True, metadata
    assert metadata["runs_cancel_requested"] == [live["id"]], metadata
    assert metadata["runs_already_cancelling"] == [], metadata


def test_stop_existing_false_only_records_the_revocation(t070_stack, tmp_path: Path) -> None:
    owner, version = _published_version(t070_stack, login="t070-nostop")
    live = t070.seed_published_run(
        t070_stack, owner_id=owner.creator.principal_id, version=version, state="running"
    )
    admin = t070.admin_session(t070_stack)

    response = t070.revoke_version(
        admin, version["id"], reason="record only", stop_existing=False
    )
    assert response.status == 200, response.text
    run = t070.run_row(t070_stack.dbname, live["id"])
    assert run["cancel_requested_at"] is None, run
    assert run["error_code"] is None, run
    assert run["state"] == "running", run
    assert t070.run_events(t070_stack.dbname, live["id"]) == []


def test_a_revoked_version_cannot_be_a_subapp_dependency(t070_stack) -> None:
    owner = t070.sales_owner(t070_stack, login="t070-subapp")
    child_application = t027.application_with_definition(
        owner.creator,
        t042.linear_definition(input_schema=t068.TOOL_INPUT_SCHEMA),
        name="t070 child",
    )
    sealed = t070.sealed_manifest(t070_stack, owner.creator, child_application)
    child_version = t070.seed_version(
        t070_stack, owner_id=owner.creator.principal_id,
        application_id=child_application, sealed=sealed,
    )
    t070.activate_version(t070_stack.dbname, child_application, child_version["id"])

    parent_application = t027.application_with_definition(
        owner.creator, t027.subapp_definition(child_version["id"]), name="t070 parent",
    )
    valid = t025.validate(owner.creator, parent_application)
    assert valid.status == 200, valid.text
    assert valid.json == {"valid": True, "errors": []}, valid.json
    assert valid.header("X-Manifest-SHA256"), "the valid draft must report its manifest hash"

    admin = t070.admin_session(t070_stack)
    revoked = t070.revoke_version(admin, child_version["id"], reason="child is unsafe")
    assert revoked.status == 200, revoked.text

    refused = t025.validate(owner.creator, parent_application)
    # validateApplication answers 200 with the located refusal list (the draft is
    # not executable); the refusal itself is the assertion.
    assert refused.status == 200, refused.text
    errors = refused.json["errors"]
    assert refused.json["valid"] is False, refused.json
    assert any(error["code"] == "dependency_revoked" for error in errors), errors
    assert any(
        error["pointer"] == "/nodes/1/config/application_version" for error in errors
    ), errors
    assert refused.header("X-Manifest-SHA256") is None
