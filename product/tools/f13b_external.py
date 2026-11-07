"""F13B/T104: offline import of adapted tau2-bench airline tasks.

This module is the import gate for the external-task subset frozen under
``product/tests/f13b/external``. It never imports tau2-bench, never starts a
model, database, sandbox or network connection, and never executes the product's
evaluation runner. It answers five mechanical questions:

* is the pinned source real and fully recorded (repo, commit, licence, per-file
  SHA-256, task ids/paths, acquisition commands)?
* does every adapted case carry the three things an external task needs before
  it may be imported: a **task**, a committed **environment fixture** and a
  non-empty **checker**?
* would a task be scored by the upstream default-success path (missing
  ``evaluation_criteria`` -> ``reward=1.0``, missing/empty ``reward_basis``,
  text-only assertions)? Those are refused, never imported.
* is the import traceable (*provenance*) and labelled as adapted rather than a
  released benchmark result?
* does the imported set violate the T102 dev/frozen_test isolation rules?

The module also provides a deliberately small offline fixture model
(:func:`simulate_reference`) and a mirror of the five contract assertion kinds
(:func:`evaluate_assertions`). These prove the imported checkers are decidable on
the committed fixture and that a wrong trajectory fails; they are **not** the Go
product checker and their result is a stub, never a benchmark score.

Stable refusal codes
--------------------
``missing_task_id``, ``missing_user_scenario``, ``no_environment``,
``missing_checker``, ``invalid_checker``, ``empty_criteria``, ``no_actions``,
``text_only_checker``, ``empty_reward_basis``, ``invalid_reward_basis``,
``default_success``, ``invalid_task``, ``duplicate_task_id``,
``source_unlicensed``, ``license_hash_mismatch``, ``source_commit_mismatch``,
``source_schema_mismatch``, ``source_repo_mismatch``, ``source_file_missing``,
``source_file_pin_invalid``, ``source_task_missing``, ``source_criteria_mismatch``,
``source_trap_missing``, ``source_acquisition_incomplete``,
``unpinned_source_task``, ``not_adapted``, ``license_not_reviewed``,
``provenance_reference_mismatch``, ``unsupported_checker``,
``default_success_checker``, ``unknown_source_task``, ``required_facts_mismatch``,
``provenance_mismatch``, ``fixture_mismatch``, ``registry_case_mismatch``,
``missing_adaptation``, ``missing_truth``, ``missing_template``,
``missing_scenario``, ``cross_split_family``, ``cross_split_template``,
``near_duplicate_leak``, ``external_family_reused``, ``external_case_id_collision``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import f13b_corpus  # noqa: E402

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_DIR = PRODUCT_ROOT / "tests" / "f13b" / "external"
PIN_PATH = EXTERNAL_DIR / "tau2" / "source_pin.json"
TASKS_PATH = EXTERNAL_DIR / "tau2" / "tasks.json"
REGISTRY_PATH = EXTERNAL_DIR / "registry.json"
LICENSE_PATH = EXTERNAL_DIR / "tau2" / "LICENSE.tau2-mit.txt"

REPO = "sierra-research/tau2-bench"
REPO_URL = "https://github.com/sierra-research/tau2-bench"
PINNED_COMMIT = "da0835f6c67baef58bdda18f2a3b037dfb3a1754"
SOURCE_PATH = "data/tau2/domains/airline/tasks.json"
POLICY_PATH = "data/tau2/domains/airline/policy.md"
SPLIT_PATH = "data/tau2/domains/airline/split_tasks.json"
DB_PATH = "data/tau2/domains/airline/db.json"
DATA_MODEL_PATH = "src/tau2/data_model/tasks.py"
EVALUATOR_PATH = "src/tau2/evaluator/evaluator.py"
PYPROJECT_PATH = "pyproject.toml"

SOURCE_SCHEMA = "orchara.f13b-tau2-source-pin/1"
REGISTRY_SCHEMA = "orchara.f13b-external-import/1"
LICENSE_SPDX = "MIT"
LICENSE_ID = "MIT-tau2-bench"
USAGE_LABEL = "adapted_external"

EXPECTED_LICENSE_SHA256 = "74741b87a33d4704c6f33e5c71742542446c984233f7a7e4905bf36f4da5a33b"
EXPECTED_TASKS_SHA256 = "34dfcf0622792d0d0fa32aa491d21582e1b5d3ec1948e7743bae881885f878ea"
EXPECTED_POLICY_SHA256 = "7db375ddcce5061cf9f48a5aabe1fc3e52aa2936d6a65b7c5b62e3c7ca54cfe4"
EXPECTED_SPLIT_SHA256 = "8249d7af4c654a48e5fa776993c3416c3b68c7213a26d00ec89e976c28363e97"
EXPECTED_DB_SHA256 = "7184914bd3720d93f1160a09bb2724c3a5601d8ca39d02d371cbbfa62626f7e2"
EXPECTED_DATA_MODEL_SHA256 = "dd169761cf3449a504b528a452ad4d1ef6667ce18336eacd4a033630ad43c61e"
EXPECTED_EVALUATOR_SHA256 = "b7de29656b0c5f31c5b0b7ac9a7cdf58e90595f54aa03b7d326e3ebb1f8b1ecf"
EXPECTED_PYPROJECT_SHA256 = "b9e5d3880fceb2ae2ccad2178d261c5ec6f7acbb4d13055c4ccb62bf26d6220c"

SOURCE_TASK_IDS = ("11", "39", "40")
EXTERNAL_CASE_IDS = (
    "tau2-airline-39-refund-eligibility",
    "tau2-airline-11-flight-downgrade",
    "tau2-airline-40-passenger-correction",
)

REWARD_BASIS = {"DB", "ENV_ASSERTION", "NL_ASSERTION", "ACTION", "COMMUNICATE"}
ASSERTION_KINDS = {
    "json_equals",
    "decimal_equals",
    "run_state",
    "effect_count",
    "citation_sources",
}
SUBSTANTIVE_KINDS = {"json_equals", "decimal_equals", "effect_count", "citation_sources"}

COLLECTION_FACTS = tuple(f13b_corpus.COLLECTION_FACTS) + ("fixture.sha256",)

REFERENCE_RE = re.compile(
    r"^sierra-research/tau2-bench@(?P<commit>[0-9a-f]{40})"
    r"#task=(?P<task_id>\d+)"
    r"#(?P<path>data/tau2/domains/airline/tasks\.json)$"
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

Issue = f13b_corpus.Issue


# ---------------------------------------------------------------------------
# loading / hashing
# ---------------------------------------------------------------------------
@dataclass
class External:
    """The committed external import: pin, adapted cases and registry."""

    directory: Path
    pin: dict
    cases_document: dict
    registry: dict
    license_bytes: bytes

    @property
    def cases(self) -> list[dict]:
        return list(self.cases_document.get("cases", []))

    def case(self, case_id: str) -> dict | None:
        for case in self.cases:
            if isinstance(case, dict) and case.get("case_id") == case_id:
                return case
        return None

    def entry(self, case_id: str) -> dict | None:
        entries = self.registry.get("cases")
        if isinstance(entries, dict):
            candidate = entries.get(case_id)
            if isinstance(candidate, dict):
                return candidate
        return None


def load_external(directory: Path | str | None = None) -> External:
    """Load the committed pin, adapted cases, registry and licence bytes."""
    root = Path(directory) if directory is not None else EXTERNAL_DIR
    pin = json.loads((root / "tau2" / "source_pin.json").read_text(encoding="utf-8"))
    document = json.loads((root / "tau2" / "tasks.json").read_text(encoding="utf-8"))
    registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    if not isinstance(pin, dict):
        raise ValueError("source_pin.json must be a JSON object")
    if not isinstance(document, dict) or not isinstance(document.get("cases"), list):
        raise ValueError("tau2/tasks.json must be an object with a cases array")
    if not isinstance(registry, dict):
        raise ValueError("registry.json must be a JSON object")
    license_bytes = (root / "tau2" / "LICENSE.tau2-mit.txt").read_bytes()
    return External(
        directory=root,
        pin=pin,
        cases_document=document,
        registry=registry,
        license_bytes=license_bytes,
    )


def canonical_bytes(payload: Any) -> bytes:
    """The canonical JSON bytes used for every recorded digest."""
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_task_sha256(task: dict) -> str:
    return sha256_hex(canonical_bytes(task))


def canonical_criteria_sha256(criteria: dict) -> str:
    return sha256_hex(canonical_bytes(criteria))


# ---------------------------------------------------------------------------
# the upstream empty-checker gate
# ---------------------------------------------------------------------------
_MISSING = object()


def reject_reason(task: Any, environment: dict | None = None) -> list[str]:
    """Stable refusal codes for one tau2-shaped source task.

    This is the import gate, not a copy of ``evaluate_simulation``: a missing
    ``evaluation_criteria`` is refused (upstream would return ``reward=1.0``), an
    empty ``reward_basis`` is refused (upstream would fall back to the default),
    a text-only assertion list is refused (no environment/action truth), and a
    task with no environment reference is refused before it can be adapted.
    """
    if not isinstance(task, dict):
        return ["invalid_task"]
    reasons: set[str] = set()
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        reasons.add("missing_task_id")

    user_scenario = task.get("user_scenario")
    instructions = (
        user_scenario.get("instructions") if isinstance(user_scenario, dict) else None
    )
    if not isinstance(user_scenario, dict) or not isinstance(instructions, (dict, str)):
        reasons.add("missing_user_scenario")

    has_environment = bool(environment) if isinstance(environment, dict) else False
    explicit_environment = task.get("environment")
    if isinstance(explicit_environment, dict) and explicit_environment:
        has_environment = True
    initial_state = task.get("initial_state")
    if isinstance(initial_state, dict) and initial_state:
        has_environment = True
    if isinstance(instructions, dict) and str(instructions.get("domain", "")).strip():
        has_environment = True
    if not has_environment:
        reasons.add("no_environment")

    criteria = task.get("evaluation_criteria", _MISSING)
    if criteria is _MISSING or criteria is None:
        # The upstream evaluator returns RewardInfo(reward=1.0) here.
        reasons.update(
            {
                "missing_checker",
                "empty_criteria",
                "default_success",
                "empty_reward_basis",
            }
        )
        return sorted(reasons)
    if not isinstance(criteria, dict):
        reasons.add("invalid_checker")
        return sorted(reasons)

    lists = {}
    for member in ("actions", "env_assertions", "communicate_info", "nl_assertions"):
        value = criteria.get(member)
        if value is not None and not isinstance(value, list):
            reasons.add("invalid_checker")
            value = []
        lists[member] = value or []
    action_count = len(lists["actions"])
    env_count = len(lists["env_assertions"])
    text_count = len(lists["communicate_info"]) + len(lists["nl_assertions"])
    if action_count == 0 and env_count == 0 and text_count == 0:
        reasons.add("empty_criteria")
    if action_count == 0 and env_count == 0:
        # No DB target and no environment assertion: either an empty checker or
        # a text-only one. Upstream scores these through the default path.
        reasons.add("no_actions")
        reasons.add("default_success")
        if text_count:
            reasons.add("text_only_checker")

    reward_basis = criteria.get("reward_basis", _MISSING)
    if reward_basis is _MISSING or reward_basis is None:
        reasons.add("empty_reward_basis")
    elif not isinstance(reward_basis, list):
        reasons.add("invalid_reward_basis")
    elif not reward_basis:
        reasons.add("empty_reward_basis")
    elif set(reward_basis) - REWARD_BASIS:
        reasons.add("invalid_reward_basis")
    else:
        # A declared reward component with no check behind it makes the fixed
        # upstream evaluator return reward=1.0 for that component: ``DB`` builds
        # the gold environment from ``actions``, ``ACTION`` compares those same
        # actions, ``ENV_ASSERTION`` multiplies an empty env_assertions list
        # (start value 1.0), and the NL/communicate components behave the same.
        # A task is only *effectively empty* when **no** declared component has
        # a check; a real upstream task may legitimately declare a vacuous
        # component alongside a substantive one (e.g. airline task 39:
        # ``reward_basis`` contains COMMUNICATE with an empty communicate_info
        # but 11 DB actions). Requiring at least one substantive component is
        # what makes ``reward_basis=["ENV_ASSERTION"]`` with empty
        # env_assertions a refusal instead of a default success.
        component_checks = {
            "DB": action_count,
            "ACTION": action_count,
            "ENV_ASSERTION": env_count,
            "NL_ASSERTION": len(lists["nl_assertions"]),
            "COMMUNICATE": len(lists["communicate_info"]),
        }
        if all(component_checks.get(basis, 0) == 0 for basis in reward_basis):
            reasons.add("reward_basis_without_assertions")
    return sorted(reasons)


def validate_source_pin(pin: Any) -> list[Issue]:
    """Every fact the source pin claims about the upstream checkout."""
    issues: list[Issue] = []
    if not isinstance(pin, dict):
        return [Issue("source_schema_mismatch", detail="the source pin must be a JSON object")]

    if pin.get("schema") != SOURCE_SCHEMA:
        issues.append(Issue("source_schema_mismatch", detail=f"schema must be {SOURCE_SCHEMA!r}"))
    if pin.get("repo") != REPO or pin.get("repo_url") != REPO_URL:
        issues.append(Issue("source_repo_mismatch", detail="repo or repo_url differs from the pinned source"))
    if pin.get("commit") != PINNED_COMMIT:
        issues.append(
            Issue(
                "source_commit_mismatch",
                detail=f"commit must be {PINNED_COMMIT!r}, found {pin.get('commit')!r}",
            )
        )

    licence = pin.get("license")
    if not isinstance(licence, dict):
        issues.append(Issue("source_unlicensed", detail="the licence block is missing"))
    else:
        if licence.get("spdx") != LICENSE_SPDX:
            issues.append(
                Issue("source_unlicensed", detail=f"SPDX must be {LICENSE_SPDX!r}")
            )
        if licence.get("review") != "reviewed":
            issues.append(Issue("license_not_reviewed", detail="the licence review is not final"))
        digest = str(licence.get("sha256", ""))
        if not _HEX64.match(digest):
            issues.append(Issue("license_hash_mismatch", detail="LICENSE sha256 is not 64-hex"))
        elif digest != EXPECTED_LICENSE_SHA256:
            issues.append(
                Issue("license_hash_mismatch", detail="LICENSE sha256 differs from the acquired file")
            )
        url = str(licence.get("source_url", ""))
        if not url.startswith(REPO_URL + "/blob/" + PINNED_COMMIT + "/"):
            issues.append(
                Issue("source_unlicensed", detail="the LICENSE source URL is not pinned to the commit")
            )
        if not str(licence.get("committed_path", "")).strip():
            issues.append(Issue("source_file_missing", detail="the committed LICENSE path is missing"))

    acquisition = pin.get("acquisition")
    if not isinstance(acquisition, dict):
        issues.append(Issue("source_acquisition_incomplete", detail="acquisition is missing"))
    else:
        commands = acquisition.get("commands")
        joined = "\n".join(str(item) for item in commands) if isinstance(commands, list) else ""
        if acquisition.get("result") != "ok":
            issues.append(Issue("source_acquisition_incomplete", detail="acquisition result is not ok"))
        if acquisition.get("network") != "available":
            issues.append(Issue("source_acquisition_incomplete", detail="network availability is not recorded"))
        if PINNED_COMMIT not in joined or "fetch --depth 1 origin" not in joined:
            issues.append(
                Issue("source_acquisition_incomplete", detail="the acquisition commands do not pin the commit")
            )

    files = pin.get("files")
    file_map: dict[str, dict] = {}
    if not isinstance(files, list) or not files:
        issues.append(Issue("source_file_missing", detail="pin.files must be a non-empty list"))
    else:
        for item in files:
            if not isinstance(item, dict) or not str(item.get("path", "")).strip():
                issues.append(Issue("source_file_pin_invalid", detail="a file pin has no path"))
                continue
            file_map[item["path"]] = item
            if not _HEX64.match(str(item.get("sha256", ""))):
                issues.append(
                    Issue("source_file_pin_invalid", where=item["path"], detail="sha256 is not 64-hex")
                )
            if not isinstance(item.get("size_bytes"), int) or item["size_bytes"] <= 0:
                issues.append(
                    Issue("source_file_pin_invalid", where=item["path"], detail="size_bytes must be > 0")
                )
    expected_hashes = {
        "LICENSE": EXPECTED_LICENSE_SHA256,
        SOURCE_PATH: EXPECTED_TASKS_SHA256,
        POLICY_PATH: EXPECTED_POLICY_SHA256,
        SPLIT_PATH: EXPECTED_SPLIT_SHA256,
        DB_PATH: EXPECTED_DB_SHA256,
        DATA_MODEL_PATH: EXPECTED_DATA_MODEL_SHA256,
        EVALUATOR_PATH: EXPECTED_EVALUATOR_SHA256,
        PYPROJECT_PATH: EXPECTED_PYPROJECT_SHA256,
    }
    for path, expected in expected_hashes.items():
        item = file_map.get(path)
        if item is None:
            issues.append(Issue("source_file_missing", where=path, detail="the file is not pinned"))
        elif item.get("sha256") != expected:
            issues.append(
                Issue(
                    "source_file_pin_invalid",
                    where=path,
                    detail="sha256 differs from the acquired upstream file",
                )
            )

    environment = pin.get("environment")
    if not isinstance(environment, dict):
        issues.append(Issue("no_environment", detail="pin.environment is missing"))
    else:
        if environment.get("domain") != "airline":
            issues.append(Issue("no_environment", detail="environment domain is not airline"))
        if environment.get("db_path") != DB_PATH:
            issues.append(Issue("no_environment", detail="environment DB path is not pinned"))
        if environment.get("db_sha256") != EXPECTED_DB_SHA256:
            issues.append(Issue("no_environment", detail="environment DB hash is not pinned"))

    pin_tasks = pin.get("tasks")
    task_map: dict[str, dict] = {}
    if not isinstance(pin_tasks, list):
        issues.append(Issue("source_task_missing", detail="pin.tasks must be a list"))
    else:
        for item in pin_tasks:
            if not isinstance(item, dict):
                issues.append(Issue("source_task_missing", detail="a task pin is not an object"))
                continue
            task_id = str(item.get("task_id", ""))
            task_map[task_id] = item
            if not _HEX64.match(str(item.get("canonical_sha256", ""))):
                issues.append(
                    Issue("source_task_missing", where=f"task={task_id}", detail="canonical_sha256 is not 64-hex")
                )
            criteria = item.get("criteria")
            if not isinstance(criteria, dict):
                issues.append(
                    Issue("source_task_missing", where=f"task={task_id}", detail="criteria extract is missing")
                )
            else:
                if canonical_criteria_sha256(criteria) != item.get("criteria_sha256"):
                    issues.append(
                        Issue(
                            "source_criteria_mismatch",
                            where=f"task={task_id}",
                            detail="criteria_sha256 does not match the recorded criteria",
                        )
                    )
                if not isinstance(criteria.get("actions"), list) or not criteria.get("actions"):
                    issues.append(
                        Issue("source_task_missing", where=f"task={task_id}", detail="criteria has no actions")
                    )
                if not criteria.get("reward_basis"):
                    issues.append(
                        Issue("source_task_missing", where=f"task={task_id}", detail="criteria has no reward_basis")
                    )
            if len(str(item.get("purpose", "")).strip()) < 20:
                issues.append(
                    Issue("source_task_missing", where=f"task={task_id}", detail="purpose is empty")
                )
    for task_id in SOURCE_TASK_IDS:
        if task_id not in task_map:
            issues.append(
                Issue("source_task_missing", where=f"task={task_id}", detail="the selected task is not pinned")
            )

    trap = pin.get("empty_checker_trap")
    if not isinstance(trap, dict):
        issues.append(Issue("source_trap_missing", detail="empty_checker_trap is missing"))
    else:
        if trap.get("path") != EVALUATOR_PATH:
            issues.append(Issue("source_trap_missing", detail="the trap path is not the evaluator"))
        if "reward=1.0" not in str(trap.get("observed", "")):
            issues.append(Issue("source_trap_missing", detail="the observed reward=1.0 snippet is missing"))
        if trap.get("import_gate") != "reject":
            issues.append(Issue("source_trap_missing", detail="the import gate is not reject"))
        if {"missing_checker", "empty_criteria", "empty_reward_basis"} - set(
            trap.get("reason_codes") or []
        ):
            issues.append(Issue("source_trap_missing", detail="the trap reason codes are incomplete"))
    return issues


def validate_tau2_source(
    pin: Any, tasks: Any, environment: dict | None = None
) -> list[Issue]:
    """Validate a pinned tau2 source and its task list before any import.

    ``tasks`` are tau2-shaped task objects (the upstream ``Task`` JSON shape).
    A task id that appears in the pin also has its recorded
    ``criteria_sha256`` re-checked, so a silently edited criteria cannot pass.
    """
    issues = list(validate_source_pin(pin))
    pin_task_map: dict[str, dict] = {}
    if isinstance(pin, dict) and isinstance(pin.get("tasks"), list):
        for item in pin["tasks"]:
            if isinstance(item, dict) and isinstance(item.get("task_id"), str):
                pin_task_map[item["task_id"]] = item
    context = environment
    if context is None and isinstance(pin, dict):
        context = pin.get("environment")
    if not isinstance(tasks, list):
        issues.append(Issue("invalid_task", detail="tasks must be a list"))
        return issues
    seen: set[str] = set()
    for index, task in enumerate(tasks):
        where = f"tasks/{index}"
        if not isinstance(task, dict):
            issues.append(Issue("invalid_task", where=where, detail="a task must be a JSON object"))
            continue
        task_id = task.get("id")
        if isinstance(task_id, str):
            if task_id in seen:
                issues.append(Issue("duplicate_task_id", where=where, detail="task id is duplicated"))
            seen.add(task_id)
        for code in reject_reason(task, context if isinstance(context, dict) else None):
            issues.append(Issue(code, where=where, detail=f"task {task_id!r}"))
        if isinstance(task_id, str):
            pinned = pin_task_map.get(task_id)
            if pinned is None:
                issues.append(
                    Issue("unpinned_source_task", where=where, detail=f"task {task_id!r} is not pinned")
                )
            else:
                criteria = task.get("evaluation_criteria")
                if isinstance(criteria, dict) and canonical_criteria_sha256(
                    criteria
                ) != pinned.get("criteria_sha256"):
                    issues.append(
                        Issue(
                            "source_criteria_mismatch",
                            where=where,
                            detail=f"task {task_id!r} criteria changed after the pin",
                        )
                    )
    return _dedupe(issues)


# ---------------------------------------------------------------------------
# adapted case gate
# ---------------------------------------------------------------------------
def validate_adapted_case(case: Any) -> list[Issue]:
    """The committed schema plus the external-import rules for one adapted case."""
    issues = list(f13b_corpus.validate_case(case))
    if not isinstance(case, dict):
        return issues
    case_id = case.get("case_id") if isinstance(case.get("case_id"), str) else None
    provenance = case.get("provenance")
    if isinstance(provenance, dict):
        if provenance.get("origin") != "adapted_external":
            issues.append(Issue("not_adapted", detail="origin must be adapted_external", case_id=case_id))
        if provenance.get("license_review") != "reviewed":
            issues.append(
                Issue("license_not_reviewed", detail="the adapted licence review is not final", case_id=case_id)
            )
        reference = str(provenance.get("reference", ""))
        match = REFERENCE_RE.match(reference)
        if match is None:
            issues.append(
                Issue(
                    "provenance_reference_mismatch",
                    detail="reference must be repo@commit#task=<id>#<tasks.json path>",
                    case_id=case_id,
                )
            )
        elif match.group("commit") != PINNED_COMMIT:
            issues.append(
                Issue(
                    "provenance_reference_mismatch",
                    detail="the reference commit is not the pinned commit",
                    case_id=case_id,
                )
            )
    assertions = case.get("assertions")
    if isinstance(assertions, list):
        kinds: set[str] = set()
        for item in assertions:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            if isinstance(kind, str):
                kinds.add(kind)
                if kind not in ASSERTION_KINDS:
                    issues.append(
                        Issue(
                            "unsupported_checker",
                            detail=f"assertion kind {kind!r} is not one of the five contract kinds",
                            case_id=case_id,
                        )
                    )
        if not kinds & SUBSTANTIVE_KINDS:
            issues.append(
                Issue(
                    "default_success_checker",
                    detail="a run_state-only checker cannot decide a task; add an observable assertion",
                    case_id=case_id,
                )
            )
    return _dedupe(issues)


def required_facts(case: dict) -> list[str]:
    """The facts collection must produce before this imported case may be decided."""
    facts = set(COLLECTION_FACTS)
    facts.update(f13b_corpus.derive_required_facts(case))
    return sorted(facts)


def is_decidable(case: Any, entry: Any) -> tuple[bool, list[str]]:
    """Whether an imported case can produce a verdict at all."""
    reasons = f13b_corpus.reject_reasons(case)
    if not isinstance(entry, dict):
        return False, sorted(set(reasons + ["missing_registry_entry"]))
    declared = entry.get("required_facts")
    if not isinstance(declared, list) or not declared:
        return False, sorted(set(reasons + ["missing_required_facts"]))
    missing = [
        f"missing_fact:{fact}"
        for fact in required_facts(case)
        if fact not in set(declared)
    ]
    return not reasons and not missing, sorted(set(reasons + missing))


# ---------------------------------------------------------------------------
# external registry validation
# ---------------------------------------------------------------------------
def validate_external(external: External, corpus: Any = None) -> list[Issue]:
    """Every cross-file rule the committed external import must satisfy."""
    issues: list[Issue] = []
    pin = external.pin

    # 1. the pinned source: recorded facts plus the task-shaped criteria extract.
    issues.extend(validate_source_pin(pin))
    pin_tasks = pin.get("tasks") if isinstance(pin, dict) else None
    if isinstance(pin_tasks, list):
        source_shaped = []
        for item in pin_tasks:
            if not isinstance(item, dict):
                continue
            source_shaped.append(
                {
                    "id": item.get("task_id"),
                    "user_scenario": {
                        "instructions": {
                            "domain": str((pin.get("environment") or {}).get("domain", "")),
                            "reason_for_call": "recorded criteria extract",
                            "known_info": None,
                            "unknown_info": None,
                            "task_instructions": "recorded criteria extract",
                        }
                    },
                    "initial_state": None,
                    "evaluation_criteria": item.get("criteria"),
                }
            )
        issues.extend(
            validate_tau2_source(pin, source_shaped, environment=pin.get("environment"))
        )

    registry = external.registry
    if registry.get("schema") != REGISTRY_SCHEMA:
        issues.append(Issue("registry_case_mismatch", detail=f"schema must be {REGISTRY_SCHEMA!r}"))
    if registry.get("generated_by") != "tests/f13b/build_external.py":
        issues.append(Issue("registry_case_mismatch", detail="generated_by is not the external builder"))
    if registry.get("usage_label") != USAGE_LABEL:
        issues.append(Issue("not_adapted", detail=f"usage_label must be {USAGE_LABEL!r}"))

    source_pin = registry.get("source_pin")
    if not isinstance(source_pin, dict):
        issues.append(Issue("source_file_missing", detail="registry.source_pin is missing"))
    else:
        if source_pin.get("sha256") != sha256_hex(
            (external.directory / "tau2" / "source_pin.json").read_bytes()
        ):
            issues.append(Issue("source_file_pin_invalid", detail="source_pin sha256 drifted"))
        if source_pin.get("commit") != PINNED_COMMIT:
            issues.append(Issue("source_commit_mismatch", detail="registry source pin commit drifted"))

    licence = registry.get("license")
    if not isinstance(licence, dict):
        issues.append(Issue("source_unlicensed", detail="registry.license is missing"))
    else:
        if licence.get("spdx") != LICENSE_SPDX or licence.get("license_id") != LICENSE_ID:
            issues.append(Issue("source_unlicensed", detail="registry licence id is not the pinned MIT id"))
        if licence.get("review") != "reviewed":
            issues.append(Issue("license_not_reviewed", detail="registry licence review is not final"))
        if licence.get("redistributable") is not True:
            issues.append(Issue("source_unlicensed", detail="the imported licence is not redistributable"))
        if licence.get("sha256") != EXPECTED_LICENSE_SHA256:
            issues.append(Issue("license_hash_mismatch", detail="registry licence sha256 drifted"))
        license_file = str(licence.get("license_file", ""))
        if not license_file:
            issues.append(Issue("source_file_missing", detail="registry licence file path is missing"))

    # 2. file pins and committed bytes.
    files = registry.get("files")
    if not isinstance(files, dict):
        issues.append(Issue("source_file_missing", detail="registry.files must be an object"))
        files = {}
    tasks_document = external.cases_document
    for relative, item in sorted(files.items()):
        target = external.directory / relative
        if not target.exists():
            issues.append(Issue("source_file_missing", where=relative, detail="pinned file does not exist"))
            continue
        payload = target.read_bytes()
        if not isinstance(item, dict):
            issues.append(Issue("source_file_pin_invalid", where=relative, detail="pin is not an object"))
            continue
        if item.get("sha256") != sha256_hex(payload):
            issues.append(Issue("source_file_pin_invalid", where=relative, detail="sha256 drifted"))
        if item.get("size_bytes") != len(payload):
            issues.append(Issue("source_file_pin_invalid", where=relative, detail="size drifted"))
        if relative == "tau2/tasks.json" and item.get("cases") != len(tasks_document.get("cases", [])):
            issues.append(Issue("source_file_pin_invalid", where=relative, detail="case count drifted"))

    # 3. fixtures: committed bytes, media type, path.
    fixtures = registry.get("fixtures")
    if not isinstance(fixtures, dict):
        issues.append(Issue("no_environment", detail="registry.fixtures must be an object"))
        fixtures = {}
    for fixture_id, fixture in sorted(fixtures.items()):
        if not isinstance(fixture, dict):
            issues.append(Issue("no_environment", where=f"fixtures/{fixture_id}", detail="not an object"))
            continue
        if fixture.get("kind") != "file":
            issues.append(Issue("no_environment", where=f"fixtures/{fixture_id}", detail="must be a file fixture"))
            continue
        if not str(fixture.get("media_type", "")).strip():
            issues.append(Issue("no_environment", where=f"fixtures/{fixture_id}", detail="media_type is empty"))
        relative = str(fixture.get("path", ""))
        if not relative:
            issues.append(Issue("no_environment", where=f"fixtures/{fixture_id}", detail="path is empty"))
            continue
        target = external.directory / relative
        if not target.exists():
            issues.append(Issue("source_file_missing", where=relative, detail="fixture does not exist"))
            continue
        if fixture.get("sha256") != sha256_hex(target.read_bytes()):
            issues.append(Issue("source_file_pin_invalid", where=relative, detail="fixture sha256 drifted"))

    # 4. families and per-case descriptions.
    families = registry.get("families")
    if not isinstance(families, dict):
        issues.append(Issue("missing_template", detail="registry.families must be an object"))
        families = {}
    for family_id, family in sorted(families.items()):
        if not isinstance(family, dict):
            issues.append(Issue("missing_template", where=f"families/{family_id}", detail="not an object"))
            continue
        if family.get("category") not in f13b_corpus.CATEGORIES:
            issues.append(Issue("registry_case_mismatch", where=f"families/{family_id}", detail="bad category"))
        if family.get("split") not in f13b_corpus.SPLITS:
            issues.append(Issue("registry_case_mismatch", where=f"families/{family_id}", detail="bad split"))
        if not str(family.get("template", "")).strip():
            issues.append(Issue("missing_template", where=f"families/{family_id}", detail="empty template"))

    entries = registry.get("cases")
    if not isinstance(entries, dict):
        issues.append(Issue("registry_case_mismatch", detail="registry.cases must be an object"))
        entries = {}
    case_ids = [case.get("case_id") for case in external.cases if isinstance(case, dict)]
    if len(case_ids) != len(set(case_ids)):
        issues.append(Issue("external_case_id_collision", detail="the adapted cases repeat an id"))
    for case in external.cases:
        if not isinstance(case, dict) or not isinstance(case.get("case_id"), str):
            continue
        case_id = case["case_id"]
        for issue in validate_adapted_case(case):
            issues.append(
                Issue(issue.code, where=issue.where, detail=issue.detail, case_id=case_id)
            )
        entry = entries.get(case_id)
        if not isinstance(entry, dict):
            issues.append(
                Issue("registry_case_mismatch", detail="the case has no registry description", case_id=case_id)
            )
            continue
        for key in ("category", "family_id", "split"):
            if entry.get(key) != case.get(key):
                issues.append(
                    Issue(
                        "registry_case_mismatch",
                        detail=f"registry {key} is {entry.get(key)!r}, case has {case.get(key)!r}",
                        case_id=case_id,
                    )
                )
        if entry.get("provenance") != case.get("provenance"):
            issues.append(Issue("provenance_mismatch", detail="registry provenance differs", case_id=case_id))
        if entry.get("fixture_id") != case.get("fixture_id"):
            issues.append(Issue("fixture_mismatch", detail="registry fixture differs", case_id=case_id))
        if case.get("fixture_id") not in fixtures:
            issues.append(Issue("fixture_mismatch", detail="case fixture is not registered", case_id=case_id))
        if entry.get("assertion_kinds") != sorted(
            {item.get("kind") for item in case.get("assertions", []) if isinstance(item, dict)}
        ):
            issues.append(Issue("registry_case_mismatch", detail="assertion kinds drifted", case_id=case_id))
        family = families.get(entry.get("family_id")) if entry.get("family_id") in families else None
        if isinstance(family, dict):
            if entry.get("template") != family.get("template"):
                issues.append(Issue("missing_template", detail="case template differs from family", case_id=case_id))
            if entry.get("split") != family.get("split"):
                issues.append(Issue("registry_case_mismatch", detail="case split differs from family", case_id=case_id))
        if not str(entry.get("template", "")).strip():
            issues.append(Issue("missing_template", detail="template is empty", case_id=case_id))
        if not str(entry.get("scenario", "")).strip():
            issues.append(Issue("missing_scenario", detail="scenario is empty", case_id=case_id))
        truth = entry.get("truth")
        if not isinstance(truth, dict) or len(str(truth.get("derivation", "")).strip()) < 20:
            issues.append(Issue("missing_truth", detail="truth derivation is missing", case_id=case_id))
        if entry.get("required_facts") != required_facts(case):
            issues.append(
                Issue("required_facts_mismatch", detail="required_facts does not match the checker", case_id=case_id)
            )
        decidable, reasons = is_decidable(case, entry)
        if not decidable:
            issues.append(
                Issue("required_facts_mismatch", detail=f"case is not decidable: {reasons}", case_id=case_id)
            )
        if entry.get("label") != USAGE_LABEL:
            issues.append(Issue("not_adapted", detail="case label is not adapted_external", case_id=case_id))
        adaptation = entry.get("adaptation")
        if not isinstance(adaptation, dict) or len(str(adaptation.get("summary", "")).strip()) < 20:
            issues.append(Issue("missing_adaptation", detail="adaptation summary is missing", case_id=case_id))
        else:
            for key in ("preserved", "changed"):
                if not isinstance(adaptation.get(key), list) or not adaptation.get(key):
                    issues.append(
                        Issue("missing_adaptation", detail=f"adaptation.{key} is empty", case_id=case_id)
                    )

        licence_entry = entry.get("license")
        if not isinstance(licence_entry, dict):
            issues.append(Issue("source_unlicensed", detail="case licence is missing", case_id=case_id))
        else:
            if licence_entry.get("origin") != "adapted_external":
                issues.append(Issue("not_adapted", detail="case licence origin is wrong", case_id=case_id))
            if licence_entry.get("license_id") != LICENSE_ID:
                issues.append(Issue("source_unlicensed", detail="case licence id is not traceable", case_id=case_id))
            if licence_entry.get("redistributable") is not True:
                issues.append(Issue("source_unlicensed", detail="case licence is not redistributable", case_id=case_id))
            if licence_entry.get("review") != "reviewed":
                issues.append(Issue("license_not_reviewed", detail="case licence review is not final", case_id=case_id))

        source = entry.get("source")
        if not isinstance(source, dict):
            issues.append(Issue("unknown_source_task", detail="source block is missing", case_id=case_id))
        else:
            source_id = str(source.get("task_id", ""))
            pinned = None
            if isinstance(pin_tasks, list):
                for item in pin_tasks:
                    if isinstance(item, dict) and str(item.get("task_id")) == source_id:
                        pinned = item
                        break
            if pinned is None:
                issues.append(
                    Issue("unknown_source_task", detail=f"task {source_id!r} is not pinned", case_id=case_id)
                )
            else:
                if source.get("commit") != PINNED_COMMIT or source.get("repo") != REPO:
                    issues.append(Issue("provenance_mismatch", detail="source repo/commit drifted", case_id=case_id))
                if source.get("path") != SOURCE_PATH:
                    issues.append(Issue("provenance_mismatch", detail="source path drifted", case_id=case_id))
                if source.get("canonical_sha256") != pinned.get("canonical_sha256"):
                    issues.append(Issue("provenance_mismatch", detail="source task hash drifted", case_id=case_id))
                if source.get("criteria_sha256") != pinned.get("criteria_sha256"):
                    issues.append(Issue("provenance_mismatch", detail="source criteria hash drifted", case_id=case_id))
                reference = str((case.get("provenance") or {}).get("reference", ""))
                match = REFERENCE_RE.match(reference)
                if match is not None and match.group("task_id") != source_id:
                    issues.append(
                        Issue(
                            "provenance_mismatch",
                            detail="provenance reference names a different upstream task",
                            case_id=case_id,
                        )
                    )

        environment = entry.get("environment")
        if not isinstance(environment, dict) or not environment:
            issues.append(Issue("no_environment", detail="the adapted case has no environment", case_id=case_id))
        else:
            if environment.get("kind") != "local_http_fixture":
                issues.append(Issue("no_environment", detail="environment kind is not a local fixture", case_id=case_id))
            if environment.get("fixture_id") != case.get("fixture_id"):
                issues.append(Issue("fixture_mismatch", detail="environment fixture differs", case_id=case_id))
            endpoints = environment.get("endpoints")
            if not isinstance(endpoints, list) or not endpoints:
                issues.append(Issue("no_environment", detail="environment endpoints are missing", case_id=case_id))
            else:
                for endpoint in endpoints:
                    if not isinstance(endpoint, dict) or not str(endpoint.get("capability", "")).startswith("booking."):
                        issues.append(
                            Issue("no_environment", detail="an endpoint has no booking capability", case_id=case_id)
                        )
            if environment.get("fixture_id") not in fixtures:
                issues.append(Issue("fixture_mismatch", detail="environment fixture is not registered", case_id=case_id))

        actions = entry.get("adapted_actions")
        if not isinstance(actions, list) or not actions:
            issues.append(Issue("no_actions", detail="the reference trajectory is missing", case_id=case_id))
        else:
            write_capabilities = {
                action.get("capability")
                for action in actions
                if isinstance(action, dict) and str(action.get("capability", "")).startswith("booking.")
            }
            if not write_capabilities:
                issues.append(Issue("no_actions", detail="the trajectory has no adapted write action", case_id=case_id))

    for case_id in sorted(set(entries) - {str(item) for item in case_ids}):
        issues.append(Issue("registry_case_mismatch", detail="the registry describes no such case", case_id=case_id))

    # 5. the summary must agree with the documents.
    summary = _summarize_external(external)
    declared = registry.get("categories")
    if not isinstance(declared, dict):
        issues.append(Issue("registry_case_mismatch", detail="registry.categories is missing"))
    else:
        for category, data in summary.items():
            entry = declared.get(category)
            if not isinstance(entry, dict):
                issues.append(Issue("registry_case_mismatch", detail=f"category {category} is missing"))
                continue
            for key in ("cases", "dev", "frozen_test"):
                if entry.get(key) != data[key]:
                    issues.append(
                        Issue(
                            "registry_case_mismatch",
                            detail=f"{category}.{key} is {entry.get(key)!r}, import has {data[key]!r}",
                        )
                    )
            if entry.get("families") != data["families"]:
                issues.append(Issue("registry_case_mismatch", detail=f"{category}.families drifted"))

    reference = registry.get("corpus_reference")
    if not isinstance(reference, dict) or not str(reference.get("registry_path", "")).strip():
        issues.append(Issue("registry_case_mismatch", detail="corpus_reference.registry_path is missing"))

    # 6. isolation against the T102 frozen corpus.
    issues.extend(isolation_issues(external, corpus=corpus))
    return _dedupe(issues)


def _summarize_external(external: External) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for case in external.cases:
        if not isinstance(case, dict):
            continue
        category = case.get("category")
        if category not in f13b_corpus.CATEGORIES:
            continue
        data = summary.setdefault(
            category, {"cases": 0, "dev": 0, "frozen_test": 0, "families": set()}
        )
        data["cases"] += 1
        if case.get("split") in f13b_corpus.SPLITS:
            data[case["split"]] += 1
        if isinstance(case.get("family_id"), str):
            data["families"].add(case["family_id"])
    for data in summary.values():
        data["families"] = sorted(data["families"])
    return summary


# ---------------------------------------------------------------------------
# T102 split isolation
# ---------------------------------------------------------------------------
def isolation_issues(external: External, corpus: Any = None) -> list[Issue]:
    """Family/template/near-duplicate isolation between the import and T102."""
    if corpus is None:
        corpus = f13b_corpus.load_corpus()
    issues: list[Issue] = []
    registry_entries = corpus.registry.get("cases") if isinstance(corpus.registry, dict) else {}
    registry_entries = registry_entries if isinstance(registry_entries, dict) else {}
    external_entries = external.registry.get("cases")
    external_entries = external_entries if isinstance(external_entries, dict) else {}

    records: list[tuple[dict, dict]] = []
    for case in corpus.cases:
        if isinstance(case, dict):
            records.append((case, registry_entries.get(case.get("case_id"), {})))
    for case in external.cases:
        if isinstance(case, dict):
            records.append((case, external_entries.get(case.get("case_id"), {})))

    family_splits: dict[str, set[str]] = {}
    for case, _entry in records:
        family = case.get("family_id")
        split = case.get("split")
        if isinstance(family, str) and isinstance(split, str):
            family_splits.setdefault(family, set()).add(split)
    for family, splits in sorted(family_splits.items()):
        if len(splits) > 1:
            issues.append(
                Issue("cross_split_family", detail=f"family {family!r} appears in {sorted(splits)}")
            )

    t102_families = set(corpus.registry.get("families") or {})
    imported_families = set(external.registry.get("families") or {})
    for family in sorted(t102_families & imported_families):
        issues.append(
            Issue("external_family_reused", detail=f"family {family!r} exists in the T102 corpus")
        )

    template_splits: dict[str, set[str]] = {}
    for case, entry in records:
        template = f13b_corpus.normalize_template(str(entry.get("template", "")))
        if not template:
            continue
        template_splits.setdefault(template, set()).add(str(case.get("split")))
    for template, splits in sorted(template_splits.items()):
        if len(splits) > 1:
            issues.append(
                Issue(
                    "cross_split_template",
                    detail=f"normalized template {template!r} appears in {sorted(splits)}",
                )
            )

    t102_ids = {case.get("case_id") for case in corpus.cases if isinstance(case, dict)}
    for case in external.cases:
        if not isinstance(case, dict):
            continue
        if case.get("case_id") in t102_ids:
            issues.append(
                Issue(
                    "external_case_id_collision",
                    detail="the imported case id already exists in the T102 corpus",
                    case_id=case.get("case_id"),
                )
            )
        external_entry = external_entries.get(case.get("case_id"), {})
        for t102_case in corpus.cases:
            if not isinstance(t102_case, dict):
                continue
            if case.get("split") == t102_case.get("split"):
                continue
            t102_entry = registry_entries.get(t102_case.get("case_id"), {})
            similarity = f13b_corpus.near_duplicate_similarity(
                f13b_corpus.leak_text(case, external_entry),
                f13b_corpus.leak_text(t102_case, t102_entry),
                n=f13b_corpus.NEAR_DUPLICATE_NGRAM,
            )
            if similarity >= f13b_corpus.NEAR_DUPLICATE_THRESHOLD:
                issues.append(
                    Issue(
                        "near_duplicate_leak",
                        detail=(
                            f"{case.get('case_id')} ({case.get('split')}) and "
                            f"{t102_case.get('case_id')} ({t102_case.get('split')}) are "
                            f"{similarity:.4f} similar"
                        ),
                        case_id=case.get("case_id"),
                    )
                )
    return _dedupe(issues)


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


# ---------------------------------------------------------------------------
# offline fixture model (stub, not the product checker)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Outcome:
    """One mirrored assertion verdict, with expected/actual evidence."""

    kind: str
    pointer: str = ""
    capability: str = ""
    expected: str = ""
    actual: str = ""
    passed: bool = False
    detail: str = ""

    def __str__(self) -> str:
        parts = [self.kind]
        if self.pointer:
            parts.append(self.pointer)
        if self.capability:
            parts.append(self.capability)
        parts.append(f"expected={self.expected}")
        parts.append(f"actual={self.actual}")
        if self.detail:
            parts.append(self.detail)
        return " ".join(parts)


@dataclass
class SimulationResult:
    """The offline fixture result of one adapted reference trajectory."""

    output: dict
    effects: dict[str, int] = field(default_factory=dict)
    run_state: str = "succeeded"
    citations: list[str] = field(default_factory=list)
    actions_applied: list[dict] = field(default_factory=list)


def load_fixture(external: External, fixture_id: str) -> dict:
    """Load one committed fixture and refuse a drifted hash."""
    fixture = external.registry.get("fixtures", {}).get(fixture_id)
    if not isinstance(fixture, dict):
        raise KeyError(f"fixture {fixture_id!r} is not registered")
    target = external.directory / str(fixture["path"])
    payload = target.read_bytes()
    if sha256_hex(payload) != fixture.get("sha256"):
        raise ValueError(f"fixture {fixture_id!r} drifted from its registry pin")
    document = json.loads(payload.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"fixture {fixture_id!r} must be a JSON object")
    return document


def _bookings_of(fixture: dict) -> dict[str, dict]:
    if isinstance(fixture.get("bookings"), list):
        return {
            booking["reservation_id"]: copy.deepcopy(booking)
            for booking in fixture["bookings"]
        }
    if isinstance(fixture.get("booking"), dict):
        booking = fixture["booking"]
        return {booking["reservation_id"]: copy.deepcopy(booking)}
    raise ValueError("the fixture has neither bookings nor booking")


def refund_eligible(booking: dict, policy: dict | None = None) -> bool:
    """The committed adapted refund policy, evaluated on one booking."""
    if booking.get("cabin") == "basic_economy":
        return False
    if booking.get("flight_departed"):
        return False
    if int(booking.get("booked_hours_ago", 10**9)) <= 24:
        return True
    if booking.get("has_insurance") and booking.get("passenger_illness"):
        return True
    return bool(booking.get("flight_cancelled_by_airline"))


def _decimal(value: Decimal) -> str:
    return f"{value:.2f}"


def simulate_reference(
    external: External,
    case_id: str,
    *,
    actions: list[dict] | None = None,
) -> SimulationResult:
    """Apply the adapted reference actions to the committed fixture.

    This is a deterministic offline model of the local booking fixture. It is a
    stub: it does not call the product runner, a model or tau2-bench. The result
    is used only to prove the imported checker is decidable and not vacuous.
    """
    case = external.case(case_id)
    entry = external.entry(case_id)
    if case is None or entry is None:
        raise KeyError(case_id)
    fixture = load_fixture(external, case["fixture_id"])
    bookings = _bookings_of(fixture)
    policy = fixture.get("policy") if isinstance(fixture.get("policy"), dict) else {}
    applied = copy.deepcopy(actions if actions is not None else entry["adapted_actions"])
    effects: dict[str, int] = {}
    refund = Decimal("0")
    for action in applied:
        if not isinstance(action, dict):
            raise ValueError("an adapted action must be an object")
        name = action.get("name")
        arguments = action.get("arguments") or {}
        if name == "get_customer":
            continue
        if name == "get_reservation":
            reservation_id = str(arguments.get("reservation_id", ""))
            if reservation_id not in bookings:
                raise KeyError(f"unknown reservation {reservation_id!r}")
            continue
        if name == "cancel_reservation":
            reservation_id = str(arguments.get("reservation_id", ""))
            booking = bookings[reservation_id]
            booking["status"] = "cancelled"
            if refund_eligible(booking, policy):
                refund += Decimal(str(booking.get("refund_amount", "0.00")))
            effects["booking.cancel"] = effects.get("booking.cancel", 0) + 1
            continue
        if name == "update_reservation_flights":
            reservation_id = str(arguments.get("reservation_id", ""))
            booking = bookings[reservation_id]
            booking["cabin"] = arguments["cabin"]
            booking["flights"] = copy.deepcopy(arguments["flights"])
            if "fare_paid" in booking and "fare_after_change" in booking:
                refund += Decimal(str(booking["fare_paid"])) - Decimal(
                    str(booking["fare_after_change"])
                )
            effects["booking.update_flights"] = effects.get("booking.update_flights", 0) + 1
            continue
        if name == "update_reservation_passengers":
            reservation_id = str(arguments.get("reservation_id", ""))
            bookings[reservation_id]["passengers"] = copy.deepcopy(arguments["passengers"])
            effects["booking.update_passengers"] = (
                effects.get("booking.update_passengers", 0) + 1
            )
            continue
        raise ValueError(f"unsupported adapted action {name!r}")
    try:
        refund_total = _decimal(refund)
    except InvalidOperation as error:  # pragma: no cover - guarded by committed data
        raise ValueError(f"the fixture produced an invalid refund total: {error}") from error
    return SimulationResult(
        output={"bookings": bookings, "refund_total": refund_total},
        effects=effects,
        run_state="succeeded",
        citations=[],
        actions_applied=applied,
    )


def _resolve_pointer(document: Any, pointer: str) -> tuple[Any, bool]:
    if pointer == "":
        return document, True
    if not pointer.startswith("/"):
        return None, False
    current = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return None, False
            current = current[token]
        elif isinstance(current, list):
            if token == "" or (len(token) > 1 and token[0] == "0"):
                return None, False
            try:
                index = int(token)
            except ValueError:
                return None, False
            if index < 0 or index >= len(current):
                return None, False
            current = current[index]
        else:
            return None, False
    return current, True


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_equal(left: Any, right: Any) -> bool:
    """JSON-typed equality: ``true``/``false`` never equal ``1``/``0``.

    Python's ``==`` treats ``bool`` as an ``int`` subclass, so ``True == 1`` and
    ``False == 0`` hold. That is not JSON semantics: the product's checker keeps
    the boolean/number distinction, and the imported AgentDojo gold results use
    booleans (``read: false``). A plain ``==`` let a gold ``false`` match a
    recorded ``0`` and reported a security expectation as met. This recursive
    comparison keeps the JSON types disjoint; numbers compare by value (JSON has
    a single number type), lists by position and objects by exact key set.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return (
            isinstance(left, bool)
            and isinstance(right, bool)
            and left == right
        )
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            json_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(
            json_equal(left[key], right[key]) for key in left
        )
    return False


