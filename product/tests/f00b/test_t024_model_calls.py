"""T024 acceptance: controlled, metered model calls.

The worker names the candidate it owns and a logical key; the server loads the
job's fixture, resolves the registered provider, records one ledger row per
physical attempt with real usage and decimal money, and seals the provider's
answer as a ready artifact. The stub is a labelled provider profile (never a
stand-in for a real model), and the real path is honestly not configured here.
"""

from __future__ import annotations

import re
from pathlib import Path

from support import f00b, t024
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    t024_stack,
)

DECIMAL = re.compile(r"^\d+\.\d{8}$")


def call(stack, job, *, logical_key="compatibility:test", params=None, candidate_id=None):
    return stack.internal_client().model_call(
        job.job_id,
        job.attempt_token,
        logical_key=logical_key,
        model_profile_version_id=candidate_id or job.candidate["id"],
        params=params if params is not None else {"task": "model_compatibility"},
    )


def test_every_model_call_is_metered_with_real_usage(t024_stack) -> None:
    job = t024.prepare_model_validate_job(t024_stack)
    response = call(t024_stack, job)
    assert response.status == 200, response.text
    body = response.json
    assert set(body) == {"call_id", "state", "result_artifact_id", "data"}, body
    assert body["state"] == "succeeded", body
    assert body["result_artifact_id"], body
    assert body["data"]["provider"] == "stub"
    assert body["data"]["stub"] is True
    assert body["data"]["fixture_sha256"] == t024.sha256_hex(job.fixture.content)
    assert body["data"]["fixture_bytes"] == len(job.fixture.content)
    check = body["data"]["check"]
    assert check["name"] == "structured_output" and check["passed"] is True, check

    rows = t024.model_calls(t024_stack.dbname, job.job_id)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["id"] == body["call_id"]
    assert row["owner_id"] == job.owner_id
    assert row["background_job_id"] == job.job_id
    assert row["request_id"] is None, "a background call must not pretend to be a user request"
    assert row["model_profile_version_id"] == job.candidate["id"]
    assert row["scope_key"] == job.job_id
    assert row["logical_key"] == "compatibility:test"
    assert row["physical_attempt"] == 1
    assert row["state"] == "succeeded"
    assert row["result_artifact_id"] == body["result_artifact_id"]
    assert row["currency"] == "USD"
    assert row["tokens_in"] and int(row["tokens_in"]) > 0, row
    assert row["tokens_out"] and int(row["tokens_out"]) > 0, row
    assert DECIMAL.match(row["reserved_amount"]), row
    assert DECIMAL.match(row["charged_amount"]), row
    assert float(row["charged_amount"]) > 0, row
    assert row["params_sha256"] == t024.params_digest({"task": "model_compatibility"}), row

    result = f00b.artifact_row(t024_stack.dbname, body["result_artifact_id"])
    assert result["state"] == "ready"
    assert result["media_type"] == "application/json"
    assert result["owner_id"] == job.owner_id
    assert t024.job_artifact_links(t024_stack.dbname, job.job_id) == [
        {
            "background_job_id": job.job_id,
            "artifact_id": body["result_artifact_id"],
            "relation": "output",
            "purpose": "model_result",
        }
    ]


def test_the_answer_depends_on_the_real_fixture_bytes(t024_stack) -> None:
    first = t024.prepare_model_validate_job(
        t024_stack, fixture_content=t024.fixture_bytes("ok"), dedup_key="t024-fixture-a"
    )
    second = t024.prepare_model_validate_job(
        t024_stack,
        fixture_content=t024.fixture_bytes("ok") + b" padding-bytes",
        dedup_key="t024-fixture-b",
    )
    first_response = call(t024_stack, first, logical_key="fixture:a").json
    second_response = call(t024_stack, second, logical_key="fixture:b").json
    assert first_response["data"]["fixture_sha256"] == t024.sha256_hex(first.fixture.content)
    assert second_response["data"]["fixture_sha256"] == t024.sha256_hex(second.fixture.content)
    assert first_response["data"]["fixture_sha256"] != second_response["data"]["fixture_sha256"]
    first_tokens = int(t024.model_calls(t024_stack.dbname, first.job_id)[0]["tokens_in"])
    second_tokens = int(t024.model_calls(t024_stack.dbname, second.job_id)[0]["tokens_in"])
    assert first_tokens != second_tokens, "usage must be derived from the fixture the server read"


