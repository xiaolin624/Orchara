"""T023 acceptance 3, honest boundary: an invalid draft can be saved.

PLAN §2 D6 and T023's brief split this deliberately: saving only performs
structural DraftInput validation, so a semantically invalid workflow (a cycle) is
persisted and read back unchanged. Refusing to *debug* or *submit* it belongs to
T025/T027/F02B. T025 added validate and T027 added debugDraft; the last test
proves those two refusals are real and records that publication-submit still has
no endpoint (F02B), so it does not treat a 404 as a refuse-path success.
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


def test_an_invalid_draft_is_refused_by_validate_and_debug_while_submit_is_absent(f02a_stack) -> None:
    """The invalid draft is refused by the two endpoints that exist.

    T025 added ``POST /applications/{id}/validate``; T027 added
    ``POST /applications/{id}/debug-runs``. Both refuse the empty draft with
    located errors. Publication-submit is F02B and has no endpoint yet: the 404
    below proves an endpoint is absent, not that a refusal path was implemented,
    so T023's acceptance item 3 stays ``partial`` until F02B lands (report §2/§8).
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

    # debug-runs exists after T027. It requires the Idempotency-Key first ...
    debug_path = f"/api/v1/applications/{application['id']}/debug-runs"
    missing_key = owner.client.post(debug_path, body={}, csrf=owner.csrf)
    assert missing_key.status == 400, missing_key.text
    assert missing_key.json["error"]["code"] == "idempotency_key_required", missing_key.text

    # ... and with one it refuses the not-executable draft with located errors.
    refused = owner.client.post(
        debug_path,
        body={"draft_revision": 1, "input": {}, "artifact_ids": []},
        csrf=owner.csrf,
        headers={"Idempotency-Key": "t023-acceptance-3"},
    )
    assert refused.status == 422, refused.text
    error = refused.json["error"]
    assert error["code"] == "validation_failed", refused.text
    located = error["details"]["errors"]
    assert isinstance(located, list) and located, refused.text
    assert all({"code", "pointer", "message"} <= set(entry) for entry in located), located

    # Publication-submit is now delivered by F02B/T068. An empty request is
    # refused by the new endpoint's own contract (Idempotency-Key first, then the
    # two-field body), so this no longer proves absence; T023 keeps its partial
    # acceptance item and the F02B behaviour is covered by tests/f02b.
    submit = owner.client.post(
        f"/api/v1/applications/{application['id']}/submit", body={}, csrf=owner.csrf
    )
    assert submit.status == 400, f"submit shape: {submit.status} {submit.text}"
    assert submit.json["error"]["code"] == "idempotency_key_required", submit.text
    submit_with_key = owner.client.post(
        f"/api/v1/applications/{application['id']}/submit",
        body={},
        csrf=owner.csrf,
        headers={"Idempotency-Key": "t023-submit-shape"},
    )
    assert submit_with_key.status == 422, submit_with_key.text
    assert submit_with_key.json["error"]["code"] == "validation_failed", submit_with_key.text
