"""F13B/T108 (GitHub #109): real delegation faults FAULT-07/08/13.

Every case drives the real F13B stack: a throwaway PostgreSQL 16.4 database
migrated by the compiled ``cmd/admin`` chain, the compiled ``cmd/api`` with the
real mTLS internal listener and object store, the real ``cmd/supervisor exec
--once`` OS process, the real ``cmd/maintenance artifact_finalize`` worker and
the real quota/waiting/run tables. No scheduler, no HTTP client and no database
row is mocked; child Runs are only ever created by the contract ``spawnChild``
operation.

* **FAULT-07** — one claimed parent attempt fires two **barrier-synchronised**
  ``spawnChild`` requests for the *same pinned child application version* with
  distinct logical keys. One child's slot is then held by a real claim while a
  real supervisor process executes the other, so two child slots are provably
  live at the same instant (the durable attempt rows are the proof). The held
  attempt is released through the platform's documented "never ran"
  ``started=false`` exit, requeued, and then really executed by the supervisor.
  The same parent is resumed, replays both invocations through the real graph
  scheduler and converges at one join. The evidence pins the two child Run ids,
  their distinct checkpoint threads/attempt evidence, the sealed per-call inputs,
  the two live child-slot allocations and the final join envelope (no crosstalk:
  left keeps ``alpha``, right keeps ``beta``).
* **FAULT-08** — 20 real parent Runs each spawn one child and park in
  ``waiting_children``; the Python execution pool is saturated to one slot and
  the independent child pool shrunk to two. The test samples the authoritative
  rows while the children progress: at most two active, 18 queued, 20 waiting
  parents, and a queued child never has an attempt. Queueing is never counted as
  execution. After the wave, P50/P95 queue and completion latencies are read from
  PostgreSQL, and the deliberately blocked resumes prove the queue drains once
  the foreign top-level slot is released.
* **FAULT-13** — one nested condition/join graph (an inner region inside an
  outer region's selected branch) with a skipped branch and a required branch.
  In the success run each join fires exactly once and the envelope names the one
  activated and the one skipped source; in the required-failure run the child's
  real ``budget_exhausted`` fails the whole run explicitly (``failed``, stable
  code) and neither join ever fires. The durable event sequence stays contiguous
  and the run never stays parked forever.

The machine-readable evidence is written to ``product/reports/f13b/T108-faults.json``
only when ``F13B_WRITE_EVIDENCE=1`` (see ``support/t108.py``); the narrative
report is ``reports/f13b/T108-delegation-faults.md``.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from support.runtime_stack import Stack
from support import (
    f00b,
    t024,
    t025,
    t027,
    t029,
    t032,
    t033,
    t036,
    t075,
    t078,
    t080,
    t081,
    t108,
)
from support import stack as st
from support.certs import make_stack_certificates, unique_internal_addr
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
)

GATE_COMMAND = "python -m pytest tests/f13b/test_t108_delegation_faults.py -q"


@pytest.fixture
def t108_stack(tmp_path, fresh_database, go_binaries, api_server) -> Stack:
    """The T029 real stack with the T068/T084/T101 execution-lease margin.

    ``ORCHARA_EXECUTION_LEASE_TTL=300s`` is the documented test-only margin: the
    cases claim a child attempt, sample/park the parent and then complete the
    child over several protocol calls while other suites share the host. It is a
    deployment knob, not a product rule; the default documented lease is 30s.
    """
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    env = t029.api_extra_env(object_dir, certificates, signing_key=signing_key)
    env["ORCHARA_EXECUTION_LEASE_TTL"] = "300s"
    server = api_server(dsn, extra_env=env)
    stack = Stack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        object_dir=object_dir,
        certificates=certificates,
        binaries=go_binaries,
        signing_key=signing_key,
    )
    f00b.wait_for_internal(stack)
    return stack


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# FAULT-07: one parent, two simultaneous calls to the same child version
# ---------------------------------------------------------------------------
def test_fault_07_one_parent_calls_the_same_child_version_twice_concurrently(
    t108_stack,
) -> None:
    stack = t108_stack
    dbname = stack.dbname
    with t108.start(
        "FAULT-07",
        "One parent Run calls the same pinned child application version twice at "
        "the same time",
        injection_point=(
            "two barrier-synchronised POST /internal/v1/runs/{parent_run_id}/children "
            "(contract spawnChild) requests from one claimed real parent attempt, same "
            "application_version_id, distinct logical keys call_app:left / "
            "call_app:right; one child slot is then held by a real claim while a real "
            "cmd/supervisor exec --once process executes the other child, and the parent "
            "is resumed by another supervisor process that replays both invocations "
            "through the real graph scheduler"
        ),
        expected=[
            "two child Runs with distinct ids, distinct checkpoint threads and distinct attempts",
            "each child seals only its own mapped input (alpha / beta) and returns only its own echo",
            "both child attempts are provably live at the same instant and each active child "
            "holds exactly two live child-slot allocations (global + parent)",
            "the resumed parent converges at one join and succeeds with both child outputs in place",
        ],
    ) as fault:
        fault.command(GATE_COMMAND)
        _admin, creator = t075.creator(stack)
        child = t078.create_child_version(stack, creator)
        definition = t108.parallel_calls_definition(child["id"])
        fault.seed = {
            "child_application_version_id": child["id"],
            "child_manifest_sha256": child["manifest_sha256"],
            "parent_definition_sha256": t108.definition_digest(definition)["sha256"],
            "logical_keys": ["call_app:left", "call_app:right"],
            "inputs": {"call_app:left": {"source": "alpha"}, "call_app:right": {"source": "beta"}},
            "concurrency": "threading.Barrier(2); two independent real mTLS HTTP connections",
        }
        application_id, parent = t078.debug_parent(stack, creator, definition, {})
        fault.seed["parent_application_id"] = application_id
        fault.seed["parent_run_id"] = parent["id"]

        # 1. The real claim: the parent attempt is a genuine live lease.
        parent_lease = t108.claim(stack, run_id=parent["id"])
        assert t081.execution_counter(dbname)["used"] == 1
        fault.truth("parent_attempt_start", {
            "parent_run_id": parent["id"],
            "attempt_token_sha256": _sha256(parent_lease["attempt_token"].encode()),
            "execution_slots_used": 1,
        })

        # 2. Inject the concurrency: both spawnChild calls are in flight together.
        calls = [
            ("call_app:left", {"source": "alpha"}),
            ("call_app:right", {"source": "beta"}),
        ]
        responses = t108.spawn_concurrently(
            stack,
            parent["id"],
            attempt_token=parent_lease["attempt_token"],
            application_version_id=child["id"],
            calls=calls,
        )
        fault.check(
            "both spawnChild calls were accepted (202)",
            [response["status"] for response in responses] == [202, 202],
            [(response["status"], response["text"][:200]) for response in responses],
        )
        overlap = {
            "max_started_at": max(response["started_at"] for response in responses),
            "min_finished_at": min(response["finished_at"] for response in responses),
            "all_started_before_any_finished": max(
                response["started_at"] for response in responses
            )
            < min(response["finished_at"] for response in responses),
        }
        fault.check(
            "the two spawnChild requests were simultaneously in flight",
            overlap["all_started_before_any_finished"],
            overlap,
        )
        fault.observe("spawn_overlap", overlap)
        by_key = {response["logical_key"]: response for response in responses}
        assert set(by_key) == {"call_app:left", "call_app:right"}
        child_ids = [by_key[key]["body"]["id"] for key in ("call_app:left", "call_app:right")]
        fault.check(
            "the platform returned two distinct child Runs for the same parent",
            len(set(child_ids)) == 2,
            child_ids,
        )

        # 3. Independent truth: the run_children receipts and the sealed inputs.
        receipts = {
            row["parent_invocation_key"]: row
            for row in t078.run_children_rows(dbname, parent["id"])
        }
        fault.check(
            "both logical keys have their own run_children receipt",
            set(receipts) == {"call_app:left", "call_app:right"},
            sorted(receipts),
        )
        threads: dict[str, str] = {}
        for key, source in (("call_app:left", "alpha"), ("call_app:right", "beta")):
            receipt = receipts[key]
            sealed = t108.artifact_bytes(stack, receipt["input_artifact_id"])
            fault.check(
                f"the sealed input for {key} is exactly its own mapping",
                json.loads(sealed) == {"source": source},
                {"sealed": json.loads(sealed), "expected_source": source},
            )
            fault.check(
                f"the sealed input hash for {key} matches the receipt",
                receipt["input_sha256"] == _sha256(sealed),
                {"recorded": receipt["input_sha256"], "recomputed": _sha256(sealed)},
            )
            child_row = t078.run_row(dbname, receipt["child_run_id"])
            threads[key] = child_row["checkpoint_thread_id"]
            fault.check(
                f"{key} is an independent child Run of the same parent version",
                child_row["parent_run_id"] == parent["id"]
                and child_row["application_version_id"] == child["id"]
                and child_row["owner_id"] == creator.principal_id
                and child_row["state"] == "queued",
                child_row,
            )
            fault.check(
                f"the child receipt for {key} froze depth 1 and the pinned version",
                receipt["depth"] == 1 and receipt["child_version_id"] == child["id"],
                receipt,
            )
        parent_thread = t078.run_row(dbname, parent["id"])["checkpoint_thread_id"]
        fault.check(
            "the two children have distinct checkpoint threads, both distinct from the parent",
            len(set(threads.values())) == 2 and parent_thread not in threads.values(),
            {"parent": parent_thread, "children": threads},
        )

        # 4. One child is held by the real claim protocol while a real supervisor
        #    process executes the other. The held live lease makes the two-slot
        #    overlap deterministic; the durable attempt rows prove it.
        held = t108.claim_child(stack)
        held_child = held["run_id"]
        held_allocations = [
            row
            for row in t081.allocation_rows(dbname)
            if row["holder_key"] == "job:" + held["job_id"]
        ]
        fault.check(
            "one child holds a real live child-slot lease: two live allocations (global + parent)",
            int(t081.child_counter(dbname)["used"]) == 1
            and len(held_allocations) == 2
            and all(row["released_at"] is None for row in held_allocations)
            and sorted(row["scope"] for row in held_allocations)
            == ["global", "parent:" + parent["id"]],
            held_allocations,
        )
        fault.observe("held_child_allocations", held_allocations)
        executing_report, sampler = t108.execute_once_with_slot_peak(stack)
        executing_child = executing_report["run_id"]
        fault.check(
            "the real supervisor executed the other child while the first lease was live",
            executing_child in child_ids
            and executing_child != held_child
            and executing_report["status"] == "executed"
            and executing_report["outcome"] == "succeeded",
            executing_report,
        )
        overlap_facts = t108.child_attempt_overlap(dbname, held_child, executing_child)
        fault.check(
            "the durable attempts show both children live at the same instant",
            int(overlap_facts["held_live_attempts"]) == 1
            and int(overlap_facts["executing_exited_attempts"]) == 1
            and bool(overlap_facts["held_started_before_executing"])
            and bool(overlap_facts["executing_interval_ordered"]),
            overlap_facts,
        )
        fault.observe("child_slot_overlap", {
            "sampled_peak_child_slots_used": sampler["peak_child_slots_used"],
            "sampler_reads": len(sampler["reads"]),
            "durable": overlap_facts,
        })

        # The held attempt is released as the platform's documented "never ran"
        # exit (started=false -> job ready, run queued), then the real supervisor
        # executes that child too.
        releaser = t029.execution_client(stack, "supervisor")
        requeued = releaser.confirm_exit(
            held["attempt_token"],
            execution_unit_id=t108.UNIT,
            evidence=t029.exit_evidence(held, started=False, sandbox_started=False),
        )
        fault.check(
            "the held attempt is released as never-started and the Run is requeued",
            requeued.status == 200
            and t081.wait_run_state(dbname, held_child, {"queued"})["state"] == "queued",
            {"status": requeued.status, "text": requeued.text},
        )
        fault.check(
            "the requeue released the held child slot exactly once",
            int(t081.child_counter(dbname)["used"]) == 0,
            t081.child_counter(dbname),
        )
        held_report, _completed = t108.execute_once(stack)
        fault.check(
            "the real supervisor then executed the requeued child",
            held_report["status"] == "executed"
            and held_report["run_id"] == held_child
            and held_report["outcome"] == "succeeded",
            held_report,
        )
        child_outputs: dict[str, dict] = {}
        attempt_evidence: dict[str, list[dict]] = {}
        key_by_child = {
            receipts[key]["child_run_id"]: key for key in ("call_app:left", "call_app:right")
        }
        for key in ("call_app:left", "call_app:right"):
            child_id = receipts[key]["child_run_id"]
            child_row = t078.run_row(dbname, child_id)
            fault.check(
                f"the child for {key} really succeeded with a sealed output",
                child_row["state"] == "succeeded" and child_row["output_artifact_id"],
                child_row,
            )
            child_outputs[key] = t108.artifact_document(
                stack, child_row["output_artifact_id"]
            )
            evidence = t108.attempt_exit_evidence(dbname, child_id)
            attempt_evidence[key] = evidence
            if child_id == held_child:
                fault.check(
                    f"the held child for {key} has one never-started attempt and one executed one",
                    len(evidence) == 2
                    and evidence[0]["exit_evidence"]["started"] is False
                    and evidence[1]["exited_at"] is not None,
                    evidence,
                )
            else:
                fault.check(
                    f"the executing child for {key} ran exactly one real attempt",
                    len(evidence) == 1 and evidence[0]["exited_at"] is not None,
                    evidence,
                )
        fault.check(
            "each child executed its own sealed input (no crosstalk)",
            child_outputs == {
                "call_app:left": {"echo": "alpha"},
                "call_app:right": {"echo": "beta"},
            },
            child_outputs,
        )
        fault.check(
            "the child pool was fully released after both children exited",
            int(t081.child_counter(dbname)["used"]) == 0,
            t081.child_counter(dbname),
        )

        # 5. Park the parent at the documented children checkpoint and resume it.
        t108.park_waiting(stack, parent_lease, child_run_id=receipts["call_app:left"]["child_run_id"])
        waiting = t081.waiting_row(dbname, parent["id"])
        fault.check(
            "the parent really parked at the children checkpoint",
            waiting is not None and waiting["kind"] == "children",
            waiting,
        )
        fault.check(
            "the parked parent released its own top-level execution slot",
            int(t081.execution_counter(dbname)["used"]) == 0,
            t081.execution_counter(dbname),
        )
        resumed, _completed = t108.execute_once(stack)
        fault.check(
            "the real supervisor resumed and succeeded the same parent Run",
            resumed["status"] == "executed"
            and resumed["run_id"] == parent["id"]
            and resumed["outcome"] == "succeeded",
            resumed,
        )
        parent_row = t081.wait_run_state(dbname, parent["id"], {"succeeded"})
        final = t108.artifact_document(stack, parent_row["output_artifact_id"])
        expected_envelope = {
            "results": {
                "left": {"status": "succeeded", "value": {"echo": "alpha"}},
                "right": {"status": "succeeded", "value": {"echo": "beta"}},
            }
        }
        fault.check(
            "the resumed parent converged at one join with the two distinct child results",
            final == expected_envelope,
            final,
        )
        firing = t108.join_firing(dbname, parent["id"], "join")
        fault.check(
            "the join fired exactly once",
            firing["data_rows"] == 1 and len(firing["succeeded_event_seqs"]) == 1,
            firing,
        )
        fault.check(
            "no second child was created by the resumed attempt",
            len(t078.run_children_rows(dbname, parent["id"])) == 2
            and t078.child_count(dbname, parent["id"]) == 2,
            {
                "receipts": len(t078.run_children_rows(dbname, parent["id"])),
                "children": t078.child_count(dbname, parent["id"]),
            },
        )
        attempts = {
            "parent": len(t078.attempt_rows(dbname, parent["id"])),
            "held_child": len(attempt_evidence[key_by_child[held_child]]),
            "executing_child": len(attempt_evidence[key_by_child[executing_child]]),
        }
        fault.check(
            "the parent ran twice (spawn + replay) and each child has one succeeded attempt",
            attempts
            == {"parent": 2, "held_child": 2, "executing_child": 1},
            attempts,
        )
        t081.assert_slot_consistent(dbname)
        fault.check(
            "every child-slot allocation is released exactly once",
            int(t081.child_counter(dbname)["used"]) == 0
            and t081.parent_counter(dbname, parent["id"])["used"] == 0
            and int(t081.execution_counter(dbname)["used"]) == 0,
            {
                "child": t081.child_counter(dbname),
                "parent": t081.parent_counter(dbname, parent["id"]),
                "execution": t081.execution_counter(dbname),
            },
        )
        seq = t108.event_seq_facts(dbname, parent["id"])
        fault.check(
            "the parent's durable event sequence stayed contiguous",
            seq["contiguous"] and seq["first_seq"] == 1,
            seq,
        )
        fault.truth("children", {
            key: {
                "child_run_id": receipts[key]["child_run_id"],
                "checkpoint_thread_id": threads[key],
                "sealed_input_sha256": receipts[key]["input_sha256"],
                "output": child_outputs[key],
                "attempts": attempt_evidence[key],
            }
            for key in ("call_app:left", "call_app:right")
        })
        fault.truth("parent", {
            "run_id": parent["id"],
            "state": parent_row["state"],
            "attempts": attempts["parent"],
            "final_output": final,
            "join_firing": firing,
            "event_seq": seq,
        })
        fault.observe("slot_counters", {
            "child_pool": t081.child_counter(dbname),
            "parent_scope": t081.parent_counter(dbname, parent["id"]),
            "execution_pool": t081.execution_counter(dbname),
        })
        fault.mark_passed()


# ---------------------------------------------------------------------------
# FAULT-08: 20 waiting parents, one Python slot, two child slots
# ---------------------------------------------------------------------------
def test_fault_08_twenty_waiting_parents_do_not_starve_a_two_slot_child_pool(
    t108_stack,
) -> None:
    stack = t108_stack
    dbname = stack.dbname
    child_capacity = 2
    child_count_target = 20
    with t108.start(
        "FAULT-08",
        "20 parent Runs waiting on children do not starve a two-slot child pool",
        injection_point=(
            "20 real debug parent Runs each claim -> spawnChild -> waiting_children; the "
            "global/execution_slots pool is saturated to one live top-level Run and the "
            "independent global/child_slots pool is shrunk from 8 to 2 (platform.quota_counters)"
        ),
        expected=[
            "children still claim and complete while all 20 parents wait (no pool starvation)",
            "at most two children are ever active and the other 18 are queued with no attempt",
            "completion is monotonic and every parent receives exactly one children decision",
            "the parents' resume jobs are explicitly blocked by the foreign top-level slot, "
            "not lost: releasing it lets the queue drain",
        ],
    ) as fault:
        fault.command(GATE_COMMAND)
        fault.seed = {
            "parents": child_count_target,
            "execution_pool_capacity": 1,
            "child_pool_capacity": child_capacity,
            "child_graph": "start -> end echo child executed by the real supervisor",
            "saturating_top_level_run": "a real claimed debug Run that owns the only Python slot",
        }
        _admin, creator = t075.creator(stack)
        child = t078.create_child_version(stack, creator)
        application_id = t081.parent_application(
            stack, creator, t078.parent_definition([child["id"]])
        )
        fault.seed["parent_application_id"] = application_id
        fault.seed["child_application_version_id"] = child["id"]

        # A real claimed top-level Run holds the single Python execution slot.
        t081.set_execution_capacity(dbname, 40)
        saturating = t081.submit_parent_run(stack, creator, application_id, {"source": "saturating"})
        saturating_lease = t108.claim(stack, run_id=saturating["id"])
        fault.truth("saturating_run", {
            "run_id": saturating["id"],
            "state": t078.run_row(dbname, saturating["id"])["state"],
            "execution_slots_used": 1,
        })

        # Setup: the child pool is closed, so every spawn is an explicitly queued child.
        t081.set_child_capacity(dbname, 0)
        parents: list[dict] = []
        for index in range(child_count_target):
            source = f"t108-parent-{index:02d}"
            parent = t081.submit_parent_run(stack, creator, application_id, {"source": source})
            lease = t081.claim_lease(stack)
            assert lease["run_id"] == parent["id"], (lease, parent)
            spawned_child = t081.spawn_child(
                stack,
                parent["id"],
                lease,
                logical_key="call_app:child",
                application_version_id=child["id"],
                input_value={"source": source},
            )
            t081.park_waiting(stack, lease, child_run_id=spawned_child["id"])
            parents.append(
                {
                    "parent_id": parent["id"],
                    "child_id": spawned_child["id"],
                    "source": source,
                }
            )
        parent_ids = [entry["parent_id"] for entry in parents]
        child_ids = [entry["child_id"] for entry in parents]
        by_child = {entry["child_id"]: entry for entry in parents}

        # The Python pool is now saturated to one slot (the saturating Run owns it).
        t081.set_execution_capacity(dbname, 1)
        fault.truth("setup", {
            "parents": len(parents),
            "children_queued": len(child_ids),
            "execution_slots_used": int(t081.execution_counter(dbname)["used"]),
            "child_slots_used": int(t081.child_counter(dbname)["used"]),
        })

        samples: list[dict] = []

        def sample(label: str) -> dict:
            snapshot = t108.delegation_snapshot(
                dbname, parent_ids=parent_ids, child_ids=child_ids
            )
            snapshot["label"] = label
            samples.append(snapshot)
            t108.assert_snapshot_invariants(
                snapshot, total_children=child_count_target, capacity=child_capacity
            )
            return snapshot

        s0 = sample("after-setup")
        assert s0["active"] == 0 and s0["queued"] == child_count_target
        assert s0["parents_waiting_row"] == child_count_target
        fault.check(
            "after setup no child is executing and every child is queued",
            s0["active"] == 0
            and s0["queued"] == child_count_target
            and s0["completed"] == 0
            and s0["live_child_allocations"] == 0,
            s0,
        )
        fault.check(
            "queued children have no attempt: queueing is not execution",
            s0["queued_with_attempts"] == [] and s0["child_pool"]["used"] == 0,
            s0,
        )

        # Phase A: the small pool really admits two concurrent children, no more.
        t081.set_child_capacity(dbname, child_capacity)
        first = t108.claim_child(stack)
        second = t108.claim_child(stack)
        s1 = sample("two-children-active")
        fault.check(
            "the two-slot child pool admitted exactly two simultaneous active children",
            s1["active"] == 2 and s1["queued"] == child_count_target - 2,
            s1,
        )
        fault.check(
            "each active child holds its own live attempt and two live child-slot allocations",
            s1["active_without_live_attempt"] == []
            and s1["active_without_two_allocations"] == []
            and s1["live_child_allocations"] == 4,
            s1,
        )
        fault.check(
            "a third child cannot claim while both slots are taken",
            t081.claim_lease(stack) is None,
            {"child_pool": t081.child_counter(dbname)},
        )

        first_child_id = first["run_id"]
        t108.complete_child(
            stack,
            first,
            output=t108.child_output_from_sealed_input(
                stack, by_child[first_child_id]["parent_id"], "call_app:child"
            ),
        )
        t081.wait_run_state(dbname, first_child_id, {"succeeded"})
        s2 = sample("after-first-completion")
        fault.check(
            "completion is monotonic: one completed, one still active, the rest queued",
            s2["completed"] == 1
            and s2["active"] == 1
            and s2["queued"] == child_count_target - 2,
            s2,
        )
        third = t108.claim_child(stack)
        s3 = sample("slot-backfilled")
        fault.check(
            "the freed child slot was immediately backfilled from the queue",
            s3["active"] == 2
            and s3["completed"] == 1
            and third["run_id"] != first_child_id,
            s3,
        )
        for lease in (second, third):
            child_id = lease["run_id"]
            t108.complete_child(
                stack,
                lease,
                output=t108.child_output_from_sealed_input(
                    stack, by_child[child_id]["parent_id"], "call_app:child"
                ),
            )
            t081.wait_run_state(dbname, child_id, {"succeeded"})
        s4 = sample("three-completed")
        fault.check(
            "three children completed through the protocol with no leak",
            s4["completed"] == 3
            and s4["active"] == 0
            and s4["queued"] == child_count_target - 3
            and s4["live_child_allocations"] == 0,
            s4,
        )

        # Phase B: the remaining children are executed by the real supervisor while
        # every parent still waits and the Python pool stays saturated.
        done = {first_child_id, second["run_id"], third["run_id"]}
        remaining = [child_id for child_id in child_ids if child_id not in done]
        executed = []
        for index in range(len(remaining)):
            report, _completed = t108.execute_once(stack)
            fault.check(
                f"real supervisor execution {index} ran a queued child while the Python pool "
                "stayed saturated",
                report["status"] == "executed"
                and report["outcome"] == "succeeded"
                and report["run_id"] in remaining,
                report,
            )
            executed.append(report["run_id"])
            fault.check(
                "the child execution consumed no Python execution slot",
                int(t081.execution_counter(dbname)["used"]) == 1,
                t081.execution_counter(dbname),
            )
            sample(f"supervisor-{index}")
        fault.check(
            "every remaining child completed exactly once",
            sorted(executed) == sorted(remaining) and len(set(executed)) == len(remaining),
            {"executed": len(executed), "remaining": len(remaining)},
        )

        # Completion truth from the rows, not from the process reports.
        final_children = t108.children_final_rows(dbname, child_ids)
        for child_id in child_ids:
            row = final_children[child_id]
            fault.check(
                f"child {child_id[:8]} is succeeded with one attempt and one sealed output",
                row["state"] == "succeeded"
                and row["output_artifact_id"]
                and int(row["attempts"]) == 1,
                row,
            )
        faults = [
            child_id
            for child_id, row in final_children.items()
            if row["state"] != "succeeded"
        ]
        fault.check("no child failed", faults == [], faults)

        # Every parent has exactly one children decision and one ready resume job.
        final_parents = t108.parents_final_rows(dbname, parent_ids)
        for entry in parents:
            row = final_parents[entry["parent_id"]]
            decision = json.loads(row["decision"]) if row["decision"] else None
            fault.check(
                f"parent {entry['source']} has one succeeded children decision and one resume job",
                int(row["waitings"]) == 1
                and decision
                == {
                    "kind": "children",
                    "outcome": "succeeded",
                    "child_run_id": entry["child_id"],
                }
                and int(row["resume_jobs"]) == 1
                and row["resume_job_state"] == "ready"
                and row["state"] == "queued",
                row,
            )

        latency = t108.latency_percentiles(dbname, child_ids)
        fault.observe("latency", latency)
        fault.check(
            "every child has a completion timestamp and the latency sample is complete",
            latency["samples"] == child_count_target
            and latency["finished"] == child_count_target
            and latency["p95_total_s"] >= latency["p50_total_s"] >= 0,
            latency,
        )
        fault.check(
            "no child slot or Python slot leaked",
            int(t081.child_counter(dbname)["used"]) == 0
            and int(t081.execution_counter(dbname)["used"]) == 1
            and t081.parent_counter(dbname, parents[0]["parent_id"])["used"] == 0,
            {
                "child": t081.child_counter(dbname),
                "execution": t081.execution_counter(dbname),
                "one_parent": t081.parent_counter(dbname, parents[0]["parent_id"]),
            },
        )
        t081.assert_slot_consistent(dbname)
        fault.check(
            "nothing is claimable while the foreign top-level slot is held (explicit block)",
            t081.claim_lease(stack) is None,
            {
                "execution": t081.execution_counter(dbname),
                "ready_parent_jobs": sum(
                    int(row["resume_jobs"]) for row in final_parents.values()
                ),
            },
        )
        fault.observe("samples", [
            {
                "label": item["label"],
                "active": item["active"],
                "queued": item["queued"],
                "completed": item["completed"],
                "failed": item["failed"],
                "parents_by_state": item["parents_by_state"],
                "parents_with_waiting_row": item["parents_waiting_row"],
                "child_pool_used": item["child_pool"]["used"],
                "execution_pool_used": item["execution_pool"]["used"],
                "claimable_jobs": item["claimable_jobs"],
            }
            for item in samples
        ])
        fault.observe("max_active_observed", max(item["active"] for item in samples))
        fault.observe("max_queued_observed", max(item["queued"] for item in samples))

        # The blocked resumés are not deadlocked: release the foreign top-level
        # slot through the real cancel/exit protocol and the queue drains.
        cancelled = t036.cancel_run(creator, saturating["id"])
        assert cancelled.status == 202, cancelled.text
        supervisor = t029.execution_client(stack, "supervisor")
        exited = supervisor.confirm_exit(
            saturating_lease["attempt_token"],
            execution_unit_id=t108.UNIT,
            evidence=t029.exit_evidence(saturating_lease, started=False),
        )
        assert exited.status == 200, exited.text
        t081.wait_run_state(dbname, saturating["id"], {"cancelled"})
        fault.check(
            "releasing the foreign Python slot returns it exactly once",
            int(t081.execution_counter(dbname)["used"]) == 0,
            t081.execution_counter(dbname),
        )
        drained, _completed = t108.execute_once(stack)
        fault.check(
            "a blocked parent resume is claimable once the foreign slot is free",
            drained["status"] == "executed"
            and drained["run_id"] in parent_ids
            and drained["outcome"] == "succeeded",
            drained,
        )
        if drained["run_id"] in parent_ids:
            resumed_parent = drained["run_id"]
            fault.check(
                "the drained parent really succeeds and replays its one child",
                t078.child_count(dbname, resumed_parent) == 1,
                {"children": t078.child_count(dbname, resumed_parent)},
            )
            resumed_row = t081.wait_run_state(
                dbname, resumed_parent, {"succeeded"}
            )
            entry = next(item for item in parents if item["parent_id"] == resumed_parent)
            output = t108.artifact_document(stack, resumed_row["output_artifact_id"])
            fault.check(
                "the drained parent's output is its own child's echo",
                output == {"echo": entry["source"]},
                output,
            )
        fault.truth("final", {
            "children_succeeded": child_count_target,
            "errors": len(faults),
            "parents_with_decision": sum(
                1
                for row in final_parents.values()
                if row["decision"] is not None
            ),
            "execution_slots_used": int(t081.execution_counter(dbname)["used"]),
            "child_slots_used": int(t081.child_counter(dbname)["used"]),
            "latency": latency,
            "samples": len(samples),
        })
        fault.mark_passed()


# ---------------------------------------------------------------------------
# FAULT-13: nested branches, skipped node, required failure
# ---------------------------------------------------------------------------
def _join_input_facts(manifest: dict, envelope: dict) -> dict:
    """The sealed join config versus the envelope's activated/skipped sources."""
    by_id = {node["id"]: node for node in manifest["definition"]["nodes"]}
    facts = {}
    for join_id, document in envelope.items():
        config = by_id[join_id]["config"]
        sources = list(config["required_sources"]) + list(config["optional_sources"])
        results = document["results"]
        activated = [source for source in sources if results[source]["status"] != "skipped"]
        skipped = [source for source in sources if results[source]["status"] == "skipped"]
        facts[join_id] = {
            "fork_id": config["fork_id"],
            "sources": sources,
            "required": list(config["required_sources"]),
            "optional": list(config["optional_sources"]),
            "activated": activated,
            "skipped": skipped,
        }
    return facts


