"""F09/T066: the Mem0-vs-PostgreSQL experiment harness and its verdict rules.

Two truths are kept apart on purpose, exactly as the ticket requires:

* the **Mem0 run** needs the isolated ``experiments/mem0/.venv`` (mem0ai is not a
  product dependency). This suite never imports mem0; it asserts the blocked
  record the harness produces without it and, once one exists, the committed
  result record of the real run;
* the **PostgreSQL baseline** is real and runs here: a throwaway database, the
  compiled ``cmd/admin`` and the experiment's compiled ``pgdriver`` (which calls
  the real ``internal/memory`` service), real sealed objects on disk, and the
  copy-cleanup enumeration after a real delete.

No test is skipped because a condition is missing: a missing isolated venv is a
``blocked`` record, not a skip (BRIEF §1.1).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = PRODUCT_ROOT / "experiments" / "mem0"
if str(EXPERIMENT) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT))

from mem0exp import decision as decision_module  # noqa: E402
from mem0exp import embedder, fixture, records  # noqa: E402

from support import stack as st  # noqa: E402


# ---------------------------------------------------------------------------
# fixture: deterministic synthetic data in isolation
# ---------------------------------------------------------------------------
def test_the_fixture_is_byte_identical_across_builds_and_seed_changes_it():
    first = fixture.build()
    second = fixture.build()
    assert first.canonical_json() == second.canonical_json()
    assert first.sha256() == second.sha256()
    assert len(first.sha256()) == 64
    assert fixture.build(seed=67).sha256() != first.sha256()


def test_every_query_points_at_a_real_scope_and_a_live_expectation():
    data = fixture.build()
    memory_keys = {memory.key for memory in data.memories}
    user_keys = {user.key for user in data.users}
    app_keys = {application.key for application in data.applications}
    for query in data.queries:
        assert query.owner_key in user_keys, query.key
        assert query.app_key in app_keys, query.key
        assert data.application(query.app_key).owner_key == query.owner_key, query.key
        if query.kind == "positive":
            assert query.expected_memory in memory_keys, query.key
            expected = data.memory(query.expected_memory)
            assert (expected.owner_key, expected.app_key) == (query.owner_key, query.app_key)
            assert query.expected_memory != data.deleted_memory, query.key
            assert expected in data.live_memories_in_scope(query.owner_key, query.app_key), query.key
        else:
            assert query.expected_memory is None, query.key
            assert query.forbidden_memories, query.key
    assert data.deleted_memory in memory_keys
    assert set(data.revisions) <= memory_keys
    assert data.candidate.state == "pending"
    assert {query.kind for query in data.queries} == {"positive", "negative"}


# ---------------------------------------------------------------------------
# the shared deterministic embedder (the same one Mem0 is wired to)
# ---------------------------------------------------------------------------
def test_the_embedder_is_deterministic_normalised_and_self_matching():
    text = "My flight preference is an aisle seat on morning departures."
    first = embedder.embed(text)
    assert first == embedder.embed(text)
    assert len(first) == embedder.DIMENSIONS
    assert abs(sum(value * value for value in first) - 1.0) < 1e-9
    assert abs(embedder.cosine(first, first) - 1.0) < 1e-9
    assert first != embedder.embed("A completely different sentence about trains.")


def test_the_shared_embedder_ranks_every_positive_query_expected_first():
    data = fixture.build()
    for query in data.queries:
        if query.kind != "positive":
            continue
        candidates = {
            memory.key: embedder.embed(data.text_of(memory))
            for memory in data.live_memories_in_scope(query.owner_key, query.app_key)
        }
        ranked = embedder.rank(query.text, candidates, top_k=3)
        assert ranked[0] == query.expected_memory, (query.key, ranked)


def test_a_negative_query_never_ranks_a_forbidden_memory_in_its_scope():
    data = fixture.build()
    for query in data.queries:
        if query.kind != "negative":
            continue
        candidates = {
            memory.key: embedder.embed(data.text_of(memory))
            for memory in data.live_memories_in_scope(query.owner_key, query.app_key)
        }
        ranked = embedder.rank(query.text, candidates, top_k=3)
        assert not (set(ranked) & set(query.forbidden_memories)), (query.key, ranked)


# ---------------------------------------------------------------------------
# verdict rules: default is keep PostgreSQL
# ---------------------------------------------------------------------------
def _pg(hit1: float = 1.0, hit3: float = 1.0, p95: float = 4.0) -> dict:
    return {"status": "run", "hit_at_1": hit1, "hit_at_3": hit3, "p95_ms": p95}


def _mem0(
    *, status: str = "run", evidence_level: str = "real", hit1: float = 1.0,
    hit3: float = 1.0, p95: float = 4.0, residual: list[str] | None = None,
) -> dict:
    record = {
        "status": status,
        "evidence_level": evidence_level,
        "hit_at_1": hit1,
        "hit_at_3": hit3,
        "p95_ms": p95,
        "cleanup": {"residual_body_copies": residual if residual is not None else []},
    }
    if status != "run":
        for key in ("hit_at_1", "hit_at_3", "p95_ms"):
            record.pop(key)
    return record


def test_mem0_that_was_not_run_is_not_adopted_on_no_evidence():
    verdict = decision_module.decide(_pg(), _mem0(status="blocked", residual=[]), f11_gate_passed=False)
    assert verdict["decision"] == "insufficient_evidence"
    assert any("did not run" in reason for reason in verdict["reasons"])


def test_an_unclean_delete_disqualifies_mem0_even_with_better_recall():
    verdict = decision_module.decide(
        _pg(hit1=0.75, hit3=0.9),
        _mem0(hit1=1.0, hit3=1.0, p95=1.0, residual=["sqlite:history.new_memory"]),
        f11_gate_passed=False,
    )
    assert verdict["decision"] == "do_not_adopt"
    assert any("delete" in reason or "copy" in reason for reason in verdict["reasons"])


def test_no_measurable_benefit_keeps_postgres():
    verdict = decision_module.decide(
        _pg(hit1=1.0, hit3=1.0, p95=3.0),
        _mem0(hit1=1.0, hit3=1.0, p95=3.0, residual=[]),
        f11_gate_passed=True,
    )
    assert verdict["decision"] == "do_not_adopt"
    assert any("benefit" in reason for reason in verdict["reasons"])


def test_a_stub_only_benefit_is_insufficient_evidence_not_adoption():
    verdict = decision_module.decide(
        _pg(hit1=0.5, hit3=0.75, p95=10.0),
        _mem0(hit1=1.0, hit3=1.0, p95=1.0, evidence_level="stub", residual=[]),
        f11_gate_passed=False,
    )
    assert verdict["decision"] == "insufficient_evidence"
    assert any("stub" in reason or "F11" in reason for reason in verdict["reasons"])


def test_adoption_needs_benefit_clean_copies_real_evidence_and_the_f11_gate():
    better = _mem0(hit1=1.0, hit3=1.0, p95=1.0, evidence_level="real", residual=[])
    without_gate = decision_module.decide(_pg(hit1=0.5, hit3=0.75, p95=10.0), better, f11_gate_passed=False)
    assert without_gate["decision"] == "insufficient_evidence"
    assert any("F11" in reason for reason in without_gate["reasons"])
    with_gate = decision_module.decide(_pg(hit1=0.5, hit3=0.75, p95=10.0), better, f11_gate_passed=True)
    assert with_gate["decision"] == "adopt"
    assert with_gate["remaining_gates"] == []


def test_the_pg_baseline_alone_never_yields_adoption():
    verdict = decision_module.decide(_pg(), None, f11_gate_passed=True)
    assert verdict["decision"] == "insufficient_evidence"
    assert any("did not run" in reason for reason in verdict["reasons"])


def test_the_verdict_reads_the_nested_metrics_of_a_real_result_record():
    """The runner passes full system records (recall/latency nested), not flat dicts."""
    pg = {
        "status": "run",
        "recall": {"hit_at_1": 0.75, "hit_at_3": 0.875},
        "latency": {"p95_ms": 10.0},
        "cleanup": {"residual_body_copies": ["object:sealed/aa"]},
    }
    mem0 = {
        "status": "run",
        "evidence_level": "real",
        "recall": {"hit_at_1": 1.0, "hit_at_3": 1.0},
        "latency": {"p95_ms": 2.0},
        "cleanup": {"residual_body_copies": []},
    }
    verdict = decision_module.decide(pg, mem0, f11_gate_passed=True)
    assert verdict["benefit"]["pg_hit_at_1"] == 0.75
    assert verdict["benefit"]["mem0_hit_at_1"] == 1.0
    assert verdict["benefit"]["pg_p95_ms"] == 10.0
    assert verdict["benefit"]["mem0_p95_ms"] == 2.0
    assert verdict["benefit"]["recall_gain"] is True
    assert verdict["decision"] == "adopt"


# ---------------------------------------------------------------------------
# machine-readable record: an unexecuted experiment is not "complete"
# ---------------------------------------------------------------------------
def test_a_record_refuses_false_completion_and_adoption():
    good = records.sample_record(
        pg=records.system_run(
            evidence_level="real",
            recall={"hit_at_1": 1.0, "hit_at_3": 1.0, "positive_queries": 8, "negative_queries": 4, "forbidden_hits": 0},
            latency={"p50_ms": 1.0, "p95_ms": 2.0, "runs": 168},
            cleanup={"residual_body_copies": ["object:sealed/aa"]},
            commands=["python experiments/mem0/run_experiment.py both"],
        ),
        mem0=records.system_run(
            evidence_level="stub",
            recall={"hit_at_1": 1.0, "hit_at_3": 1.0, "positive_queries": 8, "negative_queries": 4, "forbidden_hits": 0},
            latency={"p50_ms": 1.0, "p95_ms": 2.0, "runs": 168},
            cleanup={"residual_body_copies": ["sqlite:history.new_memory"]},
            commands=["python experiments/mem0/run_experiment.py both"],
        ),
        decision={"decision": "adopt", "reasons": ["made up"], "remaining_gates": []},
    )
    errors = records.validate(good)
    assert any("adopt" in error for error in errors), errors

    good["decision"] = {"decision": "do_not_adopt", "reasons": ["original copy in history"], "remaining_gates": ["F11"]}
    assert records.validate(good) == []

    complete = json.loads(json.dumps(good))
    complete["experiment_status"] = "complete"
    assert any("complete" in error for error in records.validate(complete))

    blocked = records.sample_record(
        pg=records.system_blocked("isolated PostgreSQL cluster is not running"),
        mem0=records.system_blocked("mem0ai is not importable in the isolated venv"),
        decision={"decision": "adopt", "reasons": [], "remaining_gates": []},
    )
    blocked_errors = records.validate(blocked)
    assert any("adopt" in error for error in blocked_errors)


def test_the_withheld_mem0_run_is_recorded_as_blocked_without_fake_numbers(tmp_path):
    out = tmp_path / "mem0.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(EXPERIMENT / "run_experiment.py"),
            "mem0",
            "--out",
            str(out),
            "--work-dir",
            str(tmp_path / "work"),
        ],
        cwd=str(PRODUCT_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.exists(), completed.stdout + completed.stderr
    record = json.loads(out.read_text(encoding="utf-8"))
    assert completed.returncode == 2, completed.stdout + completed.stderr
    mem0 = record["systems"]["mem0"]
    assert mem0["status"] == "blocked"
    assert "mem0ai" in mem0["reason"]
    assert "recall" not in mem0 and "latency" not in mem0
    assert record["decision"]["decision"] == "insufficient_evidence"
    assert records.validate(record) == []


# ---------------------------------------------------------------------------
# the committed result of the executed run
# ---------------------------------------------------------------------------
def test_the_committed_evidence_is_a_stub_mem0_run_that_does_not_adopt():
    path = EXPERIMENT / "evidence" / "t066-both.json"
    assert path.exists(), "the executed experiment result is part of the deliverable"
    record = json.loads(path.read_text(encoding="utf-8"))
    assert records.validate(record) == []
    assert record["experiment_status"] == "executed"
    assert record["systems"]["pg"]["status"] == "run"
    assert record["systems"]["pg"]["evidence_level"] == "real"
    assert record["systems"]["mem0"]["status"] == "run"
    assert record["systems"]["mem0"]["evidence_level"] == "stub"
    assert record["decision"]["decision"] == "do_not_adopt"
    assert record["decision"]["remaining_gates"] == ["F11"]
    residual = record["systems"]["mem0"]["cleanup"]["residual_body_copies"]
    assert any("history" in item for item in residual), residual
    # The committed record must not contain a memory body (only ids/hashes/metrics).
    text = path.read_text(encoding="utf-8")
    for memory in fixture.build().memories:
        assert memory.text not in text
    assert fixture.build().candidate.text not in text


# ---------------------------------------------------------------------------
# the real PostgreSQL baseline: measurements and copy-cleanup enumeration
# ---------------------------------------------------------------------------
def test_the_pg_baseline_measures_recall_latency_storage_and_deletion_cleanup(
    fresh_database, tmp_path, go_binaries
):
    dsn, dbname = fresh_database
    binaries = st.build_go_binaries(
        tmp_path / "bin", {"pgdriver": "./experiments/mem0/pgdriver"}
    )
    out = tmp_path / "pg.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(EXPERIMENT / "run_experiment.py"),
            "pg",
            "--dsn",
            dsn,
            "--admin",
            str(go_binaries["admin"]),
            "--pgdriver",
            str(binaries["pgdriver"]),
            "--object-dir",
            str(tmp_path / "objects"),
            "--out",
            str(out),
            "--iterations",
            "21",
        ],
        cwd=str(PRODUCT_ROOT),
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    record = json.loads(out.read_text(encoding="utf-8"))
    assert records.validate(record) == []
    pg = record["systems"]["pg"]
    assert pg["status"] == "run"
    assert pg["evidence_level"] == "real"
    assert pg["recall"]["hit_at_1"] == 1.0
    assert pg["recall"]["hit_at_3"] == 1.0
    assert pg["recall"]["forbidden_hits"] == 0
    assert pg["latency"]["runs"] >= 20
    assert pg["latency"]["p95_ms"] >= pg["latency"]["p50_ms"]

    cleanup = pg["cleanup"]
    deleted = record["fixture"]["deleted_memory"]
    assert deleted == fixture.build().deleted_memory
    assert cleanup["logical"]["hits"] == [], cleanup["logical"]
    assert cleanup["tombstone"]["deleted_at"], cleanup["tombstone"]
    assert cleanup["deletion_job"]["state"] == "pending"
    # F11/T069: the product's remaining scopes are the contract vocabulary; the
    # experiment still shows the sealed object as the outstanding copy.
    assert "object_store" in cleanup["deletion_job"]["remaining_scopes"]
    # The sealed body is still one real file: T058 stops recall synchronously and
    # leaves the physical purge to F07. The experiment must show that, not hide it.
    assert cleanup["object_files"], cleanup
    assert any(item["contains_body"] for item in cleanup["object_files"])
    assert "object:" in " ".join(cleanup["residual_body_copies"])
    # The superseded revision body is the same known debt, with its own copy.
    assert cleanup["revision_leftovers"]["object_files"], cleanup["revision_leftovers"]
    # And the isolated experiment never touches the product database.
    assert dbname.startswith("orchara_")
