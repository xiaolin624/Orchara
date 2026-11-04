"""F13A/T056 independent Decimal truth for the sales case fixtures.

This module is the *reference calculator*: it reads a committed CSV/XLSX fixture
with the standard library only (``csv``, ``zipfile``, ``xml.etree.ElementTree``)
and adds the money with ``decimal.Decimal``. It is deliberately independent of
the product's preset sales tool: it imports nothing from ``platform_worker`` and
runs in a separate process in the tests, so "the expectation" is never produced
by the thing under test.

Two uses:

* ``summarize`` derives the expected summary for a happy-path/boundary fixture;
  the values are frozen in ``tests/f13a/datasets/expected_values.json`` and the
  test asserts the calculator reproduces them (a drift guard on this module);
* for a rejected fixture the reference raises ``FixtureRejected(code)`` with the
  same structural rule the case expects, giving an independent reading of *why*
  the fixture is bad (the case's ``expected_failure`` is checked against the
  product's real run error code, never against this module).

Money never touches a binary float: amounts are parsed straight into ``Decimal``
and rendered with exactly two decimal places.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import zipfile
from decimal import Decimal
from xml.etree import ElementTree

CSV_MEDIA = "text/csv"
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

EXPECTED_COLUMNS = ("order_id", "date", "region", "amount")
MONEY_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?$")
ISO_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
MAX_ROWS = 10_000

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class FixtureRejected(Exception):
    """The fixture violates a documented input rule; ``code`` is the stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# structural validation (independent re-statement of the case's contract)
# ---------------------------------------------------------------------------
def _cell(value: str, column: str) -> str:
    stripped = value.lstrip()
    if stripped.startswith(("=", "+", "@")):
        raise FixtureRejected("formula_not_supported")
    if stripped.startswith("-") and column != "amount":
        raise FixtureRejected("formula_not_supported")
    if len(value) > 4_096:
        raise FixtureRejected("field_too_large")
    return value


def _amount(value: str) -> Decimal:
    if value == "" or not MONEY_PATTERN.match(value):
        raise FixtureRejected("invalid_amount")
    amount = Decimal(value)
    if amount < 0:
        raise FixtureRejected("negative_amount")
    return amount


def _validate_header(header: list[str]) -> None:
    if len(header) < len(EXPECTED_COLUMNS):
        raise FixtureRejected("missing_column")
    if len(header) > len(EXPECTED_COLUMNS):
        raise FixtureRejected("extra_column")
    if tuple(header) != EXPECTED_COLUMNS:
        raise FixtureRejected("column_order_mismatch")


def _row(order_id: str, date: str, region: str, amount: str, seen: set[str]) -> tuple:
    order_id = _cell(order_id, "order_id")
    date = _cell(date, "date")
    region = _cell(region, "region")
    amount_value = _amount(_cell(amount, "amount"))
    if order_id == "":
        raise FixtureRejected("empty_order_id")
    if not ISO_DATE_PATTERN.match(date):
        raise FixtureRejected("invalid_date")
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        raise FixtureRejected("invalid_date")
    if not region.strip():
        raise FixtureRejected("empty_region")
    if order_id in seen:
        raise FixtureRejected("duplicate_order_id")
    seen.add(order_id)
    return order_id, date, region, amount_value


def _summary(orders: list[tuple[str, str, str, Decimal]]) -> dict:
    if not orders:
        raise FixtureRejected("empty_input")
    total = Decimal("0.00")
    regions: dict[str, Decimal] = {}
    for _order_id, _date, region, amount in orders:
        total += amount
        regions[region] = regions.get(region, Decimal("0.00")) + amount
    return {
        "total": str(total.quantize(Decimal("0.01"))),
        "regions": {
            region: str(value.quantize(Decimal("0.01"))) for region, value in regions.items()
        },
        "order_count": len(orders),
    }


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def summarize_csv(payload: bytes) -> dict:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FixtureRejected("invalid_encoding")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration:
        raise FixtureRejected("empty_input")
    _validate_header(header)
    orders: list[tuple[str, str, str, Decimal]] = []
    seen: set[str] = set()
    try:
        for row in reader:
            if not row or all(cell == "" for cell in row):
                continue
            if len(row) != len(EXPECTED_COLUMNS):
                raise FixtureRejected("invalid_row")
            if len(orders) >= MAX_ROWS:
                raise FixtureRejected("row_limit_exceeded")
            orders.append(_row(*row, seen))
    except csv.Error:
        raise FixtureRejected("invalid_csv")
    return _summary(orders)


