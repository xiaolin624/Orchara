"""T026 acceptance: the evaluation control interface (cmd/admin evaluations enqueue).

One transaction writes ``platform.evaluation_jobs`` (queued) and the
``application_evaluate`` background job that executes it. Bad flag combinations
exit 2 and write nothing; semantic refusals decided against the database exit 1 and
write nothing; a successful enqueue leaves the two rows linked.
"""

from __future__ import annotations

import pytest

from support import f00b, t024, t026, t027
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t026_stack,
)

USAGE_EXIT = 2


def _assert_nothing_written(stack) -> None:
    assert t026.evaluation_count(stack.dbname) == 0
    assert f00b.job_count(stack.dbname) == 0
    assert t026.report_count(stack.dbname) == 0


@pytest.mark.parametrize(
    ("label", "kwargs", "needle"),
    [
        ("missing kind", {"kind": ""}, "--kind is required"),
        ("unknown kind", {"kind": "delete_content"}, "--kind must be model or application"),
        (
            "model without candidate",
            {"kind": "model", "model_profile_version_id": None},
            "--model-profile-version-id is required",
        ),
        (
            "model with snapshot",
            {"kind": "model", "snapshot_id": "11111111-1111-4111-8111-111111111111"},
            "--snapshot-id is only valid",
        ),
        (
            "application without snapshot",
            {"kind": "application", "model_profile_version_id": None},
            "--snapshot-id is required",
        ),
        ("application with candidate", {"kind": "application"}, "--model-profile-version-id is only valid"),
        ("malformed dataset", {"dataset_artifact_id": "not-a-uuid"}, "must be a UUID"),
    ],
)
def test_bad_command_lines_exit_2_without_writing(t026_stack, label, kwargs, needle) -> None:
    owner = f00b.principal_id_for_login(t026_stack.dbname)
    dataset = t026.seed_dataset(t026_stack, owner)
    arguments = {
        "kind": "model",
        "dataset_artifact_id": dataset.id,
        "model_profile_version_id": t024.seed_candidate(
            t026_stack.dbname, t026_stack.object_dir, owner
        )["id"],
    }
    arguments.update(kwargs)
    arguments = {key: value for key, value in arguments.items() if value is not None}
    completed = t026.enqueue_evaluation(t026_stack, **arguments)
    assert completed.returncode == USAGE_EXIT, f"{label}: {completed.stdout} {completed.stderr}"
    assert needle in completed.stderr, f"{label}: {completed.stderr}"
    _assert_nothing_written(t026_stack)


def test_an_unknown_owner_login_is_refused(t026_stack) -> None:
    owner = f00b.principal_id_for_login(t026_stack.dbname)
    dataset = t026.seed_dataset(t026_stack, owner)
    candidate = t024.seed_candidate(t026_stack.dbname, t026_stack.object_dir, owner)
    completed = t026.enqueue_evaluation(
        t026_stack,
        owner_login="nobody-here",
        dataset_artifact_id=dataset.id,
        model_profile_version_id=candidate["id"],
    )
    assert completed.returncode == 1, (completed.stdout, completed.stderr)
    assert "no user with login" in completed.stderr
    _assert_nothing_written(t026_stack)


def test_a_missing_dataset_artifact_is_refused(t026_stack) -> None:
    owner = f00b.principal_id_for_login(t026_stack.dbname)
    candidate = t024.seed_candidate(t026_stack.dbname, t026_stack.object_dir, owner)
    completed = t026.enqueue_evaluation(
        t026_stack,
        dataset_artifact_id=t026.new_uuid(),
        model_profile_version_id=candidate["id"],
    )
    assert completed.returncode == 1, (completed.stdout, completed.stderr)
    assert "foreign key" in completed.stderr.lower(), completed.stderr
    _assert_nothing_written(t026_stack)


def test_a_missing_candidate_is_refused(t026_stack) -> None:
    owner = f00b.principal_id_for_login(t026_stack.dbname)
    dataset = t026.seed_dataset(t026_stack, owner)
    completed = t026.enqueue_evaluation(
        t026_stack,
        dataset_artifact_id=dataset.id,
        model_profile_version_id=t026.new_uuid(),
    )
    assert completed.returncode == 1, (completed.stdout, completed.stderr)
    assert "does not exist" in completed.stderr
    _assert_nothing_written(t026_stack)


def test_model_evaluation_enqueue_writes_both_rows_in_one_transaction(t026_stack) -> None:
    owner = f00b.principal_id_for_login(t026_stack.dbname)
    candidate = t024.seed_candidate(t026_stack.dbname, t026_stack.object_dir, owner)
    dataset = t026.seed_dataset(t026_stack, owner)
    payload = t026.enqueue_evaluation_ok(
        t026_stack,
        dataset_artifact_id=dataset.id,
        model_profile_version_id=candidate["id"],
    )
    assert payload["kind"] == "model"
    assert payload["state"] == "queued"

    evaluation = t026.evaluation_row(t026_stack.dbname, payload["id"])
    assert evaluation["kind"] == "model"
    assert evaluation["owner_id"] == owner
    assert evaluation["state"] == "queued"
    assert evaluation["background_job_id"] == payload["background_job_id"]
    assert evaluation["dataset_artifact_id"] == dataset.id
    assert evaluation["model_profile_version_id"] == candidate["id"]
    assert evaluation["snapshot_id"] is None
    assert evaluation["report_id"] is None
    assert evaluation["finished_at"] is None

    job = f00b.job_row(t026_stack.dbname, payload["background_job_id"])
    assert job["kind"] == "application_evaluate"
    assert job["state"] == "ready"
    assert job["target_id"] == payload["id"]
    assert job["owner_id"] == owner
    assert job["input_artifact_id"] == dataset.id
    assert job["dedup_key"] == payload["id"]
    assert job["attempt_count"] == 0


def test_application_kind_requires_a_real_snapshot_after_t027(t026_stack) -> None:
    owner = f00b.principal_id_for_login(t026_stack.dbname)
    dataset = t026.seed_dataset(t026_stack, owner)
    # T027's 0014 makes evaluation_jobs.snapshot_id a real FK; the enqueue path
    # now needs an execution snapshot row that exists and belongs to the owner.
    snapshot_id = t027.seed_execution_snapshot(t026_stack.dbname, owner)
    payload = t026.enqueue_evaluation_ok(
        t026_stack,
        kind="application",
        dataset_artifact_id=dataset.id,
        snapshot_id=snapshot_id,
    )
    evaluation = t026.evaluation_row(t026_stack.dbname, payload["id"])
    assert evaluation["kind"] == "application"
    assert evaluation["snapshot_id"] == snapshot_id
    assert evaluation["model_profile_version_id"] is None

    # The binding now resolves: the owner's real snapshot carries a manifest hash.
    assert f00b.job_row(t026_stack.dbname, payload["background_job_id"])["state"] == "ready"
    snapshot = t027.snapshot_row(t026_stack.dbname, snapshot_id)
    assert snapshot["manifest_sha256"]
