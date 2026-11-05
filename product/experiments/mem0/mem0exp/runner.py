"""Replayable CLI for the T066 comparison and the machine-readable evidence file.

    python experiments/mem0/run_experiment.py pg    --out evidence/t066-pg.json
    python experiments/mem0/run_experiment.py mem0  --out evidence/t066-mem0.json
    python experiments/mem0/run_experiment.py both  --out evidence/t066-both.json

``both`` is the experiment result. ``pg`` runs the real PostgreSQL baseline with
the compiled product/experiment binaries; ``mem0`` needs the isolated venv
(``experiments/mem0/.venv``) and records ``blocked``, never a fake pass, when
``mem0ai`` is not importable. Exit codes: 0 all requested sides ran, 2 a side is
blocked, 1 the harness itself produced an invalid record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
if str(EXPERIMENT_ROOT) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT_ROOT))

from mem0exp import decision as decision_module  # noqa: E402
from mem0exp import embedder, fixture, mem0_adapter, records  # noqa: E402
from mem0exp import pg_baseline  # noqa: E402

F11_REASON = (
    "F11 deletion governance has not delivered its physical purge, retention/backup "
    "policy and vendor egress gate in this build; no evidence is claimed for it"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def harness_sha256() -> str:
    """One digest over every harness *input*, so a result names the code that made it.

    Documentation is deliberately excluded: editing README/report text must not
    change the identity of an executed result.
    """
    digest = hashlib.sha256()
    for path in sorted(EXPERIMENT_ROOT.rglob("*")):
        if not path.is_file() or ".venv" in path.parts or "work" in path.parts:
            continue
        if ".target" in path.parts or "evidence" in path.parts:
            continue
        if path.suffix not in {".py", ".go", ".in", ".txt"}:
            continue
        relative = path.relative_to(EXPERIMENT_ROOT).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def environment_profile() -> dict:
    return {
        "python": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "generated_at": _utc_now(),
        "working_directory": str(PRODUCT_ROOT),
        "mem0ai": "recorded per system",
        "isolation": (
            "throwaway PostgreSQL database + experiment temp directory; the product "
            "database and product runtime dependencies are never used"
        ),
    }


def _default_binaries(args: argparse.Namespace) -> tuple[Path, Path, list[dict]]:
    commands = []
    target = EXPERIMENT_ROOT / ".target" / "bin"
    admin = Path(args.admin) if args.admin else target / _binary_name("orchara-admin")
    driver = Path(args.pgdriver) if args.pgdriver else target / _binary_name("t066-pgdriver")
    if args.admin:
        commands.append({"cmd": f"use supplied admin binary {admin}", "exit": 0, "sha256": _file_sha256(admin)})
    else:
        commands.append(pg_baseline.build_binary("./cmd/admin", admin))
    if args.pgdriver:
        commands.append(
            {"cmd": f"use supplied pgdriver binary {driver}", "exit": 0, "sha256": _file_sha256(driver)}
        )
    else:
        commands.append(pg_baseline.build_binary("./experiments/mem0/pgdriver", driver))
    return admin, driver, commands


def _binary_name(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


def _run_pg(args: argparse.Namespace, data, work_dir: Path) -> tuple[dict, list[dict]]:
    commands: list[dict] = []
    try:
        admin, driver, build_commands = _default_binaries(args)
        commands.extend(build_commands)
        created = False
        if args.dsn:
            dsn = args.dsn
            dbname = pg_baseline.check_isolated_dsn(dsn)
        else:
            dsn, dbname = pg_baseline.create_isolated_database()
            created = True
        try:
            object_dir = Path(args.object_dir) if args.object_dir else work_dir / "objects"
            if not args.object_dir and object_dir.exists():
                # A previous run's sealed files would otherwise be counted as copies
                # of this run's body (the store is append-only inside a run).
                shutil.rmtree(object_dir)
            system = pg_baseline.run(
                data=data,
                dsn=dsn,
                dbname=dbname,
                admin=admin,
                driver=driver,
                object_dir=object_dir,
                iterations=args.iterations,
                top_k=args.top_k,
            )
            system["database"] = {
                "name": dbname,
                "isolated": created or dbname != pg_baseline.pg_dev.DEFAULT_DB,
                "created_by_experiment": created,
                "dropped_after": created and not args.keep,
            }
            commands.extend(system["commands"])
            return system, commands
        finally:
            if created and not args.keep:
                pg_baseline.drop_database(dbname)
    except pg_baseline.PgBlocked as error:
        return records.system_blocked(str(error), commands=commands), commands
    except Exception as error:  # a real failure is recorded as blocked, never as a pass
        return (
            records.system_blocked(
                f"the PostgreSQL baseline failed: {type(error).__name__}: {error}", commands=commands
            ),
            commands,
        )


def _run_mem0(args: argparse.Namespace, data, work_dir: Path) -> tuple[dict, list[dict]]:
    available = mem0_adapter.availability()
    if available["status"] != "available":
        return (
            records.system_blocked(
                available["reason"],
                commands=[
                    {
                        "cmd": ".venv/Scripts/python -c 'import mem0'",
                        "exit": 1,
                        "note": "isolated venv not usable in this interpreter",
                    }
                ],
                versions=available,
            ),
            [],
        )
    try:
        system = mem0_adapter.run(
            data=data, work_dir=work_dir / "mem0", iterations=args.iterations, top_k=args.top_k
        )
        return system, []
    except Exception as error:  # never turn a crash into numbers
        return (
            records.system_blocked(
                f"the Mem0 run failed: {type(error).__name__}: {error}",
                commands=[{"cmd": "mem0exp.mem0_adapter.run", "exit": 1}],
                versions=available,
            ),
            [],
        )


def _cost(record_systems: dict) -> dict:
    mem0 = record_systems.get("mem0") or {}
    pg = record_systems.get("pg") or {}
    mem0_versions = mem0.get("versions") or {}
    return {
        "mem0": {
            "python_distributions": mem0_versions.get("installed_distributions"),
            "venv_bytes": (mem0.get("storage") or {}).get("venv_bytes"),
            "history_db_bytes": (mem0.get("storage") or {}).get("history_db_bytes"),
            "qdrant_bytes": (mem0.get("storage") or {}).get("qdrant_bytes"),
            "new_runtime_dependency": True,
        },
        "pg": {
            "database_bytes": (pg.get("storage") or {}).get("database_bytes"),
            "new_runtime_dependency": False,
            "note": "the baseline uses the existing PostgreSQL instance and the existing Go service",
        },
    }


def build_record(
    *,
    command: str,
    data,
    systems: dict,
    commands: list[dict],
    argv: list[str],
) -> dict:
    run_systems = {name: system for name, system in systems.items() if system.get("status") == "run"}
    blocked = any(system.get("status") == "blocked" for system in systems.values())
    experiment_status = "executed" if run_systems else ("blocked" if blocked else "not_run")
    decision = decision_module.decide(
        systems.get("pg"),
        systems.get("mem0"),
        f11_gate_passed=False,
        f11_reason=F11_REASON,
    )
    record = records.sample_record(
        pg=systems.get("pg") or records.system_not_run("not requested"),
        mem0=systems.get("mem0") or records.system_not_run("not requested"),
        decision=decision,
        experiment_status=experiment_status,
    )
    record["generated_at"] = _utc_now()
    record["command"] = command
    record["environment"] = environment_profile()
    record["fixture"] = data.manifest()
    record["inputs"] = {
        "fixture_sha256": data.sha256(),
        "fixture_schema": fixture.SCHEMA,
        "embedder": embedder.profile(),
        "embedder_sha256": _file_sha256(EXPERIMENT_ROOT / "mem0exp" / "embedder.py"),
        "harness_sha256": harness_sha256(),
        "requirements_lock_sha256": _file_sha256(EXPERIMENT_ROOT / "requirements.lock.txt")
        if (EXPERIMENT_ROOT / "requirements.lock.txt").exists()
        else "0" * 64,
        "requirements_in_sha256": _file_sha256(EXPERIMENT_ROOT / "requirements.in"),
    }
    record["commands"] = [
        {"cmd": "python experiments/mem0/run_experiment.py " + " ".join(argv), "exit": 0},
        *commands,
    ]
    record["cost"] = _cost(systems)
    record["stub_real"] = {
        "pg": "real" if (systems.get("pg") or {}).get("status") == "run" else "not_run",
        "mem0": (systems.get("mem0") or {}).get("evidence_level", "not_run"),
        "note": (
            "Mem0 ran the real library, SQLite history and Qdrant store, but only the "
            "infer=False write path with a deterministic local embedder (no model provider): "
            "labelled stub, never adoption evidence"
        ),
    }
    record["not_run"] = [
        "Mem0 infer=True extraction / LLM query rewriting / reranker (no model provider)",
        "F11 deletion-governance physical purge and retention/backup review",
        "browser/UI (the platform has no Mem0 UI and this ticket adds none)",
    ]
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T066 Mem0 vs PostgreSQL isolated experiment")
    parser.add_argument("system", choices=("pg", "mem0", "both"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--iterations", type=int, default=21)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--seed", type=int, default=fixture.FIXTURE_SEED)
    parser.add_argument("--dsn", default=None, help="an existing isolated throwaway database")
    parser.add_argument("--admin", default=None, help="prebuilt cmd/admin binary")
    parser.add_argument("--pgdriver", default=None, help="prebuilt experiments/mem0/pgdriver binary")
    parser.add_argument("--object-dir", default=None)
    parser.add_argument("--keep", action="store_true", help="keep the throwaway database / work dir")
    args = parser.parse_args(argv)
    if args.iterations < 20:
        parser.error("--iterations must be at least 20 (the ticket requires >=20 runs for p50/p95)")

    data = fixture.build(seed=args.seed)
    work_dir = args.work_dir or (EXPERIMENT_ROOT / "work" / args.system)
    work_dir.mkdir(parents=True, exist_ok=True)

    systems: dict[str, dict] = {}
    commands: list[dict] = []
    if args.system in ("pg", "both"):
        system, pg_commands = _run_pg(args, data, work_dir)
        systems["pg"] = system
        commands.extend(pg_commands)
    else:
        systems["pg"] = records.system_not_run("the PostgreSQL side was not requested in this invocation")
    if args.system in ("mem0", "both"):
        system, mem0_commands = _run_mem0(args, data, work_dir)
        systems["mem0"] = system
        commands.extend(mem0_commands)
    else:
        systems["mem0"] = records.system_not_run("the Mem0 side was not requested in this invocation")

    record = build_record(
        command=args.system, data=data, systems=systems, commands=commands, argv=sys.argv[1:]
    )
    errors = records.validate(record)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    digest = _file_sha256(args.out)
    print(f"record: {args.out} sha256={digest}")
    for name in ("pg", "mem0"):
        system = record["systems"][name]
        if system["status"] == "run":
            recall = system["recall"]
            latency = system["latency"]
            cleanup = system["cleanup"]
            print(
                f"  {name}: run ({system['evidence_level']}) hit@1={recall['hit_at_1']} "
                f"hit@k={recall['hit_at_3']} p50={latency['p50_ms']}ms p95={latency['p95_ms']}ms "
                f"clean={cleanup['stores_clean']} residual={len(cleanup['residual_body_copies'])}"
            )
        else:
            print(f"  {name}: {system['status']} - {system.get('reason', '')[:160]}")
    print(f"decision: {record['decision']['decision']}")
    for reason in record["decision"]["reasons"]:
        print(f"  - {reason}")
    if errors:
        print("INVALID RECORD: " + "; ".join(errors))
        return 1
    if any(system["status"] == "blocked" for system in record["systems"].values()):
        return 2
    return 0
