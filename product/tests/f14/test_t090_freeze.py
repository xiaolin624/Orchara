"""T090 (#91, F14): the freeze manifest and the leakage checks are real assertions.

These tests are pure Python over committed files: no database, no stack, no model.
They fail when a byte of the T084 frozen corpus, the frozen catalog card
definitions, the T084 evidence anchor or the manifest itself drifts.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from f08.routing import corpus, runner
from f14 import routing_freeze as freeze

PRODUCT_ROOT = freeze.PRODUCT_ROOT


def _manifest() -> dict:
    return freeze.load_manifest()


def test_the_manifest_freezes_every_corpus_byte() -> None:
    manifest = _manifest()
    measured = freeze.corpus_facts()
    assert manifest["corpus"] == measured, (
        "the frozen routing corpus drifted from T090-freeze.json; regenerate with "
        "`python -m tests.f14.routing_freeze` only after an explicit re-freeze decision"
    )
    assert measured["case_count"] == 34
    assert measured["split_counts"]["frozen_test"]["cases"] == 25
    assert measured["split_counts"]["dev"]["cases"] == 9
    assert measured["held_out"]["tuning_allowed"] is False
    # Every hash is a real recomputation of the bytes on disk, not a copy of the
    # registry's own claim: mutate nothing and the two agree.
    for entry in measured["files"]:
        path = corpus.DATASETS / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        assert len(path.read_bytes()) == entry["size"]


def test_the_t084_evidence_anchor_still_matches_the_corpus() -> None:
    manifest = _manifest()
    measured = freeze.corpus_facts()
    anchor = freeze.t084_anchor()
    assert manifest["evidence_anchor"] == anchor
    assert anchor["registry_sha256"] == measured["registry_sha256"]
    assert anchor["dataset_digest"] == measured["dataset_digest"]
    assert anchor["case_count"] == measured["case_count"]
    document = json.loads(freeze.T084_REPORT.read_text(encoding="utf-8"))
    assert document["corpus"]["registry_sha256"] == measured["registry_sha256"]
    assert document["corpus"]["dataset_digest"] == measured["dataset_digest"]
    assert document["environment"]["git_head"] == anchor["git_head"]


def test_one_family_lives_in_exactly_one_split_and_cell() -> None:
    manifest = _manifest()
    cases = corpus.load_cases()
    measured = freeze.family_isolation(cases)
    assert manifest["leakage"]["family_isolation"] == measured
    assert measured["violations"] == []
    assert measured["family_count"] == 26  # 18 frozen_test + 8 dev, per T084
    frozen = [entry for entry in measured["families"].values() if entry["split"] == "frozen_test"]
    dev = [entry for entry in measured["families"].values() if entry["split"] == "dev"]
    assert len(frozen) == 18 and len(dev) == 8
    # A family never appears in two cells either (a cell shift is a leak of the
    # intended decision to the other cell).
    cells = {entry["cell"] for entry in measured["families"].values()}
    assert cells == set(corpus.CELLS)


def test_no_cross_split_message_repeats_even_after_normalisation() -> None:
    manifest = _manifest()
    similarity = manifest["leakage"]["similarity"]
    cases = corpus.load_cases()
    measured = freeze.similarity_report(cases)
    assert similarity == measured, "the leakage measurements drifted from the frozen manifest"
    assert similarity["exact_duplicate_pairs"] == []
    assert similarity["normalized_duplicate_pairs"] == []
    # The normaliser must actually normalise: prove it on a pair we construct.
    assert freeze.normalize_message("  Summarise, THIS csv!  ") == "summarise this csv"


def test_near_duplicate_threshold_is_pre_registered_and_reviewed() -> None:
    manifest = _manifest()
    similarity = manifest["leakage"]["similarity"]
    assert similarity["threshold"] == freeze.NEAR_DUPLICATE_THRESHOLD == 0.75
    assert similarity["pairs_compared"] == 9 * 25 == 225
    assert similarity["max_cross_split_similarity"] == max(
        pair["jaccard"] for pair in similarity["top_pairs"]
    )
    cases = corpus.load_cases()
    measured = freeze.similarity_report(cases)
    assert measured["near_duplicate_pairs"] == similarity["near_duplicate_pairs"]
    assert measured["max_cross_split_similarity"] == similarity["max_cross_split_similarity"]
    for pair in similarity["near_duplicate_pairs"]:
        reviewed = {item["pair"] for item in manifest["leakage"]["manual_review"]["reviewed"]}
        assert f"{pair['dev']}|{pair['frozen_test']}" in reviewed, (
            f"cross-split near duplicate {pair} was never reviewed"
        )
    if not similarity["near_duplicate_pairs"]:
        assert manifest["leakage"]["manual_review"]["required"] is False
    # The similarity tokenizer must not be the router's matcher (circularity guard).
    assert "messageTokens" in similarity["tokenizer"]
    assert freeze.similarity_tokens("summarise this csv") == {"summarise", "this", "csv"}


def test_provenance_and_licence_fields_are_complete() -> None:
    manifest = _manifest()
    cases = corpus.load_cases()
    measured = freeze.provenance_report(cases)
    assert manifest["leakage"]["provenance"] == measured
    assert measured["violations"] == []
    assert measured["origins"] == {"self_created": 34}
    assert measured["license_reviews"] == {"not_applicable": 34}
    # The one imported byte source is the public repository sample and it is inside
    # this repository (the corpus holds a byte-identical copy; registry pins it).
    sample = (PRODUCT_ROOT / "docs/spec/examples/sales/input.csv").read_bytes()
    frozen_copy = (corpus.DATASETS / "inputs/sales_sample.csv").read_bytes()
    assert sample == frozen_copy, "the corpus attachment is no longer the repository sample"


def test_catalog_card_definitions_are_frozen() -> None:
    manifest = _manifest()
    measured = freeze.catalog_facts()
    assert manifest["catalog"] == measured
    fixtures = freeze.fixture_card_definitions()
    frozen_cards = {**fixtures, "sales": freeze.T084_SALES_CARD}
    probe_cards = {**fixtures, "sales": freeze.SALES_CARD}
    for name, cards in (
        ("frozen_t084_catalog", frozen_cards),
        ("product_template_card_probe", probe_cards),
    ):
        entry = measured[name]
        assert entry["definitions_sha256"] == freeze.sha256_bytes(
            freeze.canonical_json(cards).encode("utf-8")
        ), name
        for key, card in cards.items():
            assert entry["cards"][key]["card_sha256"] == freeze.card_sha256(card), (name, key)
    # T084's catalog card is the generic card `t068.sales_application` saved, and
    # the T084 report records exactly that purpose; this anchors the arm to T084
    # instead of a remembered card.
    from support import f02a

    assert freeze.T084_SALES_CARD == f02a.full_card()
    t084_report = json.loads(freeze.T084_REPORT.read_text(encoding="utf-8"))
    assert t084_report["catalog"]["sales"]["purpose"] == freeze.T084_SALES_CARD["purpose"]
    # The product template card is a product constant: every string must still be
    # in the Go source, else the frozen probe card content is stale.
    source = (PRODUCT_ROOT / "internal/templates/templates.go").read_text(encoding="utf-8")
    for field, values in freeze.SALES_CARD.items():
        for value in values if isinstance(values, list) else [values]:
            assert value in source, f"sales card {field} value is no longer in templates.go: {value!r}"
    # runner.FIXTURE_APPS is the same frozen definition the T084 evaluator publishes.
    for key, spec in runner.FIXTURE_APPS.items():
        assert fixtures[key] == {field: spec[field] for field in freeze.CARD_FIELDS}


def test_retrieval_is_explicitly_not_used_not_fabricated() -> None:
    manifest = _manifest()
    retrieval = manifest["retrieval"]
    assert retrieval["not_used"] is True
    for field in (
        "embedding_model_revision",
        "chunker_version",
        "kb_document_revision",
        "index_revision",
        "acl_epoch",
    ):
        assert retrieval[field] is None, f"{field} must stay null when no retrieval runs"
    assert "no knowledge-base" in retrieval["reason"].lower()
    assert "catalog.definitions_sha256" in retrieval["what_is_frozen_instead"]


def test_the_control_model_is_labelled_as_a_stub_not_a_model() -> None:
    control = _manifest()["control_model"]
    assert control["provider"] == "stub"
    assert control["stub"] is True
    assert control["validation_state"] == "passed"
    assert control["semantic_baseline_allowed"] is False
    assert "not a vendor model" in control["note"]


def test_the_strong_prompt_arm_and_training_are_blocked_not_run() -> None:
    manifest = _manifest()
    strong = manifest["strong_prompt_arm"]
    assert strong["status"] == "blocked"
    assert strong["executed"] is False
    joined = " ".join(strong["not_run"]).lower()
    for missing in ("credential", "endpoint", "weights", "gpu", "budget"):
        assert missing in joined, f"the blocked arm must name the missing {missing}"
    training = manifest["training"]
    assert training["status"] == "not_run"
    assert training["executed"] is False
    assert "no training-capable gpu" in training["reason"].lower()
    # Honesty: the manifest never turns the missing arm into a measured result.
    text = json.dumps(manifest, ensure_ascii=False).lower()
    for claim in ("training ran", "training_ran", "strong_prompt_arm\": {\"status\": \"passed"):
        assert claim not in text


def test_the_manifest_environment_is_recorded() -> None:
    environment = _manifest()["environment"]
    assert re.fullmatch(r"[0-9a-f]{40}", environment["git_head"]), environment["git_head"]
    assert environment["python"], environment
    assert environment["os"], environment
    assert environment["profile"] == "dev-stub (the only model path on this host; no vendor credential)"


def test_the_baseline_report_keeps_the_blocked_labels() -> None:
    if not freeze.T090_REPORT.exists():
        pytest.fail(
            "reports/f14/T090-routing-baseline.json is missing; run the bounded "
            "baseline evidence run with T090_ROUTING_REPORT set"
        )
    report = json.loads(freeze.T090_REPORT.read_text(encoding="utf-8"))
    assert report["schema"] == "orchara.t090-routing-baseline/1"
    assert report["status"] in ("blocked", "partial")
    assert report["strong_prompt_arm"]["status"] == "blocked"
    assert report["strong_prompt_arm"]["executed"] is False
    assert report["training"]["status"] == "not_run"
    assert report["training"]["executed"] is False
    assert report["control_model"]["stub"] is True
    assert report["coverage"]["strong_prompt_baseline"] == "not_run"
    assert report["coverage"]["training"] == "not_run"
    text = json.dumps(report, ensure_ascii=False).lower()
    for forbidden in ("training ran", '"training_ran": true', '"strong_prompt_arm": {"status": "passed'):
        assert forbidden not in text
