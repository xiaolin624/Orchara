"""T104 / F13B: deterministic builder for the imported tau2-bench subset.

This module is the human-authored source of truth for
``product/tests/f13b/external``. It defines:

* the three adapted evaluation cases (``tau2/tasks.json``), each valid against
  ``contracts/evaluation-case.schema.json``;
* the three committed local booking fixtures the adapted cases run against;
* ``registry.json``: the source pin, per-file pins, family/template table,
  per-case truth/adaptation/licence/environment/action-trajectory descriptions.

The upstream source pin (``tau2/source_pin.json``) and the upstream MIT licence
text (``tau2/LICENSE.tau2-mit.txt``) are acquired evidence, not generated: the
builder validates them and pins their bytes. It refuses to build when the pin
does not match the recorded commit/licence/hash facts or when an adapted case
would fail the import gate.

``build()`` returns the exact bytes of the generated documents, so
``test_external_bytes_are_reproducible`` catches any hand edit that did not go
through this builder. Regenerate with::

    python tests/f13b/build_external.py --write

Nothing here executes tau2-bench or the product: the cases are declarative, the
fixtures are local JSON and no model, database or sandbox is involved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
EXTERNAL_DIR = Path(__file__).resolve().parent / "external"
TOOLS = PRODUCT_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import f13b_external as ext  # noqa: E402

TARGET = {"kind": "debug_snapshot", "id": "tau2-airline-adapted-fixture"}
POLICY_REF = "tau2-airline-adapted-policy-1"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _task39_fixture() -> dict:
    """Seven bookings whose refund eligibility is derivable from the policy."""
    return {
        "fixture_version": "tau2-airline-adapted/1",
        "source_task": "39",
        "policy": {
            "policy_id": "refund-eligibility-adapted-1",
            "refundable_if_any": [
                "booked_hours_ago <= 24",
                "has_insurance and passenger_illness",
                "flight_cancelled_by_airline",
            ],
            "never_refundable_if_any": [
                "cabin == basic_economy",
                "flight_departed",
            ],
        },
        "bookings": [
            {
                "reservation_id": "8C8K4E",
                "status": "confirmed",
                "cabin": "economy",
                "booked_hours_ago": 10,
                "has_insurance": False,
                "passenger_illness": False,
                "flight_cancelled_by_airline": False,
                "flight_departed": False,
                "refund_amount": "420.50",
            },
            {
                "reservation_id": "UDMOP1",
                "status": "confirmed",
                "cabin": "economy",
                "booked_hours_ago": 72,
                "has_insurance": False,
                "passenger_illness": False,
                "flight_cancelled_by_airline": False,
                "flight_departed": False,
                "refund_amount": "0.00",
            },
            {
                "reservation_id": "XAZ3C0",
                "status": "confirmed",
                "cabin": "basic_economy",
                "booked_hours_ago": 5,
                "has_insurance": False,
                "passenger_illness": False,
                "flight_cancelled_by_airline": False,
                "flight_departed": False,
                "refund_amount": "0.00",
            },
            {
                "reservation_id": "LU15PA",
                "status": "confirmed",
                "cabin": "economy",
                "booked_hours_ago": 60,
                "has_insurance": True,
                "passenger_illness": True,
                "flight_cancelled_by_airline": False,
                "flight_departed": False,
                "refund_amount": "315.25",
            },
            {
                "reservation_id": "MSJ4OA",
                "status": "confirmed",
                "cabin": "economy",
                "booked_hours_ago": 48,
                "has_insurance": False,
                "passenger_illness": False,
                "flight_cancelled_by_airline": True,
                "flight_departed": False,
                "refund_amount": "260.00",
            },
            {
                "reservation_id": "I6M8JQ",
                "status": "confirmed",
                "cabin": "economy",
                "booked_hours_ago": 30,
                "has_insurance": False,
                "passenger_illness": False,
                "flight_cancelled_by_airline": False,
                "flight_departed": True,
                "refund_amount": "0.00",
            },
            {
                "reservation_id": "4XGCCM",
                "status": "confirmed",
                "cabin": "economy",
                "booked_hours_ago": 48,
                "has_insurance": False,
                "passenger_illness": False,
                "flight_cancelled_by_airline": False,
                "flight_departed": False,
                "refund_amount": "0.00",
            },
        ],
    }


def _task11_fixture() -> dict:
    """One booking whose fare difference is exactly the source task's refund."""
    return {
        "fixture_version": "tau2-airline-adapted/1",
        "source_task": "11",
        "policy": {
            "policy_id": "fare-difference-adapted-1",
            "refund_rule": "downgrade the whole booking; refund fare_paid - fare_after_change to the original payment method",
            "passenger_count_may_not_change": True,
        },
        "booking": {
            "reservation_id": "GV1N64",
            "status": "confirmed",
            "cabin": "economy",
            "flights": [
                {"flight_number": "HAT003", "date": "2024-05-19"},
                {"flight_number": "HAT290", "date": "2024-05-20"},
            ],
            "passengers": [
                {"first_name": "James", "last_name": "Patel", "dob": "1982-05-30"},
                {"first_name": "Sophia", "last_name": "Patel", "dob": "1985-08-14"},
            ],
            "payment_id": "gift_card_1642017",
            "fare_paid": "12880.00",
            "fare_after_change": "7636.00",
        },
    }


