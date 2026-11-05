"""F07/T075: a creator pins a published child version and sees located errors.

Every case drives the real compiled API over real HTTP with a real creator
session and CSRF token, against real PostgreSQL. The child's frozen I/O schema
comes from its published version row, whose manifest bytes are the exact
canonical bytes the compiler sealed through ``POST /validate`` (the publisher's
stored shape). ``tests/support/t075.py`` documents the one step a fixture
performs instead of the publication review transaction, and why: publishing
needs a passed five-category evaluation, and ``internal/presetexec`` refuses any
definition containing ``call_app`` until T078 implements child runs.

What is covered: compatible mapping accepted and frozen; missing/unknown/
type-incompatible inputs and incompatible outputs located at the right JSON
pointer and node; unpublished, revoked and archived child versions refused with
distinguishable codes; a real cycle in ``platform.version_dependencies`` (and a
call to the application's own published version) refused; the depth and count
limits enforced over the real closure; a newer child version never replaces the
pinned reference; the catalog/version reads a picker uses are ACL-filtered.
"""

from __future__ import annotations

import json

from support import t025, t075
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    t025_stack,
)


def child_version(stack, creator, *, version_no: int = 1, definition: dict | None = None) -> dict:
    """Create one child application, seal its draft and store a version row."""
    model = t025.seed_model_version(stack.dbname, creator.principal_id)
    application = t025.create_application_or_fail(creator, "t075 child")
    draft = t075.child_definition(model)
    if definition is not None:
        draft = definition
    saved = t075.save_definition(creator, application["id"], draft)
    assert saved.status == 200, saved.text
    sealed = t075.seal_version(stack, creator, application["id"], version_no=version_no)
    sealed["application"] = application["id"]
    return sealed


def parent_definition(
    version_id: str,
    *,
    inputs: dict | None = None,
    output_schema: dict | None = None,
    max_child_calls: int = 12,
    max_child_depth: int = 3,
    two_calls: bool = False,
) -> dict:
    nodes = [t075.call_app_node(version_id, inputs=inputs, output_schema=output_schema)]
    if two_calls:
        nodes.append(
            t075.call_app_node(
                version_id,
                node_id="child2",
                inputs={"source": {"ref": "input#/source"}},
            )
        )
    return t075.chain_definition(
        nodes,
        input_schema={"type": "object", "properties": {"source": {"type": "string"}},
                      "required": ["source"]},
        policy={
            "budget_limit": "1.00",
            "currency": "USD",
            "max_active_seconds": 300,
            "max_child_calls": max_child_calls,
            "max_child_depth": max_child_depth,
        },
    )


def save_and_validate(stack, creator, application_id: str, definition: dict):
    saved = t075.save_definition(creator, application_id, definition)
    assert saved.status == 200, saved.text
    return t025.validate(creator, application_id)


def test_a_compatible_mapping_is_accepted_and_the_child_version_is_pinned(t025_stack) -> None:
    _admin, creator = t075.creator(t025_stack)
    child = child_version(t025_stack, creator)
    application = t025.create_application_or_fail(creator, "t075 compatible parent")
    definition = parent_definition(
        child["id"],
        inputs={"source": {"ref": "input#/source"}},
        output_schema={
            "type": "object",
            "properties": {"report": {"type": "string"}},
            "required": ["report"],
        },
    )
    response = save_and_validate(t025_stack, creator, application["id"], definition)
    assert response.status == 200, response.text
    assert response.json == {"valid": True, "errors": []}, response.json
    manifest_hash = response.header("X-Manifest-SHA256")
    assert manifest_hash and len(manifest_hash) == 64

    sealed = t075.validated_manifest(t025_stack, creator, application["id"])
    assert sealed["sha256"] == manifest_hash
    assert sealed["manifest"]["subapp_versions"] == [child["id"]], sealed["manifest"]
    # The pinned child is a published version of its own application, and the
    # frozen schema is the child's own (never a "latest" pointer).
    assert sealed["manifest"]["definition"]["nodes"][1]["config"]["application_version"] == child["id"]
    child_row = t075.version_row(t025_stack.dbname, child["id"])
    assert child_row["manifest_sha256"] == child["manifest_sha256"]


