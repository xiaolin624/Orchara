"""T023 acceptance: create, draft GET/PUT, ETag/If-Match and structural errors.

Every request goes through the real compiled ``cmd/api`` over a socket; every
assertion about persistence reads the real PostgreSQL row. The draft endpoints
are the ticket's user-facing contract, so nothing here is a unit stub.
"""

from __future__ import annotations

import json

from support import f02a as f
from support.fixtures import (  # noqa: F401 - pytest fixtures
    f02a_stack,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _setup(stack):
    admin = f.admin_session(stack)
    owner = f.invite_and_activate(stack, admin, "t023-draft-owner")
    application = f.create_application_or_fail(owner, "sales report")
    return admin, owner, application


def test_create_application_makes_revision_one_with_an_empty_draft(f02a_stack) -> None:
    _admin, owner, application = _setup(f02a_stack)
    row = f.draft_row(f02a_stack.dbname, application["id"])
    assert row["revision"] == 1
    assert row["execution_revision"] == 1
    assert row["definition"] == {}
    assert row["layout"] == {}
    assert row["capability_card"] == {}, "a new draft must be honestly empty, not padded"


def test_get_draft_returns_the_contract_shape_and_a_strong_etag(f02a_stack) -> None:
    _admin, owner, application = _setup(f02a_stack)
    response = f.get_draft(owner, application["id"])
    assert response.status == 200, response.text
    assert response.header("ETag") == '"1"', response.headers
    assert response.header("Cache-Control") == "no-store"
    body = response.json
    assert set(body) == {"definition", "layout", "capability_card"}, body
    assert body == {"definition": {}, "layout": {}, "capability_card": {}}


def test_save_then_get_round_trips_every_field_and_the_new_revision(f02a_stack) -> None:
    _admin, owner, application = _setup(f02a_stack)
    definition = {"dsl_version": "1.0", "nodes": [{"id": "start", "type": "start"}]}
    layout = {"viewport": {"x": 12, "y": 34, "zoom": 1.5}}
    card = f.full_card()
    saved = f.save_draft(
        owner, application["id"], '"1"',
        definition=definition, layout=layout, capability_card=card,
    )
    assert saved.status == 200, saved.text
    assert saved.header("ETag") == '"2"'
    assert saved.json == {"id": application["id"], "state": "saved"}

    # Refresh: the GET is what a browser reload receives, including the card.
    refreshed = f.get_draft(owner, application["id"])
    assert refreshed.status == 200
    assert refreshed.header("ETag") == '"2"'
    assert refreshed.json == {
        "definition": definition, "layout": layout, "capability_card": card,
    }
    row = f.draft_row(f02a_stack.dbname, application["id"])
    assert row["revision"] == 2
    assert row["execution_revision"] == 2, "a changed definition/card must bump the executable counter"


def test_fields_persist_independently(f02a_stack) -> None:
    _admin, owner, application = _setup(f02a_stack)
    definition = {"dsl_version": "1.0", "nodes": []}
    layout_a = {"viewport": {"x": 0, "y": 0, "zoom": 1}}
    layout_b = {"viewport": {"x": 200, "y": 80, "zoom": 0.75}}
    card = f.full_card(purpose="first purpose")

    assert f.save_draft(owner, application["id"], '"1"', definition=definition,
                        layout=layout_a, capability_card=card).status == 200
    row = f.draft_row(f02a_stack.dbname, application["id"])
    assert row["definition"] == definition and row["layout"] == layout_a
    assert row["capability_card"] == card

    # A layout-only save must not touch definition or the capability card.
    assert f.save_draft(owner, application["id"], '"2"', definition=definition,
                        layout=layout_b, capability_card=card).status == 200
    row = f.draft_row(f02a_stack.dbname, application["id"])
    assert row["definition"] == definition
    assert row["layout"] == layout_b
    assert row["capability_card"] == card
    assert row["execution_revision"] == 2, "layout is not executable content"


def test_missing_or_empty_if_match_is_428_and_changes_nothing(f02a_stack) -> None:
    _admin, owner, application = _setup(f02a_stack)
    before = f.draft_row(f02a_stack.dbname, application["id"])
    for missing in (None, ""):
        response = f.save_draft(owner, application["id"], missing)
        assert response.status == 428, response.text
        error = response.json["error"]
        assert error["code"] == "precondition_required", error
        assert error["details"]["header"] == "If-Match", error
        assert f.draft_row(f02a_stack.dbname, application["id"]) == before


def test_stale_and_garbled_etags_are_412_and_change_nothing(f02a_stack) -> None:
    _admin, owner, application = _setup(f02a_stack)
    assert f.save_draft(owner, application["id"], '"1"').status == 200
    before = f.draft_row(f02a_stack.dbname, application["id"])
    for stale in ('"1"', '"99"', 'W/"2"', '"abc"', "*", '"2", "3"'):
        response = f.save_draft(owner, application["id"], stale)
        assert response.status == 412, f"{stale}: {response.text}"
        error = response.json["error"]
        assert error["code"] == "precondition_failed", error
        assert error["details"] == {"current_revision": 2, "current_etag": '"2"'}, error
        assert f.draft_row(f02a_stack.dbname, application["id"]) == before


def test_unknown_or_malformed_application_ids_are_404(f02a_stack) -> None:
    _admin, owner, _application = _setup(f02a_stack)
    for candidate in (f.new_uuid(), "not-a-uuid"):
        assert f.get_draft(owner, candidate).status == 404
        assert f.save_draft(owner, candidate, '"1"').status == 404
        assert f.get_draft(owner, candidate).json["error"]["code"] == "resource_not_found"


def test_application_name_is_trimmed_and_bounded(f02a_stack) -> None:
    _admin, owner, _application = _setup(f02a_stack)
    trimmed = f.create_application(owner, "  padded name  ")
    assert trimmed.status == 201, trimmed.text
    assert trimmed.json["name"] == "padded name"

    for bad in ("", "   ", "x" * 201):
        response = f.create_application(owner, bad)
        assert response.status == 422, response.text
        error = response.json["error"]
        assert error["code"] == "validation_failed", error
        assert error["details"]["field"] == "name", error

    boundary = f.create_application(owner, "x" * 200)
    assert boundary.status == 201, boundary.text


def test_draft_input_shape_violations_are_422_with_the_field(f02a_stack) -> None:
    _admin, owner, application = _setup(f02a_stack)
    cases = {
        "missing definition": json.dumps({"layout": {}, "capability_card": {}}),
        "unknown top-level key": json.dumps(
            {"definition": {}, "layout": {}, "capability_card": {}, "visibility": "public"}
        ),
        "definition array": json.dumps(
            {"definition": [], "layout": {}, "capability_card": {}}
        ),
        "layout null": json.dumps(
            {"definition": {}, "layout": None, "capability_card": {}}
        ),
        "card partial": json.dumps(
            {"definition": {}, "layout": {}, "capability_card": {"purpose": "only purpose"}}
        ),
        "card one example": json.dumps(
            {
                "definition": {}, "layout": {},
                "capability_card": {
                    "purpose": "p", "includes": [], "excludes": [],
                    "required_inputs": [], "expected_outputs": [], "examples": ["one"],
                },
            }
        ),
    }
    expected_fields = {
        "missing definition": "definition",
        "unknown top-level key": "visibility",
        "definition array": "definition",
        "layout null": "layout",
        "card partial": "capability_card.includes",
        "card one example": "capability_card.examples",
    }
    for name, raw in cases.items():
        response = f.save_draft(owner, application["id"], '"1"', raw_body=raw)
        assert response.status == 422, f"{name}: {response.text}"
        error = response.json["error"]
        assert error["code"] == "validation_failed", (name, error)
        assert error["details"]["field"] == expected_fields[name], (name, error)
    # None of the refused bodies may have been stored.
    assert f.draft_row(f02a_stack.dbname, application["id"])["revision"] == 1


def test_oversize_documents_and_bodies_are_refused_without_storing(f02a_stack) -> None:
    _admin, owner, application = _setup(f02a_stack)
    before = f.draft_row(f02a_stack.dbname, application["id"])

    # A single field above its cap is a semantic 422, not a 413: the whole body
    # is still small enough to parse.
    oversized_definition = {"pad": "x" * (256 * 1024 + 1)}
    response = f.save_draft(
        owner, application["id"], '"1"', definition=oversized_definition
    )
    assert response.status == 422, response.text
    assert response.json["error"]["details"]["field"] == "definition"

    # A body beyond the request cap is the contract's 413.
    huge = json.dumps({"definition": {"pad": "x" * (1024 * 1024 + 1024)},
                       "layout": {}, "capability_card": {}})
    response = f.save_draft(owner, application["id"], '"1"', raw_body=huge)
    assert response.status == 413, response.text
    assert response.json["error"]["code"] == "payload_too_large"

    assert f.draft_row(f02a_stack.dbname, application["id"]) == before


def test_a_fresh_empty_draft_round_trips_through_get_and_put(f02a_stack) -> None:
    """The contract-gap guard: GET a brand-new draft and PUT it back unchanged."""
    _admin, owner, application = _setup(f02a_stack)
    current = f.get_draft(owner, application["id"])
    assert current.json["capability_card"] == {}
    saved = f.save_draft(
        owner,
        application["id"],
        current.header("ETag"),
        definition=current.json["definition"],
        layout=current.json["layout"],
        capability_card=current.json["capability_card"],
    )
    assert saved.status == 200, saved.text
    assert saved.header("ETag") == '"2"'
    assert f.get_draft(owner, application["id"]).json["capability_card"] == {}