def _task40_fixture() -> dict:
    """One booking whose second passenger is the source task's correction target."""
    return {
        "fixture_version": "tau2-airline-adapted/1",
        "source_task": "40",
        "policy": {
            "policy_id": "passenger-correction-adapted-1",
            "rule": "one passenger name correction on an existing booking; the rest of the booking is unchanged",
        },
        "booking": {
            "reservation_id": "3RK2T9",
            "status": "confirmed",
            "cabin": "economy",
            "flights": [{"flight_number": "HAT117", "date": "2024-05-18"}],
            "passengers": [
                {"first_name": "Anya", "last_name": "Garcia", "dob": "1992-11-12"},
                {"first_name": "Mei", "last_name": "Lee", "dob": "1989-12-13"},
            ],
            "payment_id": "credit_card_4456188",
            "fare_paid": "540.00",
        },
    }


FIXTURE_BUILDERS = {
    "tau2-airline-task39-bookings": (
        "tau2/fixtures/tau2_airline_task39_bookings.json",
        _task39_fixture,
    ),
    "tau2-airline-task11-booking": (
        "tau2/fixtures/tau2_airline_task11_booking.json",
        _task11_fixture,
    ),
    "tau2-airline-task40-booking": (
        "tau2/fixtures/tau2_airline_task40_booking.json",
        _task40_fixture,
    ),
}


def fixture_entries() -> dict[str, dict]:
    """The registry fixture entries, with their committed bytes hashed."""
    entries: dict[str, dict] = {}
    for fixture_id, (relative, builder) in FIXTURE_BUILDERS.items():
        payload = _dump(builder())
        entries[fixture_id] = {
            "kind": "file",
            "path": relative,
            "media_type": "application/json",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "description": {
                "tau2-airline-task39-bookings": (
                    "Seven local bookings with the fields the adapted refund policy "
                    "derives eligibility from; no tau2 database or tooling is used."
                ),
                "tau2-airline-task11-booking": (
                    "One local booking with the passengers and fares the adapted "
                    "downgrade/refund case asserts on."
                ),
                "tau2-airline-task40-booking": (
                    "One local booking with the passenger list the adapted name "
                    "correction asserts on."
                ),
            }[fixture_id],
        }
    return entries


def fixture_bytes(entry: dict) -> bytes:
    """The deterministic bytes of one fixture entry (by its registry entry)."""
    for fixture_id, (relative, builder) in FIXTURE_BUILDERS.items():
        if entry.get("path") == relative:
            return _dump(builder())
    raise KeyError(f"no fixture builder for {entry.get('path')!r}")


# ---------------------------------------------------------------------------
# adapted cases
# ---------------------------------------------------------------------------
def _source_block(pin: dict, task_id: str) -> dict:
    pinned = None
    for item in pin["tasks"]:
        if item["task_id"] == task_id:
            pinned = item
            break
    if pinned is None:
        raise AssertionError(f"task {task_id!r} is not pinned")
    return {
        "repo": ext.REPO,
        "commit": ext.PINNED_COMMIT,
        "task_id": task_id,
        "path": ext.SOURCE_PATH,
        "canonical_sha256": pinned["canonical_sha256"],
        "criteria_sha256": pinned["criteria_sha256"],
        "purpose": pinned["purpose"],
        "selected_because": pinned["selected_because"],
    }


