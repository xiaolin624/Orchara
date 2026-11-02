#!/usr/bin/env python3
"""The trusted preset "sales statistics" tool (F05/T030, Python 3.12, stdlib).

Protocol (WORKFLOW-SPEC §5):

    python sales_stats.py \
        --input  /workspace/input.json \
        --output /workspace/output.json \
        --inputs-dir  /workspace/inputs \
        --artifacts-dir /workspace/artifacts

``input.json`` is written by the platform and names the one authorized input
file inside ``--inputs-dir``::

    {"tool": "sales_stats",
     "file": "input.csv",
     "artifact": {"artifact_id": "...", "media_type": "text/csv",
                  "size_bytes": 114, "sha256": "..."}}

On success ``output.json`` is exactly::

    {"data": {"total": "250.00",
              "regions": {"东区": "200.00", "西区": "50.00"},
              "order_count": 3},
     "artifacts": ["summary.json", "summary.csv"]}

and the two artifacts are written **only** inside ``--artifacts-dir``. On a
user-input rejection the process exits 3 and writes::

    {"error": {"code": "missing_column", "message": "...", "fix": "...",
               "location": {"row": 1, "column": "date"}}}

The platform never parses stdout/stderr for output, so a rejection is a
structured document, not a log line. Both shapes are fixed: the caller (the Go
executor) rejects unknown top-level keys.

Safety boundaries this file is responsible for:

* money is ``decimal.Decimal``-only; money is never converted to binary
  floating point;
* the tool reads environment variables never and opens no socket (verified by
  the unit tests, not just by convention);
* only the one input file named in ``input.json`` (basename inside
  ``--inputs-dir``) and the two fixed artifact names can be read/written;
* CSV/XLSX formulas are rejected, never evaluated; XLSX macros, DTD/entities,
  zip traversal and archive/row/cell/worksheet caps are rejected with stable
  safe codes;
* a rejected run writes no summary artifacts.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import stat
import sys
import traceback
import zipfile
from decimal import Decimal
from xml.etree import ElementTree

TOOL_NAME = "sales_stats"
TOOL_VERSION = "1"

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_REJECTED = 3

# Defaults from WORKFLOW-SPEC §5. The Go executor always passes all four
# explicitly; the constants exist so the file is runnable standalone.
DEFAULT_INPUT = "/workspace/input.json"
DEFAULT_OUTPUT = "/workspace/output.json"
DEFAULT_INPUTS_DIR = "/workspace/inputs"
DEFAULT_ARTIFACTS_DIR = "/workspace/artifacts"

CSV_MEDIA_TYPE = "text/csv"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUPPORTED_MEDIA_TYPES = (CSV_MEDIA_TYPE, XLSX_MEDIA_TYPE)

# Platform caps. The executor enforces the same numbers on its side; the tool
# re-checks them so a direct invocation is not a way around them.
MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_JSON_BYTES = 1024 * 1024
MAX_ROWS = 10_000
# A legal maximum sheet has MAX_ROWS data rows plus the header row. The cell cap
# must not reject that sheet, so it is exactly (MAX_ROWS + 1) * 4.
MAX_CELLS = (MAX_ROWS + 1) * 4
MAX_FIELD_CHARS = 4_096
MAX_ORDER_ID_CHARS = 200
MAX_REGION_CHARS = 200
MAX_AMOUNT_CHARS = 32

# XLSX archive caps (the same scale T028 admission uses).
MAX_ARCHIVE_ENTRIES = 512
MAX_ARCHIVE_UNCOMPRESSED = 64 * 1024 * 1024
MAX_ARCHIVE_ENTRY = 32 * 1024 * 1024
MAX_ARCHIVE_RATIO = 100.0
MAX_WORKSHEETS = 64
MAX_SHARED_STRINGS = 40_000

EXPECTED_COLUMNS = ("order_id", "date", "region", "amount")
DECIMAL_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?$")
ISO_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

FIXES = {
    "missing_column": "请使用表头 order_id,date,region,amount（共四列，按此顺序）。",
    "extra_column": "请删除多余列，只保留 order_id,date,region,amount 四列。",
    "column_order_mismatch": "列顺序必须是 order_id,date,region,amount。",
    "duplicate_order_id": "每个 order_id 只能出现一次，请合并或删除重复订单。",
    "negative_amount": "金额不能为负数，请修正该行 amount。",
    "invalid_amount": "amount 必须是十进制金额（最多两位小数，例如 120.50），不能使用科学计数法或千分位。",
    "empty_region": "region 不能为空，请填写订单所属地区。",
    "invalid_date": "date 必须是 ISO 日期 YYYY-MM-DD（例如 2026-01-01）。",
    "formula_not_supported": "首版不执行公式：请把公式单元格替换为静态文本或数值。",
    "invalid_row": "每行必须恰好包含 order_id、date、region、amount 四个单元格。",
    "empty_input": "文件至少需要一行订单数据（表头之后不能为空）。",
    "invalid_encoding": "CSV 必须是 UTF-8 文本，无法解码的内容不会被猜测。",
    "unsupported_media_type": "预置工具只接受 text/csv 或 XLSX 文件。",
    "input_file_missing": "授权输入文件不存在，请重新上传附件。",
    "input_path_invalid": "输入文件路径不在授权目录内，已被拒绝。",
    "input_too_large": "输入文件超过 20 MiB 上限。",
    "invalid_input": "平台 input.json 结构不符合工具协议。",
    "invalid_csv": "CSV 格式无法解析（引号/换行不完整）。",
    "field_too_large": "单个单元格超过 4096 字符上限。",
    "empty_order_id": "order_id 不能为空。",
    "invalid_xlsx": "XLSX 结构无效或缺少工作簿/第一个工作表。",
    "macro_present": "XLSX 含宏（vbaProject.bin），首版拒绝。",
    "dtd_not_supported": "XLSX/XML 含 DTD 或实体声明，首版拒绝。",
    "archive_limit_exceeded": "XLSX 压缩包超过条目数/解压总量/压缩比上限。",
    "archive_path_traversal": "XLSX 压缩包含路径穿越条目。",
    "worksheet_limit_exceeded": "XLSX 工作表数量超过上限。",
    "row_limit_exceeded": "数据行数超过 10000 行上限。",
    "cell_limit_exceeded": "单元格数量超过上限（10000 行 × 四列 + 表头）。",
    "invalid_cell_type": "该列的单元格类型不受支持（日期/订单号/地区必须是文本，金额可以是文本或数字）。",
    "artifact_path_invalid": "产物路径必须是 artifacts 目录内的普通文件名。",
}


class ToolError(Exception):
    """A safe, user-actionable rejection: code + message + optional location."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        row: int | None = None,
        column: str | None = None,
        cell: str | None = None,
        fix: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.row = row
        self.column = column
        self.cell = cell
        self.fix = fix or FIXES.get(code, "请修正输入后重试。")

    def document(self) -> dict:
        location = {}
        if self.row is not None:
            location["row"] = self.row
        if self.column is not None:
            location["column"] = self.column
        if self.cell is not None:
            location["cell"] = self.cell
        error = {"code": self.code, "message": self.message, "fix": self.fix}
        if location:
            error["location"] = location
        return {"error": error}


