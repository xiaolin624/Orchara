"""RV1: effect_count reads confirmed effects from the real operation ledger.

Finding 2 of the eval-integrity review: ``platform.operations`` rows in
``prepared``/``awaiting_approval``/``authorized``/``failed`` states are not
effects, and an ``unknown``/``dispatching`` row could mean an effect happened
without a confirmed outcome. The collector must count only
``state='succeeded'``; if any row is unconfirmed, the run's effect evidence is
*not collected* (no number at all, a recorded reason) so ``expected=0`` can
neither fail spuriously on an attempted operation nor pass on an unconfirmed one.

The ledger rows are written into the real ``platform.operations`` table with the
same shape the F06 capability writer uses (CHECK-constrained states, a real
params artifact from the run's own sealed input). The subject under test is the
collector's SQL, reached through the real internal facts bridge, the real
maintenance pools and the real ``cmd/supervisor exec`` executor; no platform
behaviour is mocked. The rows exist as a fixture because this build has no
tool-bearing evaluation snapshot that could drive a real capability call inside
a case run (the application evaluation slice executes preset tool nodes directly,
not through the capability proxy).

The assertion key is the exact ``capability_version`` the platform records (a
lowercase UUID). A case names that version id; the checker normalises both
sides, and the count is per capability version, not the run's total.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from support import t052
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)

# The capability version a real capability request would name. Stored lowercased
# by the platform writer; the case's `capability` member names the same value.
CAPABILITY = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
OTHER_CAPABILITY = "0f9e8d7c-6b5a-4498-8736-5f4e3d2c1b0a"
PARAMS_SHA256 = "ab" * 32

# case id, ledger states recorded for the case's run, capability version stored
# on those rows, capability the case asserts, expected count
SCENARIOS: list[tuple[str, list[str], str, str, int]] = [
    ("confirmed-succeeded-counts-one", ["succeeded"], CAPABILITY, CAPABILITY, 1),
    (
        "non-dispatch-states-count-zero",
        ["failed", "prepared", "authorized", "awaiting_approval"],
        CAPABILITY,
        CAPABILITY,
        0,
    ),
    ("unknown-is-uncollected", ["unknown"], CAPABILITY, CAPABILITY, 0),
    ("dispatching-is-uncollected", ["dispatching"], CAPABILITY, CAPABILITY, 0),
    ("empty-ledger-is-zero", [], CAPABILITY, CAPABILITY, 0),
    # A confirmed effect under another capability version must not satisfy this
    # case: the count is keyed by capability_version, not the run's total.
    ("per-capability-not-run-total", ["succeeded"], CAPABILITY, OTHER_CAPABILITY, 0),
    # A confirmed row does not rescue an unconfirmed one: the whole evidence is
    # uncollected, so expected=1 must fail rather than report the partial 1.
    ("mixed-confirmed-and-unknown", ["succeeded", "unknown"], CAPABILITY, CAPABILITY, 1),
]
UNCOLLECTED = {
    "unknown-is-uncollected": "unconfirmed_unknown",
    "dispatching-is-uncollected": "unconfirmed_dispatching",
    "mixed-confirmed-and-unknown": "unconfirmed_unknown",
}


def _dataset_cases() -> list[dict]:
    return [
        t052.evaluation_case(
            case_id=case_id,
            assertions=[
                t052.assertion_run_state("failed"),
                t052.assertion_effect_count(asserted_capability, expected),
            ],
        )
        for case_id, _states, _stored, asserted_capability, expected in SCENARIOS
    ]


def _case_run_row(dbname: str, evaluation_id: str, case_id: str, *, timeout: float = 120.0) -> dict:
    """Wait for the handler to create the case's real run (still queued)."""
    deadline = time.time() + timeout
    last: list[dict] = []
    while time.time() < deadline:
        last = t052.case_run_rows(dbname, evaluation_id)
        for row in last:
            if row["case_id"] == case_id and row["run_id"]:
                return row
        time.sleep(0.05)
    raise AssertionError(f"case {case_id} never got a run; case rows={last}")


def _record_operation_rows(dbname: str, run_id: str, states: list[str], capability: str) -> None:
    """Insert the real ledger rows the F06 writer would have written."""
    if not states:
        return
    params_artifact_id = t052.run_row(dbname, run_id)["input_artifact_id"]
    assert params_artifact_id, "the run must have a sealed input artifact"
    values = []
    for index, state in enumerate(states):
        values.append(
            "("
            + ", ".join(
                [
                    f"{st.quote_literal(str(uuid.uuid4()))}::uuid",
                    f"{st.quote_literal(run_id)}::uuid",
                    st.quote_literal(f"rv1-effect-{index}-{state}"),
                    st.quote_literal(capability),
                    "'tool'",
                    "'write'",
                    st.quote_literal(PARAMS_SHA256),
                    f"{st.quote_literal(params_artifact_id)}::uuid",
                    st.quote_literal(state),
                ]
            )
            + ")"
        )
    st.sql(
        dbname,
        "INSERT INTO platform.operations "
        "(id, run_id, logical_key, capability_version, capability_kind, risk_class, "
        " params_sha256, params_artifact_id, state) VALUES " + ", ".join(values),
    )