def test_mapping_errors_are_located_at_the_offending_field(t025_stack) -> None:
    _admin, creator = t075.creator(t025_stack)
    child = child_version(t025_stack, creator)
    app = t025.create_application_or_fail(creator, "t075 mapping errors")

    # 1. A required child input that the node never maps.
    response = save_and_validate(t025_stack, creator, app["id"],
                                 parent_definition(child["id"], inputs={}))
    error = t075.assert_error(t075.errors_of(response),
                              code="io_mapping_missing_field",
                              pointer="/nodes/1/inputs/source", node_id="child")
    assert "source" in error["message"], error
    # 2. A mapped field the child version does not declare.
    response = save_and_validate(
        t025_stack, creator, app["id"],
        parent_definition(child["id"], inputs={
            "source": {"ref": "input#/source"},
            "region": {"literal": "eu"},
        }),
    )
    t075.assert_error(t075.errors_of(response), code="io_mapping_unknown_field",
                      pointer="/nodes/1/inputs/region", node_id="child")
    # 3. A literal whose JSON type the child schema refuses.
    response = save_and_validate(
        t025_stack, creator, app["id"],
        parent_definition(child["id"], inputs={"source": {"literal": 42}}),
    )
    t075.assert_error(t075.errors_of(response), code="io_mapping_type_mismatch",
                      pointer="/nodes/1/inputs/source", node_id="child")
    # A failing mapping never freezes a manifest or a version.
    assert t025.count_where_application(t025_stack.dbname, "application_versions", app["id"]) == 0
    assert t025.count_where_application(t025_stack.dbname, "manifest_artifacts", app["id"]) == 0


def test_an_incompatible_output_declaration_is_located(t025_stack) -> None:
    _admin, creator = t075.creator(t025_stack)
    child = child_version(t025_stack, creator)
    app = t025.create_application_or_fail(creator, "t075 output mapping")
    response = save_and_validate(
        t025_stack, creator, app["id"],
        parent_definition(child["id"], output_schema={
            "type": "object",
            "properties": {
                "bogus": {"type": "string"},
                "report": {"type": "integer"},
            },
        }),
    )
    errors = t075.errors_of(response)
    t075.assert_error(errors, code="io_output_unknown_field",
                      pointer="/nodes/1/output_schema/properties/bogus", node_id="child")
    t075.assert_error(errors, code="io_output_type_mismatch",
                      pointer="/nodes/1/output_schema/properties/report", node_id="child")


def test_unpublished_revoked_and_archived_child_versions_are_distinguishable(t025_stack) -> None:
    admin, creator = t075.creator(t025_stack)
    app = t025.create_application_or_fail(creator, "t075 refusals")
    definition = parent_definition(t025.new_uuid())

    # Unpublished: no such version row exists at all.
    response = save_and_validate(t025_stack, creator, app["id"], definition)
    t075.assert_error(t075.errors_of(response), code="dependency_not_found",
                      pointer="/nodes/1/config/application_version", node_id="child")

    # Revoked: a real published version row with revoked_at set.
    revoked_child = child_version(t025_stack, creator)
    # T068's revoke shape requires a reason beside revoked_at (the T070 endpoint
    # will be the producer; this case exercises the readiness rule).
    t025.sql_utf8(
        t025_stack.dbname,
        "UPDATE platform.application_versions SET revoked_at = now(), "
        "revoke_reason = 't075 revocation case' "
        f"WHERE id = {t025._literal(revoked_child['id'])}::uuid",
    )
    response = save_and_validate(t025_stack, creator, app["id"],
                                 parent_definition(revoked_child["id"]))
    t075.assert_error(t075.errors_of(response), code="dependency_revoked",
                      pointer="/nodes/1/config/application_version", node_id="child")

    # Archived application: a live version of an application nobody may call.
    archived_child = child_version(t025_stack, creator)
    t025.sql_utf8(
        t025_stack.dbname,
        "UPDATE platform.applications SET archived_at = now() "
        f"WHERE id = {t025._literal(archived_child['application'])}::uuid",
    )
    response = save_and_validate(t025_stack, creator, app["id"],
                                 parent_definition(archived_child["id"]))
    t075.assert_error(t075.errors_of(response), code="dependency_not_visible",
                      pointer="/nodes/1/config/application_version", node_id="child")

    # A foreign version without a read grant is not visible either, even when it
    # exists: the mapping can never be checked against another principal's data.
    _foreign_admin, foreign = t075.creator(t025_stack)
    foreign_child = child_version(t025_stack, foreign)
    response = save_and_validate(t025_stack, creator, app["id"],
                                 parent_definition(foreign_child["id"]))
    t075.assert_error(t075.errors_of(response), code="dependency_not_visible",
                      pointer="/nodes/1/config/application_version", node_id="child")
    assert t025.count_where_application(t025_stack.dbname, "application_versions", app["id"]) == 0