# ---------------------------------------------------------------------------
# input document and input file
# ---------------------------------------------------------------------------
def _read_json(path: str, *, limit: int, code: str) -> object:
    try:
        with open(path, "rb") as handle:
            payload = handle.read(limit + 1)
    except OSError:
        raise ToolError(code, "无法读取平台写入的 JSON 文件。")
    if len(payload) > limit:
        raise ToolError(code, "平台 JSON 文件超过大小上限。")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ToolError(code, "平台 JSON 文件不是合法 UTF-8 JSON。")


def _require_object(value: object, code: str, what: str) -> dict:
    if not isinstance(value, dict):
        raise ToolError(code, f"{what} 必须是 JSON 对象。")
    return value


def _validate_input_document(document: object) -> dict:
    document = _require_object(document, "invalid_input", "input.json")
    if set(document) - {"tool", "file", "artifact"}:
        raise ToolError("invalid_input", "input.json 含未知字段。")
    if document.get("tool") != TOOL_NAME:
        raise ToolError("invalid_input", "input.json 的 tool 不是 sales_stats。")
    file_name = document.get("file")
    if not isinstance(file_name, str) or not file_name:
        raise ToolError("input_path_invalid", "input.json 缺少授权文件名。")
    if (
        file_name in (".", "..")
        or os.path.basename(file_name) != file_name
        or "/" in file_name
        or "\\" in file_name
        or "\x00" in file_name
    ):
        raise ToolError("input_path_invalid", "授权文件名必须是不含路径的普通文件名。")
    artifact = _require_object(document.get("artifact"), "invalid_input", "artifact")
    media_type = artifact.get("media_type")
    if media_type not in SUPPORTED_MEDIA_TYPES:
        raise ToolError(
            "unsupported_media_type",
            "预置销售统计工具只接受 text/csv 或 XLSX 输入。",
        )
    return {"file": file_name, "media_type": media_type, "artifact": artifact}


