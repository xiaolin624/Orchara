"""Per-operation tests: one pytest case per OpenAPI operation.

The operation ids come from ``contracts/openapi.json`` at collection time, so a
contract change that adds or removes an operation is visible here too. Every
case fails if the gate has no record, if the record was silently skipped, or if
the operation is not implemented / its authorization boundary failed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import f12_api_gate as gate

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = PRODUCT_ROOT / "contracts" / "openapi.json"
OPERATIONS = {operation.operation_id: operation for operation in gate.load_operations(CONTRACT)}


@pytest.mark.parametrize("operation_id", sorted(OPERATIONS))
def test_operation_is_classified_and_has_an_authorization_record(
    f12_report: dict, operation_id: str
) -> None:
    record = next(
        item for item in f12_report["operations"] if item["operation_id"] == operation_id
    )
    # Route existence and functional verification are different claims (T093
    # review, G). A registered route whose only probe is the anonymous 401 is
    # `route_only`, not `implemented`.
    assert record["route_registered"] is True, (
        "the literal route registry does not register this operation"
    )
    assert record["implementation_status"] in {
        "functional",
        "route_only",
        "probe_failed",
    }, record["implementation_status"]
    if record["implementation_status"] == "functional":
        assert record["implemented"] is True
        assert record["functional_probe"]["checked"] is True
        assert record["functional_probe"]["ok"] is True
        assert record["functional_probe"]["status"] in OPERATIONS[operation_id].success_statuses
    else:
        assert record["implemented"] is False
        if record["implementation_status"] == "route_only":
            assert record["functional_probe"]["checked"] is False
    assert record["authorization_ok"] is True, record["failures"]
    assert record["verdict"] != "fail", record["failures"]
    assert record["probes"], "the operation has no probe record (a silent skip)"
    for probe in record["probes"]:
        assert probe["kind"] != "skip"
        if probe["status"] is None:
            # The only legitimate no-HTTP-response probe is the missing client
            # certificate on the internal listener.
            assert probe["transport_error"], probe


@pytest.mark.parametrize("operation_id", sorted(OPERATIONS))
def test_operation_records_cross_principal_content_isolation(
    f12_report: dict, operation_id: str
) -> None:
    record = next(
        item for item in f12_report["operations"] if item["operation_id"] == operation_id
    )
    isolation = record.get("cross_principal_isolation")
    assert isinstance(isolation, dict), (
        f"{operation_id} has no cross_principal_isolation record; the review requires "
        "an explicit content-isolation fact for every list/get operation"
    )
    if isolation.get("applicable"):
        if isolation.get("checked"):
            assert isolation["isolation_ok"] is True, isolation
            assert isolation["foreign_id_visible"] is False, isolation
            assert isolation["member_status"] in (200, 403, 404), isolation
        else:
            assert isolation.get("reason"), (
                f"{operation_id}: an applicable but unchecked isolation probe must "
                "state why it was not run"
            )
    else:
        assert isolation.get("reason"), (
            f"{operation_id}: an exempt read must state why isolation does not apply"
        )


def test_the_summary_reports_route_registration_separately_from_functional_probes(
    f12_report: dict,
) -> None:
    summary = f12_report["summary"]
    total = summary["operations"]
    assert summary["route_registered"] == total
    assert (
        summary["functional_probe"]
        + summary["route_only"]
        + summary["probe_failed"]
        == total
    )
    assert summary["implemented"] == summary["functional_probe"]
    assert summary["route_only_operations"], "the declared route_only set must be listed"
    assert summary["functional_coverage"] == f"{summary['functional_probe']}/{total}"
    isolation = summary["cross_principal_isolation"]
    assert isolation["checked"] > 0, "no cross-principal list/detail read was checked"
    assert isolation["failures"] == []



@pytest.mark.parametrize("operation_id", sorted(OPERATIONS))
def test_operation_record_matches_the_contract_shape(f12_report: dict, operation_id: str) -> None:
    operation = OPERATIONS[operation_id]
    record = next(
        item for item in f12_report["operations"] if item["operation_id"] == operation_id
    )
    assert record["method"] == operation.method
    assert record["path"] == operation.path
    assert record["security"] == operation.security
    assert record["internal_role"] == operation.internal_role
    if operation.security == "internal":
        probe = next(
            item for item in record["probes"] if item["kind"] == "unauthenticated"
        )
        assert probe["transport_error"], "a certificate-less internal call must not reach HTTP"
        wrong = next(item for item in record["probes"] if item["kind"] == "wrong_internal_role")
        assert wrong["status"] == 403
        assert wrong["error_code"] == "forbidden"
    elif operation.security == "public":
        probe = record["probes"][0]
        assert probe["status"] < 500
    else:
        anonymous = next(
            item for item in record["probes"] if item["kind"] == "anonymous"
        )
        assert anonymous["status"] == 401
        assert anonymous["error_code"] in {
            "unauthenticated",
            "invalid_credentials",
        }, anonymous
