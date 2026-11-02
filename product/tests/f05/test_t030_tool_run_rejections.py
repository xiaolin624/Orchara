"""T030 real-stack: every documented input rejection is a specific safe failure.

Each case is a real ready artifact (CSV/XLSX uploaded through T028, or a seeded
ready macro workbook for the state the public admission path deliberately
refuses), a real POST /api/v1/tool-runs, a real maintenance process executing the
preset tool, and the real Python tool rejecting the bytes. The assertions cover
the job's error code/failure class, the safe diagnostics artifact, the absence of
a summary artifact, and the safety of the capped log.

The wrong-media-type and unreachable-artifact refusals are covered in
``test_t030_tool_run_flow.py`` (they are API-layer refusals and never become a
job); this file is about tool rejections that do become a durable failed job.
"""

from __future__ import annotations

import json
import secrets

import pytest

from support import f00b, t028, t030
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t030_stack,
)


def owner_session(stack: t030.T030Stack) -> t030.Session:
    admin = t030.admin_session(stack)
    return t030.invite_and_activate(stack, admin, f"t030-reject-{secrets.token_hex(3)}")


def prepare_ready_uploads(
    stack: t030.T030Stack, session: t030.Session, cases: list[tuple[str, bytes, str]]
) -> list[tuple[str, str, str]]:
    """Upload every case, run one finalizer drain, wait for every artifact.

    Returns (label, expected_code, artifact_id) triples.
    """
    prepared: list[tuple[str, str, str]] = []
    for label, content, expected_code in cases:
        media_type = t030.XLSX if label.endswith("xlsx") else t030.CSV
        created = t028.create_upload(
            session, media_type=media_type, size_bytes=len(content), purpose="task_input"
        )
        assert created.status == 201, (label, created.text)
        artifact_id = created.json["artifact_id"]
        assert t028.put_link(created.json["upload_url"], content).status == 204
        completed = t028.complete_upload(session, artifact_id, t030.sha256_hex(content))
        assert completed.status == 202, (label, completed.text)
        prepared.append((label, expected_code, artifact_id))
    t028.finalize_once(stack)
    for _label, _code, artifact_id in prepared:
        t028.wait_for_artifact_state(session, artifact_id, {"ready"})
    return prepared


CSV_CASES = [
    (
        "missing_column",
        b"order_id,region,amount\nA1,x,1.00\n",
        "missing_column",
    ),
    (
        "extra_column",
        b"order_id,date,region,amount,currency\nA1,2026-01-01,x,1.00,USD\n",
        "extra_column",
    ),
    (
        "duplicate_order_id",
        b"order_id,date,region,amount\nA1,2026-01-01,x,1.00\nA1,2026-01-02,x,2.00\n",
        "duplicate_order_id",
    ),
    (
        "negative_amount",
        b"order_id,date,region,amount\nA1,2026-01-01,x,-1.00\n",
        "negative_amount",
    ),
    (
        "empty_region",
        b"order_id,date,region,amount\nA1,2026-01-01, ,1.00\n",
        "empty_region",
    ),
    (
        "invalid_date",
        b"order_id,date,region,amount\nA1,2026-13-01,x,1.00\n",
        "invalid_date",
    ),
    (
        "formula_csv",
        b'order_id,date,region,amount\nA1,2026-01-01,x,"=SUM(1,2)"\n',
        "formula_not_supported",
    ),
    (
        "invalid_amount",
        b"order_id,date,region,amount\nA1,2026-01-01,x,1e3\n",
        "invalid_amount",
    ),
]


def expected_location(code: str, label: str = "") -> tuple[int | None, str | None, str | None]:
    return {
        "missing_column": (1, "date", None),
        "extra_column": (1, "currency", None),
        "duplicate_order_id": (3, "order_id", None),
        "negative_amount": (2, "amount", None),
        "empty_region": (2, "region", None),
        "invalid_date": (2, "date", None),
        "formula_not_supported": (2, "amount", "D2" if label == "formula_xlsx" else None),
        "invalid_amount": (2, "amount", None),
    }.get(code, (None, None, None))