def _read_input_file(inputs_dir: str, file_name: str) -> bytes:
    root = os.path.realpath(inputs_dir)
    target = os.path.realpath(os.path.join(root, file_name))
    if os.path.dirname(target) != root:
        raise ToolError("input_path_invalid", "授权输入文件不在 inputs 目录内。")
    if os.path.islink(os.path.join(root, file_name)):
        raise ToolError("input_path_invalid", "授权输入文件不能是符号链接。")
    try:
        info = os.lstat(target)
    except OSError:
        raise ToolError("input_file_missing", "授权输入文件不存在。")
    if not stat.S_ISREG(info.st_mode):
        raise ToolError("input_path_invalid", "授权输入文件必须是普通文件。")
    if info.st_size > MAX_INPUT_BYTES:
        raise ToolError("input_too_large", "输入文件超过 20 MiB 上限。")
    try:
        with open(target, "rb") as handle:
            payload = handle.read(MAX_INPUT_BYTES + 1)
    except OSError:
        raise ToolError("input_file_missing", "授权输入文件无法读取。")
    if len(payload) > MAX_INPUT_BYTES:
        raise ToolError("input_too_large", "输入文件超过 20 MiB 上限。")
    return payload


# ---------------------------------------------------------------------------
# shared row validation
# ---------------------------------------------------------------------------
def _cell_value(
    value: str,
    *,
    row: int,
    column: str,
    cell: str | None = None,
) -> str:
    """Return one cell after the formula/leading-prefix safety checks."""
    stripped = value.lstrip()
    if stripped.startswith(("=", "+", "@")):
        raise ToolError(
            "formula_not_supported",
            f"第 {row} 行 {column} 列以公式字符开头，首版不执行公式。",
            row=row,
            column=column,
            cell=cell,
        )
    if stripped.startswith("-") and column != "amount":
        raise ToolError(
            "formula_not_supported",
            f"第 {row} 行 {column} 列以 '-' 开头，按公式注入风险拒绝。",
            row=row,
            column=column,
            cell=cell,
        )
    if len(value) > MAX_FIELD_CHARS:
        raise ToolError(
            "field_too_large",
            f"第 {row} 行 {column} 列超过 {MAX_FIELD_CHARS} 字符上限。",
            row=row,
            column=column,
            cell=cell,
        )
    return value


def _parse_order_id(value: str, *, row: int, cell: str | None = None) -> str:
    if value == "":
        raise ToolError("empty_order_id", f"第 {row} 行 order_id 为空。", row=row, column="order_id", cell=cell)
    if len(value) > MAX_ORDER_ID_CHARS:
        raise ToolError(
            "field_too_large",
            f"第 {row} 行 order_id 超过 {MAX_ORDER_ID_CHARS} 字符上限。",
            row=row,
            column="order_id",
            cell=cell,
        )
    return value


def _parse_date(value: str, *, row: int, cell: str | None = None) -> str:
    if not ISO_DATE_PATTERN.match(value):
        raise ToolError(
            "invalid_date",
            f"第 {row} 行 date 不是 ISO 日期 YYYY-MM-DD。",
            row=row,
            column="date",
            cell=cell,
        )
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise ToolError(
            "invalid_date",
            f"第 {row} 行 date 不是真实存在的日期。",
            row=row,
            column="date",
            cell=cell,
        )
    return value


