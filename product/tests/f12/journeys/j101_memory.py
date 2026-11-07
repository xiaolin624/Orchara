"""T101/J3: the personal-memory journey.

The candidate is produced by the **real worker identity** over the internal mTLS
route while a real Run holds the lease; it is confirmed by the human through the
contract decision route; and the recall is proven by a real agent Run whose
context trace and sealed model request are read back from the object store.

The journey then changes the current revision and deletes the memory, re-running
the agent each time: the old text must never be recalled again, and the trace
never carries memory text (only identifiers/counts).

The model provider is the documented deterministic ``dev-stub``; the memory store,
the run, the sealed bytes and the context builder are real. The trace says
``included``/``budget`` from the product, and the budget assertion is the
product-reported sub-budget, not a hand-computed constant.
"""

from __future__ import annotations

import json

from support import f00b, t054, t058, t063
from support import stack as st

import t101_support

TEXT = "t101-memory-marker: the user prefers concise weekly summaries."
NEW_TEXT = "t101-memory-revised-marker: the user prefers quarterly totals."
PENDING_TEXT = "t101-memory-pending-marker: an unconfirmed guess."


def _recall(stack, owner, application_id: str, record, label: str):
    """Submit and execute one real agent Run; return (run_id, turn, request_text)."""
    run = t063.submit_run(stack, owner, application_id)
    completed, report = t063.run_agent_once(stack)
    record.command("cmd/supervisor exec --once (agent node, dev-stub model)")
    record.check(
        f"{label}: the real supervisor run succeeds",
        completed.returncode == 0 and report.get("outcome") == "succeeded",
        {"returncode": completed.returncode, "report": report},
    )
    trace = t063.trace_document(owner.creator, stack, run["id"])
    turn = t063.last_turn(trace)
    request_text = t063.messages_text(t063.model_messages(stack, run["id"]))
    return run["id"], turn, request_text, trace