def evaluate_assertions(case: dict, result: SimulationResult) -> list[Outcome]:
    """Mirror the five contract assertion kinds against a simulation result.

    This is a Python mirror used by the offline stub tests, not the Go checker.
    """
    outcomes: list[Outcome] = []
    for item in case.get("assertions", []):
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind == "json_equals":
            actual, found = _resolve_pointer(result.output, str(item.get("pointer", "")))
            expected = item.get("expected")
            passed = found and json_equal(actual, expected)
            outcomes.append(
                Outcome(
                    kind=kind,
                    pointer=str(item.get("pointer", "")),
                    expected=_compact(expected),
                    actual=_compact(actual) if found else "not_collected",
                    passed=passed,
                    detail="" if passed else "the value at this JSON Pointer is not equal",
                )
            )
        elif kind == "decimal_equals":
            actual, found = _resolve_pointer(result.output, str(item.get("pointer", "")))
            actual_literal = actual if isinstance(actual, str) else _compact(actual)
            try:
                passed = found and Decimal(str(actual_literal)) == Decimal(
                    str(item.get("expected"))
                )
            except (InvalidOperation, ValueError):
                passed = False
            outcomes.append(
                Outcome(
                    kind=kind,
                    pointer=str(item.get("pointer", "")),
                    expected=str(item.get("expected")),
                    actual=str(actual_literal) if found else "not_collected",
                    passed=passed,
                    detail="" if passed else "the decimal value differs",
                )
            )
        elif kind == "run_state":
            expected = str(item.get("expected"))
            passed = result.run_state == expected
            outcomes.append(
                Outcome(
                    kind=kind,
                    expected=expected,
                    actual=result.run_state,
                    passed=passed,
                    detail="" if passed else "the run reached another state",
                )
            )
        elif kind == "effect_count":
            capability = str(item.get("capability"))
            expected_count = int(item.get("expected", 0))
            actual_count = int(result.effects.get(capability, 0))
            passed = actual_count == expected_count
            outcomes.append(
                Outcome(
                    kind=kind,
                    capability=capability,
                    expected=str(expected_count),
                    actual=str(actual_count),
                    passed=passed,
                    detail="" if passed else "the ledger recorded another number of effects",
                )
            )
        elif kind == "citation_sources":
            allowed = set(item.get("allowed_source_ids") or [])
            outside = sorted({source for source in result.citations if source not in allowed})
            passed = not outside
            if passed and item.get("require_at_least_one") and not result.citations:
                passed = False
            outcomes.append(
                Outcome(
                    kind=kind,
                    expected=",".join(sorted(allowed)),
                    actual=",".join(result.citations),
                    passed=passed,
                    detail="" if passed else "the citations are not allowed",
                )
            )
        else:
            outcomes.append(
                Outcome(
                    kind=str(kind),
                    passed=False,
                    detail="this checker is not one of the five contract kinds",
                )
            )
    return outcomes


