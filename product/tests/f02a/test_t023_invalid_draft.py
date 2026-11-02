"""T023 acceptance 3, honest boundary: an invalid draft can be saved.

PLAN §2 D6 and T023's brief split this deliberately: saving only performs
structural DraftInput validation, so a semantically invalid workflow (a cycle) is
persisted and read back unchanged. Refusing to *debug* or *submit* it belongs to
T025/T027, whose endpoints do not exist in this ticket. The last test asserts
that absence instead of pretending the behaviour is already enforced.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from support import f02a as f
from support.fixtures import (  # noqa: F401 - pytest fixtures
    f02a_stack,
    fresh_database,
    go_binaries,
    migrated_database,
)

CORPUS = Path(__file__).resolve().parents[2] / "probes" / "p05_dsl" / "corpus.json"


def _cycle_definition() -> dict:
    corpus = json.load(io.open(CORPUS, encoding="utf-8"))
    for entry in corpus["cases"]:
        if entry["name"] == "cycle":
            return entry["input"]
    raise AssertionError("the P05 corpus has no cycle case")


def test_a_cyclic_definition_saves_and_reads_back_verbatim(f02a_stack) -> None:
    admin = f.admin_session(f02a_stack)
    owner = f.invite_and_activate(f02a_stack, admin, "t023-invalid-owner")
    application = f.create_application_or_fail(owner, "invalid but saved")
    definition = _cycle_definition()

    saved = f.save_draft(
        owner, application["id"], '"1"', definition=definition,
        layout={"viewport": {"x": 0, "y": 0, "zoom": 1}}, capability_card={},
    )
    assert saved.status == 200, saved.text
    assert saved.header("ETag") == '"2"'

    refreshed = f.get_draft(owner, application["id"])
    assert refreshed.status == 200
    assert refreshed.json["definition"] == definition
    row = f.draft_row(f02a_stack.dbname, application["id"])
    assert row["definition"] == definition


def test_validate_debug_and_submit_endpoints_are_still_absent(f02a_stack) -> None:
    """T023 exposes CheckExecutable, not the endpoints that consume it.

    Their absence is the ticket boundary: validateApplication is T025,
    debugDraft is T027 and submitApplication is F02B. A 404 here is the evidence
    that T023 did not claim their acceptance as passed.
    """
    admin = f.admin_session(f02a_stack)
    owner = f.invite_and_activate(f02a_stack, admin, "t023-absent-owner")
    application = f.create_application_or_fail(owner, "endpoints not built")
    for path in ("validate", "debug-runs", "submit"):
        response = owner.client.post(
            f"/api/v1/applications/{application['id']}/{path}",
            body={},
            csrf=owner.csrf,
        )
        assert response.status == 404, f"{path} unexpectedly exists: {response.status} {response.text}"