def test_a_real_dependency_cycle_and_a_self_call_are_refused(t025_stack) -> None:
    _admin, creator = t075.creator(t025_stack)
    first = child_version(t025_stack, creator)
    second = child_version(t025_stack, creator)
    # The cycle is a real fact in platform.version_dependencies (F02B writes these
    # edges at publication; a published child cannot be edited, so the fixture
    # inserts the two edges directly and the reader under test is the real one).
    t025.seed_version_dependency(t025_stack.dbname, first["id"], second["id"])
    t025.seed_version_dependency(t025_stack.dbname, second["id"], first["id"])

    app = t025.create_application_or_fail(creator, "t075 cycle")
    response = save_and_validate(t025_stack, creator, app["id"],
                                 parent_definition(second["id"]))
    t075.assert_error(t075.errors_of(response), code="dependency_cycle",
                      pointer="/nodes/1/config/application_version", node_id="child")

    # A draft that calls a published version of its own application is a cycle.
    self_app = t025.create_application_or_fail(creator, "t075 self call")
    own = child_version(t025_stack, creator)
    draft = parent_definition(own["id"])
    saved = t075.save_definition(creator, self_app["id"], draft)
    assert saved.status == 200, saved.text
    own_version = t075.seal_version(t025_stack, creator, self_app["id"], version_no=1)
    assert own_version["manifest"]["subapp_versions"] == [own["id"]]
    self_draft = parent_definition(own_version["id"])
    response = save_and_validate(t025_stack, creator, self_app["id"], self_draft)
    t075.assert_error(t075.errors_of(response), code="dependency_cycle",
                      pointer="/nodes/1/config/application_version", node_id="child")


def test_child_depth_is_enforced_over_the_real_version_closure(t025_stack) -> None:
    _admin, creator = t075.creator(t025_stack)
    child = child_version(t025_stack, creator)
    grandchild = child_version(t025_stack, creator)
    t025.seed_version_dependency(t025_stack.dbname, child["id"], grandchild["id"])

    app = t025.create_application_or_fail(creator, "t075 depth")
    # Depth 1 allows only a direct child; this child closes over two generations.
    response = save_and_validate(
        t025_stack, creator, app["id"],
        parent_definition(child["id"], max_child_depth=1),
    )
    error = t075.assert_error(t075.errors_of(response), code="child_depth_exceeded",
                              pointer="/nodes/1/config/application_version", node_id="child")
    assert "2" in error["message"], error
    # The same pin is legal when the policy really allows two generations.
    response = save_and_validate(
        t025_stack, creator, app["id"],
        parent_definition(child["id"], max_child_depth=2),
    )
    assert response.json == {"valid": True, "errors": []}, response.json


def test_child_call_count_is_enforced_over_the_declared_references(t025_stack) -> None:
    _admin, creator = t075.creator(t025_stack)
    child = child_version(t025_stack, creator)
    app = t025.create_application_or_fail(creator, "t075 count")
    response = save_and_validate(
        t025_stack, creator, app["id"],
        parent_definition(child["id"], two_calls=True, max_child_calls=1),
    )
    t075.assert_error(t075.errors_of(response), code="child_calls_exceeded",
                      pointer="/policy/max_child_calls")
    response = save_and_validate(
        t025_stack, creator, app["id"],
        parent_definition(child["id"], two_calls=True, max_child_calls=2),
    )
    assert response.json == {"valid": True, "errors": []}, response.json


