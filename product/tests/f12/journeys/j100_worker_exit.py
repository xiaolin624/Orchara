"""T100 journey 4: worker terminal dual evidence (submitWorkerResult + confirmAttemptExit).

Both arrival orders are exercised over the real internal mTLS protocol, on Runs
created by the real debug-submit HTTP entry:

* **result then exit**: the result is only a proposal stored on the attempt
  (Run stays ``running`` / ``phase=finalizing``); the supervisor's exit evidence
  commits the terminal Run exactly once (one revision bump, one completed job,
  one released quota allocation, one output link).
* **exit then result**: the exit alone parks the Run in ``recovery_pending`` and
  holds the job; the later result commits the same terminal state once.

The output artifact is registered through the product's run-scoped internal
artifact routes (createWorkerUpload -> signed PUT -> complete -> real
artifact_finalize -> register), never seeded with SQL. Independent truth comes
from ``platform.runs``, ``platform.jobs``, ``platform.run_attempts``,
``platform.quota_*`` and ``platform.run_artifacts`` in PostgreSQL.

The last section adds the product's own executor path: a real
``cmd/supervisor exec --once`` claims and runs a start -> python -> end Run in
WSL2/runsc, producing the same dual evidence internally. Its Decimal truth is
computed by this test from the input CSV bytes, not read from the product.
"""

from __future__ import annotations

import csv
import decimal
import io
import json
import secrets

from support import run_observations as observations
from support import execution_support as execution
from support import f00b, t025, t027, t029, t031, t033, t042
from t100_support import sha256_hex


def _owner(stack, label: str) -> t029.Owner:
    """A real creator plus an application whose draft is saved through the API."""
    admin = f00b.admin_session(stack)
    creator = f00b.invite_and_activate(
        stack, admin, f"t100.{label}-" + secrets.token_hex(3), roles=("creator",)
    )
    application_id = t027.application_with_definition(
        creator, t027.chain_definition([]), name=f"t100 {label}"
    )
    return t029.Owner(admin=admin, creator=creator, application_id=application_id)


def _claim(stack, owner, unit: str) -> dict:
    lease = t029.claim_lease(stack, owner, execution_unit_id=unit)
    assert lease["attempt_token"], lease
    return lease


def _upload_output(stack, lease: dict, content: bytes) -> tuple[str, str]:
    """Register a real output artifact through the run-scoped worker routes."""
    client = t033.artifact_client(stack, "worker")
    return t033.upload_run_artifact(
        stack,
        client,
        lease,
        content=content,
        purpose="final_output",
        media_type="application/json",
        relation="output",
    )


def _assert_run_artifact_links(stack, run_id: str, output_artifact_id: str) -> None:
    links = t033.run_artifact_links(stack.dbname, run_id)
    assert {link["relation"] for link in links} == {"input", "output"}, links
    assert output_artifact_id in {link["artifact_id"] for link in links}, links
    assert len(links) == 2, links


