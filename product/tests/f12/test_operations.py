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
def test_operation_is_implemented_and_has_an_authorization_record(
    f12_report: dict, operation_id: str
) -> None:
    record = next(
        item for item in f12_report["operations"] if item["operation_id"] == operation_id
    )
    assert record["implemented"] is True, record["failures"]
    assert record["route_registered"] is True, (
        "the literal route registry does not register this operation"
    )
    assert record["authorization_ok"] is True, record["failures"]
    assert record["verdict"] == "pass", record["failures"]
    assert record["probes"], "the operation has no probe record (a silent skip)"
    for probe in record["probes"]:
        assert probe["kind"] != "skip"
        if probe["status"] is None:
            # The only legitimate no-HTTP-response probe is the missing client
            # certificate on the internal listener.
            assert probe["transport_error"], probe


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