def _parse_region(value: str, *, row: int, cell: str | None = None) -> str:
    if not value.strip():
        raise ToolError("empty_region", f"第 {row} 行 region 为空。", row=row, column="region", cell=cell)
    if len(value) > MAX_REGION_CHARS:
        raise ToolError(
            "field_too_large",
            f"第 {row} 行 region 超过 {MAX_REGION_CHARS} 字符上限。",
            row=row,
            column="region",
            cell=cell,
        )
    return value


def _parse_amount(value: str, *, row: int, cell: str | None = None) -> Decimal:
    if value == "" or len(value) > MAX_AMOUNT_CHARS or not DECIMAL_PATTERN.match(value):
        raise ToolError(
            "invalid_amount",
            f"第 {row} 行 amount 不是合法的十进制金额字符串。",
            row=row,
            column="amount",
            cell=cell,
        )
    amount = Decimal(value)
    if amount < 0:
        raise ToolError(
            "negative_amount",
            f"第 {row} 行 amount 为负数。",
            row=row,
            column="amount",
            cell=cell,
        )
    return amount


def _validate_header(header: list[str]) -> None:
    if len(header) < len(EXPECTED_COLUMNS):
        missing = next(
            (name for name in EXPECTED_COLUMNS if name not in header),
            EXPECTED_COLUMNS[len(header)],
        )
        raise ToolError(
            "missing_column",
            f"缺少必需列: {missing}",
            row=1,
            column=missing,
        )
    if len(header) > len(EXPECTED_COLUMNS):
        extra = header[len(EXPECTED_COLUMNS)]
        raise ToolError(
            "extra_column",
            f"存在多余列: {extra or '空表头'}",
            row=1,
            column=extra or f"column-{len(EXPECTED_COLUMNS) + 1}",
        )
    if tuple(header) != EXPECTED_COLUMNS:
        raise ToolError(
            "column_order_mismatch",
            "列顺序必须是 order_id,date,region,amount。",
            row=1,
        )


def _build_summary(orders: list[tuple[str, str, str, Decimal]]) -> dict:
    total = Decimal("0.00")
    regions: dict[str, Decimal] = {}
    seen: set[str] = set()
    for order_id, _date, region, amount in orders:
        if order_id in seen:
            # Callers validate uniqueness with a row number; this is a last
            # line of defence and must never be reachable in normal flow.
            raise ToolError("duplicate_order_id", "订单号重复。", column="order_id")
        seen.add(order_id)
        total += amount
        regions[region] = regions.get(region, Decimal("0.00")) + amount
    if not orders:
        raise ToolError("empty_input", "文件至少需要一行订单数据。")
    return {
        "total": str(total.quantize(Decimal("0.01"))),
        "regions": {name: str(value.quantize(Decimal("0.01"))) for name, value in regions.items()},
        "order_count": len(orders),
    }


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def _read_csv(payload: bytes) -> list[tuple[str, str, str, Decimal]]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ToolError("invalid_encoding", "CSV 不是合法 UTF-8 文本。")
    if "\x00" in text:
        raise ToolError("invalid_encoding", "CSV 含 NUL 字节，不是合法文本。")
    if len(text) > MAX_INPUT_BYTES:
        raise ToolError("input_too_large", "CSV 超过 20 MiB 上限。")

    csv.field_size_limit(MAX_FIELD_CHARS)
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration:
        raise ToolError("empty_input", "CSV 为空。")
    except csv.Error:
        raise ToolError("invalid_csv", "CSV 表头无法解析。")
    for index, name in enumerate(header):
        _cell_value(name, row=1, column=EXPECTED_COLUMNS[index] if index < 4 else f"column-{index + 1}")
    _validate_header(header)

    orders: list[tuple[str, str, str, Decimal]] = []
    seen: set[str] = set()
    try:
        for row in reader:
            if not row or all(cell == "" for cell in row):
                continue
            row_number = reader.line_num
            if len(row) != 4:
                raise ToolError(
                    "invalid_row",
                    f"第 {row_number} 行必须恰好包含四个单元格。",
                    row=row_number,
                )
            if len(orders) >= MAX_ROWS:
                raise ToolError("row_limit_exceeded", "数据行数超过 10000 行上限。")
            order_id = _parse_order_id(_cell_value(row[0], row=row_number, column="order_id"), row=row_number)
            date = _parse_date(_cell_value(row[1], row=row_number, column="date"), row=row_number)
            region = _parse_region(_cell_value(row[2], row=row_number, column="region"), row=row_number)
            amount = _parse_amount(_cell_value(row[3], row=row_number, column="amount"), row=row_number)
            if order_id in seen:
                raise ToolError(
                    "duplicate_order_id",
                    f"第 {row_number} 行 order_id 重复。",
                    row=row_number,
                    column="order_id",
                )
            seen.add(order_id)
            orders.append((order_id, date, region, amount))
    except csv.Error as error:
        message = str(error).lower()
        if "field larger" in message:
            raise ToolError("field_too_large", "CSV 单元格超过 4096 字符上限。")
        raise ToolError("invalid_csv", "CSV 格式无法解析。")
    return orders


