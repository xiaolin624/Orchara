"""F04A/T049 real-stack helpers: the sales template, its copied application and
the independent Decimal truth checker.

Everything here goes through the product's real path:

* a model profile registered and validated through the real T039 HTTP endpoints
  and the real ``model_validate`` + ``artifact_finalize`` maintenance processes;
* the template seeded by the real ``orchara-admin seed-sales-template`` command,
  which reads the real ``sales_stats.py`` bytes for the tool image digest;
* the non-contract instantiate endpoint called by a second creator over HTTP;
* the real debug submission, the compiled supervisor and the real WSL2/runsc
  sandbox for both the python node and the agent node;
* the real node-results read and owner-only artifact downloads.

The checker below is deliberately independent of the tool: it recomputes the
public sample with :class:`decimal.Decimal` from literal rows and compares the
tool's summary/document to that result. It never imports the tool.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from decimal import Decimal

from support import run_observations as observations
from support import execution_support as execution
from support import stack as st
from support import f00b, f02a, t027, t033, t039

ADMIN_LOGIN = f02a.ADMIN_LOGIN
SALES_TEMPLATE_ID = "sales"
PRESET_TOOL_ID = "11111111-1111-4111-8111-111111111111"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# The public sample from WORKFLOW-SPEC §6. These literals are the independent
# source of truth; the checker never derives them from the tool's output.
PUBLIC_ROWS = (
    ("order_id", "date", "region", "amount"),
    ("A1", "2026-01-01", "东区", "120.50"),
    ("A2", "2026-01-02", "东区", "79.50"),
    ("A3", "2026-01-03", "西区", "50.00"),
)
EXPECTED_TOTAL = Decimal("250.00")
EXPECTED_REGIONS = {"东区": Decimal("200.00"), "西区": Decimal("50.00")}
EXPECTED_ORDER_COUNT = 3


# ---------------------------------------------------------------------------
# model + template seeding through the real commands
# ---------------------------------------------------------------------------
def create_passed_model(stack, admin) -> str:
    """Register and validate one dev-stub profile through the real T039 flow."""
    model = t039.register_or_fail(admin)
    assert model["state"] == "pending", model
    key = "t049-model-" + model["id"][:8]
    accepted = t039.validate(admin, model["id"], key=key)
    assert accepted.status == 202, accepted.text
    completed, reports, finalizer_log = t039.run_validation(stack)
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}\n{finalizer_log}"
    job_reports = f00b.job_reports(reports)
    assert len(job_reports) == 1, reports
    assert job_reports[0]["result"] == "succeeded", job_reports
    passed = t039.get_model(admin, model["id"])
    assert passed.status == 200, passed.text
    assert passed.json["validation_state"] == "passed", passed.json
    assert t039.model_row(stack.dbname, model["id"])["provider"] == "stub"
    return model["id"]


def seed_template(stack, model_id: str, *, login: str = "ADMIN") -> dict:
    """Run the real seed command; return its JSON summary."""
    completed = st.run_admin(
        stack.binaries["admin"],
        [
            "seed-sales-template",
            "--owner-login",
            login,
            "--model-version-id",
            model_id,
        ],
        dsn=stack.dsn,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, completed.stdout
    summary = json.loads(lines[0])
    assert set(summary) == {
        "template_id",
        "application_id",
        "tool_version_id",
        "model_version_id",
    }, summary
    assert summary["template_id"] == SALES_TEMPLATE_ID, summary
    assert summary["tool_version_id"] == PRESET_TOOL_ID, summary
    assert summary["model_version_id"] == model_id, summary
    return summary


def instantiate(session, template_id: str = SALES_TEMPLATE_ID):
    return session.client.post(
        f"/api/v1/templates/{template_id}/applications",
        body={},
        csrf=session.csrf,
    )


def instantiate_or_fail(session, template_id: str = SALES_TEMPLATE_ID) -> dict:
    response = instantiate(session, template_id)
    assert response.status == 201, response.text
    body = response.json
    assert set(body) == {"id", "revision", "definition_revision"}, body
    assert body["revision"] == 1, body
    assert body["definition_revision"] == 1, body
    return body


# ---------------------------------------------------------------------------
# run path
# ---------------------------------------------------------------------------
def submit_sales_run(
    session, application_id: str, artifact_id: str, *, media_type: str, file_name: str
):
    revision = t027.draft_revision(session, application_id)
    response = t027.submit_debug(
        session,
        application_id,
        draft_revision=revision,
        input_value={"file": file_name, "media_type": media_type},
        artifact_ids=[artifact_id],
    )
    assert response.status == 202, response.text
    run = response.json
    assert run["state"] == "queued", run
    return run


def run_once(stack, *, env_extra: dict[str, str] | None = None):
    env = execution.supervisor_exec_env(stack)
    if env_extra:
        env.update(env_extra)
    with execution.FinalizeWorker(stack):
        completed = execution.run_supervisor_exec(stack, env)
    return completed, execution.exec_report(completed)


def wait_node_results(session, run_id: str, *, expected: int = 7) -> dict:
    return observations.wait_node_results(session, run_id, expected=expected)


def grouped(items: list[dict]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for item in items:
        result.setdefault(item["node_id"], []).append(item)
    return result


def rows_of(grouped_rows: dict[str, list[dict]], node: str, kind: str) -> list[dict]:
    return [row for row in grouped_rows.get(node, []) if row["kind"] == kind]


def content_json(session, artifact_id: str) -> dict:
    response = observations.artifact_content(session, artifact_id)
    assert response.status == 200, response.text
    return json.loads(response.raw)


def content_bytes(session, artifact_id: str) -> bytes:
    response = observations.artifact_content(session, artifact_id)
    assert response.status == 200, response.text
    return response.raw


def download(session, artifact_id: str):
    return t033.artifact_download(session, artifact_id)


# ---------------------------------------------------------------------------
# independent Decimal checker (test-side, never the tool)
# ---------------------------------------------------------------------------
class CheckerError(AssertionError):
    pass


def _decimal(value: object, *, field: str) -> Decimal:
    if not isinstance(value, str):
        raise CheckerError(f"{field} must be a decimal string, got {value!r}")
    if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)\.[0-9]{2}", value):
        raise CheckerError(f"{field} is not a two-place decimal string: {value!r}")
    return Decimal(value)


def independent_truth() -> tuple[Decimal, dict[str, Decimal], int]:
    """Recompute the public sample with Decimal from the literal rows."""
    total = Decimal("0.00")
    regions: dict[str, Decimal] = {}
    count = 0
    for order_id, _date, region, amount in PUBLIC_ROWS[1:]:
        value = Decimal(amount)
        total += value
        regions[region] = regions.get(region, Decimal("0.00")) + value
        count += 1
    return total, regions, count


def check_summary(summary: dict) -> None:
    """Check one summary document against the independent Decimal truth."""
    if not isinstance(summary, dict):
        raise CheckerError(f"summary must be an object, got {summary!r}")
    if set(summary) != {"total", "regions", "order_count"}:
        raise CheckerError(f"summary fields = {sorted(summary)}")
    total, regions, count = independent_truth()

    got_total = _decimal(summary["total"], field="summary.total")
    if got_total != total:
        raise CheckerError(f"summary total {got_total} != independent {total}")

    got_regions = summary["regions"]
    if not isinstance(got_regions, dict):
        raise CheckerError("summary.regions must be an object")
    normalised = {
        name: _decimal(value, field=f"summary.regions[{name!r}]")
        for name, value in got_regions.items()
    }
    if normalised != regions:
        raise CheckerError(f"summary regions {normalised} != independent {regions}")

    if summary["order_count"] != count:
        raise CheckerError(f"summary order_count {summary['order_count']!r} != independent {count}")

    # Defence in depth: the region decimals must add up to the total.
    if sum(normalised.values(), Decimal("0.00")) != got_total:
        raise CheckerError("summary regions do not add up to the summary total")


def check_summary_csv(payload: bytes) -> None:
    """Check the exported CSV against the independent Decimal truth."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CheckerError(f"summary.csv is not UTF-8: {error}")
    lines = [line for line in text.splitlines() if line]
    if not lines or lines[0] != "region,total":
        raise CheckerError(f"summary.csv header = {lines[:1]}")
    total, regions, _count = independent_truth()
    seen: dict[str, Decimal] = {}
    for line in lines[1:]:
        name, _, value = line.rpartition(",")
        if not name:
            raise CheckerError(f"summary.csv row is malformed: {line!r}")
        seen[name] = _decimal(value, field=f"summary.csv[{name!r}]")
    if seen != regions:
        raise CheckerError(f"summary.csv regions {seen} != independent {regions}")
    if sum(seen.values(), Decimal("0.00")) != total:
        raise CheckerError("summary.csv regions do not add up to the independent total")


