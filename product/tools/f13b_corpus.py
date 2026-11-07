"""F13B/T102: loader, validator, split-leak detector and rejection reasons for
the frozen seven-category internal evaluation corpus.

The corpus lives in ``product/tests/f13b/corpus``: one ``cases.<category>.json``
document per category (each ``{"cases": [...]}``, the exact shape
``internal/evaluation/dataset.go`` parses) plus ``registry.json``. The registry
is the "public truth and licence are traceable" artifact: it pins the schema
bytes, pins every corpus file by SHA-256/size, and describes every case with its
family, template, scenario, independent-truth derivation, licence and the
collection facts a case must produce before it may be decided.

Nothing in this module executes a case. It answers four mechanical questions:

* does the document match the committed JSON schema (``validate_case``);
* is the corpus internally complete and traceable (``validate_corpus``);
* does a family, a normalized template or a near-duplicate case cross
  dev/frozen_test (``split_leakage``);
* is a case decidable at all, or is it empty/skipped/incomplete
  (``reject_reasons``/``is_decidable``).

The module is offline: standard library plus ``jsonschema``. No product tool,
no model, no database.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import jsonschema

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_DIR = PRODUCT_ROOT / "tests" / "f13b" / "corpus"
SCHEMA_PATH = PRODUCT_ROOT / "contracts" / "evaluation-case.schema.json"

REGISTRY_SCHEMA = "orchara.f13b-corpus/1"
CATEGORIES = (
    "sales",
    "routing",
    "rag",
    "memory",
    "delegation",
    "approval_effects",
    "lifecycle",
)
SPLITS = ("dev", "frozen_test")
NEAR_DUPLICATE_THRESHOLD = 0.75
NEAR_DUPLICATE_NGRAM = 5

#: Every case must collect these before any assertion may be decided. A missing
#: fact is ``not_run``/incomplete, never a pass.
COLLECTION_FACTS = (
    "collection.complete",
    "eval.artifact_manifest",
    "run.id",
    "run.input_sha256",
)

#: Category-specific fact the truth of a case depends on (documented in the
#: registry so a collector knows what to snapshot).
CATEGORY_FACTS = {
    "sales": "fixture.sha256",
    "routing": "routing.catalog_snapshot",
    "rag": "rag.source_index_snapshot",
    "memory": "memory.revision_history",
    "delegation": "delegation.quota_ledger",
    "approval_effects": "approval.operation_ledger",
    "lifecycle": "recovery.checkpoint_manifest",
}

#: The independent truth each category is allowed to claim. The point is that a
#: category names one of these and a derivation, instead of "the product said so".
TRUTH_SOURCES = {
    "sales": "independent_decimal_reference",
    "routing": "human_labeled_catalog_condition",
    "rag": "committed_source_span_truth",
    "memory": "human_labeled_platform_state",
    "delegation": "human_labeled_effect_ledger",
    "approval_effects": "human_labeled_effect_ledger",
    "lifecycle": "human_labeled_platform_state",
}

LICENSE_REVIEWS = {"not_applicable", "pending", "reviewed"}
LICENSE_IDS = {"LicenseRef-Orchara-SelfCreated", "not_applicable"}
TARGET_KINDS = {"published_version", "debug_snapshot"}
ORIGINS = {"self_created", "adapted_external"}
FIXTURE_KINDS = {"file", "generated", "logical"}

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# issues and reports
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Issue:
    """One located refusal. ``code`` is a stable machine code."""

    code: str
    where: str = ""
    detail: str = ""
    case_id: str | None = None

    def __str__(self) -> str:
        location = self.where or "corpus"
        prefix = f"{self.case_id}: " if self.case_id else ""
        return f"{prefix}{self.code} at {location}: {self.detail}"


@dataclass
class Corpus:
    """The loaded corpus: seven case documents plus the registry."""

    directory: Path
    schema: dict
    registry: dict
    files: dict[str, dict]
    schema_path: Path = SCHEMA_PATH

    @property
    def cases(self) -> list[dict]:
        """Every case, flattened in file order (computed, so tests can mutate)."""
        items: list[dict] = []
        for document in self.files.values():
            items.extend(document.get("cases", []))
        return items

    def case(self, case_id: str) -> dict | None:
        for case in self.cases:
            if isinstance(case, dict) and case.get("case_id") == case_id:
                return case
        return None

    def source_of(self, case_id: str) -> str | None:
        for filename, document in self.files.items():
            for case in document.get("cases", []):
                if isinstance(case, dict) and case.get("case_id") == case_id:
                    return filename
        return None

    def entries(self) -> Iterator[tuple[str, int, dict]]:
        for filename, document in self.files.items():
            for index, case in enumerate(document.get("cases", [])):
                yield filename, index, case


@dataclass
class LeakageReport:
    """The measured split-isolation result, including the numbers a report cites."""

    threshold: float
    ngram: int
    family_splits: dict[str, list[str]] = field(default_factory=dict)
    cross_split_families: list[str] = field(default_factory=list)
    cross_split_templates: list[dict] = field(default_factory=list)
    max_cross_split_similarity: float = 0.0
    most_similar_cross_split_pair: tuple[str, str] | None = None
    max_within_split_similarity: float = 0.0
    most_similar_within_split_pair: tuple[str, str] | None = None
    cross_split_pairs_at_or_above_threshold: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.cross_split_families
            or self.cross_split_templates
            or self.cross_split_pairs_at_or_above_threshold
        )


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def load_corpus(
    directory: Path | str | None = None,
    schema_path: Path | str | None = None,
) -> Corpus:
    """Load the seven case documents and the registry.

    Only structural document shape errors raise here; schema and corpus rules
    are returned as located :class:`Issue` objects by the validators, so a
    negative test can load a deliberately broken corpus and assert the refusal.
    """
    corpus_dir = Path(directory) if directory is not None else DEFAULT_CORPUS_DIR
    schema_file = Path(schema_path) if schema_path is not None else SCHEMA_PATH
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    registry_path = corpus_dir / "registry.json"
    if not registry_path.exists():
        raise FileNotFoundError(f"the corpus registry is missing: {registry_path}")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(registry, dict):
        raise ValueError(f"{registry_path} must be a JSON object")

    files: dict[str, dict] = {}
    for path in sorted(corpus_dir.glob("cases.*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("cases"), list):
            raise ValueError(f"{path.name} must be an object with a cases array")
        files[path.name] = document
    if not files:
        raise FileNotFoundError(f"no cases.<category>.json documents under {corpus_dir}")
    return Corpus(
        directory=corpus_dir,
        schema=schema,
        registry=registry,
        files=files,
        schema_path=schema_file,
    )


# ---------------------------------------------------------------------------
# case-level validation
# ---------------------------------------------------------------------------
_DEFAULT_VALIDATOR: jsonschema.Draft202012Validator | None = None


def _validator() -> jsonschema.Draft202012Validator:
    global _DEFAULT_VALIDATOR
    if _DEFAULT_VALIDATOR is None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        _DEFAULT_VALIDATOR = jsonschema.Draft202012Validator(schema)
    return _DEFAULT_VALIDATOR


def validate_case(case: Any) -> list[Issue]:
    """Validate one case against the committed JSON schema and the semantic rules."""
    issues: list[Issue] = []
    if not isinstance(case, dict):
        return [Issue("schema_violation", detail="an evaluation case must be a JSON object")]
    case_id = case.get("case_id") if isinstance(case.get("case_id"), str) else None
    for error in sorted(_validator().iter_errors(case), key=lambda item: list(item.path)):
        where = "".join(
            "/" + str(part).replace("~", "~0").replace("/", "~1") for part in error.absolute_path
        ) or "/"
        issues.append(Issue("schema_violation", where=where, detail=error.message, case_id=case_id))
    for code in reject_reasons(case):
        issues.append(Issue(code, detail="semantic rule", case_id=case_id))
    return _dedupe(issues)


def reject_reasons(case: Any) -> list[str]:
    """Stable semantic refusal codes for an empty/skipped/incomplete case.

    This deliberately runs before (and independently of) the JSON schema, so a
    ``skipped: true`` or a cross-file ``expected_failure``-shaped case produces a
    readable code instead of only "additionalProperties is false".
    """
    if not isinstance(case, dict):
        return ["invalid_case_document"]
    reasons: set[str] = set()
    if case.get("skipped") is True:
        reasons.add("skipped_case")
    if "schema_version" not in case:
        reasons.add("missing_schema_version")
    elif case.get("schema_version") != "1.0":
        reasons.add("invalid_schema_version")
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        reasons.add("missing_case_id")
    category = case.get("category")
    if category is None:
        reasons.add("missing_category")
    elif category not in CATEGORIES:
        reasons.add("unsupported_category")
    if "family_id" not in case or not str(case.get("family_id", "")).strip():
        reasons.add("missing_family")
    split = case.get("split")
    if split is None:
        reasons.add("missing_split")
    elif split not in SPLITS:
        reasons.add("invalid_split")
    target = case.get("target")
    if target is None:
        reasons.add("missing_target")
    elif (
        not isinstance(target, dict)
        or target.get("kind") not in TARGET_KINDS
        or not str(target.get("id", "")).strip()
    ):
        reasons.add("invalid_target")
    if "input" not in case:
        reasons.add("missing_input")
    elif not isinstance(case.get("input"), dict) or not case.get("input"):
        reasons.add("empty_input")
    assertions = case.get("assertions")
    if "assertions" not in case:
        reasons.add("missing_assertions")
    elif not isinstance(assertions, list) or not assertions:
        reasons.add("empty_assertions")
    provenance = case.get("provenance")
    if provenance is None:
        reasons.add("missing_provenance")
    elif not isinstance(provenance, dict):
        reasons.add("invalid_provenance")
    else:
        if provenance.get("origin") not in ORIGINS:
            reasons.add("invalid_origin")
        if not str(provenance.get("reference", "")).strip():
            reasons.add("missing_provenance_reference")
        if "license_review" not in provenance:
            reasons.add("missing_license_review")
        elif provenance.get("license_review") not in LICENSE_REVIEWS:
            reasons.add("invalid_license_review")
    fixture_id = case.get("fixture_id")
    if not isinstance(fixture_id, str) or not fixture_id.strip():
        reasons.add("missing_fixture_id")
    return sorted(reasons)


def is_decidable(case: Any, registry_entry: Any) -> tuple[bool, list[str]]:
    """Whether a case can produce a verdict at all.

    Decidable means: no semantic refusal, and every required collection fact and
    every assertion-derived fact is declared. Empty/skipped/incomplete cases are
    not decidable, so they can never be counted as passed.
    """
    reasons = reject_reasons(case)
    if not isinstance(registry_entry, dict):
        return False, sorted(set(reasons + ["missing_registry_entry"]))
    declared = registry_entry.get("required_facts")
    if not isinstance(declared, list) or not declared:
        return False, sorted(set(reasons + ["missing_required_facts"]))
    required = set(declared)
    missing = [f"missing_fact:{fact}" for fact in CATEGORY_FACTS.values() if fact not in required]
    missing.extend(
        f"missing_fact:{fact}" for fact in COLLECTION_FACTS if fact not in required
    )
    missing.extend(
        f"missing_fact:{fact}"
        for fact in derive_required_facts(case)
        if fact not in required
    )
    return not reasons and not missing, sorted(set(reasons + missing))


def derive_required_facts(case: dict) -> list[str]:
    """The facts a case's own assertions need, derived from the assertion kinds."""
    facts: set[str] = set()
    for item in case.get("assertions", []):
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind == "run_state":
            facts.add("run.terminal_state")
        elif kind in {"json_equals", "decimal_equals"}:
            facts.add("result_json:" + str(item.get("pointer", "")))
        elif kind == "effect_count":
            facts.add("stub_ledger:" + str(item.get("capability", "")))
        elif kind == "citation_sources":
            facts.add("citations.source_ids")
    return sorted(facts)


