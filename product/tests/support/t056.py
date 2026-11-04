"""F13A/T056 real-stack helpers: sales cases, bad inputs and per-case results.

The stack is T052's (real PostgreSQL migrated by ``cmd/admin``, the compiled
``cmd/api`` with a real object store and mTLS, a real ``cmd/maintenance run``
process serving both pools, and the real ``cmd/supervisor exec --once`` executor
running the fixed preset sales tool in WSL2/runsc). Nothing here mocks the thing
under test.

The committed fixtures under ``tests/f13a/datasets/`` are the case datasets
(valid against ``product/contracts/evaluation-case.schema.json``) plus their
small CSV inputs. Two inputs are too large to commit and are generated
deterministically instead; their SHA-256 is frozen in the committed dataset, so
the dataset still names exact bytes and a drift is a hard failure:

* the over-limit CSV (10 001 data rows -> ``row_limit_exceeded``);
* the timeout CSV (exactly 20 MiB, header + blank lines): the stdlib CSV parse of
  ~20 million blank lines takes >4 s on the Linux sandbox even before the
  sandbox boot, so a 2 s sandbox timeout is exceeded deterministically. The
  margin is recorded in the T056 report.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

from support import f00b, f02a, t024, t025, t027, t029, t033, t042, t052

PRODUCT_ROOT = t052.PRODUCT_ROOT
DATASETS_DIR = Path(__file__).resolve().parents[1] / "f13a" / "datasets"
INPUTS_DIR = DATASETS_DIR / "inputs"
EXAMPLES = PRODUCT_ROOT.parent / "examples" / "sales"

STACK = t052.T052Stack

CSV_MEDIA = "text/csv"
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DATASET_PURPOSE = "evaluation_dataset"
FIXTURE_PURPOSE = "task_input"

# --- deterministic generators -------------------------------------------------
OVER_LIMIT_HEADER = b"order_id,date,region,amount\n"
TIMEOUT_HEADER = b"order_id,date,region,amount\n"
TIMEOUT_BYTES = 20 * 1024 * 1024


def over_limit_csv() -> bytes:
    """10 001 unique orders: one more than the tool's documented row ceiling."""
    rows = b"".join(
        f"o-{index:05d},2026-01-01,东区,1.00\n".encode("utf-8") for index in range(1, 10002)
    )
    return OVER_LIMIT_HEADER + rows


def timeout_csv() -> bytes:
    """Exactly 20 MiB: a valid header followed by blank lines the CSV reader walks.

    The tool skips blank rows before its row cap, so this input is not rejected
    by a validation rule: it is slow (measured >4 s in the Linux sandbox), which
    is what lets the case exceed a 2 s tool timeout deterministically instead of
    racing it.
    """
    padding = TIMEOUT_BYTES - len(TIMEOUT_HEADER)
    assert padding > 0
    return TIMEOUT_HEADER + b"\n" * padding


# ---------------------------------------------------------------------------
# committed fixtures
# ---------------------------------------------------------------------------
def load_dataset(name: str) -> dict:
    return json.loads((DATASETS_DIR / name).read_text(encoding="utf-8"))


def dataset_bytes(document: dict) -> bytes:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def expected_values() -> dict:
    return json.loads((DATASETS_DIR / "expected_values.json").read_text(encoding="utf-8"))


def dataset_case(document: dict, case_id: str) -> dict:
    for item in document["cases"]:
        if item["case_id"] == case_id:
            return item
    raise KeyError(case_id)


