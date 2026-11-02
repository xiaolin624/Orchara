"""T027: every refusal is located, and nothing is created by a refusal.

The draft may be saved with an invalid DSL or an incomplete capability card, but
it cannot be debugged. Dependencies must be fixed, ready versions the caller may
use (the author-private rule is narrower than shared execution but not weaker
than ownership), and referenced inputs must be the caller's own ready artifacts.
A refused submission leaves no snapshot, run, job, artifact, receipt or version
behind.
"""

from __future__ import annotations

import json
import secrets

from support import stack as st
from support import t025, t027
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t027_stack,
)


def owner(t027_ctx):
    admin = t027.admin_session(t027_ctx)
    creator = t027.invite_and_activate(
        t027_ctx, admin, "t027-refuse-" + secrets.token_hex(4), roles=("creator",)
    )
    return admin, creator


def located_errors(response) -> list[dict]:
    body = response.json
    assert body["error"]["code"] == "validation_failed", body
    errors = body["error"]["details"]["errors"]
    assert isinstance(errors, list) and errors, body
    for error in errors:
        assert set(error) <= {"node_id", "code", "pointer", "message"}, error
        assert set(error) >= {"code", "pointer", "message"}, error
    return errors


def test_a_stale_revision_is_refused_and_never_silently_debugged(t027_stack) -> None:
    admin, creator = owner(t027_stack)
    application_id = t027.seed_golden_draft(t027_stack.dbname, creator.principal_id)

    stale = t027.submit_debug(creator, application_id, draft_revision=99)
    error = st.assert_error(stale, 409, "revision_conflict")
    assert error["details"] == {"requested_revision": 99, "current_revision": 1}, error
    t027.no_execution_rows(t027_stack.dbname, application_id)

    accepted = t027.submit_debug(creator, application_id, draft_revision=1)
    assert accepted.status == 202, accepted.text

    draft = t027.get_draft(creator, application_id)
    saved = t027.save_draft(
        creator,
        application_id,
        draft.header("ETag"),
        definition=t027.p05_case("anchor-minimal-linear"),
        capability_card=t027.full_card(),
    )
    assert saved.status == 200, saved.text
    stale = t027.submit_debug(creator, application_id, draft_revision=1)
    error = st.assert_error(stale, 409, "revision_conflict")
    assert error["details"] == {"requested_revision": 1, "current_revision": 2}, error
    assert t027.execution_rows(t027_stack.dbname, application_id)["snapshots"] == 1


def test_an_invalid_draft_or_empty_card_can_be_saved_but_not_debugged(t027_stack) -> None:
    _admin, creator = owner(t027_stack)
    application = t027.create_application_or_fail(creator, "invalid debug draft")
    application_id = application["id"]

    # A semantically invalid DSL still saves (T023's contract) ...
    draft = t027.get_draft(creator, application_id)
    saved = t027.save_draft(
        creator,
        application_id,
        draft.header("ETag"),
        definition=t027.p05_case("cycle"),
        capability_card=t027.full_card(),
    )
    assert saved.status == 200, saved.text
    assert t027.get_draft(creator, application_id).json["definition"] == t027.p05_case("cycle")

    # ... and the debug endpoint refuses it with the validator's own location.
    response = t027.submit_debug(
        creator, application_id, draft_revision=t027.draft_revision(creator, application_id)
    )
    errors = located_errors(response)
    assert any(error["code"] == "cycle" for error in errors), errors
    t027.no_execution_rows(t027_stack.dbname, application_id)

    # A valid definition with the honest empty capability card is also refused:
    # the empty card is saveable and not executable, never padded.
    draft = t027.get_draft(creator, application_id)
    saved = t027.save_draft(
        creator,
        application_id,
        draft.header("ETag"),
        definition=t027.p05_case("anchor-minimal-linear"),
        capability_card={},
    )
    assert saved.status == 200, saved.text
    response = t027.submit_debug(
        creator, application_id, draft_revision=t027.draft_revision(creator, application_id)
    )
    errors = located_errors(response)
    card_errors = [error for error in errors if error["pointer"].startswith("/capability_card")]
    assert card_errors and all(error["code"] == "capability_card_incomplete" for error in card_errors), errors
    t027.no_execution_rows(t027_stack.dbname, application_id)


