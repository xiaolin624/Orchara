"""F10/T060 comparison acceptance: real measured baseline vs available candidates.

The test executes the comparison harness (a real compiled Go probe for the
registered parser/chunker plus real PyMuPDF extraction for the runnable second
candidate) on the frozen fixture set and asserts the measured quality properties.
The Docling candidate's status is whatever the recorded isolated attempt says
(``blocked`` with the raw pip evidence, or ``measured``); the test never upgrades a
blocked attempt to a pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROBE = Path(__file__).resolve().parents[2] / "probes" / "f10_pdf_compare"
PRODUCT_ROOT = PROBE.parents[1]
REFERENCE = PRODUCT_ROOT / "tests" / "fixtures" / "f10" / "t060_reference.json"
ATTEMPT = PROBE / "docling_attempt.json"


def run_comparison(tmp_path: Path) -> dict:
    out = tmp_path / "comparison.json"
    env = os.environ.copy()
    completed = subprocess.run(
        [
            sys.executable,
            str(PROBE / "run_comparison.py"),
            "--json-out",
            str(out),
            "--runs",
            "2",
            "--candidates",
            "baseline,pymupdf",
            "--docling-attempt",
            str(ATTEMPT),
        ],
        cwd=str(PRODUCT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, f"comparison failed:\n{completed.stdout}\n{completed.stderr}"
    return json.loads(out.read_text(encoding="utf-8"))


def candidate(report: dict, label: str) -> dict:
    for entry in report["candidates"]:
        if entry["meta"]["label"] == label:
            return entry
    raise AssertionError(f"candidate {label!r} missing: {[c['meta']['label'] for c in report['candidates']]}")


def test_the_registered_parser_is_measured_against_a_real_second_candidate(tmp_path) -> None:
    report = run_comparison(tmp_path)
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))

    # Frozen inputs: the comparison ran on exactly the committed bytes.
    for name, fixture in report["fixtures"].items():
        assert fixture["sha256"] == reference["fixtures"][name]["sha256"], name
        assert fixture["pages"] == reference["fixtures"][name]["pages"], name
    assert report["min_text_runes"] == reference["min_text_runes"] == 32

    labels = {entry["meta"]["label"] for entry in report["candidates"]}
    assert labels == {"baseline-orchara-pdf", "candidate-b-pymupdf-sentence"}, labels
    baseline = candidate(report, "baseline-orchara-pdf")
    substitute = candidate(report, "candidate-b-pymupdf-sentence")

    # The baseline is the *registered* parser/chunker, not a Python reimplementation.
    assert baseline["meta"]["kind"] == "baseline"
    assert baseline["meta"]["version"] == "orchara-pdf-parser/1"
    assert baseline["meta"]["chunker_version"] == "orchara-pdf-page-paragraph-chunker/1"

    # Candidate B is an explicitly-labelled substitute, never presented as Docling.
    assert substitute["meta"]["kind"] == "available-substitute"
    assert substitute["meta"]["docling"] is False
    assert "NOT Docling" in substitute["meta"]["note"]
    assert "docling" not in substitute["meta"]["label"].lower()

    # Quality on the text fixtures is measured, not asserted from the platform's
    # own answer: the reference is the committed hand-checked page text.
    for name in ("t060_plain.pdf", "t060_layout.pdf"):
        base = baseline["fixtures"][name]
        other = substitute["fixtures"][name]
        assert base["word_coverage"] >= 0.99, (name, base["word_coverage"])
        assert base["page_exact_count"] == len(base["page_exact"]), (name, base["page_exact"])
        assert base["page_attribution"]["ratio"] == 1.0, (name, base["page_attribution"])
        assert other["word_coverage"] >= 0.99, (name, other["word_coverage"])
        # Measured parity on quality: the substitute shows no coverage/exact-page
        # benefit over the registered parser on these frozen inputs, which is why
        # the ticket eliminates it instead of adopting it.
        assert base["word_coverage"] >= other["word_coverage"], name
        assert base["page_exact_count"] >= other["page_exact_count"], name

    # A scanned PDF produces no text from any candidate; the platform refuses it
    # with its own code while the substitute merely returns an empty page. The
    # platform behaviour is the safety property under test.
    scanned = baseline["fixtures"]["t060_scanned.pdf"]
    assert scanned["parse_error"] == "pdf_no_text_layer", scanned
    assert scanned["chunk_stats"]["count"] == 0, scanned
    assert scanned["page_attribution"]["ratio"] is None, scanned
    scanned_substitute = substitute["fixtures"]["t060_scanned.pdf"]
    assert scanned_substitute["chunk_stats"]["count"] == 0, scanned_substitute
    assert scanned_substitute["pages_without_text"] == [1], scanned_substitute

    # The threshold fixture (a short text layer) is refused by the platform and
    # would have been accepted by a naive extractor: the baseline's no-text-layer
    # rule is a real quality/safety difference, measured on the same bytes.
    short = baseline["fixtures"]["t060_short.pdf"]
    assert short["parse_error"] == "pdf_no_text_layer", short
    assert substitute["fixtures"]["t060_short.pdf"]["chunk_stats"]["count"] == 1

    # Resource numbers really were measured for both candidates (wall clock and
    # peak working set are machine-dependent, so their values are reported in the
    # committed JSON but not asserted here).
    for entry in (baseline, substitute):
        assert entry["runs"] == 2
        assert entry["wall_s_median"] > 0, entry["meta"]
        assert entry["peak_rss_mb_median"] > 0, entry["meta"]
        for name, fixture in entry["fixtures"].items():
            assert fixture["compute_ms_median"] >= 0, (entry["meta"]["label"], name)

    # Docling: report exactly what the isolated attempt recorded. A blocked
    # attempt must carry the command and the raw failure; a measured one carries
    # real quality numbers. It is never turned into a made-up pass.
    docling = report["docling"]
    assert docling["status"] in {"blocked", "measured"}, docling
    if docling["status"] == "blocked":
        assert docling.get("reason"), docling
        assert docling.get("command"), docling
        assert docling.get("error") or docling.get("import_error"), docling
    else:
        assert docling.get("command"), docling
        assert docling.get("pip_exit") == 0, docling
        assert docling.get("fixtures"), docling
        for name, fixture in docling["fixtures"].items():
            assert fixture.get("word_coverage") is not None, (name, docling)
            assert fixture.get("chunk_count") is not None, (name, docling)
            assert fixture.get("sha256") == reference["fixtures"][name]["sha256"], name
        # The Docling pipeline really ran on the scanned fixture too: it reports
        # the image-only page as empty instead of inventing text.
        assert docling["fixtures"]["t060_scanned.pdf"]["pages_without_text"] == [1]

    # No-benefit elimination is a measured decision, not a claim: every substitute
    # and the Docling attempt matched (or lost to) the baseline on the graded
    # fixtures, so none of them is adopted.
    eliminations = {entry["candidate"]: entry for entry in report["eliminations"]}
    assert "docling-native" in eliminations, eliminations
    assert "candidate-b-pymupdf-sentence" in eliminations, eliminations
    for label, entry in eliminations.items():
        assert entry["strict_quality_gain"] is False, (label, entry)
        assert "eliminated" in entry["decision"], (label, entry)
    assert "Keep the registered baseline" in report["decision"], report["decision"]
    assert "none shows a strict quality gain" in report["decision"], report["decision"]


def test_the_comparison_harness_is_a_real_re_runnable_entry_point() -> None:
    assert (PROBE / "run_comparison.py").is_file()
    assert (PROBE / "candidate_worker.py").is_file()
    assert (PROBE / "go_baseline" / "main.go").is_file()
    assert (PROBE / "requirements.txt").is_file()
    # Docling is not declared as a working dependency anywhere in the probe.
    requirements = (PROBE / "requirements.txt").read_text(encoding="utf-8")
    assert "docling==" not in requirements