# ---------------------------------------------------------------------------
# XLSX (zip + XML, no third-party libraries)
# ---------------------------------------------------------------------------
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_element(payload: bytes, *, what: str):
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        raise ToolError("invalid_xlsx", f"{what} 不是合法 XML。")


def _xml_bytes(archive: zipfile.ZipFile, name: str, *, what: str) -> bytes:
    try:
        payload = archive.read(name)
    except (KeyError, OSError, RuntimeError):
        raise ToolError("invalid_xlsx", f"缺少 {what}。")
    lowered = payload.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ToolError("dtd_not_supported", f"{what} 含 DTD/实体声明。")
    return payload


def _open_xlsx(payload: bytes) -> zipfile.ZipFile:
    if len(payload) > MAX_INPUT_BYTES:
        raise ToolError("input_too_large", "XLSX 超过 20 MiB 上限。")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        raise ToolError("invalid_xlsx", "XLSX 不是合法 zip 压缩包。")
    infos = archive.infolist()
    names: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        raw_name = info.filename
        if "\x00" in raw_name:
            raise ToolError("archive_path_traversal", "XLSX 压缩包含非法条目名。")
        name = raw_name.replace("\\", "/")
        if name.startswith("/") or any(segment == ".." for segment in name.split("/")):
            raise ToolError("archive_path_traversal", "XLSX 压缩包含路径穿越条目。")
        if os.path.basename(name).lower() == "vbaproject.bin":
            raise ToolError("macro_present", "XLSX 含宏（vbaProject.bin）。")
        if info.flag_bits & 0x1:
            raise ToolError("invalid_xlsx", "XLSX 含加密条目，首版拒绝。")
        names[name] = info
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise ToolError("archive_limit_exceeded", "XLSX 条目数超过 512 上限。")
    uncompressed = sum(info.file_size for info in infos)
    if uncompressed > MAX_ARCHIVE_UNCOMPRESSED:
        raise ToolError("archive_limit_exceeded", "XLSX 解压总量超过 64 MiB 上限。")
    for info in infos:
        if info.file_size > MAX_ARCHIVE_ENTRY:
            raise ToolError("archive_limit_exceeded", "XLSX 单个条目超过 32 MiB 上限。")
        if info.file_size > 0 and info.compress_size == 0:
            raise ToolError("archive_limit_exceeded", "XLSX 压缩比异常。")
        if info.compress_size > 0 and info.file_size / info.compress_size > MAX_ARCHIVE_RATIO:
            raise ToolError("archive_limit_exceeded", "XLSX 压缩比超过 100 上限。")
    # Reject DTD/entities in every XML part before any payload is parsed. This
    # is what makes the XML parser a data reader, not an entity expander.
    for name in names:
        if name.lower().endswith((".xml", ".rels")):
            _xml_bytes(archive, name, what=name)
    return archive


