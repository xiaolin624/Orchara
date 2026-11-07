#!/usr/bin/env python3
"""F13B/T103: compare a deterministic hard checker with a quality score.

One sealed evaluation report in; one machine-readable comparison out:

* ``hard_checker`` recomputes the verdict **from the report's assertions and
  facts**, never from its declared ``status`` and never from ``quality``. It is
  an independent Python reimplementation of the documented hard rules
  (``product/internal/evaluation/report.go`` and ``assertions.go`` are the
  reference, not a dependency). The declared status is compared afterwards.
* ``quality`` runs ``heuristic-local/1``: a deterministic local proxy score over
  the *same* recorded evidence (completeness, evaluated assertions,
  explainability). It is **not** an approved judge and it is never an input to
  the hard verdict. A real judge run is ``not_run`` because no approved judge
  endpoint or credential exists on this host (``ORCHARA_PROFILE=dev-stub`` only).
* ``outbound`` counts non-loopback socket connects made while the local score was
  computed; ``cost`` reports zero vendor tokens/USD because no vendor was called.
* ``gate`` is the observed publication-gate decision when the caller supplies the
  raw response (``--gate-result``); otherwise ``not_observed``.

The probe has no database, HTTP or product imports. It cannot launder a failed
hard check into a pass: the quality score is computed after the hard verdict and
is never read back into it.

Usage::

    python probes/f13b_quality/compare.py --report sealed-report.json --json
    python probes/f13b_quality/compare.py --report r.json --gate-result response.json --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SCHEMA = "orchara-t103-quality-compare/1"
HARD_VERSION = "t103-independent-hard/1"
QUALITY_ADAPTER = "heuristic-local/1"
COMPLETED_RUN_STATES = {"succeeded", "failed"}
CASE_REST_STATES = {"succeeded", "failed", "cancelled", "needs_reconciliation", "recovery_pending"}
#: The assertion kinds this probe can recompute. An unknown kind is a hard fail
#: (an unsupported checker is never a pass), matching the deterministic checker.
SUPPORTED_KINDS = {
    "run_state", "effect_count", "json_equals", "decimal_equals",
    "citation_sources", "failure_reason",
}
NOT_COLLECTED = "not_collected"
JUDGE_NOT_RUN_REASON = (
    "no approved judge endpoint or credential exists on this host; the only model "
    "profile is ORCHARA_PROFILE=dev-stub, so a semantic judge run is not_run"
)
#: Documented boundary of the local proxy: it measures evidence completeness and
#: explainability, not answer correctness. Correctness is the hard checker's job.
QUALITY_RUBRIC = {
    "version": QUALITY_ADAPTER,
    "case_signals": {
        "run_recorded": "facts.run_id and facts.run_state are present",
        "assertions_evaluated": "every assertion kind is supported and has a collected actual",
        "effects_accounted": "facts.effect_counts is an object, or effect_evidence explains why not",
        "citations_accounted": "facts.citation_sources is a list, or the run produced no output to cite",
        "explained": "a passed case carries no refusal reason; a non-passed case carries reason + detail",
    },
    "aggregation": "mean of the five per-case booleans, then mean over cases",
    "explicitly_not_a_judge": True,
}


class OutboundCounter:
    """Count non-loopback socket connects made while scoring (never blocks them)."""

    def __init__(self) -> None:
        self.connections: list[str] = []
        self._original_connect = None
        self._original_connect_ex = None
        self._original_create = None

    @staticmethod
    def _local(host: Any) -> bool:
        if not isinstance(host, str):
            return False
        return host in {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}

    def _record(self, address: Any) -> None:
        if isinstance(address, tuple) and address:
            host = address[0]
            if self._local(host):
                return
        if isinstance(address, str) and self._local(address):
            return
        self.connections.append(str(address))

    def __enter__(self) -> "OutboundCounter":
        counter = self

        self._original_connect = socket.socket.connect
        self._original_connect_ex = socket.socket.connect_ex
        self._original_create = socket.create_connection

        def connect(sock, address, *args, **kwargs):  # type: ignore[no-untyped-def]
            counter._record(address)
            return counter._original_connect(sock, address, *args, **kwargs)

        def connect_ex(sock, address, *args, **kwargs):  # type: ignore[no-untyped-def]
            counter._record(address)
            return counter._original_connect_ex(sock, address, *args, **kwargs)

        def create_connection(address, *args, **kwargs):  # type: ignore[no-untyped-def]
            counter._record(address)
            return counter._original_create(address, *args, **kwargs)

        socket.socket.connect = connect  # type: ignore[assignment]
        socket.socket.connect_ex = connect_ex  # type: ignore[assignment]
        socket.create_connection = create_connection  # type: ignore[assignment]
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._original_connect is not None:
            socket.socket.connect = self._original_connect  # type: ignore[assignment]
        if self._original_connect_ex is not None:
            socket.socket.connect_ex = self._original_connect_ex  # type: ignore[assignment]
        if self._original_create is not None:
            socket.create_connection = self._original_create  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# hard checker: recompute from assertions / facts only
# ---------------------------------------------------------------------------
def _decimal_literal(value: str) -> bool:
    """The case-format decimal shape ``^-?\\d+(\\.\\d+)?$`` (no float ever enters)."""
    if not value:
        return False
    index = 1 if value[0] == "-" else 0
    digits = 0
    while index < len(value) and value[index].isdigit():
        index += 1
        digits += 1
    if digits == 0:
        return False
    if index == len(value):
        return True
    if value[index] != ".":
        return False
    index += 1
    fraction = 0
    while index < len(value) and value[index].isdigit():
        index += 1
        fraction += 1
    return fraction > 0 and index == len(value)


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, (int, float, Decimal)) and isinstance(right, (int, float, Decimal)):
        try:
            return Decimal(str(left)) == Decimal(str(right))
        except InvalidOperation:  # pragma: no cover - defensive
            return False
    return type(left) is type(right) and left == right


def _parse_json_literal(value: str) -> tuple[bool, Any]:
    try:
        return True, json.loads(value, parse_float=Decimal)
    except (TypeError, ValueError):
        return False, None


def recompute_assertion(outcome: dict) -> tuple[bool, str]:
    """Recompute one assertion outcome from its expected/actual evidence."""
    kind = outcome.get("kind")
    expected = outcome.get("expected")
    actual = outcome.get("actual")
    expected = "" if expected is None else str(expected)
    actual = "" if actual is None else str(actual)
    if kind not in SUPPORTED_KINDS:
        return False, "unsupported assertion kind; an unsupported checker is never a pass"
    if actual in ("", NOT_COLLECTED):
        return False, "the actual fact was not collected"
    if kind == "run_state":
        if actual not in CASE_REST_STATES:
            return False, "the actual state is not a run rest state"
        return actual == expected, "" if actual == expected else "the run reached a different state"
    if kind == "failure_reason":
        return actual == expected, "" if actual == expected else "a different failure reason was observed"
    if kind == "effect_count":
        try:
            return int(actual) == int(expected), ""
        except ValueError:
            return False, "the effect count is not an integer"
    if kind == "decimal_equals":
        if not _decimal_literal(actual):
            return False, "the actual value is not a decimal literal"
        if not _decimal_literal(expected):
            return False, "the expected value is not a decimal literal"
        return Decimal(actual) == Decimal(expected), ""
    if kind == "json_equals":
        ok_actual, actual_value = _parse_json_literal(actual)
        ok_expected, expected_value = _parse_json_literal(expected)
        if not ok_actual or not ok_expected:
            return False, "the JSON literal does not parse"
        equal = _json_equal(expected_value, actual_value)
        return equal, "" if equal else "the JSON value differs"
    # citation_sources: the outcome carries the allowed set as CSV in `expected`
    # and the cited ids as CSV in `actual`. The contract's require_at_least_one
    # flag is not part of the recorded outcome, so this recompute proves only
    # "no citation outside the allowed set"; it records that boundary.
    allowed = {item for item in expected.split(",") if item}
    cited = [item for item in actual.split(",") if item]
    outside = [item for item in cited if item not in allowed]
    if outside:
        return False, "cited sources outside the allowed set: " + ",".join(sorted(set(outside)))
    return True, "no citation outside the allowed set (require_at_least_one not recorded)"


def _case_recompute(case: dict) -> dict:
    assertions = case.get("assertions") or []
    facts = case.get("facts") or {}
    outcomes = []
    every_passed = True
    for outcome in assertions:
        passed, reason = recompute_assertion(outcome)
        recorded = outcome.get("passed")
        outcomes.append(
            {
                "kind": outcome.get("kind"),
                "expected": outcome.get("expected"),
                "actual": outcome.get("actual"),
                "recorded_passed": recorded,
                "recomputed_passed": passed,
                "agrees_with_recorded_outcome": isinstance(recorded, bool) and recorded == passed,
                "reason": reason,
            }
        )
        every_passed = every_passed and passed
    run_state = facts.get("run_state")
    run_completed = run_state in COMPLETED_RUN_STATES
    has_checker = len(assertions) > 0
    recomputed_passed = has_checker and every_passed and run_completed
    if not has_checker:
        detail = "the case carries no assertion evidence; an empty checker is not a pass"
    elif not every_passed:
        detail = "at least one declared assertion fails against the recorded evidence"
    elif not run_completed:
        detail = "the case's run did not finish; an unfinished run is never a pass"
    else:
        detail = "every declared assertion passed against a finished run"
    return {
        "id": case.get("id"),
        "declared_status": case.get("status"),
        "declared_reason_code": case.get("reason_code"),
        "run_state": run_state,
        "run_completed": run_completed,
        "assertion_count": len(assertions),
        "recomputed_status": "passed" if recomputed_passed else "failed",
        "detail": detail,
        "assertions": outcomes,
    }


def recompute_hard_verdict(document: dict) -> dict:
    cases = document.get("cases") or []
    recomputed_cases = [_case_recompute(case) for case in cases]
    declared_case_statuses = [str(case.get("status") or "") for case in cases]

    if not cases:
        verdict = "not_run"
    elif all(case["recomputed_status"] == "passed" for case in recomputed_cases):
        verdict = "passed"
    elif all(status in ("not_run", "blocked") for status in declared_case_statuses) and not all(
        case["recomputed_status"] == "passed" for case in recomputed_cases
    ):
        verdict = "not_run"
    else:
        verdict = "failed"

    failed_assertions = [
        {"case_id": case["id"], **outcome}
        for case in recomputed_cases
        for outcome in case["assertions"]
        if not outcome["recomputed_passed"]
    ]
    declared_status = document.get("status")
    return {
        "version": HARD_VERSION,
        "verdict": verdict,
        "declared_product_status": declared_status,
        "agrees_with_declared_status": verdict == declared_status,
        "recompute_source": "cases[].assertions + cases[].facts",
        "quality_used": False,
        "case_count": len(cases),
        "cases": recomputed_cases,
        "failed_assertions": failed_assertions,
    }


# ---------------------------------------------------------------------------
# quality: deterministic local proxy score (advisory only)
# ---------------------------------------------------------------------------
def _case_quality(case: dict) -> dict:
    facts = case.get("facts") or {}
    assertions = case.get("assertions") or []
    has_run = bool(facts.get("run_id")) and bool(facts.get("run_state"))
    assertions_evaluated = len(assertions) > 0 and all(
        outcome.get("kind") in SUPPORTED_KINDS
        and str(outcome.get("actual") or "") not in ("", NOT_COLLECTED)
        for outcome in assertions
    )
    effects_accounted = isinstance(facts.get("effect_counts"), dict) or bool(
        facts.get("effect_evidence")
    )
    output_absent = not facts.get("output_artifact_id")
    # The sealed document has no "citations collected" flag: an empty collected
    # citation set is a Go nil slice and marshals as null. So null is accounted
    # when there is no output to cite, or when a sealed output hash proves the
    # output (and therefore its citations) was collected.
    citations_accounted = (
        isinstance(facts.get("citation_sources"), list)
        or output_absent
        or bool(facts.get("output_sha256"))
    )
    declared_passed = case.get("status") == "passed"
    if declared_passed:
        explained = not case.get("reason_code")
    else:
        explained = bool(case.get("reason_code") and case.get("detail"))
    signals = [
        has_run,
        assertions_evaluated,
        effects_accounted,
        citations_accounted,
        explained,
    ]
    return {
        "case_id": case.get("id"),
        "signals": {
            "run_recorded": has_run,
            "assertions_evaluated": assertions_evaluated,
            "effects_accounted": effects_accounted,
            "citations_accounted": citations_accounted,
            "explained": explained,
        },
        "score": round(sum(1 for signal in signals if signal) / len(signals), 3),
    }


def compute_quality(document: dict) -> dict:
    cases = document.get("cases") or []
    per_case = [_case_quality(case) for case in cases]
    score = round(sum(case["score"] for case in per_case) / len(per_case), 3) if per_case else 0.0
    return {
        "adapter": QUALITY_ADAPTER,
        "score": score,
        "rubric": QUALITY_RUBRIC,
        "cases": per_case,
        "recorded_slot": document.get("quality"),
        "advisory_only": True,
        "judge_run": "not_run",
        "judge_blocked_reason": JUDGE_NOT_RUN_REASON,
    }


# ---------------------------------------------------------------------------
# observed publication gate
# ---------------------------------------------------------------------------
def read_gate_result(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    http_status = int(payload.get("http_status") or 0)
    body = payload.get("body")
    if body is None:
        raw = payload.get("raw_text") or ""
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
    code = None
    details = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            details = error.get("details")
        elif body.get("id"):
            code = None
    decision = "allowed" if 200 <= http_status < 300 else "refused"
    return {
        "decision": decision,
        "http_status": http_status,
        "code": code,
        "details": details,
        "source": payload.get("source", "raw HTTP response supplied by the caller"),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def build_result(document: dict, report_path: Path, gate_result: Path | None) -> dict:
    hard = recompute_hard_verdict(document)
    with OutboundCounter() as counter:
        quality = compute_quality(document)
    gate = read_gate_result(gate_result) if gate_result is not None else {
        "decision": "not_observed",
        "http_status": None,
        "code": None,
        "details": None,
        "source": "no --gate-result was supplied",
    }
    return {
        "schema": SCHEMA,
        "report": {
            "path": str(report_path),
            "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "report_version": document.get("report_version"),
            "kind": document.get("kind"),
            "evaluation_kind": document.get("evaluation_kind"),
            "evaluation_id": document.get("evaluation_id"),
            "evaluator": document.get("evaluator"),
            "case_count": len(document.get("cases") or []),
            "declared_status": document.get("status"),
            "manifest_sha256": document.get("manifest_sha256"),
            "dataset_sha256": document.get("dataset_sha256"),
            "quality_slot": document.get("quality"),
        },
        "hard_checker": hard,
        "quality": quality,
        "gate": gate,
        "outbound": {
            "connections": len(counter.connections),
            "targets": counter.connections,
            "observation": "non-loopback socket.connect/connect_ex/create_connection calls "
                           "made while the local quality score was computed",
        },
        "cost": {
            "vendor_tokens": 0,
            "usd": 0.0,
            "basis": "local CPU only; the heuristic adapter made no vendor call",
            "judge_cost": "not_run",
        },
        "verdict": {
            "hard": hard["verdict"],
            "quality": quality["score"],
            "quality_adapter": QUALITY_ADAPTER,
            "quality_can_override_hard": False,
            "gate": gate["decision"],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path, help="sealed evaluation report JSON")
    parser.add_argument("--gate-result", type=Path, default=None,
                        help="raw HTTP response JSON captured from the publication submit")
    parser.add_argument("--out", type=Path, default=None,
                        help="write the comparison JSON to this path (UTF-8, no BOM)")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    arguments = parser.parse_args(argv)

    document = json.loads(arguments.report.read_text(encoding="utf-8"))
    result = build_result(document, arguments.report, arguments.gate_result)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.out is not None:
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        arguments.out.write_text(encoded, encoding="utf-8", newline="\n")
    if arguments.json:
        print(encoded, end="")
        return 0
    print(
        f"hard={result['hard_checker']['verdict']} "
        f"(declared {result['hard_checker']['declared_product_status']}) "
        f"quality={result['quality']['score']} ({QUALITY_ADAPTER}) "
        f"gate={result['gate']['decision']} "
        f"outbound={result['outbound']['connections']} "
        f"vendor_tokens={result['cost']['vendor_tokens']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
