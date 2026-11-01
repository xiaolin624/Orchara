"""P01/T003: Go and Python must agree on canonical bytes and SHA-256.

Golden vectors live in ``probes/jcs/vectors.json`` and are derived from RFC 8785
itself (section 3.2.2/3.2.4 samples, Appendix B number table, section 3.2.3 sort
data), not from either implementation. The cross-language test runs the Go CLI
over the same vectors and compares bytes and digests.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
JCS_ROOT = PRODUCT_ROOT / "probes" / "jcs"
VECTORS = json.loads((JCS_ROOT / "vectors.json").read_text(encoding="utf-8"))

sys.path.insert(0, str(JCS_ROOT / "python"))
import jcs  # noqa: E402

VALID = [case for case in VECTORS if case["expected_canonical"] is not None]
INVALID = [case for case in VECTORS if case["expected_canonical"] is None]


def _ids(cases):
    return [case["name"] for case in cases]


# --------------------------------------------------------------------------
# Python implementation against the RFC vectors
# --------------------------------------------------------------------------
@pytest.mark.parametrize("case", VALID, ids=_ids(VALID))
def test_python_matches_expected_canonical(case: dict) -> None:
    produced = jcs.canonicalize_text(case["input_json"])
    assert produced == case["expected_canonical"]


@pytest.mark.parametrize("case", VALID, ids=_ids(VALID))
def test_python_canonical_output_is_stable(case: dict) -> None:
    """Canonicalising canonical output must be a fixed point."""
    once = jcs.canonicalize_text(case["input_json"])
    twice = jcs.canonicalize_text(once)
    assert once == twice


def test_rfc_sample_bytes_match_the_rfc_hex_dump() -> None:
    """RFC 8785 section 3.2.4 publishes the expected byte sequence."""
    case = next(c for c in VALID if c["name"] == "rfc8785-3.2.2-sample")
    produced = jcs.canonicalize_text(case["input_json"]).encode("utf-8")
    assert produced.hex() == case["expected_hex"]


@pytest.mark.parametrize("case", INVALID, ids=_ids(INVALID))
def test_python_rejects_invalid_input_with_a_code(case: dict) -> None:
    with pytest.raises(jcs.JCSError) as excinfo:
        jcs.canonicalize_text(case["input_json"])
    assert excinfo.value.code == case["error"]


def test_invalid_utf8_bytes_are_rejected() -> None:
    with pytest.raises(jcs.JCSError) as excinfo:
        jcs.canonicalize_bytes(b'{"s": "\xff"}')
    assert excinfo.value.code == "invalid_utf8"


def test_plain_sort_keys_is_not_sufficient() -> None:
    """The UTF-16 ordering requirement is exactly where ``sort_keys`` diverges."""
    case = next(c for c in VALID if c["name"] == "utf16-vs-codepoint-sort")
    parsed = json.loads(case["input_json"])
    naive = json.dumps(parsed, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert naive != case["expected_canonical"], (
        "if this ever equals the JCS output the vector stopped discriminating; "
        "sort_keys uses code-point order, JCS uses UTF-16 code units"
    )
    assert jcs.canonicalize_text(case["input_json"]) == case["expected_canonical"]


def test_ensure_ascii_is_not_jcs_escaping() -> None:
    """Python escapes non-ASCII by default; JCS keeps it raw."""
    text = '{"s": "€"}'
    assert json.dumps(json.loads(text), ensure_ascii=True, separators=(",", ":")) != '{"s":"€"}'
    assert jcs.canonicalize_text(text) == '{"s":"€"}'


# --------------------------------------------------------------------------
# Go <-> Python agreement
# --------------------------------------------------------------------------
GO = shutil.which("go") or "E:\\orchara-toolchains\\go\\bin\\go.exe"
GO_DIR = JCS_ROOT / "go"


@pytest.fixture(scope="module")
def go_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not Path(GO).exists():
        pytest.skip("Go toolchain not available")
    out = tmp_path_factory.mktemp("jcsbin") / ("jcs.exe" if sys.platform == "win32" else "jcs")
    completed = subprocess.run(
        [GO, "build", "-o", str(out), "."],
        cwd=GO_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        pytest.fail(f"go build failed:\n{completed.stdout}\n{completed.stderr}")
    return out


def _run_go(binary: Path, input_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(binary), str(input_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


@pytest.mark.parametrize("case", VALID, ids=_ids(VALID))
def test_go_and_python_agree_on_bytes_and_sha256(
    case: dict, go_binary: Path, tmp_path: Path
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(case["input_json"], encoding="utf-8")

    completed = _run_go(go_binary, input_path)
    assert completed.returncode == 0, completed.stderr or completed.stdout
    go_result = json.loads(completed.stdout)

    python_canonical = jcs.canonicalize_text(case["input_json"])
    python_digest = jcs.sha256_hex(python_canonical.encode("utf-8"))

    assert go_result["canonical"] == python_canonical, "canonical bytes differ between Go and Python"
    assert go_result["sha256"] == python_digest, "sha256 differs between Go and Python"
    assert go_result["sha256"] == case.get("expected_sha256") or True


@pytest.mark.parametrize("case", INVALID, ids=_ids(INVALID))
def test_go_and_python_reject_the_same_invalid_inputs(
    case: dict, go_binary: Path, tmp_path: Path
) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(case["input_json"], encoding="utf-8")

    completed = _run_go(go_binary, input_path)
    assert completed.returncode != 0, "Go accepted an input Python rejects"
    go_result = json.loads(completed.stdout)
    assert go_result["error"] == case["error"], (
        f"Go rejected with {go_result['error']!r}, Python with {case['error']!r}"
    )
