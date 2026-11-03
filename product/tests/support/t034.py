"""F03/T034 real-stack helpers: cross-attempt active time and the deadline.

T034 makes active execution time a cross-attempt quantity: every attempt settles
its interval exactly once into ``runs.active_elapsed_ms``, and a new attempt
continues from that accumulated total instead of resetting it. ``runs.max_active_ms``
is the frozen limit (NULL = unknown/no deadline). At/over the limit the platform
records the pre-cancel intent and the one T031 finalizer commits
``failed(deadline_exceeded)`` once the execution unit exits.

Everything here is real: the T031 stack (PostgreSQL + compiled API + real mTLS +
real object bytes), real HTTP/mTLS calls, and deterministic time manipulation by
moving ``run_attempts.started_at`` into the past with SQL -- never by sleeping.
The only non-contract surface is the three active-time headers on
``GET /api/v1/runs/{id}`` (the contract Run schema has no such fields).
"""

from __future__ import annotations

import json

from support import stack as st
from support import t031, t032

# Re-exports so a T034 test imports one module.
T034Stack = t031.T031Stack
Session = t031.Session
admin_session = t031.admin_session
session_for = t031.session_for
invite_and_activate = t031.invite_and_activate
create_application_or_fail = t031.create_application_or_fail
new_uuid = t031.new_uuid
sql_utf8 = t031.sql_utf8
submit_debug = t031.submit_debug
seed_golden_draft = t031.seed_golden_draft
GOLDEN_APPLICATION_ID = t031.GOLDEN_APPLICATION_ID
GOLDEN_SHA256 = t031.GOLDEN_SHA256
WORKER_IMAGE_DIGEST = t031.WORKER_IMAGE_DIGEST
COMPILER_VERSION = t031.COMPILER_VERSION

golden_owner = t031.golden_owner
minimal_owner = t031.minimal_owner
queued_run = t031.queued_run
claim_lease = t031.claim_lease
execution_client = t031.execution_client
exit_identity = t031.exit_identity
exit_evidence = t031.exit_evidence
attempt_row = t031.attempt_row
attempts_for_run = t031.attempts_for_run
quota_counter = t031.quota_counter
quota_allocations = t031.quota_allocations
allocation_for_job = t031.allocation_for_job
run_state_row = t031.run_state_row
job_row = t031.job_row
jobs_for_run = t031.jobs_for_run
policy_revision = t031.policy_revision
force_lease_expiry = t031.force_lease_expiry
revoke_attempt = t031.revoke_attempt
wait_until = t031.wait_until
run_path = t031.run_path
get_run = t031.get_run
assert_run_shape = t031.assert_run_shape
seed_output_artifact = t031.seed_output_artifact
link_output = t031.link_output
unlink_output = t031.unlink_output
result_body = t031.result_body
reported_result = t031.reported_result
terminal_states = t031.terminal_states
allocations_for_holder = t031.allocations_for_holder
run_artifact_count = t031.run_artifact_count
claimed_with_output = t031.claimed_with_output
count = t031.count
seed_child_run = t031.seed_child_run
run_events = t032.run_events
run_event_count = t032.run_event_count
payload_of = t032.payload_of

# The non-contract Run-detail headers T034 adds.
ACTIVE_ELAPSED_HEADER = "X-Run-Active-Elapsed-Ms"
MAX_ACTIVE_HEADER = "X-Run-Max-Active-Ms"
DEADLINE_HEADER = "X-Run-Deadline-Exceeded"

# The golden draft's policy.max_active_seconds (900s, workflow.schema.json bound).
GOLDEN_MAX_ACTIVE_MS = 900_000
DEADLINE_CODE = "deadline_exceeded"