def _provenance(task_id: str) -> dict:
    return {
        "origin": "adapted_external",
        "reference": (
            f"{ext.REPO}@{ext.PINNED_COMMIT}#task={task_id}#{ext.SOURCE_PATH}"
        ),
        "license_review": "reviewed",
    }


def _license_block(task_id: str) -> dict:
    return {
        "origin": "adapted_external",
        "license_id": ext.LICENSE_ID,
        "redistributable": True,
        "review": "reviewed",
        "notes": (
            f"Adapted from {ext.REPO} at {ext.PINNED_COMMIT} (MIT, see "
            "tau2/source_pin.json and tau2/LICENSE.tau2-mit.txt). The adapted "
            "case, fixture and actions are new Orchara data."
        ),
    }


def _environment(fixture_id: str, writes: list[tuple[str, str]]) -> dict:
    endpoints = [
        {
            "method": "GET",
            "path": "/customers/{customer_id}",
            "capability": "booking.get_customer",
        },
        {
            "method": "GET",
            "path": "/bookings/{reservation_id}",
            "capability": "booking.get_reservation",
        },
    ]
    for method, path_and_capability in writes:
        path, capability = path_and_capability.split("#", 1)
        endpoints.append({"method": method, "path": path, "capability": capability})
    return {
        "kind": "local_http_fixture",
        "fixture_id": fixture_id,
        "base_path": "/booking-desk",
        "endpoints": endpoints,
    }


def _adaptation(task_id: str, purpose: str) -> dict:
    return {
        "summary": (
            f"Rewritten from tau2-bench airline task {task_id} into an isolated "
            "corporate booking fixture with a local HTTP endpoint surface; "
            "tau2's user simulator, tools and database are not reused."
        ),
        "preserved": [
            "the upstream reference action trajectory (lookups plus write actions)",
            "the upstream DB end-state idea: which reservations may change and which must not",
            "the upstream reward_basis intent: database/environment truth rather than answer text",
            f"the upstream task purpose: {purpose}",
        ],
        "changed": [
            "user scenario rewritten as one Orchara booking-desk request",
            "airline ids and passenger names kept as local fixture data",
            "the DB-hash check replaced by per-booking status and refund_total assertions",
            "nl_assertions recorded as adaptation notes, never used as a checker",
        ],
        "not_preserved": [
            "the tau2 user simulator and its LLM calls",
            "the tau2 airline environment, tools and db.json",
            "LLM-judged nl_assertions and communicate matching",
        ],
    }


def _read_actions(*reservation_ids: str) -> list[dict]:
    return [
        {"name": "get_reservation", "arguments": {"reservation_id": reservation_id}}
        for reservation_id in reservation_ids
    ]


