"""T030: the trusted preset sales-statistics tool, tested as a real subprocess.

These tests do not need PostgreSQL, the API or the maintenance binary: they run
``worker/platform_worker/nodes/sales_stats.py`` exactly the way the Go executor
does (fixed argv, a real workspace, real input files) and inspect the real
``output.json`` and artifact bytes it wrote.

What is proven here:

* the published example CSV and XLSX produce exactly ``expected.json``;
* money is summed with ``decimal.Decimal`` (no float), decimal strings keep
  their trailing zeros (``250.00``), and ``0.1 + 0.2`` stays exact;
* every documented rejection is a specific safe code with a row/column pointer
  and no traceback/host path, and a rejected run writes no summary artifacts;
* XLSX is read with the stdlib only (zip + XML), and macros, DTD/entities,
  formulas, archive/row/cell/worksheet caps are refused;
* the tool writes only inside its workspace and its artifact writer refuses
  traversal-shaped paths.
"""

from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TESTS_ROOT.parent
REPO_ROOT = PRODUCT_ROOT.parent
TOOL = PRODUCT_ROOT / "worker" / "platform_worker" / "nodes" / "sales_stats.py"
EXAMPLES = REPO_ROOT / "examples" / "sales"

CSV_MEDIA_TYPE = "text/csv"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

EXPECTED = {
    "total": "250.00",
    "regions": {"东区": "200.00", "西区": "50.00"},
    "order_count": 3,
}

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


# ---------------------------------------------------------------------------
# subprocess harness
# ---------------------------------------------------------------------------
@dataclass
class ToolRun:
    returncode: int
    stdout: str
    stderr: str
    workspace: Path
    output: dict | None
    artifacts_dir: Path

    def artifact(self, name: str) -> bytes:
        return (self.artifacts_dir / name).read_bytes()

    def data(self) -> dict:
        assert self.output is not None
        return self.output["data"]

    def rejection(self) -> dict:
        assert self.output is not None
        assert "error" in self.output, self.output
        return self.output["error"]


def _input_document(media_type: str, file_name: str, artifact_id: str = "11111111-1111-4111-8111-111111111111") -> dict:
    return {
        "tool": "sales_stats",
        "file": file_name,
        "artifact": {
            "artifact_id": artifact_id,
            "media_type": media_type,
            "size_bytes": 1,
            "sha256": "0" * 64,
        },
    }


def run_tool(
    tmp_path: Path,
    content: bytes,
    *,
    media_type: str = CSV_MEDIA_TYPE,
    file_name: str = "input.csv",
    input_document: dict | None = None,
    arguments: list[str] | None = None,
) -> ToolRun:
    workspace = tmp_path / "workspace"
    inputs = workspace / "inputs"
    artifacts = workspace / "artifacts"
    inputs.mkdir(parents=True)
    artifacts.mkdir(parents=True)
    (inputs / file_name).write_bytes(content)
    document = input_document if input_document is not None else _input_document(media_type, file_name)
    input_json = workspace / "input.json"
    input_json.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    output_json = workspace / "output.json"

    argv = [
        sys.executable,
        str(TOOL),
        "--input",
        str(input_json),
        "--output",
        str(output_json),
        "--inputs-dir",
        str(inputs),
        "--artifacts-dir",
        str(artifacts),
    ]
    if arguments is not None:
        argv = [sys.executable, str(TOOL), *arguments]
    completed = subprocess.run(
        argv,
        cwd=str(workspace),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    parsed = None
    if output_json.exists():
        parsed = json.loads(output_json.read_text(encoding="utf-8"))
    return ToolRun(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        workspace=workspace,
        output=parsed,
        artifacts_dir=artifacts,
    )


def assert_rejected(run: ToolRun, code: str, *, row: int | None = None, column: str | None = None) -> dict:
    assert run.returncode == 3, (run.returncode, run.stdout, run.stderr)
    rejection = run.rejection()
    assert rejection["code"] == code, rejection
    assert isinstance(rejection["message"], str) and rejection["message"], rejection
    assert isinstance(rejection.get("fix"), str) and rejection["fix"], rejection
    location = rejection.get("location") or {}
    if row is not None:
        assert location.get("row") == row, rejection
    if column is not None:
        assert location.get("column") == column, rejection
    # The rejection is safe: no traceback, no absolute host path, no input echo.
    assert "Traceback" not in run.stderr, run.stderr
    assert str(run.workspace) not in json.dumps(rejection, ensure_ascii=False), rejection
    assert str(run.workspace) not in run.stderr, run.stderr
    # A rejected run never writes summary artifacts.
    assert sorted(path.name for path in run.artifacts_dir.iterdir()) == [], run.artifacts_dir
    return rejection


# ---------------------------------------------------------------------------
# XLSX fixture builders (real zip archives, stdlib only)
# ---------------------------------------------------------------------------
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


def _inline_cell(ref: str, value: str) -> str:
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_escape(value)}</t></is></c>'


