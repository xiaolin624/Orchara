"""Manifest tests: the gate's operation list is the contract, not a hand copy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import f12_api_gate as gate

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = PRODUCT_ROOT / "contracts" / "openapi.json"


def test_the_contract_still_lists_the_f12_baseline_of_106_operations() -> None:
    operations = gate.load_operations(CONTRACT)
    assert len(operations) == 106
    assert len({operation.operation_id for operation in operations}) == 106
    assert gate.DEFAULT_EXPECTED_OPERATIONS == 106


def test_the_gate_refuses_a_manifest_that_dropped_one_operation(tmp_path: Path) -> None:
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    # One operation, not one path: /api/v1/memories carries GET and POST.
    del document["paths"]["/api/v1/memories"]["get"]
    mutated = tmp_path / "openapi-105.json"
    mutated.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(gate.ManifestError, match=r"105 operations.*106"):
        gate.load_operations(mutated)


def test_the_gate_refuses_a_manifest_that_added_an_operation(tmp_path: Path) -> None:
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    document["paths"]["/api/v1/f12-injected"] = {
        "get": {
            "operationId": "f12Injected",
            "responses": {"200": {"description": "ok"}},
        }
    }
    mutated = tmp_path / "openapi-107.json"
    mutated.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(gate.ManifestError, match=r"107 operations.*106"):
        gate.load_operations(mutated)


def test_the_gate_refuses_a_duplicate_operation_id(tmp_path: Path) -> None:
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    document["paths"]["/api/v1/memories"]["get"]["operationId"] = "listApplications"
    mutated = tmp_path / "openapi-duplicate.json"
    mutated.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(gate.ManifestError, match="duplicate operationId"):
        gate.load_operations(mutated, expected_count=None)


def test_the_static_registry_is_the_authority_for_operation_existence(tmp_path: Path) -> None:
    """A missing route must be classified as missing even when its 404 is the
    contract envelope (a missing resource and a missing route look identical at
    runtime, so the literal registry decides)."""
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    document["paths"]["/api/v1/f12-injected-missing"] = {
        "get": {
            "operationId": "f12InjectedMissing",
            "responses": {"200": {"description": "ok"}, "404": {"description": "missing"}},
        }
    }
    mutated = tmp_path / "openapi-107.json"
    mutated.write_text(json.dumps(document), encoding="utf-8")

    real = gate.scan_route_registry(PRODUCT_ROOT, gate.load_operations(CONTRACT))
    assert real["missing_operation_ids"] == []
    assert real["missing_registrations"] == []
    assert real["contract_operations"] == 106

    injected = gate.scan_route_registry(
        PRODUCT_ROOT, gate.load_operations(mutated, expected_count=107)
    )
    assert injected["missing_operation_ids"] == ["f12InjectedMissing"]
    assert any(
        "GET /api/v1/f12-injected-missing" in registration
        for registration in injected["missing_registrations"]
    )


def test_only_the_contract_declared_string_ids_are_exempt_from_the_uuid_rule() -> None:
    """The UUID invariant judges ids the contract types as UUIDs.

    `listCredentialRefs` and `listModelEndpoints` declare `id: {type: string}`
    (deployer keys, e.g. `stub-endpoint`). The exemption must be derived from the
    contract, not from the observed values, and no platform resource may sneak in.
    """
    operations = {operation.operation_id: operation for operation in gate.load_operations(CONTRACT)}
    exempt = {
        operation_id: operation.non_uuid_id_fields
        for operation_id, operation in operations.items()
        if operation.non_uuid_id_fields
    }
    assert exempt == {
        "listCredentialRefs": ("id",),
        "listModelEndpoints": ("id",),
    }, exempt


def test_every_operation_has_a_declared_probe_and_an_epic(f12_report: dict) -> None:
    records = {record["operation_id"]: record for record in f12_report["operations"]}
    assert len(records) == 106
    expected = {operation.operation_id for operation in gate.load_operations(CONTRACT)}
    assert set(records) == expected
    unmapped = [record["operation_id"] for record in records.values() if record["epic"] == "F12-unmapped"]
    assert unmapped == []
    for record in records.values():
        assert record["probes"], f"{record['operation_id']} has no probe record"
        assert all(probe["kind"] != "skip" for probe in record["probes"])
        assert all("status" in probe or probe.get("transport_error") for probe in record["probes"])


def test_the_report_covers_the_public_internal_and_session_shapes(f12_report: dict) -> None:
    records = f12_report["operations"]
    assert sum(1 for record in records if record["security"] == "public") == 4
    assert sum(1 for record in records if record["security"] == "internal") == 23
    assert sum(1 for record in records if record["security"] in ("session", "session_only")) == 79
    internal = [record for record in records if record["security"] == "internal"]
    assert all(record["internal_role"] in ("worker", "supervisor", "maintenance") for record in internal)
    assert all(
        {probe["kind"] for probe in record["probes"]}
        == {"unauthenticated", "correct_internal_role", "wrong_internal_role"}
        for record in internal
    )


def test_the_report_is_machine_readable_and_has_the_required_sections(f12_report: dict) -> None:
    assert f12_report["ticket"] == "T093"
    assert f12_report["issue"] == 94
    assert f12_report["contract"]["operations"] == 106
    assert len(f12_report["contract"]["sha256"]) == 64
    summary = f12_report["summary"]
    assert set(summary) >= {
        "operations",
        "implemented",
        "missing_operations",
        "authorization_failed",
        "per_operation_failed",
        "invariant_failures",
        "not_run",
        "by_epic",
        "verdict",
    }
    assert summary["operations"] == 106
    assert isinstance(summary["by_epic"], dict) and summary["by_epic"]
    assert summary["verdict"] in ("passed", "failed")
    assert f12_report["environment"]["transport"].startswith("real HTTP")