# ---------------------------------------------------------------------------
# XLSX (zip + XML; no third-party library)
# ---------------------------------------------------------------------------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml(archive: zipfile.ZipFile, name: str):
    try:
        payload = archive.read(name)
    except KeyError:
        raise FixtureRejected("invalid_xlsx")
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        raise FixtureRejected("invalid_xlsx")


def _worksheet_name(archive: zipfile.ZipFile) -> str:
    workbook = _xml(archive, "xl/workbook.xml")
    sheet = next(
        (element for element in workbook.iter() if _local(element.tag) == "sheet"), None
    )
    if sheet is None:
        raise FixtureRejected("invalid_xlsx")
    relationship_id = None
    for key, value in sheet.attrib.items():
        if _local(key) == "id":
            relationship_id = value
            break
    if not relationship_id:
        raise FixtureRejected("invalid_xlsx")
    rels = _xml(archive, "xl/_rels/workbook.xml.rels")
    for relationship in rels.iter():
        if _local(relationship.tag) != "Relationship":
            continue
        if relationship.get("Id") == relationship_id:
            target = relationship.get("Target") or ""
            target = target.replace("\\", "/").lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            return target
    raise FixtureRejected("invalid_xlsx")


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        payload = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(payload)
    values = []
    for element in root.iter():
        if _local(element.tag) != "si":
            continue
        values.append(
            "".join(child.text or "" for child in element.iter() if _local(child.tag) == "t")
        )
    return values


def _cell_text(cell, shared: list[str]) -> str:
    for child in cell:
        if _local(child.tag) == "f":
            raise FixtureRejected("formula_not_supported")
    cell_type = cell.get("t", "n")
    value = ""
    inline = None
    for child in cell:
        if _local(child.tag) == "v":
            value = child.text or ""
        elif _local(child.tag) == "is":
            inline = child
    if cell_type == "s":
        index = int(value) if value.isdigit() else -1
        if index < 0 or index >= len(shared):
            raise FixtureRejected("invalid_xlsx")
        return shared[index]
    if cell_type == "inlineStr":
        if inline is None:
            return ""
        return "".join(child.text or "" for child in inline.iter() if _local(child.tag) == "t")
    if cell_type in ("str", "n", ""):
        return value
    raise FixtureRejected("invalid_cell_type")


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


def summarize_xlsx(payload: bytes) -> dict:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        raise FixtureRejected("invalid_xlsx")
    worksheet = _xml(archive, _worksheet_name(archive))
    shared = _shared_strings(archive)
    row_elements = [element for element in worksheet.iter() if _local(element.tag) == "row"]
    if not row_elements:
        raise FixtureRejected("empty_input")

    def values_of(row_element) -> list[str]:
        values: dict[int, str] = {}
        position = 0
        for cell in row_element:
            if _local(cell.tag) != "c":
                continue
            reference = cell.get("r") or ""
            index = _column_index(reference) if reference else None
            if index is None:
                index = position
            position = index + 1
            values[index] = _cell_text(cell, shared)
        highest = max(values) if values else -1
        return [values.get(index, "") for index in range(highest + 1)]

    header = values_of(row_elements[0])
    while header and header[-1] == "":
        header.pop()
    _validate_header(header)
    orders: list[tuple[str, str, str, Decimal]] = []
    seen: set[str] = set()
    for row_element in row_elements[1:]:
        values = values_of(row_element)
        if not any(values):
            continue
        if len(orders) >= MAX_ROWS:
            raise FixtureRejected("row_limit_exceeded")
        cells = values[:4] + [""] * (4 - len(values[:4]))
        orders.append(_row(*cells, seen))
    return _summary(orders)


def summarize(media_type: str, payload: bytes) -> dict:
    if media_type == CSV_MEDIA:
        return summarize_csv(payload)
    if media_type == XLSX_MEDIA:
        return summarize_xlsx(payload)
    raise FixtureRejected("unsupported_media_type")
