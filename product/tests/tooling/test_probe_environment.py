"""Tests for the D00/T001 environment probe.

The probe is the evidence source for ticket T001. These tests pin the
guarantees the ticket states in its acceptance criteria:

* every reported version carries the command that produced it;
* no credential or environment-variable value leaks into the report;
* a missing Linux isolation host is reported as unavailable, never "passed";
* missing model credentials and GPU are recorded as separate blockers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PRODUCT_ROOT / "tools"))

import probe_environment as probe  # noqa: E402


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Run the real probe once for the assertions below."""
    out = tmp_path_factory.mktemp("probe")
    return probe.build_report(toolchain_dir=probe.default_toolchain_dir(), out_dir=out)


def test_required_sections_present(report: dict) -> None:
    for section in ("host", "tools", "services", "capabilities", "blockers", "probe_commands"):
        assert section in report, f"missing section: {section}"
    assert report["schema_version"] == probe.SCHEMA_VERSION
    assert report["schema_version"] != ""


def test_every_tool_records_its_probe_command(report: dict) -> None:
    tools = report["tools"]
    assert tools, "expected at least one probed tool"
    for tool in tools:
        assert tool["command"], f"{tool['name']} has no recorded probe command"
        # available tools must carry a version string; unavailable ones an error
        if tool["available"]:
            assert tool["version"], f"{tool['name']} marked available without version"
        else:
            assert tool["error"], f"{tool['name']} marked unavailable without error"


_SECRETISH_ENV = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE",
    "DSN",
)


def test_no_env_var_or_credential_values_leak(report: dict) -> None:
    """The ticket forbids secrets in the report. Assert on the serialised form."""
    blob = json.dumps(report, ensure_ascii=False)
    lowered = blob.lower()

    # Credential-shaped values must never appear.
    for marker in ("sk-", "ghp_", "gho_", "github_pat_", "-----begin"):
        assert marker not in lowered, f"credential-shaped marker {marker!r} leaked"

    # No secret-named environment variable may have its value copied in.
    for key, value in __import__("os").environ.items():
        if len(value) < 8 or key.startswith("DSH_"):
            continue
        if not any(token in key.upper() for token in _SECRETISH_ENV):
            continue
        assert value not in blob, f"secret-shaped env value for {key} leaked into report"

    # Provider credentials are reported as presence flags only, never values.
    assert isinstance(report["credential_env_present"], list)
    for name in report["credential_env_present"]:
        assert __import__("os").environ.get(name, "") not in blob


def test_linux_isolation_is_not_claimed_without_a_real_linux_host(report: dict) -> None:
    caps = report["capabilities"]
    assert caps["linux_isolation"]["status"] in {"available", "unavailable", "blocked"}
    if caps["linux_isolation"]["status"] != "available":
        assert caps["linux_isolation"]["status"] != "passed"
        # and it must surface as a blocker with a reason
        joined = json.dumps(report["blockers"], ensure_ascii=False)
        assert "linux_isolation" in joined


def test_model_and_gpu_blockers_are_separate(report: dict) -> None:
    """Missing model credentials and a missing GPU are distinct blockers.

    The report must not conflate them, and must not invent a GPU blocker when
    nvidia-smi actually reported a device.
    """
    caps = report["capabilities"]
    ids = {b["id"] for b in report["blockers"]}

    if caps["gpu"]["status"] == "available":
        assert "gpu" not in ids, "GPU reported as available but still listed as a blocker"
    else:
        assert "gpu" in ids

    if report["credential_env_present"]:
        assert "model_credentials" not in ids
    else:
        assert "model_credentials" in ids
        assert "gpu" in ids or caps["gpu"]["status"] == "available"

    for blocker in report["blockers"]:
        assert blocker["reason"], f"blocker {blocker['id']} has no reason"
        assert blocker["unblocks"], f"blocker {blocker['id']} does not name what it unblocks"


def test_report_roundtrips_to_disk(report: dict) -> None:
    path = Path(report["artifacts"]["json"])
    assert path.exists()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == report["schema_version"]
    md = Path(report["artifacts"]["markdown"])
    assert md.exists()
    assert "D00" in md.read_text(encoding="utf-8")


def test_postgres_capability_refers_to_the_owned_cluster(report: dict) -> None:
    """The machine's own 5432 belongs to another installation and must not be claimed."""
    sys.path.insert(0, str(PRODUCT_ROOT / "tools"))
    import pg_dev

    services = {service["name"]: service for service in report["services"]}
    assert services["postgres"]["port"] == pg_dev.DEFAULT_PORT
    assert services["postgres"]["owned_by_project"] is True
    assert report["capabilities"]["postgres"]["port"] == pg_dev.DEFAULT_PORT

    foreign = services["postgres_port_5432_foreign"]
    assert foreign["port"] == 5432
    assert foreign["owned_by_project"] is False
    assert report["capabilities"]["foreign_postgres"]["status"] in {
        "present_not_owned",
        "absent",
    }


def test_blockers_are_derived_from_capabilities(report: dict) -> None:
    """A blocker exists for exactly those capabilities that are unavailable."""
    blocker_ids = {blocker["id"] for blocker in report["blockers"]}
    for name, capability in report["capabilities"].items():
        declared = capability.get("blocker")
        if not declared:
            continue
        if capability["status"] == "available":
            assert declared["id"] not in blocker_ids, (
                f"{name} is available but still reported as blocked"
            )
        else:
            assert declared["id"] in blocker_ids, (
                f"{name} is unavailable but produced no blocker"
            )