def test_only_the_jobs_candidate_and_kind_may_call_a_model(t024_stack) -> None:
    job = t024.prepare_model_validate_job(t024_stack)
    other_candidate = t024.seed_candidate(
        t024_stack.dbname, t024_stack.object_dir, job.owner_id
    )
    mismatch = call(t024_stack, job, candidate_id=other_candidate["id"])
    assert mismatch.status == 422, mismatch.text
    assert mismatch.json["error"]["code"] == "candidate_mismatch"

    revoked = t024.seed_candidate(
        t024_stack.dbname, t024_stack.object_dir, job.owner_id, revoked=True
    )
    revoked_job = t024.prepare_model_validate_job(
        t024_stack, candidate=revoked, dedup_key="t024-revoked-candidate"
    )
    revoked_response = call(t024_stack, revoked_job)
    assert revoked_response.status == 422, revoked_response.text
    assert revoked_response.json["error"]["code"] == "candidate_unavailable"

    passed = t024.seed_candidate(
        t024_stack.dbname, t024_stack.object_dir, job.owner_id, validation_state="passed"
    )
    passed_job = t024.prepare_model_validate_job(
        t024_stack, candidate=passed, dedup_key="t024-passed-candidate"
    )
    passed_response = call(t024_stack, passed_job)
    assert passed_response.status == 422, passed_response.text
    assert passed_response.json["error"]["code"] == "candidate_unavailable"

    # A kind that is not model_validate may not invoke a model at all.
    artifact = f00b.seed_artifact(
        t024_stack.dbname, t024_stack.object_dir, job.owner_id, state="scanning"
    )
    lease = t024.enqueue_and_claim_kind(
        t024_stack,
        t024.KIND_ARTIFACT_FINALIZE,
        target_id=artifact.id,
        owner_id=job.owner_id,
        dedup_key="t024-model-kind:" + artifact.id,
        expected_revision=1,
        expected_sha256=artifact.expected_sha256,
    )
    wrong_kind = t024_stack.internal_client().model_call(
        lease["job_id"],
        lease["attempt_token"],
        logical_key="wrong-kind",
        model_profile_version_id=artifact.id,
        params={"task": "x"},
    )
    assert wrong_kind.status == 422, wrong_kind.text
    assert wrong_kind.json["error"]["code"] == "model_not_allowed"

    # A validation job without a fixture cannot be given one by the worker.
    owner = job.owner_id
    candidate = t024.seed_candidate(t024_stack.dbname, t024_stack.object_dir, owner)
    payload = f00b.enqueue_ok(
        t024_stack,
        kind=t024.KIND_MODEL_VALIDATE,
        target_id=candidate["id"],
        owner_id=owner,
        dedup_key="t024-no-fixture-call",
    )
    lease = t024_stack.internal_client().claim(kinds=[t024.KIND_MODEL_VALIDATE]).json
    assert lease["job_id"] == payload["id"]
    no_fixture = t024_stack.internal_client().model_call(
        lease["job_id"],
        lease["attempt_token"],
        logical_key="no-fixture",
        model_profile_version_id=candidate["id"],
        params={"task": "x"},
    )
    assert no_fixture.status == 422, no_fixture.text
    assert no_fixture.json["error"]["code"] == "fixture_missing"


def test_the_same_logical_key_replays_and_different_params_conflict(t024_stack) -> None:
    job = t024.prepare_model_validate_job(t024_stack)
    first = call(t024_stack, job, logical_key="replay:1")
    assert first.status == 200, first.text
    replay = call(t024_stack, job, logical_key="replay:1")
    assert replay.status == 200, replay.text
    assert replay.json["call_id"] == first.json["call_id"]
    assert replay.json["result_artifact_id"] == first.json["result_artifact_id"]
    # A replay returns the original call's data, not a placeholder: the handler
    # re-derives its compatibility report from this map, so a marker would turn a
    # passed validation into a failed one.
    assert replay.json["data"] == first.json["data"], replay.json["data"]
    assert replay.json["data"]["provider"] == "stub"
    assert replay.json["data"]["stub"] is True
    assert replay.json["data"]["check"]["passed"] is True
    assert "replayed" not in replay.json["data"]
    assert len(t024.model_calls(t024_stack.dbname, job.job_id)) == 1

    conflict = call(t024_stack, job, logical_key="replay:1", params={"task": "different"})
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == "model_call_conflict"
    rows = t024.model_calls(t024_stack.dbname, job.job_id)
    assert len(rows) == 1, rows


