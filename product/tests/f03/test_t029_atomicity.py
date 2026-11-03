"""T029 acceptance: a failed claim leaves no half-lease behind.

The claim transaction writes the quota increment, allocation, attempt, job and
run in one transaction; the manifest bytes are verified and the sealed links are
minted *before* the commit. These tests break the claim after the quota increment
(missing/tampered bytes, no signer, a forced database failure) and then assert
that no attempt, no allocation, no `used` increment, no lease and no run
transition remain.
"""

from __future__ import annotations

from pathlib import Path

from support import stack as st
from support import t024, t029
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)


def assert_no_lease_side_effects(t029_stack, run: dict) -> None:
    assert t029.attempt_count(t029_stack.dbname) == 0, "an attempt row survived"
    jobs = t029.jobs_for_run(t029_stack.dbname, run["id"])
    assert len(jobs) == 1, jobs
    job = t029.job_row(t029_stack.dbname, jobs[0]["id"])
    assert job["state"] == "ready", job
    assert job["current_attempt_token"] is None, job

    stored_run = t029.run_state_row(t029_stack.dbname, run["id"])
    assert stored_run["state"] == "queued", stored_run
    assert stored_run["phase"] == "idle", stored_run
    assert stored_run["revision"] == 1, stored_run
    assert stored_run["started_at"] is None, stored_run

    assert t029.quota_counter(t029_stack.dbname)["used"] == 0
    assert t029.quota_allocations(t029_stack.dbname) == []


def manifest_object_key(t029_stack, run: dict) -> str:
    snapshot = t029.snapshot_row(t029_stack.dbname, run["execution_snapshot_id"])
    return t029.artifact_row(t029_stack.dbname, snapshot["manifest_artifact_id"])["object_key"]


def test_a_missing_sealed_manifest_rolls_the_claim_back(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    run = t029.queued_run(t029_stack, owner)
    t029.delete_object(t029_stack.object_dir, manifest_object_key(t029_stack, run))

    response = t029.execution_client(t029_stack).claim_job()
    assert response.status == 503, response.text
    assert response.json["error"]["code"] == "object_store_unavailable", response.text
    assert_no_lease_side_effects(t029_stack, run)


def test_tampered_sealed_manifest_bytes_roll_the_claim_back(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    run = t029.queued_run(t029_stack, owner)
    # Same length is irrelevant: the verifier hashes before it parses.
    t029.tamper_object(t029_stack.object_dir, manifest_object_key(t029_stack, run), b"{}")

    response = t029.execution_client(t029_stack).claim_job()
    assert response.status == 503, response.text
    assert response.json["error"]["code"] == "object_store_unavailable", response.text
    assert_no_lease_side_effects(t029_stack, run)


def test_an_unready_input_artifact_rolls_the_claim_back(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    run = t029.queued_run(t029_stack, owner)
    input_artifact = t029.run_state_row(t029_stack.dbname, run["id"])["input_artifact_id"]
    st.sql(
        t029_stack.dbname,
        "UPDATE platform.artifacts SET state = 'scanning' "
        f"WHERE id = {st.quote_literal(input_artifact)}::uuid",
    )

    response = t029.execution_client(t029_stack).claim_job()
    assert response.status == 503, response.text
    assert response.json["error"]["code"] == "object_store_unavailable", response.text
    assert_no_lease_side_effects(t029_stack, run)


def test_a_process_without_a_signing_key_never_issues_a_lease(
    t029_stack, api_server, tmp_path: Path
) -> None:
    owner = t029.golden_owner(t029_stack)
    run = t029.queued_run(t029_stack, owner)

    _server, certificates = t024.start_second_api(
        api_server,
        t029_stack,
        tmp_path / "no-signing-key",
        signing_key=None,
    )
    client = t029.ExecutionInternalClient(
        certificates.internal_url,
        certificates.ca.cert,
        certificates.client("supervisor"),
    )
    response = client.claim_job()
    assert response.status == 503, response.text
    assert response.json["error"]["code"] == "object_signing_unavailable", response.text
    assert_no_lease_side_effects(t029_stack, run)


def test_a_forced_failure_after_the_quota_increment_rolls_everything_back(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    run = t029.queued_run(t029_stack, owner)

    # The trigger fires on the attempt insert: by then the counter has already
    # been incremented and the allocation re-armed inside the transaction.
    st.sql(
        t029_stack.dbname,
        "CREATE OR REPLACE FUNCTION t029_force_attempt_failure() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 't029 forced attempt failure'; END $$;",
    )
    st.sql(
        t029_stack.dbname,
        "CREATE TRIGGER t029_force_attempt_failure BEFORE INSERT ON platform.run_attempts "
        "FOR EACH ROW EXECUTE FUNCTION t029_force_attempt_failure();",
    )

    response = t029.execution_client(t029_stack).claim_job()
    assert response.status == 500, response.text
    assert response.json["error"]["code"] == "internal_error", response.text
    assert_no_lease_side_effects(t029_stack, run)

    # The rollback left the work claimable: remove the fault and claim for real.
    st.sql(
        t029_stack.dbname,
        "DROP TRIGGER t029_force_attempt_failure ON platform.run_attempts",
    )
    lease = t029.execution_client(t029_stack).claim_job(execution_unit_id="unit-after-failure")
    assert lease.status == 200, lease.text
    t029.assert_lease(lease.json)
    assert t029.quota_counter(t029_stack.dbname)["used"] == 1
    assert len(t029.quota_allocations(t029_stack.dbname)) == 1
