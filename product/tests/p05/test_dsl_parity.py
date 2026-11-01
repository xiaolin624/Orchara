"""P05/T007: one corpus, two languages, one verdict.

The shared fixture is ``product/probes/p05_dsl/corpus.json``. Python validates
it directly (``probes/p05_dsl/python/validate.py``); the Go CLI
(``probes/p05_dsl/go``) is built and run over the same file. Both must produce
the same verdict, the same ordered reason codes, the same locatable pointers and
the same canonical digests — the test compares their *stdout byte for byte*.

The existing documentation linter (``tools/validate_contracts.py``) is reference
only: it is exercised here so the gap is measured, not assumed.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PRODUCT_ROOT.parent
PROBE = PRODUCT_ROOT / "probes" / "p05_dsl"
CORPUS_PATH = PROBE / "corpus.json"
RULES_PATH = PROBE / "rules.json"
GO_DIR = PROBE / "go"
EXAMPLES = REPO_ROOT / "examples"
LINTER = REPO_ROOT / "tools" / "validate_contracts.py"

sys.path.insert(0, str(PROBE / "python"))
import validate  # noqa: E402

RULES = validate.Rules.load(RULES_PATH)
CORPUS, CORPUS_BYTES = validate.load_corpus(CORPUS_PATH)
CASES = CORPUS["cases"]
BY_NAME = {case["name"]: case for case in CASES}
VALID = [case for case in CASES if case["verdict"] == "valid"]
INVALID = [case for case in CASES if case["verdict"] == "invalid"]

# Acceptance criterion "非法类型/跨区/循环有拒绝原因" plus the codes the ticket
# names as examples, plus every code the rule table declares.
REQUIRED_KINDS = ("unknown_type", "cross_region_edge", "cycle", "missing_value",
                  "duplicate_id", "bad_port")


def _ids(cases):
    return [case["name"] for case in cases]


def _validate(case):
    return validate.validate(case["input"], RULES, input_sha256=case["input_sha256"],
                             corpus_sha256=validate.sha256_bytes(CORPUS_BYTES),
                             rules_sha256=RULES.sha256)


# --------------------------------------------------------------------------
# corpus integrity
# --------------------------------------------------------------------------
def test_corpus_is_well_formed() -> None:
    assert validate.check_corpus(CORPUS) == []


def test_corpus_is_reproducible() -> None:
    """The pins are generated, so a hand edit cannot silently drift."""
    completed = subprocess.run(
        [sys.executable, str(PROBE / "build_corpus.py"), "--check"],
        cwd=PROBE, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "up to date" in completed.stdout


def test_corpus_is_derived_from_the_real_examples() -> None:
    """Valid cases come from the contract fixtures, not from invented dialects."""
    for case_name, relative in (
        ("linear-sales", "sales/workflow.json"),
        ("parallel-two-branch", "parallel/workflow.json"),
        ("condition-branch-approval", "branch-approval/workflow.json"),
        ("cycle", "invalid/cycle.json"),
    ):
        expected = json.loads((EXAMPLES / relative).read_text(encoding="utf-8"))
        assert BY_NAME[case_name]["input"] == expected, (
            f"{case_name} no longer matches examples/{relative}"
        )


def test_every_pinned_hash_is_consistent() -> None:
    for case in VALID:
        assert case["expected_hash"], f"{case['name']} has no hash"
        assert len(case["expected_hash"]) == 64
        if "expected_canonical" in case:
            # Independent of the validator: the pin must be the digest of the
            # pinned bytes.
            assert case["expected_hash"] == hashlib.sha256(
                case["expected_canonical"].encode("utf-8")
            ).hexdigest()
    for case in INVALID:
        assert case["input_sha256"], f"{case['name']} has no input hash"
        assert "expected_hash" not in case, "a rejected document has no normal form"


# --------------------------------------------------------------------------
# Python verdicts
# --------------------------------------------------------------------------
@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_python_verdict_matches_corpus(case: dict) -> None:
    result = _validate(case)
    assert result.verdict == case["verdict"]
    assert result.input_sha256 == case["input_sha256"]
    if case["verdict"] == "valid":
        assert result.problems == ()
        assert result.data is not None and result.artifact is not None
        assert result.data.sha256 == case["expected_hash"]
        if "expected_canonical" in case:
            assert result.data.canonical == case["expected_canonical"]
        # The pin is re-derived here through the JCS entry point, not through
        # the normaliser's own digest field.
        import jcs

        assert hashlib.sha256(
            jcs.canonicalize_value(result.data.document).encode("utf-8")
        ).hexdigest() == case["expected_hash"]
    else:
        assert result.reason == case["reason"]
        assert result.reasons() == case["reasons"]
        assert result.data is None and result.artifact is None


@pytest.mark.parametrize("case", INVALID, ids=_ids(INVALID))
def test_every_rejection_is_locatable(case: dict) -> None:
    result = _validate(case)
    declared = set(RULES.codes())
    assert result.problems, "an invalid case must report at least one problem"
    for problem in result.problems:
        assert problem.code in declared, f"{problem.code} is not a declared code"
        pointer = problem.pointer
        assert pointer == "" or pointer.startswith("/")
        for token in pointer.split("/")[1:]:
            residue = token.replace("~0", "").replace("~1", "")
            assert "~" not in residue, f"unescaped ~ in pointer {pointer!r}"


def test_every_declared_reason_code_has_a_negative_case() -> None:
    exercised = {code for case in INVALID for code in case["reasons"]}
    declared = set(RULES.codes())
    assert declared <= exercised, f"codes without a negative case: {sorted(declared - exercised)}"
    assert exercised <= declared, f"codes outside the rule table: {sorted(exercised - declared)}"
    for kind in REQUIRED_KINDS:
        assert kind in exercised, f"required rejection kind {kind!r} is missing"


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------
def test_normalisation_is_order_independent() -> None:
    assert BY_NAME["linear-sales-nodes-shuffled"]["expected_hash"] == (
        BY_NAME["linear-sales"]["expected_hash"]
    ), "reordering nodes/edges must not change the data digest"
    shuffled = _validate(BY_NAME["linear-sales-nodes-shuffled"])
    original = _validate(BY_NAME["linear-sales"])
    assert shuffled.data.canonical == original.data.canonical


@pytest.mark.parametrize("case", VALID, ids=_ids(VALID))
def test_normalised_data_never_contains_an_invented_null(case: dict) -> None:
    result = _validate(case)
    _assert_no_null(result.data.document, case["name"])


def _assert_no_null(value, path: str) -> None:
    assert value is not None, f"normalised data contains null at {path}"
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_no_null(item, f"{path}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_null(item, f"{path}/{index}")


# --------------------------------------------------------------------------
# missing values never become implicit null
# --------------------------------------------------------------------------
def test_missing_value_is_not_implicit_null() -> None:
    case = BY_NAME["end-missing-required-output"]
    result = _validate(case)

    assert result.reason == "missing_value"
    assert any(problem.pointer.endswith("/inputs/summary") for problem in result.problems)
    assert result.data is None, "a rejected document must not get a normal form"
    assert result.artifact is None, "a rejected document must not get an artifact"

    wire = validate.wire_result(result, case["name"])
    for key in ("data_sha256", "data_canonical", "artifact_sha256"):
        assert key not in wire, f"absent data must stay absent, found {key!r}"


def test_explicit_null_is_a_value_and_missing_keys_are_not_null() -> None:
    """``null`` is rejected as a container; a deleted key is rejected as missing.
    Neither is silently turned into ``{}``."""
    document = json.loads(json.dumps(BY_NAME["anchor-minimal-linear"]["input"]))
    document["nodes"][0]["inputs"] = None
    nulled = validate.validate(document, RULES)
    assert nulled.verdict == "invalid"
    assert nulled.reason == "bad_document"

    document = json.loads(json.dumps(BY_NAME["anchor-minimal-linear"]["input"]))
    del document["nodes"][0]["inputs"]
    missing = validate.validate(document, RULES)
    assert missing.verdict == "invalid"
    assert missing.reason == "bad_document"


def test_valid_verdict_carries_no_reason_key() -> None:
    wire = validate.wire_result(_validate(BY_NAME["anchor-minimal-linear"]), "anchor")
    assert "reason" not in wire and "problems" not in wire
    assert set(wire) == {"name", "verdict", "input_sha256", "data_sha256",
                         "data_canonical", "artifact_sha256"}


# --------------------------------------------------------------------------
# data vs artifact
# --------------------------------------------------------------------------
def test_data_and_artifact_are_separate_objects() -> None:
    case = BY_NAME["linear-sales"]
    result = _validate(case)
    data, artifact = result.data, result.artifact

    assert isinstance(data, validate.NormalizedWorkflow)
    assert isinstance(artifact, validate.WorkflowArtifact)
    assert data is not artifact
    assert data.document is not artifact.descriptor
    assert data.canonical != artifact.canonical
    assert data.sha256 != artifact.sha256
    assert artifact.descriptor["data_sha256"] == data.sha256
    assert artifact.descriptor["schema_id"] == RULES.raw["schema_id"]


def test_provenance_changes_the_artifact_and_never_the_data() -> None:
    case = BY_NAME["linear-sales"]
    result = _validate(case)
    data = result.data

    first = validate.publish(data, validate.build_provenance(
        RULES, input_sha256=result.input_sha256, data_sha256=data.sha256,
        corpus_sha256="0000", rules_sha256=RULES.sha256))
    second = validate.publish(data, validate.build_provenance(
        RULES, input_sha256=result.input_sha256, data_sha256=data.sha256,
        corpus_sha256="ffff", rules_sha256=RULES.sha256))

    assert first.sha256 != second.sha256, "artifact digest must cover provenance"
    assert data.sha256 == case["expected_hash"], "republishing must not change data"
    # And the published artifact still names the same data digest.
    assert first.descriptor["data_sha256"] == second.descriptor["data_sha256"] == data.sha256
    assert first.descriptor["provenance"]["corpus_sha256"] != (
        second.descriptor["provenance"]["corpus_sha256"]
    )


# --------------------------------------------------------------------------
# Go <-> Python
# --------------------------------------------------------------------------
GO = os.environ.get("ORCHARA_GO") or shutil.which("go") or r"E:\orchara-toolchains\go\bin\go.exe"
GO_ENV = dict(
    os.environ,
    GOCACHE=os.environ.get("GOCACHE", r"E:\orchara-toolchains\gocache"),
    GOMODCACHE=os.environ.get("GOMODCACHE", r"E:\orchara-toolchains\gomodcache"),
)


@pytest.fixture(scope="session")
def go_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not Path(GO).exists():
        pytest.skip("Go toolchain not available")
    out = tmp_path_factory.mktemp("p05go") / ("p05dsl.exe" if sys.platform == "win32" else "p05dsl")
    completed = subprocess.run(
        [GO, "build", "-o", str(out), "."],
        cwd=GO_DIR, capture_output=True, text=True, timeout=300, env=GO_ENV,
    )
    if completed.returncode != 0:
        pytest.fail(f"go build failed:\n{completed.stdout}\n{completed.stderr}")
    return out


def _python_cli() -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(PROBE / "python" / "validate.py"), "--corpus", str(CORPUS_PATH)],
        cwd=PROBE, capture_output=True, timeout=300,
    )


def _go_cli(binary: Path, *extra: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(binary), "-corpus", str(CORPUS_PATH), *extra],
        cwd=PROBE, capture_output=True, timeout=300, env=GO_ENV,
    )


def test_go_and_python_stdout_is_byte_identical(go_binary: Path) -> None:
    python_run = _python_cli()
    go_run = _go_cli(go_binary)
    assert python_run.returncode == 0, python_run.stderr.decode("utf-8", "replace")
    assert go_run.returncode == 0, go_run.stderr.decode("utf-8", "replace")
    assert python_run.stdout, "the Python CLI printed nothing"
    assert go_run.stdout == python_run.stdout, "Go and Python disagree on the corpus"
    assert python_run.stdout.startswith(b'{"cases":')


def test_go_and_python_agree_on_every_case(go_binary: Path) -> None:
    python_output = json.loads(_python_cli().stdout)
    go_output = json.loads(_go_cli(go_binary).stdout)
    assert go_output["corpus_sha256"] == python_output["corpus_sha256"]
    assert go_output["rules_sha256"] == python_output["rules_sha256"]
    python_cases = {case["name"]: case for case in python_output["cases"]}
    go_cases = {case["name"]: case for case in go_output["cases"]}
    assert set(python_cases) == set(go_cases) == set(BY_NAME)
    for name in sorted(python_cases):
        left, right = python_cases[name], go_cases[name]
        for key in ("verdict", "input_sha256", "reason", "reasons",
                    "data_sha256", "artifact_sha256", "problems"):
            assert left.get(key) == right.get(key), f"{name}: {key} differs between Go and Python"


def test_go_check_mode_accepts_the_corpus(go_binary: Path) -> None:
    completed = _go_cli(go_binary, "-check")
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    assert completed.stdout.decode("utf-8").strip() == f"OK: {len(CASES)} cases"


def test_python_check_mode_accepts_the_corpus() -> None:
    completed = subprocess.run(
        [sys.executable, str(PROBE / "python" / "validate.py"),
         "--corpus", str(CORPUS_PATH), "--check"],
        cwd=PROBE, capture_output=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# the reference linter is measured, not assumed
# --------------------------------------------------------------------------
def _reference_linter():
    """Load ``tools/validate_contracts.py``'s limited graph linter only.

    The module also imports documentation-only dependencies (pglast,
    openapi_spec_validator) that the product environment does not install, so
    the single function is lifted out of the AST instead of importing it.
    """
    module = ast.parse(LINTER.read_text(encoding="utf-8"))
    function = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "graph_errors"
    )
    namespace: dict[str, object] = {"deque": deque}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(LINTER), "exec"), namespace)
    return namespace["graph_errors"]


def test_existing_document_linter_is_reference_only() -> None:
    """Measure the old documentation linter against the corpus.

    It is not the implementation: it cannot even run on some malformed
    documents, it stays silent on most semantic negatives, it has its own
    (smaller) vocabulary, and its precedence differs from the pinned one.
    """
    linter = _reference_linter()
    silent: list[str] = []
    crashed: list[str] = []
    reported: list[tuple[str, list[str]]] = []
    for case in INVALID:
        try:
            codes = linter(case["input"])
        except Exception:  # the linter was written for happy-path fixtures
            crashed.append(case["name"])
            continue
        if codes:
            reported.append((case["name"], codes))
        else:
            silent.append(case["name"])

    assert len(silent) >= 15, (
        f"the linter rejected {len(reported)} of {len(INVALID)} cases; "
        "it is not as thin as documented"
    )
    assert crashed, "the linter is expected to be unable to run on at least one input"
    assert len(reported) + len(silent) + len(crashed) == len(INVALID)

    linter_codes = {code for _, codes in reported for code in codes}
    our_codes = set(RULES.codes())
    assert len(linter_codes) < len(our_codes)
    assert {"missing_value", "bad_binding", "bad_pointer", "type_mismatch",
            "cross_region_edge", "bad_join_sources"} - linter_codes, (
        "the corpus relies on rejection kinds the documentation linter never produces"
    )

    # Different precedence, not just different coverage: the linter reports the
    # duplicate id while the pinned corpus says the schema stage wins.
    linter_view = dict(reported)
    precedence_case = BY_NAME["precedence-schema-over-duplicate-id"]
    assert "duplicate_id" in linter_view[precedence_case["name"]]
    assert precedence_case["reasons"] == ["unknown_type"]

    # No false positives on the documents this probe accepts.
    for case in VALID:
        assert not linter(case["input"]), f"{case['name']}: linter rejected a valid case"


def test_contract_json_schema_alone_accepts_most_semantic_negatives() -> None:
    """The schema gate is not the validator: it cannot see refs, regions or cycles."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (PRODUCT_ROOT / "contracts" / "workflow.schema.json").read_text(encoding="utf-8")
    )
    checker = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    accepted = [case["name"] for case in INVALID if checker.is_valid(case["input"])]
    assert len(accepted) >= 15, (
        "the semantic negatives should mostly pass the JSON Schema; "
        f"only {len(accepted)} of {len(INVALID)} did"
    )
    # Every valid case must of course satisfy the contract schema itself.
    for case in VALID:
        checker.validate(case["input"])
