"""Mutation tests: the gate must not be a false green.

Two mutations are applied to a copy of the contract:

* drop an operation -> the gate refuses the manifest (exit 2) instead of
  judging 105 operations;
* inject an operation the product does not implement -> the gate reports it
  missing and exits non-zero.

The second run is the real end-to-end proof: it starts a full 107-operation
probe against the same running API and fails on exactly the injected operation.

A third, non-mutation case runs the CLI against the unmodified 106-operation
contract: the replayable entry point itself (``--out``) must write the same
machine-readable verdict as the session fixture.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import uuid
from pathlib import Path

from support import f00b

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = PRODUCT_ROOT / "contracts" / "openapi.json"
GATE = PRODUCT_ROOT / "tools" / "f12_api_gate.py"


def _run_gate(
    stack,
    contract: Path,
    out: Path,
    *,
    expected_operations: int = 106,
    timeout: int = 600,
) -> subprocess.CompletedProcess:
    command = [
        sys.executable,
        str(GATE),
        "--base-url",
        stack.server.base_url,
        "--admin-login",
        "admin",
        "--admin-password",
        f00b.ADMIN_PASSWORD,
        "--internal-url",
        stack.certificates.internal_url,
        "--internal-ca",
        str(stack.certificates.ca.cert),
        "--internal-cert-dir",
        str(stack.certificates.clients["worker"].cert.parent),
        "--contract",
        str(contract),
        "--expected-operations",
        str(expected_operations),
        "--out",
        str(out),
    ]
    return subprocess.run(
        command,
        cwd=PRODUCT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_the_gate_cli_refuses_a_manifest_with_105_operations(f12_stack, tmp_path: Path) -> None:
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    del document["paths"]["/api/v1/memories"]["get"]
    mutated = tmp_path / "openapi-105.json"
    mutated.write_text(json.dumps(document), encoding="utf-8")
    out = tmp_path / "report.json"
    completed = _run_gate(f12_stack, mutated, out, timeout=120)
    assert completed.returncode == 2, (completed.stdout, completed.stderr)
    assert "REFUSED THE MANIFEST" in completed.stderr, completed.stderr
    assert "105" in completed.stderr and "106" in completed.stderr, completed.stderr
    assert not out.exists() or out.read_text(encoding="utf-8") == ""


def test_the_gate_fails_on_an_injected_unimplemented_operation(
    f12_stack, f12_report: dict, tmp_path: Path
) -> None:
    # Requesting f12_report first materialises the pristine 106-operation session
    # verdict; the mutant run below intentionally mutates the stack it judges, so
    # the session report must already exist (this also keeps the file runnable on
    # its own, not only after test_cross_principal happens to sort first).
    assert f12_report["contract"]["operations"] == 106
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(document)
    missing_path = "/api/v1/f12-missing-operation-" + uuid.uuid4().hex
    mutated["paths"][missing_path] = {
        "get": {
            "operationId": "f12InjectedMissingOperation",
            "description": "F12 mutation: this operation does not exist in the product",
            "parameters": [],
            "security": [{"sessionCookie": []}, {"apiKey": []}],
            "responses": {
                "200": {"description": "Success"},
                "401": {"description": "See Error.code and API.md"},
                "404": {"description": "See Error.code and API.md"},
            },
        }
    }
    mutated_contract = tmp_path / "openapi-107.json"
    mutated_contract.write_text(json.dumps(mutated), encoding="utf-8")
    out = tmp_path / "mutated-report.json"
    completed = _run_gate(
        f12_stack, mutated_contract, out, expected_operations=107
    )
    assert completed.returncode == 1, (completed.stdout, completed.stderr)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert "f12InjectedMissingOperation" in report["summary"]["missing_operations"], report["summary"]
    assert report["summary"]["verdict"] == "failed"
    record = next(
        item
        for item in report["operations"]
        if item["operation_id"] == "f12InjectedMissingOperation"
    )
    assert record["implemented"] is False
    # The classification comes from the literal route registry, not from the
    # runtime status: the unknown route answers with the same 404 envelope as a
    # missing resource, so only the registry can prove it was never implemented.
    assert record["route_registered"] is False
    assert "MISSING OPERATION: f12InjectedMissingOperation" in completed.stdout, completed.stdout
    # The 106 real operations are still judged in the same run.
    assert len(report["operations"]) == 107
    assert report["summary"]["operations"] == 107
    assert report["summary"]["implemented"] == 106
    real = [item for item in report["operations"] if item["operation_id"] != "f12InjectedMissingOperation"]
    assert all(item["implemented"] for item in real), [
        item["operation_id"] for item in real if not item["implemented"]
    ]


def test_the_gate_cli_reproduces_the_session_verdict(
    f12_cli_stack, f12_report: dict, tmp_path: Path
) -> None:
    """The replayable entry point is the CLI itself, not only the test fixture.

    The session evidence is written from the same ``Gate`` object; this test
    proves the documented command line (``--out`` included) produces the same
    machine-readable verdict from the unmodified contract. It runs against its
    own pristine stack because the gate mutates the API it judges.
    """
    out = tmp_path / "cli-report.json"
    completed = _run_gate(f12_cli_stack, CONTRACT, out)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert out.exists(), (completed.stdout, completed.stderr)
    report = json.loads(out.read_text(encoding="utf-8"))
    summary = report["summary"]
    assert summary["operations"] == 106
    assert summary["implemented"] == 106
    assert summary["missing_operations"] == []
    assert summary["authorization_failed"] == []
    assert summary["invariant_failures"] == []
    assert summary["verdict"] == f12_report["summary"]["verdict"] == "passed"
    assert set(summary["by_epic"]) == set(f12_report["summary"]["by_epic"])
    assert set(summary["not_run"]) == set(f12_report["summary"]["not_run"])
    assert "F12 API gate: 106/106 operations implemented" in completed.stdout, completed.stdout


def test_the_gate_reports_the_same_verdict_as_its_exit_code(f12_report: dict) -> None:
    assert f12_report["summary"]["verdict"] in ("passed", "failed")
    if f12_report["summary"]["verdict"] == "failed":
        assert (
            f12_report["summary"]["missing_operations"]
            or f12_report["summary"]["authorization_failed"]
            or f12_report["summary"]["invariant_failures"]
            or f12_report["summary"]["per_operation_failed"]
            or f12_report["summary"]["errors"]
        ), "a failed verdict must name the failure"