# ---------------------------------------------------------------------------
# corpus-level validation
# ---------------------------------------------------------------------------
def validate_corpus(corpus: Corpus) -> list[Issue]:
    """Every cross-file rule the frozen corpus must satisfy.

    An empty list means: schema-clean, seven categories with both splits, one
    registry description per case, traceable licences, pinned bytes, compatible
    collection facts, no family/template/near-duplicate split leak. It does not
    mean any case was executed.
    """
    issues: list[Issue] = []

    # 1. case documents: schema + semantic rules.
    seen_ids: dict[str, list[str]] = {}
    for filename, index, case in corpus.entries():
        where = f"{filename}#/cases/{index}"
        for issue in validate_case(case):
            issues.append(
                Issue(issue.code, where=where + (issue.where or ""), detail=issue.detail,
                      case_id=issue.case_id)
            )
        if isinstance(case, dict) and isinstance(case.get("case_id"), str):
            seen_ids.setdefault(case["case_id"], []).append(where)
    for case_id, locations in sorted(seen_ids.items()):
        if len(locations) > 1:
            issues.append(
                Issue("duplicate_case_id", where=", ".join(locations),
                      detail=f"case id {case_id!r} appears {len(locations)} times",
                      case_id=case_id)
            )

    # 2. seven categories, dev + frozen_test in every one.
    present = {case.get("category") for case in corpus.cases if isinstance(case, dict)}
    for category in CATEGORIES:
        if category not in present:
            issues.append(Issue("missing_category", detail=f"no {category} case is frozen"))
    for category in CATEGORIES:
        splits = {
            case.get("split")
            for case in corpus.cases
            if isinstance(case, dict) and case.get("category") == category
        }
        for split in SPLITS:
            if split not in splits:
                issues.append(
                    Issue("missing_split_for_category", detail=f"{category} has no {split} case")
                )

    # 3. registry metadata and pins.
    registry = corpus.registry
    if registry.get("schema") != REGISTRY_SCHEMA:
        issues.append(Issue("registry_schema_mismatch", detail=f"schema must be {REGISTRY_SCHEMA!r}"))
    if not str(registry.get("generated_by", "")).strip():
        issues.append(Issue("registry_missing_generated_by", detail="generated_by is empty"))
    thresholds = registry.get("thresholds")
    if not isinstance(thresholds, dict):
        issues.append(Issue("threshold_mismatch", detail="thresholds object is missing"))
    else:
        if thresholds.get("near_duplicate_jaccard") != NEAR_DUPLICATE_THRESHOLD:
            issues.append(
                Issue("threshold_mismatch",
                      detail=f"near_duplicate_jaccard must be {NEAR_DUPLICATE_THRESHOLD}")
            )
        if thresholds.get("near_duplicate_ngram") != NEAR_DUPLICATE_NGRAM:
            issues.append(
                Issue("threshold_mismatch",
                      detail=f"near_duplicate_ngram must be {NEAR_DUPLICATE_NGRAM}")
            )
    collection = registry.get("collection")
    collection_facts = set()
    if not isinstance(collection, dict) or not collection.get("required_facts"):
        issues.append(Issue("collection_rule_invalid", detail="collection.required_facts is empty"))
    else:
        collection_facts = set(collection["required_facts"])
        if "collection.complete" not in collection_facts:
            issues.append(
                Issue("collection_rule_invalid",
                      detail="collection.complete must be a required collection fact")
            )

    schema_bytes = corpus.schema_path.read_bytes()
    if registry.get("schema_sha256") != hashlib.sha256(schema_bytes).hexdigest():
        issues.append(
            Issue("schema_pin_mismatch",
                  detail=f"schema_sha256 does not pin {corpus.schema_path}")
        )
    for filename, document in sorted(corpus.files.items()):
        pin = registry.get("files", {}).get(filename)
        payload = (corpus.directory / filename).read_bytes() if (corpus.directory / filename).exists() else b""
        if not isinstance(pin, dict):
            issues.append(Issue("file_pin_mismatch", where=filename, detail="no registry pin"))
            continue
        if pin.get("sha256") != hashlib.sha256(payload).hexdigest():
            issues.append(Issue("file_pin_mismatch", where=filename, detail="sha256 drifted"))
        if pin.get("size_bytes") != len(payload):
            issues.append(Issue("file_pin_mismatch", where=filename, detail="size drifted"))
        if pin.get("cases") != len(document.get("cases", [])):
            issues.append(Issue("file_pin_mismatch", where=filename, detail="case count drifted"))

    # 4. one registry description per case, with truth/licence/facts.
    entries = registry.get("cases")
    if not isinstance(entries, dict):
        issues.append(Issue("registry_missing_cases", detail="registry.cases must be an object"))
        entries = {}
    known_ids = set(seen_ids)
    for case_id in sorted(set(entries) - known_ids):
        issues.append(
            Issue("registry_unknown_case", detail="the registry describes no such case",
                  case_id=case_id)
        )
    for case_id, locations in sorted(seen_ids.items()):
        case = corpus.case(case_id)
        entry = entries.get(case_id)
        if not isinstance(entry, dict):
            issues.append(
                Issue("registry_missing_case", where=", ".join(locations),
                      detail="the case has no registry description", case_id=case_id)
            )
            continue
        issues.extend(_validate_registry_entry(corpus, case, entry, collection_facts))

    # 5. fixture registry entries are well formed and every task_input resolves.
    fixtures = registry.get("fixtures")
    if not isinstance(fixtures, dict):
        issues.append(Issue("registry_missing_fixtures", detail="registry.fixtures must be an object"))
        fixtures = {}
    for fixture_id, fixture in sorted(fixtures.items()):
        if not isinstance(fixture, dict):
            issues.append(Issue("invalid_fixture", where=f"fixtures/{fixture_id}",
                                detail="a fixture entry must be an object"))
            continue
        kind = fixture.get("kind")
        if kind not in FIXTURE_KINDS:
            issues.append(Issue("invalid_fixture", where=f"fixtures/{fixture_id}",
                                detail="fixture.kind must be file, generated or logical"))
            continue
        if kind in {"file", "generated", "logical"}:
            if kind == "logical":
                if not str(fixture.get("description", "")).strip():
                    issues.append(Issue("invalid_fixture", where=f"fixtures/{fixture_id}",
                                        detail="a logical fixture needs a description"))
                continue
            if not str(fixture.get("media_type", "")).strip():
                issues.append(Issue("invalid_fixture", where=f"fixtures/{fixture_id}",
                                    detail="a file fixture needs a media_type"))
            if not _HEX64.match(str(fixture.get("sha256", ""))):
                issues.append(Issue("invalid_fixture", where=f"fixtures/{fixture_id}",
                                    detail="a file fixture needs a 64-hex sha256"))
            if kind == "file" and not str(fixture.get("path", "")).strip():
                issues.append(Issue("invalid_fixture", where=f"fixtures/{fixture_id}",
                                    detail="a committed fixture needs a path"))
            if kind == "generated" and not str(fixture.get("generator", "")).strip():
                issues.append(Issue("invalid_fixture", where=f"fixtures/{fixture_id}",
                                    detail="a generated fixture needs a generator"))
    for case_id, case in ((item.get("case_id"), item) for item in corpus.cases if isinstance(item, dict)):
        if not isinstance(case_id, str):
            continue
        fixture_id = case.get("fixture_id")
        if not isinstance(fixture_id, str):
            continue
        fixture = fixtures.get(fixture_id)
        if not isinstance(fixture, dict):
            issues.append(Issue("unknown_fixture", detail=f"fixture {fixture_id!r} is not registered",
                                case_id=case_id))
            continue
        binding = case.get("input", {}).get("task_input") if isinstance(case.get("input"), dict) else None
        if isinstance(binding, dict):
            if fixture.get("kind") == "logical":
                issues.append(Issue("fixture_hash_mismatch", detail="a logical fixture cannot back task_input",
                                    case_id=case_id))
            elif binding.get("sha256") != fixture.get("sha256") or binding.get(
                "media_type"
            ) != fixture.get("media_type"):
                issues.append(
                    Issue("fixture_hash_mismatch",
                          detail=f"task_input does not pin fixture {fixture_id!r}",
                          case_id=case_id)
                )

    # 6. families and normalized templates never cross a split.
    issues.extend(_validate_families(corpus, entries))

    # 7. measured near-duplicate leakage and template leakage.
    report = split_leakage(corpus)
    for template in report.cross_split_templates:
        issues.append(
            Issue(
                "cross_split_template",
                detail=(
                    f"normalized template {template['normalized']!r} appears in "
                    f"{template['splits']}"
                ),
            )
        )
    for pair in report.cross_split_pairs_at_or_above_threshold:
        issues.append(
            Issue(
                "near_duplicate_leak",
                detail=(
                    f"{pair['left']} ({pair['left_split']}) and {pair['right']} "
                    f"({pair['right_split']}) are {pair['similarity']:.4f} similar "
                    f"(threshold {report.threshold})"
                ),
                case_id=pair["left"],
            )
        )

    # 8. the summary table agrees with the documents.
    summary = summarize(corpus)
    declared_summary = registry.get("categories")
    if not isinstance(declared_summary, dict):
        issues.append(Issue("category_summary_mismatch", detail="registry.categories is missing"))
    else:
        for category, data in summary.items():
            declared = declared_summary.get(category)
            if not isinstance(declared, dict):
                issues.append(Issue("category_summary_mismatch", detail=f"{category} is missing"))
                continue
            for key in ("dev", "frozen_test", "cases", "assertions"):
                if declared.get(key) != data[key]:
                    issues.append(
                        Issue("category_summary_mismatch",
                              detail=f"{category}.{key} is {declared.get(key)!r}, corpus has {data[key]!r}")
                    )
            if declared.get("families") != data["families"]:
                issues.append(Issue("category_summary_mismatch",
                                    detail=f"{category}.families drifted"))
    return _dedupe(issues)


