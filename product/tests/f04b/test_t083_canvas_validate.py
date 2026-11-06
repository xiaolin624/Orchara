"""F04B/T083 integration: canvas-authored condition/join definitions over HTTP.

The canvas save path is type-agnostic; ``POST /applications/{id}/validate`` is
the authority. The fixtures are the shared P05 corpus documents (the same bytes
Go and Python validate), with their ``model``/``approval`` nodes replaced by
``python`` nodes bound to a seeded ready+approved tool version: the dependency
stage runs before the DSL stage, and this ticket is about the region verdict, so
the dependencies must be ready rather than masking it.

What is covered:
* a condition/join definition with edge ports round-trips through GET/PUT draft
  exactly as the canvas produced it (ports are not lost);
* the server accepts the condition, parallel and nested-region documents
  (valid:true + X-Manifest-SHA256);
* an illegal cross-region edge, an undeclared port and incomplete join sources
  come back as located errors with the pointer the canvas maps to an edge/field.

Not covered here (recorded as not_run in the report): running an F04B graph.
The executor refuses condition/join today (another agent's ticket), so no
debug-run is submitted; this file proves authoring + validate only.
"""

from __future__ import annotations

import json
import secrets

from support import f00b, f02a, t025


def creator(stack) -> f00b.Session:
    admin = f00b.admin_session(stack)
    login = "t083-creator-" + secrets.token_hex(4)
    return f00b.invite_and_activate(stack, admin, login, roles=("creator",))


def canvas_layout(definition: dict) -> dict:
    positions = {}
    for index, node in enumerate(definition["nodes"]):
        positions[node["id"]] = {"x": 80 + 220 * index, "y": 120 + (index % 3) * 80}
    return {"version": 1, "positions": positions}


def save(stack, session: f00b.Session, application_id: str, etag: str | None,
         definition: dict, layout: dict | None = None) -> str:
    response = t025.save_draft(
        session, application_id, etag,
        definition=definition,
        layout=layout if layout is not None else canvas_layout(definition),
        capability_card=f02a.full_card(),
    )
    assert response.status == 200, response.text
    return response.header("ETag")


def python_stand_ins(document: dict, tool_id: str) -> dict:
    """The P05 fixture with every non-pure node replaced by a seeded python node.

    The corpus uses placeholder tool ids (it is a language-neutral validator
    fixture); the HTTP validate path also runs the dependency stage, so every
    python node must point at the real seeded tool version.
    """
    definition = json.loads(json.dumps(document))
    for node in definition["nodes"]:
        if node["type"] in ("model", "approval", "python"):
            node["type"] = "python"
            node["config"] = {"tool_version": tool_id}
            node["inputs"] = {}
    return definition


def validated(t025_stack, owner: f00b.Session, name: str, document: dict) -> tuple[dict, dict]:
    """Create an application, save the definition and return (draft, validate body)."""
    application = t025.create_application_or_fail(owner, name)
    _draft, etag = draft_and_etag(owner, application["id"])
    tool_id = t025.seed_tool_version(t025_stack.dbname, owner.principal_id)
    definition = python_stand_ins(document, tool_id)
    layout = canvas_layout(definition)
    save(t025_stack, owner, application["id"], etag, definition, layout)
    response = t025.validate(owner, application["id"])
    assert response.status == 200, response.text
    return {
        "application_id": application["id"],
        "definition": definition,
        "layout": layout,
        "tool_id": tool_id,
        "response": response,
    }


def draft_and_etag(session: f00b.Session, application_id: str) -> tuple[dict, str]:
    response = t025.get_draft(session, application_id)
    assert response.status == 200, response.text
    return response.json, response.header("ETag")


def test_a_condition_definition_round_trips_ports_and_validates(t025_stack) -> None:
    owner = creator(t025_stack)
    application = t025.create_application_or_fail(owner, "canvas condition")
    _draft, etag = draft_and_etag(owner, application["id"])
    tool_id = t025.seed_tool_version(t025_stack.dbname, owner.principal_id)
    definition = python_stand_ins(t025.p05_case("condition-branch-approval"), tool_id)
    layout = canvas_layout(definition)
    save(t025_stack, owner, application["id"], etag, definition, layout)

    reloaded, _etag = draft_and_etag(owner, application["id"])
    assert reloaded["definition"] == definition
    assert reloaded["layout"] == layout
    ports = {
        (edge["source"], edge["target"]): edge.get("port")
        for edge in reloaded["definition"]["edges"]
        if edge["source"] == "choose"
    }
    assert ports == {("choose", "approve"): "yes", ("choose", "direct"): "no"}, ports
    choose = next(node for node in reloaded["definition"]["nodes"] if node["id"] == "choose")
    assert choose["config"]["join_id"] == "join"
    assert choose["config"]["cases"] == [
        {"input": "requires_approval", "op": "eq", "value": True, "port": "yes"},
    ]
    join = next(node for node in reloaded["definition"]["nodes"] if node["id"] == "join")
    assert join["config"] == {
        "fork_id": "choose",
        "mode": "all_active",
        "required_sources": ["approve", "direct"],
        "optional_sources": [],
    }

    response = t025.validate(owner, application["id"])
    assert response.status == 200, response.text
    assert response.json == {"valid": True, "errors": []}, response.json
    manifest_hash = response.header("X-Manifest-SHA256")
    assert manifest_hash and len(manifest_hash) == 64, manifest_hash


def test_parallel_and_nested_regions_validate(t025_stack) -> None:
    owner = creator(t025_stack)
    for case_name in ("parallel-two-branch", "parallel-optional-source",
                      "condition-nested-parallel"):
        application = t025.create_application_or_fail(owner, "canvas " + case_name)
        _draft, etag = draft_and_etag(owner, application["id"])
        tool_id = t025.seed_tool_version(t025_stack.dbname, owner.principal_id)
        definition = python_stand_ins(t025.p05_case(case_name), tool_id)
        save(t025_stack, owner, application["id"], etag, definition)
        response = t025.validate(owner, application["id"])
        assert response.status == 200, response.text
        assert response.json == {"valid": True, "errors": []}, (
            case_name, response.json,
        )
        assert response.header("X-Manifest-SHA256"), case_name


def test_region_violations_are_located_by_pointer(t025_stack) -> None:
    owner = creator(t025_stack)

    # 1. A branch that escapes its inner join is a cross-region edge.
    escaped = validated(t025_stack, owner, "canvas cross region",
                        t025.p05_case("condition-branch-escapes-outer-join"))
    errors = t025.validation_errors(escaped["response"])
    cross = [error for error in errors if error["code"] == "cross_region_edge"]
    assert len(cross) == 1, errors
    t025.assert_location(cross[0], code="cross_region_edge", pointer="/edges/10")
    assert cross[0]["message"], cross[0]

    # 2. A condition port that is not declared in cases/default_port.
    bad_port = validated(t025_stack, owner, "canvas bad port",
                         t025.p05_case("condition-port-not-declared"))
    errors = t025.validation_errors(bad_port["response"])
    assert any(error["code"] == "bad_port" and error["pointer"] == "/edges/1"
               for error in errors), errors
    assert any(error["code"] == "bad_port" and error["pointer"] == "/nodes/1/config/cases"
               for error in errors), errors

    # 3. Join sources that do not cover every direct predecessor.
    bad_join = validated(t025_stack, owner, "canvas join sources",
                         t025.p05_case("join-sources-mismatch"))
    errors = t025.validation_errors(bad_join["response"])
    t025.assert_location(
        next(error for error in errors if error["code"] == "bad_join_sources"),
        code="bad_join_sources",
        pointer="/nodes/4/config/required_sources",
    )