def test_unready_and_foreign_dependencies_are_refused(t027_stack) -> None:
    admin, creator = owner(t027_stack)

    pending_model = t027.seed_model_version(
        t027_stack.dbname, creator.principal_id, validation_state="pending"
    )
    application_id = t027.application_with_definition(
        creator, t027.model_definition(pending_model)
    )
    errors = located_errors(
        t027.submit_debug(
            creator, application_id, draft_revision=t027.draft_revision(creator, application_id)
        )
    )
    assert errors[0]["code"] == "dependency_not_ready", errors
    assert errors[0]["pointer"] == "/nodes/1/config/model_profile_version", errors
    assert errors[0]["node_id"] == "analysis", errors
    t027.no_execution_rows(t027_stack.dbname, application_id)

    building_tool = t027.seed_tool_version(
        t027_stack.dbname, creator.principal_id, state="building", image_digest=None, approved=False
    )
    application_id = t027.application_with_definition(creator, t027.tool_definition(building_tool))
    errors = located_errors(
        t027.submit_debug(
            creator, application_id, draft_revision=t027.draft_revision(creator, application_id)
        )
    )
    assert errors[0]["code"] == "dependency_not_ready", errors
    assert errors[0]["pointer"] == "/nodes/1/config/tool_version", errors
    t027.no_execution_rows(t027_stack.dbname, application_id)

    # Another principal's ready-but-unapproved tool is a private-debug ownership
    # error, not a silent inclusion.
    foreign_owner = t027.seed_principal(t027_stack.dbname, "t027-foreign-tool")
    foreign_tool = t027.seed_tool_version(
        t027_stack.dbname, foreign_owner, approved=False, state="ready"
    )
    application_id = t027.application_with_definition(creator, t027.tool_definition(foreign_tool))
    errors = located_errors(
        t027.submit_debug(
            creator, application_id, draft_revision=t027.draft_revision(creator, application_id)
        )
    )
    assert errors[0]["code"] == "dependency_not_visible", errors
    assert "owned by another principal" in errors[0]["message"], errors
    t027.no_execution_rows(t027_stack.dbname, application_id)

    # A sub-application version without a read grant on its application is not
    # compiled; an explicit grant makes the same draft executable.
    foreign_app = t027.seed_application(t027_stack.dbname, foreign_owner, name="t027 foreign app")
    foreign_version = t027.seed_published_version(
        t027_stack.dbname, foreign_owner, foreign_app, version_no=1
    )
    application_id = t027.application_with_definition(
        creator, t027.subapp_definition(foreign_version)
    )
    errors = located_errors(
        t027.submit_debug(
            creator, application_id, draft_revision=t027.draft_revision(creator, application_id)
        )
    )
    assert errors[0]["code"] == "dependency_not_visible", errors
    assert errors[0]["pointer"] == "/nodes/1/config/application_version", errors
    t027.no_execution_rows(t027_stack.dbname, application_id)

    t027.grant_read(admin, creator.principal_id, foreign_app)
    accepted = t027.submit_debug(
        creator, application_id, draft_revision=t027.draft_revision(creator, application_id)
    )
    assert accepted.status == 202, accepted.text
    snapshot = t027.snapshot_row(t027_stack.dbname, accepted.json["execution_snapshot_id"])
    manifest_artifact = t027.artifact_row(t027_stack.dbname, snapshot["manifest_artifact_id"])
    manifest = json.loads(
        t027.object_bytes(t027_stack.object_dir, manifest_artifact["object_key"])
    )
    assert manifest["subapp_versions"] == [foreign_version], manifest


def test_the_author_private_rule_allows_the_owners_own_unapproved_tool(t027_stack) -> None:
    _admin, creator = owner(t027_stack)
    own_tool = t027.seed_tool_version(
        t027_stack.dbname, creator.principal_id, approved=False, state="ready"
    )
    application_id = t027.application_with_definition(creator, t027.tool_definition(own_tool))

    # Shared execution (validate) requires organization approval ...
    validation = t025.validate(creator, application_id)
    assert validation.status == 200, validation.text
    assert validation.json["valid"] is False, validation.json
    assert validation.json["errors"][0]["code"] == "dependency_not_ready", validation.json

    # ... while author-private debug may use the author's own ready tool.
    accepted = t027.submit_debug(
        creator, application_id, draft_revision=t027.draft_revision(creator, application_id)
    )
    assert accepted.status == 202, accepted.text
    snapshot = t027.snapshot_row(t027_stack.dbname, accepted.json["execution_snapshot_id"])
    manifest_artifact = t027.artifact_row(t027_stack.dbname, snapshot["manifest_artifact_id"])
    manifest = json.loads(
        t027.object_bytes(t027_stack.object_dir, manifest_artifact["object_key"])
    )
    assert [entry["id"] for entry in manifest["tool_artifacts"]] == [own_tool], manifest
    assert accepted.header("X-Manifest-SHA256") == snapshot["manifest_sha256"]


