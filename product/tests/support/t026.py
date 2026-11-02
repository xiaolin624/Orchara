"""F00B/T026 real-stack helpers: evaluations, the dual pool and the orphan sweep.

The stack is the T022/T024 one (throwaway PostgreSQL migrated by the real
``cmd/admin`` binary, the compiled ``cmd/api`` with the internal mTLS listener and
signed object links, the real object store on the filesystem) plus T026's
evaluation control interface.

Two boundaries are deliberate and documented in the T026 report/ADR:

* The evaluator is a deterministic *synthetic* placeholder (ADR 0004 §31): it
  re-canonicalises each dataset case's input and compares the JCS SHA-256 with the
  case's declared ``expected_sha256``. It proves the real pipeline
  (download -> evaluate -> report -> finalize -> guarded report row) without
  pretending the F13A runner/checker suite exists.
* ``kind=application`` is accepted structurally by the CLI, but
  ``execution_snapshots`` is T027's table: such an evaluation fails honestly with
  ``evaluation_binding_missing`` and is never reported as executed here.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

from support import f00b, t024
from support import stack as st

PRODUCT_ROOT = st.PRODUCT_ROOT
KIND_APPLICATION_EVALUATE = "application_evaluate"
ADMIN_LOGIN = f00b.ADMIN_LOGIN
ADMIN_PASSWORD = f00b.ADMIN_PASSWORD
USER_PASSWORD = f00b.USER_PASSWORD

# T026Stack is the T024 stack: the evaluation work needs the same real database,
# binaries, object store, signing key and internal mTLS certificates.
T026Stack = t024.T024Stack


def new_uuid() -> str:
    return f00b.new_uuid()


def sha256_hex(content: bytes | str) -> str:
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


def canonical_json(value) -> bytes:
    """RFC 8785-equivalent bytes for the ASCII/simple values the tests use."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


# ---------------------------------------------------------------------------
# datasets and the synthetic evaluator's expected values
# ---------------------------------------------------------------------------
def dataset_case(*, case_id: str, input_value, expected_sha256: str | None = None) -> dict:
    """One dataset case; by default it carries the correct ground-truth hash."""
    return {
        "id": case_id,
        "input": input_value,
        "expected_sha256": expected_sha256
        if expected_sha256 is not None
        else sha256_hex(canonical_json(input_value)),
    }


def dataset_document(cases: list[dict], *, schema: str = "orchara.evaluation-dataset/1") -> bytes:
    document = {"schema": schema, "cases": cases}
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def seed_dataset(
    stack: T026Stack,
    owner_id: str,
    *,
    cases: list[dict] | None = None,
    content: bytes | None = None,
    state: str = "ready",
    sealed: bool = True,
) -> f00b.SeededArtifact:
    """Insert a real dataset artifact (ready by default) with real bytes.

    ``state="uploading"`` seeds the "the handler must wait" state: the row exists
    with a staging key but no bytes yet; ``make_dataset_ready`` later performs the
    uploader-side steps (staging bytes, complete's CAS, finalize job).
    """
    if content is None:
        cases = cases if cases is not None else [dataset_case(case_id="case-1", input_value={"q": 1})]
        content = dataset_document(cases)
    return f00b.seed_artifact(
        stack.dbname,
        stack.object_dir,
        owner_id,
        content=content,
        state=state,
        staging=False,
        sealed_content=content if state == "ready" and sealed else None,
        media_type="application/json",
        purpose="evaluation_dataset",
    )


def make_dataset_ready(stack: T026Stack, owner_id: str, artifact: f00b.SeededArtifact) -> dict:
    """Simulate T024's completeUpload for a dataset seeded as ``uploading``.

    The test writes the staging bytes (the real link path is covered by T024),
    performs the same CAS ``uploading -> scanning`` (revision + 1, expected hash)
    and enqueues the real artifact_finalize job. Activation is still the finalize
    guard's decision; this helper never sets ``ready`` itself.
    """
    content = artifact.content
    digest = sha256_hex(content)
    f00b.write_object(stack.object_dir, artifact.staging_key, content)
    revision = int(f00b.artifact_row(stack.dbname, artifact.id)["revision"])
    next_revision = revision + 1
    st.sql(
        stack.dbname,
        "UPDATE platform.artifacts "
        f"SET state = 'scanning', expected_sha256 = {st.quote_literal(digest)}, "
        f"    revision = revision + 1 "
        f"WHERE id = {st.quote_literal(artifact.id)}::uuid",
    )
    return f00b.enqueue_ok(
        stack,
        kind="artifact_finalize",
        target_id=artifact.id,
        owner_id=owner_id,
        dedup_key=f"t026-dataset:{artifact.id}:{digest}",
        expected_revision=next_revision,
        expected_sha256=digest,
    )