def _numeric_cell(ref: str, value: str) -> str:
    return f'<c r="{ref}"><v>{_escape(value)}</v></c>'


def sheet_xml(rows: list[list[object]], *, formula_cell: str | None = None, extra_cells: int = 0) -> str:
    body = ""
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row):
            ref = f"{_column(column_index)}{row_index}"
            if isinstance(value, tuple) and value[0] == "number":
                cells.append(_numeric_cell(ref, value[1]))
            else:
                cells.append(_inline_cell(ref, str(value)))
        for offset in range(extra_cells):
            ref = f"{_column(len(row) + offset)}{row_index}"
            cells.append(f'<c r="{ref}"/>')
        body += f'<row r="{row_index}">' + "".join(cells) + "</row>"
    if formula_cell is not None:
        body = body.replace(
            f'<c r="{formula_cell}" t="inlineStr"><is><t xml:space="preserve">PLACEHOLDER</t></is></c>',
            f'<c r="{formula_cell}"><f>SUM(1,2)</f><v>3</v></c>',
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{NS}"><sheetData>{body}</sheetData></worksheet>'
    )


def workbook_xml(sheet_count: int = 1) -> str:
    sheets = "".join(
        f'<sheet name="Sheet{index + 1}" sheetId="{index + 1}" r:id="rId{index + 1}"/>'
        for index in range(sheet_count)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{NS}" xmlns:r="{REL_NS}"><sheets>{sheets}</sheets></workbook>'
    )


