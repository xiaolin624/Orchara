"""T022 acceptance: the formal admin control interface and dedup semantics.

The admin CLI is the only way to create fixed-handler work in this batch. It
validates the kind whitelist and UUID/hash shapes on the command line (exit 2),
but semantically refuses nothing: the database enforces ownership and
uniqueness, and a dedup key that already names a different intention is a
conflict (exit 1) with no second row.
"""

from __future__ import annotations

import pytest

from support import f00b
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
    t022_stack,
)

USAGE_EXIT = 2


def test_enqueue_creates_one_ready_job_with_the_contract_shape(t022_stack) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    target = f00b.new_uuid()
    payload = f00b.enqueue_ok(
        t022_stack,
        kind=f00b.KIND_ARTIFACT_FINALIZE,
        target_id=target,
        owner_id=owner,
        dedup_key="finalize:" + target,
        expected_revision=1,
        expected_sha256="ab" * 32,
    )
    assert payload["state"] == "ready"
    assert payload["kind"] == "artifact_finalize"
    row = f00b.job_row(t022_stack.dbname, payload["id"])
    assert row["kind"] == "artifact_finalize"
    assert row["target_id"] == target
    assert row["owner_id"] == owner
    assert row["state"] == "ready"
    assert row["attempt_count"] == 0
    assert row["expected_revision"] == 1
    assert row["expected_sha256"] == "ab" * 32
    assert row["finished_at"] is None
    assert f00b.job_count(t022_stack.dbname) == 1


def test_the_same_request_is_idempotent(t022_stack) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    target = f00b.new_uuid()
    key = "finalize:same"
    first = f00b.enqueue_ok(
        t022_stack, kind="artifact_finalize", target_id=target, owner_id=owner,
        dedup_key=key, expected_revision=3,
    )
    second = f00b.enqueue_ok(
        t022_stack, kind="artifact_finalize", target_id=target, owner_id=owner,
        dedup_key=key, expected_revision=3,
    )
    assert first["id"] == second["id"], (first, second)
    assert f00b.job_count(t022_stack.dbname) == 1


def test_a_different_target_under_the_same_key_conflicts(t022_stack) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    first_target = f00b.new_uuid()
    second_target = f00b.new_uuid()
    f00b.enqueue_ok(
        t022_stack, kind="artifact_finalize", target_id=first_target, owner_id=owner,
        dedup_key="finalize:conflict",
    )
    completed = f00b.enqueue(
        t022_stack, kind="artifact_finalize", target_id=second_target, owner_id=owner,
        dedup_key="finalize:conflict",
    )
    assert completed.returncode == 1, completed.stdout
    assert "dedup key conflict" in completed.stderr, completed.stderr
    assert "target_id differs" in completed.stderr, completed.stderr
    assert completed.stdout.strip() == ""
    assert f00b.job_count(t022_stack.dbname) == 1


def test_a_different_kind_with_the_same_key_is_a_second_job(t022_stack) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    target = f00b.new_uuid()
    # dedup is scoped by (kind, dedup_key), exactly like the UNIQUE constraint.
    finalize = f00b.enqueue_ok(
        t022_stack, kind="artifact_finalize", target_id=target, owner_id=owner,
        dedup_key="shared-key",
    )
    other = f00b.enqueue_ok(
        t022_stack, kind="catalog_index", target_id=target, owner_id=owner,
        dedup_key="shared-key",
    )
    assert finalize["id"] != other["id"]
    assert f00b.job_count(t022_stack.dbname) == 2


@pytest.mark.parametrize(
    ("label", "kwargs", "needle"),
    [
        ("shell text as kind", {"kind": "sh -c id"}, "unknown kind"),
        ("unknown kind", {"kind": "not_a_kind"}, "unknown kind"),
        ("malformed target", {"target_id": "not-a-uuid"}, "target-id must be a UUID"),
        ("malformed owner", {"owner_id": "admin"}, "owner-id must be a UUID"),
        ("empty dedup", {"dedup_key": "  "}, "dedup-key is required"),
        ("short hash", {"expected_sha256": "abc"}, "expected-sha256"),
        ("bad hash", {"expected_sha256": "zz" * 32}, "expected-sha256"),
        ("malformed input artifact", {"input_artifact_id": "nope"}, "input-artifact-id"),
        ("zero revision", {"expected_revision": 0}, "expected-revision"),
        ("negative revision", {"expected_revision": -2}, "expected-revision"),
    ],
)
def test_bad_command_lines_exit_2_without_writing(
    t022_stack, label: str, kwargs: dict, needle: str
) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    target = f00b.new_uuid()
    arguments = {
        "kind": "artifact_finalize",
        "target_id": target,
        "owner_id": owner,
        "dedup_key": "cli-" + label.replace(" ", "-"),
    }
    arguments.update(kwargs)
    completed = f00b.enqueue(t022_stack, **arguments)
    assert completed.returncode == USAGE_EXIT, f"{label}: {completed.stdout} {completed.stderr}"
    assert needle in completed.stderr, f"{label}: {completed.stderr}"
    assert f00b.job_count(t022_stack.dbname) == 0


def test_the_maintenance_registry_refuses_uncompiled_or_unknown_kinds(t022_stack) -> None:
    for raw, needle in (("sh -c id", "unknown kind"), ("route_request", "no compiled handler")):
        completed = f00b.run_maintenance(t022_stack, "--once", "--kinds", raw)
        assert completed.returncode == 2, (raw, completed.stdout, completed.stderr)
        assert needle in completed.stderr, (raw, completed.stderr)


def test_an_unknown_owner_is_refused_by_the_foreign_key(t022_stack) -> None:
    target = f00b.new_uuid()
    completed = f00b.enqueue(
        t022_stack,
        kind="artifact_finalize",
        target_id=target,
        owner_id=f00b.new_uuid(),
        dedup_key="unknown-owner",
    )
    assert completed.returncode == 1, (completed.stdout, completed.stderr)
    assert "foreign key" in completed.stderr.lower(), completed.stderr
    assert f00b.job_count(t022_stack.dbname) == 0


def test_an_upper_case_hash_is_normalised_not_rejected(t022_stack) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    target = f00b.new_uuid()
    upper = ("AB" * 32)
    payload = f00b.enqueue_ok(
        t022_stack, kind="artifact_finalize", target_id=target, owner_id=owner,
        dedup_key="upper-hash", expected_sha256=upper,
    )
    row = f00b.job_row(t022_stack.dbname, payload["id"])
    assert row["expected_sha256"] == upper.lower()
