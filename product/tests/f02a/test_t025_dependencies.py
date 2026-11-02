"""T025: dependency closure, readiness, ownership and cycle rejection.

The referenced rows are real (SQL-seeded into the real tables/constraints, like
F01 seeded applications); the checks run in Go against those rows. Every failing
case asserts the located pointer and that no manifest/version rows were created.
"""

from __future__ import annotations

import json
import secrets

from support import stack as st, t025
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
    t025_stack,
)

POLICY = {
    "max_active_seconds": 900,
    "max_child_depth": 3,
    "max_child_calls": 12,
    "budget_limit": "1.00",
    "currency": "USD",
}


def base(nodes: list[dict], edges: list[dict]) -> dict:
    return {
        "dsl_version": "1.0",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "nodes": nodes,
        "edges": edges,
        "policy": dict(POLICY),
    }


def start(node_id: str = "start") -> dict:
    return {"id": node_id, "type": "start", "inputs": {}, "output_schema": {"type": "object"}, "config": {}}


def end(node_id: str = "end") -> dict:
    return {"id": node_id, "type": "end", "inputs": {}, "output_schema": {"type": "object"}, "config": {}}


def model_definition(model_id: str) -> dict:
    return base(
        [
            start(),
            {
                "id": "analysis",
                "type": "model",
                "inputs": {"task": {"literal": "hello"}},
                "output_schema": {"type": "object"},
                "config": {
                    "model_profile_version": model_id,
                    "prompt_template": "{{task}}",
                    "response_mode": "text",
                },
            },
            end(),
        ],
        [{"source": "start", "target": "analysis"}, {"source": "analysis", "target": "end"}],
    )


def tool_definition(tool_id: str) -> dict:
    return base(
        [
            start(),
            {
                "id": "stats",
                "type": "python",
                "inputs": {},
                "output_schema": {"type": "object"},
                "config": {"tool_version": tool_id},
            },
            end(),
        ],
        [{"source": "start", "target": "stats"}, {"source": "stats", "target": "end"}],
    )


def subapp_definition(version_id: str) -> dict:
    return base(
        [
            start(),
            {
                "id": "child",
                "type": "call_app",
                "inputs": {},
                "output_schema": {"type": "object"},
                "config": {"application_version": version_id},
            },
            end(),
        ],
        [{"source": "start", "target": "child"}, {"source": "child", "target": "end"}],
    )


def agent_definition(model_id: str, tool_ids: list[str]) -> dict:
    return base(
        [
            start(),
            {
                "id": "analysis",
                "type": "agent",
                "inputs": {},
                "output_schema": {"type": "object"},
                "config": {
                    "model_profile_version": model_id,
                    "instructions": "summarise",
                    "tool_versions": tool_ids,
                    "subapp_versions": [],
                    "max_turns": 3,
                },
            },
            end(),
        ],
        [{"source": "start", "target": "analysis"}, {"source": "analysis", "target": "end"}],
    )


def owner(t025_ctx):
    admin = t025.admin_session(t025_ctx)
    login = "t025-dep-" + secrets.token_hex(4)
    return admin, t025.invite_and_activate(t025_ctx, admin, login)


def submit(session, definition: dict):
    application = t025.create_application_or_fail(session, "dependency case")
    revision = t025.get_draft(session, application["id"]).header("ETag")
    saved = t025.save_draft(
        session,
        application["id"],
        revision,
        definition=definition,
        capability_card=t025.full_card(),
    )
    assert saved.status == 200, saved.text
    return application["id"], t025.validate(session, application["id"])


def clean_failure(stack, application_id: str) -> None:
    assert t025.count_where_application(stack.dbname, "application_versions", application_id) == 0
    assert t025.count_where_application(stack.dbname, "publication_reviews", application_id) == 0
    assert t025.count_where_application(stack.dbname, "manifest_artifacts", application_id) == 0