def _worksheet_target(archive: zipfile.ZipFile, names: dict[str, zipfile.ZipInfo]) -> str:
    if "xl/workbook.xml" not in names:
        raise ToolError("invalid_xlsx", "XLSX 缺少 xl/workbook.xml。")
    workbook = _xml_element(
        _xml_bytes(archive, "xl/workbook.xml", what="xl/workbook.xml"),
        what="xl/workbook.xml",
    )
    sheet_elements = [
        element for element in workbook.iter() if _local(element.tag) == "sheet"
    ]
    if not sheet_elements:
        raise ToolError("invalid_xlsx", "XLSX 工作簿没有任何工作表。")
    if len(sheet_elements) > MAX_WORKSHEETS:
        raise ToolError("worksheet_limit_exceeded", "XLSX 工作表数量超过 64 上限。")
    first = sheet_elements[0]
    relationship_id = None
    for key, value in first.attrib.items():
        if _local(key) == "id" and (key.startswith(REL_NS) or "relationships" in key):
            relationship_id = value
            break
    if not relationship_id:
        raise ToolError("invalid_xlsx", "XLSX 第一个工作表缺少关系 ID。")
    rels_name = "xl/_rels/workbook.xml.rels"
    if rels_name not in names:
        raise ToolError("invalid_xlsx", "XLSX 缺少工作簿关系文件。")
    rels = _xml_element(_xml_bytes(archive, rels_name, what=rels_name), what=rels_name)
    target = None
    for relationship in rels.iter():
        if _local(relationship.tag) != "Relationship":
            continue
        if relationship.get("Id") == relationship_id:
            target = relationship.get("Target")
            break
    if not target:
        raise ToolError("invalid_xlsx", "XLSX 第一个工作表的关系目标不存在。")
    target = target.replace("\\", "/")
    if target.startswith("/"):
        target = target[1:]
    if not target.startswith("xl/"):
        target = "xl/" + target
    if ".." in target.split("/") or target not in names:
        raise ToolError("invalid_xlsx", "XLSX 第一个工作表目标无效。")
    return target


def _shared_strings(archive: zipfile.ZipFile, names: dict[str, zipfile.ZipInfo]) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in names:
        return []
    root = _xml_element(_xml_bytes(archive, name, what=name), what=name)
    values: list[str] = []
    for element in root.iter():
        if _local(element.tag) != "si":
            continue
        if len(values) >= MAX_SHARED_STRINGS:
            raise ToolError("cell_limit_exceeded", "XLSX 共享字符串数量超过上限。")
        text = "".join(
            child.text or "" for child in element.iter() if _local(child.tag) == "t"
        )
        if len(text) > MAX_FIELD_CHARS:
            raise ToolError("field_too_large", "XLSX 共享字符串超过 4096 字符上限。")
        values.append(text)
    return values


def _cell_text(
    cell,
    shared: list[str],
    *,
    row: int,
    column: str,
) -> tuple[str, str]:
    """Return (text, cell_reference) for one worksheet cell."""
    reference = cell.get("r") or ""
    for child in cell:
        if _local(child.tag) == "f":
            raise ToolError(
                "formula_not_supported",
                f"第 {row} 行 {column} 列是公式单元格，首版不执行公式。",
                row=row,
                column=column,
                cell=reference or None,
            )
    cell_type = cell.get("t", "n")
    value_element = None
    inline_text = None
    for child in cell:
        if _local(child.tag) == "v":
            value_element = child
        elif _local(child.tag) == "is":
            inline_text = child
    raw = (value_element.text or "") if value_element is not None else ""
    if cell_type in ("n", "") and raw == "" and inline_text is None:
        # An empty cell (often just formatting residue) carries no value; it is
        # reported as an empty string so the column-level rules decide whether
        # that is acceptable.
        return "", reference
    if cell_type == "s":
        if not raw.isdigit():
            raise ToolError("invalid_xlsx", f"第 {row} 行 {column} 列共享字符串引用无效。")
        index = int(raw)
        if index >= len(shared):
            raise ToolError("invalid_xlsx", f"第 {row} 行 {column} 列共享字符串引用越界。")
        text = shared[index]
    elif cell_type == "inlineStr":
        if inline_text is None:
            text = ""
        else:
            text = "".join(
                child.text or "" for child in inline_text.iter() if _local(child.tag) == "t"
            )
    elif cell_type in ("str", "n", "e", "b", ""):
        text = raw
    else:
        raise ToolError(
            "invalid_cell_type",
            f"第 {row} 行 {column} 列的单元格类型 {cell_type} 不受支持。",
            row=row,
            column=column,
            cell=reference or None,
        )
    if cell_type in ("e", "b"):
        raise ToolError(
            "invalid_cell_type",
            f"第 {row} 行 {column} 列是错误/布尔单元格，首版拒绝。",
            row=row,
            column=column,
            cell=reference or None,
        )
    if cell_type not in ("s", "inlineStr", "str") and column != "amount":
        raise ToolError(
            "invalid_cell_type",
            f"第 {row} 行 {column} 列必须是文本单元格。",
            row=row,
            column=column,
            cell=reference or None,
        )
    if len(text) > MAX_FIELD_CHARS:
        raise ToolError(
            "field_too_large",
            f"第 {row} 行 {column} 列超过 {MAX_FIELD_CHARS} 字符上限。",
            row=row,
            column=column,
            cell=reference or None,
        )
    return text, reference


