"""T102 / F13B: the frozen seven-category internal evaluation corpus.

What this suite freezes (offline: no PostgreSQL, no sandbox, no model):

* the seven category documents validate against the committed
  ``contracts/evaluation-case.schema.json`` and load through the product's own
  strict dataset parser (the Go test ``dataset_f13b_corpus_test.go`` covers the
  parser; this suite covers the schema);
* every case carries a family, a split, at least one assertion and traceable
  provenance/licence, and every case is described in ``registry.json`` with an
  independent-truth description and the facts collection must produce before the
  case can be decided;
* no family and no normalized template crosses dev/frozen_test, and the measured
  maximum cross-split near-duplicate similarity is below the reported threshold;
* the sales expectations are recomputed by the independent Decimal reference
  (never by a product tool); routing labels are checked against the committed
  catalog snapshots and RAG labels against the committed source snapshots;
* empty, skipped, incomplete and forged cases are rejected with stable codes.

Nothing here executes an evaluation case: the runner and a real model are
``not_run``. Schema validity is not a verdict.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

import f13b_corpus
from f13a.reference import sales_reference
from f13b import build_corpus

CORPUS_DIR = f13b_corpus.DEFAULT_CORPUS_DIR
REGISTRY_SCHEMA = "orchara.f13b-corpus/1"
NEAR_DUPLICATE_THRESHOLD = 0.75


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def corpus() -> f13b_corpus.Corpus:
    return f13b_corpus.load_corpus(CORPUS_DIR)


def get_case(corpus: f13b_corpus.Corpus, case_id: str) -> dict:
    for case in corpus.cases:
        if case["case_id"] == case_id:
            return case
    raise KeyError(case_id)


def entry_of(corpus: f13b_corpus.Corpus, case_id: str) -> dict:
    return corpus.registry["cases"][case_id]


def assertion(case: dict, kind: str, **match) -> dict:
    for item in case["assertions"]:
        if item["kind"] != kind:
            continue
        if all(item.get(key) == value for key, value in match.items()):
            return item
    raise KeyError((kind, match))


def run_state(case: dict) -> str:
    return assertion(case, "run_state")["expected"]


def issues_codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def cases_in(corpus: f13b_corpus.Corpus, category: str) -> list[dict]:
    return [case for case in corpus.cases if case["category"] == category]


# ---------------------------------------------------------------------------
# 1. seven category documents, schema validity, uniqueness
# ---------------------------------------------------------------------------
def test_the_corpus_is_seven_category_documents(corpus: f13b_corpus.Corpus) -> None:
    expected = {f"cases.{category}.json" for category in f13b_corpus.CATEGORIES}
    assert expected <= set(corpus.files)
    for filename in sorted(expected):
        document = corpus.files[filename]
        assert set(document) == {"cases"}, filename
        category = filename.split(".")[1]
        assert document["cases"], filename
        assert {case["category"] for case in document["cases"]} == {category}, filename
    assert set(corpus.registry["categories"]) == set(f13b_corpus.CATEGORIES)


def test_every_case_validates_against_the_committed_schema(
    corpus: f13b_corpus.Corpus,
) -> None:
    schema = json.loads(f13b_corpus.SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for case in corpus.cases:
        errors = sorted(validator.iter_errors(case), key=lambda error: list(error.path))
        assert not errors, (
            f"{case['case_id']} is not valid against evaluation-case.schema.json: "
            f"{[error.message for error in errors]}"
        )
        assert not issues_codes(f13b_corpus.validate_case(case)) & {"schema_violation"}


def test_the_whole_corpus_passes_cross_file_validation(
    corpus: f13b_corpus.Corpus,
) -> None:
    issues = f13b_corpus.validate_corpus(corpus)
    assert issues == [], "\n".join(str(issue) for issue in issues)


def test_case_ids_are_globally_unique(corpus: f13b_corpus.Corpus) -> None:
    ids = [case["case_id"] for case in corpus.cases]
    assert len(ids) == len(set(ids))
    assert len(ids) > 0
    assert set(corpus.registry["cases"]) == set(ids)


def test_every_category_has_dev_and_frozen_cases(corpus: f13b_corpus.Corpus) -> None:
    summary = f13b_corpus.summarize(corpus)
    assert set(summary) == set(f13b_corpus.CATEGORIES)
    for category, data in summary.items():
        assert data["dev"] >= 1, category
        assert data["frozen_test"] >= 1, category
        assert data["cases"] == data["dev"] + data["frozen_test"], category


def test_every_case_has_assertions_family_split_and_provenance(
    corpus: f13b_corpus.Corpus,
) -> None:
    for case in corpus.cases:
        assert case["assertions"], case["case_id"]
        assert case["family_id"].strip(), case["case_id"]
        assert case["split"] in f13b_corpus.SPLITS, case["case_id"]
        assert case["fixture_id"].strip(), case["case_id"]
        provenance = case["provenance"]
        assert provenance["origin"] in {"self_created", "adapted_external"}
        assert provenance["reference"].strip(), case["case_id"]
        assert provenance["license_review"] in {
            "not_applicable",
            "pending",
            "reviewed",
        }, case["case_id"]
        assert case["input"], case["case_id"]


# ---------------------------------------------------------------------------
# 2. registry pins: file bytes, schema bytes, per-case description
# ---------------------------------------------------------------------------
def test_registry_pins_every_corpus_file(corpus: f13b_corpus.Corpus) -> None:
    assert corpus.registry["schema"] == REGISTRY_SCHEMA
    assert corpus.registry["generated_by"].strip()
    for filename, document in corpus.files.items():
        pin = corpus.registry["files"][filename]
        payload = (CORPUS_DIR / filename).read_bytes()
        assert pin["sha256"] == hashlib.sha256(payload).hexdigest(), filename
        assert pin["size_bytes"] == len(payload), filename
        assert pin["cases"] == len(document["cases"]), filename


def test_registry_pins_the_committed_schema_bytes(corpus: f13b_corpus.Corpus) -> None:
    schema_bytes = f13b_corpus.SCHEMA_PATH.read_bytes()
    assert corpus.registry["schema_sha256"] == hashlib.sha256(schema_bytes).hexdigest()
    schema = json.loads(schema_bytes)
    assert schema["$id"] == "urn:agent-platform:evaluation-case:1.0"
    assert set(schema["properties"]["category"]["enum"]) == set(
        f13b_corpus.CATEGORIES
    )


def test_registry_describes_every_case(corpus: f13b_corpus.Corpus) -> None:
    for case in corpus.cases:
        case_id = case["case_id"]
        entry = entry_of(corpus, case_id)
        families = corpus.registry["families"]
        assert entry["category"] == case["category"], case_id
        assert entry["family_id"] == case["family_id"], case_id
        assert entry["split"] == case["split"], case_id
        assert entry["family_id"] in families, case_id
        assert entry["template"].strip(), case_id
        assert entry["template"] == families[entry["family_id"]]["template"], case_id
        assert families[entry["family_id"]]["split"] == case["split"], case_id
        assert entry["scenario"].strip(), case_id
        assert entry["provenance"] == case["provenance"], case_id
        assert entry["assertion_kinds"] == sorted(
            {item["kind"] for item in case["assertions"]}
        ), case_id
        truth = entry["truth"]
        assert truth["source"].strip(), case_id
        assert len(truth["derivation"].strip()) >= 20, case_id
        assert entry["required_facts"], case_id
        assert entry["required_facts"] == sorted(set(entry["required_facts"])), case_id
        assert entry["fixture_id"] == case["fixture_id"], case_id
        assert case["fixture_id"] in corpus.registry["fixtures"], case_id


def test_registry_category_summary_matches_the_case_files(
    corpus: f13b_corpus.Corpus,
) -> None:
    summary = f13b_corpus.summarize(corpus)
    for category, data in summary.items():
        entry = corpus.registry["categories"][category]
        assert entry["dev"] == data["dev"], category
        assert entry["frozen_test"] == data["frozen_test"], category
        assert entry["cases"] == data["cases"], category
        assert entry["assertions"] == data["assertions"], category
        assert entry["families"] == data["families"], category
        assert len(entry["families"]) >= 2, category


# ---------------------------------------------------------------------------
# 3. split isolation: family, template and measured near-duplicate similarity
# ---------------------------------------------------------------------------
def test_family_and_normalized_template_never_cross_splits(
    corpus: f13b_corpus.Corpus,
) -> None:
    report = f13b_corpus.split_leakage(corpus)
    assert report.cross_split_families == []
    assert report.cross_split_templates == []
    assert all(len(splits) == 1 for splits in report.family_splits.values())
    assert report.family_splits  # the metric is not vacuous


def test_the_measured_max_cross_split_near_duplicate_is_below_the_threshold(
    corpus: f13b_corpus.Corpus,
) -> None:
    report = f13b_corpus.split_leakage(corpus)
    assert report.threshold == NEAR_DUPLICATE_THRESHOLD
    assert report.ngram == 5
    assert report.cross_split_pairs_at_or_above_threshold == []
    assert report.max_cross_split_similarity < report.threshold
    # ...and the detector still discriminates: same-split rewrites (the CSV/XLSX
    # equivalence pair, the memory state-machine cases) score clearly higher than
    # any cross-split pair, so the low cross number is not a trivial metric.
    assert report.max_within_split_similarity > report.max_cross_split_similarity
    assert report.max_within_split_similarity >= 0.5


# ---------------------------------------------------------------------------
# 4. required collection facts, licence traceability
# ---------------------------------------------------------------------------
def test_required_facts_are_compatible_with_the_assertions(
    corpus: f13b_corpus.Corpus,
) -> None:
    collection = set(corpus.registry["collection"]["required_facts"])
    assert collection
    for case in corpus.cases:
        required = set(entry_of(corpus, case["case_id"])["required_facts"])
        assert collection <= required, case["case_id"]
        assert set(f13b_corpus.derive_required_facts(case)) <= required, case["case_id"]
        for item in case["assertions"]:
            if item["kind"] == "effect_count":
                assert item["source"] == "stub_ledger", case["case_id"]
                assert f"stub_ledger:{item['capability']}" in required, case["case_id"]
            if item["kind"] == "citation_sources":
                assert "citations.source_ids" in required, case["case_id"]


def test_licences_are_traceable_and_redistributable(corpus: f13b_corpus.Corpus) -> None:
    allowed_ids = {"LicenseRef-Orchara-SelfCreated", "not_applicable"}
    for case in corpus.cases:
        case_id = case["case_id"]
        licence = entry_of(corpus, case_id)["license"]
        assert licence["origin"] == case["provenance"]["origin"] == "self_created", case_id
        assert licence["license_id"] in allowed_ids, case_id
        assert licence["redistributable"] is True, case_id
        assert licence["review"] in {"not_applicable", "reviewed"}, case_id
        assert licence["review"] == case["provenance"]["license_review"], case_id
        assert licence["notes"].strip(), case_id


# ---------------------------------------------------------------------------
# 5. independent truth: sales (Decimal reference), routing catalog, RAG sources
# ---------------------------------------------------------------------------
def test_sales_truth_is_recomputed_by_the_independent_decimal_reference(
    corpus: f13b_corpus.Corpus,
) -> None:
    sales = cases_in(corpus, "sales")
    assert sales, "the sales corpus is empty"
    for case in sales:
        case_id = case["case_id"]
        fixture = corpus.registry["fixtures"][case["fixture_id"]]
        payload = build_corpus.fixture_bytes(fixture)
        assert hashlib.sha256(payload).hexdigest() == fixture["sha256"], case_id
        binding = case["input"]["task_input"]
        assert binding["sha256"] == fixture["sha256"], case_id
        assert binding["media_type"] == fixture["media_type"], case_id
        if "expected_failure" in case["input"]:
            with pytest.raises(sales_reference.FixtureRejected) as failure:
                sales_reference.summarize(fixture["media_type"], payload)
            assert failure.value.code == case["input"]["expected_failure"]["error_code"]
            assert run_state(case) == "failed", case_id
        else:
            summary = sales_reference.summarize(fixture["media_type"], payload)
            expected = {
                (item["kind"], item["pointer"]): item["expected"]
                for item in case["assertions"]
                if item["kind"] in {"decimal_equals", "json_equals"}
            }
            assert expected[("decimal_equals", "/summary/total")] == summary["total"]
            assert expected[("json_equals", "/summary/order_count")] == summary["order_count"]
            regions = {
                pointer.rsplit("/", 1)[-1]: value
                for (kind, pointer), value in expected.items()
                if kind == "decimal_equals" and pointer.startswith("/summary/regions/")
            }
            assert regions == summary["regions"], case_id
            assert run_state(case) == "succeeded", case_id
        for item in case["assertions"]:
            if item["kind"] == "effect_count":
                assert item["source"] == "stub_ledger"
                assert item["expected"] == 0


def test_sales_csv_and_xlsx_equivalence_pair_freezes_identical_expectations(
    corpus: f13b_corpus.Corpus,
) -> None:
    csv_case = get_case(corpus, "sales-dev-csv-summary")
    xlsx_case = get_case(corpus, "sales-dev-xlsx-summary")
    assert csv_case["family_id"] == xlsx_case["family_id"]
    assert csv_case["split"] == xlsx_case["split"] == "dev"

    def expectations(case: dict) -> dict:
        return {
            (item["kind"], item.get("pointer")): item.get("expected")
            for item in case["assertions"]
            if item["kind"] in {"decimal_equals", "json_equals"}
        }

    assert expectations(csv_case) == expectations(xlsx_case)
    csv_fixture = corpus.registry["fixtures"][csv_case["fixture_id"]]
    xlsx_fixture = corpus.registry["fixtures"][xlsx_case["fixture_id"]]
    assert csv_fixture["media_type"] == "text/csv"
    assert xlsx_fixture["media_type"].endswith("spreadsheetml.sheet")
    assert build_corpus.fixture_bytes(csv_fixture) != build_corpus.fixture_bytes(xlsx_fixture)


def test_rag_citation_truth_is_independent_of_the_product(
    corpus: f13b_corpus.Corpus,
) -> None:
    sources = corpus.registry["rag_sources"]
    rag = cases_in(corpus, "rag")
    assert rag
    for case in rag:
        case_id = case["case_id"]
        for item in case["assertions"]:
            if item["kind"] != "citation_sources":
                continue
            for source_id in item["allowed_source_ids"]:
                assert source_id in sources, case_id
                assert sources[source_id]["status"] == "active", (case_id, source_id)
        page = case["input"]
        if "required_source_id" in page:
            assert page["required_source_id"] in sources, case_id
        if "missing_source_id" in page:
            assert page["missing_source_id"] not in sources, case_id
        if "observed_citation" in page:
            assert page["observed_citation"] not in sources, case_id
        if "revoked_source_id" in page:
            assert sources[page["revoked_source_id"]]["status"] == "revoked", case_id
        if "old_source_id" in page:
            old = sources[page["old_source_id"]]
            new = sources[page["new_source_id"]]
            assert old["status"] == "revoked", case_id
            assert new["status"] == "active", case_id
            assert new["version"] > old["version"], case_id
        if "correct_fact" in page:
            text = sources[page["source_id"]]["text"]
            assert page["correct_fact"] in text, case_id
            assert page["observed_fact"] not in text, case_id


ROUTING_DECISION = {
    "unique_authorized_match": "auto_execute",
    "ambiguous_authorized_matches": "clarify",
    "incomplete_request": "clarify",
    "no_matching_application": "reject",
    "unseen_application_family": "reject",
    "unauthorized_candidate_only": "reject",
}


def routing_condition(request: dict, catalog: dict, requester: str) -> tuple[str, list[dict]]:
    """Derive the routing condition from the committed catalog, never from the label."""
    capability = request.get("required_capability")
    family = request.get("requested_family")
    if not capability or not family:
        return "incomplete_request", []
    applications = [app for app in catalog["applications"] if app["family"] == family]
    if not applications:
        return "unseen_application_family", []
    matching = [app for app in applications if capability in app["capabilities"]]
    if not matching:
        return "no_matching_application", []
    authorized = [app for app in matching if requester in app["granted_to"]]
    if not authorized:
        return "unauthorized_candidate_only", []
    if len(authorized) > 1:
        return "ambiguous_authorized_matches", authorized
    return "unique_authorized_match", authorized


def test_routing_labels_match_the_committed_catalog_conditions(
    corpus: f13b_corpus.Corpus,
) -> None:
    catalogs = corpus.registry["routing_catalogs"]
    routing = cases_in(corpus, "routing")
    assert routing
    for case in routing:
        case_id = case["case_id"]
        page = case["input"]
        catalog = catalogs[page["catalog_version"]]
        assert catalog["split"] == case["split"], case_id
        condition, authorized = routing_condition(
            page["request"], catalog, page["requester"]["id"]
        )
        truth = entry_of(corpus, case_id)["truth"]
        assert truth["condition"] == condition, case_id
        decision = assertion(case, "json_equals", pointer="/routing/decision")["expected"]
        assert decision == ROUTING_DECISION[condition], case_id
        selected = assertion(
            case, "json_equals", pointer="/routing/selected_application_id"
        )["expected"]
        if condition == "unique_authorized_match":
            assert selected == authorized[0]["application_id"], case_id
        else:
            assert selected is None, case_id


# ---------------------------------------------------------------------------
# 6. effect/state truth invariants for delegation, approval and lifecycle
# ---------------------------------------------------------------------------
def test_effect_and_state_labels_are_internally_consistent(
    corpus: f13b_corpus.Corpus,
) -> None:
    for case in corpus.cases:
        for item in case["assertions"]:
            if item["kind"] == "effect_count":
                assert item["source"] == "stub_ledger", case["case_id"]
                assert item["expected"] >= 0, case["case_id"]

    duplicate = get_case(corpus, "delegation-dev-duplicate-delivery")
    assert duplicate["input"]["delivery_count"] == 2
    assert (
        assertion(duplicate, "effect_count", capability="delegation.child_dispatch")["expected"]
        == 1
    )

    budget = get_case(corpus, "delegation-frozen-parent-budget")
    assert run_state(budget) == "failed"
    assert (
        assertion(budget, "effect_count", capability="delegation.child_dispatch")["expected"]
        == 0
    )

    lost = get_case(corpus, "approval-frozen-lost-receipt")
    assert run_state(lost) == "needs_reconciliation"
    assert assertion(lost, "effect_count", capability="test.send")["expected"] == 1

    cancelled = get_case(corpus, "approval-frozen-cancel")
    assert run_state(cancelled) == "cancelled"
    assert assertion(cancelled, "effect_count", capability="test.send")["expected"] == 0

    expired = get_case(corpus, "approval-frozen-expired")
    assert run_state(expired) == "failed"
    assert assertion(expired, "effect_count", capability="test.send")["expected"] == 0

    for case in cases_in(corpus, "lifecycle"):
        assert assertion(case, "effect_count", capability="test.send")["expected"] == 0


# ---------------------------------------------------------------------------
# 7. the frozen bytes are reproducible from the committed builder
# ---------------------------------------------------------------------------
def test_the_registry_and_case_documents_are_reproducible() -> None:
    built = build_corpus.build()
    assert set(built) == {
        *{f"cases.{category}.json" for category in f13b_corpus.CATEGORIES},
        "registry.json",
    }
    for filename, payload in built.items():
        committed = (CORPUS_DIR / filename).read_bytes()
        assert payload == committed, (
            f"{filename} drifted from tests/f13b/build_corpus.py; "
            "run `python tests/f13b/build_corpus.py --write`"
        )


# ---------------------------------------------------------------------------
# 8. negative rejections: empty, skipped, incomplete, forged
# ---------------------------------------------------------------------------
def _semantic_mutations() -> list[tuple[str, str, str]]:
    """(name, callable name, expected reason code) applied to a valid case."""
    return [
        ("empty_assertions", "empty_assertions", "empty_assertions"),
        ("empty_input", "empty_input", "empty_input"),
        ("skipped_case", "skipped", "skipped_case"),
        ("missing_license_review", "drop_license_review", "missing_license_review"),
        ("missing_provenance", "drop_provenance", "missing_provenance"),
        ("unsupported_category", "bad_category", "unsupported_category"),
        ("invalid_split", "bad_split", "invalid_split"),
        ("missing_fixture_id", "drop_fixture", "missing_fixture_id"),
    ]


def _mutate(name: str, case: dict) -> None:
    if name == "empty_assertions":
        case["assertions"] = []
    elif name == "empty_input":
        case["input"] = {}
    elif name == "skipped":
        case["skipped"] = True
    elif name == "drop_license_review":
        case["provenance"].pop("license_review")
    elif name == "drop_provenance":
        case.pop("provenance")
    elif name == "bad_category":
        case["category"] = "salesforce"
    elif name == "bad_split":
        case["split"] = "train"
    elif name == "drop_fixture":
        case.pop("fixture_id")
    else:  # pragma: no cover - guarded by the parametrisation list
        raise AssertionError(name)


@pytest.mark.parametrize(
    ("name", "mutation", "expected"),
    _semantic_mutations(),
    ids=[item[0] for item in _semantic_mutations()],
)
def test_reject_reasons_refuse_broken_cases(
    corpus: f13b_corpus.Corpus, name: str, mutation: str, expected: str
) -> None:
    broken = copy.deepcopy(get_case(corpus, "sales-dev-csv-summary"))
    _mutate(mutation, broken)
    assert expected in f13b_corpus.reject_reasons(broken)
    assert f13b_corpus.validate_case(broken), name


def test_every_frozen_case_is_decidable(corpus: f13b_corpus.Corpus) -> None:
    """The 46 frozen cases must be decidable, not merely schema-valid.

    The first implementation required *every* category's fact on every case,
    so ``is_decidable`` reported 0/46 even though each registry entry declares
    its own category fact plus the collection/assertion facts. A corpus that
    cannot produce a verdict for any case is not a frozen acceptance corpus.
    """
    assert len(corpus.cases) == 46
    undecidable = {}
    for case in corpus.cases:
        ok, reasons = f13b_corpus.is_decidable(
            case, corpus.registry["cases"].get(case["case_id"])
        )
        if not ok:
            undecidable[case["case_id"]] = reasons
    assert undecidable == {}, undecidable
    # And the category fact really is the one the case's own category names.
    for case in corpus.cases:
        registry_entry = corpus.registry["cases"][case["case_id"]]
        assert f13b_corpus.CATEGORY_FACTS[case["category"]] in registry_entry[
            "required_facts"
        ], case["case_id"]


def test_empty_or_incomplete_cases_are_not_decidable(
    corpus: f13b_corpus.Corpus,
) -> None:
    case = get_case(corpus, "sales-dev-csv-summary")
    registry_entry = entry_of(corpus, "sales-dev-csv-summary")

    empty = copy.deepcopy(case)
    empty["assertions"] = []
    decidable, reasons = f13b_corpus.is_decidable(empty, registry_entry)
    assert decidable is False
    assert "empty_assertions" in reasons or any("assertion" in reason for reason in reasons)

    incomplete = copy.deepcopy(registry_entry)
    incomplete["required_facts"] = ["run.id"]
    decidable, reasons = f13b_corpus.is_decidable(case, incomplete)
    assert decidable is False
    assert any(reason.startswith("missing_fact:") for reason in reasons)


def test_validate_corpus_rejects_a_forged_cross_split_family(
    corpus: f13b_corpus.Corpus,
) -> None:
    mutated = copy.deepcopy(corpus)
    frozen = get_case(mutated, "sales-frozen-boundary-decimals")
    frozen["family_id"] = "sales-file-summary"
    mutated.registry["cases"][frozen["case_id"]]["family_id"] = "sales-file-summary"
    assert "cross_split_family" in issues_codes(f13b_corpus.validate_corpus(mutated))


def test_validate_corpus_rejects_a_forged_cross_split_template(
    corpus: f13b_corpus.Corpus,
) -> None:
    mutated = copy.deepcopy(corpus)
    frozen = get_case(mutated, "sales-frozen-boundary-decimals")
    dev_template = mutated.registry["families"]["sales-file-summary"]["template"]
    mutated.registry["cases"][frozen["case_id"]]["template"] = dev_template
    assert "cross_split_template" in issues_codes(f13b_corpus.validate_corpus(mutated))


def test_validate_corpus_rejects_a_near_duplicate_across_splits(
    corpus: f13b_corpus.Corpus,
) -> None:
    mutated = copy.deepcopy(corpus)
    dev = get_case(mutated, "sales-dev-csv-summary")
    frozen = get_case(mutated, "sales-frozen-boundary-decimals")
    frozen["input"] = copy.deepcopy(dev["input"])
    frozen["fixture_id"] = dev["fixture_id"]
    mutated.registry["cases"][frozen["case_id"]]["scenario"] = mutated.registry["cases"][
        dev["case_id"]
    ]["scenario"]
    report = f13b_corpus.split_leakage(mutated)
    assert report.cross_split_pairs_at_or_above_threshold, report
    assert "near_duplicate_leak" in issues_codes(f13b_corpus.validate_corpus(mutated))


def test_validate_corpus_rejects_a_duplicate_case_id(
    corpus: f13b_corpus.Corpus,
) -> None:
    mutated = copy.deepcopy(corpus)
    duplicate = copy.deepcopy(get_case(mutated, "sales-dev-csv-summary"))
    mutated.files["cases.routing.json"]["cases"].append(duplicate)
    assert "duplicate_case_id" in issues_codes(f13b_corpus.validate_corpus(mutated))


def test_validate_corpus_rejects_missing_registry_descriptions(
    corpus: f13b_corpus.Corpus,
) -> None:
    missing_entry = copy.deepcopy(corpus)
    missing_entry.registry["cases"].pop("sales-dev-csv-summary")
    assert "registry_missing_case" in issues_codes(
        f13b_corpus.validate_corpus(missing_entry)
    )

    missing_licence = copy.deepcopy(corpus)
    missing_licence.registry["cases"]["sales-dev-csv-summary"].pop("license")
    assert "missing_license" in issues_codes(
        f13b_corpus.validate_corpus(missing_licence)
    )


def test_validate_corpus_rejects_incomplete_collection_facts(
    corpus: f13b_corpus.Corpus,
) -> None:
    no_collection_fact = copy.deepcopy(corpus)
    facts = no_collection_fact.registry["cases"]["sales-dev-csv-summary"]["required_facts"]
    no_collection_fact.registry["cases"]["sales-dev-csv-summary"]["required_facts"] = [
        fact for fact in facts if fact != "collection.complete"
    ]
    assert "collection_incomplete" in issues_codes(
        f13b_corpus.validate_corpus(no_collection_fact)
    )

    missing_derived = copy.deepcopy(corpus)
    derived = missing_derived.registry["cases"]["sales-dev-csv-summary"]["required_facts"]
    missing_derived.registry["cases"]["sales-dev-csv-summary"]["required_facts"] = [
        fact for fact in derived if fact != "run.terminal_state"
    ]
    assert "facts_incompatible_with_assertions" in issues_codes(
        f13b_corpus.validate_corpus(missing_derived)
    )


def test_validate_corpus_rejects_tampered_pins(corpus: f13b_corpus.Corpus) -> None:
    bad_file = copy.deepcopy(corpus)
    bad_file.registry["files"]["cases.sales.json"]["sha256"] = "0" * 64
    assert "file_pin_mismatch" in issues_codes(f13b_corpus.validate_corpus(bad_file))

    bad_schema = copy.deepcopy(corpus)
    bad_schema.registry["schema_sha256"] = "0" * 64
    assert "schema_pin_mismatch" in issues_codes(f13b_corpus.validate_corpus(bad_schema))