def test_unknown_or_unready_model_is_refused(t025_stack) -> None:
    _admin, creator = owner(t025_stack)
    application_id, response = submit(creator, model_definition(t025.new_uuid()))
    errors = t025.validation_errors(response)
    t025.assert_location(
        errors[0],
        code="dependency_not_found",
        pointer="/nodes/1/config/model_profile_version",
        node_id="analysis",
    )
    clean_failure(t025_stack, application_id)

    pending = t025.seed_model_version(t025_stack.dbname, creator.principal_id, validation_state="pending")
    application_id, response = submit(creator, model_definition(pending))
    errors = t025.validation_errors(response)
    t025.assert_location(errors[0], code="dependency_not_ready", pointer="/nodes/1/config/model_profile_version")
    clean_failure(t025_stack, application_id)

    revoked = t025.seed_model_version(t025_stack.dbname, creator.principal_id, revoked=True)
    application_id, response = submit(creator, model_definition(revoked))
    errors = t025.validation_errors(response)
    t025.assert_location(errors[0], code="dependency_revoked", pointer="/nodes/1/config/model_profile_version")
    clean_failure(t025_stack, application_id)


def test_tool_readiness_requires_ready_approved_and_image_pinned(t025_stack) -> None:
    _admin, creator = owner(t025_stack)

    building = t025.seed_tool_version(
        t025_stack.dbname, creator.principal_id, state="building", image_digest=None, approved=False
    )
    application_id, response = submit(creator, tool_definition(building))
    errors = t025.validation_errors(response)
    t025.assert_location(
        errors[0], code="dependency_not_ready", pointer="/nodes/1/config/tool_version", node_id="stats"
    )
    clean_failure(t025_stack, application_id)

    unapproved = t025.seed_tool_version(t025_stack.dbname, creator.principal_id, approved=False)
    application_id, response = submit(creator, tool_definition(unapproved))
    errors = t025.validation_errors(response)
    assert errors[0]["code"] == "dependency_not_ready", errors
    assert "approved" in errors[0]["message"], errors[0]
    clean_failure(t025_stack, application_id)

    approved = t025.seed_tool_version(
        t025_stack.dbname, creator.principal_id, approved=True, approved_by=creator.principal_id
    )
    application_id, response = submit(creator, tool_definition(approved))
    assert response.status == 200, response.text
    assert response.json == {"valid": True, "errors": []}, response.json
    rows = t025.manifest_artifacts(t025_stack.dbname, application_id)
    assert len(rows) == 1, rows
    manifest = json.loads(t025.manifest_artifact_bytes(t025_stack.object_dir, rows[0]))
    assert manifest["tool_artifacts"] == [
        {"id": approved, "image_digest": t025.TOOL_IMAGE_DIGEST}
    ]


def test_subapp_readiness_requires_a_live_version_and_a_readable_application(t025_stack) -> None:
    admin, creator = owner(t025_stack)
    foreign_owner = t025.seed_principal(t025_stack.dbname, "t025-foreign-owner")
    foreign_app = t025.seed_application(t025_stack.dbname, foreign_owner, name="foreign app")
    version = t025.seed_published_version(
        t025_stack.dbname, foreign_owner, foreign_app, version_no=1
    )

    application_id, response = submit(creator, subapp_definition(version))
    errors = t025.validation_errors(response)
    t025.assert_location(
        errors[0],
        code="dependency_not_visible",
        pointer="/nodes/1/config/application_version",
        node_id="child",
    )
    clean_failure(t025_stack, application_id)

    # An explicit read grant on the dependency application is enough.
    grant_id = t025.grant_read(admin, creator.principal_id, foreign_app)
    application_id, response = submit(creator, subapp_definition(version))
    assert response.status == 200, response.text
    assert response.json == {"valid": True, "errors": []}, response.json
    rows = t025.manifest_artifacts(t025_stack.dbname, application_id)
    manifest = json.loads(t025.manifest_artifact_bytes(t025_stack.object_dir, rows[0]))
    assert manifest["subapp_versions"] == [version]

    # Revoking the version is a refusal even with the grant still present.
    t025.revoke_grant(admin, grant_id)
    revoked_app = t025.seed_application(t025_stack.dbname, foreign_owner, name="revoked app")
    revoked_version = t025.seed_published_version(
        t025_stack.dbname, foreign_owner, revoked_app, version_no=1, revoked=True
    )
    t025.grant_read(admin, creator.principal_id, revoked_app)
    application_id, response = submit(creator, subapp_definition(revoked_version))
    errors = t025.validation_errors(response)
    t025.assert_location(errors[0], code="dependency_revoked", pointer="/nodes/1/config/application_version")

    # An archived dependency application is not silently compiled either.
    archived_app = t025.seed_application(t025_stack.dbname, foreign_owner, name="archived app")
    archived_version = t025.seed_published_version(
        t025_stack.dbname, foreign_owner, archived_app, version_no=1
    )
    t025.grant_read(admin, creator.principal_id, archived_app)
    st.sql(
        t025_stack.dbname,
        f"UPDATE platform.applications SET archived_at = now() WHERE id = '{archived_app}'::uuid",
    )
    application_id, response = submit(creator, subapp_definition(archived_version))
    errors = t025.validation_errors(response)
    t025.assert_location(errors[0], code="dependency_not_visible", pointer="/nodes/1/config/application_version")