def _validate_registry_entry(
    corpus: Corpus, case: dict, entry: dict, collection_facts: set[str]
) -> list[Issue]:
    issues: list[Issue] = []
    case_id = case.get("case_id", "")
    for key in ("category", "family_id", "split"):
        if entry.get(key) != case.get(key):
            issues.append(
                Issue("registry_case_mismatch",
                      detail=f"registry {key} is {entry.get(key)!r}, case has {case.get(key)!r}",
                      case_id=case_id)
            )
    if not str(entry.get("template", "")).strip():
        issues.append(Issue("missing_template", detail="template is empty", case_id=case_id))
    if not str(entry.get("scenario", "")).strip():
        issues.append(Issue("missing_scenario", detail="scenario is empty", case_id=case_id))
    if entry.get("provenance") != case.get("provenance"):
        issues.append(Issue("provenance_mismatch",
                            detail="registry provenance differs from the case provenance",
                            case_id=case_id))
    if entry.get("fixture_id") != case.get("fixture_id"):
        issues.append(Issue("fixture_mismatch",
                            detail="registry fixture_id differs from the case fixture_id",
                            case_id=case_id))
    expected_kinds = sorted({item["kind"] for item in case.get("assertions", []) if isinstance(item, dict)})
    if entry.get("assertion_kinds") != expected_kinds:
        issues.append(Issue("assertion_kinds_mismatch",
                            detail=f"registry says {entry.get('assertion_kinds')!r}, case has {expected_kinds!r}",
                            case_id=case_id))

    truth = entry.get("truth")
    if not isinstance(truth, dict):
        issues.append(Issue("missing_truth", detail="truth object is missing", case_id=case_id))
    else:
        source = truth.get("source")
        if not str(source or "").strip():
            issues.append(Issue("missing_truth", detail="truth.source is empty", case_id=case_id))
        elif source != TRUTH_SOURCES.get(case.get("category")):
            issues.append(
                Issue("unexpected_truth_source",
                      detail=f"{source!r} is not the {case.get('category')} truth source",
                      case_id=case_id)
            )
        if len(str(truth.get("derivation", "")).strip()) < 20:
            issues.append(Issue("missing_truth",
                                detail="truth.derivation must describe how the truth is derived",
                                case_id=case_id))

    licence = entry.get("license")
    if not isinstance(licence, dict):
        issues.append(Issue("missing_license", detail="license object is missing", case_id=case_id))
    else:
        if licence.get("origin") not in ORIGINS:
            issues.append(Issue("missing_license", detail="license.origin is invalid", case_id=case_id))
        if licence.get("license_id") not in LICENSE_IDS:
            issues.append(Issue("missing_license", detail="license.license_id is not traceable",
                                case_id=case_id))
        if licence.get("redistributable") is not True:
            issues.append(Issue("missing_license", detail="license.redistributable must be true",
                                case_id=case_id))
        if licence.get("review") not in {"not_applicable", "reviewed"}:
            issues.append(Issue("missing_license", detail="license.review is not final",
                                case_id=case_id))
        if not str(licence.get("notes", "")).strip():
            issues.append(Issue("missing_license", detail="license.notes is empty", case_id=case_id))
        if isinstance(case.get("provenance"), dict):
            if licence.get("review") != case["provenance"].get("license_review"):
                issues.append(Issue("license_provenance_mismatch",
                                    detail="license.review differs from provenance.license_review",
                                    case_id=case_id))
            if licence.get("origin") != case["provenance"].get("origin"):
                issues.append(Issue("license_provenance_mismatch",
                                    detail="license.origin differs from provenance.origin",
                                    case_id=case_id))

    required = entry.get("required_facts")
    if not isinstance(required, list) or not required:
        issues.append(Issue("missing_required_facts",
                            detail="required_facts must be a non-empty list", case_id=case_id))
    else:
        required_set = set(required)
        if required != sorted(required_set):
            issues.append(Issue("required_facts_unsorted",
                                detail="required_facts must be a sorted unique list", case_id=case_id))
        missing_collection = collection_facts - required_set
        if missing_collection:
            issues.append(
                Issue("collection_incomplete",
                      detail=f"required_facts misses {sorted(missing_collection)}", case_id=case_id)
            )
        missing_derived = set(derive_required_facts(case)) - required_set
        if missing_derived:
            issues.append(
                Issue("facts_incompatible_with_assertions",
                      detail=f"assertions need {sorted(missing_derived)}", case_id=case_id)
            )
        category_fact = CATEGORY_FACTS.get(case.get("category"))
        if category_fact and category_fact not in required_set:
            issues.append(
                Issue("facts_incompatible_with_assertions",
                      detail=f"the category needs {category_fact!r}", case_id=case_id)
            )
    return issues