def workbook_rels(sheet_count: int = 1) -> str:
    rels = "".join(
        f'<Relationship Id="rId{index + 1}" '
        f'Type="{REL_NS}/worksheet" Target="/xl/worksheets/sheet1.xml"/>'
        for index in range(sheet_count)
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{PKG_REL_NS}">{rels}</Relationships>'


def content_types() -> str:
    return (
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


def xlsx_bytes(
    rows: list[list[object]],
    *,
    sheet: str | None = None,
    extra_entries: dict[str, bytes] | None = None,
    sheet_count: int = 1,
    workbook: str | None = None,
    rels: str | None = None,
    omit_workbook: bool = False,
    omit_content_types: bool = False,
) -> bytes:
    import io

    entries: dict[str, bytes] = {}
    if not omit_content_types:
        entries["[Content_Types].xml"] = content_types().encode("utf-8")
    if not omit_workbook:
        entries["xl/workbook.xml"] = (workbook or workbook_xml(sheet_count)).encode("utf-8")
        entries["xl/_rels/workbook.xml.rels"] = (rels or workbook_rels(sheet_count)).encode("utf-8")
    entries["xl/worksheets/sheet1.xml"] = (sheet or sheet_xml(rows)).encode("utf-8")
    for name, payload in (extra_entries or {}).items():
        entries[name] = payload
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


SAMPLE_ROWS = [
    ["order_id", "date", "region", "amount"],
    ["A1", "2026-01-01", "东区", "120.50"],
    ["A2", "2026-01-02", "东区", "79.50"],
    ["A3", "2026-01-03", "西区", "50.00"],
]


# ---------------------------------------------------------------------------
# success: the published example, exact results
# ---------------------------------------------------------------------------
def test_the_example_csv_produces_the_published_expected_json(tmp_path) -> None:
    content = (EXAMPLES / "input.csv").read_bytes()
    run = run_tool(tmp_path, content)
    assert run.returncode == 0, (run.stdout, run.stderr)
    assert run.output is not None
    assert run.output["data"] == EXPECTED
    assert run.output["artifacts"] == ["summary.json", "summary.csv"]

    summary = json.loads(run.artifact("summary.json").decode("utf-8"))
    assert summary == EXPECTED
    assert run.artifact("summary.csv").decode("utf-8") == "region,total\n东区,200.00\n西区,50.00\n"
    # No floating-point number appears anywhere in the summary bytes: every
    # amount is a quoted decimal string.
    assert '"total": "250.00"' in run.artifact("summary.json").decode("utf-8")
    assert "250.0" not in run.artifact("summary.csv").decode("utf-8").replace("250.00", "")

    # The tool wrote only inside its workspace: input.json, inputs/ and the
    # artifacts directory it was given, plus output.json.
    outside = [
        path
        for path in run.workspace.parent.rglob("*")
        if path.is_file() and run.workspace not in path.parents
    ]
    assert outside == [], outside
    assert sorted(path.name for path in run.artifacts_dir.iterdir()) == ["summary.csv", "summary.json"]


def test_the_example_xlsx_produces_the_same_result(tmp_path) -> None:
    content = (EXAMPLES / "input.xlsx").read_bytes()
    run = run_tool(tmp_path, content, media_type=XLSX_MEDIA_TYPE, file_name="input.xlsx")
    assert run.returncode == 0, (run.stdout, run.stderr)
    assert run.data() == EXPECTED
    assert json.loads(run.artifact("summary.json").decode("utf-8")) == EXPECTED
    assert run.artifact("summary.csv").decode("utf-8") == "region,total\n东区,200.00\n西区,50.00\n"


def test_decimal_sums_are_exact_and_trailing_zeros_are_preserved(tmp_path) -> None:
    content = (
        "order_id,date,region,amount\n"
        "O1,2026-02-01,北区,0.1\n"
        "O2,2026-02-02,北区,0.2\n"
        "O3,2026-02-03,南区,120.50\n"
        "O4,2026-02-04,南区,1\n"
    ).encode("utf-8")
    run = run_tool(tmp_path, content)
    assert run.returncode == 0, (run.stdout, run.stderr)
    assert run.data() == {
        "total": "121.80",
        "regions": {"北区": "0.30", "南区": "121.50"},
        "order_count": 4,
    }
    # 0.1 + 0.2 must be 0.30, never 0.30000000000000004.
    assert run.artifact("summary.csv").decode("utf-8") == "region,total\n北区,0.30\n南区,121.50\n"
    raw = run.artifact("summary.json").decode("utf-8")
    assert "0.30000000000000004" not in raw
    assert isinstance(run.data()["total"], str)


def test_a_utf8_bom_and_crlf_csv_are_read_without_guessing_cells(tmp_path) -> None:
    content = (
        "\ufefforder_id,date,region,amount\r\n"
        "A1,2026-01-01,东区,120.50\r\n"
        "A2,2026-01-02,西区,50.00\r\n"
    ).encode("utf-8")
    run = run_tool(tmp_path, content)
    assert run.returncode == 0, (run.stdout, run.stderr)
    assert run.data()["total"] == "170.50"
    assert run.data()["regions"] == {"东区": "120.50", "西区": "50.00"}


def test_a_generated_xlsx_with_numeric_amounts_is_read_as_decimal_text(tmp_path) -> None:
    rows = [
        ["order_id", "date", "region", "amount"],
        ["A1", "2026-01-01", "东区", ("number", "120.50")],
        ["A2", "2026-01-02", "西区", ("number", "50")],
    ]
    run = run_tool(tmp_path, xlsx_bytes(rows), media_type=XLSX_MEDIA_TYPE, file_name="input.xlsx")
    assert run.returncode == 0, (run.stdout, run.stderr)
    assert run.data() == {"total": "170.50", "regions": {"东区": "120.50", "西区": "50.00"}, "order_count": 2}


def test_a_shared_strings_xlsx_is_read(tmp_path) -> None:
    shared = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<sst xmlns="{NS}" count="8" uniqueCount="8">'
        + "".join(f"<si><t>{_escape(value)}</t></si>" for value in [
            "order_id", "date", "region", "amount", "A1", "2026-01-01", "东区", "120.50",
        ])
        + "</sst>"
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<worksheet xmlns="{NS}"><sheetData>'
        '<row r="1">'
        '<c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
        '<c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c></row>'
        '<row r="2">'
        '<c r="A2" t="s"><v>4</v></c><c r="B2" t="s"><v>5</v></c>'
        '<c r="C2" t="s"><v>6</v></c><c r="D2" t="s"><v>7</v></c></row>'
        "</sheetData></worksheet>"
    )
    payload = xlsx_bytes([], sheet=sheet, extra_entries={"xl/sharedStrings.xml": shared.encode("utf-8")})
    run = run_tool(tmp_path, payload, media_type=XLSX_MEDIA_TYPE, file_name="input.xlsx")
    assert run.returncode == 0, (run.stdout, run.stderr)
    assert run.data() == {"total": "120.50", "regions": {"东区": "120.50"}, "order_count": 1}


# ---------------------------------------------------------------------------
# rejections: the user must get a specific safe code
# ---------------------------------------------------------------------------
def test_missing_column_is_rejected_with_the_column_name(tmp_path) -> None:
    run = run_tool(tmp_path, b"order_id,region,amount\nA1,x,1.00\n")
    assert_rejected(run, "missing_column", row=1, column="date")


def test_extra_column_is_rejected_with_the_extra_header(tmp_path) -> None:
    run = run_tool(tmp_path, b"order_id,date,region,amount,currency\nA1,2026-01-01,x,1.00,USD\n")
    assert_rejected(run, "extra_column", row=1, column="currency")


def test_reordered_columns_are_refused_instead_of_guessed(tmp_path) -> None:
    run = run_tool(tmp_path, b"date,order_id,region,amount\n2026-01-01,A1,x,1.00\n")
    assert_rejected(run, "column_order_mismatch", row=1)


def test_duplicate_order_id_is_rejected(tmp_path) -> None:
    run = run_tool(
        tmp_path,
        b"order_id,date,region,amount\nA1,2026-01-01,x,1.00\nA1,2026-01-02,x,2.00\n",
    )
    assert_rejected(run, "duplicate_order_id", row=3, column="order_id")


def test_negative_amount_is_rejected(tmp_path) -> None:
    run = run_tool(tmp_path, b"order_id,date,region,amount\nA1,2026-01-01,x,-1.00\n")
    assert_rejected(run, "negative_amount", row=2, column="amount")


def test_non_decimal_amount_is_rejected(tmp_path) -> None:
    for amount in ("1e3", "abc", "1.001", " 1.00", "--1.00"):
        run = run_tool(tmp_path / amount.replace(" ", "_"), f"order_id,date,region,amount\nA1,2026-01-01,x,{amount}\n".encode())
        assert_rejected(run, "invalid_amount", row=2, column="amount")


def test_empty_region_is_rejected(tmp_path) -> None:
    run = run_tool(tmp_path, b"order_id,date,region,amount\nA1,2026-01-01, ,1.00\n")
    assert_rejected(run, "empty_region", row=2, column="region")


def test_invalid_iso_date_is_rejected(tmp_path) -> None:
    for date in ("2026-13-01", "20260101", "01/02/2026", "2026-02-30"):
        run = run_tool(
            tmp_path / date.replace("/", "_"),
            f"order_id,date,region,amount\nA1,{date},x,1.00\n".encode(),
        )
        assert_rejected(run, "invalid_date", row=2, column="date")


def test_a_formula_in_any_csv_cell_is_rejected_not_executed(tmp_path) -> None:
    run = run_tool(tmp_path, b'order_id,date,region,amount\nA1,2026-01-01,x,"=SUM(1,2)"\n')
    assert_rejected(run, "formula_not_supported", row=2, column="amount")

    run = run_tool(tmp_path / "id", b"order_id,date,region,amount\n=1+1,2026-01-01,x,1.00\n")
    assert_rejected(run, "formula_not_supported", row=2, column="order_id")


def test_a_wrong_cell_count_row_is_rejected(tmp_path) -> None:
    run = run_tool(tmp_path, b"order_id,date,region,amount\nA1,2026-01-01,x\n")
    assert_rejected(run, "invalid_row", row=2)


def test_an_empty_dataset_is_rejected(tmp_path) -> None:
    run = run_tool(tmp_path, b"order_id,date,region,amount\n")
    assert_rejected(run, "empty_input")


def test_invalid_utf8_is_rejected(tmp_path) -> None:
    run = run_tool(tmp_path, b"order_id,date,region,amount\nA1,2026-01-01,\xff\xfe,1.00\n")
    assert_rejected(run, "invalid_encoding")


def test_a_media_type_outside_the_tool_contract_is_rejected(tmp_path) -> None:
    run = run_tool(
        tmp_path,
        b'{"orders":[]}',
        media_type="application/json",
        file_name="input.json",
    )
    assert_rejected(run, "unsupported_media_type")


def test_a_missing_input_file_is_rejected_safely(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "inputs").mkdir(parents=True)
    (workspace / "artifacts").mkdir()
    document = _input_document(CSV_MEDIA_TYPE, "does-not-exist.csv")
    input_json = workspace / "input.json"
    input_json.write_text(json.dumps(document), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--input",
            str(input_json),
            "--output",
            str(workspace / "output.json"),
            "--inputs-dir",
            str(workspace / "inputs"),
            "--artifacts-dir",
            str(workspace / "artifacts"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 3, (completed.stdout, completed.stderr)
    rejection = json.loads((workspace / "output.json").read_text(encoding="utf-8"))["error"]
    assert rejection["code"] == "input_file_missing", rejection
    assert str(workspace) not in json.dumps(rejection), rejection


# ---------------------------------------------------------------------------
# XLSX rejections and caps
# ---------------------------------------------------------------------------
def test_a_formula_cell_in_xlsx_is_rejected(tmp_path) -> None:
    sheet = sheet_xml(SAMPLE_ROWS)
    sheet = sheet.replace(
        '<c r="D2" t="inlineStr"><is><t xml:space="preserve">120.50</t></is></c>',
        '<c r="D2"><f>SUM(1,2)</f><v>3</v></c>',
    )
    payload = xlsx_bytes([], sheet=sheet)
    run = run_tool(tmp_path, payload, media_type=XLSX_MEDIA_TYPE, file_name="input.xlsx")
    rejection = assert_rejected(run, "formula_not_supported", row=2, column="amount")
    assert rejection["location"].get("cell") == "D2"


def test_a_macro_xlsx_is_rejected(tmp_path) -> None:
    payload = xlsx_bytes(
        SAMPLE_ROWS, extra_entries={"xl/vbaProject.bin": b"macro bytes"}
    )
    run = run_tool(tmp_path, payload, media_type=XLSX_MEDIA_TYPE, file_name="input.xlsx")
    assert_rejected(run, "macro_present")


def test_a_doctype_or_entity_in_xlsx_is_rejected(tmp_path) -> None:
    sheet = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE worksheet [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        f'<worksheet xmlns="{NS}"><sheetData/></worksheet>'
    )
    run = run_tool(
        tmp_path,
        xlsx_bytes([], sheet=sheet),
        media_type=XLSX_MEDIA_TYPE,
        file_name="input.xlsx",
    )
    assert_rejected(run, "dtd_not_supported")


def test_a_zip_entry_cap_is_enforced(tmp_path) -> None:
    extra = {f"xl/dummy{i}.xml": b"<x/>" for i in range(520)}
    run = run_tool(
        tmp_path,
        xlsx_bytes(SAMPLE_ROWS, extra_entries=extra),
        media_type=XLSX_MEDIA_TYPE,
        file_name="input.xlsx",
    )
    assert_rejected(run, "archive_limit_exceeded")


def test_a_zip_compression_ratio_bomb_is_rejected(tmp_path) -> None:
    payload = xlsx_bytes(
        SAMPLE_ROWS, extra_entries={"xl/bomb.xml": b"0" * (2 * 1024 * 1024)}
    )
    run = run_tool(tmp_path, payload, media_type=XLSX_MEDIA_TYPE, file_name="input.xlsx")
    assert_rejected(run, "archive_limit_exceeded")


def test_a_row_limit_is_enforced(tmp_path) -> None:
    rows = [["order_id", "date", "region", "amount"]] + [
        [f"O{index}", "2026-01-01", "x", "1.00"] for index in range(10_001)
    ]
    run = run_tool(
        tmp_path,
        xlsx_bytes(rows),
        media_type=XLSX_MEDIA_TYPE,
        file_name="input.xlsx",
    )
    assert_rejected(run, "row_limit_exceeded")


def test_a_cell_limit_is_enforced(tmp_path) -> None:
    rows = [["order_id", "date", "region", "amount"]] + [
        [f"O{index}", "2026-01-01", "x", "1.00", "extra"] for index in range(9_000)
    ]
    run = run_tool(
        tmp_path,
        xlsx_bytes(rows),
        media_type=XLSX_MEDIA_TYPE,
        file_name="input.xlsx",
    )
    assert_rejected(run, "cell_limit_exceeded")


def test_a_maximum_legal_sheet_is_not_rejected_by_the_cell_cap(tmp_path) -> None:
    # 10,000 data rows x 4 columns + the header is exactly 40,004 cells. The cap
    # must admit this legal maximum; the next row is rejected by the row cap.
    rows = [["order_id", "date", "region", "amount"]] + [
        [f"O{index}", "2026-01-01", "x", "1.00"] for index in range(10_000)
    ]
    run = run_tool(
        tmp_path,
        xlsx_bytes(rows),
        media_type=XLSX_MEDIA_TYPE,
        file_name="input.xlsx",
    )
    assert run.returncode == 0, (run.stdout, run.stderr)
    assert run.data() == {"total": "10000.00", "regions": {"x": "10000.00"}, "order_count": 10_000}


def test_a_worksheet_limit_is_enforced(tmp_path) -> None:
    run = run_tool(
        tmp_path,
        xlsx_bytes(SAMPLE_ROWS, sheet_count=65),
        media_type=XLSX_MEDIA_TYPE,
        file_name="input.xlsx",
    )
    assert_rejected(run, "worksheet_limit_exceeded")


def test_a_missing_workbook_is_rejected(tmp_path) -> None:
    run = run_tool(
        tmp_path,
        xlsx_bytes(SAMPLE_ROWS, omit_workbook=True),
        media_type=XLSX_MEDIA_TYPE,
        file_name="input.xlsx",
    )
    assert_rejected(run, "invalid_xlsx")


def test_a_zip_path_traversal_entry_is_rejected(tmp_path) -> None:
    run = run_tool(
        tmp_path,
        xlsx_bytes(SAMPLE_ROWS, extra_entries={"../evil.xml": b"<x/>"}),
        media_type=XLSX_MEDIA_TYPE,
        file_name="input.xlsx",
    )
    assert_rejected(run, "archive_path_traversal")


def test_a_numeric_date_cell_is_refused_instead_of_guessed(tmp_path) -> None:
    rows = [
        ["order_id", "date", "region", "amount"],
        ["A1", ("number", "46023"), "x", "1.00"],
    ]
    run = run_tool(
        tmp_path,
        xlsx_bytes(rows),
        media_type=XLSX_MEDIA_TYPE,
        file_name="input.xlsx",
    )
    assert_rejected(run, "invalid_cell_type", row=2, column="date")


def test_an_unknown_xlsx_cell_type_is_refused(tmp_path) -> None:
    sheet = sheet_xml(SAMPLE_ROWS).replace(
        '<c r="D2" t="inlineStr"><is><t xml:space="preserve">120.50</t></is></c>',
        '<c r="D2" t="b"><v>1</v></c>',
    )
    run = run_tool(
        tmp_path,
        xlsx_bytes([], sheet=sheet),
        media_type=XLSX_MEDIA_TYPE,
        file_name="input.xlsx",
    )
    assert_rejected(run, "invalid_cell_type", row=2, column="amount")


def test_a_sheet_target_path_traversal_is_rejected(tmp_path) -> None:
    rels = (
        '<?xml version="1.0"?>'
        f'<Relationships xmlns="{PKG_REL_NS}">'
        f'<Relationship Id="rId1" Type="{REL_NS}/worksheet" Target="../../etc/passwd"/>'
        "</Relationships>"
    )
    run = run_tool(
        tmp_path,
        xlsx_bytes(SAMPLE_ROWS, rels=rels),
        media_type=XLSX_MEDIA_TYPE,
        file_name="input.xlsx",
    )
    assert_rejected(run, "invalid_xlsx")


# ---------------------------------------------------------------------------
# the tool itself is safe by construction
# ---------------------------------------------------------------------------
def test_the_tool_source_uses_no_network_subprocess_or_environment_reads() -> None:
    source = TOOL.read_text(encoding="utf-8")
    for forbidden in (
        "import socket",
        "import urllib",
        "import http",
        "import requests",
        "import subprocess",
        "os.environ",
        "os.getenv",
        "socket.",
        "urllib.",
        "subprocess.",
        "eval(",
        "exec(",
    ):
        assert forbidden not in source, f"sales_stats.py must not contain {forbidden!r}"
    assert "Decimal" in source
    assert "float(" not in source, "money must never be converted with float()"


def test_the_default_paths_match_the_workflow_spec_protocol() -> None:
    source = TOOL.read_text(encoding="utf-8")
    assert "/workspace/input.json" in source
    assert "/workspace/output.json" in source
    assert "/workspace/inputs" in source
    assert "/workspace/artifacts" in source


def test_region_text_with_csv_metacharacters_stays_one_two_column_row(tmp_path) -> None:
    """Legal region names must round-trip through summary.csv.

    A comma, a quote or a newline in a region is data, not structure: the export
    must use a real CSV writer. The summary.json value stays the raw region text.
    """
    payload = (
        "order_id,date,region,amount\n"
        'A1,2026-01-01,"East, Zone",10.00\n'
        'A2,2026-01-02,"He said ""x""",20.00\n'
        'A3,2026-01-03,"line\nbreak",30.00\n'
    ).encode("utf-8")
    run = run_tool(tmp_path, payload)
    assert run.returncode == 0, (run.stdout, run.stderr)

    data = run.data()
    assert data["total"] == "60.00"
    assert data["order_count"] == 3
    assert data["regions"] == {
        "East, Zone": "10.00",
        'He said "x"': "20.00",
        "line\nbreak": "30.00",
    }

    parsed = list(csv.reader(io.StringIO(run.artifact("summary.csv").decode("utf-8"), newline="")))
    assert parsed[0] == ["region", "total"]
    assert all(len(row) == 2 for row in parsed), parsed
    by_region = {row[0]: row[1] for row in parsed[1:]}
    assert by_region == data["regions"]