def run(stack, record) -> dict:
    record.command("python -m pytest tests/f12/test_t101_journeys.py -k memory")
    admin = f00b.admin_session(stack)
    owner = t101_support.golden_owner(stack)
    application_id = t063.application_for(owner)
    record.command(
        "POST /api/v1/applications + PUT /api/v1/applications/{id}/draft (agent definition)"
    )
    record.truth_source(
        "platform.memory_candidates / platform.memories / sealed body artifacts via psql + object dir"
    )
    record.truth_source("runs/{id}/node-results context trace + sealed model request artifact")

    # -- 1. real worker proposes a candidate while a real Run holds the lease -
    proposal_run = t063.submit_run(stack, owner, application_id)
    lease = t063.claim_run(stack, proposal_run)
    candidate = t063.propose_candidate(
        stack, lease, text=TEXT, logical_key="agent:memory-node:memory:1"
    )
    record.command("POST /internal/v1/runs/{id}/memory-candidates (mTLS worker)")
    row = t054.candidate_row(stack.dbname, candidate["id"])
    record.check(
        "the worker's candidate is pending and sealed with the exact bytes",
        candidate.get("state") == "pending"
        and row["state"] == "pending"
        and t054.candidate_text(stack, row) == TEXT,
        {"candidate": candidate, "row": row},
    )
    record.check(
        "the candidate is scoped to the run's application and owner",
        row["application_id"] == application_id
        and row["owner_id"] == owner.creator.principal_id,
        row,
    )
    pending = t063.propose_candidate(
        stack, lease, text=PENDING_TEXT, logical_key="agent:memory-node:memory:pending"
    )
    record.check(
        "a pending candidate is not yet a memory",
        t054.memory_rows(stack.dbname, owner_id=owner.creator.principal_id) == [],
    )

    # -- 2. only the human sees and decides it; service keys are refused -----
    listed = t054.list_candidates(owner.creator)
    record.http("GET", "/api/v1/memory-candidates", listed)
    record.check(
        "the owner's queue contains both candidates",
        listed.status == 200
        and {item["id"] for item in listed.json["items"]} == {candidate["id"], pending["id"]},
        listed.json,
    )
    stranger = f00b.invite_and_activate(stack, admin, "t101.memory-stranger", roles=("creator",))
    foreign_list = t054.list_candidates(stranger)
    record.check(
        "another human sees an empty queue (owner-scoped, not a global one)",
        foreign_list.status == 200 and foreign_list.json["items"] == [],
        foreign_list.json,
    )
    bearer = t054.service_key_client(stack, admin, display_name="t101-memory-svc")
    service_list = t054.list_candidates(bearer)
    record.http("GET", "/api/v1/memory-candidates (service key)", service_list)
    st.assert_error(service_list, 403, t054.PERSONAL_SCOPE_FORBIDDEN)
    record.check(
        "a service credential can never touch personal memory",
        service_list.status == 403, service_list.text,
    )
    record.check(
        "the personal-data refusal is the only thing the key hit; the candidate queue is unchanged",
        [item["id"] for item in t054.candidate_rows(stack.dbname)] == [candidate["id"], pending["id"]],
    )
    foreign_decision = t054.decide(stranger, candidate["id"])
    record.http("POST", "/api/v1/memory-candidates/{id}/decision (stranger)", foreign_decision)
    record.check(
        "a stranger's decision is the same not-found shape as a missing candidate",
        foreign_decision.status == 404, foreign_decision.text,
    )

    # -- 3. the human confirms: exactly one memory --------------------------
    memory_id = t063.confirm_candidate(owner.creator, candidate["id"])
    record.http("POST", "/api/v1/memory-candidates/{id}/decision (confirm)", None)
    memory = t054.memory_row(stack.dbname, memory_id)
    record.check(
        "the confirmed candidate becomes exactly one memory with the sealed bytes",
        memory is not None
        and memory["deleted_at"] is None
        and memory["candidate_id"] == candidate["id"]
        and memory["application_id"] == application_id
        and t054.memory_text(stack, memory) == TEXT
        and t054.candidate_row(stack.dbname, candidate["id"])["state"] == "confirmed",
        memory,
    )
    record.check(
        "the pending candidate stayed pending and created no memory",
        t054.candidate_row(stack.dbname, pending["id"])["state"] == "pending"
        and len(t054.memory_rows(stack.dbname, owner_id=owner.creator.principal_id)) == 1,
    )
    replay = t054.decide(owner.creator, candidate["id"], decision="reject")
    record.check(
        "a second, opposite decision on a confirmed candidate is refused",
        replay.status == 409
        and replay.json["error"]["code"] == t054.CANDIDATE_CONFIRMED,
        replay.text,
    )

    expired = t063.propose_candidate(
        stack, lease, text="t101-memory-expired-marker", logical_key="agent:memory-node:memory:expired"
    )
    t054.set_expiry(stack.dbname, expired["id"], delta_seconds=-60)
    expired_decision = t054.decide(owner.creator, expired["id"])
    record.check(
        "an expired candidate cannot produce a memory",
        expired_decision.status == 409
        and expired_decision.json["error"]["code"] == t054.CANDIDATE_EXPIRED,
        expired_decision.text,
    )
    record.command(
        "UPDATE platform.memory_candidates SET expires_at = now() - interval '60 seconds' "
        "(time's effect; the decision path re-checks it)"
    )

    # -- 4. recall: the confirmed memory is in the context, the pending one is not
    run1, turn1, text1, trace1 = _recall(stack, owner, application_id, record, "recall after confirm")
    sources1 = t063.memory_sources(turn1)
    ids1 = {source["memory_id"] for source in sources1}
    record.check(
        "the confirmed memory is recalled with memory_state=included",
        turn1.get("memory_state") == "included"
        and ids1 == {memory_id}
        and PENDING_TEXT.split(":")[0] not in text1,
        {"turn": turn1, "sources": sources1},
    )
    record.check(
        "the recall respects the product-reported context budget",
        0 < int(turn1["memory_budget_tokens"])
        and 0 < int(turn1["memory_tokens"]) <= int(turn1["memory_budget_tokens"]),
        {
            "budget": turn1.get("memory_budget_tokens"),
            "tokens": turn1.get("memory_tokens"),
        },
    )
    record.check(
        "the sealed model request really carried the confirmed memory text",
        TEXT in text1,
        text1[:400],
    )
    record.check(
        "the pending and expired candidates are not recall sources",
        PENDING_TEXT not in text1 and "t101-memory-expired-marker" not in text1,
    )
    t063.assert_no_content_leak(trace1, [TEXT, NEW_TEXT, PENDING_TEXT])
    record.check(
        "the trace never carries memory text, only identifiers/counts",
        "t101-memory" not in t063.trace_text(trace1),
    )
    record.check(
        "the stranger cannot read the owner's run trace",
        st.assert_error(
            stranger.client.get(f"/api/v1/runs/{run1}/node-results"), 404, "resource_not_found"
        )
        is not None,
    )

    # -- 5. revision: the current text is recalled, the old one is not ------
    detail = t058.detail(owner.creator, memory_id)
    etag = t058.etag_of(detail)
    record.http("GET", f"/api/v1/memories/{memory_id}", detail)
    updated = t058.update(owner.creator, memory_id, text=NEW_TEXT, if_match=etag)
    record.http("PATCH", f"/api/v1/memories/{memory_id}", updated)
    record.check(
        "the memory is revised to revision 2",
        updated.status == 200 and updated.json["revision"] == 2,
        updated.text,
    )
    stale = t058.update(owner.creator, memory_id, text="t101-stale-marker", if_match=etag)
    record.check(
        "an old If-Match cannot overwrite the newer revision",
        stale.status == 412 and stale.json["error"]["code"] == t058.PRECONDITION_FAILED,
        stale.text,
    )
    _run2, turn2, text2, trace2 = _recall(stack, owner, application_id, record, "recall after revision")
    ids2 = {source["memory_id"] for source in t063.memory_sources(turn2)}
    record.check(
        "the revised text is recalled and the superseded text is not",
        ids2 == {memory_id} and NEW_TEXT in text2 and TEXT not in text2,
        {"sources": ids2},
    )
    t063.assert_no_content_leak(trace2, [TEXT, NEW_TEXT, PENDING_TEXT])
    record.check(
        "the recalled revision count matches the durable memory revision",
        next(s for s in t063.memory_sources(turn2) if s["memory_id"] == memory_id)[
            "memory_revision"
        ]
        == t054.memory_row(stack.dbname, memory_id)["revision"],
    )

    # -- 6. delete: recall stops immediately -------------------------------
    deletion = t058.delete(owner.creator, memory_id)
    record.http("DELETE", f"/api/v1/memories/{memory_id}", deletion)
    record.check(
        "the deletion is accepted as a real background job",
        deletion.status == 202 and deletion.json["resource_id"] == memory_id,
        deletion.text,
    )
    _run3, turn3, text3, trace3 = _recall(stack, owner, application_id, record, "recall after delete")
    ids3 = {source["memory_id"] for source in t063.memory_sources(turn3)}
    record.check(
        "the deleted memory is no longer recalled",
        memory_id not in ids3 and NEW_TEXT not in text3 and TEXT not in text3,
        {"sources": ids3, "text_prefix": text3[:200]},
    )
    memories_after = t054.list_memories(owner.creator)
    record.http("GET", "/api/v1/memories (after delete)", memories_after)
    record.check(
        "the deleted memory is gone from the read model",
        memories_after.status == 200
        and [item["id"] for item in memories_after.json["items"]] == [],
        memories_after.json,
    )
    record.note(
        "recall",
        {
            "run_after_confirm": run1,
            "confirm_sources": sorted(ids1),
            "budget": turn1.get("memory_budget_tokens"),
            "tokens": turn1.get("memory_tokens"),
            "after_revision_sources": sorted(ids2),
            "after_delete_sources": sorted(ids3),
        },
    )
    return {
        "candidate_id": candidate["id"],
        "memory_id": memory_id,
        "recall_run_id": run1,
        "after_delete_sources": sorted(ids3),
    }
