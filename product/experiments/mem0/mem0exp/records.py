"""Machine-readable result records for the T066 experiment.

The record is the deliverable. It has to be impossible to read it as "passed"
when the Mem0 side did not actually run, and impossible to read it as "adopted"
while the deletion enumeration still finds body copies. ``validate`` enforces
exactly those two rules in addition to the shape checks, and the unit tests call
it on deliberately dishonest records.
"""

from __future__ import annotations

import re

SCHEMA = "t066.mem0.experiment.v1"
EXPERIMENT_STATUSES = ("executed", "blocked", "not_run")
SYSTEM_STATUSES = ("run", "blocked", "not_run")
EVIDENCE_LEVELS = ("real", "stub")
DECISIONS = ("adopt", "do_not_adopt", "insufficient_evidence")

_HEX64 = re.compile(r"[0-9a-f]{64}")


def system_run(
    *,
    evidence_level: str,
    recall: dict,
    latency: dict,
    cleanup: dict,
    storage: dict | None = None,
    id_map: dict | None = None,
    versions: dict | None = None,
    commands: list | None = None,
    limitations: list | None = None,
    **extra: object,
) -> dict:
    """One system that really ran; measurements are mandatory."""
    record: dict = {
        "status": "run",
        "evidence_level": evidence_level,
        "recall": recall,
        "latency": latency,
        "cleanup": cleanup,
        "storage": storage or {},
        "id_map": id_map or {},
        "versions": versions or {},
        "commands": commands or [],
        "limitations": limitations or [],
    }
    record.update(extra)
    return record


def system_blocked(
    reason: str,
    *,
    commands: list | None = None,
    versions: dict | None = None,
    limitations: list | None = None,
    **extra: object,
) -> dict:
    """A system that did not run. Never carries numbers."""
    record: dict = {
        "status": "blocked",
        "reason": reason,
        "commands": commands or [],
        "versions": versions or {},
        "limitations": limitations or [],
    }
    record.update(extra)
    return record


def system_not_run(reason: str, **extra: object) -> dict:
    record: dict = {"status": "not_run", "reason": reason, "commands": [], "limitations": []}
    record.update(extra)
    return record


