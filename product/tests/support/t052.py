"""F13A/T052 real-stack helpers: the evaluation runner and its independent checker.

The stack is the T024/T026 one (throwaway PostgreSQL migrated by the real
``cmd/admin`` binary, the compiled ``cmd/api`` with the internal mTLS listener
and signed object links, the real object store on the filesystem) plus:

* the public ``POST /api/v1/applications/{id}/evaluations`` submission;
* a real ``cmd/maintenance run`` process serving the two pools (the
  ``application_evaluate`` handler in ``work`` and ``artifact_finalize`` in
  ``system_io``);
* the real ``cmd/supervisor exec --once`` executor, which claims the per-case
  runs the evaluation creates and executes the frozen snapshot.

Nothing here mocks the thing under test. The dataset is a real uploaded artifact
(``purpose=evaluation_dataset``) sealed by the real finalize guard; the report is
the real sealed report artifact the completion guard reads; every state
assertion reads PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

from support import f00b, t026, t027, t028, t029, t033
from support import stack as st

PRODUCT_ROOT = st.PRODUCT_ROOT
KIND_APPLICATION_EVALUATE = t026.KIND_APPLICATION_EVALUATE

# T052Stack needs everything T026 has (database, binaries, object store, signing
# key, mTLS certificates) plus the supervisor binary the execution loop uses.
T052Stack = t026.T026Stack

DATASET_PURPOSE = "evaluation_dataset"


# ---------------------------------------------------------------------------
# identities and applications
# ---------------------------------------------------------------------------
def minimal_owner(stack: T052Stack, *, login: str | None = None) -> t029.Owner:
    """A real creator who owns a start -> end application (no tool, no sandbox).

    The minimal chain is a genuine execution: the run is claimed and executed by
    ``cmd/supervisor exec`` and produces a real final-output artifact. It just
    does not need a tool subprocess, which keeps the evaluation slice's truth
    independent of the sandbox.
    """
    return t029.minimal_owner(stack, login=login)


def other_creator(stack: T052Stack, *, login: str) -> t029.Owner:
    return t029.minimal_owner(stack, login=login)


def draft_revision(session, application_id: str) -> int:
    return t027.draft_revision(session, application_id)


# ---------------------------------------------------------------------------
# datasets
# ---------------------------------------------------------------------------
def assertion_run_state(expected: str) -> dict:
    return {"kind": "run_state", "expected": expected}


def assertion_decimal(pointer: str, expected: str) -> dict:
    return {"kind": "decimal_equals", "pointer": pointer, "expected": expected}


def assertion_json(pointer: str, expected) -> dict:
    return {"kind": "json_equals", "pointer": pointer, "expected": expected}


def assertion_effect_count(capability: str, expected: int) -> dict:
    return {
        "kind": "effect_count",
        "capability": capability,
        "expected": expected,
        "source": "stub_ledger",
    }


def assertion_citations(allowed: list[str], *, require_one: bool = True) -> dict:
    return {
        "kind": "citation_sources",
        "allowed_source_ids": list(allowed),
        "require_at_least_one": require_one,
    }


def evaluation_case(
    *,
    case_id: str,
    assertions: list[dict],
    input_value: dict | None = None,
    target_kind: str = "debug_snapshot",
    target_id: str = "frozen-snapshot",
    category: str = "lifecycle",
    split: str = "dev",
) -> dict:
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "category": category,
        "family_id": "t052-family",
        "split": split,
        "target": {"kind": target_kind, "id": target_id},
        "input": dict(input_value or {}),
        "fixture_id": "t052-fixture",
        "assertions": list(assertions),
        "provenance": {
            "origin": "self_created",
            "reference": "tests/f13a",
            "license_review": "not_applicable",
        },
    }


def dataset_document(cases: list[dict]) -> bytes:
    return json.dumps({"cases": cases}, sort_keys=True, separators=(",", ":")).encode("utf-8")


def upload_dataset(stack: T052Stack, session, content: bytes) -> str:
    """Upload the dataset through the public T028 path and finalize it for real."""
    return t033.upload_artifact(
        stack, session, content, purpose=DATASET_PURPOSE, media_type="application/json"
    )


# ---------------------------------------------------------------------------
# the public submission
# ---------------------------------------------------------------------------
def submit_evaluation(
    session,
    application_id: str,
    *,
    draft_revision: int,
    dataset_artifact_id: str,
    idempotency_key: str | None = None,
    body_extra: dict | None = None,
    omit_key: bool = False,
    extra_headers: dict[str, str] | None = None,
):
    body = {
        "draft_revision": draft_revision,
        "dataset_artifact_id": dataset_artifact_id,
    }
    if body_extra:
        body.update(body_extra)
    headers: dict[str, str] = dict(extra_headers or {})
    if not omit_key:
        headers["Idempotency-Key"] = idempotency_key or f00b.new_uuid()
    return session.client.post(
        f"/api/v1/applications/{application_id}/evaluations",
        body=body,
        csrf=session.csrf,
        headers=headers,
    )


def evaluation_path(evaluation_id: str) -> str:
    return f"/api/v1/evaluations/{evaluation_id}"


def report_path(report_id: str) -> str:
    return f"/api/v1/evaluation-reports/{report_id}"


def get_evaluation(session, evaluation_id: str):
    return session.client.get(evaluation_path(evaluation_id))


def get_report(session, report_id: str):
    return session.client.get(report_path(report_id))


# ---------------------------------------------------------------------------
# database read models
# ---------------------------------------------------------------------------
EVALUATION_FIELDS = {
    "id", "kind", "state", "background_job_id", "report_id", "error_code",
}
REPORT_FIELDS = {"id", "status", "manifest_sha256", "result_artifact_id"}


def evaluation_row(dbname: str, evaluation_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, owner_id::text AS owner_id, "
        "background_job_id::text AS background_job_id, snapshot_id::text AS snapshot_id, "
        "dataset_artifact_id::text AS dataset_artifact_id, report_id::text AS report_id, "
        "state, error_code, application_id::text AS application_id, "
        "progress_total, progress_done "
        "FROM platform.evaluation_jobs "
        f"WHERE id = {st.quote_literal(evaluation_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def evaluation_count(dbname: str) -> int:
    return int(st.sql(dbname, "SELECT count(*) FROM platform.evaluation_jobs"))


def report_row(dbname: str, report_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, manifest_sha256, dataset_sha256, "
        "result_artifact_id::text AS result_artifact_id, status "
        "FROM platform.evaluation_reports "
        f"WHERE id = {st.quote_literal(report_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def report_count(dbname: str) -> int:
    return int(st.sql(dbname, "SELECT count(*) FROM platform.evaluation_reports"))


def case_run_rows(dbname: str, evaluation_id: str) -> list[dict]:
    # `verdict` is F13A/T065's additive column: the runner's per-case decision,
    # independent of the observed run state. T052's tests keep reading the same
    # fields and may now also assert the decision on a runless case.
    return st.sql_rows(
        dbname,
        "SELECT case_id, ordinal, run_id::text AS run_id, state, reason_code, verdict "
        "FROM platform.evaluation_case_runs "
        f"WHERE evaluation_id = {st.quote_literal(evaluation_id)}::uuid ORDER BY ordinal",
    )


def run_row(dbname: str, run_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, state, kind, "
        "execution_snapshot_id::text AS execution_snapshot_id, "
        "manifest_sha256, input_artifact_id::text AS input_artifact_id, "
        "output_artifact_id::text AS output_artifact_id, error_code "
        f"FROM platform.runs WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def operation_count(dbname: str, run_id: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.operations "
            f"WHERE run_id = {st.quote_literal(run_id)}::uuid",
        )
    )


def run_artifact_ids(dbname: str, run_id: str) -> list[str]:
    return [
        row["artifact_id"]
        for row in st.sql_rows(
            dbname,
            "SELECT artifact_id::text AS artifact_id FROM platform.run_artifacts "
            f"WHERE run_id = {st.quote_literal(run_id)}::uuid ORDER BY artifact_id",
        )
    ]


def archive_application(dbname: str, application_id: str) -> None:
    st.sql(
        dbname,
        "UPDATE platform.applications SET archived_at = now() "
        f"WHERE id = {st.quote_literal(application_id)}::uuid",
    )


def artifact_row(dbname: str, artifact_id: str) -> dict:
    return f00b.artifact_row(dbname, artifact_id)


def report_document(stack: T052Stack, artifact_id: str) -> dict:
    """Read the sealed report artifact's real bytes and decode them."""
    row = artifact_row(stack.dbname, artifact_id)
    path = Path(stack.object_dir) / row["object_key"]
    assert path.exists(), f"the sealed report object is missing: {row['object_key']}"
    content = path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == row["sha256"], "the sealed report bytes changed"
    return json.loads(content.decode("utf-8"))


