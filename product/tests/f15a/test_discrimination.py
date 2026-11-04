"""T053/F15A: the probe's discrimination proof against real local fixture servers.

The discriminator is the whole deliverable: the *same* probe process must pass a
fixture that enforces the policy, and must reject a fixture that breaks one rule 鈥?and the rejection must name the assertion that measured that rule. A green run
against the compliant fixture is **not** evidence that a real S3 candidate is
compatible; that acceptance stays ``blocked`` until a candidate endpoint exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import (
    ALL_VIOLATIONS,
    SECRET_VALUES,
    FixtureProcess,
    assert_no_secret_values,
    load_report,
    run_probe,
    statuses,
)

EXPECTED_IDS = [
    "anonymous_read_denied",
    "anonymous_put_denied",
    "anonymous_list_denied",
    "out_of_range_key_denied",
    "sealed_put_denied",
    "staging_overwrite_preserves_sealed",
    "short_lived_signature_expiry",
    "proxy_download_boundary",
    "delete_semantics",
    "versions_immutable",
]


def test_compliant_fixture_passes_and_records_real_evidence(tmp_path: Path) -> None:
    report_path = tmp_path / "compliant.json"
    with FixtureProcess("compliant_server.py") as fixture:
        completed = run_probe(fixture.port, report_path)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = load_report(report_path)
    assert report["status"] == "passed"
    assert report["scope"] == "candidate-s3"
    assert report["exit_code"] == 0
    assert [item["id"] for item in report["assertions"]] == EXPECTED_IDS
    status_map = statuses(report)
    for assertion_id in ALL_VIOLATIONS:
        assert status_map[assertion_id] == "passed", (assertion_id, report["assertions"])
    # Versioning is observational: a fixture without versioning must say not_run.
    assert status_map["versions_immutable"] == "not_run"
    assert report["summary"]["passed"] == 9
    assert report["summary"]["not_run"] == 1
    assert report["cleanup"]["failed"] == []

    # Evidence must be reproducible and credential-free.
    assert set(report["input_hashes"]) >= {"anonymous-read", "oversized", "delete-a"}
    assert all(entry["sha256"] for entry in report["input_hashes"].values())
    assert report["config"]["credentials_present"] == {
        "runtime": True,
        "provisioning": True,
    }
    assert_no_secret_values(report_path)
    # The pre-expiry URL observation must be recorded honestly, not claimed as revocation.
    proxy = {item["id"]: item for item in report["assertions"]}["proxy_download_boundary"]
    revocation = proxy["evidence"]["pre_expiry_url_revocation"]
    assert revocation["get_after_delete_status"] == 404
    assert revocation["pre_expiry_url_still_authorizes_key"] is True
    assert revocation["requires_proxy_download"] is True


def test_violating_fixture_fails_every_enforceable_assertion(tmp_path: Path) -> None:
    report_path = tmp_path / "violating-all.json"
    with FixtureProcess("violating_server.py", violations=tuple(ALL_VIOLATIONS)) as fixture:
        completed = run_probe(fixture.port, report_path)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    report = load_report(report_path)
    assert report["status"] == "failed"
    status_map = statuses(report)
    for assertion_id in ALL_VIOLATIONS:
        assert status_map[assertion_id] == "failed", (assertion_id, report["assertions"])
    assert status_map["versions_immutable"] == "not_run"
    assert report["summary"]["failed"] == 9
    # A violating server still gets its evidence written, with the failing checks.
    for item in report["assertions"]:
        if item["status"] == "failed":
            assert any(not check["ok"] for check in item["checks"])
            assert item["reason"].startswith("failed checks:")


@pytest.mark.parametrize("violation", ALL_VIOLATIONS)
def test_one_broken_rule_is_caught_by_its_own_assertion(tmp_path: Path, violation: str) -> None:
    """The sharpest proof: only the assertion measuring that rule may fail."""
    report_path = tmp_path / f"{violation}.json"
    with FixtureProcess("violating_server.py", violations=(violation,)) as fixture:
        completed = run_probe(fixture.port, report_path)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    report = load_report(report_path)
    status_map = statuses(report)
    assert status_map[violation] == "failed", (violation, report["assertions"])
    for other in ALL_VIOLATIONS:
        if other == violation:
            continue
        assert status_map[other] == "passed", (
            f"breaking {violation} also changed {other}: {report['assertions']}"
        )
    assert status_map["versions_immutable"] == "not_run"


def test_transient_5xx_is_retried_and_visible_in_the_evidence(tmp_path: Path) -> None:
    report_path = tmp_path / "transient.json"
    with FixtureProcess("compliant_server.py", transient_failures=1) as fixture:
        completed = run_probe(fixture.port, report_path)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = load_report(report_path)
    assert report["status"] == "passed"
    assert report["client_stats"]["retries"] >= 1
    assert report["client_stats"]["requests"] > report["client_stats"]["retries"]


def test_missing_provisioning_credentials_blocks_sealed_assertions(tmp_path: Path) -> None:
    """The reachable assertions run; sealed ones are blocked, never passed."""
    import os
    import subprocess
    import sys

    from helpers import PROBE, probe_env

    report_path = tmp_path / "no-provision.json"
    with FixtureProcess("compliant_server.py") as fixture:
        env = probe_env(fixture.port)
        env.pop("F15A_S3_PROVISION_ACCESS_KEY")
        env.pop("F15A_S3_PROVISION_SECRET_KEY")
        completed = subprocess.run(
            [sys.executable, str(PROBE), "--out", str(report_path), "--short-ttl", "1"],
            cwd=str(PROBE.parents[2]),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
    assert completed.returncode == 2, completed.stdout + completed.stderr
    report = load_report(report_path)
    assert report["status"] == "blocked"
    assert report["config"]["credentials_present"]["provisioning"] is False
    status_map = statuses(report)
    # Anonymous checks need no sealed object, so they still run and pass.
    assert status_map["anonymous_put_denied"] == "passed"
    assert status_map["anonymous_list_denied"] == "passed"
    # Everything that needs a sealed object is blocked with a reason.
    for assertion_id in (
        "anonymous_read_denied",
        "out_of_range_key_denied",
        "sealed_put_denied",
        "staging_overwrite_preserves_sealed",
        "short_lived_signature_expiry",
        "proxy_download_boundary",
        "delete_semantics",
    ):
        assert status_map[assertion_id] == "blocked", (assertion_id, report["assertions"])
    assert all(secret not in report_path.read_text("utf-8") for secret in SECRET_VALUES)