def _task39_case(pin: dict) -> tuple[dict, dict]:
    case_id = "tau2-airline-39-refund-eligibility"
    family_id = "tau2-airline-refund-eligibility"
    fixture_id = "tau2-airline-task39-bookings"
    template = "cancel-only-the-reservations-whose-refund-policy-applies"
    scenario = (
        "A customer asks to cancel every upcoming reservation; only the three "
        "reservations whose fixture fields meet the refund policy may be "
        "cancelled, and only those add to the refund total."
    )
    actions = [
        {"name": "get_customer", "arguments": {"customer_id": "amelia_davis_8890"}},
        *_read_actions(
            "8C8K4E", "UDMOP1", "XAZ3C0", "LU15PA", "MSJ4OA", "I6M8JQ", "4XGCCM"
        ),
        *[
            {
                "name": "cancel_reservation",
                "arguments": {"reservation_id": reservation_id},
                "capability": "booking.cancel",
            }
            for reservation_id in ("8C8K4E", "LU15PA", "MSJ4OA")
        ],
    ]
    case = {
        "schema_version": "1.0",
        "case_id": case_id,
        "category": "approval_effects",
        "family_id": family_id,
        "split": "frozen_test",
        "target": dict(TARGET),
        "input": {
            "channel": "corporate-booking-desk",
            "customer": {
                "customer_id": "amelia_davis_8890",
                "display_name": "Amelia Davis",
            },
            "request": {
                "intent": "cancel_all_upcoming_reservations",
                "utterance": (
                    "Please cancel all of my upcoming reservations today. I "
                    "understand that not all of them will be refundable."
                ),
            },
            "policy_ref": POLICY_REF,
        },
        "fixture_id": fixture_id,
        "assertions": [
            {"kind": "run_state", "expected": "succeeded"},
            {
                "kind": "effect_count",
                "capability": "booking.cancel",
                "expected": 3,
                "source": "stub_ledger",
            },
            *[
                {
                    "kind": "json_equals",
                    "pointer": f"/bookings/{reservation_id}/status",
                    "expected": "cancelled",
                }
                for reservation_id in ("8C8K4E", "LU15PA", "MSJ4OA")
            ],
            *[
                {
                    "kind": "json_equals",
                    "pointer": f"/bookings/{reservation_id}/status",
                    "expected": "confirmed",
                }
                for reservation_id in ("UDMOP1", "XAZ3C0", "I6M8JQ", "4XGCCM")
            ],
            {"kind": "decimal_equals", "pointer": "/refund_total", "expected": "995.75"},
        ],
        "provenance": _provenance("39"),
    }
    meta = {
        "category": case["category"],
        "family_id": family_id,
        "split": case["split"],
        "template": template,
        "scenario": scenario,
        "provenance": case["provenance"],
        "fixture_id": fixture_id,
        "assertion_kinds": sorted({item["kind"] for item in case["assertions"]}),
        "truth": {
            "source": "adapted_source_actions_and_committed_fixture",
            "derivation": (
                "Recompute refund eligibility from the committed fixture policy "
                "(booked_hours_ago <= 24, insurance plus illness, or an "
                "airline-cancelled flight; basic_economy and departed flights are "
                "excluded). The three eligible reservation ids equal the upstream "
                "cancel_reservation actions 39_8..39_10, and refund_total is the "
                "Decimal sum of their fixture refund_amount values (995.75)."
            ),
        },
        "required_facts": [],
        "license": _license_block("39"),
        "source": _source_block(pin, "39"),
        "adaptation": _adaptation(
            "39",
            "only reservations whose refund policy applies may be cancelled",
        ),
        "environment": _environment(
            fixture_id, [("POST", "/bookings/{reservation_id}/cancel#booking.cancel")]
        ),
        "adapted_actions": actions,
        "label": ext.USAGE_LABEL,
    }
    return case, meta