def test_a_newer_child_version_never_replaces_the_pinned_reference(t025_stack) -> None:
    _admin, creator = t075.creator(t025_stack)
    # Child v1 freezes input_schema {source} and output {report, summary}.
    child = child_version(t025_stack, creator)

    parent_app = t025.create_application_or_fail(creator, "t075 pinned parent")
    definition = parent_definition(child["id"], output_schema={
        "type": "object",
        "properties": {"report": {"type": "string"}},
        "required": ["report"],
    })
    response = save_and_validate(t025_stack, creator, parent_app["id"], definition)
    assert response.json == {"valid": True, "errors": []}, response.json
    frozen_hash = response.header("X-Manifest-SHA256")
    frozen = t075.validated_manifest(t025_stack, creator, parent_app["id"])
    assert frozen["manifest"]["subapp_versions"] == [child["id"]]
    paired_bytes = frozen["bytes"]
    parent_version = t075.seal_version(t025_stack, creator, parent_app["id"], version_no=1)
    assert t075.dependency_edges(t025_stack.dbname, parent_version["id"]) == [child["id"]]
    parent_row_before = t075.version_row(t025_stack.dbname, parent_version["id"])

    # A newer child version (v2) now exists with a stricter input schema: it
    # requires `region` in addition to `source`. It is stored the same way v1 was.
    changed = t075.child_definition(
        t025.seed_model_version(t025_stack.dbname, creator.principal_id),
        input_schema={
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "region": {"type": "string"},
            },
            "required": ["source", "region"],
        },
    )
    saved = t075.save_definition(creator, child["application"], changed)
    assert saved.status == 200, saved.text
    newer = t075.seal_version(t025_stack, creator, child["application"], version_no=2)
    assert newer["manifest_sha256"] != child["manifest_sha256"]

    # The parent still freezes exactly the same bytes, the same pinned child and
    # the same dependency edge; it is still valid although v2 now requires a
    # field the parent never mapped. Selecting v2 instead is refused, which
    # proves the newer version really did change.
    response = t025.validate(creator, parent_app["id"])
    assert response.json == {"valid": True, "errors": []}, response.json
    assert response.header("X-Manifest-SHA256") == frozen_hash
    assert t075.artifact_bytes(t025_stack, parent_version["manifest_artifact_id"]) == paired_bytes
    assert t075.version_row(t025_stack.dbname, parent_version["id"]) == parent_row_before
    assert t075.dependency_edges(t025_stack.dbname, parent_version["id"]) == [child["id"]]

    pinned = t075.get_version(creator, child["id"])
    assert pinned.status == 200, pinned.text
    assert pinned.json["manifest_sha256"] == child["manifest_sha256"], pinned.text
    latest = t075.get_version(creator, newer["id"])
    assert latest.status == 200, latest.text
    assert latest.json["manifest_sha256"] == newer["manifest_sha256"], latest.text

    switched = parent_definition(newer["id"])
    response = save_and_validate(t025_stack, creator, parent_app["id"], switched)
    t075.assert_error(t075.errors_of(response), code="io_mapping_missing_field",
                      pointer="/nodes/1/inputs/region", node_id="child")


def test_the_picker_reads_are_acl_filtered(t025_stack) -> None:
    admin, creator = t075.creator(t025_stack)
    own_child = child_version(t025_stack, creator)
    _foreign_admin, foreign = t075.creator(t025_stack)
    foreign_child = child_version(t025_stack, foreign)

    applications = t075.list_applications(creator)
    visible = t075.application_ids(applications)
    assert own_child["application"] in visible
    assert foreign_child["application"] not in visible

    # The creator cannot enumerate another principal's versions, so a picker
    # built on these reads can never offer a version the creator may not call.
    denied = t075.list_versions(creator, foreign_child["application"])
    assert denied.status == 404, denied.text
    assert denied.json["error"]["code"] == "resource_not_found", denied.text

    # An explicit read grant makes the application and its versions offerable.
    t025.grant_read(admin, creator.principal_id, foreign_child["application"])
    listed = t075.list_versions(creator, foreign_child["application"])
    assert listed.status == 200, listed.text
    items = listed.json["items"]
    assert [item["id"] for item in items] == [foreign_child["id"]], items
    # The version read carries the frozen manifest the picker renders the child's
    # input/output schemas from.
    assert items[0]["manifest"]["definition"]["input_schema"]["required"] == ["source"]
    assert json.dumps(items[0]["manifest"]["definition"]["output_schema"]).find("report") >= 0

    # T070: a revoked version is not a published version the creator may call, so
    # the list (and the picker built from it) never offers it, and its detail read
    # is the same 404 as a missing row.
    revoked_id = t025.seed_published_version(
        t025_stack.dbname,
        foreign.principal_id,
        foreign_child["application"],
        version_no=2,
        revoked=True,
    )
    relisted = t075.list_versions(creator, foreign_child["application"])
    assert relisted.status == 200, relisted.text
    offered = [item["id"] for item in relisted.json["items"]]
    assert offered == [foreign_child["id"]], offered
    assert revoked_id not in offered
    revoked = t075.get_version(creator, revoked_id)
    assert revoked.status == 404, revoked.text
    assert revoked.json["error"]["code"] == "resource_not_found", revoked.text
