"""T103 acceptance: one recorded result, two verdicts, and the real gate.

The ticket compares a deterministic hard checker with a semantic quality score on
the *same* recorded evaluation result and proves that a high quality score cannot
override a hard failure at the real publication gate.

Three layers, kept separate on purpose:

1. **The real recorded result** is produced by the real runner: the compiled
   maintenance pool collects two ``start -> end`` cases and the real
   ``cmd/supervisor exec`` executor runs them. The completion guard seals the
   report. One case asserts exactly what the run produced (state ``succeeded``
   and the sealed output ``{}``) and passes; the other asserts a ``total`` the
   real output does not contain, so its ``json_equals`` hard check fails. The
   hard verdict is recomputed independently by
   ``probes/f13b_quality/compare.py`` from ``cases[].assertions``; the product's
   status is only compared afterwards, never trusted.
2. **The quality override** re-seals the same case evidence with
   ``quality = {informational: true, adapter: "heuristic-local/1", score: 1.0}``
   through the real job-scoped report path, so the *completion guard* accepts a
   failed report whose quality slot is maximal. The real
   ``POST /applications/{id}/submit`` must still refuse it.
3. **The reverse control** submits a passed five-category report whose quality
   slot is 0.0; the publication gate must allow it. Quality is advisory only.

``heuristic-local/1`` is a deterministic local scorer built for this ticket
(:mod:`probes.f13b_quality.compare`), **not** an approved judge. No judge
endpoint or credential exists on this host, so the real judge run is
``not_run``/``blocked`` and is reported that way.

The sealed bytes of every recorded result are exported when
``ORCHARA_T103_EVIDENCE_DIR`` is set (the ticket's evidence run uses
``E:\\Orchara\\.scratch\\f13b\\T103``); otherwise the test stays inside ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from support import f00b, t052, t062, t068
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)

PRODUCT_ROOT = st.PRODUCT_ROOT
COMPARE_PROBE = PRODUCT_ROOT / "probes" / "f13b_quality" / "compare.py"
QUALITY_ADAPTER = "heuristic-local/1"
RUNNER_EVALUATOR = t062.RUNNER_EVALUATOR
TERMINAL_RUN_STATES = {"succeeded", "failed", "cancelled", "recovery_pending", "needs_reconciliation"}

# The two halves of one recorded result. Both declare the same two hard checks
# (the real run state and a value in the run's sealed output document). A
# start->end minimal chain really executes to `succeeded` and seals the empty
# output `{}`, so:
#   * `t103-hard-pass` asserts exactly what the run produced -> a genuine pass;
#   * `t103-hard-fail` asserts a total the output does not contain -> the
#     `json_equals` hard check fails on the very same record.
HARD_PASS_CASE = "t103-hard-pass"
HARD_FAIL_CASE = "t103-hard-fail"
#: sha256("{}"), the sealed output of the real start->end execution.
EMPTY_OUTPUT_SHA256 = "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"


# ---------------------------------------------------------------------------
# the independent probe: the hard verdict is recomputed, not read
# ---------------------------------------------------------------------------
def _run_compare(
    tmp_path: Path, document: dict, *extra: str, raw: bytes | None = None
) -> dict:
    path = tmp_path / ("report-" + uuid.uuid4().hex + ".json")
    if raw is not None:
        # Hash the sealed artifact's exact bytes, never a re-serialisation.
        path.write_bytes(raw)
    else:
        path.write_bytes(
            json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
    completed = subprocess.run(
        [sys.executable, str(COMPARE_PROBE), "--report", str(path), "--json", *extra],
        cwd=str(PRODUCT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    return json.loads(completed.stdout)


def test_compare_probe_recomputes_the_verdict_from_the_assertions(tmp_path: Path) -> None:
    """A declared `passed` cannot survive a failed assertion in the independent probe.

    The document is internally inconsistent on purpose (status `passed`, a case
    `failed`, a failed assertion) and carries a maximal quality score. The probe
    must derive `failed` from the assertion evidence, report the disagreement with
    the declared status, and leave both the quality score and any vendor cost out
    of the hard calculation.
    """
    document = {
        "report_version": 1,
        "kind": "evaluation",
        "evaluation_kind": "application",
        "evaluation_id": str(uuid.uuid4()),
        "dataset_artifact_id": str(uuid.uuid4()),
        "dataset_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "status": "passed",
        "checks": ["run_state"],
        "case_count": 1,
        "synthetic": False,
        "evaluator": RUNNER_EVALUATOR,
        "provenance": {
            "snapshot_id": str(uuid.uuid4()),
            "application_id": str(uuid.uuid4()),
            "draft_revision": 1,
        },
        "quality": {
            "informational": True,
            "adapter": QUALITY_ADAPTER,
            "score": 1.0,
            "note": "deterministic local proxy score; not a judge",
        },
        "cases": [
            {
                "id": "synthetic-case",
                "status": "failed",
                "verdict": "failed",
                "reason_code": "assertion_failed",
                "detail": "1 of 1 assertions failed against the run's collected truth",
                "assertions": [
                    {
                        "kind": "run_state",
                        "expected": "succeeded",
                        "actual": "failed",
                        "passed": False,
                    }
                ],
                "facts": {
                    "run_id": str(uuid.uuid4()),
                    "run_state": "failed",
                    "run_error_code": "tool_unsupported_graph",
                    "effect_counts": {},
                    "citation_sources": [],
                },
            }
        ],
    }

    result = _run_compare(tmp_path, document)
    hard = result["hard_checker"]
    assert hard["verdict"] == "failed", hard
    assert hard["declared_product_status"] == "passed", hard
    assert hard["agrees_with_declared_status"] is False, hard
    assert hard["quality_used"] is False, hard
    assert hard["cases"][0]["recomputed_status"] == "failed", hard["cases"]
    recomputed = hard["cases"][0]["assertions"][0]
    assert recomputed["recomputed_passed"] is False, recomputed
    assert recomputed["agrees_with_recorded_outcome"] is True, recomputed
    # The quality score is high and local; it changes nothing about the verdict.
    assert result["quality"]["score"] == 1.0, result["quality"]
    assert result["quality"]["judge_run"] == "not_run", result["quality"]
    assert result["outbound"]["connections"] == 0, result["outbound"]
    assert result["cost"]["vendor_tokens"] == 0, result["cost"]
    assert result["cost"]["usd"] == 0.0, result["cost"]


# ---------------------------------------------------------------------------
# the real recorded result
# ---------------------------------------------------------------------------
def _evidence_dir() -> Path | None:
    raw = os.environ.get("ORCHARA_T103_EVIDENCE_DIR")
    if not raw:
        return None
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _export(name: str, payload) -> None:
    directory = _evidence_dir()
    if directory is None:
        return
    content = (
        payload if isinstance(payload, bytes)
        else json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    )
    (directory / name).write_bytes(content)


def _recorded_cases() -> list[dict]:
    """One hard pass and one hard fail, both decided against the real run.

    The pass case asserts the output the run really seals (``{}``); the fail case
    asserts a ``total`` the real output does not carry, so its ``json_equals``
    outcome is a genuine hard failure on the same recorded result.
    """
    return [
        t052.evaluation_case(
            case_id=HARD_PASS_CASE,
            assertions=[
                t052.assertion_run_state("succeeded"),
                t052.assertion_json("", {}),
            ],
            input_value={"prompt": "record the output the chain really produces"},
        ),
        t052.evaluation_case(
            case_id=HARD_FAIL_CASE,
            assertions=[
                t052.assertion_run_state("succeeded"),
                t052.assertion_json("", {"total": "100.00"}),
            ],
            input_value={"prompt": "expect a total the chain did not produce"},
        ),
    ]


def _run_genuine_evaluation(stack, owner, cases: list[dict], *, log: Path) -> dict:
    """Run one evaluation through the real pools + real supervisor executor.

    The dataset upload and the evaluation submission happen *before* the pool
    starts: the pool claims every kind it serves (including artifact_finalize), so
    starting it first would race the deterministic finalize of the dataset.
    """
    evaluation_id = t062.submit_evaluation_for(stack, owner, cases, case_timeout="60s")
    process, handle = t052.start_pools(stack, log_path=log, case_timeout="60s")
    try:
        deadline = time.time() + 180
        while time.time() < deadline:
            evaluation = t052.evaluation_row(stack.dbname, evaluation_id)
            if evaluation["state"] in {"succeeded", "failed", "cancelled"}:
                break
            completed = t052.run_supervisor_exec(
                stack, t052.supervisor_exec_env(stack), timeout=180
            )
            report = t052.exec_report(completed)
            if report.get("status") == "idle":
                time.sleep(0.05)
        else:  # pragma: no cover - a hung executor is a failure, never a pass
            raise AssertionError(
                "the genuine evaluation never became terminal: "
                f"{t052.evaluation_row(stack.dbname, evaluation_id)}"
            )
    finally:
        t052.stop_process(process, handle, log)

    evaluation = t052.evaluation_row(stack.dbname, evaluation_id)
    assert evaluation["state"] == "succeeded", evaluation
    assert evaluation["report_id"], evaluation
    report = t052.report_row(stack.dbname, evaluation["report_id"])
    raw = t062.sealed_bytes(stack, report["result_artifact_id"])
    sealed = json.loads(raw.decode("utf-8"))
    return {
        "evaluation": evaluation,
        "report": report,
        "sealed": sealed,
        "raw": raw,
        "sha256": t062.artifact_row(stack.dbname, report["result_artifact_id"])["sha256"],
    }


def test_the_real_recorded_result_is_a_hard_failure_and_a_local_quality_one(
    t103_stack, tmp_path: Path
) -> None:
    """A genuine runner report: hard checker fails, the local quality proxy says 1.0.

    The product status, the independently recomputed status and the per-assertion
    truth must agree; the report's own quality slot must stay honest
    (``not_installed``/null), because no judge adapter is installed in this build.
    """
    owner = t052.minimal_owner(t103_stack, login="t103-real-owner")
    recorded = _run_genuine_evaluation(
        t103_stack, owner, _recorded_cases(), log=tmp_path / "pools-real.log"
    )

    # 1. The product's own verdict is a *business* failure, and the job succeeded.
    assert recorded["report"]["status"] == "failed", recorded["report"]
    assert recorded["evaluation"]["state"] == "succeeded", recorded["evaluation"]
    sealed = recorded["sealed"]
    assert sealed["evaluator"] == RUNNER_EVALUATOR, sealed
    assert sealed["synthetic"] is False, sealed
    assert sealed["case_count"] == 2, sealed

    # 2. The quality slot is the build's honest empty slot, never a fabricated score.
    quality_slot = sealed["quality"]
    assert quality_slot["informational"] is True, quality_slot
    assert quality_slot["adapter"] == "not_installed", quality_slot
    assert quality_slot["score"] is None, quality_slot

    # 3. The recorded case evidence itself: one hard pass, one hard fail.
    statuses = {case["id"]: case["status"] for case in sealed["cases"]}
    assert statuses == {HARD_PASS_CASE: "passed", HARD_FAIL_CASE: "failed"}, statuses
    cases = {case["id"]: case for case in sealed["cases"]}
    for case in sealed["cases"]:
        assert case["facts"]["run_id"], case
        assert case["facts"]["run_state"] == "succeeded", case["facts"]
        assert case["facts"].get("run_error_code", "") == "", case["facts"]
        assert case["facts"]["output_artifact_id"], case["facts"]
        assert case["facts"]["output_sha256"] == EMPTY_OUTPUT_SHA256, case["facts"]
        # The sealed document has no citations-collected flag; a collected empty
        # set is a Go nil slice, i.e. null.
        assert case["facts"]["citation_sources"] in (None, []), case["facts"]
        # The real run really exists and really succeeded in platform rows.
        rows = t052.st.sql_rows(
            t103_stack.dbname,
            "SELECT run_id::text AS run_id, state FROM platform.evaluation_case_runs "
            f"WHERE evaluation_id = {t052.st.quote_literal(recorded['evaluation']['id'])}::uuid "
            f"AND case_id = {t052.st.quote_literal(case['id'])}",
        )
        assert len(rows) == 1 and rows[0]["run_id"] == case["facts"]["run_id"], rows
        run = t052.run_row(t103_stack.dbname, case["facts"]["run_id"])
        assert run["state"] == "succeeded", run
        assert run["output_artifact_id"] == case["facts"]["output_artifact_id"], run
        assert run["execution_snapshot_id"] == recorded["evaluation"]["snapshot_id"], run
        assert run["manifest_sha256"] == sealed["manifest_sha256"], run

    # The hard failure is an output-value mismatch, not a missing fact: the run
    # state assertion passed and only the declared `total` failed.
    fail_outcomes = {o["kind"]: o for o in cases[HARD_FAIL_CASE]["assertions"]}
    assert fail_outcomes["run_state"]["passed"] is True, fail_outcomes["run_state"]
    assert fail_outcomes["json_equals"]["passed"] is False, fail_outcomes["json_equals"]
    assert fail_outcomes["json_equals"]["expected"] == '{"total":"100.00"}', fail_outcomes
    assert fail_outcomes["json_equals"]["actual"] == "{}", fail_outcomes
    pass_outcomes = {o["kind"]: o for o in cases[HARD_PASS_CASE]["assertions"]}
    assert all(o["passed"] is True for o in pass_outcomes.values()), pass_outcomes

    # 4. The independent probe recomputes the same hard verdict from the assertions.
    compared = _run_compare(tmp_path, sealed, raw=recorded["raw"])
    hard = compared["hard_checker"]
    assert hard["verdict"] == "failed", hard
    assert hard["declared_product_status"] == "failed", hard
    assert hard["agrees_with_declared_status"] is True, hard
    assert hard["quality_used"] is False, hard
    assert compared["report"]["sha256"] == recorded["sha256"], compared["report"]
    for case in hard["cases"]:
        assert case["recomputed_status"] == statuses[case["id"]], case
        for outcome in case["assertions"]:
            # The product's recorded `passed` and the probe's recomputation agree.
            assert outcome["agrees_with_recorded_outcome"] is True, outcome

    # 5. A deterministic local quality proxy scores the same record 1.0: it is
    #    evidence completeness, not correctness, and it is *not* a judge run.
    assert compared["quality"]["score"] == 1.0, compared["quality"]
    assert compared["quality"]["judge_run"] == "not_run", compared["quality"]
    assert compared["outbound"]["connections"] == 0, compared["outbound"]
    assert compared["cost"]["vendor_tokens"] == 0, compared["cost"]
    assert compared["cost"]["usd"] == 0.0, compared["cost"]

    _export("t103-real-recorded-report.json", recorded["raw"])
    _export(
        "t103-real-recorded-summary.json",
        {
            "evaluation_id": recorded["evaluation"]["id"],
            "report_id": recorded["report"]["id"],
            "report_status": recorded["report"]["status"],
            "result_artifact_id": recorded["report"]["result_artifact_id"],
            "sealed_sha256": recorded["sha256"],
            "manifest_sha256": sealed["manifest_sha256"],
            "dataset_sha256": sealed["dataset_sha256"],
            "snapshot_id": recorded["evaluation"]["snapshot_id"],
            "hard_verdict_recomputed": hard["verdict"],
            "local_quality_score": compared["quality"]["score"],
        },
    )


# ---------------------------------------------------------------------------
# the quality override at the real publication gate
# ---------------------------------------------------------------------------
def _drive_case_runs(
    stack, evaluation_id: str, *, deadline_s: float = 180.0, log: Path | None = None
) -> list[dict]:
    """Execute the crafted evaluation's real case runs with the real executor."""
    env = t052.supervisor_exec_env(stack)
    deadline = time.time() + deadline_s
    attempts: list[str] = []
    while time.time() < deadline:
        rows = t052.case_run_rows(stack.dbname, evaluation_id)
        states = {
            row["case_id"]: t052.run_row(stack.dbname, row["run_id"])["state"]
            for row in rows
            if row["run_id"]
        }
        if len(states) == 2 and all(state in TERMINAL_RUN_STATES for state in states.values()):
            if log is not None:
                log.write_text("\n".join(attempts), encoding="utf-8")
            return rows
        completed = t052.run_supervisor_exec(stack, env, timeout=180)
        attempts.append(
            f"exit={completed.returncode}\nstdout={completed.stdout}\nstderr={completed.stderr}"
        )
        report = t052.exec_report(completed)
        if report.get("status") == "idle":
            time.sleep(0.05)
    if log is not None:
        log.write_text("\n".join(attempts), encoding="utf-8")
    raise AssertionError(
        "the crafted evaluation's runs never became terminal: "
        f"{t052.case_run_rows(stack.dbname, evaluation_id)}; last exec: "
        f"{(attempts[-1] if attempts else 'none')[-2000:]}"
    )