def _task11_case(pin: dict) -> tuple[dict, dict]:
    case_id = "tau2-airline-11-flight-downgrade"
    family_id = "tau2-airline-flight-change-refund"
    fixture_id = "tau2-airline-task11-booking"
    template = "downgrade-the-whole-booking-without-changing-the-passenger-count"
    scenario = (
        "A customer asks to remove one passenger; policy forbids changing the "
        "passenger count, so the whole booking is downgraded to basic economy and "
        "the exact fare difference is refunded."
    )
    flights = [
        {"flight_number": "HAT003", "date": "2024-05-19"},
        {"flight_number": "HAT290", "date": "2024-05-20"},
    ]
    passengers = [
        {"first_name": "James", "last_name": "Patel", "dob": "1982-05-30"},
        {"first_name": "Sophia", "last_name": "Patel", "dob": "1985-08-14"},
    ]
    actions = [
        {
            "name": "update_reservation_flights",
            "arguments": {
                "reservation_id": "GV1N64",
                "cabin": "basic_economy",
                "flights": flights,
                "payment_id": "gift_card_1642017",
            },
            "capability": "booking.update_flights",
        },
    ]
    case = {
        "schema_version": "1.0",
        "case_id": case_id,
        "category": "approval_effects",
        "family_id": family_id,
        "split": "frozen_test",
        "target": dict(TARGET),
        "input": {
            "channel": "corporate-booking-desk",
            "customer": {
                "customer_id": "james_patel_9828",
                "display_name": "James Patel",
            },
            "request": {
                "intent": "remove_one_passenger_then_downgrade",
                "utterance": (
                    "I want to take one passenger off reservation GV1N64. If the "
                    "number of passengers cannot change, downgrade every passenger "
                    "to basic economy and refund the difference to my gift card."
                ),
            },
            "policy_ref": POLICY_REF,
        },
        "fixture_id": fixture_id,
        "assertions": [
            {"kind": "run_state", "expected": "succeeded"},
            {
                "kind": "effect_count",
                "capability": "booking.update_flights",
                "expected": 1,
                "source": "stub_ledger",
            },
            {
                "kind": "effect_count",
                "capability": "booking.update_passengers",
                "expected": 0,
                "source": "stub_ledger",
            },
            {
                "kind": "json_equals",
                "pointer": "/bookings/GV1N64/cabin",
                "expected": "basic_economy",
            },
            {
                "kind": "json_equals",
                "pointer": "/bookings/GV1N64/flights",
                "expected": flights,
            },
            {
                "kind": "json_equals",
                "pointer": "/bookings/GV1N64/passengers",
                "expected": passengers,
            },
            {"kind": "decimal_equals", "pointer": "/refund_total", "expected": "5244.00"},
        ],
        "provenance": _provenance("11"),
    }
    meta = {
        "category": case["category"],
        "family_id": family_id,
        "split": case["split"],
        "template": template,
        "scenario": scenario,
        "provenance": case["provenance"],
        "fixture_id": fixture_id,
        "assertion_kinds": sorted({item["kind"] for item in case["assertions"]}),
        "truth": {
            "source": "adapted_source_actions_and_committed_fixture",
            "derivation": (
                "Apply the upstream reference action 11_0 to the committed "
                "fixture, which changes only the cabin and the flight list. "
                "refund_total is recomputed as Decimal(fare_paid 12880.00) - "
                "Decimal(fare_after_change 7636.00) = 5244.00, which equals the "
                "upstream communicate_info value 5244; the passenger list is "
                "asserted unchanged because the source task forbids removing a "
                "passenger."
            ),
        },
        "required_facts": [],
        "license": _license_block("11"),
        "source": _source_block(pin, "11"),
        "adaptation": _adaptation(
            "11",
            "a passenger cannot be removed; downgrade and refund the difference",
        ),
        "environment": _environment(
            fixture_id,
            [("POST", "/bookings/{reservation_id}/flights#booking.update_flights")],
        ),
        "adapted_actions": actions,
        "label": ext.USAGE_LABEL,
    }
    return case, meta