def test_csv_and_xlsx_rejections_fail_with_specific_safe_diagnostics(t030_stack) -> None:
    owner = owner_session(t030_stack)
    formula_workbook = t030.formula_xlsx()
    prepared = prepare_ready_uploads(
        t030_stack,
        owner,
        CSV_CASES
        + [
            ("formula_xlsx", formula_workbook, "formula_not_supported"),
        ],
    )
    # The macro path can never become ready through T028 admission; seed the
    # ready row directly so the tool's own independent macro check is what runs.
    macro = t030.seed_ready_artifact(
        t030_stack, owner.principal_id, t030.macro_xlsx(), t030.XLSX
    )
    prepared.append(("macro_xlsx", "macro_present", macro.id))

    created: list[tuple[str, str, str]] = []
    for label, expected_code, artifact_id in prepared:
        response = t030.create_tool_run(owner, artifact_id, key=f"t030-reject-{label}")
        assert response.status == 202, (label, response.text)
        created.append((label, expected_code, response.json["id"]))

    reports = t030.job_reports(t030.run_tool_pools_once(t030_stack)[1])
    assert len(reports) == len(created), reports
    assert all(report["result"] == "failed" for report in reports), reports

    for label, expected_code, job_id in created:
        run = t030.wait_for_run_state(owner, job_id, {"failed"})
        t030.assert_run_view(run)
        assert run["state"] == "failed", (label, run)
        assert run["error_code"] == expected_code, (label, run)
        assert t030.diagnostics_row(t030_stack.dbname, job_id) is not None, label

        row = t030.job_row(t030_stack.dbname, job_id)
        assert row["error_code"] == expected_code, (label, row)
        assert row["failure_class"] == "business", (label, row)
        assert row["result_artifact_id"] is None, (label, row)
        assert int(row["attempt_count"]) == 1, (label, row)

        purposes = {artifact["purpose"] for artifact in run["artifacts"]}
        assert purposes == {"diagnostics", "log"}, (label, purposes)

        diagnostics = t030.diagnostics_row(t030_stack.dbname, job_id)
        f00b.wait_until(
            lambda artifact_id=diagnostics["id"]: t028.get_artifact(owner, artifact_id).json.get("state")
            == "ready",
            what=f"{label} diagnostics to become ready",
        )
        document = t030.read_artifact_json(owner, diagnostics["id"])
        t030.assert_diagnostics_safe(document, expected_code=expected_code)
        assert document["retryable"] is False, (label, document)
        assert document["input"]["artifact_id"] in {
            artifact_id for _label, _code, artifact_id in prepared
        }
        assert document["input"]["sha256"] == t028.artifact_row(
            t030_stack.dbname, document["input"]["artifact_id"]
        )["sha256"]

        wanted_row, wanted_column, wanted_cell = expected_location(expected_code, label)
        location = document.get("location") or {}
        if wanted_row is not None:
            assert location.get("row") == wanted_row, (label, document)
        if wanted_column is not None:
            assert location.get("column") == wanted_column, (label, document)
        if wanted_cell is not None:
            assert location.get("cell") == wanted_cell, (label, document)

        # The rejected input bytes never enter the log artifact.
        log = next(artifact for artifact in run["artifacts"] if artifact["purpose"] == "log")
        log_text = t030.read_artifact_text(owner, log["id"])
        assert "order_id,date,region,amount" not in log_text, label
        assert "A1,2026-01-01" not in log_text, label
        assert "ORCHARA_" not in log_text, label
        assert str(t030_stack.object_dir) not in log_text, label
        assert t030_stack.signing_key not in log_text, label

    # A failed run never exposes a summary artifact through the read model or the
    # database, and its output artifacts are diagnostics/log only.
    for label, expected_code, job_id in created:
        artifact_rows = t030.output_artifacts(t030_stack.dbname, job_id)
        assert {artifact["purpose"] for artifact in artifact_rows} == {"diagnostics", "log"}, (
            label,
            artifact_rows,
        )
        assert all(row["state"] in {"scanning", "ready"} for row in artifact_rows), (label, artifact_rows)

    # The replay path still works after a failure: the same key returns the same
    # terminal job and the same body, and no second job is created.
    label, expected_code, artifact_id = prepared[0]
    replay = t030.create_tool_run(owner, artifact_id, key=f"t030-reject-{label}")
    assert replay.status == 202, replay.text
    assert replay.json["state"] == "ready"  # the recorded 202 body, not the current state
    assert replay.json["id"] == created[0][2]
    assert t030.count_tool_runs(t030_stack.dbname) == len(created)


def test_a_json_or_yaml_artifact_is_refused_before_a_job_exists(t030_stack) -> None:
    owner = owner_session(t030_stack)
    ready_json = t030.upload_ready_artifact(
        t030_stack, owner, b'{"orders": []}', t030.JSON
    )
    refused = t030.create_tool_run(owner, ready_json)
    assert refused.status == 422, refused.text
    assert refused.json["error"]["code"] == "validation_failed"
    assert refused.json["error"]["details"]["field"] == "artifact_id"
    assert t030.count_tool_runs(t030_stack.dbname) == 0


def test_a_failed_run_can_be_re_run_with_a_new_key(t030_stack) -> None:
    """A rejected job is terminal for that intention, not for the artifact.

    The idempotency receipt owns "same request replays"; a new key is a new run
    intention, so the user can try again (for example after fixing nothing but a
    transient platform failure). The second attempt is still refused with the
    same deterministic code for the same bad bytes.
    """
    owner = owner_session(t030_stack)
    artifact_id = t030.upload_ready_artifact(
        t030_stack, owner, b"order_id,date,region,amount\nA1,2026-01-01,x,-1.00\n"
    )
    first = t030.create_tool_run(owner, artifact_id, key="t030-failed-first")
    assert first.status == 202
    t030.run_tool_pools_once(t030_stack)
    failed = t030.wait_for_run_state(owner, first.json["id"], {"failed"})
    assert failed["error_code"] == "negative_amount"

    second = t030.create_tool_run(owner, artifact_id, key="t030-failed-rerun")
    assert second.status == 202, second.text
    assert second.json["id"] != first.json["id"]
    assert t030.count_tool_runs(t030_stack.dbname) == 2
    t030.run_tool_pools_once(t030_stack)
    again = t030.wait_for_run_state(owner, second.json["id"], {"failed"})
    assert again["error_code"] == "negative_amount"

    # The replay of the first create still answers from its recorded receipt and
    # never creates a third job.
    replay = t030.create_tool_run(owner, artifact_id, key="t030-failed-first")
    assert replay.status == 202
    assert replay.json["id"] == first.json["id"]
    assert replay.json["state"] == "ready"  # recorded 202 body
    assert t030.count_tool_runs(t030_stack.dbname) == 2