# ---------------------------------------------------------------------------
# database read models
# ---------------------------------------------------------------------------
def run_active_row(dbname: str, run_id: str) -> dict:
    """The Run's accounting/state facts, including the T034 columns."""
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, state, phase, revision, active_elapsed_ms, "
        "max_active_ms, cancel_requested_at::text AS cancel_requested_at, "
        "error_code, finished_at::text AS finished_at, "
        "output_artifact_id::text AS output_artifact_id, started_at::text AS started_at "
        "FROM platform.runs "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def attempt_active_row(dbname: str, token: str) -> dict:
    """The attempt's settlement facts (started/settled/active ms)."""
    rows = st.sql_rows(
        dbname,
        "SELECT token::text AS token, run_id::text AS run_id, "
        "started_at::text AS started_at, exited_at::text AS exited_at, "
        "revoked_at::text AS revoked_at, active_ms, "
        "active_settled_at::text AS active_settled_at, "
        "reported_result::text AS reported_result "
        "FROM platform.run_attempts "
        f"WHERE token = {st.quote_literal(token)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


# ---------------------------------------------------------------------------
# deterministic time manipulation (no sleeps)
# ---------------------------------------------------------------------------
def shift_attempt_started_at_seconds(dbname: str, token: str, seconds: int) -> None:
    """Move the claim time of one attempt into the past by ``seconds``.

    Settlement computes ``now() - started_at`` in PostgreSQL, so this makes the
    accumulated active interval deterministic without sleeping. It changes only
    the synthetic elapsed part; the lease/heartbeat timestamps stay real.
    """
    assert seconds > 0
    st.sql(
        dbname,
        "UPDATE platform.run_attempts "
        f"SET started_at = started_at - make_interval(secs => {int(seconds)}) "
        f"WHERE token = {st.quote_literal(token)}::uuid",
    )


def set_run_max_active_ms(dbname: str, run_id: str, milliseconds: int | None) -> None:
    """Set/clear the frozen limit directly (tests that need a short deadline).

    New debug runs get the value from the frozen policy at creation; this helper
    exists to shrink the limit for a deterministic deadline without editing a
    published workflow.
    """
    if milliseconds is not None:
        assert milliseconds > 0
    value = "NULL" if milliseconds is None else str(int(milliseconds))
    st.sql(
        dbname,
        f"UPDATE platform.runs SET max_active_ms = {value} "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )


def set_run_active_elapsed_ms(dbname: str, run_id: str, milliseconds: int) -> None:
    assert milliseconds >= 0
    st.sql(
        dbname,
        f"UPDATE platform.runs SET active_elapsed_ms = {int(milliseconds)} "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )


def set_run_phase(dbname: str, run_id: str, phase: str) -> None:
    """Move a run's phase without a state change (T035's waiting phase shape).

    Waiting/resume is T035's transition; T034 only needs the phase the later
    ticket will write to prove waiting time does not accrue.
    """
    assert phase in {"idle", "executing", "waiting", "finalizing"}
    st.sql(
        dbname,
        f"UPDATE platform.runs SET phase = {st.quote_literal(phase)} "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )


# ---------------------------------------------------------------------------
# response headers + assertions
# ---------------------------------------------------------------------------
def active_headers(response) -> dict:
    """The three non-contract active-time headers, typed (None = absent)."""
    elapsed = response.header(ACTIVE_ELAPSED_HEADER)
    maximum = response.header(MAX_ACTIVE_HEADER)
    return {
        "active_elapsed_ms": None if elapsed in (None, "") else int(elapsed),
        "max_active_ms": None if maximum in (None, "") else int(maximum),
        "deadline_exceeded": response.header(DEADLINE_HEADER) == "true",
    }


def assert_active_ms(value: int, *, at_least: int, below: int, label: str = "active ms") -> None:
    """A real elapsed interval: deterministic lower bound, generous upper bound."""
    assert at_least <= value < below, f"{label} = {value}, want [{at_least}, {below})"


def latest_run_status(dbname: str, run_id: str) -> dict | None:
    """The last committed run.status event payload (state + optional error_code)."""
    rows = [row for row in run_events(dbname, run_id) if row["type"] == "run.status"]
    if not rows:
        return None
    return json.loads(rows[-1]["payload"])