def _validate_families(corpus: Corpus, entries: dict) -> list[Issue]:
    issues: list[Issue] = []
    families = corpus.registry.get("families")
    if not isinstance(families, dict):
        return [Issue("registry_missing_families", detail="registry.families must be an object")]

    referenced = {
        case.get("family_id") for case in corpus.cases if isinstance(case, dict)
    }
    for family_id in sorted(referenced - set(families)):
        issues.append(Issue("family_missing", detail=f"family {family_id!r} is not registered"))
    for family_id in sorted(set(families) - referenced):
        issues.append(Issue("family_unknown", detail=f"family {family_id!r} has no case"))

    family_splits = split_leakage(corpus).family_splits
    for family_id, splits in sorted(family_splits.items()):
        if len(splits) > 1:
            issues.append(
                Issue("cross_split_family",
                      detail=f"family {family_id!r} appears in {splits}")
            )

    for family_id, family in sorted(families.items()):
        if not isinstance(family, dict):
            issues.append(Issue("family_missing", detail=f"family {family_id!r} is not an object"))
            continue
        if family.get("split") not in SPLITS:
            issues.append(Issue("invalid_split", detail=f"family {family_id!r} has a bad split"))
        if not str(family.get("template", "")).strip():
            issues.append(Issue("missing_template", detail=f"family {family_id!r} has no template"))

    # every case's per-case template must agree with its family's template.
    for case in corpus.cases:
        if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
            continue
        entry = entries.get(case["case_id"])
        family = families.get(case.get("family_id"))
        if isinstance(entry, dict) and isinstance(family, dict):
            if entry.get("template") != family.get("template"):
                issues.append(
                    Issue("template_mismatch",
                          detail=f"case template differs from family {case.get('family_id')!r}",
                          case_id=case["case_id"])
                )
    return issues