def _execute_run_once(stack, env: dict[str, str], run_id: str, *, timeout: float = 120.0) -> str:
    """Drive the real executor until this run is terminal (one attempt at a time)."""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = t052.run_row(stack.dbname, run_id)["state"]
        if last in {"succeeded", "failed", "cancelled"}:
            return last
        report = t052.exec_report(t052.run_supervisor_exec(stack, env, timeout=180))
        if report.get("status") == "idle":
            time.sleep(0.05)
    raise AssertionError(f"run {run_id} never became terminal; last state={last}")


def test_effect_count_reads_confirmed_effects_from_the_real_ledger(
    t052_stack, tmp_path: Path
) -> None:
    owner = t052.minimal_owner(t052_stack)
    artifact_id = t052.upload_dataset(
        t052_stack, owner.creator, t052.dataset_document(_dataset_cases())
    )
    submitted = t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=t052.draft_revision(owner.creator, owner.application_id),
        dataset_artifact_id=artifact_id,
    )
    assert submitted.status == 202, submitted.text
    evaluation_id = submitted.json["id"]

    log_path = tmp_path / "pools.log"
    process, handle = t052.start_pools(t052_stack, log_path=log_path, case_timeout="60s")
    try:
        env = t052.supervisor_exec_env(t052_stack)
        for case_id, states, stored_capability, _asserted, _expected in SCENARIOS:
            row = _case_run_row(t052_stack.dbname, evaluation_id, case_id)
            # The handler creates the run before it waits for a rest state; the
            # ledger rows are recorded before the executor can finish it, so the
            # collector's final facts read always sees them.
            assert t052.run_row(t052_stack.dbname, row["run_id"])["state"] == "queued", row
            _record_operation_rows(t052_stack.dbname, row["run_id"], states, stored_capability)
            assert _execute_run_once(t052_stack, env, row["run_id"]) == "failed"
        evaluation = t052.wait_for_evaluation(
            t052_stack.dbname, evaluation_id, {"succeeded", "failed"}, timeout=180
        )
    finally:
        t052.stop_process(process, handle, log_path)

    assert evaluation["state"] == "succeeded", evaluation
    assert evaluation["report_id"] is not None, evaluation
    report = t052.report_row(t052_stack.dbname, evaluation["report_id"])
    sealed = t052.report_document(t052_stack, report["result_artifact_id"])
    assert report["status"] == "failed", (report, sealed)
    cases = {case["id"]: case for case in sealed["cases"]}
    assert set(cases) == {case_id for case_id, *_ in SCENARIOS}, sorted(cases)

    for case_id, _states, _stored, asserted_capability, expected in SCENARIOS:
        case = cases[case_id]
        facts = case["facts"]
        outcomes = {outcome["kind"]: outcome for outcome in case["assertions"]}
        assert outcomes["run_state"]["actual"] == "failed", outcomes["run_state"]
        assert outcomes["run_state"]["passed"] is True, outcomes["run_state"]
        effect = outcomes["effect_count"]
        assert effect["expected"] == str(expected), effect
        assert effect["capability"] == asserted_capability, effect

        if case_id in UNCOLLECTED:
            # Unconfirmed evidence: no number, a recorded reason, and the
            # assertion can never pass even for expected=0.
            assert case["verdict"] == "failed" and case["status"] == "failed", case
            assert effect["passed"] is False, effect
            assert effect["actual"] == "not_collected", effect
            assert "effect_evidence=" + UNCOLLECTED[case_id] in effect["detail"], effect
            assert facts.get("effect_counts") is None, facts
            assert facts.get("effect_evidence") == UNCOLLECTED[case_id], facts
            continue

        assert case["verdict"] == "passed", case
        assert effect["passed"] is True, effect
        assert effect["actual"] == str(expected), effect
        assert "effect_evidence" not in facts, facts
        if case_id == "confirmed-succeeded-counts-one":
            assert facts["effect_counts"] == {CAPABILITY: 1}, facts
        elif case_id == "per-capability-not-run-total":
            # The real ledger has one confirmed effect, but under a different
            # capability version; the asserted version is honestly zero.
            assert facts["effect_counts"] == {CAPABILITY: 1}, facts
        else:
            assert facts["effect_counts"] == {}, facts

    # The fixture really is the ledger the report was decided from: re-read the
    # states from PostgreSQL and compare with what each case recorded.
    for case_id, states, _stored, _asserted, _expected in SCENARIOS:
        run_id = next(
            row["run_id"]
            for row in t052.case_run_rows(t052_stack.dbname, evaluation_id)
            if row["case_id"] == case_id
        )
        rows = st.sql_rows(
            t052_stack.dbname,
            "SELECT state, count(*) AS n FROM platform.operations "
            f"WHERE run_id = {st.quote_literal(run_id)}::uuid GROUP BY state ORDER BY state",
        )
        recorded = {row["state"]: int(row["n"]) for row in rows}
        expected_states: dict[str, int] = {}
        for state in states:
            expected_states[state] = expected_states.get(state, 0) + 1
        assert recorded == expected_states, (case_id, recorded, expected_states)