def _column_index(reference: str) -> int | None:
    letters = "".join(char for char in reference if char.isalpha()).upper()
    if not letters:
        return None
    index = 0
    for char in letters:
        if not ("A" <= char <= "Z"):
            return None
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _read_xlsx(payload: bytes) -> list[tuple[str, str, str, Decimal]]:
    archive = _open_xlsx(payload)
    names = {info.filename.replace("\\", "/"): info for info in archive.infolist()}
    target = _worksheet_target(archive, names)
    worksheet = _xml_element(_xml_bytes(archive, target, what=target), what=target)
    shared = _shared_strings(archive, names)

    row_elements = [
        element
        for element in worksheet.iter()
        if _local(element.tag) == "row"
    ]
    if len(row_elements) > MAX_ROWS + 1:
        raise ToolError("row_limit_exceeded", "数据行数超过 10000 行上限。")
    cell_total = 0
    for row_element in row_elements:
        cell_total += sum(1 for child in row_element if _local(child.tag) == "c")
    if cell_total > MAX_CELLS:
        raise ToolError("cell_limit_exceeded", "单元格数量超过 10000 行 × 四列 + 表头的上限。")

    if not row_elements:
        raise ToolError("empty_input", "XLSX 第一个工作表没有数据。")

    def row_values(row_element, row_number: int) -> tuple[dict[int, str], int]:
        values: dict[int, str] = {}
        maximum = -1
        position = 0
        for cell in row_element:
            if _local(cell.tag) != "c":
                continue
            reference = cell.get("r") or ""
            index = _column_index(reference) if reference else None
            if index is None:
                index = position
            position = index + 1
            maximum = max(maximum, index)
            column_name = (
                EXPECTED_COLUMNS[index] if index < len(EXPECTED_COLUMNS) else f"column-{index + 1}"
            )
            text, _reference = _cell_text(
                cell, shared, row=row_number, column=column_name
            )
            values[index] = text
        return values, maximum

    header_element = row_elements[0]
    header_values, header_maximum = row_values(header_element, 1)
    if header_maximum < 0:
        raise ToolError("empty_input", "XLSX 第一行没有表头。")
    header = [header_values.get(index, "") for index in range(header_maximum + 1)]
    while header and header[-1] == "":
        header.pop()
    for index, name in enumerate(header):
        if index < len(EXPECTED_COLUMNS):
            _cell_value(name, row=1, column=EXPECTED_COLUMNS[index])
    _validate_header(header)

    orders: list[tuple[str, str, str, Decimal]] = []
    seen: set[str] = set()
    for offset, row_element in enumerate(row_elements[1:], start=2):
        declared = row_element.get("r")
        row_number = int(declared) if declared and declared.isdigit() else offset
        values, maximum = row_values(row_element, row_number)
        if not values:
            continue
        if maximum > 3 and any(values.get(index, "") for index in range(4, maximum + 1)):
            raise ToolError(
                "invalid_row",
                f"第 {row_number} 行包含超过四个单元格。",
                row=row_number,
            )
        cells = [values.get(index, "") for index in range(4)]
        if all(cell == "" for cell in cells):
            continue
        if len(orders) >= MAX_ROWS:
            raise ToolError("row_limit_exceeded", "数据行数超过 10000 行上限。")
        order_id = _parse_order_id(_cell_value(cells[0], row=row_number, column="order_id"), row=row_number)
        date = _parse_date(_cell_value(cells[1], row=row_number, column="date"), row=row_number)
        region = _parse_region(_cell_value(cells[2], row=row_number, column="region"), row=row_number)
        amount = _parse_amount(_cell_value(cells[3], row=row_number, column="amount"), row=row_number)
        if order_id in seen:
            raise ToolError(
                "duplicate_order_id",
                f"第 {row_number} 行 order_id 重复。",
                row=row_number,
                column="order_id",
            )
        seen.add(order_id)
        orders.append((order_id, date, region, amount))
    return orders


