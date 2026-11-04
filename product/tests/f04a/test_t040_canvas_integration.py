"""F04A/T040 integration: canvas-shaped drafts over real HTTP and PostgreSQL.

The stack is the T039/T024 shape (``f06_stack``): real PostgreSQL migrated by
``cmd/admin``, the compiled API over a socket, a real filesystem object store
and the compiler inputs validate needs. Every assertion goes through the
contract endpoints; nothing compiles in-process.

What is covered: the canvas page and its committed assets are served; a
canvas-shaped {definition, layout} draft round-trips exactly; invalid
refs/outputs/types come back as located errors with node ids; a pending model or
an unapproved/non-ready tool is refused with dependency_not_ready; the saved
draft/export carries no run inputs, memory or secret material; non-owner and
non-creator refusals are unchanged.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from support import f00b, f02a, t025

CORPUS = Path(__file__).resolve().parent / "corpus" / "linear_f04a.json"


def creator(stack) -> f00b.Session:
    admin = f00b.admin_session(stack)
    login = "t040-creator-" + secrets.token_hex(4)
    return f00b.invite_and_activate(stack, admin, login, roles=("creator",))


def canvas_definition(tool_id: str, model_id: str) -> dict:
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    case = next(item for item in payload["cases"] if item["name"] == "valid-linear")
    definition = json.loads(json.dumps(case["document"]))
    definition["nodes"][1]["config"]["tool_version"] = tool_id
    definition["nodes"][2]["config"]["model_profile_version"] = model_id
    return definition


def canvas_layout() -> dict:
    return {
        "version": 1,
        "positions": {
            "start": {"x": 40.5, "y": 120},
            "extract": {"x": 280, "y": 121.25},
            "summarise": {"x": 520, "y": 122},
            "end": {"x": 760, "y": 123.75},
        },
    }


def draft_and_etag(session: f00b.Session, application_id: str) -> tuple[dict, str]:
    response = t025.get_draft(session, application_id)
    assert response.status == 200, response.text
    return response.json, response.header("ETag")


def save(session: f00b.Session, application_id: str, etag: str, definition: dict,
         layout: dict | None = None, card: dict | None = None) -> str:
    response = t025.save_draft(
        session, application_id, etag,
        definition=definition,
        layout=layout if layout is not None else canvas_layout(),
        capability_card=card if card is not None else f02a.full_card(),
    )
    assert response.status == 200, response.text
    return response.header("ETag")


def assert_located(errors: list[dict], code: str, pointer: str, node_id: str) -> dict:
    matches = [
        error for error in errors
        if error["code"] == code and error["pointer"] == pointer
        and error.get("node_id") == node_id
    ]
    assert matches, (code, pointer, node_id, errors)
    error = matches[0]
    assert set(error) == {"node_id", "code", "pointer", "message"}, error
    assert error["message"], error
    return error


def test_the_canvas_page_and_assets_are_served(f06_stack) -> None:
    application_id = f02a.new_uuid()
    page = f06_stack.server.http.get(f"/apps/{application_id}/edit")
    assert page.status == 200, page.text
    assert "text/html" in page.header("Content-Type")
    assert page.header("Cache-Control") == "no-store"
    body = page.text
    for marker in (
        'id="canvas-root"', 'id="draft-form"', 'id="draft-save"',
        "/assets/canvas.js", "/assets/canvas.css", "/assets/editor.js",
        "/assets/app.js", 'id="conflict-panel"', 'id="debug-entry"',
    ):
        assert marker in body, marker

    script = f06_stack.server.http.get("/assets/canvas.js")
    assert script.status == 200, script.text
    assert "javascript" in script.header("Content-Type")
    assert script.header("Cache-Control") == "no-store"
    assert "OrcharaCanvas" in script.text
    style = f06_stack.server.http.get("/assets/canvas.css")
    assert style.status == 200, style.text
    assert "css" in style.header("Content-Type")
    assert ".orchara-canvas" in style.text


def test_canvas_draft_round_trips_definition_and_layout_exactly(f06_stack) -> None:
    owner = creator(f06_stack)
    application = t025.create_application_or_fail(owner, "canvas round trip")
    _draft, etag = draft_and_etag(owner, application["id"])

    tool_id = t025.seed_tool_version(f06_stack.dbname, owner.principal_id)
    model_id = t025.seed_model_version(f06_stack.dbname, owner.principal_id)
    definition = canvas_definition(tool_id, model_id)
    layout = canvas_layout()
    next_etag = save(owner, application["id"], etag, definition, layout)
    assert next_etag and next_etag != etag

    reloaded, reloaded_etag = draft_and_etag(owner, application["id"])
    assert reloaded_etag == next_etag
    assert reloaded["definition"] == definition
    assert reloaded["layout"] == layout
    # Both halves of the canvas state are stored: every config value and every
    # fractional node position survives the round trip.
    assert reloaded["definition"]["nodes"][1]["config"]["tool_version"] == tool_id
    assert reloaded["definition"]["nodes"][2]["config"]["model_profile_version"] == model_id
    assert reloaded["layout"]["positions"]["extract"]["x"] == 280


def test_valid_canvas_definition_returns_the_manifest_hash(f06_stack) -> None:
    owner = creator(f06_stack)
    application = t025.create_application_or_fail(owner, "canvas validate ok")
    _draft, etag = draft_and_etag(owner, application["id"])
    tool_id = t025.seed_tool_version(f06_stack.dbname, owner.principal_id)
    model_id = t025.seed_model_version(f06_stack.dbname, owner.principal_id)
    save(owner, application["id"], etag, canvas_definition(tool_id, model_id))

    response = t025.validate(owner, application["id"])
    assert response.status == 200, response.text
    assert response.json == {"valid": True, "errors": []}, response.json
    manifest_hash = response.header("X-Manifest-SHA256")
    assert manifest_hash and len(manifest_hash) == 64


def test_invalid_ref_output_and_config_are_located_to_nodes(f06_stack) -> None:
    owner = creator(f06_stack)
    application = t025.create_application_or_fail(owner, "canvas located errors")
    _draft, etag = draft_and_etag(owner, application["id"])
    tool_id = t025.seed_tool_version(f06_stack.dbname, owner.principal_id)
    model_id = t025.seed_model_version(f06_stack.dbname, owner.principal_id)
    baseline = canvas_definition(tool_id, model_id)
    etag = save(owner, application["id"], etag, baseline)

    # 1. an unknown application-input pointer on the python node.
    bad_ref = json.loads(json.dumps(baseline))
    bad_ref["nodes"][1]["inputs"]["text"] = {"ref": "input#/missing"}
    etag = save(owner, application["id"], etag, bad_ref)
    response = t025.validate(owner, application["id"])
    assert response.status == 200 and response.json["valid"] is False, response.text
    assert_located(t025.validation_errors(response), "bad_pointer",
                   "/nodes/1/inputs/text", "extract")

    # 2. a required application output with no end mapping.
    missing_output = json.loads(json.dumps(baseline))
    del missing_output["nodes"][3]["inputs"]["summary"]
    etag = save(owner, application["id"], etag, missing_output)
    response = t025.validate(owner, application["id"])
    assert response.json["valid"] is False, response.text
    assert_located(t025.validation_errors(response), "missing_value",
                   "/nodes/3/inputs/summary", "end")

    # 3. a config type error on the python node.
    bad_config = json.loads(json.dumps(baseline))
    bad_config["nodes"][1]["config"]["tool_version"] = "not-a-uuid"
    save(owner, application["id"], etag, bad_config)
    response = t025.validate(owner, application["id"])
    assert response.json["valid"] is False, response.text
    assert_located(t025.validation_errors(response), "bad_config",
                   "/nodes/1/config/tool_version", "extract")


def test_pending_model_and_unapproved_or_unready_tool_are_refused(f06_stack) -> None:
    owner = creator(f06_stack)
    application = t025.create_application_or_fail(owner, "canvas dependency gate")
    _draft, etag = draft_and_etag(owner, application["id"])

    pending_model = t025.seed_model_version(
        f06_stack.dbname, owner.principal_id, validation_state="pending"
    )
    unapproved_tool = t025.seed_tool_version(
        f06_stack.dbname, owner.principal_id, state="ready", approved=False
    )
    etag = save(owner, application["id"], etag,
                canvas_definition(unapproved_tool, pending_model), card=f02a.full_card())

    response = t025.validate(owner, application["id"])
    assert response.status == 200, response.text
    errors = t025.validation_errors(response)
    assert response.json["valid"] is False, response.text
    assert_located(errors, "dependency_not_ready",
                   "/nodes/1/config/tool_version", "extract")
    assert_located(errors, "dependency_not_ready",
                   "/nodes/2/config/model_profile_version", "summarise")

    # A tool that is not ready (still building) is refused for the same reason.
    building_tool = t025.seed_tool_version(
        f06_stack.dbname, owner.principal_id, state="building", approved=True
    )
    ready_model = t025.seed_model_version(f06_stack.dbname, owner.principal_id)
    etag = save(owner, application["id"], etag,
                canvas_definition(building_tool, ready_model), card=f02a.full_card())
    response = t025.validate(owner, application["id"])
    assert response.json["valid"] is False, response.text
    assert_located(t025.validation_errors(response), "dependency_not_ready",
                   "/nodes/1/config/tool_version", "extract")


def test_non_owner_and_non_creator_refusals_are_unchanged(f06_stack) -> None:
    owner = creator(f06_stack)
    other = creator(f06_stack)
    application = t025.create_application_or_fail(owner, "canvas acl")
    _draft, etag = draft_and_etag(owner, application["id"])
    tool_id = t025.seed_tool_version(f06_stack.dbname, owner.principal_id)
    model_id = t025.seed_model_version(f06_stack.dbname, owner.principal_id)
    save(owner, application["id"], etag, canvas_definition(tool_id, model_id))
    path = f"/api/v1/applications/{application['id']}"

    # A different creator cannot see or compile the draft (404, not 403: ids are
    # not enumerable), and cannot save it either.
    from support import stack as st

    st.assert_error(other.client.get(path + "/draft"), 404, "resource_not_found")
    st.assert_error(other.client.post(path + "/validate", csrf=other.csrf),
                    404, "resource_not_found")
    st.assert_error(
        t025.save_draft(other, application["id"], '"1"',
                        definition=canvas_definition(tool_id, model_id)),
        404, "resource_not_found",
    )

    # An administrator without the creator role cannot create applications.
    admin = f00b.admin_session(f06_stack)
    st.assert_error(admin.client.post("/api/v1/applications", body={"name": "x"},
                                      csrf=admin.csrf), 403, "creator_role_required")


def test_the_saved_draft_export_has_no_run_inputs_secrets_or_memory(f06_stack) -> None:
    owner = creator(f06_stack)
    application = t025.create_application_or_fail(owner, "canvas export")
    _draft, etag = draft_and_etag(owner, application["id"])
    tool_id = t025.seed_tool_version(f06_stack.dbname, owner.principal_id)
    model_id = t025.seed_model_version(f06_stack.dbname, owner.principal_id)
    save(owner, application["id"], etag, canvas_definition(tool_id, model_id))

    draft, _etag = draft_and_etag(owner, application["id"])
    assert set(draft) == {"definition", "layout", "capability_card"}, draft.keys()
    definition_json = json.dumps(draft["definition"]).lower()
    for forbidden in ("run_inputs", "run_input", "memory", "personal", "secret",
                      "credential", "password", "api_key", "bearer"):
        assert forbidden not in definition_json, forbidden
    for node in draft["definition"]["nodes"]:
        assert set(node) == {"id", "type", "inputs", "output_schema", "config"}, node
    # Config only carries fixed versions/instructions/turn limits, never a
    # provider secret.
    assert draft["definition"]["nodes"][2]["config"]["model_profile_version"] == model_id
    assert "secret" not in json.dumps(draft).lower()
