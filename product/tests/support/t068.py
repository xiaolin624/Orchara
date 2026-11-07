"""F02B/T068 real-stack helpers: publication submission, review and evidence.

Everything here drives the real platform: the compiled API over real HTTP, real
PostgreSQL rows, the sealed object store, the internal mTLS job protocol, the real
``cmd/maintenance`` pools and the real ``cmd/supervisor exec`` executor with the
real WSL2/runsc sandbox.

Publication evidence is produced the way the product produces it, by the real
runner: a five-case sales evaluation (two normal cases, a bad input, an
out-of-bounds input and a genuine tool failure) whose report the real completion
guard accepts. The tool-failure case is a real sandbox startup failure
(``sandbox_unavailable``) produced by starting the supervisor for that one case
with a deliberately wrong rootfs digest; the platform error code is categorised as
a tool failure. No report assertion is authored by the test.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

from support import f00b, t025, t026, t027, t029, t033, t042, t052, t056, t062

KIND_APPLICATION_EVALUATE = t026.KIND_APPLICATION_EVALUATE
TERMINAL_RUN_STATES = {"succeeded", "failed", "cancelled", "recovery_pending", "needs_reconciliation"}
TOOL_FAILURE_CASE = "sales-tool-failure"
BAD_SANDBOX_DIGEST = "sha256:" + "0" * 64
# The sales app's own schema: a case input without a string `source` fails the
# start node before any sandbox runs, which is a genuine located bad-input case.
TOOL_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"source": {"type": "string"}},
    "required": ["source"],
    "additionalProperties": True,
}


# ---------------------------------------------------------------------------
# identities, applications
# ---------------------------------------------------------------------------
def creator(stack, *, login: str | None = None):
    admin = f00b.admin_session(stack)
    session = f00b.invite_and_activate(
        stack, admin, login or ("t068-creator-" + f00b.new_uuid()[:8]),
        roles=("creator",),
    )
    return session


def admin_session(stack):
    return f00b.admin_session(stack)


def sales_application(stack, *, login: str | None = None):
    """A genuine start -> python(sales_stats) -> end application + its owner.

    The definition uses an explicit input schema so a schema-violating case is a
    real located failure of the platform (before any sandbox starts), and the
    fixed preset tool is seeded as a real approved tool version exactly like the
    T042/T056 chains.
    """
    admin = f00b.admin_session(stack)
    session = f00b.invite_and_activate(
        stack, admin, login or ("t068-sales-" + f00b.new_uuid()[:8]),
        roles=("creator",),
    )
    t025.seed_tool_version(stack.dbname, session.principal_id, tool_id=t042.PRESET_TOOL_ID)
    t056.seed_evaluation_capacity(stack)
    application_id = t027.application_with_definition(
        session,
        t042.linear_definition(input_schema=TOOL_INPUT_SCHEMA),
        name="t068 sales chain",
    )
    return t029.Owner(admin=admin, creator=session, application_id=application_id)


def minimal_application(session, *, name: str = "t068 minimal") -> str:
    """A start -> end application. Only used where the report evidence is seeded."""
    return t027.application_with_definition(session, t027.chain_definition([]), name=name)


def owner_for(session, application_id: str):
    """The t029.Owner view of an existing creator session and application."""
    return t029.Owner(admin=None, creator=session, application_id=application_id)


# ---------------------------------------------------------------------------
# publication endpoints
# ---------------------------------------------------------------------------
def submit_path(application_id: str) -> str:
    return f"/api/v1/applications/{application_id}/submit"


def preview_path(application_id: str) -> str:
    return f"/api/v1/applications/{application_id}/publication-preview"


def decision_path(review_id: str) -> str:
    return f"/api/v1/reviews/{review_id}/decision"


def submit_publication(
    session,
    application_id: str,
    *,
    expected_manifest_sha256: str,
    evaluation_report_id: str,
    idempotency_key: str | None = None,
    omit_key: bool = False,
    body: dict | None = None,
    extra_headers: dict[str, str] | None = None,
):
    payload = body if body is not None else {
        "expected_manifest_sha256": expected_manifest_sha256,
        "evaluation_report_id": evaluation_report_id,
    }
    headers: dict[str, str] = dict(extra_headers or {})
    if not omit_key:
        headers["Idempotency-Key"] = idempotency_key or f00b.new_uuid()
    return session.client.post(
        submit_path(application_id), body=payload, csrf=session.csrf, headers=headers
    )


def preview_publication(session, application_id: str):
    return session.client.post(preview_path(application_id), csrf=session.csrf)


def decided_review(
    session,
    review_id: str,
    *,
    decision: str,
    manifest_sha256: str,
    reason: str = "reviewed against the frozen evidence",
    idempotency_key: str | None = None,
):
    headers = {"Idempotency-Key": idempotency_key or f00b.new_uuid()}
    return session.client.post(
        decision_path(review_id),
        body={"decision": decision, "manifest_sha256": manifest_sha256, "reason": reason},
        csrf=session.csrf,
        headers=headers,
    )


def list_reviews(session, *, cursor: str | None = None, limit: int | None = None):
    query = []
    if cursor is not None:
        query.append("cursor=" + cursor)
    if limit is not None:
        query.append("limit=" + str(limit))
    path = "/api/v1/reviews"
    if query:
        path += "?" + "&".join(query)
    return session.client.get(path)


def review_path(review_id: str) -> str:
    return f"/api/v1/reviews/{review_id}"


def get_review(session, review_id: str):
    return session.client.get(review_path(review_id))


# ---------------------------------------------------------------------------
# evidence: the real five-category sales evaluation
# ---------------------------------------------------------------------------
def _case(document_name: str, index: int) -> dict:
    return copy.deepcopy(t056.load_dataset(document_name)["cases"][index])


def _bad_schema_case(case_id: str, source_value) -> dict:
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "category": "sales",
        "family_id": "sales-bad-input",
        "split": "dev",
        "target": {"kind": "debug_snapshot", "id": "frozen-evaluation-snapshot"},
        "input": {
            "source": source_value,
            "expected_failure": {"error_code": "node_input_invalid"},
        },
        "fixture_id": "sales-bad-input-v1",
        "assertions": [
            {"kind": "run_state", "expected": "failed"},
            {"kind": "effect_count", "capability": "test.send", "expected": 0, "source": "stub_ledger"},
        ],
        "provenance": {
            "origin": "self_created",
            "reference": "tests/f13a/datasets",
            "license_review": "not_applicable",
        },
    }


def five_category_cases() -> list[dict]:
    """Five genuine author cases: normal/output, two bad inputs, over-limit, tool fail.

    Only the normal case and the over-limit case really enter the sandbox. The
    two bad inputs fail at the start node's input schema (before any sandbox), and
    the tool-failure case starts the executor with a wrong rootfs digest so the
    platform records a real ``sandbox_unavailable`` failure.
    """
    happy_csv = _case("sales_happy_csv.json", 0)
    over_limit = copy.deepcopy(t056.load_dataset("bad_inputs.json")["cases"][4])
    tool_failure = copy.deepcopy(happy_csv)
    tool_failure["case_id"] = TOOL_FAILURE_CASE
    tool_failure["family_id"] = "sales-tool-failure"
    tool_failure["input"] = {
        "source": TOOL_FAILURE_CASE,
        "task_input": copy.deepcopy(happy_csv["input"]["task_input"]),
        "expected_failure": {"error_code": "sandbox_unavailable"},
    }
    tool_failure["assertions"] = [
        {"kind": "run_state", "expected": "failed"},
        {"kind": "effect_count", "capability": "test.send", "expected": 0, "source": "stub_ledger"},
    ]
    return [
        happy_csv,
        _bad_schema_case("sales-bad-schema-1", 42),
        over_limit,
        tool_failure,
        _bad_schema_case("sales-bad-schema-2", 7),
    ]


def one_normal_case() -> list[dict]:
    return [_case("sales_happy_csv.json", 0)]


def _next_ready_case(dbname: str, evaluation_id: str) -> str | None:
    rows = t052.st.sql_rows(
        dbname,
        "SELECT cr.case_id FROM platform.evaluation_case_runs AS cr "
        "JOIN platform.jobs AS j ON j.run_id = cr.run_id "
        f"WHERE cr.evaluation_id = {t052.st.quote_literal(evaluation_id)}::uuid "
        "AND j.kind = 'start' AND j.state = 'ready' "
        "ORDER BY cr.ordinal LIMIT 1",
    )
    if not rows:
        return None
    return rows[0]["case_id"]


def run_supervisor_safe(stack, env: dict[str, str], *, timeout: float = 120.0) -> dict:
    """Run one real ``cmd/supervisor exec --once`` with process-tree isolation.

    ``subprocess.run(timeout=)`` kills only the direct child; a WSL/sandbox
    grandchild that inherited the pipes can keep it blocked forever on this host
    when another suite is hammering the shared sandbox. This wrapper kills the
    whole tree on timeout and returns the parsed report, so an environment stall
    is a recorded retry instead of a hung test.
    """
    process = subprocess.Popen(
        [str(stack.binaries["supervisor"]), "exec", "--once"],
        cwd=t052.PRODUCT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        try:
            process.communicate(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - last resort
            process.kill()
        raise AssertionError(
            f"supervisor exec exceeded {timeout}s and its process tree was killed"
        )
    lines = [line for line in (stdout or "").splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f"supervisor exec printed nothing; stderr={stderr}")
    return json.loads(lines[-1])


def run_adaptive_evaluation(stack, owner, cases: list[dict], *, log_path: Path) -> dict:
    """Drive one real evaluation, choosing the supervisor configuration per case.

    Every case is created and collected by the real maintenance runner; the test
    only starts the real executor for the queued case. The designated tool-failure
    case runs with a wrong rootfs digest so the sandbox refuses to start (a real
    ``sandbox_unavailable`` platform failure); every other case runs normally.
    """
    document = {"cases": cases}
    t056.upload_dataset_fixtures(stack, owner.creator, document)
    dataset_artifact = t056.upload_dataset(stack, owner.creator, t056.dataset_bytes(document))
    process, handle = t052.start_pools(stack, log_path=log_path, case_timeout="90s")
    try:
        submitted = t056.submit_dataset_artifact(stack, owner, dataset_artifact)
        evaluation_id = submitted["id"]
        deadline = time.time() + 600
        evaluation: dict = {}
        while time.time() < deadline:
            evaluation = t052.evaluation_row(stack.dbname, evaluation_id)
            if evaluation["state"] in {"succeeded", "failed", "cancelled"}:
                break
            case_id = _next_ready_case(stack.dbname, evaluation_id)
            digest = BAD_SANDBOX_DIGEST if case_id == TOOL_FAILURE_CASE else None
            env = t052.supervisor_exec_env(stack, digest=digest, timeout="60s")
            try:
                exec_report = run_supervisor_safe(stack, env, timeout=60)
            except AssertionError as failure:
                # An environment stall: the evaluation's own case timeout will
                # record not_run and the retry wrapper re-submits. Never a pass.
                print(f"T068: supervisor stall for case {case_id!r}: {failure}")
                time.sleep(0.2)
                continue
            if exec_report.get("status") == "idle":
                time.sleep(0.05)
        else:
            raise AssertionError(f"the evaluation never finished: {evaluation}")
        if not evaluation.get("report_id"):
            raise AssertionError(f"the evaluation produced no report: {evaluation}")
        report = t052.report_row(stack.dbname, evaluation["report_id"])
        sealed = t052.report_document(stack, report["result_artifact_id"])
        return {"evaluation": evaluation, "report": report, "sealed": sealed}
    finally:
        t026.stop_process(process, handle, log_path)


class SandboxEnvironmentUnavailable(RuntimeError):
    """The host has no WSL2/runsc runtime at all.

    This is the only condition the genuine-runner test may turn into a skip. It is
    decided by the pre-flight probe (:func:`genuine_sandbox_present`), never by a
    verdict the product recorded: a supervisor that starts too slowly records
    ``run_timeout``/``not_run`` exactly like a missing runtime used to, and a
    product-recorded timeout must fail the test instead of skipping it.
    """


VERDICT_PASSED = "passed"
VERDICT_ENVIRONMENT_ABSENT = "environment_absent"
VERDICT_PRODUCT_FAILURE = "product_failure"


def classify_genuine_outcome(report_status: str, *, sandbox_present: bool) -> str:
    """Decide pass / skip / fail for one genuine-runner outcome.

    A non-passed report is a *product* outcome — a slow or absent supervisor
    records ``run_timeout`` — and only the pre-flight environment probe that found
    no WSL2/runsc at all may make it the skippable environment absence. The test
    over this classifier pins that distinction.
    """
    if report_status == "passed":
        return VERDICT_PASSED
    if not sandbox_present:
        return VERDICT_ENVIRONMENT_ABSENT
    return VERDICT_PRODUCT_FAILURE


def genuine_sandbox_present() -> bool:
    """Pre-flight: WSL2 exists on the host and its guest has runsc installed."""
    return shutil.which("wsl") is not None and t052.sandbox_available()


def _genuine_outcome(stack, owner, cases: list[dict], *, log_path: Path, attempts: int = 3) -> dict:
    """Run the genuine evaluation, never laundering a product verdict into a skip.

    A missing WSL2/runsc runtime is probed before the run and is the only
    condition that may skip. A product-recorded non-passed report (a supervisor
    started too slowly and the product wrote ``run_timeout``/``not_run``) is a
    **failure**: it is retried only to absorb contention on the shared sandbox and
    the retry budget ends in an AssertionError, never in ``pytest.skip``.
    """
    if not genuine_sandbox_present():
        raise SandboxEnvironmentUnavailable(
            "WSL2/runsc is not installed on this host: "
            "`wsl -d Ubuntu-24.04 -u root -e bash -lc 'runsc --version'` failed"
        )
    last: dict = {}
    for attempt in range(1, attempts + 1):
        last = run_adaptive_evaluation(stack, owner, cases, log_path=log_path)
        verdict = classify_genuine_outcome(
            last["report"]["status"], sandbox_present=True
        )
        if verdict == VERDICT_PASSED:
            return last
        print(
            f"T068: genuine evaluation attempt {attempt}/{attempts} recorded a product "
            f"failure {last['report']['status']!r}: " + _case_evidence(last)
        )
    raise AssertionError(
        "the genuine evaluation recorded a product failure "
        f"({last['report']['status']!r}) on all {attempts} attempts; this is not a "
        f"missing environment (WSL2/runsc is installed): {_case_evidence(last)}"
    )


def _case_evidence(outcome: dict) -> str:
    return ", ".join(
        f"{case.get('id')}={case.get('reason_code') or case.get('status')}"
        for case in outcome.get("sealed", {}).get("cases", [])
    )


def genuine_sales_report(stack, *, login: str | None = None, log_path: Path) -> dict:
    """The end-to-end evidence: a real five-category runner report."""
    owner = sales_application(stack, login=login)
    outcome = _genuine_outcome(stack, owner, five_category_cases(), log_path=log_path)
    assert outcome["report"]["status"] == "passed", outcome["sealed"]
    snapshot = t027.snapshot_row(stack.dbname, outcome["evaluation"]["snapshot_id"])
    return {
        "owner": owner,
        "session": owner.creator,
        "application_id": owner.application_id,
        "evaluation": outcome["evaluation"],
        "report": outcome["report"],
        "sealed": outcome["sealed"],
        "manifest_sha256": snapshot["manifest_sha256"],
        "snapshot": snapshot,
    }


def under_covered_report(stack, *, login: str | None = None, log_path: Path) -> dict:
    """A genuine passed report with only one case (fails the five-case gate)."""
    owner = sales_application(stack, login=login)
    outcome = _genuine_outcome(stack, owner, one_normal_case(), log_path=log_path)
    assert outcome["report"]["status"] == "passed", outcome["sealed"]
    snapshot = t027.snapshot_row(stack.dbname, outcome["evaluation"]["snapshot_id"])
    return {
        "session": owner.creator,
        "application_id": owner.application_id,
        "evaluation": outcome["evaluation"],
        "report": outcome["report"],
        "sealed": outcome["sealed"],
        "manifest_sha256": snapshot["manifest_sha256"],
        "snapshot": snapshot,
    }


# ---------------------------------------------------------------------------
# evidence: guard-accepted sealed reports for the negative binding paths
# ---------------------------------------------------------------------------
def start_evaluation(stack, session, application_id: str, cases: list[dict]) -> dict:
    """Submit one evaluation and claim its job without running the maintenance pool."""
    dataset_artifact = t052.upload_dataset(
        stack, session, t052.dataset_document(cases)
    )
    submitted = t052.submit_evaluation(
        session,
        application_id,
        draft_revision=t027.draft_revision(session, application_id),
        dataset_artifact_id=dataset_artifact,
    )
    assert submitted.status == 202, submitted.text
    evaluation = t052.evaluation_row(stack.dbname, submitted.json["id"])
    client, lease = t062.claim_evaluation(stack)
    return {"application_id": application_id, "evaluation": evaluation, "client": client, "lease": lease}


def finish_crafted_report(
    stack,
    started: dict,
    report_cases: list[dict],
    *,
    status: str = "passed",
    checks: list[str] | None = None,
    evaluator: str | None = None,
    quality: dict | None = None,
) -> dict:
    """Upload one crafted sealed report and let the real completion guard decide.

    ``quality`` is the optional informational quality slot (F13B/T103). The guard
    does not read it; it exists so a test can prove that a recorded score cannot
    change the verdict or the publication decision.
    """
    document = t062.crafted_document(
        stack,
        started["evaluation"],
        status=status,
        cases=report_cases,
        checks=checks,
        **({"evaluator": evaluator} if evaluator is not None else {}),
        **({"quality": quality} if quality is not None else {}),
    )
    artifact_id = t062.upload_ready_report(
        stack, started["client"], started["lease"], t062.report_bytes(document)
    )
    result = t062.submit_result(started["client"], started["lease"], result_artifact_id=artifact_id)
    return {"document": document, "artifact_id": artifact_id, "result": result}


def not_run_report(stack, *, login: str | None = None) -> dict:
    """A guard-accepted ``not_run`` report (zero cases) for a real application."""
    session = creator(stack, login=login or ("t068-not-run-" + f00b.new_uuid()[:8]))
    application_id = minimal_application(session, name="t068 not run")
    started = start_evaluation(
        stack, session, application_id,
        [t052.evaluation_case(case_id="never-ran", assertions=[t052.assertion_run_state("succeeded")])],
    )
    finished = finish_crafted_report(
        stack, started, [], status="not_run", checks=["run_state"]
    )
    assert finished["result"].status == 200, finished["result"].text
    assert finished["result"].json["state"] == "succeeded", finished["result"].json
    evaluation = t052.evaluation_row(stack.dbname, started["evaluation"]["id"])
    report = t052.report_row(stack.dbname, evaluation["report_id"])
    assert report["status"] == "not_run", report
    snapshot = t027.snapshot_row(stack.dbname, started["evaluation"]["snapshot_id"])
    return {
        "session": session,
        "application_id": application_id,
        "evaluation": evaluation,
        "report": report,
        "manifest_sha256": snapshot["manifest_sha256"],
    }


def forged_runner_report(stack, *, login: str | None = None) -> dict:
    """Seed a passed report row whose sealed document declares a synthetic runner.

    The current completion guard cannot write this row (T062 refuses any
    application report that is not the real runner); it simulates a row left by an
    older/bypassed writer so the publication gate's own re-read is exercised.
    """
    session = creator(stack, login=login or ("t068-forged-" + f00b.new_uuid()[:8]))
    application_id = minimal_application(session, name="t068 forged")
    started = start_evaluation(
        stack, session, application_id,
        [t052.evaluation_case(case_id="forged-case", assertions=[t052.assertion_run_state("succeeded")])],
    )
    document = t062.crafted_document(
        stack,
        started["evaluation"],
        status="passed",
        cases=[t062.passed_case("forged-case", run_id=f00b.new_uuid())],
        checks=["run_state"],
        evaluator="synthetic-jcs-case/1",
    )
    artifact_id = t062.upload_ready_report(
        stack, started["client"], started["lease"], t062.report_bytes(document)
    )
    snapshot = t027.snapshot_row(stack.dbname, started["evaluation"]["snapshot_id"])
    dataset = t052.artifact_row(
        stack.dbname, started["evaluation"]["dataset_artifact_id"]
    )
    report_id = f00b.new_uuid()
    t052.st.sql(
        stack.dbname,
        "INSERT INTO platform.evaluation_reports "
        "(id, manifest_sha256, dataset_sha256, result_artifact_id, status) VALUES ("
        f"{t052.st.quote_literal(report_id)}::uuid, "
        f"{t052.st.quote_literal(snapshot['manifest_sha256'])}, "
        f"{t052.st.quote_literal(dataset['sha256'])}, "
        f"{t052.st.quote_literal(artifact_id)}::uuid, 'passed')",
    )
    t052.st.sql(
        stack.dbname,
        "UPDATE platform.evaluation_jobs SET state = 'succeeded', "
        f"report_id = {t052.st.quote_literal(report_id)}::uuid "
        f"WHERE id = {t052.st.quote_literal(started['evaluation']['id'])}::uuid",
    )
    return {
        "session": session,
        "application_id": application_id,
        "report_id": report_id,
        "manifest_sha256": snapshot["manifest_sha256"],
    }


# ---------------------------------------------------------------------------
# seeded terminal runs + a guard-accepted passed report (fast review fixtures)
# ---------------------------------------------------------------------------
_TERMINAL_SPECS = [
    # case id, seeded run state, seeded error code, declared author category code
    ("author-normal-1", "succeeded", None, None),
    ("author-normal-2", "succeeded", None, None),
    ("author-bad-input", "failed", "node_input_invalid", "node_input_invalid"),
    ("author-out-of-bounds", "failed", "row_limit_exceeded", "row_limit_exceeded"),
    ("author-tool-failure", "failed", "sandbox_unavailable", "sandbox_unavailable"),
]


def _seed_terminal_run(stack, run_id: str, state: str, error_code: str | None) -> None:
    """Set a real case run row to the terminal state the executor would produce.

    The run row, its evaluation link, snapshot and manifest are real; only the
    terminal state is seeded because these review-transaction fixtures must not
    depend on the shared sandbox. The dedicated end-to-end test uses a report the
    real runner produced. This is the same technique T070 uses for published-run
    retention rows, and it is recorded in the T068 report.
    """
    error_sql = "NULL" if error_code is None else t052.st.quote_literal(error_code)
    t052.st.sql(
        stack.dbname,
        "UPDATE platform.runs SET state = " + t052.st.quote_literal(state) +
        ", error_code = " + error_sql + ", phase = 'idle', revision = revision + 1 "
        f"WHERE id = {t052.st.quote_literal(run_id)}::uuid",
    )
    t052.st.sql(
        stack.dbname,
        "UPDATE platform.evaluation_case_runs SET state = " + t052.st.quote_literal(state) +
        f" WHERE run_id = {t052.st.quote_literal(run_id)}::uuid",
    )


def seeded_passed_report(
    stack, *, login: str | None = None, cases: int = 5,
    session=None, application_id: str | None = None,
    quality: dict | None = None,
) -> dict:
    """A guard-accepted five-category report over real (seeded-terminal) runs.

    Two normal runs, a bad input, an out-of-bounds input and a tool failure are
    real evaluation case runs whose terminal states are seeded; the report then
    travels through the real completion guard, which re-checks every passed case
    against those run rows. Fast, and used by the submission/review fixtures; the
    genuine runner path has its own end-to-end test.
    """
    session = session or creator(stack, login=login or ("t068-seeded-" + f00b.new_uuid()[:8]))
    application_id = application_id or minimal_application(session, name="t068 seeded")
    specs = _TERMINAL_SPECS[:cases]
    dataset_cases = [
        t052.evaluation_case(
            case_id=spec[0],
            assertions=[t052.assertion_run_state("succeeded")],
            input_value={"seed": spec[0]},
        )
        for spec in specs
    ]
    started = start_evaluation(stack, session, application_id, dataset_cases)
    runs: list[tuple[str, str, str | None, str | None]] = []
    for case_id, state, error_code, category_code in specs:
        created = t062.create_case_run(
            started["client"], started["lease"], case_id=case_id,
            input_value={"seed": case_id},
        )
        _seed_terminal_run(stack, created["run_id"], state, error_code)
        runs.append((case_id, state, error_code, category_code))

    report_cases = []
    for case_id, state, error_code, category_code in runs:
        facts = {
            "run_id": None,
            "run_state": state,
            "run_error_code": error_code or "",
            "effect_counts": {},
            "citation_sources": [],
        }
        # Resolve the real run id from the case ledger (the bridge returned it).
        rows = t052.st.sql_rows(
            stack.dbname,
            "SELECT run_id::text AS run_id FROM platform.evaluation_case_runs "
            f"WHERE evaluation_id = {t052.st.quote_literal(started['evaluation']['id'])}::uuid "
            f"AND case_id = {t052.st.quote_literal(case_id)}",
        )
        assert len(rows) == 1 and rows[0]["run_id"], rows
        facts["run_id"] = rows[0]["run_id"]
        assertions = [
            {"kind": "run_state", "expected": state, "actual": state, "passed": True}
        ]
        if category_code is None:
            assertions.append(
                {"kind": "json_equals", "pointer": "", "expected": "{}", "actual": "{}", "passed": True}
            )
        else:
            assertions.append(
                {
                    "kind": "failure_reason",
                    "expected": category_code,
                    "actual": error_code,
                    "passed": True,
                }
            )
        report_cases.append(
            {
                "id": case_id,
                "status": "passed",
                "verdict": "passed",
                "assertions": assertions,
                "facts": facts,
            }
        )
    finished = finish_crafted_report(
        stack, started, report_cases, status="passed",
        checks=["run_state", "json_equals", "failure_reason"],
        quality=quality,
    )
    assert finished["result"].status == 200, finished["result"].text
    assert finished["result"].json["state"] == "succeeded", finished["result"].json
    evaluation = t052.evaluation_row(stack.dbname, started["evaluation"]["id"])
    assert evaluation["report_id"], evaluation
    report = t052.report_row(stack.dbname, evaluation["report_id"])
    assert report["status"] == "passed", report
    snapshot = t027.snapshot_row(stack.dbname, started["evaluation"]["snapshot_id"])
    return {
        "session": session,
        "application_id": application_id,
        "evaluation": evaluation,
        "report": report,
        "sealed": finished["document"],
        "manifest_sha256": snapshot["manifest_sha256"],
        "snapshot": snapshot,
    }


# ---------------------------------------------------------------------------
# database facts
# ---------------------------------------------------------------------------
def review_row(dbname: str, review_id: str) -> dict:
    rows = t052.st.sql_rows(
        dbname,
        "SELECT id::text AS id, application_id::text AS application_id, "
        "submitted_by::text AS submitted_by, state, manifest_sha256, "
        "report_id::text AS report_id, reviewer_id::text AS reviewer_id, reason, "
        "version_id::text AS version_id, snapshot_id::text AS snapshot_id, "
        "evaluation_id::text AS evaluation_id, dataset_sha256, "
        "manifest_artifact_id::text AS manifest_artifact_id, snapshot::text AS snapshot, "
        "decided_at::text AS decided_at, created_at::text AS created_at "
        "FROM platform.publication_reviews "
        f"WHERE id = {t052.st.quote_literal(review_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def review_count(dbname: str, application_id: str | None = None) -> int:
    where = ""
    if application_id is not None:
        where = f" WHERE application_id = {t052.st.quote_literal(application_id)}::uuid"
    return int(t052.st.sql(dbname, "SELECT count(*) FROM platform.publication_reviews" + where))


def version_rows(dbname: str, application_id: str) -> list[dict]:
    return t052.st.sql_rows(
        dbname,
        "SELECT id::text AS id, application_id::text AS application_id, version_no, "
        "review_id::text AS review_id, manifest::text AS manifest, manifest_sha256, "
        "manifest_artifact_id::text AS manifest_artifact_id, capability_card::text AS capability_card, "
        "compiler_version, worker_image_digest, state_schema_version, "
        "revoked_at::text AS revoked_at "
        "FROM platform.application_versions "
        f"WHERE application_id = {t052.st.quote_literal(application_id)}::uuid "
        "ORDER BY version_no",
    )


def version_count(dbname: str, application_id: str) -> int:
    return int(
        t052.st.sql(
            dbname,
            "SELECT count(*) FROM platform.application_versions "
            f"WHERE application_id = {t052.st.quote_literal(application_id)}::uuid",
        )
    )


def outbox_rows(dbname: str, aggregate_id: str | None = None) -> list[dict]:
    where = ""
    if aggregate_id is not None:
        where = f" WHERE aggregate_id = {t052.st.quote_literal(aggregate_id)}::uuid"
    return t052.st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, aggregate_id::text AS aggregate_id, payload::text AS payload, "
        "delivered_at::text AS delivered_at, attempts FROM platform.outbox" + where,
    )


def audit_actions(dbname: str, resource_id: str) -> list[str]:
    rows = t052.st.sql_rows(
        dbname,
        "SELECT action FROM platform.audit_events "
        f"WHERE resource_id = {t052.st.quote_literal(resource_id)}::uuid ORDER BY action",
    )
    return [row["action"] for row in rows]


def artifact_row(dbname: str, artifact_id: str) -> dict:
    return t052.artifact_row(dbname, artifact_id)


def sealed_bytes(stack, artifact_id: str) -> bytes:
    """Read one sealed object's exact bytes and re-verify the recorded hash."""
    row = artifact_row(stack.dbname, artifact_id)
    content = (Path(stack.object_dir) / row["object_key"]).read_bytes()
    assert hashlib.sha256(content).hexdigest() == row["sha256"], "the sealed bytes changed"
    return content


def active_version_id(dbname: str, application_id: str) -> str | None:
    rows = t052.st.sql_rows(
        dbname,
        "SELECT active_version_id::text AS active_version_id FROM platform.applications "
        f"WHERE id = {t052.st.quote_literal(application_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]["active_version_id"]


def archive_application(dbname: str, application_id: str) -> None:
    t052.st.sql(
        dbname,
        "UPDATE platform.applications SET archived_at = now() "
        f"WHERE id = {t052.st.quote_literal(application_id)}::uuid",
    )


__all__ = [name for name in dir() if not name.startswith("_")]