def _task40_case(pin: dict) -> tuple[dict, dict]:
    case_id = "tau2-airline-40-passenger-correction"
    family_id = "tau2-airline-passenger-correction"
    fixture_id = "tau2-airline-task40-booking"
    template = "correct-one-passenger-name-on-an-existing-booking"
    scenario = (
        "A customer asks to correct the second passenger's surname on an "
        "existing booking; the passenger list is the environment truth and the "
        "rest of the booking must not change."
    )
    passengers = [
        {"first_name": "Anya", "last_name": "Garcia", "dob": "1992-11-12"},
        {"first_name": "Mei", "last_name": "Garcia", "dob": "1989-12-13"},
    ]
    actions = [
        {"name": "get_reservation", "arguments": {"reservation_id": "3RK2T9"}},
        {
            "name": "update_reservation_passengers",
            "arguments": {"reservation_id": "3RK2T9", "passengers": passengers},
            "capability": "booking.update_passengers",
        },
    ]
    case = {
        "schema_version": "1.0",
        "case_id": case_id,
        "category": "approval_effects",
        "family_id": family_id,
        "split": "frozen_test",
        "target": dict(TARGET),
        "input": {
            "channel": "corporate-booking-desk",
            "customer": {
                "customer_id": "anya_garcia_5901",
                "display_name": "Anya Garcia",
            },
            "request": {
                "intent": "correct_passenger_name",
                "utterance": (
                    "Please change the passenger name on reservation 3RK2T9 from "
                    "Mei Lee to Mei Garcia."
                ),
            },
            "policy_ref": POLICY_REF,
        },
        "fixture_id": fixture_id,
        "assertions": [
            {"kind": "run_state", "expected": "succeeded"},
            {
                "kind": "effect_count",
                "capability": "booking.update_passengers",
                "expected": 1,
                "source": "stub_ledger",
            },
            {
                "kind": "effect_count",
                "capability": "booking.update_flights",
                "expected": 0,
                "source": "stub_ledger",
            },
            {
                "kind": "json_equals",
                "pointer": "/bookings/3RK2T9/passengers",
                "expected": passengers,
            },
            {
                "kind": "json_equals",
                "pointer": "/bookings/3RK2T9/cabin",
                "expected": "economy",
            },
            {
                "kind": "json_equals",
                "pointer": "/bookings/3RK2T9/status",
                "expected": "confirmed",
            },
        ],
        "provenance": _provenance("40"),
    }
    meta = {
        "category": case["category"],
        "family_id": family_id,
        "split": case["split"],
        "template": template,
        "scenario": scenario,
        "provenance": case["provenance"],
        "fixture_id": fixture_id,
        "assertion_kinds": sorted({item["kind"] for item in case["assertions"]}),
        "truth": {
            "source": "adapted_source_actions_and_committed_fixture",
            "derivation": (
                "Apply the upstream reference action 40_1 to the committed "
                "fixture: the first passenger is unchanged and the second becomes "
                "Mei Garcia (dob 1989-12-13), which is the full passenger list "
                "asserted by the adapted checker. No flight write may be recorded."
            ),
        },
        "required_facts": [],
        "license": _license_block("40"),
        "source": _source_block(pin, "40"),
        "adaptation": _adaptation(
            "40", "correct the second passenger's surname and keep the rest of the booking"
        ),
        "environment": _environment(
            fixture_id,
            [("POST", "/bookings/{reservation_id}/passengers#booking.update_passengers")],
        ),
        "adapted_actions": actions,
        "label": ext.USAGE_LABEL,
    }
    return case, meta


def _cases(pin: dict) -> list[tuple[dict, dict]]:
    return [_task39_case(pin), _task11_case(pin), _task40_case(pin)]


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def _dump(payload) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _self_check(pin: dict, pairs: list[tuple[dict, dict]], fixtures: dict[str, dict]) -> None:
    issues = ext.validate_source_pin(pin)
    if issues:
        raise AssertionError(
            "the committed source pin failed its own gate: "
            + "; ".join(str(issue) for issue in issues)
        )
    for case, meta in pairs:
        case_issues = ext.validate_adapted_case(case)
        if case_issues:
            raise AssertionError(
                f"adapted case {case['case_id']} failed the import gate: "
                + "; ".join(str(issue) for issue in case_issues)
            )
        if meta["source"]["canonical_sha256"] not in {
            item["canonical_sha256"] for item in pin["tasks"]
        }:
            raise AssertionError(f"{case['case_id']}: source task hash is not pinned")
        if meta["fixture_id"] not in fixtures:
            raise AssertionError(f"{case['case_id']}: fixture is not registered")
        meta["required_facts"] = ext.required_facts(case)


