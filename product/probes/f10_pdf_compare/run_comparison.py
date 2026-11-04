#!/usr/bin/env python3
"""F10/T060 parser/chunker comparison: real measured numbers on frozen fixtures.

Baseline: the platform's registered parser + chunker, run through the compiled
``go_baseline`` probe (the same knowledge.ParserFor / Parse / ChunkParsed /
ChunkerVersionFor calls the ingestion handler makes).

Candidate A: Docling Native + Docling chunker. Docling is not installed in this
environment; ``docling_worker.py`` records the isolated installation attempt and
the harness copies that status into the report. A blocked attempt is a result, not
a missing number.

Candidate B (and extra extraction rows): available substitutes, explicitly
labelled as substitutes and never as Docling:
  * PyMuPDF extraction + the T060 sentence packer (the runnable second candidate)
  * pypdf extraction + the same chunker
  * pdfminer.six extraction + the same chunker

Every child process is measured the same way: wall clock around the process, peak
working set sampled from the OS, and the child's own parse+chunk duration. Quality
is measured against the committed hand-checked reference pages.

Usage:
    python probes/f10_pdf_compare/run_comparison.py \
        --json-out reports/f10/t060-comparison.json --runs 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRODUCT_ROOT = HERE.parents[1]
FIXTURES_DIR = PRODUCT_ROOT / "tests" / "fixtures" / "f10"
REFERENCE_PATH = FIXTURES_DIR / "t060_reference.json"
DEFAULT_DOCLING_ATTEMPT = HERE / "docling_attempt.json"

sys.path.insert(0, str(HERE))
import candidates  # noqa: E402
import metrics  # noqa: E402

try:  # imported once: a lazy import inside the sampling loop can outlive a short child
    import psutil  # noqa: E402
except Exception:  # noqa: BLE001 - reported as a measurement failure, never faked
    psutil = None

sys.path.insert(0, str(PRODUCT_ROOT / "tools"))
import toolchain  # noqa: E402

GO_BASELINE = "baseline-go"
BASELINE_META = {
    "label": "baseline-orchara-pdf",
    "kind": "baseline",
    "docling": False,
    "extractor": f"orchara-pdf-parser/1 (Go, rsc.io/pdf) via probes/f10_pdf_compare/go_baseline",
    "chunker": "orchara-pdf-page-paragraph-chunker/1",
    "licence": "BSD-3-Clause (rsc.io/pdf)",
    "note": "The registered platform parser/chunker, exercised through its real interface.",
}


# ---------------------------------------------------------------------------
# environment + fixtures
# ---------------------------------------------------------------------------
def environment() -> dict:
    packages = {}
    for module in ("fitz", "pypdf", "pdfminer", "psutil"):
        try:
            imported = __import__(module)
            packages[module] = str(
                getattr(imported, "VersionBind", None)
                or getattr(imported, "__version__", "unknown")
            )
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            packages[module] = f"unavailable: {exc}"
    try:
        go_version = subprocess.run(
            [toolchain.go_binary(), "version"], capture_output=True, text=True, timeout=60
        ).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        go_version = f"unavailable: {exc}"
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "go": go_version,
        "packages": packages,
        "note": "Windows host; wall clock is process wall time, RSS is peak working set.",
    }


def load_fixtures() -> dict:
    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    names = ["t060_plain.pdf", "t060_layout.pdf", "t060_scanned.pdf", "t060_partial.pdf", "t060_short.pdf"]
    fixtures = {}
    for name in names:
        path = FIXTURES_DIR / name
        raw = path.read_bytes()
        entry = reference["fixtures"][name]
        actual = hashlib.sha256(raw).hexdigest()
        if actual != entry["sha256"]:
            raise SystemExit(f"{name}: sha256 drifted from the committed reference")
        fixtures[name] = {
            "path": str(path),
            "sha256": actual,
            "bytes": len(raw),
            "page_count": entry["page_count"],
            "pages": entry["pages"],
            "pages_without_text": entry["pages_without_text"],
        }
    return {"min_text_runes": reference["min_text_runes"], "fixtures": fixtures}


# ---------------------------------------------------------------------------
# uniform child measurement
# ---------------------------------------------------------------------------
def _peak_working_set(process) -> int:
    if psutil is None:
        return 0
    try:
        info = psutil.Process(process.pid).memory_info()
    except Exception:  # noqa: BLE001 - the process may have exited between samples
        return 0
    peak = getattr(info, "peak_wset", None)
    if peak:
        return int(peak)
    return int(getattr(info, "rss", 0) or 0)


def run_child(command: list[str], *, cwd: Path, env: dict, timeout: float = 600.0) -> dict:
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    peak = 0
    while True:
        # Sample before the poll so a short-lived process still yields at least
        # one reading; 1 ms sleep keeps the sampler from pinning a core.
        peak = max(peak, _peak_working_set(process))
        if process.poll() is not None:
            break
        if time.perf_counter() - started > timeout:
            process.kill()
            stdout, stderr = process.communicate()
            raise RuntimeError(f"child timed out after {timeout}s: {command}\n{stderr[-4000:]}")
        time.sleep(0.001)
    stdout, stderr = process.communicate()
    wall = time.perf_counter() - started
    if process.returncode != 0:
        raise RuntimeError(
            f"child failed ({process.returncode}): {command}\nstdout:{stdout[-2000:]}\nstderr:{stderr[-4000:]}"
        )
    if not peak:
        raise RuntimeError(f"could not sample peak working set for {command}")
    return {"wall_s": wall, "peak_rss_bytes": peak, "stdout": stdout, "stderr": stderr}


def build_go_baseline() -> tuple[Path, str]:
    out_dir = Path(tempfile.mkdtemp(prefix="t060-baseline-"))
    binary = out_dir / ("baseline.exe" if os.name == "nt" else "baseline")
    completed = subprocess.run(
        [toolchain.go_binary(), "build", "-o", str(binary), "./probes/f10_pdf_compare/go_baseline"],
        cwd=str(PRODUCT_ROOT),
        env=toolchain.go_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise SystemExit(f"go build failed: {completed.stderr}")
    go_version = subprocess.run(
        [toolchain.go_binary(), "version"], capture_output=True, text=True, timeout=60
    ).stdout.strip()
    return binary, go_version


def measure_baseline(
    binary: Path, fixtures: list[str], runs: int, workdir: Path
) -> dict:
    fixture_paths = [str(FIXTURES_DIR / name) for name in fixtures]
    records = []
    for _run in range(runs):
        out_path = workdir / "baseline-run.json"
        child = run_child(
            [str(binary), "-out", str(out_path), *fixture_paths],
            cwd=PRODUCT_ROOT,
            env=toolchain.go_env(),
        )
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        records.append({"wall_s": child["wall_s"], "peak_rss_bytes": child["peak_rss_bytes"], "payload": payload})
    return {
        "meta": {
            **BASELINE_META,
            "version": records[0]["payload"]["parser_version"],
            "chunker_version": records[0]["payload"]["chunker_version"],
            "go_version": records[0]["payload"]["go_version"],
        },
        "runs": records,
        "fixtures": {
            Path(entry["file"]).name: {
                "sha256": entry["sha256"],
                "compute_ns": entry["parse_ns"] + entry["chunk_ns"],
                "compute_ns_mean": entry.get("compute_ns_mean"),
                "iterations": entry.get("iterations"),
                "parse_ns": entry["parse_ns"],
                "chunk_ns": entry["chunk_ns"],
                "page_count": entry.get("page_count", 0),
                "pages": entry.get("pages") or [],
                "pages_without_text": entry.get("pages_without_text") or [],
                "chunks": entry.get("chunks") or [],
                "parse_error": entry.get("parse_error"),
            }
            for entry in records[0]["payload"]["fixtures"]
        },
    }


def measure_python_candidate(
    backend: str, fixtures: list[str], runs: int, workdir: Path
) -> dict:
    fixture_paths = [str(FIXTURES_DIR / name) for name in fixtures]
    records = []
    env = os.environ.copy()
    env["PYTHONPATH"] = str(HERE)
    for _run in range(runs):
        out_path = workdir / f"{backend}-run.json"
        child = run_child(
            [sys.executable, str(HERE / "candidate_worker.py"), "--candidate", backend,
             "--out", str(out_path), *fixture_paths],
            cwd=HERE,
            env=env,
        )
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        records.append({"wall_s": child["wall_s"], "peak_rss_bytes": child["peak_rss_bytes"], "payload": payload})
    meta = dict(candidates.CANDIDATES[backend])
    meta["version"] = records[0]["payload"]["version"]
    return {
        "meta": meta,
        "runs": records,
        "fixtures": {
            entry["file"]: {
                "sha256": entry["sha256"],
                "compute_ns": entry["compute_ns"],
                "page_count": len(entry["pages"]),
                "pages": entry["pages"],
                "pages_without_text": [
                    index + 1 for index, text in enumerate(entry["pages"]) if not text.strip()
                ],
                "chunks": entry["chunks"],
                "parse_error": None,
            }
            for entry in records[0]["payload"]["fixtures"]
        },
    }


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def score_candidate(candidate: dict, fixtures: dict) -> dict:
    runs = candidate["runs"]
    scored = {}
    for name, entry in candidate["fixtures"].items():
        reference = fixtures[name]
        # A parser refusal is measured as "this candidate produced no page text
        # and no chunk", not as a hardcoded zero: a fixture whose reference has no
        # text either (the scanned page) is then scored correctly as nothing lost.
        measurement = metrics.measure(
            reference,
            {"pages": entry.get("pages") or [], "chunks": entry.get("chunks") or []},
        )
        compute_ms = [
            run["payload"]["fixtures"]
            for run in runs
        ]
        per_run_compute = []
        for run_payload in compute_ms:
            for record in run_payload:
                if Path(record["file"]).name == name:
                    if record.get("compute_ns_mean"):
                        per_run_compute.append(record["compute_ns_mean"] / 1_000_000)
                    elif "compute_ns" in record:
                        per_run_compute.append(record["compute_ns"] / 1_000_000)
                    else:
                        per_run_compute.append(
                            (record["parse_ns"] + record["chunk_ns"]) / 1_000_000
                        )
        scored[name] = {
            "sha256": entry["sha256"],
            "parse_error": entry.get("parse_error"),
            **measurement,
            "chunks": entry["chunks"],
            "pages": entry["pages"],
            "pages_without_text": entry["pages_without_text"],
            "compute_ms_median": round(_median(per_run_compute), 3),
        }
    return {
        "meta": candidate["meta"],
        "runs": len(runs),
        "wall_s_median": round(_median([run["wall_s"] for run in runs]), 3),
        "wall_s_runs": [round(run["wall_s"], 3) for run in runs],
        "peak_rss_mb_median": round(_median([run["peak_rss_bytes"] for run in runs]) / (1024 * 1024), 2),
        "peak_rss_mb_runs": [
            round(run["peak_rss_bytes"] / (1024 * 1024), 2) for run in runs
        ],
        "fixtures": scored,
    }


GRADED_FIXTURES = ["t060_plain.pdf", "t060_layout.pdf", "t060_partial.pdf"]


def strict_quality_gain(candidate: dict, baseline: dict) -> dict:
    """Whether a candidate beats the baseline on any graded quality metric.

    The ticket allows eliminating a candidate that shows no benefit. This is the
    measured decision: on the frozen text fixtures it compares word coverage,
    exact page text and page attribution. A candidate that only matches the
    baseline (or does worse) has no quality benefit to justify its resource and
    licence cost.
    """
    gain = {"word_coverage": False, "page_exact_count": False, "page_attribution": False}
    details = {}
    for name in GRADED_FIXTURES:
        base = baseline.get(name)
        other = candidate.get(name)
        if base is None or other is None:
            continue
        details[name] = {
            "baseline_coverage": base.get("word_coverage"),
            "candidate_coverage": other.get("word_coverage"),
            "baseline_page_exact": base.get("page_exact_count"),
            "candidate_page_exact": other.get("page_exact_count"),
            "baseline_attribution": (base.get("page_attribution") or {}).get("ratio"),
            "candidate_attribution": (other.get("page_attribution") or {}).get("ratio"),
        }
        if (other.get("word_coverage") or 0) > (base.get("word_coverage") or 0) + 1e-9:
            gain["word_coverage"] = True
        if (other.get("page_exact_count") or 0) > (base.get("page_exact_count") or 0):
            gain["page_exact_count"] = True
        base_ratio = (base.get("page_attribution") or {}).get("ratio")
        other_ratio = (other.get("page_attribution") or {}).get("ratio")
        if base_ratio is not None and other_ratio is not None and other_ratio > base_ratio + 1e-9:
            gain["page_attribution"] = True
    return {"strict_gain": any(gain.values()), "metrics": gain, "fixtures": details}


def summarise(report: dict) -> None:
    fixtures = report["fixtures"]
    print("fixture               candidate                     coverage  page-exact  attribution  chunks  ms     RSS MB")
    for name in fixtures:
        for candidate in report["candidates"]:
            entry = candidate["fixtures"][name]
            attribution = entry["page_attribution"]["ratio"]
            attribution_text = "-" if attribution is None else f"{attribution:.2f}"
            print(
                f"{name:21} {candidate['meta']['label']:29} "
                f"{entry['word_coverage']:8.2f}  {entry['page_exact_count']}/{len(entry['page_exact']):<7}  "
                f"{attribution_text:>10}  {entry['chunk_stats']['count']:>6}  "
                f"{entry['compute_ms_median']:>6.2f}  {candidate['peak_rss_mb_median']:>6.2f}"
            )
    docling = report.get("docling") or {}
    print(f"docling: {docling.get('status', 'not_attempted')} — {docling.get('reason', '')}")
    if docling.get("status") == "measured" and docling.get("fixtures"):
        print(
            f"docling runtime: worker {docling.get('worker_elapsed_s')}s, "
            f"peak RSS {docling.get('peak_rss_mb')} MB, packages {docling.get('packages')}"
        )
        for name, fixture in docling["fixtures"].items():
            attribution = (fixture.get("page_attribution") or {}).get("ratio")
            print(
                f"{name:21} {'docling-native':29} "
                f"{fixture.get('word_coverage', 0):8.2f}  "
                f"{fixture.get('page_exact_count', 0)}/{fixture.get('page_count', 0):<7}  "
                f"{'-' if attribution is None else format(attribution, '.2f'):>10}  "
                f"{fixture.get('chunk_count', 0):>6}  {fixture.get('wall_s', 0):>6.2f}  "
                f"{docling.get('peak_rss_mb') or 0:>6.2f}"
            )
    for verdict in report.get("eliminations", []):
        print(f"decision {verdict['candidate']}: {verdict['decision']}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F10/T060 PDF parser/chunker comparison")
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--candidates",
        default="baseline,pymupdf,pypdf,pdfminer",
        help="comma-separated candidates to measure (baseline is always the registered parser)",
    )
    parser.add_argument(
        "--docling-attempt",
        type=Path,
        default=DEFAULT_DOCLING_ATTEMPT,
        help="the recorded isolated Docling attempt (status blocked|measured)",
    )
    args = parser.parse_args(argv)

    frozen = load_fixtures()
    fixtures = frozen["fixtures"]
    names = list(fixtures)
    workdir = Path(tempfile.mkdtemp(prefix="t060-compare-"))

    binary, _go_version = build_go_baseline()
    baseline = measure_baseline(binary, names, args.runs, workdir)
    selected = {item.strip() for item in args.candidates.split(",") if item.strip()}
    python_candidates = [
        measure_python_candidate(backend, names, args.runs, workdir)
        for backend in ("pymupdf", "pypdf", "pdfminer")
        if backend in selected
    ]
    candidates_report = [score_candidate(baseline, fixtures)] + [
        score_candidate(candidate, fixtures) for candidate in python_candidates
    ]

    docling: dict
    if args.docling_attempt.exists():
        docling = json.loads(args.docling_attempt.read_text(encoding="utf-8"))
    else:
        docling = {
            "status": "not_attempted",
            "reason": f"no recorded attempt at {args.docling_attempt}",
        }

    baseline_scored = candidates_report[0]["fixtures"]
    eliminations = []
    for scored in candidates_report[1:]:
        verdict = strict_quality_gain(scored["fixtures"], baseline_scored)
        eliminations.append(
            {
                "candidate": scored["meta"]["label"],
                "kind": scored["meta"]["kind"],
                "strict_quality_gain": verdict["strict_gain"],
                "metrics": verdict["metrics"],
                "fixtures": verdict["fixtures"],
                "decision": (
                    "not adopted: a measured strict quality gain exists; would need a new "
                    "parser/chunker revision and an explicit adoption decision"
                    if verdict["strict_gain"]
                    else "eliminated: no measured strict quality gain over the registered "
                    "baseline on the frozen text fixtures (baseline kept)"
                ),
            }
        )
    if docling.get("status") == "measured" and docling.get("fixtures"):
        docling_fixtures = {
            name: {
                **fixture,
                "page_exact_count": fixture.get("page_exact_count", 0),
                "page_attribution": fixture.get("page_attribution")
                or {"correct": 0, "total": 0, "ratio": None},
            }
            for name, fixture in docling["fixtures"].items()
        }
        verdict = strict_quality_gain(docling_fixtures, baseline_scored)
        eliminations.append(
            {
                "candidate": "docling-native",
                "kind": "requested-candidate",
                "docling": True,
                "strict_quality_gain": verdict["strict_gain"],
                "metrics": verdict["metrics"],
                "fixtures": verdict["fixtures"],
                "decision": (
                    "not adopted: a measured strict quality gain exists; would need a new "
                    "parser/chunker revision and an explicit adoption decision"
                    if verdict["strict_gain"]
                    else "eliminated: no measured strict quality gain over the registered "
                    "baseline on the frozen text fixtures (baseline kept)"
                ),
            }
        )

    report = {
        "ticket": "T060 (#61)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "environment": environment(),
        "min_text_runes": frozen["min_text_runes"],
        "reference": {
            "generator": "tests/fixtures/f10/build_pdf_fixtures.py",
            "note": "hand-checked page text per fixture; candidates are measured against it",
        },
        "fixtures": {
            name: {
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
                "page_count": entry["page_count"],
                "pages": entry["pages"],
                "pages_without_text": entry["pages_without_text"],
            }
            for name, entry in fixtures.items()
        },
        "candidates": candidates_report,
        "docling": docling,
        "eliminations": eliminations,
        "decision": (
            "Keep the registered baseline (orchara-pdf-parser/1 + "
            "orchara-pdf-page-paragraph-chunker/1). The Docling attempt and the "
            "available substitutes are recorded as measurements; none shows a "
            "strict quality gain on the frozen text fixtures, and all cost more "
            "resources (and different licences) than the baseline."
        ),
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    summarise(report)
    print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
