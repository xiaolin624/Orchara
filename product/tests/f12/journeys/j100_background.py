"""T100 journey 5: background job claim -> heartbeat -> result (and the real handler).

Two layers, both real:

* **the product's background worker**: ``POST /tool-runs`` creates a real
  ``tool_run`` job, the real ``cmd/maintenance`` process claims it through the
  internal mTLS protocol, executes the fixed preset tool as a real Python
  subprocess and completes it; the sealed summary artefact is read back from the
  object directory and its Decimal facts are compared with this test's own
  computation from the input bytes;
* **the wire protocol precisely**: a second real job is claimed by the real
  maintenance certificate via ``claimBackgroundJob``, renewed via
  ``heartbeatBackgroundJob`` and completed via ``completeBackgroundJob`` with a
  deterministic business failure, with independent rows for the lease, the
  attempt count, the heartbeat deadline and the result event.

Boundaries: the public create/read authorization (foreign creator 404, missing
Idempotency-Key 400, same key + different body 409), the internal role gate
(worker/supervisor certificates cannot claim background jobs), an empty second
claim (204), and duplicate/late completions (idempotent replay; a different
event is 409 event_conflict and changes nothing).

The only test-side SQL is the disclosed clock-only lease-expiry in the stale
token boundary; every job, lease and result below is created by the product.
"""

from __future__ import annotations

import csv
import decimal
import hashlib
import io
import json
import secrets
import uuid

from support import f00b, stack as st, t030, t033

BAD_CSV = b"order_id,date,region,amount\no-1,2026-01-02,east,-1.00\n"


def _decimal_truth(content: bytes) -> dict:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    total = decimal.Decimal("0")
    regions: dict[str, decimal.Decimal] = {}
    count = 0
    for row in reader:
        amount = decimal.Decimal(row["amount"])
        total += amount
        regions[row["region"]] = regions.get(row["region"], decimal.Decimal("0")) + amount
        count += 1
    return {
        "total": str(total.quantize(decimal.Decimal("0.01"))),
        "regions": {
            region: str(value.quantize(decimal.Decimal("0.01")))
            for region, value in sorted(regions.items())
        },
        "order_count": count,
    }


def _sealed_json(stack, artifact_row: dict) -> dict:
    content = (stack.object_dir / artifact_row["object_key"]).read_bytes()
    assert hashlib.sha256(content).hexdigest() == artifact_row["sha256"], (
        "the sealed summary bytes changed"
    )
    return json.loads(content)


def _create(creator, artifact_id: str, *, key: str | None = None):
    created = t030.create_tool_run(creator, artifact_id, key=key)
    assert created.status == 202, created.text
    t030.assert_create_view(created.json)
    return created.json


