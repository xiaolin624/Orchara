"""F03/T035 real-stack helpers: waiting conditions and the single resume.

T035 stops a Run at a worker-supplied checkpoint (``waiting_approval`` /
``waiting_children``), releases the execution slot only after the supervisor
confirms the exit, stores the external decision once, and resumes the same logical
Run exactly once with a sealed canonical resume payload.

Everything here is real: T031's stack (PostgreSQL + compiled API + real mTLS +
real object bytes + HMAC-signed sealed links), real HTTP/mTLS calls and real
database rows. The two non-contract surfaces are documented bridges, not mocks:

* ``POST /internal/v1/runs/{id}/waiting-resolution`` (supervisor mTLS) stands in
  for the not-yet-built F06 approval / F07 child-result sources;
* the ``X-Run-Waiting-*`` / ``X-Run-Resume-*`` Run-detail headers (the contract
  Run schema has no waiting fields).

Child Runs are seeded through the real ``platform.runs`` constraints (the same
seeding boundary T031/T034 use for a feature whose producer is a later ticket:
creating a child through a public entry is F07's job).
"""

from __future__ import annotations

import json

from support import stack as st
from support import t029, t031, t032, t034

# Re-exports so a T035 test imports one module.
T035Stack = t034.T034Stack
Session = t034.Session
admin_session = t034.admin_session
session_for = t034.session_for
invite_and_activate = t034.invite_and_activate
create_application_or_fail = t034.create_application_or_fail
new_uuid = t034.new_uuid
sql_utf8 = t034.sql_utf8
submit_debug = t034.submit_debug
seed_golden_draft = t034.seed_golden_draft
GOLDEN_APPLICATION_ID = t034.GOLDEN_APPLICATION_ID
GOLDEN_SHA256 = t034.GOLDEN_SHA256
WORKER_IMAGE_DIGEST = t034.WORKER_IMAGE_DIGEST
COMPILER_VERSION = t034.COMPILER_VERSION

golden_owner = t034.golden_owner
minimal_owner = t034.minimal_owner
queued_run = t034.queued_run
claim_lease = t034.claim_lease
exit_identity = t034.exit_identity
exit_evidence = t034.exit_evidence
attempt_row = t034.attempt_row
attempts_for_run = t034.attempts_for_run
attempt_count = t029.attempt_count
quota_counter = t034.quota_counter
quota_allocations = t034.quota_allocations
allocation_for_job = t034.allocation_for_job
set_quota_capacity = t031.set_quota_capacity
run_state_row = t034.run_state_row
job_row = t034.job_row
jobs_for_run = t034.jobs_for_run
artifact_row = t031.artifact_row
policy_revision = t034.policy_revision
force_lease_expiry = t034.force_lease_expiry
revoke_attempt = t034.revoke_attempt
wait_until = t034.wait_until
run_path = t034.run_path
get_run = t034.get_run
assert_run_shape = t034.assert_run_shape
seed_output_artifact = t034.seed_output_artifact
link_output = t034.link_output
unlink_output = t034.unlink_output
result_body = t034.result_body
reported_result = t034.reported_result
terminal_states = t034.terminal_states
allocations_for_holder = t034.allocations_for_holder
run_artifact_count = t034.run_artifact_count
claimed_with_output = t034.claimed_with_output
count = t034.count
seed_child_run = t034.seed_child_run
run_events = t034.run_events
run_event_count = t034.run_event_count
payload_of = t034.payload_of
latest_run_status = t034.latest_run_status
run_active_row = t034.run_active_row
attempt_active_row = t034.attempt_active_row
shift_attempt_started_at_seconds = t034.shift_attempt_started_at_seconds
set_run_max_active_ms = t034.set_run_max_active_ms
set_run_active_elapsed_ms = t034.set_run_active_elapsed_ms
ACTIVE_ELAPSED_HEADER = t034.ACTIVE_ELAPSED_HEADER
MAX_ACTIVE_HEADER = t034.MAX_ACTIVE_HEADER
DEADLINE_HEADER = t034.DEADLINE_HEADER
active_headers = t034.active_headers
assert_active_ms = t034.assert_active_ms
set_run_phase = t034.set_run_phase
GOLDEN_MAX_ACTIVE_MS = t034.GOLDEN_MAX_ACTIVE_MS
DEADLINE_CODE = t034.DEADLINE_CODE

# Waiting/resume vocabulary (mirrors platform.run_waitings.kind and the bridge).
KIND_APPROVAL = "approval"
KIND_CHILDREN = "children"
STATE_APPROVAL = "waiting_approval"
STATE_CHILDREN = "waiting_children"
DECISION_CONFLICT = "decision_conflict"
RUN_STATE_CONFLICT = "run_state_conflict"
RUN_TERMINAL = "run_terminal"
RESUME_PURPOSE = "resume_input"

