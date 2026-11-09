"""C4.03/T154 acceptance: scheduler regressions after the fake linear view removal.

This module adds the two real-stack facts the C4.03 scheduler matrix cannot take
from the Go seam tests alone:

* a graph cancellation decision really drains the started sibling branch, then
  certifies exactly one attempt exit and proposes no result;
* a resumed graph attempt publishes the unselected branch's ``skipped`` fact
  exactly once (the waiting attempt published none), runs the activated join
  once, and commits one succeeded proposal after the resumed attempt.

Everything runs through the product's real path: a real draft published by the Go
workflow compiler, the real debug submission, the compiled ``cmd/supervisor
exec --once`` claiming the attempt and running the python branches in WSL2/runsc,
the real node-approval route and decision API, the real mTLS artifact routes, the
F04B recovery seam and the PostgreSQL run facts. No product fault endpoint is
added.

The cancellation case has two shapes:

* the started-drain test starts the real supervisor as a process, waits (bounded
  polling) until both branches' runsc containers are really *running* with a
  positive PID, and only then writes the plain T036 cancel intent. The
  supervisor's next heartbeat observes it while the units run, so a *started*
  sibling drain is exercised deterministically instead of racing the cold-start
  sandbox launch;
* the first-heartbeat test keeps the old test-owned SQL trigger (T033/T042
  precedent): the intent is written on the attempt's first heartbeat, which is
  before that heartbeat's supervisor starts any sandbox. Whether a branch had
  already begun launching is environment timing, so this test asserts the honest
  contract common to both outcomes and does not require ``started=true``.

Both shapes assert one explicit drain contract through the shared helper: both
branches started; every branch is either terminal (``failed``/``succeeded``) or
non-terminal with its runsc container/workspace really gone (per-branch sandbox id
absent from ``runsc list`` and the sandbox state root); at least one ``failed``;
join/end each exactly one ``skipped``; no join result; one attempt/one exit;
``reported_result`` NULL. The deterministic terminal decision is the Go seam test
``TestGraphRunCancellationDrainsStartedSiblings``; a drained sibling's terminal
``node.status`` projection may be lost because the supervisor logs
``node.status ... was not recorded: ... context canceled``, so the page/event
projection is not used as the authority for that branch.

The started-drain shape deliberately does not assert the exit evidence's
``started``/``sandbox_started`` flags: they are supervisor certification
timestamps, and a cancel heartbeat observed before ``sandbox.Start`` returns can
record ``started=false`` for units whose runsc containers were observed running.
The real started evidence is the pre-cancel running-container snapshot plus the
post-drain empty runsc/state-root check; the Go seam covers the deterministic
terminal decision.

The rest of the C4.03 matrix is covered by the existing real-stack acceptance
modules reported with this ticket (T083 condition/join, T085 optional/fail-fast,
T087 join-once resume, T089 call_app logical keys, T091 ten-node recovery); the
Python LangGraph proof is reported separately as ADR 0034's independent evidence.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time

import pytest

from support import execution_support as execution
from support import run_observations as observations
from support import t025, t027, t029, t033, t035, t042, t049, t083, t085, t087
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)


@pytest.fixture(scope="module")
def sandbox() -> None:
    if not t033.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: `wsl -d Ubuntu-24.04 -u root -e bash -lc "
            "'runsc --version'` failed; build the rootfs with "
            "deploy/sandbox/build_python_rootfs.sh"
        )


def _approval_then_condition_definition() -> dict:
    """start -> approve1 -> choose --yes--> left / --no--> right -> join -> end.

    The approval parks the first attempt before the condition is activated, so the
    unselected branch can only be published by the resumed attempt.
    """
    end = t027.end_node()
    end["inputs"] = {
        "approval_id": {"ref": "node:approve1#/approval_id"},
        "results": {"ref": "node:join#/results"},
    }
    end["output_schema"] = {
        "type": "object",
        "properties": {
            "approval_id": {"type": "string"},
            "results": {"type": "object"},
        },
        "required": ["approval_id", "results"],
        "additionalProperties": False,
    }
    definition = t027.definition(
        [
            t027.start_node(),
            t087.approval_node("approve1", title="Approve the branch"),
            t083.condition_node(
                "choose",
                join_id="join",
                input_name="requires_approval",
                cases=[{"input": "requires_approval", "op": "eq", "value": True, "port": "yes"}],
                default_port="no",
            ),
            t083.python_node("left"),
            t083.python_node("right"),
            t083.join_node("join", fork_id="choose", required=("left", "right")),
            end,
        ],
        [
            {"source": "start", "target": "approve1"},
            {"source": "approve1", "target": "choose"},
            {"source": "choose", "target": "left", "port": "yes"},
            {"source": "choose", "target": "right", "port": "no"},
            {"source": "left", "target": "join"},
            {"source": "right", "target": "join"},
            {"source": "join", "target": "end"},
        ],
    )
    definition["input_schema"] = t083.FLAG_INPUT_SCHEMA
    definition["output_schema"] = end["output_schema"]
    return definition


def _reported_states(stack, run_id: str) -> list[str]:
    attempts = t029.attempts_for_run(stack.dbname, run_id)
    states = []
    for attempt in attempts:
        row = t029.attempt_row(stack.dbname, attempt["token"])
        assert row["exited_at"] is not None, row
        if row["reported_result"] is None:
            states.append("")
            continue
        states.append(json.loads(row["reported_result"])["suggested_state"])
    return states


# ---------------------------------------------------------------------------
# started-drain cancellation: a Popen supervisor, a bounded sandbox poll, then
# the plain T036 cancel intent once a runsc unit really exists.
# ---------------------------------------------------------------------------
_SANDBOX_POLL_TIMEOUT = 90.0
_SANDBOX_POLL_INTERVAL = 0.05


def _drain_input_xlsx(rows: int = 9999) -> bytes:
    """A valid preset-tool workbook big enough to keep both branch units alive.

    The tool's cap is 10_000 data rows; 9_999 unique rows stay inside it. The
    workbook parser is deliberately the slower input path (native parse alone is
    ~0.6s), so a host-side ``runsc list`` poll returns while the units still run.
    The three-row public sample can finish before the poll's WSL round-trip.
    """
    table = [["order_id", "date", "region", "amount"]]
    for index in range(rows):
        table.append([f"o-{index}", "2026-01-02", "east", f"{index % 100}.50"])
    return t049.xlsx_bytes(table)


def _queue_drain_run(stack):
    """Queue one graph run whose two branches run real, long-enough sandboxes."""
    owner, application_id = t083.application_for(
        stack, t085.parallel_definition(), name="c4.03 cancel drain"
    )
    artifact_id = t033.upload_artifact(
        stack,
        owner.creator,
        _drain_input_xlsx(),
        media_type=t049.XLSX_MEDIA_TYPE,
    )
    run = t042.submit_linear_run(
        owner, application_id, artifact_id=artifact_id, input_value={}
    )
    return owner, application_id, run


def _sandbox_id_for_execution_unit(execution_unit_id: str) -> str:
    """The runsc-safe id ``internal/sandbox.NewSandboxID`` derives.

    The sandbox id is the runsc container id, so the test can attribute a listed
    container to this attempt's execution unit without reading product internals
    at run time. Keep this mapping byte-for-byte identical to NewSandboxID:
    lowercase, ``-_:./`` become ``-``, other foreign runes are dropped, leading
    and trailing ``-`` are trimmed, then truncate to 48 characters.
    """
    cleaned = execution_unit_id.strip().lower()
    characters = []
    for character in cleaned:
        if "a" <= character <= "z" or "0" <= character <= "9":
            characters.append(character)
        elif character in "-_:./":
            characters.append("-")
    return "".join(characters).strip("-")[:48]


def _graph_sandbox_unit(execution_unit_id: str, node_id: str) -> str:
    """The per-node runsc unit id ``graphSandboxUnit`` derives (agent.go)."""
    digest = hashlib.sha1(("graph:" + node_id).encode("utf-8")).hexdigest()[:8]
    suffix = "-a" + digest
    base = execution_unit_id
    if len(base) + len(suffix) > 48:
        base = base[: 48 - len(suffix)]
    return base + suffix


def _graph_sandbox_ids(execution_unit_id: str) -> dict[str, str]:
    return {
        node_id: _sandbox_id_for_execution_unit(_graph_sandbox_unit(execution_unit_id, node_id))
        for node_id in ("left", "right")
    }


def _start_supervisor_exec(stack, env: dict[str, str]) -> subprocess.Popen[str]:
    """The same binary/env as ``execution.run_supervisor_exec``, but as a process."""
    return subprocess.Popen(
        [str(stack.binaries["supervisor"]), "exec", "--once"],
        cwd=str(execution.PRODUCT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
    )


def _finish_supervisor_exec(
    process: subprocess.Popen[str], *, timeout: int = 300
) -> subprocess.CompletedProcess[str]:
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=30)
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def _running_runsc_ids(listed: list[str]) -> set[str]:
    """The sandbox ids that are really running, not only created.

    ``runsc list`` prints ``ID PID STATUS BUNDLE CREATED OWNER``. A container can
    be listed while it is still being created; writing the cancel intent at that
    instant may kill it before the driver has launched the process, which is the
    known ``started=false`` cold-start shape. Requiring a running status and a
    positive PID makes the poll observe two launched branch processes before the
    cancel lands, without teaching the test anything about product internals.
    """
    running: set[str] = set()
    for line in listed:
        fields = line.split()
        if len(fields) < 3:
            continue
        sandbox_id, pid, status = fields[0], fields[1], fields[2]
        if status == "running" and pid.isdigit() and int(pid) > 0:
            running.add(sandbox_id)
    return running


def _await_started_graph_sandbox(
    stack, run_id: str, *, timeout: float = _SANDBOX_POLL_TIMEOUT
) -> tuple[str, list[str], dict[str, str], list[str]]:
    """Poll until a *running* runsc container attributable to each branch is listed.

    Returns ``(execution_unit_id, observed_node_ids, expected_ids, runsc_list)``.
    The attempt must first be claimed (its ``execution_unit_id`` is set); after
    that, ``runsc list`` must show the sandbox ids of *both* graph branches with
    ``STATUS=running`` and a real PID, i.e. both branch sandbox processes have
    actually launched at the same time. That is the drain this test describes:
    neither branch is a still-created or still-staging sibling when the cancel
    lands. The poll is bounded and raises with the last observations, never a
    bare timeout.
    """
    deadline = time.monotonic() + timeout
    attempts: list[dict] = []
    execution_unit_id = ""
    expected: dict[str, str] = {}
    observed: list[str] = []
    listed: list[str] = []
    while time.monotonic() < deadline:
        attempts = t029.attempts_for_run(stack.dbname, run_id)
        execution_unit_id = next(
            (row["execution_unit_id"] or "" for row in attempts), ""
        )
        if execution_unit_id:
            expected = _graph_sandbox_ids(execution_unit_id)
            listed = t033.runsc_list()
            running_ids = _running_runsc_ids(listed)
            observed = [node for node, sandbox in expected.items() if sandbox in running_ids]
            if len(observed) == len(expected):
                return execution_unit_id, observed, expected, listed
        time.sleep(_SANDBOX_POLL_INTERVAL)
    raise AssertionError(
        f"the graph branches' runsc containers were not both running within {timeout:.1f}s: "
        f"attempts={attempts}, execution_unit_id={execution_unit_id!r}, "
        f"expected_sandbox_ids={expected}, observed={observed}, runsc_list={listed}"
    )


def _cancel_after_started_sandbox(
    stack, env: dict[str, str], run_id: str
) -> tuple[subprocess.CompletedProcess[str], dict]:
    """Run one supervisor --once; cancel once both branch units are really running.

    Returns ``(completed, launch)`` where ``launch`` is the independent runtime
    evidence captured by the poll: ``execution_unit_id``, both ``observed`` node
    ids with ``STATUS=running``/positive PID, the expected per-node sandbox ids,
    and the raw ``runsc list`` snapshot taken before the cancel intent was written.
    """
    process = _start_supervisor_exec(stack, env)
    try:
        execution_unit_id, observed, expected, listed = _await_started_graph_sandbox(
            stack, run_id
        )
        assert observed, (execution_unit_id, expected, listed)
        launch = {
            "execution_unit_id": execution_unit_id,
            "observed": observed,
            "expected_sandbox_ids": expected,
            "runsc_list": listed,
        }
        # Test-owned SQL intent, exactly the column T036 writes; no product
        # trigger exists. The running unit's heartbeat ticker observes it, so the
        # scheduler drains a started sibling instead of racing the launch.
        t029.set_cancel_requested(stack.dbname, run_id)
        completed = _finish_supervisor_exec(process)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=30)
    return completed, launch


def _assert_cancelled_run_and_attempt(stack, run, report: dict, completed) -> dict:
    """The honest cancellation drain contract shared by both cancel tests.

    The projection rule is deliberately not "every branch must have a terminal
    ``node.status``": when the scheduler's fail-fast cancel lands while a drained
    sibling's terminal submission is in flight, that POST can be aborted with
    "context canceled" (the supervisor logs it as ``node.status right=failed was
    not recorded: ... context canceled``), so the sibling may legitimately stay at
    ``running`` in the event projection. The deterministic terminal decision is
    covered by the Go seam test
    ``TestGraphRunCancellationDrainsStartedSiblings``.

    What this helper requires instead is the actual drain contract: both branches
    started; every branch is either terminal (``failed``/``succeeded``) or
    non-terminal *and* its runsc container/workspace is really gone (per-node
    sandbox id absent from both ``runsc list`` and the sandbox state root, plus a
    global empty ``runsc list``); at least one ``failed``; join/end published
    exactly one ``skipped`` each; no join result; one attempt/one exit;
    ``reported_result`` NULL. The started case's real "units ran" evidence is the
    caller's ``launch`` snapshot (both branch containers observed running with a
    positive PID) plus this helper's no-orphan checks, never the exit evidence's
    ``started`` flag.
    """
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "cancelled", (report, completed.stderr)
    assert report["error_code"] == "cancel_requested", report
    assert report.get("run_state") == "cancelled", report

    state = observations.wait_run_state(
        stack.dbname,
        run["id"],
        {"cancelled", "recovery_pending", "failed", "needs_reconciliation"},
        timeout=30.0,
    )
    assert state["state"] == "cancelled", state
    assert state["cancel_requested_at"] is not None, state
    assert state["error_code"] is None, state
    assert state["output_artifact_id"] is None, "a cancelled run must not publish a result"

    states = observations.node_states(stack.dbname, run["id"])
    for node_id in ("left", "right"):
        assert states[node_id][:2] == ["pending", "running"], (node_id, states)
    terminals = {node_id: states[node_id][-1] for node_id in ("left", "right")}
    assert any(state == "failed" for state in terminals.values()), states
    assert states["join"] == ["skipped"], states
    assert states["end"] == ["skipped"], states
    rows = observations.node_result_rows(stack.dbname, run["id"])
    assert [row for row in rows if row["node_id"] == "join"] == [], rows

    # Exactly one attempt, one exit: the drained sibling was not committed as a
    # second result. A cancellation proposes no result at all.
    attempts = t029.attempts_for_run(stack.dbname, run["id"])
    assert len(attempts) == 1, attempts
    attempt = t029.attempt_row(stack.dbname, attempts[0]["token"])
    assert attempt["exited_at"] is not None, attempt
    assert attempt["reported_result"] is None, attempt

    # The drain contract for a non-terminal projection: the branch's sandbox
    # container and workspace must really be gone, not merely "not shown".
    orphans = t033.no_orphan_evidence()
    listed_ids = {line.split()[0] for line in orphans["runsc_list"] if line.split()}
    expected_sandbox_ids = _graph_sandbox_ids(attempt["execution_unit_id"])
    for node_id in ("left", "right"):
        final = terminals[node_id]
        if final in {"failed", "succeeded"}:
            continue
        assert final == "running", (node_id, states)
        sandbox_id = expected_sandbox_ids[node_id]
        assert sandbox_id not in listed_ids, (node_id, sandbox_id, orphans)
        assert sandbox_id not in orphans["state_root"], (node_id, sandbox_id, orphans)
    assert orphans["runsc_list"] == [], "the cancelled sandbox left a runsc orphan"
    return json.loads(attempt["exit_evidence"])


def test_resumed_graph_attempt_publishes_the_skip_once_and_joins_once(
    t033_stack, sandbox
) -> None:
    owner, _application_id, run = t087.queue_approval_run(
        t033_stack,
        _approval_then_condition_definition(),
        input_value={"requires_approval": True},
        name="c4.03 per-attempt skip",
        csv=True,
    )

    # Attempt 1: the approval is pending, so the graph parks before choose. No
    # future node may be reported as skipped by a waiting attempt.
    completed, report = t087.exec_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "waiting_approval", report
    waiting = t087.wait_until(
        lambda: t035.waiting_row(t033_stack.dbname, run["id"]),
    )
    assert waiting["kind"] == t035.KIND_APPROVAL, waiting
    approval = t087.approval_for_node(t033_stack.dbname, run["id"], "approve1")
    assert approval is not None and approval["state"] == "pending", approval
    assert waiting["checkpoint_id"] == "approval:" + approval["id"], waiting
    first_events = observations.node_events(t033_stack.dbname, run["id"])
    assert [event for event in first_events if event["payload"]["node_id"] == "right"] == [], (
        first_events,
        "a waiting attempt must not publish the unselected branch's skip",
    )
    assert _reported_states(t033_stack, run["id"]) == ["waiting_approval"]
    assert len(t035.resume_jobs(t033_stack.dbname, run["id"])) == 0

    # The human decision creates exactly one resume.
    decided = t087.decide(
        owner.creator, approval["id"], params_sha256=approval["params_sha256"]
    )
    assert decided.status == 200 and decided.json["state"] == "approved", decided.text
    assert len(t035.resume_jobs(t033_stack.dbname, run["id"])) == 1

    # Attempt 2: choose selects "yes". The unselected branch is skipped exactly
    # once, in this attempt; the join and end run exactly once.
    completed, report = t087.exec_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "succeeded", report
    state = observations.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state

    right_events = [
        event
        for event in observations.node_events(t033_stack.dbname, run["id"])
        if event["payload"]["node_id"] == "right"
    ]
    assert [(event["payload"]["state"], event["payload"]["attempt_no"]) for event in right_events] == [
        ("skipped", 2)
    ], right_events
    states = observations.node_states(t033_stack.dbname, run["id"])
    for node_id in ("approve1", "choose", "left", "join", "end"):
        assert states[node_id][-1] == "succeeded", (node_id, states)
    assert states["join"].count("succeeded") == 1, states["join"]
    assert len(observations.node_data_rows(
        observations.node_result_items(owner.creator, run["id"], expected=6), "join"
    )) == 1, "the activated join must run exactly once"

    # One terminal proposal per attempt: the waiting hand-over and the succeeded
    # result. Nothing else proposed a run result.
    assert _reported_states(t033_stack, run["id"]) == ["waiting_approval", "succeeded"]
    assert observations.output_artifact_id(t033_stack.dbname, run["id"]) is not None
    final = observations.final_output_document(owner.creator, t033_stack.dbname, run["id"])
    assert final["approval_id"] == approval["id"], final
    assert final["results"]["left"]["status"] == "succeeded", final
    assert final["results"]["right"] == {"status": "skipped"}, final
    assert t033.no_orphan_evidence()["runsc_list"] == []


def test_graph_cancellation_drains_the_started_sibling_and_commits_one_exit(
    t033_stack, sandbox
) -> None:
    """A deterministic started-sibling drain, evidenced by the real runtime.

    The Popen supervisor is started first; a bounded ``runsc list`` poll requires
    both branch containers to be ``STATUS=running`` with a positive PID before the
    cancel intent is written. Those observed running units, their disappearance on
    cleanup, and the shared helper's no-orphan checks are the direct evidence that
    started sandboxes were drained.

    Deliberately **not** asserted here: ``exit_evidence["started"]`` and
    ``["sandbox_started"]``. That flag is a supervisor certification timestamp: if
    the cancel heartbeat is observed before the supervisor's ``sandbox.Start``
    call returns, the drained unit can be exited with ``started=false`` even
    though its runsc container/process was observed running. The flag is not proof
    that no unit ran, so it cannot serve as the started-drain assertion; the
    observed containers plus the empty runsc/state-root check are the direct
    evidence. The first-heartbeat test below keeps the honest
    ``started``/``sandbox_started`` contract for the pre-start shape.
    """
    owner, application_id, run = _queue_drain_run(t033_stack)
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )
    env = execution.supervisor_exec_env(
        t033_stack, heartbeat="50ms", timeout="60s", result_wait="30s"
    )
    with execution.FinalizeWorker(t033_stack):
        completed, launch = _cancel_after_started_sandbox(t033_stack, env, run["id"])
    report = execution.exec_report(completed)
    _assert_cancelled_run_and_attempt(t033_stack, run, report, completed)
    # The independent started evidence: both branch containers were running with a
    # positive PID in the pre-cancel snapshot, and the shared helper proves they
    # (and their workspaces) are gone now. This is what the old first-heartbeat
    # trigger could not guarantee on a cold boot.
    assert launch["observed"] == ["left", "right"], launch
    assert set(launch["expected_sandbox_ids"].values()) <= _running_runsc_ids(
        launch["runsc_list"]
    ), launch


def test_graph_cancellation_from_the_first_heartbeat_commits_one_honest_exit(
    t033_stack, sandbox
) -> None:
    """The pre-start arrival: the intent lands on the attempt's first heartbeat.

    That heartbeat happens before the supervisor starts this attempt's sandbox,
    but a branch may already be booting one on a warm WSL. Either arrival is
    honest: the attempt may certify ``started=false`` (nothing ran, so started is
    not required here) or ``started=true`` (a listed unit was drained). The old
    trigger scenario is kept; only the contract common to both outcomes is
    asserted. The deterministic *started* drain is the test above.
    """
    owner, application_id, run = t085.queue_graph_run(
        t033_stack,
        t085.parallel_definition(),
        input_value={},
        name="c4.03 cancel at first heartbeat",
    )
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )
    # Test-owned SQL (T033/T042 precedent): the cancel intent is written on the
    # attempt's first heartbeat, so the supervisor's next heartbeat observes it.
    t042.request_cancel_after_first_heartbeat(t033_stack.dbname)
    env = execution.supervisor_exec_env(
        t033_stack, heartbeat="50ms", timeout="60s", result_wait="30s"
    )
    with execution.FinalizeWorker(t033_stack):
        completed = execution.run_supervisor_exec(t033_stack, env)
    report = execution.exec_report(completed)
    evidence = _assert_cancelled_run_and_attempt(t033_stack, run, report, completed)
    # Do not require a started sandbox here. The honest invariant is only that a
    # started sandbox implies the attempt itself started (sandbox_started <= started).
    assert isinstance(evidence.get("started"), bool), evidence
    assert isinstance(evidence.get("sandbox_started"), bool), evidence
    assert not evidence["sandbox_started"] or evidence["started"], evidence