def test_version_dependency_cycles_and_self_calls_are_refused(t025_stack) -> None:
    admin, creator = owner(t025_stack)
    foreign_owner = t025.seed_principal(t025_stack.dbname, "t025-cycle-owner")
    foreign_app = t025.seed_application(t025_stack.dbname, foreign_owner, name="cycle app")
    v1 = t025.seed_published_version(t025_stack.dbname, foreign_owner, foreign_app, version_no=1)
    v2 = t025.seed_published_version(t025_stack.dbname, foreign_owner, foreign_app, version_no=2)
    t025.grant_read(admin, creator.principal_id, foreign_app)
    t025.seed_version_dependency(t025_stack.dbname, v1, v2)
    t025.seed_version_dependency(t025_stack.dbname, v2, v1)

    application_id, response = submit(creator, subapp_definition(v1))
    errors = t025.validation_errors(response)
    t025.assert_location(
        errors[0], code="dependency_cycle", pointer="/nodes/1/config/application_version", node_id="child"
    )
    clean_failure(t025_stack, application_id)

    # A draft that calls a published version of its own application is a cycle.
    self_app = t025.seed_application(t025_stack.dbname, creator.principal_id, name="self app")
    self_version = t025.seed_published_version(
        t025_stack.dbname, creator.principal_id, self_app, version_no=1
    )
    draft = t025.get_draft(creator, self_app)
    saved = t025.save_draft(
        creator,
        self_app,
        draft.header("ETag"),
        definition=subapp_definition(self_version),
        capability_card=t025.full_card(),
    )
    assert saved.status == 200, saved.text
    response = t025.validate(creator, self_app)
    errors = t025.validation_errors(response)
    t025.assert_location(errors[0], code="dependency_cycle", pointer="/nodes/1/config/application_version")

    # A closure that reaches a version of the calling application is also a cycle.
    caller_app = t025.seed_application(t025_stack.dbname, creator.principal_id, name="caller app")
    caller_version = t025.seed_published_version(
        t025_stack.dbname, creator.principal_id, caller_app, version_no=1
    )
    bridge_app = t025.seed_application(t025_stack.dbname, foreign_owner, name="bridge app")
    bridge_version = t025.seed_published_version(
        t025_stack.dbname, foreign_owner, bridge_app, version_no=1
    )
    t025.grant_read(admin, creator.principal_id, bridge_app)
    t025.seed_version_dependency(t025_stack.dbname, bridge_version, caller_version)
    draft = t025.get_draft(creator, caller_app)
    saved = t025.save_draft(
        creator,
        caller_app,
        draft.header("ETag"),
        definition=subapp_definition(bridge_version),
        capability_card=t025.full_card(),
    )
    assert saved.status == 200, saved.text
    response = t025.validate(creator, caller_app)
    errors = t025.validation_errors(response)
    assert errors[0]["code"] == "dependency_cycle", errors