def test_fault_13_nested_branches_join_once_and_a_required_failure_fails_closed(
    t108_stack,
) -> None:
    stack = t108_stack
    dbname = stack.dbname
    with t108.start(
        "FAULT-13",
        "Nested branch regions with a skipped branch and a required failing branch: "
        "join fires exactly once or the run fails explicitly, never waits forever",
        injection_point=(
            "a real debug Run of a nested condition/join graph (choose -> choose2 -> "
            "{deep|shallow} -> join2 -> join <- direct) whose branch nodes are real "
            "call_app delegations; sub-case A executes the selected child normally, "
            "sub-case B gives the parent a 0.00000001 USD allowance so the required "
            "child's real model reservation is refused (budget_exhausted)"
        ),
        expected=[
            "sub-case A: each join fires exactly once; the envelope names one activated and "
            "one skipped source; the run succeeds",
            "sub-case B: the required child's real failure fails the run with its stable code; "
            "neither join fires and no node waits forever",
            "the durable run event sequence stays contiguous and every skip is published once",
        ],
    ) as fault:
        fault.command(GATE_COMMAND)
        _admin, creator = t075.creator(stack)

        # -- sub-case A: nested branches with a skipped node -> join once ----
        echo_child = t078.create_child_version(stack, creator)
        definition_a = t108.nested_definition(
            echo_child["id"],
            output_key="echo",
            literals={"deep": "deep", "shallow": "shallow", "direct": "direct"},
        )
        fault.seed["nested_join_graph"] = t108.definition_digest(definition_a)["sha256"]
        fault.seed["echo_child_version_id"] = echo_child["id"]
        application_a, run_a = t078.debug_parent(stack, creator, definition_a, {"mode": "deep"})
        fault.seed["success_parent_run_id"] = run_a["id"]

        first, _completed = t108.execute_once(stack)
        fault.check(
            "the parent attempt spawned the selected branch child and parked on children",
            first["status"] == "executed"
            and first["run_id"] == run_a["id"]
            and first["outcome"] == "waiting_children",
            first,
        )
        waiting = t081.waiting_row(dbname, run_a["id"])
        fault.check(
            "the parent parked at the stable child checkpoint",
            waiting is not None
            and waiting["kind"] == "children"
            and waiting["checkpoint_id"].startswith("child:"),
            waiting,
        )
        deep_child = waiting["checkpoint_id"].split(":", 1)[1]
        states_after_park = t108.node_states(dbname, run_a["id"])
        fault.check(
            "the skipped branch nodes and the joins are not published on the waiting hand-over",
            "shallow" not in states_after_park
            and "direct" not in states_after_park
            and "join2" not in states_after_park
            and "join" not in states_after_park,
            states_after_park,
        )
        child_report, _completed = t108.execute_once(stack)
        fault.check(
            "the selected branch child really executed and succeeded",
            child_report["status"] == "executed"
            and child_report["run_id"] == deep_child
            and child_report["outcome"] == "succeeded",
            child_report,
        )
        decision = t081.wait_for(
            lambda: t081.waiting_decision(dbname, run_a["id"]), timeout=20.0
        )
        fault.check(
            "the lifecycle hook resolved the wait once with the child's outcome",
            decision == {
                "kind": "children",
                "outcome": "succeeded",
                "child_run_id": deep_child,
            },
            decision,
        )
        resumed, _completed = t108.execute_once(stack)
        fault.check(
            "the resumed parent converged and succeeded",
            resumed["status"] == "executed"
            and resumed["run_id"] == run_a["id"]
            and resumed["outcome"] == "succeeded",
            resumed,
        )
        parent_row = t081.wait_run_state(dbname, run_a["id"], {"succeeded"})
        states = t108.node_states(dbname, run_a["id"])
        join2 = t108.join_firing(dbname, run_a["id"], "join2")
        outer_join = t108.join_firing(dbname, run_a["id"], "join")
        fault.check(
            "each join fired exactly once (one data artifact, one succeeded event)",
            join2["data_rows"] == 1
            and len(join2["succeeded_event_seqs"]) == 1
            and outer_join["data_rows"] == 1
            and len(outer_join["succeeded_event_seqs"]) == 1,
            {"join2": join2, "join": outer_join},
        )
        join2_doc = t108.artifact_document(stack, join2["artifact_ids"][0])
        join_doc = t108.artifact_document(stack, outer_join["artifact_ids"][0])
        final = t108.artifact_document(stack, parent_row["output_artifact_id"])
        expected_join2 = {
            "results": {
                "deep": {"status": "succeeded", "value": {"echo": "deep"}},
                "shallow": {"status": "skipped"},
            }
        }
        expected_join = {
            "results": {
                "join2": {"status": "succeeded", "value": expected_join2},
                "direct": {"status": "skipped"},
            }
        }
        fault.check(
            "the inner join envelope names the activated and the skipped branch",
            join2_doc == expected_join2,
            join2_doc,
        )
        fault.check(
            "the outer join envelope nests the inner region and skips the unselected branch",
            join_doc == expected_join,
            join_doc,
        )
        fault.check(
            "the run's final output is exactly the outer join envelope",
            final == expected_join,
            final,
        )
        fault.check(
            "the skipped branch nodes were published as skipped exactly once",
            states.get("shallow") == ["skipped"] and states.get("direct") == ["skipped"],
            states,
        )
        fault.check(
            "the executed branch and the end node reached succeeded",
            states.get("deep", [])[-1:] == ["succeeded"]
            and states.get("end", [])[-1:] == ["succeeded"]
            and states.get("join2", [])[-1:] == ["succeeded"]
            and states.get("join", [])[-1:] == ["succeeded"],
            states,
        )
        manifest_a = t108.sealed_manifest(stack, run_a["id"])
        envelope_facts = _join_input_facts(
            manifest_a, {"join2": join2_doc, "join": join_doc}
        )
        for join_id, facts in envelope_facts.items():
            fault.check(
                f"{join_id} waited only for its activated required source",
                len(facts["activated"]) == 1 and len(facts["skipped"]) == 1,
                facts,
            )
        seq_a = t108.event_seq_facts(dbname, run_a["id"])
        fault.check(
            "the success run's event sequence is contiguous",
            seq_a["contiguous"] and seq_a["first_seq"] == 1,
            seq_a,
        )
        fault.truth("nested_success", {
            "parent_run_id": run_a["id"],
            "state": parent_row["state"],
            "child_run_id": deep_child,
            "node_states": states,
            "join2": {
                "artifact_id": join2["artifact_ids"][0],
                "envelope": join2_doc,
                "firing_seq": join2["succeeded_event_seqs"],
            },
            "join": {
                "artifact_id": outer_join["artifact_ids"][0],
                "envelope": join_doc,
                "firing_seq": outer_join["succeeded_event_seqs"],
            },
            "join_inputs": envelope_facts,
            "event_seq": seq_a,
            "child_children": {
                "children": t078.child_count(dbname, run_a["id"]),
                "attempts": len(t078.attempt_rows(dbname, deep_child)),
            },
        })

        # -- sub-case B: a required branch really fails -> explicit failure --
        model_id = t080.seed_priced_golden(stack, creator)
        agent_child = t080.agent_child_version(stack, creator, model_id=model_id)
        definition_b = t108.nested_definition(agent_child["id"], output_key="text")
        fault.seed["required_failure_child_version_id"] = agent_child["id"]
        fault.seed["required_failure_allowance"] = str(t080.ONE_UNIT)
        application_b, run_b = t078.debug_parent(stack, creator, definition_b, {"mode": "deep"})
        fault.seed["failure_parent_run_id"] = run_b["id"]
        t080.set_parent_budget(dbname, run_b["id"], t080.ONE_UNIT)

        first_b, _completed = t108.execute_once(stack)
        fault.check(
            "the required-failure parent also parked on its required child",
            first_b["status"] == "executed"
            and first_b["run_id"] == run_b["id"]
            and first_b["outcome"] == "waiting_children",
            first_b,
        )
        waiting_b = t081.waiting_row(dbname, run_b["id"])
        required_child = waiting_b["checkpoint_id"].split(":", 1)[1]
        fault.check(
            "the required child inherited the parent's tiny allowance",
            t080.run_budget(dbname, required_child) == t080.run_budget(dbname, run_b["id"]),
            {
                "child": t080.run_budget(dbname, required_child),
                "parent": t080.run_budget(dbname, run_b["id"]),
            },
        )
        child_report_b, _completed = t108.execute_once(stack)
        fault.check(
            "the required child's real model reservation was refused (budget_exhausted)",
            child_report_b["status"] == "executed"
            and child_report_b["run_id"] == required_child
            and child_report_b["outcome"] == "failed"
            and child_report_b["error_code"] == "budget_exhausted",
            child_report_b,
        )
        child_row_b = t081.wait_run_state(dbname, required_child, {"failed"})
        fault.check(
            "the child's failure is the authoritative budget_exhausted row",
            child_row_b["error_code"] == "budget_exhausted",
            child_row_b,
        )
        decision_b = t081.wait_for(
            lambda: t081.waiting_decision(dbname, run_b["id"]), timeout=20.0
        )
        fault.check(
            "the lifecycle hook resolved the required child's failure once",
            decision_b == {
                "kind": "children",
                "outcome": "failed",
                "child_run_id": required_child,
            },
            decision_b,
        )
        resumed_b, _completed = t108.execute_once(stack)
        fault.check(
            "the parent fails explicitly with the child's stable code",
            resumed_b["status"] == "executed"
            and resumed_b["run_id"] == run_b["id"]
            and resumed_b["outcome"] == "failed"
            and resumed_b["error_code"] == "budget_exhausted",
            resumed_b,
        )
        parent_row_b = t081.wait_run_state(dbname, run_b["id"], {"failed"})
        fault.check(
            "the failed run is terminal, not parked",
            parent_row_b["state"] == "failed"
            and parent_row_b["error_code"] == "budget_exhausted"
            and parent_row_b["finished_at"] is not None,
            parent_row_b,
        )
        states_b = t108.node_states(dbname, run_b["id"])
        failed_deep = [
            event
            for event in t108.node_status_events(dbname, run_b["id"])
            if event["payload"]["node_id"] == "deep"
            and event["payload"]["state"] == "failed"
        ]
        fault.check(
            "the required branch node failed and none of the joins ever fired",
            len(failed_deep) == 1
            and states_b.get("join2") == ["skipped"]
            and states_b.get("join") == ["skipped"],
            states_b,
        )
        join2_b = t108.join_firing(dbname, run_b["id"], "join2")
        join_b = t108.join_firing(dbname, run_b["id"], "join")
        fault.check(
            "no join data artifact and no join succeeded event exists for the failed run",
            join2_b["data_rows"] == 0
            and join2_b["succeeded_event_seqs"] == []
            and join_b["data_rows"] == 0
            and join_b["succeeded_event_seqs"] == [],
            {"join2": join2_b, "join": join_b},
        )
        fault.check(
            "the skipped nodes were published as skipped after the failure",
            states_b.get("shallow") == ["skipped"]
            and states_b.get("direct") == ["skipped"]
            and states_b.get("end") == ["skipped"],
            states_b,
        )
        seq_b = t108.event_seq_facts(dbname, run_b["id"])
        fault.check(
            "the failed run's event sequence is contiguous",
            seq_b["contiguous"] and seq_b["first_seq"] == 1,
            seq_b,
        )
        fault.truth("required_failure", {
            "parent_run_id": run_b["id"],
            "state": parent_row_b["state"],
            "error_code": parent_row_b["error_code"],
            "child_run_id": required_child,
            "child_budget": t080.run_budget(dbname, required_child),
            "node_states": states_b,
            "join_firings": {"join2": join2_b, "join": join_b},
            "event_seq": seq_b,
            "children": t078.child_count(dbname, run_b["id"]),
        })
        fault.observe("sub_cases", {
            "nested_success": {
                "parent_run_id": run_a["id"],
                "join2_firings": len(join2["succeeded_event_seqs"]),
                "join_firings": len(outer_join["succeeded_event_seqs"]),
            },
            "required_failure": {
                "parent_run_id": run_b["id"],
                "error_code": parent_row_b["error_code"],
                "join2_firings": join2_b["data_rows"],
                "join_firings": join_b["data_rows"],
            },
        })
        fault.mark_passed()


