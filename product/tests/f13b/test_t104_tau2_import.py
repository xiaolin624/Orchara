"""T104 / F13B: import adapted tau2-bench airline tasks and refuse empty checkers.

What this suite freezes (offline: no PostgreSQL, no sandbox, no model, no tau2
dependency and no external benchmark execution):

* the imported subset is pinned to the real tau2-bench repository at commit
  ``da0835f6c67baef58bdda18f2a3b037dfb3a1754`` (MIT): repo, commit, task ids,
  upstream paths, per-file SHA-256, SPDX id, LICENSE SHA-256 and the acquisition
  commands are recorded and re-checked;
* every adapted case is valid against the committed
  ``contracts/evaluation-case.schema.json`` and loads through the product's strict
  parser (``dataset_t104_external_test.go``); every case carries a task, an
  environment fixture and a non-empty checker;
* tasks whose ``evaluation_criteria`` is missing/empty, whose ``reward_basis`` is
  missing/empty (the upstream reward=1 default trap) or whose only checker is
  natural-language text are rejected with stable reason codes, and a checker that
  is only ``run_state: succeeded`` is refused as default-success;
* imported families/templates never cross the T102 dev/frozen_test splits and no
  near-duplicate pair crosses them;
* the adapted checker is exercised offline against the committed fixtures: the
  gold reference trajectory passes, and two wrong trajectories fail the expected
  assertions. This is a fixture-level stub run, not a product or benchmark run.

The adapted cases are labelled ``adapted_external``; this suite asserts that no
committed import artifact claims released-benchmark results or a leaderboard
position.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import jsonschema
import pytest

import f13b_corpus
import f13b_external as ext
from f13b import build_corpus, build_external

EXTERNAL_DIR = ext.EXTERNAL_DIR
SCHEMA = json.loads(f13b_corpus.SCHEMA_PATH.read_text(encoding="utf-8"))
CASE_IDS = ext.EXTERNAL_CASE_IDS
FORBIDDEN_CLAIM = re.compile(
    r"\b(official|leaderboard|leader-board|rank|ranking|ranked|placement)\b", re.IGNORECASE
)
REFERENCE_PATTERN = re.compile(
    r"^sierra-research/tau2-bench@"
    r"(?P<commit>[0-9a-f]{40})"
    r"#task=(?P<task_id>\d+)"
    r"#(?P<path>data/tau2/domains/airline/tasks\.json)$"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def external() -> ext.External:
    return ext.load_external(EXTERNAL_DIR)


def codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def entry_of(external: ext.External, case_id: str) -> dict:
    return external.registry["cases"][case_id]


def case_of(external: ext.External, case_id: str) -> dict:
    case = external.case(case_id)
    assert case is not None, case_id
    return case


def pin_task(pin: dict, task_id: str) -> dict:
    for item in pin["tasks"]:
        if item["task_id"] == task_id:
            return item
    raise KeyError(task_id)


def source_task(pin: dict, task_id: str = "39") -> dict:
    """A real-shaped tau2 task built from the pinned criteria extract."""
    pinned = pin_task(pin, task_id)
    return {
        "id": task_id,
        "description": {"purpose": pinned["purpose"]},
        "user_scenario": {
            "persona": None,
            "instructions": {
                "domain": "airline",
                "reason_for_call": "adapted test task",
                "known_info": "adapted test customer",
                "unknown_info": None,
                "task_instructions": "adapted test instructions",
            },
        },
        "initial_state": None,
        "evaluation_criteria": copy.deepcopy(pinned["criteria"]),
    }


# ---------------------------------------------------------------------------
# 1. source pin: real repo/commit/licence/task ids/paths/hashes
# ---------------------------------------------------------------------------
def test_source_pin_pins_the_real_tau2_commit_and_mit_licence(
    external: ext.External,
) -> None:
    pin = external.pin
    assert pin["schema"] == ext.SOURCE_SCHEMA
    assert pin["repo"] == "sierra-research/tau2-bench"
    assert pin["repo_url"] == "https://github.com/sierra-research/tau2-bench"
    assert pin["commit"] == "da0835f6c67baef58bdda18f2a3b037dfb3a1754"
    assert pin["commit"] == ext.PINNED_COMMIT

    licence = pin["license"]
    assert licence["spdx"] == "MIT"
    assert licence["review"] == "reviewed"
    assert licence["sha256"] == ext.EXPECTED_LICENSE_SHA256
    assert re.fullmatch(r"[0-9a-f]{64}", licence["sha256"])
    assert licence["source_url"].endswith(
        "/da0835f6c67baef58bdda18f2a3b037dfb3a1754/LICENSE"
    )
    assert hashlib.sha256(external.license_bytes).hexdigest() == licence["sha256"]
    text = external.license_bytes.decode("utf-8")
    assert text.startswith("MIT License")
    assert "Copyright (c) 2025 Sierra Research" in text
    assert "Permission is hereby granted" in text

    acquisition = pin["acquisition"]
    assert acquisition["result"] == "ok"
    assert acquisition["network"] == "available"
    joined = "\n".join(acquisition["commands"])
    assert ext.PINNED_COMMIT in joined
    assert "fetch --depth 1 origin" in joined


def test_source_pin_records_every_file_that_was_read(external: ext.External) -> None:
    pin = external.pin
    files = {item["path"]: item for item in pin["files"]}
    required = {
        "LICENSE",
        "data/tau2/domains/airline/tasks.json",
        "data/tau2/domains/airline/policy.md",
        "data/tau2/domains/airline/split_tasks.json",
        "data/tau2/domains/airline/db.json",
        "src/tau2/data_model/tasks.py",
        "src/tau2/evaluator/evaluator.py",
        "pyproject.toml",
    }
    assert required <= set(files)
    for path in sorted(required):
        item = files[path]
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), path
        assert item["size_bytes"] > 0, path
        assert item["role"].strip(), path
    assert files["LICENSE"]["sha256"] == ext.EXPECTED_LICENSE_SHA256
    assert files["data/tau2/domains/airline/tasks.json"]["sha256"] == (
        ext.EXPECTED_TASKS_SHA256
    )
    assert files["data/tau2/domains/airline/db.json"]["sha256"] == (
        ext.EXPECTED_DB_SHA256
    )
    environment = pin["environment"]
    assert environment["domain"] == "airline"
    assert environment["db_path"] == "data/tau2/domains/airline/db.json"
    assert environment["db_sha256"] == ext.EXPECTED_DB_SHA256


def test_source_pin_records_the_upstream_task_hashes(external: ext.External) -> None:
    pin = external.pin
    assert [item["task_id"] for item in pin["tasks"]] == ["11", "39", "40"]
    assert set(ext.SOURCE_TASK_IDS) == {"11", "39", "40"}
    for item in pin["tasks"]:
        assert item["path"] == "data/tau2/domains/airline/tasks.json"
        assert re.fullmatch(r"[0-9a-f]{64}", item["canonical_sha256"])
        criteria = item["criteria"]
        assert criteria["actions"], item["task_id"]
        assert criteria["reward_basis"], item["task_id"]
        assert ext.canonical_criteria_sha256(criteria) == item["criteria_sha256"]
        assert len(item["purpose"]) >= 20


def test_source_pin_records_the_empty_checker_trap(external: ext.External) -> None:
    trap = external.pin["empty_checker_trap"]
    assert trap["path"] == "src/tau2/evaluator/evaluator.py"
    assert "134-139" == trap["lines"]
    assert "reward=1.0" in trap["observed"]
    assert "No evaluation criteria" in trap["observed"]
    assert trap["condition"] == "task.evaluation_criteria is None"
    assert trap["import_gate"] == "reject"
    assert {"missing_checker", "empty_criteria", "empty_reward_basis"} <= set(
        trap["reason_codes"]
    )


def test_every_imported_case_has_traceable_provenance(external: ext.External) -> None:
    for case in external.cases:
        provenance = case["provenance"]
        assert provenance["origin"] == "adapted_external"
        assert provenance["license_review"] == "reviewed"
        match = REFERENCE_PATTERN.match(provenance["reference"])
        assert match, provenance["reference"]
        assert match.group("commit") == ext.PINNED_COMMIT
        assert match.group("path") == ext.SOURCE_PATH
        entry = entry_of(external, case["case_id"])
        assert entry["source"]["task_id"] == match.group("task_id")
        assert entry["source"]["commit"] == ext.PINNED_COMMIT
        pinned = pin_task(external.pin, entry["source"]["task_id"])
        assert pinned["canonical_sha256"] == entry["source"]["canonical_sha256"]
        assert pinned["criteria_sha256"] == entry["source"]["criteria_sha256"]


# ---------------------------------------------------------------------------
# 2. adapted cases: schema, task/environment/checker, three-task subset
# ---------------------------------------------------------------------------
def test_the_imported_subset_is_three_adapted_airline_tasks(
    external: ext.External,
) -> None:
    assert len(external.cases) == 3
    assert {case["case_id"] for case in external.cases} == set(CASE_IDS)
    assert {entry["source"]["task_id"] for entry in external.registry["cases"].values()} == {
        "11",
        "39",
        "40",
    }
    for case in external.cases:
        assert case["split"] == "frozen_test"
        assert case["category"] == "approval_effects"
        assert case["fixture_id"] in external.registry["fixtures"]


def test_every_imported_case_validates_against_the_committed_schema(
    external: ext.External,
) -> None:
    validator = jsonschema.Draft202012Validator(SCHEMA)
    for case in external.cases:
        errors = sorted(validator.iter_errors(case), key=lambda error: list(error.path))
        assert not errors, (
            f"{case['case_id']} is not valid against evaluation-case.schema.json: "
            f"{[error.message for error in errors]}"
        )
        assert f13b_corpus.validate_case(case) == [], case["case_id"]


def test_every_imported_case_has_task_environment_and_checker(
    external: ext.External,
) -> None:
    for case in external.cases:
        case_id = case["case_id"]
        entry = entry_of(external, case_id)
        assert case["input"], case_id
        # task: the adapted user request and the pinned source task identity.
        assert case["input"]["request"]["utterance"].strip(), case_id
        assert entry["source"]["task_id"] in {"11", "39", "40"}, case_id
        # environment: a committed local fixture plus its endpoint surface.
        environment = entry["environment"]
        assert environment["kind"] == "local_http_fixture", case_id
        assert environment["fixture_id"] == case["fixture_id"], case_id
        assert environment["endpoints"], case_id
        assert any(endpoint["capability"].startswith("booking.") for endpoint in environment["endpoints"])
        fixture = external.registry["fixtures"][case["fixture_id"]]
        assert fixture["kind"] == "file"
        payload = (EXTERNAL_DIR / fixture["path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == fixture["sha256"], case_id
        # checker: at least one substantive contract assertion.
        kinds = {item["kind"] for item in case["assertions"]}
        assert kinds, case_id
        assert kinds <= {
            "json_equals",
            "decimal_equals",
            "run_state",
            "effect_count",
            "citation_sources",
        }, case_id
        assert kinds & {"json_equals", "decimal_equals", "effect_count"}, case_id
        assert entry["adaptation"]["summary"].strip(), case_id
        assert entry["label"] == "adapted_external", case_id
        # the reference trajectory is preserved as adapted local actions.
        actions = entry["adapted_actions"]
        assert actions, case_id
        assert any(action.get("capability", "").startswith("booking.") for action in actions), case_id


UPSTREAM_TO_LOCAL_ACTION = {
    "get_user_details": "get_customer",
    "get_reservation_details": "get_reservation",
    "cancel_reservation": "cancel_reservation",
    "update_reservation_flights": "update_reservation_flights",
    "update_reservation_passengers": "update_reservation_passengers",
}
UPSTREAM_WRITE_ACTIONS = {
    "cancel_reservation",
    "update_reservation_flights",
    "update_reservation_passengers",
}


def test_adapted_actions_preserve_the_upstream_reference_trajectory(
    external: ext.External,
) -> None:
    for case in external.cases:
        entry = entry_of(external, case["case_id"])
        pinned = pin_task(external.pin, entry["source"]["task_id"])
        upstream_actions = pinned["criteria"]["actions"]
        local_actions = entry["adapted_actions"]
        assert [
            UPSTREAM_TO_LOCAL_ACTION[action["name"]] for action in upstream_actions
        ] == [action["name"] for action in local_actions], case["case_id"]
        upstream_writes = [
            action for action in upstream_actions if action["name"] in UPSTREAM_WRITE_ACTIONS
        ]
        local_writes = [action for action in local_actions if "capability" in action]
        assert len(upstream_writes) == len(local_writes), case["case_id"]


def test_the_three_cases_cover_cancellation_change_and_correction(
    external: ext.External,
) -> None:
    by_id = {case["case_id"]: case for case in external.cases}
    assert "booking.cancel" in {
        item["capability"] for item in by_id["tau2-airline-39-refund-eligibility"]["assertions"]
        if item["kind"] == "effect_count"
    }
    assert "booking.update_flights" in {
        item["capability"] for item in by_id["tau2-airline-11-flight-downgrade"]["assertions"]
        if item["kind"] == "effect_count"
    }
    assert "booking.update_passengers" in {
        item["capability"] for item in by_id["tau2-airline-40-passenger-correction"]["assertions"]
        if item["kind"] == "effect_count"
    }
    assert any(
        item["kind"] == "decimal_equals" for item in by_id["tau2-airline-39-refund-eligibility"]["assertions"]
    )
    assert any(
        item["kind"] == "decimal_equals" for item in by_id["tau2-airline-11-flight-downgrade"]["assertions"]
    )


# ---------------------------------------------------------------------------
# 3. empty-checker rejection gate (the reward=1 default trap)
# ---------------------------------------------------------------------------
def test_reject_missing_evaluation_criteria() -> None:
    task = source_task(ext.load_external().pin, "39")
    del task["evaluation_criteria"]
    reasons = ext.reject_reason(task)
    assert "missing_checker" in reasons
    assert "default_success" in reasons

    task["evaluation_criteria"] = None
    assert {"missing_checker", "default_success"} <= set(ext.reject_reason(task))


def test_reject_empty_evaluation_criteria() -> None:
    task = source_task(ext.load_external().pin, "39")
    task["evaluation_criteria"] = {
        "actions": [],
        "env_assertions": [],
        "communicate_info": [],
        "nl_assertions": [],
        "reward_basis": ["DB", "COMMUNICATE"],
    }
    reasons = ext.reject_reason(task)
    assert {"empty_criteria", "no_actions", "default_success"} <= set(reasons)
    assert "empty_reward_basis" not in reasons  # the basis is present and non-empty
    assert "text_only_checker" not in reasons  # there is no text at all


def test_reject_empty_reward_basis() -> None:
    task = source_task(ext.load_external().pin, "39")
    task["evaluation_criteria"]["reward_basis"] = []
    reasons = ext.reject_reason(task)
    assert "empty_reward_basis" in reasons
    assert "no_actions" not in reasons  # the actions are real; only the basis is empty


def test_reject_the_missing_reward_basis_default_trap() -> None:
    task = source_task(ext.load_external().pin, "39")
    del task["evaluation_criteria"]["reward_basis"]
    assert "empty_reward_basis" in ext.reject_reason(task)


def test_reject_a_text_only_checker() -> None:
    task = source_task(ext.load_external().pin, "39")
    task["evaluation_criteria"] = {
        "actions": [],
        "env_assertions": [],
        "communicate_info": [],
        "nl_assertions": ["Agent should refuse to cancel the reservation."],
        "reward_basis": ["DB", "COMMUNICATE"],
    }
    reasons = ext.reject_reason(task)
    assert "text_only_checker" in reasons
    assert "no_actions" in reasons


def test_reject_a_task_without_an_environment() -> None:
    task = source_task(ext.load_external().pin, "39")
    task["user_scenario"]["instructions"].pop("domain")
    task.pop("initial_state", None)
    assert "no_environment" in ext.reject_reason(task)


def test_validate_tau2_source_accepts_the_committed_snapshot(
    external: ext.External,
) -> None:
    tasks = [source_task(external.pin, task_id) for task_id in ext.SOURCE_TASK_IDS]
    assert ext.validate_tau2_source(external.pin, tasks) == []


def test_validate_tau2_source_rejects_an_unlicensed_source(
    external: ext.External,
) -> None:
    pin = copy.deepcopy(external.pin)
    pin["license"]["spdx"] = "Apache-2.0"
    tasks = [source_task(pin, task_id) for task_id in ext.SOURCE_TASK_IDS]
    assert "source_unlicensed" in codes(ext.validate_tau2_source(pin, tasks))


def test_validate_tau2_source_rejects_a_wrong_commit(external: ext.External) -> None:
    pin = copy.deepcopy(external.pin)
    pin["commit"] = "0" * 40
    tasks = [source_task(pin, task_id) for task_id in ext.SOURCE_TASK_IDS]
    assert "source_commit_mismatch" in codes(ext.validate_tau2_source(pin, tasks))


def test_validate_tau2_source_rejects_a_licence_hash_mismatch(
    external: ext.External,
) -> None:
    pin = copy.deepcopy(external.pin)
    pin["license"]["sha256"] = "0" * 64
    tasks = [source_task(pin, task_id) for task_id in ext.SOURCE_TASK_IDS]
    assert "license_hash_mismatch" in codes(ext.validate_tau2_source(pin, tasks))


def test_validate_tau2_source_rejects_a_changed_criteria(
    external: ext.External,
) -> None:
    tasks = [source_task(external.pin, task_id) for task_id in ext.SOURCE_TASK_IDS]
    tasks[0]["evaluation_criteria"]["actions"][0]["arguments"]["reservation_id"] = "AAAAAA"
    assert "source_criteria_mismatch" in codes(
        ext.validate_tau2_source(external.pin, tasks)
    )


def test_validate_tau2_source_rejects_the_empty_checker_snapshot(
    external: ext.External,
) -> None:
    tasks = [source_task(external.pin, task_id) for task_id in ext.SOURCE_TASK_IDS]
    del tasks[0]["evaluation_criteria"]
    assert "missing_checker" in codes(ext.validate_tau2_source(external.pin, tasks))


def test_validate_adapted_case_rejects_a_default_success_checker(
    external: ext.External,
) -> None:
    case = copy.deepcopy(case_of(external, CASE_IDS[0]))
    case["assertions"] = [{"kind": "run_state", "expected": "succeeded"}]
    assert "default_success_checker" in codes(ext.validate_adapted_case(case))


def test_validate_adapted_case_rejects_a_text_similarity_checker(
    external: ext.External,
) -> None:
    case = copy.deepcopy(case_of(external, CASE_IDS[0]))
    case["assertions"] = [
        {"kind": "text_similarity", "expected": "Agent should refuse to cancel."}
    ]
    found = codes(ext.validate_adapted_case(case))
    assert "schema_violation" in found
    assert "unsupported_checker" in found


def test_validate_adapted_case_rejects_adaptation_inside_the_case(
    external: ext.External,
) -> None:
    case = copy.deepcopy(case_of(external, CASE_IDS[0]))
    case["adaptation"] = {"summary": "must live in the registry, not in the case"}
    assert "schema_violation" in codes(ext.validate_adapted_case(case))


def test_validate_external_rejects_a_case_without_environment(
    external: ext.External,
) -> None:
    mutated = copy.deepcopy(external)
    mutated.registry["cases"][CASE_IDS[0]].pop("environment")
    assert "no_environment" in codes(ext.validate_external(mutated))


def test_validate_external_rejects_an_unreviewed_licence(
    external: ext.External,
) -> None:
    mutated = copy.deepcopy(external)
    case_id = CASE_IDS[0]
    mutated.registry["cases"][case_id]["license"]["review"] = "pending"
    assert "license_not_reviewed" in codes(ext.validate_external(mutated))


def test_validate_external_rejects_a_case_with_an_empty_checker(
    external: ext.External,
) -> None:
    mutated = copy.deepcopy(external)
    case_id = CASE_IDS[0]
    for case in mutated.cases_document["cases"]:
        if case["case_id"] == case_id:
            case["assertions"] = []
    mutated.registry["cases"][case_id]["assertion_kinds"] = []
    found = codes(ext.validate_external(mutated))
    assert "empty_assertions" in found
    assert "default_success_checker" in found


def test_validate_external_rejects_a_reference_to_another_task(
    external: ext.External,
) -> None:
    mutated = copy.deepcopy(external)
    case_id = CASE_IDS[0]
    for case in mutated.cases_document["cases"]:
        if case["case_id"] == case_id:
            reference = case["provenance"]["reference"]
            assert "#task=39" in reference
            case["provenance"]["reference"] = reference.replace("#task=39", "#task=11")
    mutated.registry["cases"][case_id]["provenance"] = copy.deepcopy(
        next(
            case["provenance"]
            for case in mutated.cases_document["cases"]
            if case["case_id"] == case_id
        )
    )
    assert "provenance_mismatch" in codes(ext.validate_external(mutated))


def test_validate_external_rejects_an_unknown_source_task(
    external: ext.External,
) -> None:
    mutated = copy.deepcopy(external)
    mutated.registry["cases"][CASE_IDS[0]]["source"]["task_id"] = "999"
    assert "unknown_source_task" in codes(ext.validate_external(mutated))


# ---------------------------------------------------------------------------
# 4. no released-benchmark / leaderboard claim
# ---------------------------------------------------------------------------
def test_import_artifacts_do_not_claim_released_benchmark_results(
    external: ext.External,
) -> None:
    assert external.registry["usage_label"] == "adapted_external"
    for case in external.cases:
        assert case["provenance"]["origin"] == "adapted_external"
        assert entry_of(external, case["case_id"])["label"] == "adapted_external"
        assert entry_of(external, case["case_id"])["adaptation"]["summary"].strip()

    paths = ["tau2/source_pin.json", "tau2/tasks.json", "registry.json"]
    paths.extend(fixture["path"] for fixture in external.registry["fixtures"].values())
    for relative in paths:
        text = (EXTERNAL_DIR / relative).read_text(encoding="utf-8")
        match = FORBIDDEN_CLAIM.search(text)
        assert match is None, f"{relative} contains {match.group(0)!r}"


# ---------------------------------------------------------------------------
# 5. registry pins, T102 integration and split isolation
# ---------------------------------------------------------------------------
def test_external_registry_pins_every_imported_file(external: ext.External) -> None:
    registry = external.registry
    assert registry["schema"] == ext.REGISTRY_SCHEMA
    assert registry["generated_by"] == "tests/f13b/build_external.py"
    assert registry["source_pin"]["sha256"] == hashlib.sha256(
        ext.PIN_PATH.read_bytes()
    ).hexdigest()
    for relative, pin in registry["files"].items():
        payload = (EXTERNAL_DIR / relative).read_bytes()
        assert pin["sha256"] == hashlib.sha256(payload).hexdigest(), relative
        assert pin["size_bytes"] == len(payload), relative
    tasks_document = json.loads(ext.TASKS_PATH.read_text(encoding="utf-8"))
    assert registry["files"]["tau2/tasks.json"]["cases"] == len(tasks_document["cases"])


def test_external_registry_describes_every_case(external: ext.External) -> None:
    registry = external.registry
    assert set(registry["cases"]) == {case["case_id"] for case in external.cases}
    for case in external.cases:
        case_id = case["case_id"]
        entry = registry["cases"][case_id]
        assert entry["category"] == case["category"], case_id
        assert entry["family_id"] == case["family_id"], case_id
        assert entry["split"] == case["split"], case_id
        assert entry["provenance"] == case["provenance"], case_id
        assert entry["fixture_id"] == case["fixture_id"], case_id
        assert entry["assertion_kinds"] == sorted(
            {item["kind"] for item in case["assertions"]}
        ), case_id
        assert entry["family_id"] in registry["families"], case_id
        assert registry["families"][entry["family_id"]]["template"] == entry["template"]
        assert entry["template"] == registry["families"][entry["family_id"]]["template"]
        assert len(entry["truth"]["derivation"].strip()) >= 20, case_id
        assert entry["required_facts"] == ext.required_facts(case), case_id
        assert ext.is_decidable(case, entry)[0] is True, case_id
        assert entry["adaptation"]["preserved"], case_id
        assert entry["adaptation"]["changed"], case_id


def test_t102_registry_references_the_external_registry(
    external: ext.External,
) -> None:
    corpus = f13b_corpus.load_corpus()
    reference = corpus.registry["external_imports"]
    assert reference["registry_path"] == "product/tests/f13b/external/registry.json"
    assert reference["registry_sha256"] == hashlib.sha256(
        ext.REGISTRY_PATH.read_bytes()
    ).hexdigest()
    assert reference["source_commit"] == ext.PINNED_COMMIT
    assert reference["label"] == "adapted_external"
    assert reference["rule"].strip()


def test_t102_corpus_still_validates_and_its_builder_reproduces() -> None:
    corpus = f13b_corpus.load_corpus()
    assert f13b_corpus.validate_corpus(corpus) == []
    built = build_corpus.build()
    for filename, payload in built.items():
        assert (f13b_corpus.DEFAULT_CORPUS_DIR / filename).read_bytes() == payload, filename


def test_external_and_t102_corpora_share_no_cross_split_family_or_template(
    external: ext.External,
) -> None:
    issues = ext.isolation_issues(external)
    assert issues == [], "\n".join(str(issue) for issue in issues)


def test_isolation_rejects_a_forged_cross_split_family(
    external: ext.External,
) -> None:
    mutated = copy.deepcopy(external)
    case_id = CASE_IDS[0]
    corpus = f13b_corpus.load_corpus()
    dev_family = "sales-file-summary"
    assert corpus.registry["families"][dev_family]["split"] == "dev"
    mutated.registry["cases"][case_id]["family_id"] = dev_family
    mutated.registry["families"][dev_family] = {
        "category": "sales",
        "split": "dev",
        "template": corpus.registry["families"][dev_family]["template"],
    }
    for case in mutated.cases_document["cases"]:
        if case["case_id"] == case_id:
            case["family_id"] = dev_family
    assert "cross_split_family" in codes(ext.isolation_issues(mutated))


def test_isolation_rejects_a_forged_cross_split_template(
    external: ext.External,
) -> None:
    mutated = copy.deepcopy(external)
    case_id = CASE_IDS[0]
    corpus = f13b_corpus.load_corpus()
    dev_template = corpus.registry["families"]["sales-file-summary"]["template"]
    mutated.registry["cases"][case_id]["template"] = dev_template
    family_id = mutated.registry["cases"][case_id]["family_id"]
    mutated.registry["families"][family_id]["template"] = dev_template
    assert "cross_split_template" in codes(ext.isolation_issues(mutated))


def test_isolation_rejects_a_forged_near_duplicate(external: ext.External) -> None:
    mutated = copy.deepcopy(external)
    case_id = CASE_IDS[0]
    corpus = f13b_corpus.load_corpus()
    dev_case = corpus.case("sales-dev-csv-summary")
    assert dev_case is not None
    dev_entry = corpus.registry["cases"]["sales-dev-csv-summary"]

    for case in mutated.cases_document["cases"]:
        if case["case_id"] == case_id:
            case["input"] = copy.deepcopy(dev_case["input"])
            case["fixture_id"] = dev_case["fixture_id"]
    mutated.registry["cases"][case_id]["scenario"] = dev_entry["scenario"]
    mutated.registry["cases"][case_id]["template"] = dev_entry["template"]
    family_id = mutated.registry["cases"][case_id]["family_id"]
    mutated.registry["families"][family_id]["template"] = dev_entry["template"]

    found = codes(ext.isolation_issues(mutated))
    assert "near_duplicate_leak" in found
    assert "cross_split_template" in found


# ---------------------------------------------------------------------------
# 6. offline fixture execution (stub): gold passes, wrong trajectories fail
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_id", CASE_IDS, ids=CASE_IDS)
def test_gold_reference_trajectory_passes_every_imported_checker(
    external: ext.External, case_id: str
) -> None:
    result = ext.simulate_reference(external, case_id)
    outcomes = ext.evaluate_assertions(case_of(external, case_id), result)
    assert outcomes
    failed = [outcome for outcome in outcomes if not outcome.passed]
    assert failed == [], [str(outcome) for outcome in failed]
    assert any(outcome.kind == "effect_count" for outcome in outcomes)
    assert any(outcome.kind in {"json_equals", "decimal_equals"} for outcome in outcomes)


def test_gold_fixture_values_are_derived_from_the_committed_fixture(
    external: ext.External,
) -> None:
    refund_case = ext.simulate_reference(
        external, "tau2-airline-39-refund-eligibility"
    )
    assert refund_case.output["refund_total"] == "995.75"
    eligible = [
        reservation_id
        for reservation_id, booking in refund_case.output["bookings"].items()
        if booking["status"] == "cancelled"
    ]
    assert sorted(eligible) == ["8C8K4E", "LU15PA", "MSJ4OA"]
    assert refund_case.effects["booking.cancel"] == 3

    downgrade = ext.simulate_reference(external, "tau2-airline-11-flight-downgrade")
    assert downgrade.output["refund_total"] == "5244.00"
    assert len(downgrade.output["bookings"]["GV1N64"]["passengers"]) == 2
    assert downgrade.output["bookings"]["GV1N64"]["cabin"] == "basic_economy"

    correction = ext.simulate_reference(
        external, "tau2-airline-40-passenger-correction"
    )
    passengers = correction.output["bookings"]["3RK2T9"]["passengers"]
    assert passengers[1]["last_name"] == "Garcia"
    assert passengers[0]["last_name"] == "Garcia"


def test_negative_control_cancels_every_reservation(external: ext.External) -> None:
    case_id = "tau2-airline-39-refund-eligibility"
    entry = entry_of(external, case_id)
    actions = [
        action
        for action in entry["adapted_actions"]
        if action["name"] != "cancel_reservation"
    ]
    for reservation_id in [
        "8C8K4E",
        "UDMOP1",
        "XAZ3C0",
        "LU15PA",
        "MSJ4OA",
        "I6M8JQ",
        "4XGCCM",
    ]:
        actions.append(
            {
                "name": "cancel_reservation",
                "arguments": {"reservation_id": reservation_id},
                "capability": "booking.cancel",
            }
        )
    result = ext.simulate_reference(external, case_id, actions=actions)
    outcomes = ext.evaluate_assertions(case_of(external, case_id), result)
    failed = {outcome.pointer or outcome.capability for outcome in outcomes if not outcome.passed}
    assert "/bookings/UDMOP1/status" in failed
    assert "/bookings/XAZ3C0/status" in failed
    assert "/bookings/I6M8JQ/status" in failed
    assert "/bookings/4XGCCM/status" in failed
    assert "booking.cancel" in failed
    assert result.effects["booking.cancel"] == 7


def test_negative_control_removes_a_passenger(external: ext.External) -> None:
    case_id = "tau2-airline-11-flight-downgrade"
    entry = entry_of(external, case_id)
    actions = copy.deepcopy(entry["adapted_actions"])
    actions.append(
        {
            "name": "update_reservation_passengers",
            "arguments": {
                "reservation_id": "GV1N64",
                "passengers": [
                    {"first_name": "James", "last_name": "Patel", "dob": "1982-05-30"}
                ],
            },
            "capability": "booking.update_passengers",
        }
    )
    result = ext.simulate_reference(external, case_id, actions=actions)
    outcomes = ext.evaluate_assertions(case_of(external, case_id), result)
    failed = {outcome.pointer or outcome.capability for outcome in outcomes if not outcome.passed}
    assert "/bookings/GV1N64/passengers" in failed
    assert "booking.update_passengers" in failed


def test_offline_cli_summary_is_green(external: ext.External) -> None:
    payload = ext.summary(external)
    assert payload["source"] == (
        "sierra-research/tau2-bench@da0835f6c67baef58bdda18f2a3b037dfb3a1754"
    )
    assert payload["usage_label"] == "adapted_external"
    assert payload["cases"] == 3
    assert payload["source_tasks"] == ["11", "39", "40"]
    assert payload["gate_issues"] == []
    for case_id, data in payload["stub_execution"].items():
        assert data["failed"] == [], case_id
        assert data["passed"] == data["assertions"], case_id
        assert data["source_task"] in {"11", "39", "40"}, case_id


def test_simulation_is_deterministic(external: ext.External) -> None:
    for case_id in CASE_IDS:
        first = ext.simulate_reference(external, case_id)
        second = ext.simulate_reference(external, case_id)
        assert first.output == second.output
        assert first.effects == second.effects


# ---------------------------------------------------------------------------
# 7. the committed external bytes are reproducible from the builder
# ---------------------------------------------------------------------------
def test_external_bytes_are_reproducible() -> None:
    built = build_external.build()
    assert set(built) == {
        "registry.json",
        "tau2/tasks.json",
        *(fixture["path"] for fixture in build_external.fixture_entries().values()),
    }
    for relative, payload in built.items():
        committed = (EXTERNAL_DIR / relative).read_bytes()
        assert committed == payload, (
            f"{relative} drifted from tests/f13b/build_external.py; "
            "run `python tests/f13b/build_external.py --write`"
        )