def build() -> dict[str, bytes]:
    """The exact committed bytes of the generated external documents."""
    pin_bytes = ext.PIN_PATH.read_bytes()
    license_bytes = ext.LICENSE_PATH.read_bytes()
    pin = json.loads(pin_bytes.decode("utf-8"))
    if hashlib.sha256(license_bytes).hexdigest() != pin["license"]["sha256"]:
        raise AssertionError("the committed MIT licence does not match the source pin")

    fixtures = fixture_entries()
    pairs = _cases(pin)
    _self_check(pin, pairs, fixtures)

    case_ids = [case["case_id"] for case, _meta in pairs]
    if len(case_ids) != len(set(case_ids)):
        raise AssertionError("the builder produced a duplicate case id")
    used_families = {case["family_id"] for case, _meta in pairs}
    families: dict[str, dict] = {}
    for case, meta in pairs:
        family = families.setdefault(
            case["family_id"],
            {
                "category": case["category"],
                "split": case["split"],
                "template": meta["template"],
            },
        )
        if family["template"] != meta["template"]:
            raise AssertionError(f"family {case['family_id']} has two templates")
    if used_families != set(families):
        raise AssertionError("unused family")

    documents = {"cases": [case for case, _meta in pairs]}
    tasks_payload = _dump(documents)
    files: dict[str, bytes] = {"tau2/tasks.json": tasks_payload}
    for fixture_id, (relative, builder) in FIXTURE_BUILDERS.items():
        payload = _dump(builder())
        if hashlib.sha256(payload).hexdigest() != fixtures[fixture_id]["sha256"]:
            raise AssertionError(f"fixture {fixture_id} is not deterministic")
        files[relative] = payload

    categories: dict[str, dict] = {}
    for case, _meta in pairs:
        data = categories.setdefault(
            case["category"],
            {"cases": 0, "dev": 0, "frozen_test": 0, "families": []},
        )
        data["cases"] += 1
        data[case["split"]] += 1
        if case["family_id"] not in data["families"]:
            data["families"].append(case["family_id"])
    for data in categories.values():
        data["families"].sort()

    registry = {
        "schema": ext.REGISTRY_SCHEMA,
        "generated_by": "tests/f13b/build_external.py",
        "usage_label": ext.USAGE_LABEL,
        "source_pin": {
            "path": "tau2/source_pin.json",
            "sha256": hashlib.sha256(pin_bytes).hexdigest(),
            "commit": ext.PINNED_COMMIT,
        },
        "license": {
            "spdx": ext.LICENSE_SPDX,
            "license_id": ext.LICENSE_ID,
            "license_file": "tau2/LICENSE.tau2-mit.txt",
            "sha256": hashlib.sha256(license_bytes).hexdigest(),
            "redistributable": True,
            "review": "reviewed",
            "notes": (
                "The upstream repository root LICENSE is MIT. Only the root "
                "licence was reviewed; per-domain or third-party data was not "
                "re-audited, so the import keeps a pin rather than a claim."
            ),
        },
        "corpus_reference": {
            "registry_path": "product/tests/f13b/corpus/registry.json",
            "isolation_rule": (
                "The imported cases are frozen_test. Their families, normalized "
                "templates and near duplicates must not overlap the T102 corpus "
                "dev split; the T102 registry pins this file's SHA-256."
            ),
        },
        "collection": {
            "required_facts": list(ext.COLLECTION_FACTS),
            "rule": (
                "An imported case may be scored only when every required fact was "
                "collected. An empty, skipped or incomplete case is not_run, never "
                "passed."
            ),
        },
        "files": {
            "tau2/tasks.json": {
                "sha256": hashlib.sha256(tasks_payload).hexdigest(),
                "size_bytes": len(tasks_payload),
                "cases": len(pairs),
            },
            "tau2/source_pin.json": {
                "sha256": hashlib.sha256(pin_bytes).hexdigest(),
                "size_bytes": len(pin_bytes),
            },
            "tau2/LICENSE.tau2-mit.txt": {
                "sha256": hashlib.sha256(license_bytes).hexdigest(),
                "size_bytes": len(license_bytes),
            },
            **{
                fixture["path"]: {
                    "sha256": fixture["sha256"],
                    "size_bytes": fixture["size_bytes"],
                }
                for fixture in fixtures.values()
            },
        },
        "families": families,
        "categories": categories,
        "fixtures": fixtures,
        "cases": {case["case_id"]: meta for case, meta in pairs},
    }
    files["registry.json"] = _dump(registry)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="rewrite the committed external import")
    args = parser.parse_args(argv)
    built = build()
    if args.write:
        EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
        for relative, payload in sorted(built.items()):
            target = EXTERNAL_DIR / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            print(f"wrote {target} ({len(payload)} bytes)")
        return 0
    drifted = [
        relative
        for relative, payload in sorted(built.items())
        if (EXTERNAL_DIR / relative).read_bytes() != payload
    ]
    if drifted:
        print("external import drift: " + ", ".join(drifted))
        return 1
    print("external import matches the builder")
    return 0


if __name__ == "__main__":
    sys.exit(main())