def fixture_bytes(expected: dict) -> bytes:
    """The exact fixture bytes one expected-values entry names."""
    name = expected["input"]
    if name == "examples/sales/input.csv":
        return EXAMPLES.joinpath("input.csv").read_bytes()
    if name == "examples/sales/input.xlsx":
        return EXAMPLES.joinpath("input.xlsx").read_bytes()
    if name == "tests/f13a/datasets/inputs/frozen_boundary.csv":
        return INPUTS_DIR.joinpath("frozen_boundary.csv").read_bytes()
    if name == "generated/over_limit.csv":
        return over_limit_csv()
    if name == "generated/timeout.csv":
        return timeout_csv()
    raise KeyError(name)


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def fixture_candidates() -> dict[str, bytes]:
    """Every committed/generable fixture input, keyed by SHA-256."""
    candidates: dict[str, bytes] = {}
    for path in sorted(INPUTS_DIR.glob("*")):
        content = path.read_bytes()
        candidates[sha256_hex(content)] = content
    for path in (EXAMPLES / "input.csv", EXAMPLES / "input.xlsx"):
        content = path.read_bytes()
        candidates[sha256_hex(content)] = content
    for content in (over_limit_csv(), timeout_csv()):
        candidates[sha256_hex(content)] = content
    return candidates


def resolve_fixtures(document: dict) -> list[tuple[bytes, str]]:
    """The exact bytes and media type every case of a committed dataset names."""
    bindings = {
        (case["input"]["task_input"]["sha256"], case["input"]["task_input"]["media_type"])
        for case in document["cases"]
        if "task_input" in case["input"]
    }
    if not bindings:
        raise AssertionError("this dataset binds no fixture")
    known = fixture_candidates()
    resolved: list[tuple[bytes, str]] = []
    for sha, media_type in sorted(bindings):
        content = known.get(sha)
        if content is None:
            raise AssertionError(f"the committed dataset names an unknown fixture hash: {sha}")
        resolved.append((content, media_type))
    return resolved


def upload_dataset_fixtures(stack: STACK, session, document: dict) -> list[str]:
    """Upload every fixture a dataset's cases name, so the runs can bind them."""
    artifact_ids = []
    for content, media_type in resolve_fixtures(document):
        artifact_ids.append(upload_fixture(stack, session, content, media_type))
    return artifact_ids


# ---------------------------------------------------------------------------
# identities, application and uploads
# ---------------------------------------------------------------------------
def sales_owner(stack: STACK, *, login: str | None = None) -> t029.Owner:
    """A real creator who owns a start -> python(sales_stats) -> end application.

    The fixed preset tool version the chain references is seeded as a real
    approved ``tool_versions`` row (the same row the golden T042 chain uses), so
    the evaluation's compile validates a genuine tool dependency rather than a
    fabricated one.
    """
    admin = f00b.admin_session(stack)
    creator = f00b.invite_and_activate(
        stack, admin, login or ("t056-owner-" + uuid.uuid4().hex[:8]), roles=("creator",)
    )
    t025.seed_tool_version(
        stack.dbname, creator.principal_id, tool_id=t042.PRESET_TOOL_ID
    )
    application_id = t027.application_with_definition(
        creator, t042.linear_definition(), name="t056 sales chain"
    )
    return t029.Owner(admin=admin, creator=creator, application_id=application_id)


def upload_fixture(stack: STACK, session, payload: bytes, media_type: str) -> str:
    return t033.upload_artifact(
        stack, session, payload, purpose=FIXTURE_PURPOSE, media_type=media_type
    )


def upload_dataset(stack: STACK, session, payload: bytes) -> str:
    return t033.upload_artifact(
        stack, session, payload, purpose=DATASET_PURPOSE, media_type="application/json"
    )


def upload_named_fixture(stack: STACK, session, entry: dict) -> str:
    """Upload one expected-values entry's real bytes as a task_input artifact."""
    payload = fixture_bytes(entry)
    assert sha256_hex(payload) == entry["sha256"], "the committed fixture bytes changed"
    return upload_fixture(stack, session, payload, entry["media_type"])


# ---------------------------------------------------------------------------
# submit + drive
# ---------------------------------------------------------------------------
def submit_dataset(
    stack: STACK, owner: t029.Owner, document: dict, *, idempotency_key: str | None = None
) -> dict:
    artifact_id = upload_dataset(stack, owner.creator, dataset_bytes(document))
    response = t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=t052.draft_revision(owner.creator, owner.application_id),
        dataset_artifact_id=artifact_id,
        idempotency_key=idempotency_key,
    )
    assert response.status == 202, response.text
    return response.json