# ---------------------------------------------------------------------------
# small offline CLI (demonstration only; never a product or benchmark run)
# ---------------------------------------------------------------------------
def summary(external: External) -> dict:
    """The offline import summary: gate issues plus the stub fixture run."""
    issues = validate_external(external)
    cases: dict[str, dict] = {}
    for case in external.cases:
        case_id = case["case_id"]
        result = simulate_reference(external, case_id)
        outcomes = evaluate_assertions(case, result)
        cases[case_id] = {
            "source_task": external.entry(case_id)["source"]["task_id"],
            "assertions": len(outcomes),
            "passed": sum(1 for outcome in outcomes if outcome.passed),
            "failed": [str(outcome) for outcome in outcomes if not outcome.passed],
            "effects": result.effects,
            "refund_total": result.output.get("refund_total"),
        }
    return {
        "source": f"{REPO}@{PINNED_COMMIT}",
        "license": LICENSE_SPDX,
        "usage_label": USAGE_LABEL,
        "cases": len(external.cases),
        "source_tasks": sorted(entry["source"]["task_id"] for entry in external.registry["cases"].values()),
        "gate_issues": [str(issue) for issue in issues],
        "stub_execution": cases,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary",
        action="store_true",
        help="validate the committed import and run the offline fixture stub",
    )
    args = parser.parse_args(argv)
    if not args.summary:
        parser.print_help()
        return 2
    external = load_external()
    payload = summary(external)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    failed = any(item["failed"] for item in payload["stub_execution"].values())
    return 1 if payload["gate_issues"] or failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
