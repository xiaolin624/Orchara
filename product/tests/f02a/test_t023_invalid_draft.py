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


def test_debug_and_submit_endpoints_are_still_absent_after_t025(f02a_stack) -> None:
    """T025 closes the validate side; debug (T027) and submit (F02B) remain absent.

    T023 shipped ``CheckExecutable`` and recorded "validate/debug/submit" as not
    built. T025 adds ``POST /applications/{id}/validate`` (it answers the empty
    draft with located errors rather than 404); the other two endpoints are still
    the ticket boundary for T027 and F02B, so a 404 here is the evidence.
    """
    admin = f.admin_session(f02a_stack)
    owner = f.invite_and_activate(f02a_stack, admin, "t023-absent-owner")
    application = f.create_application_or_fail(owner, "endpoints not built")

    validate = owner.client.post(
        f"/api/v1/applications/{application['id']}/validate",
        body={},
        csrf=owner.csrf,
    )
    assert validate.status == 200, validate.text
    assert validate.json["valid"] is False, validate.text

    for path in ("debug-runs", "submit"):
        response = owner.client.post(
            f"/api/v1/applications/{application['id']}/{path}",
            body={},
            csrf=owner.csrf,
        )
        assert response.status == 404, f"{path} unexpectedly exists: {response.status} {response.text}"