# ---------------------------------------------------------------------------
# negative boundaries: queue instead of dropping, refuse an unfunded child
# ---------------------------------------------------------------------------
def test_delegation_boundaries_queue_instead_of_dropping_and_refuse_unfunded_children(
    t108_stack,
) -> None:
    stack = t108_stack
    dbname = stack.dbname
    with t108.start(
        "FAULT-07/08-boundaries",
        "Delegation admission boundaries: a full child pool queues (never drops), an "
        "empty parent allowance refuses the child instead of starting it",
        injection_point=(
            "global/child_slots capacity 0 with a real parent waiting on its spawned child; "
            "a real parent Run whose budget_limit is 0.00000000 at spawnChild time"
        ),
        expected=[
            "a spawn under a full child pool is accepted and durably queued, with no attempt",
            "the queued child claims and completes once a slot exists; the parent succeeds",
            "a spawn with no remaining parent allowance is 429 budget_exhausted with no child Run",
        ],
    ) as fault:
        fault.command(GATE_COMMAND)
        _admin, creator = t075.creator(stack)
        child = t078.create_child_version(stack, creator)
        application_id = t081.parent_application(
            stack, creator, t078.parent_definition([child["id"]])
        )

        # (a) Full child pool: spawn succeeds, child stays queued, nothing is dropped.
        t081.set_child_capacity(dbname, 0)
        parent = t081.submit_parent_run(stack, creator, application_id, {"source": "queued"})
        first, _completed = t108.execute_once(stack)
        fault.check(
            "the parent parked waiting on its child although the child pool is full",
            first["run_id"] == parent["id"] and first["outcome"] == "waiting_children",
            first,
        )
        waiting = t081.waiting_row(dbname, parent["id"])
        child_id = waiting["checkpoint_id"].split(":", 1)[1]
        queued_row = t078.run_row(dbname, child_id)
        job = t081.job_rows_for_run(dbname, child_id)
        fault.check(
            "the spawned child is durably queued, not silently dropped",
            queued_row["state"] == "queued"
            and t078.run_children_rows(dbname, parent["id"])[0]["child_run_id"] == child_id
            and [row["state"] for row in job] == ["ready"],
            {"run": queued_row, "jobs": job},
        )
        fault.check(
            "the queued child has no attempt and holds no child slot",
            t078.attempt_rows(dbname, child_id) == []
            and int(t081.child_counter(dbname)["used"]) == 0
            and t081.parent_counter(dbname, parent["id"]) is None,
            {
                "attempts": t078.attempt_rows(dbname, child_id),
                "child_pool": t081.child_counter(dbname),
                "parent_scope": t081.parent_counter(dbname, parent["id"]),
            },
        )
        t081.set_child_capacity(dbname, 1)
        child_report, _completed = t108.execute_once(stack)
        fault.check(
            "the queued child claims and completes once a slot exists",
            child_report["status"] == "executed"
            and child_report["run_id"] == child_id
            and child_report["outcome"] == "succeeded",
            child_report,
        )
        decision = t081.wait_for(
            lambda: t081.waiting_decision(dbname, parent["id"]), timeout=20.0
        )
        fault.check(
            "the parent's wait is resolved and the parent succeeds",
            decision["outcome"] == "succeeded" and decision["child_run_id"] == child_id,
            decision,
        )
        resumed, _completed = t108.execute_once(stack)
        fault.check(
            "the resumed parent succeeded with its child's echo",
            resumed["run_id"] == parent["id"]
            and resumed["outcome"] == "succeeded"
            and t108.artifact_document(
                stack,
                t081.wait_run_state(dbname, parent["id"], {"succeeded"})["output_artifact_id"],
            )
            == {"echo": "queued"},
            resumed,
        )

        # (b) No parent allowance: the child is refused, never started.
        parent_b = t081.submit_parent_run(stack, creator, application_id, {"source": "unfunded"})
        t080.set_parent_budget(dbname, parent_b["id"], "0")
        lease = t108.claim(stack, run_id=parent_b["id"])
        refused = t108.spawn(
            stack,
            parent_b["id"],
            attempt_token=lease["attempt_token"],
            logical_key="call_app:child",
            application_version_id=child["id"],
            input_value={"source": "unfunded"},
        )
        fault.check(
            "a spawn with no remaining allowance is refused with a stable code",
            refused.status == 429
            and refused.json["error"]["code"] == "budget_exhausted",
            {"status": refused.status, "body": refused.json},
        )
        fault.check(
            "the refused spawn created no child Run, receipt or sealed input",
            t078.run_children_rows(dbname, parent_b["id"]) == []
            and t078.child_count(dbname, parent_b["id"]) == 0
            and t080.ledger_rows_at_root(dbname, parent_b["id"]) == [],
            {
                "receipts": t078.run_children_rows(dbname, parent_b["id"]),
                "children": t078.child_count(dbname, parent_b["id"]),
            },
        )
        fault.truth("queue_boundary", {
            "parent_run_id": parent["id"],
            "child_run_id": child_id,
            "child_pool_capacity_at_spawn": 0,
            "queued_state": queued_row["state"],
        })
        fault.truth("allowance_boundary", {
            "parent_run_id": parent_b["id"],
            "refused_status": refused.status,
            "refused_code": refused.json["error"]["code"],
        })
        fault.mark_passed()