def _real_handler_path(stack, journey) -> None:
    db = stack.dbname
    admin = f00b.admin_session(stack)
    creator = f00b.invite_and_activate(
        stack, admin, "t100.tool-owner-" + secrets.token_hex(3), roles=("creator",)
    )
    artifact_id = t030.upload_ready_artifact(stack, creator, t033.SALES_CSV)
    key = "t100-tool-run-" + secrets.token_hex(6)
    created = _create(creator, artifact_id, key=key)
    job_id = created["id"]
    assert created["tool"] == t030.TOOL and created["artifact_id"] == artifact_id, created
    job = f00b.job_row(db, job_id)
    assert job["kind"] == "tool_run" and job["state"] == "ready", job
    assert job["attempt_count"] == 0, job
    journey.fact("tool_run_enqueued", {
        "job_id": job_id,
        "kind": job["kind"],
        "state": job["state"],
        "artifact_id": artifact_id,
        "input_sha256": t030.artifact_row(db, artifact_id)["sha256"],
    })
    journey.assertion(
        "POST /api/v1/tool-runs (owner + CSRF + Idempotency-Key) returns 202 "
        "{id, state, tool, artifact_id}; PostgreSQL holds a ready tool_run job with attempt_count 0"
    )

    replay = t030.create_tool_run(creator, artifact_id, key=key)
    assert replay.status == 202 and replay.json == created, replay.text
    assert t030.count_tool_runs(db) == 1, "the same key created a second job"
    journey.assertion(
        "the same Idempotency-Key replays the identical 202 body and creates exactly one job"
    )

    # The real background worker: claim -> heartbeat -> execute -> complete.
    completed, reports = t030.run_tool_pools_once(stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    job_reports = [report for report in reports if report.get("job_id") == job_id]
    assert len(job_reports) == 1, reports
    assert job_reports[0]["result"] == "succeeded", job_reports
    journey.assertion(
        "the real `cmd/maintenance run --once --kinds tool_run,artifact_finalize` claims the job, "
        "executes the preset tool and completes it with result=succeeded"
    )

    view = t030.get_tool_run(creator, job_id)
    assert view.status == 200, view.text
    t030.assert_run_view(view.json)
    assert view.json["state"] == "succeeded", view.json
    assert view.json["error_code"] is None, view.json
    contract_job = creator.client.get(f"/api/v1/background-jobs/{job_id}")
    assert contract_job.status == 200, contract_job.text
    assert set(contract_job.json) == {"id", "kind", "state", "error_code"}, contract_job.json
    assert contract_job.json["kind"] == "tool_run", contract_job.json
    assert contract_job.json["state"] == "succeeded", contract_job.json
    assert contract_job.json["error_code"] is None, contract_job.json

    # Independent truth: job row, job->artifact links, sealed summary bytes.
    final = f00b.job_row(db, job_id)
    assert final["kind"] == "tool_run" and final["state"] == "succeeded", final
    assert final["attempt_count"] == 1, final
    assert final["error_code"] is None and final["failure_class"] is None, final
    assert final["result_event_id"] is not None, final
    assert final["finished_at"] is not None, final
    links = t030.job_artifacts(db, job_id)
    assert {link["purpose"] for link in links} >= {
        t030.PURPOSE_SUMMARY,
        t030.PURPOSE_ARTIFACT,
    }, links
    summary_row = t030.artifact_by_purpose(db, job_id, t030.PURPOSE_SUMMARY)
    assert summary_row is not None and summary_row["state"] == "ready", summary_row
    sealed_summary = _sealed_json(stack, summary_row)
    api_summary = t030.read_artifact_json(creator, summary_row["id"])
    expected = _decimal_truth(t033.SALES_CSV)
    assert sealed_summary == expected, (sealed_summary, expected)
    assert api_summary == sealed_summary, (api_summary, sealed_summary)
    assert sealed_summary["total"] == "250.00", sealed_summary
    assert decimal.Decimal(sealed_summary["total"]) == decimal.Decimal("250.00")
    # Read the receipt row directly: the shared helper orders by a column this
    # schema does not have (platform.idempotency_keys is (principal, method, path,
    # key) + request_sha256/response_code/response_json/expires_at).
    receipts = st.sql_rows(
        db,
        "SELECT principal_id::text AS principal_id, method, path, key, request_sha256, "
        "response_code, response_json::text AS response_json "
        "FROM platform.idempotency_keys ORDER BY key",
    )
    matching = [row for row in receipts if row["key"] == key]
    assert len(matching) == 1, matching
    assert matching[0]["response_code"] == 202, matching
    assert matching[0]["request_sha256"], matching
    journey.fact("tool_run_result", {
        "job_state": final["state"],
        "attempt_count": final["attempt_count"],
        "failure_class": final["failure_class"],
        "finished_at": final["finished_at"],
        "result_event_id": final["result_event_id"],
        "summary_artifact_id": summary_row["id"],
        "summary_sha256": summary_row["sha256"],
        "product_summary": sealed_summary,
        "independent_decimal_truth": expected,
        "idempotency_receipt": {
            "path": matching[0]["path"],
            "response_code": matching[0]["response_code"],
        },
    })
    journey.assertion(
        "independent truth: the job row is succeeded with attempt_count 1, a result_event_id, a "
        "finished_at and no error; the job owns ready tool_summary/tool_artifact links; the sealed "
        "summary bytes hash to the artifact row and their Decimal total/regions/count equal this "
        "test's own Decimal computation from the input CSV (250.00 total, 3 orders); the content "
        "route returns the same document; one idempotency receipt was recorded"
    )

    # -- create/read boundaries ------------------------------------------------
    other = f00b.invite_and_activate(
        stack, admin, "t100.tool-other-" + secrets.token_hex(3), roles=("creator",)
    )
    foreign_read = t030.get_tool_run(other, job_id)
    assert foreign_read.status == 404, foreign_read.text
    assert foreign_read.json["error"]["code"] == "resource_not_found", foreign_read.text
    foreign_content = t030.read_artifact_content(other, summary_row["id"])
    assert foreign_content.status == 404, foreign_content.text
    assert t030.get_tool_run(admin, job_id).status == 404, "an admin may not read another owner's job"

    missing_key = creator.client.post(
        "/api/v1/tool-runs",
        body={"tool": t030.TOOL, "artifact_id": artifact_id},
        csrf=creator.csrf,
    )
    assert missing_key.status == 400, missing_key.text
    assert missing_key.json["error"]["code"] == "idempotency_key_required", missing_key.text

    other_artifact = t030.upload_ready_artifact(stack, creator, t033.SALES_CSV)
    conflict = t030.create_tool_run(creator, other_artifact, key=key)
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == "idempotency_conflict", conflict.text
    assert t030.count_tool_runs(db) == 1, "the conflicting replay created a job"
    unknown_tool = t030.create_tool_run(creator, artifact_id, tool="shell")
    assert unknown_tool.status == 422, unknown_tool.text
    assert unknown_tool.json["error"]["details"]["field"] == "tool", unknown_tool.text
    journey.assertion(
        "public boundaries: another creator (and an admin) get 404 for the job and its summary "
        "artifact content; a missing Idempotency-Key is 400; the same key with a different body is "
        "409 idempotency_conflict; an unknown tool is 422 with details.field=tool — no job created"
    )


def _protocol_path(stack, journey) -> None:
    """claimBackgroundJob -> heartbeatBackgroundJob -> completeBackgroundJob."""
    db = stack.dbname
    admin = f00b.admin_session(stack)
    creator = f00b.invite_and_activate(
        stack, admin, "t100.protocol-" + secrets.token_hex(3), roles=("creator",)
    )
    artifact_id = t030.upload_ready_artifact(
        stack, creator, BAD_CSV, t030.CSV
    )
    created = _create(creator, artifact_id)
    job_id = created["id"]

    # The internal role gate: only a maintenance certificate may claim work.
    for role in ("worker", "supervisor"):
        refused = stack.internal_client(role).claim(
            worker_id=f"t100-{role}-claim", kinds=[t030.KIND_TOOL_RUN]
        )
        assert refused.status == 403, (role, refused.text)
        assert refused.json["error"]["code"] == "forbidden", (role, refused.text)
    journey.assertion(
        "claimBackgroundJob rejects worker and supervisor client certificates with 403 forbidden "
        "(only the maintenance capacity may claim background work)"
    )

    maintenance = stack.internal_client("maintenance")
    claimed = maintenance.claim(worker_id="t100-maintenance-unit", kinds=[t030.KIND_TOOL_RUN])
    assert claimed.status == 200, claimed.text
    lease = claimed.json
    assert set(lease) == {
        "job_id", "kind", "target_id", "attempt_token", "lease_deadline",
        "expected_revision", "input_artifact_id", "input_url",
    }, lease
    assert lease["job_id"] == job_id and lease["kind"] == t030.KIND_TOOL_RUN, lease
    token = lease["attempt_token"]
    leased = f00b.job_row(db, job_id)
    assert leased["state"] == "leased", leased
    assert leased["attempt_count"] == 1, leased
    assert leased["lease_token"] == token, leased
    assert leased["worker_id"] == "t100-maintenance-unit", leased
    first_deadline = leased["lease_deadline"]
    journey.fact("claim", {
        "job_id": job_id,
        "attempt_token": token,
        "attempt_count": leased["attempt_count"],
        "worker_id": leased["worker_id"],
        "lease_deadline": first_deadline,
    })
    journey.assertion(
        "claimBackgroundJob (maintenance certificate) returns the real lease and PostgreSQL shows "
        "state=leased, attempt_count=1, lease_token=attempt_token and the claiming worker id"
    )

    empty = maintenance.claim(worker_id="t100-second-worker", kinds=[t030.KIND_TOOL_RUN])
    assert empty.status == 204, empty.text
    journey.assertion("a second claim for the same kind is 204: the leased job cannot be taken twice")

    heartbeat = maintenance.heartbeat(job_id, token)
    assert heartbeat.status == 200, heartbeat.text
    assert heartbeat.json == {"id": job_id, "state": "leased"}, heartbeat.json
    renewed = f00b.job_row(db, job_id)
    assert renewed["lease_deadline"] > first_deadline, (first_deadline, renewed)
    assert renewed["lease_token"] == token and renewed["state"] == "leased", renewed
    journey.fact("heartbeat", {
        "lease_deadline_before": first_deadline,
        "lease_deadline_after": renewed["lease_deadline"],
        "renewed": renewed["lease_deadline"] > first_deadline,
    })
    journey.assertion(
        "heartbeatBackgroundJob returns {id, state:leased} and the database lease_deadline moves "
        "forward while the token and state stay unchanged"
    )

    # A retryable infrastructure result: the fixed tool_run retry table retries
    # tool_timeout, so the job returns to ready with backoff and the old attempt
    # token stops being authority.
    retry_event = str(uuid.uuid4())
    retried = maintenance.result(
        job_id,
        attempt_token=token,
        state="failed",
        error_code="tool_timeout",
        event_id=retry_event,
    )
    assert retried.status == 200, retried.text
    assert retried.json["state"] == "ready", retried.json
    waiting = f00b.job_row(db, job_id)
    assert waiting["state"] == "ready", waiting
    assert waiting["failure_class"] == "infrastructure", waiting
    assert waiting["error_code"] == "tool_timeout", waiting
    assert waiting["attempt_count"] == 1, waiting
    assert waiting["result_event_id"] is None, "a retried failure is not a result event"
    assert waiting["finished_at"] is None, waiting
    assert waiting["lease_token"] is None, waiting
    stale = maintenance.result(
        job_id,
        attempt_token=token,
        state="failed",
        error_code="negative_amount",
        event_id=str(uuid.uuid4()),
    )
    assert stale.status == 409, stale.text
    assert stale.json["error"]["code"] == "stale_attempt", stale.text
    stale_heartbeat = maintenance.heartbeat(job_id, token)
    assert stale_heartbeat.status == 409, stale_heartbeat.text
    assert stale_heartbeat.json["error"]["code"] == "stale_attempt", stale_heartbeat.text
    assert f00b.job_row(db, job_id) == waiting
    journey.assertion(
        "retry/recovery boundary: a retryable infrastructure result (tool_timeout) returns the job "
        "to ready with failure_class=infrastructure, attempt_count 1, no result event, no finish, "
        "and the old attempt token is refused with 409 stale_attempt on result and heartbeat"
    )

    f00b.wait_until(
        lambda: st.sql(
            db,
            "SELECT available_at <= now() FROM platform.background_jobs "
            f"WHERE id = {st.quote_literal(job_id)}::uuid",
        )
        == "t",
        timeout=5.0,
        interval=0.01,
        what="the retried job to become claimable",
    )
    retry_claim = maintenance.claim(worker_id="t100-maintenance-unit", kinds=[t030.KIND_TOOL_RUN])
    assert retry_claim.status == 200, retry_claim.text
    retry_lease = retry_claim.json
    assert retry_lease["job_id"] == job_id, retry_lease
    retry_token = retry_lease["attempt_token"]
    assert retry_token != token, "the retry reused the old attempt token"
    reclaimed = f00b.job_row(db, job_id)
    assert reclaimed["state"] == "leased" and reclaimed["attempt_count"] == 2, reclaimed
    assert maintenance.heartbeat(job_id, retry_token).status == 200
    journey.assertion(
        "the retried job is claimable again after its backoff: a second claim returns a new "
        "attempt token and attempt_count=2, and the new token can heartbeat"
    )

    # A deterministic business rejection is terminal and never retried.
    event = str(uuid.uuid4())
    completed = maintenance.result(
        job_id,
        attempt_token=retry_token,
        state="failed",
        error_code="negative_amount",
        event_id=event,
    )
    assert completed.status == 200, completed.text
    assert completed.json["state"] == "failed", completed.json
    final = f00b.job_row(db, job_id)
    assert final["state"] == "failed", final
    assert final["failure_class"] == "business", final
    assert final["attempt_count"] == 2, final
    assert final["result_event_id"] == event, final
    assert final["finished_at"] is not None, final
    assert final["lease_token"] is None and final["worker_id"] is None, final
    assert final["error_code"] == "negative_amount", final
    view = t030.get_tool_run(creator, job_id)
    assert view.status == 200, view.text
    assert view.json["state"] == "failed", view.json
    assert view.json["error_code"] == "negative_amount", view.json
    journey.fact("complete", {
        "retry_event": retry_event,
        "terminal_event": event,
        "state": final["state"],
        "failure_class": final["failure_class"],
        "error_code": final["error_code"],
        "attempt_count": final["attempt_count"],
        "finished_at": final["finished_at"],
    })
    journey.assertion(
        "completeBackgroundJob commits the terminal business result: state=failed, "
        "failure_class=business, attempt_count=2, result_event_id/finished_at set, lease cleared, "
        "no third attempt, and the public GET /tool-runs/{id} shows the same state/error_code"
    )

    replay = maintenance.result(
        job_id,
        attempt_token=retry_token,
        state="failed",
        error_code="negative_amount",
        event_id=event,
    )
    assert replay.status == 200, replay.text
    assert replay.json == completed.json, (replay.json, completed.json)
    assert f00b.job_row(db, job_id) == final
    different = maintenance.result(
        job_id,
        attempt_token=retry_token,
        state="succeeded",
        event_id=str(uuid.uuid4()),
    )
    assert different.status == 409, different.text
    assert different.json["error"]["code"] == "event_conflict", different.text
    assert f00b.job_row(db, job_id) == final
    assert maintenance.claim(worker_id="t100-after", kinds=[t030.KIND_TOOL_RUN]).status == 204
    journey.assertion(
        "duplicate delivery is safe: the identical terminal event replays 200 without changing the "
        "row; a different event after the terminal result is 409 event_conflict; no further claim "
        "is possible for the finished job"
    )


def run_background_journey(stack, journey) -> None:
    _real_handler_path(stack, journey)
    _protocol_path(stack, journey)