def test_a_retry_after_a_failed_attempt_is_a_new_physical_attempt(t024_stack) -> None:
    job = t024.prepare_model_validate_job(t024_stack)
    params = {"task": "model_compatibility"}
    logical_key = "retry:after-failure"
    t024.insert_ledger_attempt(
        t024_stack.dbname,
        job_id=job.job_id,
        owner_id=job.owner_id,
        candidate_id=job.candidate["id"],
        logical_key=logical_key,
        params_sha256=t024.params_digest(params),
        physical_attempt=1,
        state="failed",
    )
    response = call(t024_stack, job, logical_key=logical_key, params=params)
    assert response.status == 200, response.text
    rows = t024.model_calls(t024_stack.dbname, job.job_id)
    assert [row["physical_attempt"] for row in rows] == [1, 2], rows
    assert rows[0]["state"] == "failed"
    assert rows[1]["state"] == "succeeded"
    assert rows[1]["id"] == response.json["call_id"]


def test_stale_and_malformed_attempt_tokens_are_refused(t024_stack) -> None:
    job = t024.prepare_model_validate_job(t024_stack)
    stale = t024_stack.internal_client().model_call(
        job.job_id,
        f00b.new_uuid(),
        logical_key="stale",
        model_profile_version_id=job.candidate["id"],
        params={"task": "x"},
    )
    assert stale.status == 409, stale.text
    assert stale.json["error"]["code"] == "stale_attempt"

    malformed = t024_stack.internal_client().model_call(
        job.job_id,
        "not-a-uuid",
        logical_key="malformed",
        model_profile_version_id=job.candidate["id"],
        params={"task": "x"},
    )
    assert malformed.status == 422, malformed.text
    assert malformed.json["error"]["code"] == "validation_failed"

    missing_job = t024_stack.internal_client().model_call(
        f00b.new_uuid(),
        job.attempt_token,
        logical_key="missing-job",
        model_profile_version_id=job.candidate["id"],
        params={"task": "x"},
    )
    assert missing_job.status == 404, missing_job.text
    assert missing_job.json["error"]["code"] == "job_not_found"
    assert t024.model_calls(t024_stack.dbname, job.job_id) == []


