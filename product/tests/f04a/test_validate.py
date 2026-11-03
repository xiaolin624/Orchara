"""F04A/T040: the Python defensive validator agrees with the Go authority.

The corpus is the shared fixture ``corpus/linear_f04a.json``. Its ``expect``
fields were recorded from the Go validator (``workflow.Validate``) and are
asserted on the Go side by ``internal/workflow/f04a_corpus_test.go``; here the
same expectations are asserted against the Python copy. Shared cases must match
the Go reason code *and* pointer exactly. F04A-only cases pin the refusals this
linear runtime adds on top of the ten-node contract.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from platform_worker.compiler import validate as validate_definition


def test_every_case_matches_its_recorded_expectation(corpus: dict) -> None:
    assert len(corpus["cases"]) >= 15
    for case in corpus["cases"]:
        result = validate_definition(case["document"])
        assert result.verdict == case["expect"]["verdict"], case["name"]
        problems = [problem.as_wire() for problem in result.problems]
        assert problems == case["expect"]["problems"], case["name"]


def test_the_shared_corpus_contains_valid_and_invalid_cases(corpus: dict) -> None:
    shared = [case for case in corpus["cases"] if case["scope"] == "shared"]
    verdicts = {case["expect"]["verdict"] for case in shared}
    assert verdicts == {"valid", "invalid"}
    assert sum(1 for case in shared if case["expect"]["verdict"] == "valid") == 1


def test_valid_result_canonicalises_and_hashes_the_document(corpus: dict) -> None:
    document = next(c for c in corpus["cases"] if c["name"] == "valid-linear")["document"]
    result = validate_definition(document)
    assert result.problems == ()
    assert result.data_canonical is not None
    assert json.loads(result.data_canonical) == document
    assert result.data_sha256 == hashlib.sha256(
        result.data_canonical.encode("utf-8")
    ).hexdigest()
    wire = result.as_wire()
    assert wire["verdict"] == "valid"
    assert wire["data_sha256"] == result.data_sha256


def test_validation_is_deterministic(corpus: dict) -> None:
    document = corpus["cases"][0]["document"]
    first = validate_definition(document).as_wire()
    second = validate_definition(json.loads(json.dumps(document))).as_wire()
    assert first == second


def test_input_hash_is_stable_across_key_order(corpus: dict) -> None:
    document = next(c for c in corpus["cases"] if c["name"] == "valid-linear")["document"]
    reordered = {key: document[key] for key in reversed(list(document))}
    assert validate_definition(document).input_sha256 == validate_definition(reordered).input_sha256


def test_unsupported_f04b_type_is_refused_with_a_stable_reason(corpus: dict) -> None:
    case = next(c for c in corpus["cases"] if c["name"] == "unsupported-node-type")
    result = validate_definition(case["document"])
    assert result.reason == "unsupported_node_type"
    assert result.problems[0].pointer == "/nodes/1"


def test_missing_required_output_is_located(corpus: dict) -> None:
    document = json.loads(
        json.dumps(next(c for c in corpus["cases"] if c["name"] == "valid-linear")["document"])
    )
    del document["nodes"][3]["inputs"]["summary"]
    result = validate_definition(document)
    assert result.reason == "missing_value"
    assert result.problems[0].pointer == "/nodes/3/inputs/summary"


def test_a_branch_is_refused_even_when_the_graph_shape_is_other_valid(corpus: dict) -> None:
    document = json.loads(
        json.dumps(next(c for c in corpus["cases"] if c["name"] == "valid-linear")["document"])
    )
    document["edges"].append({"source": "extract", "target": "end"})
    result = validate_definition(document)
    assert result.verdict == "invalid"
    assert result.problems[0].code == "merge_without_join"
    assert result.problems[0].pointer == "/nodes/3"


def test_non_object_documents_are_refused() -> None:
    for document in ([], "text", None, 3):
        result = validate_definition(document)
        assert result.verdict == "invalid"
        assert result.problems[0].code == "bad_document"


@pytest.mark.parametrize(
    "mutation, code",
    [
        (lambda d: d.update(dsl_version="2.0"), "bad_document"),
        (lambda d: d["policy"].update(budget_limit="1.2.3"), "bad_policy"),
        (lambda d: d["nodes"][2]["config"].update(max_turns=0), "bad_config"),
        (lambda d: d["nodes"][1]["config"].update(tool_version="not-a-uuid"), "bad_config"),
        (lambda d: d["nodes"][1]["output_schema"].update(type="array"), "bad_schema"),
        (lambda d: d["edges"][0].update(extra="x"), "bad_edge"),
        (lambda d: d["nodes"][0]["inputs"].update(file={"ref": "input#/file"}), "bad_binding"),
        (lambda d: d["nodes"][1]["inputs"].update(text={"ref": "node:summarise#/summary"}),
         "non_ancestor_ref"),
    ],
)
def test_negative_mutations_are_located(corpus: dict, mutation, code: str) -> None:
    document = json.loads(
        json.dumps(next(c for c in corpus["cases"] if c["name"] == "valid-linear")["document"])
    )
    mutation(document)
    result = validate_definition(document)
    assert result.verdict == "invalid"
    assert result.problems[0].code == code
    assert result.problems[0].pointer != ""