# ---------------------------------------------------------------------------
# evaluation control interface (cmd/admin evaluations enqueue)
# ---------------------------------------------------------------------------
def enqueue_evaluation(
    stack: T026Stack,
    *,
    kind: str = "model",
    owner_login: str = ADMIN_LOGIN,
    dataset_artifact_id: str,
    model_profile_version_id: str | None = None,
    snapshot_id: str | None = None,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        "evaluations",
        "enqueue",
        "--kind",
        kind,
        "--owner-login",
        owner_login,
        "--dataset-artifact-id",
        dataset_artifact_id,
    ]
    if model_profile_version_id is not None:
        args += ["--model-profile-version-id", model_profile_version_id]
    if snapshot_id is not None:
        args += ["--snapshot-id", snapshot_id]
    args += list(extra_args or [])
    return st.run_admin(stack.binaries["admin"], args, dsn=stack.dsn)


def enqueue_evaluation_ok(stack: T026Stack, **kwargs) -> dict:
    completed = enqueue_evaluation(stack, **kwargs)
    assert completed.returncode == 0, (
        f"admin evaluations enqueue failed ({completed.returncode}):\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert set(payload) == {"id", "kind", "state", "background_job_id"}, payload
    return payload


def prepare_model_evaluation(
    stack: T026Stack,
    *,
    cases: list[dict] | None = None,
    content: bytes | None = None,
    owner_login: str = ADMIN_LOGIN,
    candidate: dict | None = None,
    dataset_state: str = "ready",
    sealed: bool = True,
) -> dict:
    """Seed candidate + dataset, enqueue one model evaluation, return all ids."""
    owner_id = f00b.principal_id_for_login(stack.dbname, owner_login)
    candidate = candidate or t024.seed_candidate(stack.dbname, stack.object_dir, owner_id)
    dataset = seed_dataset(
        stack, owner_id, cases=cases, content=content, state=dataset_state, sealed=sealed
    )
    payload = enqueue_evaluation_ok(
        stack,
        kind="model",
        owner_login=owner_login,
        dataset_artifact_id=dataset.id,
        model_profile_version_id=candidate["id"],
    )
    return {
        "evaluation_id": payload["id"],
        "background_job_id": payload["background_job_id"],
        "candidate": candidate,
        "dataset": dataset,
        "owner_id": owner_id,
        "owner_login": owner_login,
    }


# ---------------------------------------------------------------------------
# the dual pool (cmd/maintenance run)
# ---------------------------------------------------------------------------
def pool_env(
    stack: T026Stack,
    *,
    heartbeat_interval: str = "50ms",
    retry_backoff: str = "50ms",
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    return f00b.maintenance_env(
        stack,
        heartbeat_interval=heartbeat_interval,
        retry_backoff=retry_backoff,
        extra=extra,
    )


def pool_args(
    *,
    work_slots: int = 2,
    system_io_slots: int = 2,
    kinds: str | None = None,
    once: bool = False,
) -> list[str]:
    args: list[str] = ["--poll-interval", "20ms"]
    if once:
        args.insert(0, "--once")
    if kinds is not None:
        args += ["--kinds", kinds]
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
    ]
    return args


def run_pools_once(
    stack: T026Stack,
    *,
    work_slots: int = 2,
    system_io_slots: int = 2,
    kinds: str | None = None,
    timeout: int = 120,
) -> tuple[subprocess.CompletedProcess[str], list[dict]]:
    completed = f00b.run_maintenance(
        stack,
        *pool_args(
            work_slots=work_slots,
            system_io_slots=system_io_slots,
            kinds=kinds,
            once=True,
        ),
        env=pool_env(stack),
        timeout=timeout,
    )
    return completed, f00b.parse_json_lines(completed.stdout)


def start_pools(
    stack: T026Stack,
    *,
    work_slots: int = 2,
    system_io_slots: int = 2,
    kinds: str | None = None,
    log_path: Path,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.Popen, object]:
    """Start a real maintenance process with two pools; returns (process, log handle)."""
    env = pool_env(stack, extra=extra_env)
    handle = log_path.open("w", encoding="utf-8", newline="\n")
    process = subprocess.Popen(
        [
            str(stack.binaries["maintenance"]),
            "run",
            *pool_args(work_slots=work_slots, system_io_slots=system_io_slots, kinds=kinds),
        ],
        cwd=PRODUCT_ROOT,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return process, handle


def stop_process(process: subprocess.Popen, handle, log_path: Path) -> str:  # noqa: ANN001
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:  # pragma: no cover - teardown must not mask failures
        process.kill()
        process.wait(timeout=5)
    handle.flush()
    handle.close()
    return read_log(log_path)


def read_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:  # pragma: no cover
        return ""


# ---------------------------------------------------------------------------
# database read models and bounded waits
# ---------------------------------------------------------------------------
def evaluation_row(dbname: str, evaluation_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, owner_id::text AS owner_id, "
        "background_job_id::text AS background_job_id, snapshot_id::text AS snapshot_id, "
        "model_profile_version_id::text AS model_profile_version_id, "
        "dataset_artifact_id::text AS dataset_artifact_id, report_id::text AS report_id, "
        "state, error_code, finished_at::text AS finished_at "
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
        "result_artifact_id::text AS result_artifact_id, status, created_at::text AS created_at "
        "FROM platform.evaluation_reports "
        f"WHERE id = {st.quote_literal(report_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def report_count(dbname: str) -> int:
    return int(st.sql(dbname, "SELECT count(*) FROM platform.evaluation_reports"))


def evaluation_job_row(dbname: str, evaluation_id: str) -> dict:
    evaluation = evaluation_row(dbname, evaluation_id)
    return f00b.job_row(dbname, evaluation["background_job_id"])


def wait_for_evaluation(
    dbname: str, evaluation_id: str, states: set[str], *, timeout: float = 30.0
) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = evaluation_row(dbname, evaluation_id)
        if last["state"] in states:
            return last
        time.sleep(0.05)
    raise AssertionError(f"evaluation {evaluation_id} never reached {states}: {last}")


def wait_for_job_state(dbname: str, job_id: str, states: set[str], *, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = f00b.job_row(dbname, job_id)
        if last["state"] in states:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached {states}: {last}")


def wait_for_leased(dbname: str, job_id: str, *, timeout: float = 30.0) -> dict:
    return wait_for_job_state(dbname, job_id, {"leased"}, timeout=timeout)


def wait_until(predicate, *, timeout: float = 30.0, interval: float = 0.02, what: str) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}; last error: {last}")


# ---------------------------------------------------------------------------
# public reads
# ---------------------------------------------------------------------------
def get_evaluation(session, evaluation_id: str) -> st.Response:
    return session.client.get(f"/api/v1/evaluations/{evaluation_id}")


def get_report(session, report_id: str) -> st.Response:
    return session.client.get(f"/api/v1/evaluation-reports/{report_id}")


def get_background_job(session, job_id: str) -> st.Response:
    return session.client.get(f"/api/v1/background-jobs/{job_id}")


# ---------------------------------------------------------------------------
# orphan sweep
# ---------------------------------------------------------------------------
def run_sweep(
    stack: T026Stack,
    *args: str,
    timeout: int = 120,
) -> tuple[subprocess.CompletedProcess[str], list[dict], dict]:
    env = st.admin_env(stack.dsn)
    env.pop("ORCHARA_DB_DSN", None)
    env["ORCHARA_MAINTENANCE_DSN"] = stack.dsn
    env["ORCHARA_OBJECT_DIR"] = str(stack.object_dir)
    completed = subprocess.run(
        [str(stack.binaries["maintenance"]), "sweep", *args],
        cwd=PRODUCT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    lines = f00b.parse_json_lines(completed.stdout)
    summary = lines[-1] if lines else {}
    return completed, lines[:-1], summary


def object_files(object_dir: Path, prefix: str) -> set[str]:
    directory = object_dir / prefix.rstrip("/")
    if not directory.exists():
        return set()
    return {f"{prefix.rstrip('/')}/{entry.name}" for entry in directory.iterdir() if entry.is_file()}


__all__ = [
    "T026Stack",
    "ADMIN_LOGIN",
    "ADMIN_PASSWORD",
    "USER_PASSWORD",
    "new_uuid",
    "sha256_hex",
    "canonical_json",
    "dataset_case",
    "dataset_document",
    "seed_dataset",
    "make_dataset_ready",
    "enqueue_evaluation",
    "enqueue_evaluation_ok",
    "prepare_model_evaluation",
    "pool_args",
    "run_pools_once",
    "start_pools",
    "stop_process",
    "read_log",
    "evaluation_row",
    "evaluation_count",
    "report_row",
    "report_count",
    "evaluation_job_row",
    "wait_for_evaluation",
    "wait_for_job_state",
    "wait_for_leased",
    "wait_until",
    "get_evaluation",
    "get_report",
    "get_background_job",
    "run_sweep",
    "object_files",
]
