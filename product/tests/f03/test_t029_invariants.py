"""T029 acceptance: the database invariants behind the claim protocol.

These are schema facts demonstrated on real rows, not Go conventions:

* one live job per run (T027's index);
* one active top-level run per conversation (T027's index, including debug runs);
* one live attempt per run (T029's partial unique index).
"""

from __future__ import annotations

from support import f00b, t025, t027, stack as st
from support import t029
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)


def test_two_live_jobs_for_one_run_are_impossible(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    run = t029.queued_run(t029_stack, owner)
    job = t029.jobs_for_run(t029_stack.dbname, run["id"])[0]

    def insert_job(state: str):
        return st.psql(
            t029_stack.dbname,
            "INSERT INTO platform.jobs (id, run_id, kind, state) VALUES ("
            f"gen_random_uuid(), {st.quote_literal(run['id'])}::uuid, 'resume', "
            f"{st.quote_literal(state)})",
        )

    ready = insert_job("ready")
    assert ready.code != 0, "a second live job was accepted"
    assert "one_live_job_per_run" in ready.stderr.lower(), ready.stderr

    # A leased job is live too (the claim is still in flight).
    assert t029.execution_client(t029_stack).claim_job().status == 200
    held = insert_job("held")
    assert held.code != 0, "a held job was accepted next to a leased one"
    assert "one_live_job_per_run" in held.stderr.lower(), held.stderr

    # A terminal job no longer occupies the live slot.
    completed = insert_job("completed")
    assert completed.code == 0, completed.stderr
    assert len(t029.jobs_for_run(t029_stack.dbname, run["id"])) == 2


def test_one_active_top_level_run_per_conversation(t029_stack) -> None:
    _stack_owner = t029.golden_owner(t029_stack)
    owner = f00b.principal_id_for_login(t029_stack.dbname)
    snapshot = t027.seed_execution_snapshot(t029_stack.dbname, owner, application_id=None)
    input_artifact = t025.insert_artifact(
        t029_stack.dbname, owner, purpose="run_input", sha256="e" * 64
    )
    conversation = t029.new_uuid()

    def insert_run(conversation_id: str, state: str = "queued") -> str:
        run_id = t029.new_uuid()
        st.sql(
            t029_stack.dbname,
            "INSERT INTO platform.runs "
            "(id, conversation_id, owner_id, kind, execution_snapshot_id, manifest_sha256, "
            " input_artifact_id, checkpoint_thread_id, state, budget_limit, currency, phase) "
            "VALUES ("
            f"{st.quote_literal(run_id)}::uuid, {st.quote_literal(conversation_id)}::uuid, "
            f"{st.quote_literal(owner)}::uuid, 'debug', {st.quote_literal(snapshot)}::uuid, "
            f"{st.quote_literal('f' * 64)}, {st.quote_literal(input_artifact)}::uuid, "
            f"{st.quote_literal(t029.new_uuid())}::uuid, {st.quote_literal(state)}, "
            "1.00, 'USD', 'idle')",
        )
        return run_id

    first = insert_run(conversation)
    second = st.psql(
        t029_stack.dbname,
        "INSERT INTO platform.runs "
        "(id, conversation_id, owner_id, kind, execution_snapshot_id, manifest_sha256, "
        " input_artifact_id, checkpoint_thread_id, state, budget_limit, currency, phase) "
        "VALUES (gen_random_uuid(), "
        f"{st.quote_literal(conversation)}::uuid, {st.quote_literal(owner)}::uuid, 'debug', "
        f"{st.quote_literal(snapshot)}::uuid, {st.quote_literal('f' * 64)}, "
        f"{st.quote_literal(input_artifact)}::uuid, "
        f"{st.quote_literal(t029.new_uuid())}::uuid, 'queued', 1.00, 'USD', 'idle')",
    )
    assert second.code != 0, "a second active top-level run was accepted"
    assert "one_active_top_run_per_conversation" in second.stderr.lower(), second.stderr

    # Terminalising the first frees the conversation slot.
    st.sql(
        t029_stack.dbname,
        "UPDATE platform.runs SET state = 'succeeded', revision = revision + 1 "
        f"WHERE id = {st.quote_literal(first)}::uuid",
    )
    assert insert_run(conversation), "one active top-level run is allowed again"

    # A child run is not a top-level run and is excluded from the index. A child
    # must be a published run (the contract's debug CHECK forbids a parent), so
    # this seeds the version/review it references.
    application_id = t027.snapshot_row(t029_stack.dbname, snapshot)["application_id"]
    version_id = t025.seed_published_version(t029_stack.dbname, owner, application_id)
    child = st.psql(
        t029_stack.dbname,
        "INSERT INTO platform.runs "
        "(id, conversation_id, parent_run_id, parent_invocation_key, owner_id, kind, "
        " execution_snapshot_id, application_version_id, manifest_sha256, input_artifact_id, "
        " checkpoint_thread_id, state, budget_limit, currency, phase) VALUES (gen_random_uuid(), "
        f"{st.quote_literal(conversation)}::uuid, "
        f"{st.quote_literal(first)}::uuid, 'invocation-1', {st.quote_literal(owner)}::uuid, "
        f"'published', {st.quote_literal(snapshot)}::uuid, "
        f"{st.quote_literal(version_id)}::uuid, {st.quote_literal('f' * 64)}, "
        f"{st.quote_literal(input_artifact)}::uuid, "
        f"{st.quote_literal(t029.new_uuid())}::uuid, 'queued', 1.00, 'USD', 'idle')",
    )
    assert child.code == 0, child.stderr


def test_two_live_attempts_for_one_run_are_impossible(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    lease = t029.claim_lease(t029_stack, owner, execution_unit_id="unit-1")

    second = st.psql(
        t029_stack.dbname,
        "INSERT INTO platform.run_attempts "
        "(token, job_id, run_id, worker_id, execution_unit_id, lease_deadline, heartbeat_at) "
        "VALUES (gen_random_uuid(), "
        f"{st.quote_literal(lease['job_id'])}::uuid, "
        f"{st.quote_literal(lease['run_id'])}::uuid, 'worker', 'unit-2', "
        "now() + interval '30 seconds', now())",
    )
    assert second.code != 0, "a second live attempt was accepted"
    assert "run_attempts_one_live_per_run" in second.stderr.lower(), second.stderr

    exited = t029.execution_client(t029_stack).confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-1",
        evidence={"started": False},
    )
    assert exited.status == 200, exited.text

    replacement = st.psql(
        t029_stack.dbname,
        "INSERT INTO platform.run_attempts "
        "(token, job_id, run_id, worker_id, execution_unit_id, lease_deadline, heartbeat_at) "
        "VALUES (gen_random_uuid(), "
        f"{st.quote_literal(lease['job_id'])}::uuid, "
        f"{st.quote_literal(lease['run_id'])}::uuid, 'worker', 'unit-2', "
        "now() + interval '30 seconds', now())",
    )
    assert replacement.code == 0, replacement.stderr
    live = [
        row
        for row in t029.attempts_for_run(t029_stack.dbname, lease["run_id"])
        if row["exited_at"] is None and row["revoked_at"] is None
    ]
    assert len(live) == 1, live