def test_referenced_inputs_must_be_the_callers_own_ready_artifacts(t027_stack) -> None:
    _admin, creator = owner(t027_stack)
    application_id = t027.seed_golden_draft(t027_stack.dbname, creator.principal_id)
    foreign_owner = t027.seed_principal(t027_stack.dbname, "t027-foreign-artifact")
    foreign_ready = t027.seed_artifact(t027_stack.dbname, foreign_owner)
    own_uploading = t027.seed_artifact(t027_stack.dbname, creator.principal_id, state="uploading")
    own_deleted = t027.seed_artifact(t027_stack.dbname, creator.principal_id, deleted=True)

    cases = (
        (t027.new_uuid(), "artifact_not_found", "/artifact_ids/0"),
        (foreign_ready, "artifact_not_visible", "/artifact_ids/0"),
        (own_uploading, "artifact_not_ready", "/artifact_ids/0"),
        (own_deleted, "artifact_not_ready", "/artifact_ids/0"),
    )
    for artifact_id, code, pointer in cases:
        response = t027.submit_debug(creator, application_id, artifact_ids=[artifact_id])
        errors = located_errors(response)
        assert errors[0]["code"] == code, (code, errors)
        assert errors[0]["pointer"] == pointer, (pointer, errors)
        t027.no_execution_rows(t027_stack.dbname, application_id)

    # Shape problems name the field, like the draft save path.
    too_many = [t027.new_uuid() for _ in range(6)]
    response = t027.submit_debug(creator, application_id, artifact_ids=too_many)
    st.assert_error(response, 422, "validation_failed")
    assert response.json["error"]["details"]["field"] == "artifact_ids"

    duplicated = t027.seed_artifact(t027_stack.dbname, creator.principal_id)
    response = t027.submit_debug(creator, application_id, artifact_ids=[duplicated, duplicated])
    st.assert_error(response, 422, "validation_failed")
    assert response.json["error"]["details"]["field"] == "artifact_ids"

    response = t027.submit_debug(creator, application_id, artifact_ids=["not-a-uuid"])
    st.assert_error(response, 422, "validation_failed")
    assert response.json["error"]["details"]["field"] == "artifact_ids"
    t027.no_execution_rows(t027_stack.dbname, application_id)


def test_a_budget_outside_the_run_column_is_refused(t027_stack) -> None:
    _admin, creator = owner(t027_stack)
    definition = t027.model_definition(
        t025.GOLDEN_MODEL_ID,
        policy={
            "max_active_seconds": 900,
            "max_child_depth": 3,
            "max_child_calls": 12,
            "budget_limit": "1000000000000",
            "currency": "USD",
        },
    )
    application_id = t027.seed_application(
        t027_stack.dbname,
        creator.principal_id,
        name="budget debug",
        definition=definition,
        capability_card=t027.full_card(),
    )
    t027.seed_model_version(t027_stack.dbname, creator.principal_id, model_id=t025.GOLDEN_MODEL_ID)

    response = t027.submit_debug(creator, application_id)
    errors = located_errors(response)
    assert errors[0]["code"] == "policy_budget_out_of_range", errors
    assert errors[0]["pointer"] == "/policy/budget_limit", errors
    t027.no_execution_rows(t027_stack.dbname, application_id)

    # A definition without the required policy is refused by the validator
    # before the budget rule (defence in depth).
    definition = t027.model_definition(t025.GOLDEN_MODEL_ID)
    definition.pop("policy")
    application_id = t027.seed_application(
        t027_stack.dbname,
        creator.principal_id,
        name="no policy debug",
        definition=definition,
        capability_card=t027.full_card(),
    )
    response = t027.submit_debug(creator, application_id)
    errors = located_errors(response)
    # Removing a required top-level key makes the document shape wrong, which the
    # validator reports before any policy rule (defence in depth).
    assert any(error["code"] == "bad_document" for error in errors), errors
    t027.no_execution_rows(t027_stack.dbname, application_id)


def test_the_endpoint_fails_closed_when_the_deployment_inputs_are_missing(
    t027_stack, api_server
) -> None:
    admin = t027.admin_session(t027_stack)
    login = "t027-config-" + secrets.token_hex(4)
    session = t027.invite_and_activate(t027_stack, admin, login, roles=("creator",))
    application_id = t027.seed_golden_draft(t027_stack.dbname, session.principal_id)

    no_digest = api_server(
        t027_stack.dsn,
        extra_env=t025.api_extra_env(t027_stack.object_dir, worker_image_digest=None),
    )
    no_digest_session = t027.session_on(no_digest, login, t027.USER_PASSWORD)
    response = t027.submit_debug(no_digest_session, application_id)
    error = st.assert_error(response, 503, "compiler_unconfigured")
    assert "ORCHARA_WORKER_IMAGE_DIGEST" in error["message"], error
    t027.no_execution_rows(t027_stack.dbname, application_id)

    # Draft errors are reported even when the compiler is unconfigured: a broken
    # draft does not need a worker image to be told what is wrong.
    invalid_application = t027.seed_application(
        t027_stack.dbname,
        session.principal_id,
        definition=t027.p05_case("cycle"),
        capability_card=t027.full_card(),
    )
    response = t027.submit_debug(no_digest_session, invalid_application)
    errors = located_errors(response)
    assert any(error["code"] == "cycle" for error in errors), errors

    # A valid draft needs its canonical bytes sealed; no object store means 503.
    no_store = api_server(t027_stack.dsn, extra_env=t025.api_extra_env(None))
    no_store_session = t027.session_on(no_store, login, t027.USER_PASSWORD)
    response = t027.submit_debug(no_store_session, application_id)
    st.assert_error(response, 503, "object_store_unavailable")
    t027.no_execution_rows(t027_stack.dbname, application_id)