# The non-contract Run-detail headers T035 adds.
WAITING_KIND_HEADER = "X-Run-Waiting-Kind"
WAITING_OUTCOME_HEADER = "X-Run-Waiting-Outcome"
WAITING_DECIDED_HEADER = "X-Run-Waiting-Decided-At"
WAITING_EXIT_HEADER = "X-Run-Waiting-Exit-Confirmed-At"
RESUME_JOB_ID_HEADER = "X-Run-Resume-Job-ID"
RESUME_JOB_STATE_HEADER = "X-Run-Resume-Job-State"


# ---------------------------------------------------------------------------
# the synthetic bridge client (supervisor mTLS)
# ---------------------------------------------------------------------------
class WaitingInternalClient(t029.ExecutionInternalClient):
    """T035's internal client: the existing protocol plus waiting-resolution."""

    def resolve_waiting(
        self,
        run_id: str,
        *,
        kind: str = KIND_APPROVAL,
        outcome: str = "approved",
        reference: str | None = None,
        child_run_id: str | None = None,
        body: dict | None = None,
        **kwargs,
    ):
        if body is None:
            body = {"kind": kind, "outcome": outcome}
            if kind == KIND_APPROVAL:
                body["reference"] = reference if reference is not None else "opaque-decision-ref"
            else:
                body["child_run_id"] = child_run_id
        return self.post(f"/internal/v1/runs/{run_id}/waiting-resolution", body, **kwargs)


def execution_client(stack: T035Stack, role: str = "supervisor") -> WaitingInternalClient:
    return WaitingInternalClient(
        stack.certificates.internal_url,
        stack.certificates.ca.cert,
        stack.certificates.client(role),
    )


# ---------------------------------------------------------------------------
# driving the waiting hand-over
# ---------------------------------------------------------------------------
def submit_waiting(
    client: WaitingInternalClient,
    lease: dict,
    *,
    kind: str = KIND_APPROVAL,
    checkpoint_id: str = "ckpt-t035",
    event_id: str | None = None,
):
    """Submit the real contract waiting_* result proposal for one lease."""
    state = STATE_APPROVAL if kind == KIND_APPROVAL else STATE_CHILDREN
    return client.submit_result(
        lease["run_id"],
        body=result_body(
            lease,
            suggested_state=state,
            checkpoint_id=checkpoint_id,
            event_id=event_id,
        ),
    )


def waiting_run_with_lease(
    stack: T035Stack,
    *,
    kind: str = KIND_APPROVAL,
    execution_unit_id: str = "unit-waiting",
    checkpoint_id: str = "ckpt-t035",
    shift_seconds: int | None = None,
) -> tuple[t031.Owner, dict, object]:
    """One real run claimed and stopped at a waiting proposal."""
    owner = golden_owner(stack)
    lease = claim_lease(stack, owner, execution_unit_id=execution_unit_id)
    if shift_seconds:
        shift_attempt_started_at_seconds(stack.dbname, lease["attempt_token"], shift_seconds)
    worker = execution_client(stack, "worker")
    response = submit_waiting(
        worker, lease, kind=kind, checkpoint_id=checkpoint_id
    )
    assert response.status == 200, response.text
    return owner, lease, response


def confirm_waiting_exit(
    stack: T035Stack, lease: dict, *, execution_unit_id: str = "unit-waiting"
):
    return execution_client(stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id=execution_unit_id,
        evidence=exit_evidence(lease, started=True, exit_code=0),
    )


# ---------------------------------------------------------------------------
# database read models
# ---------------------------------------------------------------------------
def waiting_row(dbname: str, run_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT run_id::text AS run_id, kind, checkpoint_id, decision::text AS decision, "
        "decision_at::text AS decision_at, exit_confirmed_at::text AS exit_confirmed_at, "
        "resume_job_id::text AS resume_job_id, resume_artifact_id::text AS resume_artifact_id, "
        "created_at::text AS created_at, updated_at::text AS updated_at "
        "FROM platform.run_waitings "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid",
    )
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def waiting_decision(dbname: str, run_id: str) -> dict | None:
    row = waiting_row(dbname, run_id)
    if row is None or row["decision"] is None:
        return None
    return json.loads(row["decision"])


def waiting_count(dbname: str, where: str = "TRUE") -> int:
    return int(st.sql(dbname, f"SELECT count(*) FROM platform.run_waitings WHERE {where}"))


def jobs_of_kind(dbname: str, run_id: str, kind: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, run_id::text AS run_id, kind, state, "
        "current_attempt_token::text AS current_attempt_token, "
        "resume_artifact_id::text AS resume_artifact_id, "
        "created_at::text AS created_at "
        "FROM platform.jobs "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid "
        f"AND kind = {st.quote_literal(kind)} ORDER BY created_at, id",
    )


