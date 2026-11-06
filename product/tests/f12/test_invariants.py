"""Cross-endpoint invariants: the part a 106-row authorization matrix cannot see.

Each test reads the one gate run and asserts the *product* behavior the contract
fixes. A failure here is a conformance finding, not a test bug: the report lists
the same failure with the observed status/code so it can be fixed or waived
explicitly.
"""

from __future__ import annotations

import json
from pathlib import Path

import f12_api_gate as gate

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = PRODUCT_ROOT / "contracts" / "openapi.json"

REQUIRED_INVARIANTS = {
    "error_envelope",
    "pagination",
    "etag_if_match",
    "idempotency",
    "sse_frames",
    "uuid_server_generated",
    "utc_rfc3339",
    "decimal_strings",
    "no_store",
    "no_generic_crud",
}


def _invariant(report: dict, name: str) -> dict:
    assert name in report["invariants"], f"the gate never checked {name}"
    return report["invariants"][name]


def test_the_gate_checked_every_required_invariant(f12_report: dict) -> None:
    assert set(f12_report["invariants"]) >= REQUIRED_INVARIANTS
    for name in REQUIRED_INVARIANTS:
        entry = _invariant(f12_report, name)
        assert entry["checked"] is True or entry.get("note"), name


def test_every_failed_invariant_reaches_the_verdict(f12_report: dict) -> None:
    failed = [
        name
        for name in REQUIRED_INVARIANTS
        if not _invariant(f12_report, name)["passed"]
    ]
    summary_failed = {entry["invariant"] for entry in f12_report["summary"]["invariant_failures"]}
    assert set(failed) == summary_failed
    if failed:
        assert f12_report["summary"]["verdict"] == "failed"


def test_error_envelope_including_the_mux_404_and_405(f12_report: dict) -> None:
    entry = _invariant(f12_report, "error_envelope")
    assert entry["passed"], entry["failures"][:5]
    runtime = entry["observations"][0]
    assert runtime["unknown_path"]["status"] == 404
    assert runtime["unknown_path"]["envelope_ok"] is True
    assert runtime["unknown_path"]["error_code"] == "not_found"
    assert runtime["unknown_method"]["status"] == 405
    assert runtime["unknown_method"]["envelope_ok"] is True
    assert runtime["unknown_method"]["error_code"] == "method_not_allowed"


def test_pagination_is_uniform_and_never_clamps(f12_report: dict) -> None:
    entry = _invariant(f12_report, "pagination")
    assert entry["passed"], entry["failures"][:5]
    rows = {row["operation_id"]: row for row in entry["observations"]}
    assert len(rows) == 14
    assert rows["listMemories"]["cursor_round_trip"]["items"] == 1
    for row in rows.values():
        assert row["limit_1"]["has_items"] is True
        assert row["limit_1"]["has_next_cursor"] is True
        for bad in ("101", "0", "abc"):
            assert row[f"limit_{bad}"]["status"] == 422
            assert row[f"limit_{bad}"]["field"] == "limit"


def test_draft_and_memory_etag_contract(f12_report: dict) -> None:
    entry = _invariant(f12_report, "etag_if_match")
    assert entry["passed"], entry["failures"][:5]
    rows = {}
    for observation in entry["observations"]:
        if "missing_if_match" in observation:
            rows[observation["resource"]] = observation
    for resource in ("draft", "memory"):
        assert rows[resource]["missing_if_match"]["status"] == 428
        assert rows[resource]["stale_if_match"]["status"] == 412
    # The cancellation path must stay ETag-free; a 428 there would be a
    # regression. It is not probed with If-Match, and its success is asserted in
    # the normal-path record.
    cancel = next(
        record
        for record in f12_report["operations"]
        if record["operation_id"] == "cancelRun"
    )
    assert cancel["normal_path"]["status"] == 202