def test_a_business_artifact_is_never_accepted_as_a_model_fixture(t024_stack) -> None:
    """T024's boundary, in both enforcement places.

    A user upload (``parsed_data``/``report``/...) is readable but is not a
    synthetic fixture. The admin enqueue refuses it before writing a job, and the
    model-calls endpoint re-reads the job's input and refuses it again, so neither
    a stale job row nor a hand-written row can smuggle business content to a
    model.
    """
    owner = t024.owner_id(t024_stack.dbname)
    candidate = t024.seed_candidate(t024_stack.dbname, t024_stack.object_dir, owner)
    business = t024.seed_business_artifact(t024_stack.dbname, t024_stack.object_dir, owner)

    refused = f00b.enqueue(
        t024_stack,
        kind=t024.KIND_MODEL_VALIDATE,
        target_id=candidate["id"],
        owner_id=owner,
        dedup_key="t024-business-fixture",
        input_artifact_id=business.id,
    )
    assert refused.returncode == 1, (refused.stdout, refused.stderr)
    assert "model_fixture" in refused.stderr, refused.stderr
    assert f00b.job_count(t024_stack.dbname) == 0

    # A not-ready artifact is refused at the same boundary.
    not_ready = t024.seed_fixture(t024_stack.dbname, t024_stack.object_dir, owner, state="scanning")
    refused = f00b.enqueue(
        t024_stack,
        kind=t024.KIND_MODEL_VALIDATE,
        target_id=candidate["id"],
        owner_id=owner,
        dedup_key="t024-not-ready-fixture",
        input_artifact_id=not_ready.id,
    )
    assert refused.returncode == 1, (refused.stdout, refused.stderr)
    assert "ready" in refused.stderr, refused.stderr
    assert f00b.job_count(t024_stack.dbname) == 0

    # Another principal's synthetic fixture is refused too: a job owner cannot
    # borrow somebody else's fixture.
    stranger = f00b.seed_principal(t024_stack.dbname, "t024-fixture-stranger")
    foreign = t024.seed_fixture(t024_stack.dbname, t024_stack.object_dir, stranger)
    refused = f00b.enqueue(
        t024_stack,
        kind=t024.KIND_MODEL_VALIDATE,
        target_id=candidate["id"],
        owner_id=owner,
        dedup_key="t024-foreign-fixture",
        input_artifact_id=foreign.id,
    )
    assert refused.returncode == 1, (refused.stdout, refused.stderr)
    assert "different principal" in refused.stderr, refused.stderr
    assert f00b.job_count(t024_stack.dbname) == 0

    # The endpoint check is defense in depth: a job that was legitimately created
    # with a synthetic fixture is still refused if the artifact later stops being
    # one (or belongs to somebody else).
    job = t024.prepare_model_validate_job(t024_stack, dedup_key="t024-fixture-tampered-purpose")
    st.sql(
        t024_stack.dbname,
        "UPDATE platform.artifacts SET purpose = 'parsed_data' "
        f"WHERE id = {st.quote_literal(job.fixture.id)}::uuid",
    )
    refused_call = call(t024_stack, job)
    assert refused_call.status == 422, refused_call.text
    assert refused_call.json["error"]["code"] == "fixture_not_synthetic", refused_call.json
    assert t024.model_calls(t024_stack.dbname, job.job_id) == []

    job = t024.prepare_model_validate_job(t024_stack, dedup_key="t024-fixture-tampered-owner")
    st.sql(
        t024_stack.dbname,
        "UPDATE platform.artifacts SET owner_id = "
        f"{st.quote_literal(stranger)}::uuid "
        f"WHERE id = {st.quote_literal(job.fixture.id)}::uuid",
    )
    foreign_call = call(t024_stack, job)
    assert foreign_call.status == 422, foreign_call.text
    assert foreign_call.json["error"]["code"] == "fixture_missing", foreign_call.json
    assert t024.model_calls(t024_stack.dbname, job.job_id) == []


def test_the_real_provider_is_not_configured_and_never_leaks_its_reference(
    t024_stack, tmp_path: Path, api_server
) -> None:
    secret = "t024-real-secret-" + "s" * 24
    # A second API whose environment actually holds the deployer credential the
    # candidate references. No real endpoint is deployed, so the honest answer is
    # 503 and nothing is dispatched or billed.
    server, certificates = t024.start_second_api(
        api_server,
        t024_stack,
        tmp_path / "certs-real-provider",
        signing_key=t024_stack.signing_key,
        extra={"ORCHARA_MODEL_SECRET": secret},
    )
    owner = t024.owner_id(t024_stack.dbname)
    candidate = t024.seed_candidate(
        t024_stack.dbname,
        t024_stack.object_dir,
        owner,
        provider="openai",
        model_id="gpt-test",
        endpoint_ref="env:ORCHARA_MODEL_ENDPOINT",
        secret_ref="env:ORCHARA_MODEL_SECRET",
    )
    fixture = t024.seed_fixture(t024_stack.dbname, t024_stack.object_dir, owner)
    payload = f00b.enqueue_ok(
        t024_stack,
        kind=t024.KIND_MODEL_VALIDATE,
        target_id=candidate["id"],
        owner_id=owner,
        dedup_key="t024-real-provider:" + fixture.id,
        input_artifact_id=fixture.id,
    )
    client = t024_stack.internal_client(certificates=certificates)
    lease = client.claim(worker_id="t024-real-provider", kinds=[t024.KIND_MODEL_VALIDATE]).json
    assert lease["job_id"] == payload["id"]
    response = client.model_call(
        lease["job_id"],
        lease["attempt_token"],
        logical_key="real-provider",
        model_profile_version_id=candidate["id"],
        params={"task": "model_compatibility"},
    )
    assert response.status == 503, response.text
    assert response.json["error"]["code"] == "provider_not_configured"
    assert secret not in response.text
    assert "ORCHARA_MODEL_SECRET" not in response.text
    assert t024.model_calls(t024_stack.dbname, payload["id"]) == [], (
        "an undispatched attempt must not create a ledger row that could be read as a charge"
    )
    logs = server.logs()
    assert secret not in logs
    assert "ORCHARA_MODEL_SECRET" not in logs
