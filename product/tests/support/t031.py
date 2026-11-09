"""F03/T031 real-stack helpers: result proposals, identity evidence, finalizer.

T031 closes the execution loop started by T029: a worker's result is only a
proposal stored on the attempt, and the supervisor's exit evidence is combined
with it by one idempotent finalizer that commits the terminal Run exactly once.

Everything here is real: the stack is T029's (PostgreSQL + compiled API + real
mTLS + signed sealed links), exit evidence is a real HTTP request, and the output
artifact seeded by ``seed_output_artifact`` has real sealed bytes on disk.

Boundary (recorded in the report): the run/artifact *registration* endpoint is
T033. Tests therefore seed the output artifact + its ``run_artifacts`` link with
SQL; the bytes, the artifact row and every finalizer transition are real.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from support.runtime_stack import Stack
from support import stack as st
from support import t025, t027, t029

# Re-exports so a T031 test imports one module.
Session = t029.Session
admin_session = t029.admin_session
session_for = t029.session_for
invite_and_activate = t029.invite_and_activate
create_application_or_fail = t029.create_application_or_fail
new_uuid = t029.new_uuid
sql_utf8 = t029.sql_utf8
submit_debug = t029.submit_debug
seed_golden_draft = t029.seed_golden_draft
snapshot_row = t029.snapshot_row
jobs_for_run = t029.jobs_for_run
run_artifact_rows = t029.run_artifact_rows
GOLDEN_APPLICATION_ID = t029.GOLDEN_APPLICATION_ID
GOLDEN_SHA256 = t029.GOLDEN_SHA256
WORKER_IMAGE_DIGEST = t029.WORKER_IMAGE_DIGEST
COMPILER_VERSION = t029.COMPILER_VERSION

golden_owner = t029.golden_owner
minimal_owner = t029.minimal_owner
queued_run = t029.queued_run
claim_lease = t029.claim_lease
execution_client = t029.execution_client
exit_identity = t029.exit_identity
exit_evidence = t029.exit_evidence
attempt_row = t029.attempt_row
attempts_for_run = t029.attempts_for_run
attempt_count = t029.attempt_count
quota_counter = t029.quota_counter
quota_allocations = t029.quota_allocations
allocation_for_job = t029.allocation_for_job
set_quota_capacity = t029.set_quota_capacity
run_state_row = t029.run_state_row
job_row = t029.job_row
artifact_row = t029.artifact_row
policy_revision = t029.policy_revision
force_lease_expiry = t029.force_lease_expiry
revoke_attempt = t029.revoke_attempt
wait_until = t029.wait_until
run_path = t029.run_path


# ---------------------------------------------------------------------------
# contract Run shape (a reused helper must not assert a queued phase)
# ---------------------------------------------------------------------------
RUN_FIELDS = {
    "id",
    "state",
    "revision",
    "application_version_id",
    "parent_run_id",
    "output_artifact_id",
    "error_code",
    "kind",
    "execution_snapshot_id",
    "phase",
}
RUN_STATES = {
    "queued",
    "running",
    "waiting_approval",
    "waiting_children",
    "cancelling",
    "recovery_pending",
    "needs_reconciliation",
    "succeeded",
    "failed",
    "cancelled",
}


def assert_run_shape(run: dict) -> None:
    """The contract's ten Run fields, with value types, in any state."""
    assert set(run) == RUN_FIELDS, run
    assert isinstance(run["id"], str) and uuid.UUID(run["id"])
    assert run["state"] in RUN_STATES, run
    assert isinstance(run["revision"], int) and run["revision"] >= 1, run
    assert run["kind"] == "debug", run
    assert isinstance(run["execution_snapshot_id"], str) and uuid.UUID(
        run["execution_snapshot_id"]
    ), run
    assert run["phase"] in {"idle", "executing", "waiting", "finalizing"}, run
    for name in ("application_version_id", "parent_run_id", "output_artifact_id", "error_code"):
        assert name in run, run


def get_run(session: Session, run_id: str):
    return session.client.get(run_path(run_id))


# ---------------------------------------------------------------------------
# seeded output artifacts (registration endpoint is T033; the link is seeded)
# ---------------------------------------------------------------------------
def seed_output_artifact(
    stack: Stack,
    owner_id: str,
    *,
    content: bytes | None = None,
    purpose: str = "run_output",
    state: str = "ready",
    deleted: bool = False,
) -> str:
    """One artifact with real sealed bytes under the stack's object root.

    The row is inserted with SQL because T033 owns the run-bound registration
    endpoint; the bytes/sha256/size and every check the finalizer performs are
    real. ``state``/``deleted`` let a test seed the refused variants.
    """
    payload = content if content is not None else (b'{"result":"ok","run":"' + uuid.uuid4().bytes.hex().encode() + b'"}')
    digest = hashlib.sha256(payload).hexdigest()
    artifact_id = new_uuid()
    object_key = "sealed/" + hashlib.sha256((artifact_id + new_uuid()).encode()).hexdigest()
    path = stack.object_dir / object_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    deleted_sql = "now()" if deleted else "NULL"
    sql_utf8(
        stack.dbname,
        "INSERT INTO platform.artifacts "
        "(id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision, deleted_at) "
        "VALUES ("
        f"{st.quote_literal(artifact_id)}::uuid, {st.quote_literal(owner_id)}::uuid, "
        f"{st.quote_literal(object_key)}, {st.quote_literal(purpose)}, "
        f"{st.quote_literal(digest)}, 'application/json', {len(payload)}, "
        f"{st.quote_literal(state)}, 1, {deleted_sql})",
    )
    return artifact_id