def test_idempotency_key_semantics(f12_report: dict) -> None:
    entry = _invariant(f12_report, "idempotency")
    assert entry["passed"], entry["failures"][:5]
    case = next(row for row in entry["observations"] if row.get("case") == "debugDraft")
    assert case["first"]["status"] == 202
    assert case["replay"]["run_id"] == case["first"]["run_id"]
    assert case["same_key_different_body"]["status"] == 409
    missing = next(row for row in entry["observations"] if "missing_idempotency_key" in row)
    assert len(missing["missing_idempotency_key"]) == 11
    for operation_id, row in missing["missing_idempotency_key"].items():
        assert row["status"] in (400, 422), operation_id
        assert (
            row["details"].get("header") == "Idempotency-Key"
            or row["details"].get("field") == "Idempotency-Key"
        ), operation_id
    # The detail shape is recorded explicitly so the report can state the two
    # documented families rather than silently normalising them.
    assert len(entry["observations"]) >= 3


def _media_type(value: str) -> str:
    """The media type without its parameters: charset is a legal addition."""
    return value.split(";", 1)[0].strip().lower()


def test_sse_frames_carry_id_event_and_the_event_schema_envelope(f12_report: dict) -> None:
    entry = _invariant(f12_report, "sse_frames")
    assert entry["passed"], entry["failures"][:5]
    observations = entry["observations"]
    # `text/event-stream; charset=utf-8` is the same media type; the parameter is
    # allowed by RFC 9110 and by the contract.
    assert _media_type(observations[0]["content_type"]) == "text/event-stream"
    frames = observations[0]["frames"]
    assert frames, "the run-events stream produced no frame"
    seqs = [frame["id"] for frame in frames if frame["id"] is not None]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    for frame in frames:
        assert isinstance(frame["event"], str) and frame["event"]
        assert frame["schema_ok"] is True, frame["schema_error"]
        payload = frame["data"]
        assert payload["schema_version"] == "1.0"
        assert payload["run_id"] == f12_report["fixture_ids"]["run_id"]
    # The 410 retention branch is honestly recorded as not_run, not as passed.
    assert any("410" in item for item in entry.get("not_run", []))


def test_uuid_utc_decimal_and_no_store(f12_report: dict) -> None:
    for name in ("uuid_server_generated", "utc_rfc3339", "decimal_strings", "no_store"):
        entry = _invariant(f12_report, name)
        assert entry["checked"] is True, name
        assert entry["passed"], (name, entry["failures"][:5])
    # The counters report how many fields were examined, not how many failures
    # occurred: a "checked" invariant over zero fields would be a false green.
    assert _invariant(f12_report, "uuid_server_generated")["observations"][0][
        "id_fields_checked"
    ] > 0
    assert _invariant(f12_report, "utc_rfc3339")["observations"][0][
        "timestamp_fields_checked"
    ] > 0
    assert _invariant(f12_report, "decimal_strings")["observations"][0][
        "money_fields_checked"
    ] > 0


def test_no_generic_crud_fallback(f12_report: dict) -> None:
    entry = _invariant(f12_report, "no_generic_crud")
    assert entry["passed"], entry["failures"][:5]
    runtime = entry["observations"][0]["runtime"]
    assert runtime["unknown_path"]["status"] == 404
    assert runtime["unknown_method"]["status"] == 405
    assert runtime["unowned_resource"]["status"] == 404
    static = entry["observations"][1]["static_registry"]
    assert static["missing_registrations"] == []
    assert static["generic_patterns"] == []
    assert static["contract_operations"] == 106


def test_the_report_does_not_claim_a_pass_for_an_unchecked_invariant(f12_report: dict) -> None:
    for name, entry in f12_report["invariants"].items():
        if not entry["checked"]:
            assert entry["passed"] is False, name
        if entry["failures"]:
            assert entry["passed"] is False, name
    # `not_run` is explicit for the SSE retention branch instead of being a skip.
    assert f12_report["summary"]["not_run"]
