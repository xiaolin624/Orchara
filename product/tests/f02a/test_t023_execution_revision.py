"""T023: revision vs execution_revision, the two counters T025 depends on.

``revision`` is the ETag/If-Match counter and moves on every accepted save.
``execution_revision`` moves only when the executable content (definition or
capability_card) changes, so a layout-only edit cannot change the execution hash
T025 will pin. These tests prove both counters through the real save path and
read the real row after each save.
"""

from __future__ import annotations

from support import f02a as f
from support.fixtures import (  # noqa: F401 - pytest fixtures
    f02a_stack,
    fresh_database,
    go_binaries,
    migrated_database,
)

DEFINITION_A = {"dsl_version": "1.0", "nodes": [{"id": "start", "type": "start"}]}
DEFINITION_REORDERED = {"nodes": [{"id": "start", "type": "start"}], "dsl_version": "1.0"}
DEFINITION_B = {"dsl_version": "1.0", "nodes": [{"id": "start", "type": "start"}, {"id": "end", "type": "end"}]}
LAYOUT_ONE = {"viewport": {"x": 0, "y": 0, "zoom": 1}}
LAYOUT_TWO = {"viewport": {"x": 320, "y": 140, "zoom": 0.5}}


def _save(stack, owner, application_id, *, revision, definition, layout, card):
    response = f.save_draft(
        owner, application_id, f'"{revision}"',
        definition=definition, layout=layout, capability_card=card,
    )
    assert response.status == 200, response.text
    assert response.header("ETag") == f'"{revision + 1}"'
    row = f.draft_row(stack.dbname, application_id)
    assert row["revision"] == revision + 1
    return row


def test_layout_only_saves_do_not_move_execution_revision(f02a_stack) -> None:
    admin = f.admin_session(f02a_stack)
    owner = f.invite_and_activate(f02a_stack, admin, "t023-exec-owner")
    application = f.create_application_or_fail(owner, "execution counter")

    # 1 -> 2: the first save changes both definition and card, so both counters move.
    row = _save(
        f02a_stack, owner, application["id"], revision=1,
        definition=DEFINITION_A, layout=LAYOUT_ONE, card={},
    )
    assert row["execution_revision"] == 2

    # 2 -> 3: layout only; revision moves, execution_revision stays.
    row = _save(
        f02a_stack, owner, application["id"], revision=2,
        definition=DEFINITION_A, layout=LAYOUT_TWO, card={},
    )
    assert row["execution_revision"] == 2

    # 3 -> 4: an identical body (same layout too) still moves the CAS counter.
    row = _save(
        f02a_stack, owner, application["id"], revision=3,
        definition=DEFINITION_A, layout=LAYOUT_TWO, card={},
    )
    assert row["execution_revision"] == 2

    # 4 -> 5: a definition change moves both.
    row = _save(
        f02a_stack, owner, application["id"], revision=4,
        definition=DEFINITION_B, layout=LAYOUT_TWO, card={},
    )
    assert row["execution_revision"] == 3

    # 5 -> 6: a capability-card change is executable content too.
    card = f.full_card(purpose="card changes the executable content")
    row = _save(
        f02a_stack, owner, application["id"], revision=5,
        definition=DEFINITION_B, layout=LAYOUT_TWO, card=card,
    )
    assert row["execution_revision"] == 4

    # 6 -> 7: same card again does not move it.
    row = _save(
        f02a_stack, owner, application["id"], revision=6,
        definition=DEFINITION_B, layout=LAYOUT_TWO, card=card,
    )
    assert row["execution_revision"] == 4
    assert row["revision"] == 7


def test_json_object_key_order_is_not_executable_change(f02a_stack) -> None:
    """jsonb equality (the save path's proxy for canonical bytes) ignores key order."""
    admin = f.admin_session(f02a_stack)
    owner = f.invite_and_activate(f02a_stack, admin, "t023-key-order-owner")
    application = f.create_application_or_fail(owner, "key order")

    _save(
        f02a_stack, owner, application["id"], revision=1,
        definition=DEFINITION_A, layout=LAYOUT_ONE, card={},
    )
    row = _save(
        f02a_stack, owner, application["id"], revision=2,
        definition=DEFINITION_REORDERED, layout=LAYOUT_ONE, card={},
    )
    assert row["execution_revision"] == 2, (
        "the same JSON value in a different key order was treated as an executable change"
    )


def test_the_read_model_exposes_the_saved_content_not_a_counter(f02a_stack) -> None:
    admin = f.admin_session(f02a_stack)
    owner = f.invite_and_activate(f02a_stack, admin, "t023-read-model-owner")
    application = f.create_application_or_fail(owner, "read model")

    _save(
        f02a_stack, owner, application["id"], revision=1,
        definition=DEFINITION_A, layout=LAYOUT_ONE, card={},
    )
    _save(
        f02a_stack, owner, application["id"], revision=2,
        definition=DEFINITION_A, layout=LAYOUT_TWO, card={},
    )
    response = f.get_draft(owner, application["id"])
    assert response.status == 200
    assert response.header("ETag") == '"3"'
    assert response.json["definition"] == DEFINITION_A
    assert response.json["layout"] == LAYOUT_TWO