def sample_record(*, pg: dict, mem0: dict, decision: dict, experiment_status: str = "executed") -> dict:
    """A well-formed skeleton used by the shape/verdict tests and as a builder base."""
    return {
        "schema": SCHEMA,
        "ticket": "T066 (#67)",
        "experiment_status": experiment_status,
        "generated_at": "1970-01-01T00:00:00+00:00",
        "environment": {"python": "3.12", "platform": "test", "mem0ai": "2.2.1"},
        "fixture": {
            "schema": "t066.mem0.fixture.v1",
            "seed": 66,
            "sha256": "0" * 64,
            "users": 2,
            "applications": 3,
            "memories": 9,
        },
        "inputs": {
            "fixture_sha256": "0" * 64,
            "harness_sha256": "0" * 64,
            "requirements_lock_sha256": "0" * 64,
            "embedder": {"model": "t066-hashed-bow-v1", "dimensions": 64},
        },
        "commands": [{"cmd": "python experiments/mem0/run_experiment.py both", "exit": 0}],
        "systems": {"pg": pg, "mem0": mem0},
        "cost": {},
        "decision": decision,
        "not_run": [],
        "stub_real": {
            "pg": "real",
            "mem0": mem0.get("evidence_level", "not_run"),
        },
    }


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(record: object) -> list[str]:
    """Return every reason this record must not be trusted (empty = valid)."""
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["record must be a JSON object"]
    if record.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA!r}")

    status = record.get("experiment_status")
    if status not in EXPERIMENT_STATUSES:
        errors.append(
            f"experiment_status {status!r} is not one of {EXPERIMENT_STATUSES}: "
            "an unexecuted or partially executed experiment is never 'complete'"
        )

    fixture = record.get("fixture")
    if not isinstance(fixture, dict):
        errors.append("fixture manifest is missing")
    else:
        if not isinstance(fixture.get("seed"), int):
            errors.append("fixture.seed must be an integer")
        if not _HEX64.fullmatch(str(fixture.get("sha256", ""))):
            errors.append("fixture.sha256 must be 64 lower-case hex characters")

    inputs = record.get("inputs")
    if not isinstance(inputs, dict):
        errors.append("inputs are missing")
    else:
        for key in ("fixture_sha256", "harness_sha256", "requirements_lock_sha256"):
            if not _HEX64.fullmatch(str(inputs.get(key, ""))):
                errors.append(f"inputs.{key} must be 64 lower-case hex characters")

    systems = record.get("systems")
    if not isinstance(systems, dict):
        errors.append("systems are missing")
        systems = {}
    for name in ("pg", "mem0"):
        system = systems.get(name)
        if not isinstance(system, dict):
            errors.append(f"systems.{name} is missing")
            continue
        system_status = system.get("status")
        if system_status not in SYSTEM_STATUSES:
            errors.append(f"systems.{name}.status {system_status!r} is not one of {SYSTEM_STATUSES}")
            continue
        if system_status == "run":
            if system.get("evidence_level") not in EVIDENCE_LEVELS:
                errors.append(f"systems.{name}.evidence_level must be one of {EVIDENCE_LEVELS}")
            recall = system.get("recall")
            if not isinstance(recall, dict):
                errors.append(f"systems.{name}.recall is missing")
            else:
                for key in ("hit_at_1", "hit_at_3", "positive_queries", "negative_queries", "forbidden_hits"):
                    if not _is_number(recall.get(key)):
                        errors.append(f"systems.{name}.recall.{key} must be a number")
                for key in ("hit_at_1", "hit_at_3"):
                    value = recall.get(key)
                    if _is_number(value) and not 0.0 <= float(value) <= 1.0:
                        errors.append(f"systems.{name}.recall.{key} must be within 0..1")
            latency = system.get("latency")
            if not isinstance(latency, dict):
                errors.append(f"systems.{name}.latency is missing")
            else:
                runs = latency.get("runs")
                if not isinstance(runs, int) or runs < 20:
                    errors.append(f"systems.{name}.latency.runs must be an integer >= 20")
                for key in ("p50_ms", "p95_ms"):
                    if not _is_number(latency.get(key)):
                        errors.append(f"systems.{name}.latency.{key} must be a number")
                if (
                    _is_number(latency.get("p50_ms"))
                    and _is_number(latency.get("p95_ms"))
                    and latency["p50_ms"] > latency["p95_ms"]
                ):
                    errors.append(f"systems.{name}.latency.p50_ms exceeds p95_ms")
            cleanup = system.get("cleanup")
            if not isinstance(cleanup, dict):
                errors.append(f"systems.{name}.cleanup is missing")
            elif not isinstance(cleanup.get("residual_body_copies"), list):
                errors.append(f"systems.{name}.cleanup.residual_body_copies must be a list")
            if not system.get("commands"):
                errors.append(f"systems.{name}.commands must record the commands that produced it")
        else:
            if not str(system.get("reason") or "").strip():
                errors.append(f"systems.{name} is {system_status} but records no reason")
            if "recall" in system or "latency" in system:
                errors.append(f"systems.{name} is {system_status} but carries measurements")

    decision = record.get("decision")
    if not isinstance(decision, dict):
        errors.append("decision is missing")
        decision = {}
    verdict = decision.get("decision")
    if verdict not in DECISIONS:
        errors.append(f"decision.decision {verdict!r} is not one of {DECISIONS}")
    reasons = decision.get("reasons")
    if not isinstance(reasons, list) or not reasons:
        errors.append("decision.reasons must be a non-empty list")
    if not isinstance(decision.get("remaining_gates"), list):
        errors.append("decision.remaining_gates must be a list")
    if verdict == "adopt":
        if status != "executed":
            errors.append("adopt requires an executed experiment, not " + repr(status))
        for name in ("pg", "mem0"):
            system = systems.get(name) if isinstance(systems.get(name), dict) else {}
            if system.get("status") != "run":
                errors.append(f"adopt requires systems.{name} to have run")
                continue
            residual = (system.get("cleanup") or {}).get("residual_body_copies")
            if residual:
                errors.append(f"adopt is refused because systems.{name} still holds body copies: {residual}")
        mem0 = systems.get("mem0") if isinstance(systems.get("mem0"), dict) else {}
        if mem0.get("evidence_level") != "real":
            errors.append("adopt requires real (not stub) evidence for Mem0")
        gates = decision.get("gates") if isinstance(decision.get("gates"), dict) else {}
        f11 = gates.get("f11") if isinstance(gates.get("f11"), dict) else {}
        if f11.get("passed") is not True:
            errors.append("adopt requires the F11 deletion-governance gate to have passed")

    stub_real = record.get("stub_real")
    if not isinstance(stub_real, dict):
        errors.append("stub_real separation is missing")
    else:
        pg_system = systems.get("pg") if isinstance(systems.get("pg"), dict) else {}
        mem0_system = systems.get("mem0") if isinstance(systems.get("mem0"), dict) else {}
        if pg_system.get("status") == "run" and stub_real.get("pg") != "real":
            errors.append("stub_real.pg must say 'real' for a PostgreSQL run")
        if mem0_system.get("status") == "run" and stub_real.get("mem0") != mem0_system.get("evidence_level"):
            errors.append("stub_real.mem0 must match systems.mem0.evidence_level")

    return errors