def drive_supervisor(
    stack: STACK,
    evaluation_id: str,
    *,
    sandbox_timeout: str = "60s",
    deadline_s: float = 900.0,
) -> list[dict]:
    """Run the real executor until the evaluation reaches a terminal state.

    A failed drive records the executor reports why: a loaded host makes each
    sandbox attempt take longer, and an honest timeout in this helper must never
    be reported as a case verdict.
    """
    env = t052.supervisor_exec_env(stack, timeout=sandbox_timeout)
    reports: list[dict] = []
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        row = t052.evaluation_row(stack.dbname, evaluation_id)
        if row["state"] in {"succeeded", "failed", "cancelled"}:
            return reports
        started = time.time()
        completed = t052.run_supervisor_exec(stack, env, timeout=600)
        report = t052.exec_report(completed)
        report["_wall_ms"] = int((time.time() - started) * 1000)
        reports.append(report)
        if report.get("status") == "idle":
            time.sleep(0.05)
    raise AssertionError(
        "the evaluation never became terminal: "
        f"{t052.evaluation_row(stack.dbname, evaluation_id)}; last reports: "
        f"{[{key: report.get(key) for key in ('status', 'outcome', 'error_code', 'run_id', '_wall_ms')} for report in reports[-8:]]}"
    )


def run_evaluation(
    stack: STACK,
    owner: t029.Owner,
    document: dict,
    *,
    sandbox_timeout: str = "60s",
    case_timeout: str = "300s",
    log_path: Path,
) -> tuple[dict, dict, list[dict]]:
    """Submit, run the pools and the executor, and return (evaluation, report, document)."""
    upload_dataset_fixtures(stack, owner.creator, document)
    submitted = submit_dataset(stack, owner, document)
    evaluation_id = submitted["id"]
    process, handle = t052.start_pools(stack, log_path=log_path, case_timeout=case_timeout)
    try:
        reports = drive_supervisor(stack, evaluation_id, sandbox_timeout=sandbox_timeout)
    finally:
        t052.stop_process(process, handle, log_path)
    evaluation = t052.wait_for_evaluation(stack.dbname, evaluation_id, {"succeeded", "failed"})
    assert evaluation["report_id"] is not None, evaluation
    report = t052.report_row(stack.dbname, evaluation["report_id"])
    sealed = t052.report_document(stack, report["result_artifact_id"])
    return evaluation, report, sealed


# ---------------------------------------------------------------------------
# per-case results read (non-contract, ADR 0013)
# ---------------------------------------------------------------------------
def cases_path(report_id: str) -> str:
    return f"/api/v1/evaluation-reports/{report_id}/cases"


def get_cases(session, report_id: str):
    return session.client.get(cases_path(report_id))


def case_by_id(document: dict, case_id: str) -> dict:
    """One case from either the sealed report (``id``) or the cases read (``case_id``)."""
    for item in document["cases"]:
        if item.get("case_id", item.get("id")) == case_id:
            return item
    raise KeyError(case_id)


def outcome_of(case: dict, kind: str) -> dict:
    for outcome in case["assertions"]:
        if outcome["kind"] == kind:
            return outcome
    raise KeyError(kind)


def attachment_labels(case: dict) -> list[str]:
    return sorted(item["label"] for item in case["attachments"])


def scoped_api_key(stack: STACK, admin, responsible_user_id: str, scopes: list[str]):
    """A real service-account API key with exactly the given scopes."""
    service_id = f02a.create_service_account(
        admin, "t056-key-" + uuid.uuid4().hex[:8], responsible_user_id
    )
    _key_id, secret = f02a.create_api_key(admin, service_id, scopes)
    return f02a.bearer_client(stack, secret)
