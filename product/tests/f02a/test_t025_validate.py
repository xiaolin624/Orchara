"""T025: the validate endpoint — auth, CSRF, located errors and fail-closed 503s.

The stack is real: PostgreSQL migrated by ``cmd/admin``, the compiled API over a
socket and the real filesystem object store. The tests never call the compiler
in-process; every assertion goes through the contract endpoint.
"""

from __future__ import annotations

import secrets

from support import f02a, stack as st, t025
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t025_stack,
)


def owner(t025_ctx, admin=None):
    admin = admin or t025.admin_session(t025_ctx)
    login = "t025-owner-" + secrets.token_hex(4)
    return admin, t025.invite_and_activate(t025_ctx, admin, login), login


def session_on(server, login: str, password: str) -> f02a.Session:
    client = st.HttpClient(server.base_url)
    response = client.post("/api/v1/auth/login", body={"login": login, "password": password})
    assert response.status == 200, response.text
    cookie = client.cookie(st.SESSION_COOKIE)
    assert cookie, "login did not set the session cookie"
    return f02a.Session(client, response.json["csrf_token"], response.json["principal_id"], cookie)


def test_validate_is_owner_only_and_requires_csrf(t025_stack) -> None:
    admin, creator, _login = owner(t025_stack)
    application = t025.create_application_or_fail(creator, "validate access")
    path = f"/api/v1/applications/{application['id']}/validate"

    # No credential at all.
    anonymous = t025_stack.client().post(path)
    st.assert_error(anonymous, 401, "unauthenticated")

    # The administrator is not the owner and holds no grant: exactly 404. The
    # administrator must also be a creator to reach the owner check at all
    # (SECURITY §1), so grant that role through the real endpoint first: this test
    # is about ownership, not about the creator gate.
    granted = f02a.set_roles(admin, admin.principal_id, ["admin", "creator"])
    assert granted.status == 200, granted.text
    st.assert_error(admin.client.post(path, csrf=admin.csrf), 404, "resource_not_found")

    # A read grant is sufficient to read the draft and insufficient to compile it.
    reader = t025.invite_and_activate(
        t025_stack, admin, "t025-reader-" + secrets.token_hex(4), roles=("creator",)
    )
    t025.grant_read(admin, reader.principal_id, application["id"])
    st.assert_error(reader.client.post(path, csrf=reader.csrf), 404, "resource_not_found")

    # A browser session POST without the CSRF proof is refused before any work.
    st.assert_error(
        creator.client.post(path), 403, "csrf_invalid"
    )

    # A scoped API key without application:write is refused before the ACL.
    service_account = f02a.create_service_account(
        admin, "t025-reader-service", creator.principal_id
    )
    _key_id, secret = f02a.create_api_key(admin, service_account, ["application:read"])
    bearer = f02a.bearer_client(t025_stack, secret)
    response = bearer.post(path)
    st.assert_error(response, 403, "insufficient_scope")


def test_validate_reports_located_definition_and_card_errors_without_rows(t025_stack) -> None:
    admin, creator, _login = owner(t025_stack)
    application = t025.create_application_or_fail(creator, "invalid drafts")
    draft = t025.get_draft(creator, application["id"])
    revision = draft.header("ETag")

    # A valid definition plus the honest empty card: not executable, and the
    # error is located at the card, never padded with invented examples.
    saved = t025.save_draft(
        creator,
        application["id"],
        revision,
        definition=t025.p05_case("anchor-minimal-linear"),
        capability_card={},
    )
    assert saved.status == 200, saved.text
    response = t025.validate(creator, application["id"])
    assert response.status == 200, response.text
    errors = t025.validation_errors(response)
    card_errors = [error for error in errors if error["pointer"].startswith("/capability_card")]
    assert card_errors, errors
    assert all(error["code"] == "capability_card_incomplete" for error in card_errors), card_errors
    assert response.header("X-Manifest-SHA256") is None

    # A DSL cycle with the full card: the P05 code/pointer are preserved.
    revision = t025.get_draft(creator, application["id"]).header("ETag")
    saved = t025.save_draft(
        creator,
        application["id"],
        revision,
        definition=t025.p05_case("cycle"),
        capability_card=t025.full_card(),
    )
    assert saved.status == 200, saved.text
    response = t025.validate(creator, application["id"])
    errors = t025.validation_errors(response)
    assert any(error["code"] == "cycle" for error in errors), errors

    # A bad node config is located at that node's config and names the node.
    revision = t025.get_draft(creator, application["id"]).header("ETag")
    saved = t025.save_draft(
        creator,
        application["id"],
        revision,
        definition=t025.p05_case("bad-config-missing-field"),
        capability_card=t025.full_card(),
    )
    assert saved.status == 200, saved.text
    response = t025.validate(creator, application["id"])
    errors = t025.validation_errors(response)
    located = [error for error in errors if error["code"] == "bad_config"]
    assert located, errors
    t025.assert_location(located[0], code="bad_config", pointer="/nodes/1/config", node_id="stats")

    # No execution identity and no manifest bytes were created by any failure.
    assert t025.count_where_application(t025_stack.dbname, "application_versions", application["id"]) == 0
    assert t025.count_where_application(t025_stack.dbname, "publication_reviews", application["id"]) == 0
    assert t025.count(t025_stack.dbname, "manifest_artifacts") == 0
    assert t025.count(t025_stack.dbname, "background_jobs") == 0


def test_validate_fails_closed_when_the_compiler_is_unconfigured(
    t025_stack, api_server
) -> None:
    admin, creator, login = owner(t025_stack)
    application_id = t025.seed_golden_draft(t025_stack.dbname, creator.principal_id)
    path = f"/api/v1/applications/{application_id}/validate"

    # No worker image digest: 503 compiler_unconfigured, no manifest row.
    no_digest = api_server(t025_stack.dsn, extra_env=t025.api_extra_env(t025_stack.object_dir, worker_image_digest=None))
    no_digest_session = session_on(no_digest, login, f02a.USER_PASSWORD)
    response = t025.validate(no_digest_session, application_id)
    error = st.assert_error(response, 503, "compiler_unconfigured")
    assert "ORCHARA_WORKER_IMAGE_DIGEST" in error["message"], error
    assert response.header("X-Manifest-SHA256") is None
    assert t025.count(t025_stack.dbname, "manifest_artifacts") == 0

    # A missing runtime lock file is also a deployment input, not a draft error.
    missing_lock = api_server(
        t025_stack.dsn,
        extra_env=t025.api_extra_env(
            t025_stack.object_dir,
            runtime_lock_path=t025_stack.object_dir.parent / "missing-runtime.lock",
        ),
    )
    missing_lock_session = session_on(missing_lock, login, f02a.USER_PASSWORD)
    st.assert_error(
        t025.validate(missing_lock_session, application_id), 503, "compiler_unconfigured"
    )

    # A valid draft with no object store cannot seal its bytes: fail closed.
    no_store = api_server(t025_stack.dsn, extra_env=t025.api_extra_env(None))
    no_store_session = session_on(no_store, login, f02a.USER_PASSWORD)
    st.assert_error(
        t025.validate(no_store_session, application_id), 503, "object_store_unavailable"
    )

    # Draft errors are reported even when the compiler is unconfigured: a bad
    # draft does not need a worker image to be told what is wrong.
    invalid = t025.seed_application(
        t025_stack.dbname,
        creator.principal_id,
        definition=t025.p05_case("anchor-minimal-linear"),
        capability_card={},
    )
    response = t025.validate(no_digest_session, invalid)
    assert response.status == 200, response.text
    t025.validation_errors(response)