# ---------------------------------------------------------------------------
# artifacts and output
# ---------------------------------------------------------------------------
def _artifact_path(artifacts_dir: str, name: str) -> str:
    if not name or name in (".", "..") or os.path.basename(name) != name:
        raise ToolError("artifact_path_invalid", "产物名必须是普通文件名。")
    if "\x00" in name:
        raise ToolError("artifact_path_invalid", "产物名不能含 NUL。")
    root = os.path.realpath(artifacts_dir)
    target = os.path.realpath(os.path.join(root, name))
    if os.path.dirname(target) != root:
        raise ToolError("artifact_path_invalid", "产物路径必须位于 artifacts 目录内。")
    if os.path.islink(target):
        raise ToolError("artifact_path_invalid", "产物不能是符号链接。")
    return target


def _write_artifact(artifacts_dir: str, name: str, payload: bytes) -> None:
    target = _artifact_path(artifacts_dir, name)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "xb") as handle:
            handle.write(payload)
    except FileExistsError:
        raise ToolError("artifact_path_invalid", "产物已存在，拒绝覆盖。")
    except OSError:
        raise OSError("cannot write a tool artifact")


def _write_json(path: str, document: dict) -> None:
    payload = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    with open(path, "wb") as handle:
        handle.write(payload)


def _write_outputs(output_path: str, artifacts_dir: str, summary: dict) -> None:
    summary_json = json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    region_lines = ["region,total"]
    for region, total in summary["regions"].items():
        region_lines.append(f"{region},{total}")
    summary_csv = ("\n".join(region_lines) + "\n").encode("utf-8")
    _write_artifact(artifacts_dir, "summary.json", summary_json)
    _write_artifact(artifacts_dir, "summary.csv", summary_csv)
    _write_json(
        output_path,
        {
            "data": summary,
            "artifacts": ["summary.json", "summary.csv"],
        },
    )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sales_stats.py",
        description="Orchara trusted preset sales statistics tool",
    )
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--inputs-dir", default=DEFAULT_INPUTS_DIR)
    parser.add_argument("--artifacts-dir", default=DEFAULT_ARTIFACTS_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        document = _validate_input_document(
            _read_json(args.input, limit=MAX_OUTPUT_JSON_BYTES, code="invalid_input")
        )
        payload = _read_input_file(args.inputs_dir, document["file"])
        if document["media_type"] == CSV_MEDIA_TYPE:
            orders = _read_csv(payload)
        else:
            orders = _read_xlsx(payload)
        summary = _build_summary(orders)
        _write_outputs(args.output, args.artifacts_dir, summary)
        return EXIT_OK
    except ToolError as error:
        try:
            _write_json(args.output, error.document())
        except OSError:
            return EXIT_INTERNAL
        print(f"{TOOL_NAME}: rejected {error.code}", file=sys.stderr)
        return EXIT_REJECTED
    except Exception:  # noqa: BLE001 - never leak a stack as protocol output
        traceback.print_exc()
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