def test_uppercase_dependency_ids_validate_like_their_lowercase_twins(t025_stack) -> None:
    """UUIDs are case-insensitive identifiers, not opaque strings.

    PostgreSQL stores and returns lowercase uuid text, so a definition that names
    the same id in uppercase must resolve to the same row, produce the same
    readiness decision and the same normalized dependency set. The author's
    original text stays in the document (and therefore in the manifest's
    definition); only the identity comparison is normalized.
    """
    admin, creator = owner(t025_stack)
    model = t025.seed_model_version(t025_stack.dbname, creator.principal_id)
    tool = t025.seed_tool_version(
        t025_stack.dbname, creator.principal_id, approved=True, approved_by=creator.principal_id
    )
    foreign_owner = t025.seed_principal(t025_stack.dbname, "t025-case-owner")
    foreign_app = t025.seed_application(t025_stack.dbname, foreign_owner, name="case app")
    version = t025.seed_published_version(
        t025_stack.dbname, foreign_owner, foreign_app, version_no=1
    )
    t025.grant_read(admin, creator.principal_id, foreign_app)

    def manifest_for(definition: dict) -> dict:
        application_id, response = submit(creator, definition)
        assert response.status == 200, response.text
        assert response.json == {"valid": True, "errors": []}, response.json
        rows = t025.manifest_artifacts(t025_stack.dbname, application_id)
        assert len(rows) == 1, rows
        return json.loads(t025.manifest_artifact_bytes(t025_stack.object_dir, rows[0]))

    cases = {
        "model": (
            model_definition(model),
            model_definition(model.upper()),
            "model_profile_versions",
            [model],
        ),
        "tool": (
            tool_definition(tool),
            tool_definition(tool.upper()),
            "tool_artifacts",
            [{"id": tool, "image_digest": t025.TOOL_IMAGE_DIGEST}],
        ),
        "subapp": (
            subapp_definition(version),
            subapp_definition(version.upper()),
            "subapp_versions",
            [version],
        ),
    }
    for kind, (lower, upper, field, expected) in cases.items():
        lower_manifest = manifest_for(lower)
        upper_manifest = manifest_for(upper)
        assert lower_manifest[field] == expected, (kind, lower_manifest[field])
        assert upper_manifest[field] == expected, (kind, upper_manifest[field])
        # Everything except the identity of the application (and the definition,
        # which is the author's own text) is identical: the same readiness facts
        # were resolved and the same normalized closure was compiled.
        comparable_lower = {
            key: value
            for key, value in lower_manifest.items()
            if key not in {"application_id", "definition"}
        }
        comparable_upper = {
            key: value
            for key, value in upper_manifest.items()
            if key not in {"application_id", "definition"}
        }
        assert comparable_upper == comparable_lower, kind

    # An id referenced twice with different case is one dependency, not two.
    duplicated = agent_definition(model, [tool.upper(), tool.lower()])
    manifest = manifest_for(duplicated)
    assert manifest["tool_artifacts"] == [
        {"id": tool, "image_digest": t025.TOOL_IMAGE_DIGEST}
    ], manifest["tool_artifacts"]
    assert manifest["model_profile_versions"] == [model]


def test_manifest_dependency_sets_are_sorted_and_deduplicated(t025_stack) -> None:
    _admin, creator = owner(t025_stack)
    model = t025.seed_model_version(t025_stack.dbname, creator.principal_id)
    first = t025.seed_tool_version(t025_stack.dbname, creator.principal_id, approved=True, approved_by=creator.principal_id)
    second = t025.seed_tool_version(t025_stack.dbname, creator.principal_id, approved=True, approved_by=creator.principal_id)
    ordered = sorted([first, second])

    application_id, response = submit(creator, agent_definition(model, [second, first]))
    assert response.status == 200, response.text
    assert response.json == {"valid": True, "errors": []}, response.json
    rows = t025.manifest_artifacts(t025_stack.dbname, application_id)
    manifest = json.loads(t025.manifest_artifact_bytes(t025_stack.object_dir, rows[0]))
    assert [entry["id"] for entry in manifest["tool_artifacts"]] == ordered
    assert manifest["model_profile_versions"] == [model]
