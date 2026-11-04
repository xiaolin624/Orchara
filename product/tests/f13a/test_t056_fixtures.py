"""T056 fixtures, independent Decimal truth and the guard against the tool.

These tests need no database and no sandbox: they freeze the committed fixtures,
prove the expectations come from an independent calculator, and refuse a future
edit of that calculator that would import the product's preset tool (which would
make "the expectation" the thing under test).
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from support import t056

REGISTRY = json.loads((Path(t056.DATASETS_DIR) / "registry.json").read_text(encoding="utf-8"))


def dataset_files() -> list[Path]:
    return sorted(
        path
        for path in Path(t056.DATASETS_DIR).glob("*.json")
        if path.name not in {"expected_values.json", "registry.json"}
    )


def test_the_committed_datasets_validate_against_the_contract_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = Path(t056.PRODUCT_ROOT) / "contracts" / "evaluation-case.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    files = dataset_files()
    assert files, "no committed T056 dataset was found"
    for path in files:
        document = json.loads(path.read_text(encoding="utf-8"))
        assert set(document) == {"cases"}, path.name
        for index, case in enumerate(document["cases"]):
            errors = sorted(validator.iter_errors(case), key=lambda error: list(error.path))
            assert not errors, (
                f"{path.name} case {index} is not valid against the evaluation-case schema: "
                f"{[error.message for error in errors]}"
            )


def test_every_committed_dataset_case_binds_a_known_fixture() -> None:
    for path in dataset_files():
        document = json.loads(path.read_text(encoding="utf-8"))
        resolved = t056.resolve_fixtures(document)
        declared = {
            case["input"]["task_input"]["sha256"] for case in document["cases"]
        }
        assert len(resolved) == len(declared), path.name
        for content, media_type in resolved:
            assert t056.sha256_hex(content) in declared, path.name
            assert media_type in (t056.CSV_MEDIA, t056.XLSX_MEDIA)


def test_the_reference_calculator_reproduces_the_committed_expected_table() -> None:
    from f13a.reference import sales_reference

    table = t056.expected_values()
    assert set(table) == {"sales_happy_csv", "sales_happy_xlsx", "frozen_boundary"}
    for name, expected in table.items():
        payload = t056.fixture_bytes(expected)
        assert hashlib.sha256(payload).hexdigest() == expected["sha256"], name
        summary = sales_reference.summarize(expected["media_type"], payload)
        assert summary["total"] == expected["total"], (name, summary)
        assert summary["regions"] == expected["regions"], (name, summary)
        assert summary["order_count"] == expected["order_count"], (name, summary)


def test_the_committed_dataset_assertions_agree_with_the_expected_table() -> None:
    """The dataset's declarative expectations are the reference table's values."""
    table = t056.expected_values()
    for name, expected in table.items():
        filename = {
            "sales_happy_csv": "sales_happy_csv.json",
            "sales_happy_xlsx": "sales_happy_xlsx.json",
            "frozen_boundary": "frozen_boundary.json",
        }[name]
        document = json.loads((Path(t056.DATASETS_DIR) / filename).read_text(encoding="utf-8"))
        case = document["cases"][0]
        by_pointer = {
            assertion["pointer"]: assertion["expected"]
            for assertion in case["assertions"]
            if assertion["kind"] in {"decimal_equals", "json_equals"}
        }
        assert by_pointer["/summary/total"] == expected["total"], name
        assert by_pointer["/summary/order_count"] == expected["order_count"], name
        for region, total in expected["regions"].items():
            assert by_pointer["/summary/regions/" + region] == total, name


def test_the_reference_calculator_rejects_the_bad_inputs_for_their_declared_reason() -> None:
    """Independent reading of *why* each bad fixture is bad (never the tool's output)."""
    from f13a.reference import sales_reference

    document = t056.load_dataset("bad_inputs.json")
    cases = {case["case_id"]: case for case in document["cases"]}
    # Resolve every fixture by hash so the test never hard-codes which file is which.
    for sha, media_type in sorted(
        (case["input"]["task_input"]["sha256"], case["input"]["task_input"]["media_type"])
        for case in document["cases"]
    ):
        payload = t056.fixture_candidates()[sha]
        expected_code = next(
            case["input"]["expected_failure"]["error_code"]
            for case in document["cases"]
            if case["input"]["task_input"]["sha256"] == sha
        )
        with pytest.raises(sales_reference.FixtureRejected) as failure:
            sales_reference.summarize(media_type, payload)
        assert failure.value.code == expected_code, (sha, failure.value.code)
    assert len(cases) == 5


def test_the_reference_module_does_not_import_or_call_the_tool() -> None:
    module_path = Path(sales_reference_path())
    source = module_path.read_text(encoding="utf-8")
    assert "sales_stats" not in source, "the reference calculator must not name the tool"
    tree = ast.parse(source, filename=str(module_path))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    allowed_stdlib = {
        "csv", "datetime", "io", "re", "zipfile", "decimal", "xml.etree.ElementTree",
        "__future__",
    }
    for name in imported:
        assert not name.startswith("platform_worker"), name
        assert not name.startswith("orchara"), name
        assert name.split(".")[0] in {value.split(".")[0] for value in allowed_stdlib}, name
    for forbidden in ("subprocess", "importlib", "__import__", "exec(", "eval(", "ctypes"):
        assert forbidden not in source, forbidden

    # …and the module really is importable in a process where the tool is absent.
    probe = (
        "import sys; "
        "sys.path.insert(0, r'" + str(Path(t056.PRODUCT_ROOT) / "tests") + "'); "
        "from f13a.reference import sales_reference; "
        "assert 'platform_worker' not in sys.modules; "
        "assert 'sales_stats' not in sys.modules; "
        "print('independent')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert "independent" in completed.stdout


def sales_reference_path() -> Path:
    return Path(t056.PRODUCT_ROOT) / "tests" / "f13a" / "reference" / "sales_reference.py"


def test_the_registry_pins_every_dataset_to_its_bytes() -> None:
    """A committed registry proves which dataset bytes each evaluation ran."""
    assert REGISTRY["schema"] == "orchara.t056-fixtures/1"
    for name, entry in REGISTRY["datasets"].items():
        path = Path(t056.DATASETS_DIR) / name
        assert path.exists(), name
        content = path.read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"], name
        assert len(content) == entry["size_bytes"], name