def _result_then_exit(stack, journey) -> None:
    owner = _owner(stack, "worker-result-first")
    db = stack.dbname
    lease = _claim(stack, owner, "t100-unit-result-first")
    artifact, artifact_sha = _upload_output(
        stack, lease, b'{"ok":true,"order":"result-first"}'
    )
    worker = t029.execution_client(stack, "worker")
    supervisor = t029.execution_client(stack, "supervisor")
    proposal = t031.result_body(
        lease,
        suggested_state="succeeded",
        output_artifact_id=artifact,
        checkpoint_id="t100-ckpt-result-first",
    )

    submitted = worker.submit_result(lease["run_id"], body=proposal)
    assert submitted.status == 200, submitted.text
    proposed = t029.run_state_row(db, lease["run_id"])
    assert proposed["state"] == "running", proposed
    assert proposed["phase"] == "finalizing", proposed
    assert proposed["finished_at"] is None, proposed
    assert t029.job_row(db, lease["job_id"])["state"] == "leased"
    assert t029.quota_counter(db)["used"] == 1, t029.quota_counter(db)
    attempt = t029.attempt_row(db, lease["attempt_token"])
    assert attempt["reported_result"] is not None and attempt["exited_at"] is None, attempt
    journey.assertion(
        "result-first: submitWorkerResult is only a proposal — the Run stays state=running, "
        "phase=finalizing, unfinished; the job stays leased, the attempt records the result and "
        "the execution slot is still allocated"
    )

    confirmed = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="t100-unit-result-first",
        evidence=t029.exit_evidence(lease, started=True, exit_code=0),
    )
    assert confirmed.status == 200, confirmed.text
    run = t029.run_state_row(db, lease["run_id"])
    assert run["state"] == "succeeded", run
    assert run["phase"] == "idle" and run["finished_at"] is not None, run
    assert run["revision"] == proposed["revision"] + 1 == 3, (proposed, run)
    assert run["output_artifact_id"] == artifact, run
    job = t029.job_row(db, lease["job_id"])
    assert job["state"] == "completed" and job["current_attempt_token"] is None, job
    attempt = t029.attempt_row(db, lease["attempt_token"])
    assert attempt["exited_at"] is not None and attempt["revoked_at"] is not None, attempt
    assert t029.quota_counter(db)["used"] == 0
    allocations = t031.allocations_for_holder(db, "job:" + lease["job_id"])
    assert len(allocations) == 1 and allocations[0]["released_at"] is not None, allocations
    _assert_run_artifact_links(stack, lease["run_id"], artifact)
    output_bytes = observations.artifact_content(owner.creator, artifact)
    assert output_bytes.status == 200, output_bytes.text
    assert sha256_hex(output_bytes.raw) == artifact_sha
    view = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert view.status == 200 and view.json["state"] == "succeeded", view.text
    assert view.json["output_artifact_id"] == artifact, view.json
    journey.assertion(
        "result-first: confirmAttemptExit commits the terminal Run exactly once (revision 3, "
        "succeeded, idle, finished_at set, output artifact linked), completes the job, stamps "
        "exited_at+revoked_at on the attempt, releases the single quota allocation and keeps "
        "exactly the input+output artifact links; the owner read and the artifact content agree"
    )

    journey.fact("result_first", {
        "run_id": lease["run_id"],
        "job_id": lease["job_id"],
        "output_artifact_id": artifact,
        "output_sha256": artifact_sha,
        "revision_after_proposal": proposed["revision"],
        "revision_after_exit": run["revision"],
        "phase_after_proposal": proposed["phase"],
        "state_after_exit": run["state"],
        "quota_released": len(allocations) == 1 and allocations[0]["released_at"] is not None,
    })
    repeated = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="t100-unit-result-first",
        evidence=t029.exit_evidence(lease, started=True, exit_code=0),
    )
    assert repeated.status == 200, repeated.text
    assert t029.run_state_row(db, lease["run_id"]) == run
    assert t029.job_row(db, lease["job_id"])["state"] == "completed"
    late = worker.submit_result(lease["run_id"], body=proposal)
    assert late.status == 409, late.text
    assert late.json["error"]["code"] == "run_terminal", late.text
    conflicting = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="t100-unit-result-first",
        evidence=t029.exit_evidence(lease, started=True, exit_code=1),
    )
    assert conflicting.status == 409, conflicting.text
    assert conflicting.json["error"]["code"] == "attempt_evidence_conflict", conflicting.text
    assert t029.run_state_row(db, lease["run_id"]) == run
    journey.assertion(
        "result-first boundaries: a repeated identical exit is idempotent (Run unchanged), a late "
        "result is 409 run_terminal, and different exit evidence is 409 attempt_evidence_conflict "
        "with no state change"
    )


