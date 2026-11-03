"""F04A/T040: the LangGraph linear proof, executed under the P01 interpreter.

This is the only real graph execution evidence for T040. It runs a compiled
LangGraph ``StateGraph`` (start -> python -> agent -> end) through the stub
``NodeAdapter`` seam, so it proves declared data flow, failure short-circuiting
and deterministic reruns without claiming a production worker exists.

The subprocess boundary exists because the pytest interpreter is not necessarily
the P01 locked environment; a missing P01 interpreter fails the test (see
conftest) instead of skipping it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "compiler_runner.py"


def run_compiler(python: Path) -> dict:
    completed = subprocess.run(
        [str(python), str(RUNNER)],
        cwd=HERE,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_valid_linear_graph_runs_in_declared_order(graph_python: Path) -> None:
    payload = run_compiler(graph_python)
    result = payload["first"]
    assert result["status"] == "succeeded"
    records = result["records"]
    assert [record["node_id"] for record in records] == [
        "start", "extract", "summarise", "end",
    ]
    assert [record["status"] for record in records] == ["succeeded"] * 4
    assert result["outputs"] == {"summary": "summarise: sales-2026.csv"}
    # Declared data flow: python consumed the run input through input#/file,
    # agent consumed the python output, end mapped the application output.
    assert records[1]["inputs"] == {"text": "sales-2026.csv"}
    assert records[2]["inputs"] == {"text": "sales-2026.csv"}
    assert records[3]["inputs"] == {"summary": "summarise: sales-2026.csv"}


def test_repeated_execution_is_deterministic(graph_python: Path) -> None:
    payload = run_compiler(graph_python)
    assert payload["deterministic"] is True
    assert payload["first"] == payload["second"]


def test_failed_node_stops_the_chain(graph_python: Path) -> None:
    failed = run_compiler(graph_python)["failed"]
    assert failed["status"] == "failed"
    assert failed["failure_code"] == "sandbox_error"
    assert failed["failure_node_id"] == "extract"
    assert [record["node_id"] for record in failed["records"]] == ["start", "extract"]


def test_waiting_outcome_is_normalized_and_stops(graph_python: Path) -> None:
    waiting = run_compiler(graph_python)["waiting"]
    assert waiting["status"] == "waiting"
    assert waiting["failure_node_id"] == "summarise"
    assert [record["node_id"] for record in waiting["records"]] == [
        "start", "extract", "summarise",
    ]


def test_output_that_violates_the_declared_schema_is_refused(graph_python: Path) -> None:
    bad = run_compiler(graph_python)["bad_output"]
    assert bad["status"] == "failed"
    assert bad["failure_code"] == "output_mismatch"
    assert bad["failure_node_id"] == "summarise"


def test_graph_builder_refuses_non_linear_documents(graph_python: Path) -> None:
    refusals = run_compiler(graph_python)["refusals"]
    expected = {
        "missing-end": "start_end_count",
        "unknown-type": "unknown_type",
        "cycle": "cycle",
        "merge-branch": "merge_without_join",
        "fork-branch": "merge_without_join",
        "duplicate-edge": "not_linear_chain",
        "extra-port": "unsupported_port",
        "wrong-order": "linear_order",
        "unsupported-node-type": "unsupported_node_type",
    }
    for name, code in expected.items():
        assert refusals[name]["refused"] is True, name
        assert refusals[name]["code"] == code, name


def test_graph_validation_is_go_corpus_parity(graph_python: Path, corpus: dict) -> None:
    payload = run_compiler(graph_python)
    for case in corpus["cases"]:
        assert payload["validation"][case["name"]]["verdict"] == case["expect"]["verdict"]
        assert payload["validation"][case["name"]]["problems"] == case["expect"]["problems"]