# ---------------------------------------------------------------------------
# split leakage
# ---------------------------------------------------------------------------
def split_leakage(corpus: Corpus) -> LeakageReport:
    """Measure family/template/near-duplicate isolation between the two splits."""
    entries = corpus.registry.get("cases") if isinstance(corpus.registry, dict) else {}
    entries = entries if isinstance(entries, dict) else {}
    report = LeakageReport(threshold=NEAR_DUPLICATE_THRESHOLD, ngram=NEAR_DUPLICATE_NGRAM)

    usable = [case for case in corpus.cases if isinstance(case, dict)]
    splits = {case.get("case_id"): case.get("split") for case in usable}
    family_splits: dict[str, set[str]] = {}
    for case in usable:
        family = case.get("family_id")
        split = case.get("split")
        if isinstance(family, str) and isinstance(split, str):
            family_splits.setdefault(family, set()).add(split)
    report.family_splits = {
        family: sorted(values) for family, values in sorted(family_splits.items())
    }
    report.cross_split_families = sorted(
        family for family, values in family_splits.items() if len(values) > 1
    )

    template_splits: dict[str, set[str]] = {}
    declared_templates: dict[str, str] = {}
    for case in usable:
        case_id = case.get("case_id")
        entry = entries.get(case_id) if isinstance(case_id, str) else None
        if not isinstance(entry, dict):
            continue
        normalized = normalize_template(str(entry.get("template", "")))
        if not normalized:
            continue
        declared_templates[normalized] = str(entry.get("template", ""))
        template_splits.setdefault(normalized, set()).add(str(case.get("split")))
    report.cross_split_templates = [
        {
            "template": declared_templates[template],
            "normalized": template,
            "splits": sorted(values),
        }
        for template, values in sorted(template_splits.items())
        if len(values) > 1
    ]

    cross_pairs: list[dict] = []
    max_cross = -1.0
    max_cross_pair: tuple[str, str] | None = None
    max_within = -1.0
    max_within_pair: tuple[str, str] | None = None
    for left_index, left in enumerate(usable):
        left_id = str(left.get("case_id"))
        left_entry = entries.get(left_id) if isinstance(entries.get(left_id), dict) else {}
        for right in usable[left_index + 1:]:
            right_id = str(right.get("case_id"))
            right_entry = entries.get(right_id) if isinstance(entries.get(right_id), dict) else {}
            similarity = near_duplicate_similarity(
                leak_text(left, left_entry),
                leak_text(right, right_entry),
                n=NEAR_DUPLICATE_NGRAM,
            )
            same_split = splits.get(left_id) == splits.get(right_id)
            if same_split:
                if similarity > max_within:
                    max_within = similarity
                    max_within_pair = (left_id, right_id)
                continue
            if similarity > max_cross:
                max_cross = similarity
                max_cross_pair = (left_id, right_id)
            if similarity >= NEAR_DUPLICATE_THRESHOLD:
                cross_pairs.append(
                    {
                        "left": left_id,
                        "right": right_id,
                        "left_split": splits.get(left_id),
                        "right_split": splits.get(right_id),
                        "similarity": round(similarity, 6),
                    }
                )
    report.max_cross_split_similarity = max(max_cross, 0.0)
    report.most_similar_cross_split_pair = max_cross_pair
    report.max_within_split_similarity = max(max_within, 0.0)
    report.most_similar_within_split_pair = max_within_pair
    report.cross_split_pairs_at_or_above_threshold = sorted(
        cross_pairs, key=lambda pair: (-pair["similarity"], pair["left"], pair["right"])
    )
    return report


