"""F05/T030 real-stack helpers: preset tool runs over the T028 stack.

The stack is the real one: throwaway PostgreSQL migrated with the real
``cmd/admin migrate`` chain, the compiled ``cmd/api`` with a real object store and
HMAC-signed links, the compiled ``cmd/maintenance`` process running the real
``tool_run`` and ``artifact_finalize`` handlers (two real pools in one process),
and the real Python 3.12 interpreter executing the fixed preset script as a
subprocess.

No endpoint is mocked. Bytes move over signed links, the tool reads a real
workspace copy, outputs are admitted by the Go executor and sealed by the
artifact_finalize handler, and every state/error assertion is read either from
the public HTTP API or from PostgreSQL.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

from support import f00b, t028
from support import stack as st

T030Stack = t028.T028Stack
Session = t028.Session
admin_session = t028.admin_session
session_for = t028.session_for
invite_and_activate = t028.invite_and_activate
create_service_account = t028.create_service_account
create_api_key = t028.create_api_key
new_uuid = t028.new_uuid
sql_utf8 = t028.sql_utf8
put_link = t028.put_link
get_link = t028.get_link
sha256_hex = t028.sha256_hex
finalize_once = t028.finalize_once
artifact_row = t028.artifact_row
object_bytes = t028.object_bytes

PRODUCT_ROOT = st.PRODUCT_ROOT
REPO_ROOT = st.REPO_ROOT
EXAMPLES = REPO_ROOT / "examples" / "sales"

TOOL = "sales_stats"
KIND_TOOL_RUN = "tool_run"
KIND_ARTIFACT_FINALIZE = "artifact_finalize"

CSV = t028.CSV
XLSX = t028.XLSX
TEXT = t028.TEXT
JSON = t028.JSON

PURPOSE_SUMMARY = "tool_summary"
PURPOSE_ARTIFACT = "tool_artifact"
PURPOSE_DIAGNOSTICS = "diagnostics"
PURPOSE_LOG = "log"

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def create_tool_run(
    session: Session,
    artifact_id: str,
    *,
    tool: str = TOOL,
    key: str | None = None,
    csrf: bool = True,
    headers: dict[str, str] | None = None,
    client=None,
) -> st.Response:
    request_headers = {"Idempotency-Key": key or new_uuid()}
    request_headers.update(headers or {})
    target = client if client is not None else session.client
    return target.post(
        "/api/v1/tool-runs",
        body={"tool": tool, "artifact_id": artifact_id},
        csrf=session.csrf if csrf else None,
        headers=request_headers,
    )


def get_tool_run(session: Session, job_id: str, *, client=None) -> st.Response:
    target = client if client is not None else session.client
    return target.get(f"/api/v1/tool-runs/{job_id}")


def read_artifact_content(session: Session, artifact_id: str) -> st.Response:
    return t028.read_content(session, artifact_id)


def read_artifact_json(session: Session, artifact_id: str) -> dict:
    response = read_artifact_content(session, artifact_id)
    assert response.status == 200, (response.status, response.text)
    return json.loads(response.raw.decode("utf-8"))


def read_artifact_text(session: Session, artifact_id: str) -> str:
    response = read_artifact_content(session, artifact_id)
    assert response.status == 200, (response.status, response.text)
    return response.raw.decode("utf-8")


def wait_for_run_state(
    session: Session,
    job_id: str,
    states: set[str],
    *,
    timeout: float = 20.0,
) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        response = get_tool_run(session, job_id)
        if response.status == 200:
            last = response.json
            if last.get("state") in states:
                return last
        time.sleep(0.05)
    raise AssertionError(f"tool run {job_id} never reached {states}: {last}")


# ---------------------------------------------------------------------------
# uploads
# ---------------------------------------------------------------------------
def upload_ready_artifact(
    stack: T030Stack,
    session: Session,
    content: bytes,
    media_type: str = CSV,
    *,
    purpose: str = "task_input",
    finalize: bool = True,
) -> str:
    created = t028.create_upload(
        session, media_type=media_type, size_bytes=len(content), purpose=purpose
    )
    assert created.status == 201, created.text
    artifact_id = created.json["artifact_id"]
    assert t028.put_link(created.json["upload_url"], content).status == 204
    completed = t028.complete_upload(session, artifact_id, sha256_hex(content))
    assert completed.status == 202, completed.text
    if finalize:
        finalize_once(stack)
    t028.wait_for_artifact_state(session, artifact_id, {"ready"})
    return artifact_id


def upload_pending_artifact(
    stack: T030Stack,
    session: Session,
    content: bytes,
    media_type: str = CSV,
) -> str:
    """Reserve + put + complete but do not run the finalizer (not-ready case)."""
    created = t028.create_upload(
        session, media_type=media_type, size_bytes=len(content), purpose="task_input"
    )
    assert created.status == 201, created.text
    artifact_id = created.json["artifact_id"]
    assert t028.put_link(created.json["upload_url"], content).status == 204
    assert t028.complete_upload(session, artifact_id, sha256_hex(content)).status == 202
    return artifact_id


def seed_ready_artifact(
    stack: T030Stack,
    owner_id: str,
    content: bytes,
    media_type: str = XLSX,
) -> f00b.SeededArtifact:
    """Register a ready artifact directly (states the public path refuses).

    Used only for the macro-XLSX case: T028 admission deliberately quarantines a
    macro workbook, so the real upload path can never produce that input. The
    artifact row and its sealed bytes are real; the tool's own independent macro
    rejection is what the test proves.
    """
    return f00b.seed_artifact(
        stack.dbname,
        stack.object_dir,
        owner_id,
        content=content,
        state="ready",
        staging=False,
        sealed_content=content,
        media_type=media_type,
        purpose="task_input",
    )


# ---------------------------------------------------------------------------
# maintenance: one real process, both real pools
# ---------------------------------------------------------------------------
def run_tool_pools_once(
    stack: T030Stack,
    *,
    extra_env: dict[str, str] | None = None,
    timeout: int = 180,
) -> tuple[object, list[dict]]:
    """One `--once` drain with the tool_run work pool and finalize system_io pool.

    The handler waits for its own summary/log artifacts to be finalized, exactly
    like T024/T026; that is why one process must run both pools. This mirrors the
    documented pool split (artifact_finalize is system_io and never shares the
    single work slot).
    """
    env = f00b.maintenance_env(stack, extra=extra_env)
    completed = f00b.run_maintenance(
        stack,
        "--once",
        "--kinds",
        f"{KIND_TOOL_RUN},{KIND_ARTIFACT_FINALIZE}",
        "--poll-interval",
        "20ms",
        "--system-io-slots",
        "2",
        "--work-slots",
        "1",
        "--evaluation-poll-interval",
        "50ms",
        "--report-wait-timeout",
        "30s",
        env=env,
        timeout=timeout,
    )
    assert completed.returncode == 0, (
        f"maintenance run --once failed ({completed.returncode}):\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
    return completed, f00b.parse_json_lines(completed.stdout)


def job_reports(reports: list[dict]) -> list[dict]:
    return [report for report in reports if report.get("kind") == KIND_TOOL_RUN]


# ---------------------------------------------------------------------------
# read models
# ---------------------------------------------------------------------------
def job_row(dbname: str, job_id: str) -> dict:
    return f00b.job_row(dbname, job_id)


def job_artifacts(dbname: str, job_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT l.purpose, l.relation, a.id::text AS id, a.state, a.media_type, "
        "a.size_bytes, a.sha256, a.owner_id::text AS owner_id, a.object_key "
        "FROM platform.background_job_artifacts AS l "
        "JOIN platform.artifacts AS a ON a.id = l.artifact_id "
        f"WHERE l.background_job_id = {st.quote_literal(job_id)}::uuid "
        "ORDER BY l.created_at, a.id",
    )


def output_artifacts(dbname: str, job_id: str) -> list[dict]:
    return [row for row in job_artifacts(dbname, job_id) if row["relation"] == "output"]


def artifact_by_purpose(dbname: str, job_id: str, purpose: str) -> dict | None:
    matches = [row for row in output_artifacts(dbname, job_id) if row["purpose"] == purpose]
    assert len(matches) <= 1, matches
    return matches[0] if matches else None


def diagnostics_row(dbname: str, job_id: str) -> dict | None:
    return artifact_by_purpose(dbname, job_id, PURPOSE_DIAGNOSTICS)


def count_tool_runs(dbname: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.background_jobs WHERE kind = 'tool_run'",
        )
    )


def idempotency_receipts(dbname: str, path: str = "/api/v1/tool-runs") -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT principal_id::text AS principal_id, method, path, key, request_sha256, "
        "response_code, response_json::text AS response_json "
        "FROM platform.idempotency_keys "
        f"WHERE path = {st.quote_literal(path)} ORDER BY created_at",
    )


def delete_sealed_object(stack: T030Stack, artifact_id: str) -> None:
    row = artifact_row(stack.dbname, artifact_id)
    path = stack.object_dir / row["object_key"]
    assert path.is_file(), path
    path.unlink()


def job_available_at(dbname: str, job_id: str) -> str:
    return job_row(dbname, job_id)["available_at"]


# ---------------------------------------------------------------------------
# fixed example files
# ---------------------------------------------------------------------------
def example_csv() -> bytes:
    return (EXAMPLES / "input.csv").read_bytes()


def example_xlsx() -> bytes:
    return (EXAMPLES / "input.xlsx").read_bytes()


EXPECTED = {
    "total": "250.00",
    "regions": {"东区": "200.00", "西区": "50.00"},
    "order_count": 3,
}


# ---------------------------------------------------------------------------
# generated XLSX fixtures (real zip archives)
# ---------------------------------------------------------------------------
def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _column(index: int) -> str:
    letters = ""
    value = index
    while True:
        letters = chr(ord("A") + value % 26) + letters
        value = value // 26 - 1
        if value < 0:
            return letters


def _inline(ref: str, value: str) -> str:
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_escape(value)}</t></is></c>'


def _sheet(rows: list[list[str]]) -> str:
    body = ""
    for row_index, row in enumerate(rows, start=1):
        cells = "".join(_inline(f"{_column(i)}{row_index}", value) for i, value in enumerate(row))
        body += f'<row r="{row_index}">{cells}</row>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{NS}"><sheetData>{body}</sheetData></worksheet>'
    )


def xlsx_bytes(rows: list[list[str]], *, sheet: str | None = None, extra: dict[str, bytes] | None = None) -> bytes:
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{NS}" xmlns:r="{REL_NS}"><sheets>'
        '<sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{PKG_REL_NS}">'
        f'<Relationship Id="rId1" Type="{REL_NS}/worksheet" Target="/xl/worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    entries = {
        "[Content_Types].xml": content_types.encode("utf-8"),
        "xl/workbook.xml": workbook.encode("utf-8"),
        "xl/_rels/workbook.xml.rels": rels.encode("utf-8"),
        "xl/worksheets/sheet1.xml": (sheet or _sheet(rows)).encode("utf-8"),
    }
    entries.update(extra or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def formula_xlsx() -> bytes:
    """The example workbook with D2 replaced by a formula cell."""
    rows = [
        ["order_id", "date", "region", "amount"],
        ["A1", "2026-01-01", "东区", "120.50"],
        ["A2", "2026-01-02", "东区", "79.50"],
        ["A3", "2026-01-03", "西区", "50.00"],
    ]
    sheet = _sheet(rows).replace(
        '<c r="D2" t="inlineStr"><is><t xml:space="preserve">120.50</t></is></c>',
        '<c r="D2"><f>SUM(1,2)</f><v>3</v></c>',
    )
    return xlsx_bytes(rows, sheet=sheet)


def macro_xlsx() -> bytes:
    return t028.macro_xlsx_bytes()


def assert_create_view(body: dict) -> None:
    assert set(body) == {"id", "state", "tool", "artifact_id"}, body
    assert isinstance(body["id"], str) and body["id"]
    assert isinstance(body["state"], str) and body["state"]
    assert body["tool"] == TOOL
    assert isinstance(body["artifact_id"], str) and body["artifact_id"]


def assert_run_view(body: dict) -> None:
    assert set(body) == {"id", "state", "error_code", "artifacts"}, body
    assert isinstance(body["id"], str)
    assert isinstance(body["state"], str)
    assert body["error_code"] is None or isinstance(body["error_code"], str)
    assert isinstance(body["artifacts"], list)
    for artifact in body["artifacts"]:
        assert set(artifact) == {"id", "purpose", "state", "media_type", "size_bytes"}, artifact
        assert isinstance(artifact["id"], str)
        assert artifact["purpose"] in {
            PURPOSE_SUMMARY,
            PURPOSE_ARTIFACT,
            PURPOSE_DIAGNOSTICS,
            PURPOSE_LOG,
        }, artifact
        assert isinstance(artifact["size_bytes"], int) and artifact["size_bytes"] >= 0


def assert_diagnostics_safe(document: dict, *, expected_code: str) -> None:
    assert set(document) >= {
        "diagnostics_version",
        "tool",
        "status",
        "error_code",
        "message",
        "fix",
        "retryable",
        "input",
    }, document
    assert document["error_code"] == expected_code, document
    assert document["status"] == "rejected"
    assert document["tool"] == TOOL
    assert isinstance(document["message"], str) and document["message"]
    assert isinstance(document["fix"], str) and document["fix"]
    input_facts = document["input"]
    assert set(input_facts) == {"artifact_id", "media_type", "size_bytes", "sha256"}, input_facts
    text = json.dumps(document, ensure_ascii=False)
    assert "sealed/" not in text and "staging/" not in text, text
    assert "ORCHARA_" not in text, text
    assert str(PRODUCT_ROOT) not in text, text