def link_output(
    dbname: str,
    run_id: str,
    artifact_id: str,
    *,
    relation: str = "output",
    attempt_token: str | None = None,
) -> None:
    """Seed the run_artifacts registration T033 will write through its endpoint."""
    token_sql = "NULL" if attempt_token is None else f"{st.quote_literal(attempt_token)}::uuid"
    sql_utf8(
        dbname,
        "INSERT INTO platform.run_artifacts (run_id, artifact_id, attempt_token, relation) VALUES ("
        f"{st.quote_literal(run_id)}::uuid, {st.quote_literal(artifact_id)}::uuid, "
        f"{token_sql}, {st.quote_literal(relation)})",
    )


def unlink_output(dbname: str, run_id: str, artifact_id: str) -> None:
    sql_utf8(
        dbname,
        "DELETE FROM platform.run_artifacts WHERE "
        f"run_id = {st.quote_literal(run_id)}::uuid AND "
        f"artifact_id = {st.quote_literal(artifact_id)}::uuid",
    )


# ---------------------------------------------------------------------------
# result proposals
# ---------------------------------------------------------------------------
def result_body(
    lease: dict,
    *,
    suggested_state: str = "succeeded",
    event_id: str | None = None,
    output_artifact_id: str | None = None,
    checkpoint_id: str | None = None,
    error_code: str | None = None,
    **extra,
) -> dict:
    body = {
        "attempt_token": lease["attempt_token"],
        "event_id": event_id if event_id is not None else new_uuid(),
        "suggested_state": suggested_state,
    }
    if output_artifact_id is not None:
        body["output_artifact_id"] = output_artifact_id
    if checkpoint_id is not None:
        body["checkpoint_id"] = checkpoint_id
    if error_code is not None:
        body["error_code"] = error_code
    body.update(extra)
    return body


def reported_result(dbname: str, token: str) -> dict | None:
    attempt = attempt_row(dbname, token)
    if attempt["reported_result"] is None:
        return None
    return json.loads(attempt["reported_result"])


def terminal_states() -> set[str]:
    return {"succeeded", "failed", "cancelled"}


def allocations_for_holder(dbname: str, holder_key: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, holder_key, released_at::text AS released_at "
        "FROM platform.quota_allocations "
        f"WHERE holder_key = {st.quote_literal(holder_key)}",
    )


def run_artifact_count(dbname: str, run_id: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.run_artifacts WHERE "
            f"run_id = {st.quote_literal(run_id)}::uuid",
        )
    )


def claimed_with_output(
    stack: Stack, *, execution_unit_id: str = "unit-t031"
) -> tuple[t029.Owner, dict, str]:
    """One claimed run plus a ready, owned, linked output artifact."""
    owner = t029.golden_owner(stack)
    lease = t029.claim_lease(stack, owner, execution_unit_id=execution_unit_id)
    artifact = seed_output_artifact(stack, owner.creator.principal_id)
    link_output(stack.dbname, lease["run_id"], artifact)
    return owner, lease, artifact


def count(dbname: str, table: str, where: str = "TRUE") -> int:
    return int(st.sql(dbname, f"SELECT count(*) FROM platform.{table} WHERE {where}"))


def seed_child_run(
    stack: Stack,
    *,
    parent_run_id: str,
    owner_id: str,
    state: str = "running",
    invocation_key: str | None = None,
) -> str:
    """One real child Run row of the given parent (F03 §7 / T035 shape).

    The child is a *published* run (the contract's identity CHECK requires an
    application_version_id), backed by real application/version/snapshot rows.
    Children are otherwise T035's; T031's finalizer only needs to observe one.
    """
    application_id = t025.seed_application(stack.dbname, owner_id, name="t031 child host")
    version_id = t025.seed_published_version(stack.dbname, owner_id, application_id)
    snapshot_id = t027.seed_execution_snapshot(
        stack.dbname,
        owner_id,
        application_id=application_id,
        kind="published",
        application_version_id=version_id,
    )
    input_artifact = t025.insert_artifact(
        stack.dbname, owner_id, purpose="run_input", sha256=None
    )
    child_id = new_uuid()
    sql_utf8(
        stack.dbname,
        "INSERT INTO platform.runs "
        "(id, parent_run_id, parent_invocation_key, owner_id, kind, execution_snapshot_id, "
        " application_version_id, manifest_sha256, input_artifact_id, checkpoint_thread_id, "
        " state, budget_limit, currency) VALUES ("
        f"{st.quote_literal(child_id)}::uuid, {st.quote_literal(parent_run_id)}::uuid, "
        f"{st.quote_literal(invocation_key or ('child-' + child_id))}, "
        f"{st.quote_literal(owner_id)}::uuid, 'published', "
        f"{st.quote_literal(snapshot_id)}::uuid, {st.quote_literal(version_id)}::uuid, "
        f"{st.quote_literal('e' * 64)}, {st.quote_literal(input_artifact)}::uuid, "
        f"{st.quote_literal(new_uuid())}::uuid, {st.quote_literal(state)}, 1.00, 'USD')",
    )
    return child_id


def set_run_state(dbname: str, run_id: str, state: str) -> None:
    """Move a seeded child Run to a state, the way its own finalizer would."""
    sql_utf8(
        dbname,
        "UPDATE platform.runs SET state = " + st.quote_literal(state) +
        f" WHERE id = {st.quote_literal(run_id)}::uuid",
    )

