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


def test_an_invalid_draft_is_refused_by_validate_debug_and_submit(f02a_stack) -> None:
    """The invalid draft is refused by every executable-entry endpoint.

    T025 added ``POST /applications/{id}/validate``; T027 added
    ``POST /applications/{id}/debug-runs``; F02B/T068 added
    ``POST /applications/{id}/submit``. The last section was originally evidence
    that publication-submit had *no* endpoint (T023 stayed ``partial``); now that
    F02B has landed the same invalid draft must be refused by the real submit
    path, not by a 404.
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

    # Publication-submit exists since F02B/T068. It requires the Idempotency-Key
    # before the body, then refuses this not-executable draft with the same
    # located-error shape validate/debug-runs use (never a 404, never a version).
    submit_path = f"/api/v1/applications/{application['id']}/submit"
    missing_key = owner.client.post(submit_path, body={}, csrf=owner.csrf)
    assert missing_key.status == 400, missing_key.text
    assert missing_key.json["error"]["code"] == "idempotency_key_required", missing_key.text
    submit = owner.client.post(
        submit_path,
        body={"expected_manifest_sha256": "0" * 64, "evaluation_report_id": f.new_uuid()},
        csrf=owner.csrf,
        headers={"Idempotency-Key": "t023-acceptance-3-submit"},
    )
    assert submit.status == 422, f"submit did not refuse the invalid draft: {submit.status} {submit.text}"
    assert submit.json["error"]["code"] == "validation_failed", submit.text
    assert submit.json["error"]["details"]["errors"], submit.text