def _start_artifact_finalize_pool(stack, *, log: Path):
    """A real maintenance process serving only ``artifact_finalize``.

    The supervisor registers each run's final output artifact and then waits for
    the platform to seal it; without the real finalize pool the run times out as
    ``sandbox_failed``. The pool is restricted to ``artifact_finalize`` so it can
    never claim the evaluation job the test itself holds.
    """
    env = f00b.maintenance_env(
        stack, heartbeat_interval="50ms", extra={"ORCHARA_BACKGROUND_RETRY_BACKOFF": "50ms"}
    )
    handle = log.open("w", encoding="utf-8", newline="\n")
    process = subprocess.Popen(
        [
            str(stack.binaries["maintenance"]),
            "run",
            "--kinds",
            "artifact_finalize",
            "--poll-interval",
            "20ms",
            "--system-io-slots",
            "2",
            "--work-slots",
            "1",
        ],
        cwd=PRODUCT_ROOT,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return process, handle


def _crafted_quality_report(
    stack,
    owner,
    *,
    quality_score: float,
    cases: list[dict],
    observed_cases: list[dict],
    log: Path,
) -> dict:
    """Re-seal the same recorded case evidence with an informational quality score.

    ``observed_cases`` is the genuine runner report's ``cases`` array. Its
    assertion outcomes are copied verbatim (the same dataset cases ran against
    the same frozen manifest), so the crafted document differs from the recorded
    result only in the run binding and the quality slot. Every case run is
    really executed by the real supervisor before the sealed report is uploaded
    through the same job-scoped artifact path a trusted runner uses; the
    completion guard is the only thing that can accept it.
    """
    observed = {case["id"]: case for case in observed_cases}
    assert set(observed) == {case["case_id"] for case in cases}, (observed, cases)

    evaluation_id = t062.submit_evaluation_for(stack, owner, cases, case_timeout="60s")
    evaluation = t052.evaluation_row(stack.dbname, evaluation_id)
    client, lease = t062.claim_evaluation(stack)
    assert lease["target_id"] == evaluation_id, (lease, evaluation_id)

    runs: dict[str, str] = {}
    finalize_log = log.with_name(log.stem + "-artifact-finalize.log")
    finalize_process, finalize_handle = _start_artifact_finalize_pool(
        stack, log=finalize_log
    )
    try:
        for case in cases:
            created = t062.create_case_run(
                client, lease, case_id=case["case_id"], input_value={"prompt": case["case_id"]}
            )
            runs[case["case_id"]] = created["run_id"]
        _drive_case_runs(stack, evaluation_id, log=log)
    finally:
        # Stop the pool before uploading the report: the report's own finalize is
        # driven deterministically by the job-scoped upload helper.
        t052.stop_process(finalize_process, finalize_handle, finalize_log)

    report_cases = []
    for case in cases:
        run_id = runs[case["case_id"]]
        run = t052.run_row(stack.dbname, run_id)
        assert run["state"] == "succeeded", (case["case_id"], run)
        output_sha = t052.artifact_row(stack.dbname, run["output_artifact_id"])["sha256"]
        source = observed[case["case_id"]]
        # The runner's own outcomes, verbatim: same assertions, same expected and
        # actual values, same pass/fail decision.
        assertions = json.loads(json.dumps(source["assertions"]))
        for outcome in assertions:
            if outcome["kind"] == "run_state":
                assert outcome["actual"] == run["state"] == "succeeded", outcome
        report_cases.append(
            {
                "id": case["case_id"],
                "status": source["status"],
                "verdict": source["status"],
                **(
                    {}
                    if source["status"] == "passed"
                    else {
                        "reason_code": source["reason_code"],
                        "detail": source["detail"],
                    }
                ),
                "assertions": assertions,
                "facts": {
                    "run_id": run_id,
                    "run_state": run["state"],
                    "output_artifact_id": run["output_artifact_id"],
                    "output_sha256": output_sha,
                    "effect_counts": {},
                    "citation_sources": [],
                },
            }
        )
    status = "passed" if all(item["status"] == "passed" for item in report_cases) else "failed"
    finished = t068.finish_crafted_report(
        stack,
        {"evaluation": evaluation, "client": client, "lease": lease},
        report_cases,
        status=status,
        checks=["json_equals", "run_state"],
        quality={
            "informational": True,
            "adapter": QUALITY_ADAPTER,
            "score": quality_score,
            "note": "deterministic local proxy score; this value never gates the verdict",
        },
    )
    assert finished["result"].status == 200, finished["result"].text
    assert finished["result"].json["state"] == "succeeded", finished["result"].json
    evaluation = t052.evaluation_row(stack.dbname, evaluation_id)
    report = t052.report_row(stack.dbname, evaluation["report_id"])
    raw = t062.sealed_bytes(stack, report["result_artifact_id"])
    return {
        "evaluation": evaluation,
        "report": report,
        "sealed": json.loads(raw.decode("utf-8")),
        "raw": raw,
        "sha256": t062.artifact_row(stack.dbname, report["result_artifact_id"])["sha256"],
        "runs": runs,
    }


def test_quality_one_cannot_override_a_hard_failure_at_publication(
    t103_stack, tmp_path: Path
) -> None:
    """The real gate refuses a failed report whose quality score is maximal.

    The completion guard *accepts* the sealed document (it is a legitimate failed
    report recorded by the trusted job path), so the refusal can only come from
    the publication gate's own hard rules. The gate's code and details are
    captured verbatim.
    """
    owner = t052.minimal_owner(t103_stack, login="t103-quality-owner")
    # The same recorded result first: a genuine runner report with its real
    # per-case assertion evidence. Its failed status is the hard failure under
    # test; the crafted report below re-seals exactly that evidence.
    genuine = _run_genuine_evaluation(
        t103_stack, owner, _recorded_cases(), log=tmp_path / "pools-quality-real.log"
    )
    assert genuine["report"]["status"] == "failed", genuine["report"]
    recorded = _crafted_quality_report(
        t103_stack,
        owner,
        quality_score=1.0,
        cases=_recorded_cases(),
        observed_cases=genuine["sealed"]["cases"],
        log=tmp_path / "pools-quality.log",
    )
    sealed = recorded["sealed"]
    assert sealed["quality"]["informational"] is True, sealed["quality"]
    assert sealed["quality"]["adapter"] == QUALITY_ADAPTER, sealed["quality"]
    assert sealed["quality"]["score"] == 1.0, sealed["quality"]
    assert recorded["report"]["status"] == "failed", recorded["report"]

    # Independent recompute agrees with the product, and hears nothing of quality.
    compared = _run_compare(tmp_path, sealed, raw=recorded["raw"])
    assert compared["hard_checker"]["verdict"] == "failed", compared["hard_checker"]
    assert compared["hard_checker"]["agrees_with_declared_status"] is True
    assert compared["report"]["sha256"] == recorded["sha256"], compared["report"]
    assert compared["quality"]["score"] == 1.0, compared["quality"]

    # The case runs behind the refused report were really executed.
    for case_id, run_id in recorded["runs"].items():
        run = t052.run_row(t103_stack.dbname, run_id)
        assert run["state"] == "succeeded", (case_id, run)
        assert run["output_artifact_id"], (case_id, run)

    snapshot = t052.st.sql_rows(
        t103_stack.dbname,
        "SELECT manifest_sha256 FROM platform.execution_snapshots "
        f"WHERE id = {t052.st.quote_literal(recorded['evaluation']['snapshot_id'])}::uuid",
    )
    assert len(snapshot) == 1, snapshot
    manifest_sha = snapshot[0]["manifest_sha256"]

    response = t068.submit_publication(
        owner.creator,
        owner.application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=recorded["report"]["id"],
    )
    # The exact gate response, recorded so the report can quote it.
    gate = {
        "http_status": response.status,
        "body": response.json,
        "raw_text": response.text,
        "report_status": recorded["report"]["status"],
        "quality_score": sealed["quality"]["score"],
        "recomputed_hard_verdict": compared["hard_checker"]["verdict"],
    }
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "evaluation_not_passed", response.json
    assert response.json["error"]["details"]["report_status"] == "failed", response.json
    # And no review was created by the refused submission.
    assert t068.review_count(t103_stack.dbname, owner.application_id) == 0

    compared_with_gate = _run_compare(
        tmp_path,
        sealed,
        "--gate-result",
        str(_write_gate_json(tmp_path, gate)),
        raw=recorded["raw"],
    )
    assert compared_with_gate["gate"]["decision"] == "refused", compared_with_gate["gate"]
    assert compared_with_gate["gate"]["code"] == "evaluation_not_passed", compared_with_gate["gate"]

    _export("t103-quality-override-report.json", recorded["raw"])
    _export(
        "t103-quality-override-summary.json",
        {
            "evaluation_id": recorded["evaluation"]["id"],
            "report_id": recorded["report"]["id"],
            "report_status": recorded["report"]["status"],
            "sealed_sha256": recorded["sha256"],
            "manifest_sha256": sealed["manifest_sha256"],
            "quality": sealed["quality"],
            "case_runs": recorded["runs"],
        },
    )
    _export("t103-gate-refusal.json", gate)


def _write_gate_json(tmp_path: Path, gate: dict) -> Path:
    path = tmp_path / ("gate-" + uuid.uuid4().hex + ".json")
    path.write_bytes(json.dumps(gate, separators=(",", ":")).encode("utf-8"))
    return path


# ---------------------------------------------------------------------------
# the reverse control: a low quality score does not block a passed report
# ---------------------------------------------------------------------------
def test_quality_zero_does_not_block_a_passed_report_at_publication(
    t103_stack, tmp_path: Path
) -> None:
    """A passed five-category report with quality 0.0 is allowed to publish.

    The five case runs are the documented T068 review fixture (real evaluation
    rows, seeded terminal states); the sealed report still travels through the
    real completion guard and the real publication gate. The point under test is
    the gate's treatment of the quality slot: 0.0 is information, not a veto.
    """
    recorded = t068.seeded_passed_report(
        t103_stack,
        login="t103-reverse-owner",
        quality={
            "informational": True,
            "adapter": QUALITY_ADAPTER,
            "score": 0.0,
            "note": "a deliberately poor local proxy score; informational only",
        },
    )
    sealed = recorded["sealed"]
    assert recorded["report"]["status"] == "passed", recorded["report"]
    assert sealed["quality"]["score"] == 0.0, sealed["quality"]
    assert sealed["quality"]["informational"] is True, sealed["quality"]
    # A passed report has no failed case for the gate to see: every case passed.
    assert all(case["status"] == "passed" for case in sealed["cases"]), sealed["cases"]

    response = t068.submit_publication(
        recorded["session"],
        recorded["application_id"],
        expected_manifest_sha256=recorded["manifest_sha256"],
        evaluation_report_id=recorded["report"]["id"],
    )
    assert response.status == 202, response.text
    assert response.json["state"] == "submitted", response.json
    assert t068.review_count(t103_stack.dbname, recorded["application_id"]) == 1
    review = t068.review_row(t103_stack.dbname, response.json["id"])
    assert review["report_id"] == recorded["report"]["id"], review
    snapshot = json.loads(review["snapshot"])
    assert snapshot["report_status"] == "passed", snapshot
    assert snapshot["author_cases"]["satisfied"] is True, snapshot["author_cases"]

    _export(
        "t103-reverse-control-summary.json",
        {
            "evaluation_id": recorded["evaluation"]["id"],
            "report_id": recorded["report"]["id"],
            "report_status": recorded["report"]["status"],
            "quality": sealed["quality"],
            "submit_http_status": response.status,
            "submit_body": response.json,
            "review_id": response.json["id"],
            "author_cases": snapshot["author_cases"],
        },
    )
    _export("t103-reverse-control-report.json", sealed)