AMOUNT = re.compile(r"(?<![0-9.])(?:0|[1-9][0-9]*)\.[0-9]{2}(?![0-9])")


def check_report(report: str, summary: dict) -> None:
    """Check the report only cites the verified numbers.

    Every two-place decimal in the report must be one of the verified region
    totals or the verified grand total, and all three must appear, so a report
    that invents an amount or drops a verified one fails.
    """
    if not isinstance(report, str) or not report.strip():
        raise CheckerError("report must be a non-empty string")
    _total, regions, count = independent_truth()
    verified = set(regions.values()) | {EXPECTED_TOTAL}
    amounts = {Decimal(value) for value in AMOUNT.findall(report)}
    invented = amounts - verified
    if invented:
        raise CheckerError(f"report cites amounts outside the verified statistics: {sorted(invented)}")
    missing = verified - amounts
    if missing:
        raise CheckerError(f"report omits verified amounts: {sorted(missing)}")
    if f"订单数 {count}" not in report:
        raise CheckerError(f"report does not state the verified order count {count}: {report!r}")
    if summary["total"] not in report:
        raise CheckerError("report does not state the verified grand total")


def check_run_documents(summary: dict, summary_csv: bytes, report: str, totals: dict) -> None:
    check_summary(summary)
    check_summary_csv(summary_csv)
    check_report(report, summary)
    if totals != summary:
        raise CheckerError(f"totals {totals!r} are not the verified summary {summary!r}")


# ---------------------------------------------------------------------------
# XLSX bytes for the equivalent input (same rows, real zip/XML workbook)
# ---------------------------------------------------------------------------
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _column(index: int) -> str:
    letters = ""
    value = index
    while True:
        letters = chr(ord("A") + value % 26) + letters
        value = value // 26 - 1
        if value < 0:
            return letters


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _cell(ref: str, value: str) -> str:
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_escape(value)}</t></is></c>'


def xlsx_bytes(rows: list[list[object]], *, sheet: str | None = None) -> bytes:
    """A minimal real XLSX workbook with inline strings (no third-party import)."""
    body = ""
    for row_index, row in enumerate(rows, start=1):
        cells = "".join(
            _cell(f"{_column(column_index)}{row_index}", str(value))
            for column_index, value in enumerate(row)
        )
        body += f'<row r="{row_index}">' + cells + "</row>"
    sheet_xml = sheet or (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{NS}"><sheetData>{body}</sheetData></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{NS}" xmlns:r="{REL_NS}"><sheets>'
        '<sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
        "</sheets></workbook>"
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
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buffer.getvalue()


SAMPLE_ROWS = [list(row) for row in PUBLIC_ROWS]
