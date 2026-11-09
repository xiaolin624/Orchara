"""F09/T063: the agent recalls the current application's confirmed memories
inside the deterministic context budget.

Real stack throughout: PostgreSQL 16, the compiled ``cmd/api``/``cmd/supervisor``
binaries, the real mTLS internal listener, real sealed object bytes, and one real
debug run driven through the existing claim/result/exit protocol. The memory rows
are written through the real ``POST /api/v1/memories`` / candidate-confirm paths
and the exact model request is read back from the sealed T041 ledger artifact; the
trace is read through ``GET /api/v1/runs/{run_id}/node-results`` and the ordinary
owner-only artifact content route.

The model provider is the documented deterministic dev stub; the tool execution,
the memory store and the context are real.
"""

from __future__ import annotations

from support import run_observations as observations
from support import stack as st
from support import t041, t054, t058, t063


def test_the_agent_recalls_only_this_users_this_applications_current_confirmed_memories(
    t054_stack,
) -> None:
    stack = t054_stack
    run_owner = t063.owner(stack)
    application_id = t063.application_for(run_owner)

    # Four included memories, each written through a real contract path.
    first = t063.create_memory(
        run_owner.creator, application_id,
        "The user prefers concise weekly summaries. marker-recall-a",
    )
    second = t063.create_memory(
        run_owner.creator, application_id,
        "The user works in the Asia/Shanghai timezone. marker-recall-b",
    )
    # Current revision only: the first revision text must never reach the model.
    edited = t063.create_memory(
        run_owner.creator, application_id,
        "revision-old-marker: the user prefers monthly totals.",
    )
    etag = t058.etag_of(t058.detail(run_owner.creator, edited["id"]))
    updated = t058.update(
        run_owner.creator, edited["id"],
        text="revision-new-marker: the user prefers quarterly totals.",
        if_match=etag,
    )
    assert updated.status == 200, updated.text
    assert updated.json["revision"] == 2, updated.json

    # Deleted content is tombstoned and must stop being recalled immediately.
    deleted = t063.create_memory(
        run_owner.creator, application_id,
        "deleted-memory-marker: the user prefers paper invoices.",
    )
    deletion = t058.delete(run_owner.creator, deleted["id"])
    assert deletion.status == 202, deletion.text

    # A candidate confirmed from a real run is a memory with candidate/run
    # provenance; a pending candidate and an expired candidate are never recall
    # sources.
    reader_run = t063.submit_run(stack, run_owner, application_id)
    reader_lease = t063.claim_run(stack, reader_run)
    confirmed_candidate = t063.propose_candidate(
        stack, reader_lease,
        text="candidate-confirmed-marker: the user reviews orders by region.",
        logical_key="agent:memory:confirmed",
    )
    candidate_memory_id = t063.confirm_candidate(
        run_owner.creator, confirmed_candidate["id"]
    )
    pending_candidate = t063.propose_candidate(
        stack, reader_lease,
        text="pending-candidate-marker: an unconfirmed guess.",
        logical_key="agent:memory:pending",
    )
    expired_candidate = t063.propose_candidate(
        stack, reader_lease,
        text="expired-candidate-marker: an expired guess.",
        logical_key="agent:memory:expired",
    )
    t054.set_state(stack.dbname, expired_candidate["id"], "expired")

    # Isolation: another application of the same user and another user's memory.
    other_application = t063.other_application(run_owner.creator)
    t063.create_memory(
        run_owner.creator, other_application,
        "other-application-marker: belongs to a different application.",
    )
    other_owner = t041.secondary_owner(stack)
    t063.create_memory(
        other_owner.creator, other_owner.application_id,
        "other-user-marker: belongs to a different human.",
    )

    # The real run, claimed and executed by the supervisor after all rows exist.
    run = t063.submit_run(stack, run_owner, application_id)
    completed, report = t063.run_agent_once(stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "succeeded", report
    state = observations.wait_run_state(stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state

    trace = t063.trace_document(run_owner.creator, stack, run["id"])
    # Current authorization: another human cannot read this run's trace at all.
    refused_trace = other_owner.creator.client.get(
        f"/api/v1/runs/{run['id']}/node-results"
    )
    st.assert_error(refused_trace, 404, "resource_not_found")
    turn = t063.last_turn(trace)
    assert turn["memory_state"] == "included", turn
    assert turn["memory_budget_tokens"] > 0, turn
    assert turn["memory_tokens"] > 0, turn
    assert t063.optional_memory_truncations(turn) == [], turn

    sources = t063.memory_sources(turn)
    included_ids = {source["memory_id"] for source in sources}
    expected_ids = {
        first["id"], second["id"], candidate_memory_id, edited["id"],
    }
    assert included_ids == expected_ids, (included_ids, expected_ids)
    assert deleted["id"] not in included_ids
    assert pending_candidate["id"] not in included_ids
    assert expired_candidate["id"] not in included_ids

    # Every source explains itself: current revision, confirmation instant and
    # (for a candidate-confirmed memory) the memory's provenance chain.
    for source in sources:
        row = t063.memory_row(stack.dbname, source["memory_id"])
        assert source["memory_revision"] == row["revision"], (source, row)
        assert t054.same_instant(
            t054.parse_time(source["memory_confirmed_at"]),
            t054.parse_time(row["confirmed_at"]),
        ), (source, row)
    by_id = {source["memory_id"]: source for source in sources}
    candidate_source = by_id[candidate_memory_id]
    assert candidate_source["memory_candidate_id"] == confirmed_candidate["id"]
    assert candidate_source["memory_source_run_id"] == reader_run["id"]
    assert "memory_source_run_id" not in by_id[first["id"]]

    # The trace never carries memory text, only identifiers/counts.
    t063.assert_no_content_leak(
        trace,
        [
            "marker-recall-a", "marker-recall-b", "revision-old-marker",
            "revision-new-marker", "candidate-confirmed-marker",
            "deleted-memory-marker", "pending-candidate-marker",
            "expired-candidate-marker", "other-application-marker",
            "other-user-marker",
        ],
    )

    # The exact model request carries only the four included memories.
    messages = t063.model_messages(stack, run["id"])
    request_text = t063.messages_text(messages)
    memory_document = t063.memory_message(messages)
    assert memory_document is not None, request_text
    memory_ids = {item["id"] for item in memory_document["confirmed_memory"]}
    assert memory_ids == expected_ids, memory_document
    for marker in (
        "marker-recall-a", "marker-recall-b", "revision-new-marker",
        "candidate-confirmed-marker",
    ):
        assert marker in request_text, (marker, request_text)
    for marker in (
        "revision-old-marker", "deleted-memory-marker",
        "pending-candidate-marker", "expired-candidate-marker",
        "other-application-marker", "other-user-marker",
    ):
        assert marker not in request_text, (marker, request_text)

    # The sealed object bytes of every recalled memory are the real current body.
    for memory_id in (first["id"], second["id"], candidate_memory_id, edited["id"]):
        row = t063.memory_row(stack.dbname, memory_id)
        assert t054.memory_text(stack, row) in request_text


def test_over_budget_recall_truncates_lowest_relevance_memories_and_keeps_negations(
    t054_stack,
) -> None:
    stack = t054_stack
    run_owner = t063.owner(stack)
    application_id = t063.application_for(run_owner)

    # The oldest memory is a user negation: it must survive the memory budget even
    # though every newer memory outranks it by recency.
    negation = t063.create_memory(
        run_owner.creator, application_id,
        "negation-marker: the user must not receive marketing email.",
    )
    bulk = []
    for index in range(6):
        memory = t063.create_memory(
            run_owner.creator, application_id,
            f"bulk-marker-{index}: " + "value " * 200,
        )
        bulk.append(memory)

    # 3072 - 1024 = a 2048-token context budget and a 512-token memory sub-budget.
    t041.set_model_config(
        stack.dbname, {"context_window": 3072, "max_output_tokens": 1024}
    )

    run = t063.submit_run(stack, run_owner, application_id)
    completed, report = t063.run_agent_once(stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "succeeded", report

    trace = t063.trace_document(run_owner.creator, stack, run["id"])
    turn = t063.last_turn(trace)
    assert turn["memory_budget_tokens"] == 512, turn
    assert turn["memory_state"] == "included", turn
    assert 0 < turn["memory_tokens"] <= 512, turn

    sources = t063.memory_sources(turn)
    included_ids = [source["memory_id"] for source in sources]
    assert negation["id"] in included_ids, included_ids
    assert len(included_ids) == 2, included_ids  # the negation plus one bulk row
    negation_source = next(
        source for source in sources if source["memory_id"] == negation["id"]
    )
    assert negation_source["protected"] is True, negation_source

    truncated = t063.optional_memory_truncations(turn)
    assert len(truncated) == 5, truncated
    for entry in truncated:
        assert entry["memory_id"] in {memory["id"] for memory in bulk}, entry
        assert entry["memory_revision"] >= 1, entry
        assert entry["tokens_removed"] > 0, entry
    # Lowest relevance first: the oldest bulk row is the first truncation.
    assert truncated[0]["memory_id"] == bulk[0]["id"], truncated

    messages = t063.model_messages(stack, run["id"])
    request_text = t063.messages_text(messages)
    assert "negation-marker" in request_text, request_text
    for source in sources:
        if source["memory_id"] == negation["id"]:
            continue
        row = t063.memory_row(stack.dbname, source["memory_id"])
        assert t054.memory_text(stack, row) in request_text
    dropped_markers = {
        entry["memory_id"]: f"bulk-marker-{bulk.index(memory)}"
        for entry in truncated
        for memory in bulk
        if memory["id"] == entry["memory_id"]
    }
    for marker in dropped_markers.values():
        assert marker not in request_text, (marker, request_text)


def test_protection_scan_finds_a_negation_beyond_the_recency_cap_and_the_budget_keeps_it(
    t054_stack,
) -> None:
    """The oldest memory is a user negation outside the newest-200 recency page.

    A purely recency-bounded read would drop it before the builder's protection
    detection ever runs, and the model would act as if the user never stated the
    constraint. Recall must scan older rows of the same owner+application scope
    for protection markers, include the row, and say that it looked beyond the
    cap. The 512-token memory sub-budget makes "it survives" a real budget fact:
    newer rows are dropped while the older negation stays.
    """
    stack = t054_stack
    run_owner = t063.owner(stack)
    application_id = t063.application_for(run_owner)
    owner_id = run_owner.creator.principal_id

    negation = t063.create_memory(
        run_owner.creator, application_id,
        "beyond-cap-negation-marker: 禁止外发客户名单。",
    )
    t063.seed_memories(
        run_owner.creator, application_id,
        prefix="beyond-cap-bulk", count=t063.RECALL_ROW_LIMIT,
    )
    recency_page = t063.newest_memory_ids(
        stack.dbname, owner_id, application_id, t063.RECALL_ROW_LIMIT
    )
    assert len(recency_page) == t063.RECALL_ROW_LIMIT, len(recency_page)
    assert negation["id"] not in recency_page, "setup: the negation is inside the cap"

    # The older scan is exactly one owner+application scope: another application
    # of the same user, and another user's application, stay invisible even when
    # their rows carry the same protection marker.
    other_application = t063.other_application(run_owner.creator)
    other_app_negation = t063.create_memory(
        run_owner.creator, other_application,
        "other-application-negation-marker: 禁止外发客户名单。",
    )
    other_owner = t041.secondary_owner(stack)
    other_user_negation = t063.create_memory(
        other_owner.creator, other_owner.application_id,
        "other-user-negation-marker: 禁止外发客户名单。",
    )

    # 3072 - 1024 = a 2048-token context budget and a 512-token memory sub-budget.
    t041.set_model_config(
        stack.dbname, {"context_window": 3072, "max_output_tokens": 1024}
    )

    run = t063.submit_run(stack, run_owner, application_id)
    completed, report = t063.run_agent_once(stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "succeeded", report

    trace = t063.trace_document(run_owner.creator, stack, run["id"])
    turn = t063.last_turn(trace)
    assert turn["memory_state"] == "included", turn
    assert turn["memory_budget_tokens"] == 512, turn
    assert 0 < turn["memory_tokens"] <= 512, turn

    # The trace proves the source looked past the recency cap: one older row was
    # scanned, it was protected, and the scope was exhausted (no truncation).
    assert t063.memory_scan(turn) == {
        "scanned": 1,
        "limit": t063.RECALL_SCAN_LIMIT,
        "protected_found": 1,
        "truncated": False,
    }, turn
    assert t063.scan_truncations(turn) == [], turn

    sources = t063.memory_sources(turn)
    negation_source = next(
        (source for source in sources if source["memory_id"] == negation["id"]), None
    )
    assert negation_source is not None, sources
    assert negation_source["protected"] is True, negation_source
    row = t063.memory_row(stack.dbname, negation["id"])
    assert negation_source["memory_revision"] == row["revision"], (negation_source, row)
    assert t054.same_instant(
        t054.parse_time(negation_source["memory_confirmed_at"]),
        t054.parse_time(row["confirmed_at"]),
    ), (negation_source, row)
    # The sub-budget dropped newer rows rather than the older protected negation.
    truncated = t063.optional_memory_truncations(turn)
    assert truncated, turn
    assert negation["id"] not in {entry["memory_id"] for entry in truncated}, truncated

    source_ids = {source["memory_id"] for source in sources}
    assert other_app_negation["id"] not in source_ids, source_ids
    assert other_user_negation["id"] not in source_ids, source_ids

    request_text = t063.messages_text(t063.model_messages(stack, run["id"]))
    assert "beyond-cap-negation-marker" in request_text, request_text
    assert "禁止外发客户名单" in request_text, request_text
    assert "other-application-negation-marker" not in request_text, request_text
    assert "other-user-negation-marker" not in request_text, request_text
    t063.assert_no_content_leak(trace, ["beyond-cap-bulk-", "禁止外发客户名单"])


def test_protection_scan_stops_at_its_hard_bound_and_the_trace_says_so(t054_stack) -> None:
    """Beyond the hard scan bound, completeness is impossible — silence is not.

    One more older row than the scan may examine is a user negation. The scan
    must stop at its documented bound, the trace must record
    ``memory_scan_truncated`` with the scanned/limit counts, and the protected row
    must not be presented as if it had been considered.
    """
    stack = t054_stack
    run_owner = t063.owner(stack)
    application_id = t063.application_for(run_owner)
    owner_id = run_owner.creator.principal_id

    negation = t063.create_memory(
        run_owner.creator, application_id,
        "beyond-bound-negation-marker: 禁止外发客户名单。",
    )
    t063.seed_memories(
        run_owner.creator, application_id,
        prefix="beyond-bound-bulk", count=t063.RECALL_ROW_LIMIT + t063.RECALL_SCAN_LIMIT,
    )
    recency_page = t063.newest_memory_ids(
        stack.dbname, owner_id, application_id, t063.RECALL_ROW_LIMIT
    )
    assert negation["id"] not in recency_page, "setup: the negation is inside the cap"

    run = t063.submit_run(stack, run_owner, application_id)
    completed, report = t063.run_agent_once(stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "succeeded", report

    trace = t063.trace_document(run_owner.creator, stack, run["id"])
    turn = t063.last_turn(trace)
    assert turn["memory_state"] == "included", turn
    assert t063.memory_scan(turn) == {
        "scanned": t063.RECALL_SCAN_LIMIT,
        "limit": t063.RECALL_SCAN_LIMIT,
        "protected_found": 0,
        "truncated": True,
    }, turn
    entries = t063.scan_truncations(turn)
    assert len(entries) == 1, entries
    entry = entries[0]
    assert entry["source"] == "memory_scan", entry
    assert entry["scanned"] == t063.RECALL_SCAN_LIMIT, entry
    assert entry["scan_limit"] == t063.RECALL_SCAN_LIMIT, entry
    assert entry["tokens_removed"] == 0, entry

    # The bound is honest about what it could not see: the protected row beyond it
    # is absent from the trace and the model input, and the trace records exactly
    # that incompleteness instead of passing the view off as complete.
    source_ids = {source["memory_id"] for source in t063.memory_sources(turn)}
    assert negation["id"] not in source_ids, source_ids
    request_text = t063.messages_text(t063.model_messages(stack, run["id"]))
    assert "beyond-bound-negation-marker" not in request_text, request_text


def test_missing_memory_bytes_fail_closed_and_never_inject_text(t054_stack) -> None:
    stack = t054_stack
    run_owner = t063.owner(stack)
    application_id = t063.application_for(run_owner)
    memory = t063.create_memory(
        run_owner.creator, application_id,
        "unreadable-memory-marker: the body bytes are about to disappear.",
    )
    row = t063.memory_row(stack.dbname, memory["id"])
    artifact = t054.artifact_row(stack.dbname, row["body_artifact_id"])
    object_path = t054.object_path(stack, artifact["object_key"])
    payload = object_path.read_bytes()
    t054.remove_object(stack, artifact["object_key"])

    run = t063.submit_run(stack, run_owner, application_id)
    completed, report = t063.run_agent_once(stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "succeeded", report

    trace = t063.trace_document(run_owner.creator, stack, run["id"])
    turn = t063.last_turn(trace)
    assert turn["memory_state"] == "unavailable", turn
    assert turn["memory_tokens"] == 0, turn
    assert t063.memory_sources(turn) == [], turn
    request_text = t063.messages_text(t063.model_messages(stack, run["id"]))
    assert "unreadable-memory-marker" not in request_text
    assert '"confirmed_memory"' not in request_text

    # Recovery: restore the exact sealed bytes and retry the same run input. The
    # recall is uncached, so the next run reads the same authoritative row again
    # instead of replaying the previous unavailable decision.
    object_path.write_bytes(payload)
    recovered_run = t063.submit_run(stack, run_owner, application_id)
    completed, report = t063.run_agent_once(stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "succeeded", report
    recovered_trace = t063.trace_document(
        run_owner.creator, stack, recovered_run["id"]
    )
    recovered_turn = t063.last_turn(recovered_trace)
    assert recovered_turn["memory_state"] == "included", recovered_turn
    recovered_text = t063.messages_text(
        t063.model_messages(stack, recovered_run["id"])
    )
    assert "unreadable-memory-marker" in recovered_text
    assert t063.memory_sources(recovered_turn)[0]["memory_id"] == memory["id"]


def test_the_memory_producer_identity_is_verified_not_assumed(t054_stack) -> None:
    """T054's known concern: the propose endpoint is worker-only.

    The F04A executor runs as ``cmd/supervisor exec`` but holds *both* the
    supervisor and the worker mTLS capacities and already presents the worker role
    on every capability call and on the node-results report. This test records the
    exact refusal of the supervisor capacity and the worker capacity's acceptance,
    so a producer wired into the agent loop has a proven transport identity.
    """
    stack = t054_stack
    run_owner = t063.owner(stack)
    application_id = t063.application_for(run_owner)
    run = t063.submit_run(stack, run_owner, application_id)
    lease = t063.claim_run(stack, run)

    refused = t054.propose(
        stack, lease, text="supervisor-producer-marker", logical_key="memory:1",
        role="supervisor",
    )
    st.assert_error(refused, 403, "forbidden")
    maintenance = t054.propose(
        stack, lease, text="maintenance-producer-marker", logical_key="memory:2",
        role="maintenance",
    )
    st.assert_error(maintenance, 403, "forbidden")

    accepted = t054.propose(
        stack, lease, text="worker-producer-marker", logical_key="memory:3",
        role="worker",
    )
    assert accepted.status == 201, accepted.text
    candidate = accepted.json
    scope = t054.candidate_application_of(stack, lease)
    assert scope == application_id, (scope, application_id)
    stored = t054.candidate_row(stack.dbname, candidate["id"])
    assert stored["application_id"] == application_id, stored
    assert stored["state"] == "pending", stored
    assert t054.candidate_text(stack, stored) == "worker-producer-marker"