# ---------------------------------------------------------------------------
# the two pools and the real executor
# ---------------------------------------------------------------------------
def pool_args(
    *,
    work_slots: int = 2,
    system_io_slots: int = 2,
    case_timeout: str = "30s",
    once: bool = False,
) -> list[str]:
    args: list[str] = ["--poll-interval", "20ms"]
    if once:
        args.insert(0, "--once")
    args += [
        "--system-io-slots",
        str(system_io_slots),
        "--work-slots",
        str(work_slots),
        "--evaluation-poll-interval",
        "50ms",
        "--dataset-wait-timeout",
        "30s",
        "--report-wait-timeout",
        "30s",
        "--evaluation-case-timeout",
        case_timeout,
    ]
    return args


def start_pools(
    stack: T052Stack,
    *,
    log_path: Path,
    case_timeout: str = "30s",
    work_slots: int = 2,
    system_io_slots: int = 2,
) -> tuple[subprocess.Popen, object]:
    """A real maintenance process serving both pools."""
    env = f00b.maintenance_env(
        stack, heartbeat_interval="50ms", extra={"ORCHARA_BACKGROUND_RETRY_BACKOFF": "50ms"}
    )
    handle = log_path.open("w", encoding="utf-8", newline="\n")
    process = subprocess.Popen(
        [
            str(stack.binaries["maintenance"]),
            "run",
            *pool_args(
                work_slots=work_slots, system_io_slots=system_io_slots, case_timeout=case_timeout
            ),
        ],
        cwd=PRODUCT_ROOT,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return process, handle


stop_process = t026.stop_process
read_log = t026.read_log
wait_until = t026.wait_until


def wait_for_evaluation(
    dbname: str, evaluation_id: str, states: set[str], *, timeout: float = 120.0
) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = evaluation_row(dbname, evaluation_id)
        if last["state"] in states:
            return last
        time.sleep(0.05)
    raise AssertionError(f"evaluation {evaluation_id} never reached {states}: {last}")


# ---------------------------------------------------------------------------
# the supervisor executor (reused from T033: the same real binary and env)
# ---------------------------------------------------------------------------
supervisor_exec_env = t033.supervisor_exec_env
run_supervisor_exec = t033.run_supervisor_exec
exec_report = t033.exec_report
sandbox_available = t033.sandbox_available
wait_run_state = t033.wait_run_state


__all__ = [name for name in dir() if not name.startswith("_")]