_WHITESPACE = re.compile(r"\s+")
_DIGITS = re.compile(r"(?<![a-z0-9])-?\d+(?:\.\d+)?(?![a-z0-9])")
_PUNCTUATION = re.compile(r"[^a-z0-9<>\s]+")


def normalize_template(text: str) -> str:
    """Normalize a task template: drop numbers/ids/amounts/placeholders.

    Two cases whose templates normalize to the same string are the same
    template, and therefore may not be in different splits.
    """
    lowered = str(text).lower()
    lowered = re.sub(r"[0-9a-f]{64}", " <sha> ", lowered)
    lowered = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        " <uuid> ",
        lowered,
    )
    lowered = re.sub(r"\b[0-9a-f]{16,}\b", " <hash> ", lowered)
    lowered = _DIGITS.sub(" <num> ", lowered)
    lowered = _PUNCTUATION.sub(" ", lowered)
    return _WHITESPACE.sub(" ", lowered).strip()


def _similarity_text(text: str) -> str:
    lowered = str(text).lower()
    lowered = re.sub(r"[0-9a-f]{64}", " sha ", lowered)
    lowered = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        " uuid ",
        lowered,
    )
    lowered = re.sub(r"\b[0-9a-f]{16,}\b", " hash ", lowered)
    lowered = _DIGITS.sub(" num ", lowered)
    lowered = re.sub(r"[^a-z0-9\s]+", " ", lowered)
    return _WHITESPACE.sub(" ", lowered).strip()