def _exit_then_result(stack, journey) -> None:
    owner = _owner(stack, "worker-exit-first")
    db = stack.dbname
    lease = _claim(stack, owner, "t100-unit-exit-first")
    artifact, _sha = _upload_output(stack, lease, b'{"ok":true,"order":"exit-first"}')
    worker = t029.execution_client(stack, "worker")
    supervisor = t029.execution_client(stack, "supervisor")
    evidence = t029.exit_evidence(lease, started=True, exit_code=0)

    exited = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="t100-unit-exit-first",
        evidence=evidence,
    )
    assert exited.status == 200, exited.text
    parked = t029.run_state_row(db, lease["run_id"])
    assert parked["state"] == "recovery_pending", parked
    assert parked["phase"] == "idle" and parked["finished_at"] is None, parked
    assert parked["revision"] == 3, "claim 1->2 and the park 2->3"
    assert t029.job_row(db, lease["job_id"])["state"] == "held", t029.job_row(db, lease["job_id"])
    assert t029.quota_counter(db)["used"] == 0
    allocations = t031.allocations_for_holder(db, "job:" + lease["job_id"])
    assert len(allocations) == 1 and allocations[0]["released_at"] is not None, allocations
    journey.assertion(
        "exit-first: confirmAttemptExit alone parks the Run in recovery_pending (phase=idle, no "
        "finished_at, revision 3), holds the job instead of completing it, and releases the "
        "execution slot"
    )

    proposal = t031.result_body(
        lease,
        suggested_state="succeeded",
        output_artifact_id=artifact,
        checkpoint_id="t100-ckpt-exit-first",
    )
    completed = worker.submit_result(lease["run_id"], body=proposal)
    assert completed.status == 200, completed.text
    assert completed.json["state"] == "succeeded", completed.json
    run = t029.run_state_row(db, lease["run_id"])
    assert run["state"] == "succeeded", run
    assert run["phase"] == "idle" and run["finished_at"] is not None, run
    assert run["revision"] == parked["revision"] + 1 == 4, (parked, run)
    assert run["output_artifact_id"] == artifact, run
    job = t029.job_row(db, lease["job_id"])
    assert job["state"] == "completed" and job["current_attempt_token"] is None, job
    assert t029.quota_counter(db)["used"] == 0
    _assert_run_artifact_links(stack, lease["run_id"], artifact)
    journey.assertion(
        "exit-first: the later submitWorkerResult commits the same terminal Run once (revision 4, "
        "succeeded, output artifact), completes the held job and keeps exactly one released "
        "allocation"
    )

    journey.fact("exit_first", {
        "run_id": lease["run_id"],
        "job_id": lease["job_id"],
        "output_artifact_id": artifact,
        "state_after_exit_only": parked["state"],
        "revision_after_exit_only": parked["revision"],
        "revision_after_result": run["revision"],
        "state_after_result": run["state"],
        "job_state_after_exit_only": "held",
        "quota_released_after_exit_only": True,
    })
    repeated = supervisor.confirm_exit(
        lease["attempt_token"], execution_unit_id="t100-unit-exit-first", evidence=evidence
    )
    assert repeated.status == 200, repeated.text
    assert t029.run_state_row(db, lease["run_id"]) == run
    late = worker.submit_result(lease["run_id"], body=proposal)
    assert late.status == 409, late.text
    assert late.json["error"]["code"] == "run_terminal", late.text
    journey.assertion(
        "exit-first boundaries: a repeated identical exit is idempotent and a late result is 409 "
        "run_terminal; neither changes the committed Run"
    )


def _role_and_stale_token_boundaries(stack, journey) -> None:
    owner = _owner(stack, "worker-roles")
    db = stack.dbname
    lease = _claim(stack, owner, "t100-unit-roles")
    worker = t029.execution_client(stack, "worker")
    supervisor = t029.execution_client(stack, "supervisor")
    maintenance = t029.execution_client(stack, "maintenance")
    proposal = t031.result_body(
        lease, suggested_state="needs_reconciliation", checkpoint_id="t100-ckpt-roles"
    )

    denied = maintenance.submit_result(lease["run_id"], body=proposal)
    assert denied.status == 403, denied.text
    assert denied.json["error"]["code"] == "forbidden", denied.text
    assert t029.attempt_row(db, lease["attempt_token"])["reported_result"] is None
    accepted = worker.submit_result(lease["run_id"], body=proposal)
    assert accepted.status == 200, accepted.text
    interim = t029.run_state_row(db, lease["run_id"])
    assert interim["state"] == "running" and interim["phase"] == "finalizing", interim
    assert t029.job_row(db, lease["job_id"])["state"] == "leased"
    for client, label in ((worker, "worker"), (maintenance, "maintenance")):
        refused = client.confirm_exit(
            lease["attempt_token"],
            execution_unit_id="t100-unit-roles",
            evidence=t029.exit_evidence(lease, started=True),
        )
        assert refused.status == 403, (label, refused.text)
        assert refused.json["error"]["code"] == "forbidden", (label, refused.text)
    replayed = supervisor.submit_result(lease["run_id"], body=proposal)
    assert replayed.status == 200, replayed.text
    confirmed = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="t100-unit-roles",
        evidence=t029.exit_evidence(lease, started=True),
    )
    assert confirmed.status == 200, confirmed.text
    parked = t029.run_state_row(db, lease["run_id"])
    assert parked["state"] == "needs_reconciliation", parked
    assert parked["phase"] == "idle" and parked["finished_at"] is None, parked
    assert t029.job_row(db, lease["job_id"])["state"] == "completed"
    late = worker.submit_result(
        lease["run_id"],
        body=t031.result_body(lease, suggested_state="failed", error_code="late"),
    )
    assert late.status == 409, late.text
    assert late.json["error"]["code"] == "run_state_conflict", late.text
    journey.assertion(
        "roles: only a worker certificate may submit a result and only a supervisor certificate "
        "may confirm exit (maintenance is 403 on both); the needs_reconciliation proposal needs "
        "the exit to commit, then stays non-terminal (no finished_at) and a late proposal is 409 "
        "run_state_conflict"
    )

    other = _claim(stack, owner, "t100-unit-stale")
    cross = worker.submit_result(
        other["run_id"],
        attempt_token=lease["attempt_token"],
        suggested_state="needs_reconciliation",
        checkpoint_id="t100-cross",
    )
    assert cross.status == 409, cross.text
    assert cross.json["error"]["code"] == "stale_attempt", cross.text
    assert t029.attempt_row(db, other["attempt_token"])["reported_result"] is None
    assert t029.run_state_row(db, other["run_id"])["state"] == "running"
    journey.fact("role_and_stale", {
        "interim_state": interim["state"],
        "parked_state": parked["state"],
        "parked_finished_at": parked["finished_at"],
        "stale_token_run_id": other["run_id"],
        "stale_token_error": cross.json["error"]["code"],
    })
    journey.assertion(
        "a result carrying another attempt's token is 409 stale_attempt and stores nothing on "
        "either attempt"
    )