def resume_jobs(dbname: str, run_id: str) -> list[dict]:
    return jobs_of_kind(dbname, run_id, "resume")


def resume_job(dbname: str, run_id: str) -> dict:
    jobs = resume_jobs(dbname, run_id)
    assert len(jobs) == 1, jobs
    return jobs[0]


def allocation_count(dbname: str, *, released: bool | None = None) -> int:
    predicate = "TRUE"
    if released is True:
        predicate = "released_at IS NOT NULL"
    elif released is False:
        predicate = "released_at IS NULL"
    return int(st.sql(dbname, f"SELECT count(*) FROM platform.quota_allocations WHERE {predicate}"))


# ---------------------------------------------------------------------------
# control facts (SQL fixtures; no secret and no business transition)
# ---------------------------------------------------------------------------
def set_run_budget(dbname: str, run_id: str, budget: str) -> None:
    st.sql(
        dbname,
        f"UPDATE platform.runs SET budget_limit = {st.quote_literal(budget)}::numeric "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )


def set_cancel_requested(dbname: str, run_id: str) -> None:
    """Record the T035 cancel intent directly; T036 owns the public endpoint."""
    st.sql(
        dbname,
        "UPDATE platform.runs SET cancel_requested_at = now() "
        f"WHERE id = {st.quote_literal(run_id)}::uuid AND cancel_requested_at IS NULL",
    )


def age_waiting(dbname: str, run_id: str, seconds: int) -> None:
    """Move the waiting timestamps into the past (waiting must accrue no time)."""
    assert seconds > 0
    st.sql(
        dbname,
        "UPDATE platform.run_waitings SET "
        f"created_at = created_at - make_interval(secs => {int(seconds)}), "
        f"updated_at = updated_at - make_interval(secs => {int(seconds)}) "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid",
    )


def disable_owner(owner: t031.Owner, *, reason: str = "t035 permission revoked"):
    """Disable the Run owner through the real T021 admin endpoint."""
    response = owner.admin.client.request(
        "PATCH",
        f"/api/v1/admin/principals/{owner.creator.principal_id}",
        body={"disabled": True, "reason": reason},
        csrf=owner.admin.csrf,
    )
    assert response.status == 200, response.text
    return response


def seed_terminal_child(
    stack: T035Stack,
    *,
    parent_run_id: str,
    owner_id: str,
    state: str = "succeeded",
) -> str:
    return seed_child_run(stack, parent_run_id=parent_run_id, owner_id=owner_id, state=state)


def set_run_state(dbname: str, run_id: str, state: str) -> None:
    st.sql(
        dbname,
        "UPDATE platform.runs SET state = " + st.quote_literal(state) +
        f" WHERE id = {st.quote_literal(run_id)}::uuid",
    )


# ---------------------------------------------------------------------------
# the canonical sealed resume payload
# ---------------------------------------------------------------------------
def expected_resume_payload(
    run_id: str, kind: str, checkpoint_id: str, decision: dict
) -> bytes:
    """The exact RFC 8785 JCS bytes the platform must seal.

    Every value is a string, so JCS equals ``json.dumps(sort_keys=True,
    separators=(",", ":"))`` for this document; this is the independent expected
    value the test compares the real sealed object against.
    """
    document = {
        "run_id": run_id,
        "kind": kind,
        "checkpoint_id": checkpoint_id,
        "decision": decision,
    }
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def resume_payload_object(stack: T035Stack, artifact_id: str) -> tuple[bytes, str]:
    """Read the real sealed resume bytes from the object store plus their key."""
    row = artifact_row(stack.dbname, artifact_id)
    payload = (stack.object_dir / row["object_key"]).read_bytes()
    return payload, row["object_key"]


# ---------------------------------------------------------------------------
# owner-facing detail headers
# ---------------------------------------------------------------------------
def waiting_headers(response) -> dict:
    def value(name):
        raw = response.header(name)
        return None if raw in (None, "") else raw

    return {
        "kind": value(WAITING_KIND_HEADER),
        "outcome": value(WAITING_OUTCOME_HEADER),
        "decided_at": value(WAITING_DECIDED_HEADER),
        "exit_confirmed_at": value(WAITING_EXIT_HEADER),
        "resume_job_id": value(RESUME_JOB_ID_HEADER),
        "resume_job_state": value(RESUME_JOB_STATE_HEADER),
    }


def resume_artifact_count(dbname: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.artifacts "
            f"WHERE purpose = {st.quote_literal(RESUME_PURPOSE)}",
        )
    )


def assert_no_resume(dbname: str, run_id: str) -> None:
    assert resume_jobs(dbname, run_id) == [], resume_jobs(dbname, run_id)
    row = waiting_row(dbname, run_id)
    assert row is not None and row["resume_job_id"] is None, row