def _ngrams(text: str, n: int) -> set[str]:
    if len(text) < n:
        return {text} if text else set()
    return {text[index:index + n] for index in range(len(text) - n + 1)}


def near_duplicate_similarity(left: str, right: str, *, n: int = NEAR_DUPLICATE_NGRAM) -> float:
    """Character n-gram Jaccard similarity in [0, 1]."""
    left_grams = _ngrams(_similarity_text(left), n)
    right_grams = _ngrams(_similarity_text(right), n)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def leak_text(case: dict, registry_entry: dict) -> str:
    """The task-statement text a near-duplicate detector compares.

    It deliberately excludes the assertion boilerplate: two cases with different
    checkers on the same task are still duplicates, while the same checker shape
    on different tasks is not leakage.
    """
    parts = [
        str(registry_entry.get("template", "")),
        str(registry_entry.get("scenario", "")),
        str(case.get("fixture_id", "")),
        json.dumps(case.get("input", {}), ensure_ascii=False, sort_keys=True),
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# summaries and small helpers
# ---------------------------------------------------------------------------
def summarize(corpus: Corpus) -> dict[str, dict]:
    """Per-category counts the registry summary and the report cite."""
    summary: dict[str, dict] = {
        category: {"dev": 0, "frozen_test": 0, "cases": 0, "assertions": 0, "families": []}
        for category in CATEGORIES
    }
    families: dict[str, set[str]] = {category: set() for category in CATEGORIES}
    for case in corpus.cases:
        if not isinstance(case, dict):
            continue
        category = case.get("category")
        if category not in summary:
            continue
        summary[category]["cases"] += 1
        split = case.get("split")
        if split in SPLITS:
            summary[category][split] += 1
        summary[category]["assertions"] += len(case.get("assertions", []))
        if isinstance(case.get("family_id"), str):
            families[category].add(case["family_id"])
    for category in CATEGORIES:
        summary[category]["families"] = sorted(families[category])
    return summary


def _dedupe(issues: list[Issue]) -> list[Issue]:
    seen: set[tuple] = set()
    ordered: list[Issue] = []
    for issue in issues:
        key = (issue.case_id or "", issue.code, issue.where, issue.detail)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(issue)
    return sorted(ordered, key=lambda item: (item.case_id or "", item.code, item.where))