def _decimal_truth(content: bytes) -> dict:
    """The independent Decimal truth computed from the input CSV bytes."""
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


def _real_supervisor_execution(stack, journey) -> None:
    if not t033.sandbox_available():
        journey.variant(
            "real-supervisor-exec",
            "not_run",
            "WSL2/runsc is not installed on this host; the protocol half of this journey is "
            "still real (mTLS + PostgreSQL) and passes",
        )
        return
    owner = _owner(stack, "worker-real-exec")
    # Deployment seed (not a journey step): the preset sales_stats tool version a
    # dev deployment carries; the linear draft pins it by id.
    t025.seed_tool_version(
        stack.dbname, owner.creator.principal_id, tool_id=t042.PRESET_TOOL_ID
    )
    application_id = t042.application_for(owner, name="t100 real exec")
    csv_artifact = t042.ready_csv(stack, owner)
    run = t042.submit_linear_run(owner, application_id, artifact_id=csv_artifact)
    completed, report = execution.execute_once(stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["status"] == "executed", report
    assert report["outcome"] == "succeeded", report
    assert report["run_state"] == "succeeded", report
    assert report["sandbox"]["started"] is True and report["sandbox"]["exit_code"] == 0, report
    assert report["sandbox"]["cleanup"]["sandbox_list_matches"] == 0, report["sandbox"]["cleanup"]

    state = observations.wait_run_state(stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state
    output_id = observations.output_artifact_id(stack.dbname, run["id"])
    assert output_id, state
    attempts = t029.attempts_for_run(stack.dbname, run["id"])
    assert len(attempts) == 1, attempts
    assert attempts[0]["exited_at"] is not None and attempts[0]["revoked_at"] is not None, attempts
    job_row = t029.job_row(stack.dbname, attempts[0]["job_id"])
    assert job_row["state"] == "completed", job_row
    assert job_row["current_attempt_token"] is None, job_row

    results = observations.wait_node_results(owner.creator, run["id"], expected=4)
    data_rows = t042.artifacts_of_kind(results["items"], "data")
    assert len(data_rows) == 1, results["items"]
    data = observations.artifact_content(owner.creator, data_rows[0]["artifact_id"])
    assert data.status == 200, data.text
    product_summary = json.loads(data.raw)

    expected = _decimal_truth(t033.SALES_CSV)
    assert product_summary["total"] == expected["total"], (product_summary, expected)
    assert product_summary["regions"] == expected["regions"], (product_summary, expected)
    assert product_summary["order_count"] == expected["order_count"], (product_summary, expected)
    assert decimal.Decimal(product_summary["total"]) == decimal.Decimal("250.00")
    journey.fact("real_supervisor_exec", {
        "run_id": run["id"],
        "executor_report": {
            key: report.get(key)
            for key in ("status", "outcome", "run_state", "error_code")
        },
        "sandbox": {
            "started": report["sandbox"]["started"],
            "exit_code": report["sandbox"]["exit_code"],
            "cleanup": report["sandbox"]["cleanup"],
        },
        "attempts": len(attempts),
        "node_result_items": len(results["items"]),
        "independent_decimal_truth": expected,
        "product_summary": product_summary,
    })
    journey.assertion(
        "real executor: `cmd/supervisor exec --once` claims and runs start->python->end in "
        "WSL2/runsc; the Run is succeeded with one attempt carrying exited_at+revoked_at, the job "
        "is completed, four node results are owner-readable, and the product's Decimal summary "
        "equals this test's independently computed decimals from the input bytes"
    )
    journey.variant(
        "real-supervisor-exec",
        "passed",
        f"run {run['id']}: outcome={report['outcome']}, attempt exited+revoked, "
        f"sandbox cleanup={report['sandbox']['cleanup']}",
    )


def run_worker_exit_journey(stack, journey) -> None:
    _result_then_exit(stack, journey)
    _exit_then_result(stack, journey)
    _role_and_stale_token_boundaries(stack, journey)
    _real_supervisor_execution(stack, journey)
