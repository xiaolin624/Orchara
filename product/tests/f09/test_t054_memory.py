"""F09/T054 acceptance: a candidate is only a proposal until the user confirms it.

Every test drives the real stack: PostgreSQL migrated through 0033, the compiled
API, real mTLS (the worker certificate and the run's live attempt token), the real
supervisor claim protocol, real sealed object bytes on disk and real cookie+CSRF
decisions. Candidate and memory rows are read from PostgreSQL; the text is read
from the sealed object, never from the API echo.

``not_run``: a real browser DOM (the page helpers execute under Node in
``test_t054_pages.py``) and a background expiry sweeper (expiry is enforced by the
server on every decision and on the list projection, which is the contract's only
decision entry).
"""

from __future__ import annotations

import datetime
import threading
import uuid

import pytest

from support import f00b, t041, t054
from support import stack as st

TEXT = "The user prefers concise weekly summaries in Chinese."
LOGICAL_KEY = "agent:memory-node:memory:1"


def propose_ok(stack, lease, **kwargs):
    response = t054.propose(stack, lease, **kwargs)
    assert response.status == 201, response.text
    return response.json


# ---------------------------------------------------------------------------
# proposeMemoryCandidate: real worker, real run, sealed bytes
# ---------------------------------------------------------------------------
def test_a_worker_proposal_is_a_candidate_with_sealed_bytes_and_run_scope(
    t054_stack,
) -> None:
    owner, run, lease = t054.owned_run(t054_stack)
    body = propose_ok(t054_stack, lease, text=TEXT, logical_key=LOGICAL_KEY)

    # Exactly the contract's MemoryCandidate schema.
    assert set(body) == {"id", "application_id", "text", "state", "expires_at"}, body
    assert uuid.UUID(body["id"])
    assert body["text"] == TEXT
    assert body["state"] == "pending"

    candidate = t054.candidate_row(t054_stack.dbname, body["id"])
    assert candidate is not None
    assert candidate["owner_id"] == owner.creator.principal_id
    assert candidate["source_run_id"] == run["id"]
    assert candidate["logical_key"] == LOGICAL_KEY
    assert candidate["state"] == "pending"
    # The application scope is derived from the run's frozen snapshot, never from
    # the worker body (the contract body has no application_id at all).
    snapshot_application = t054.candidate_application_of(t054_stack, lease)
    assert candidate["application_id"] == snapshot_application
    assert body["application_id"] == snapshot_application

    # The body is a real sealed object, owned by the candidate owner.
    artifact = t054.artifact_row(t054_stack.dbname, candidate["body_artifact_id"])
    assert artifact["state"] == "ready", artifact
    assert artifact["purpose"] == "memory_candidate", artifact
    assert artifact["media_type"].startswith("text/plain"), artifact
    assert artifact["owner_id"] == owner.creator.principal_id, artifact
    assert artifact["sha256"] == st.sha256_hex(TEXT), artifact
    assert artifact["size_bytes"] == len(TEXT.encode("utf-8")), artifact
    assert t054.artifact_bytes(t054_stack, artifact).decode("utf-8") == TEXT

    # The deadline is a real server fact in the future, not a client value.
    expires = t054.parse_time(candidate["expires_at"])
    assert t054.same_instant(t054.parse_time(body["expires_at"]), expires), (body, candidate)
    assert expires > t054.now_utc()
    assert expires <= t054.now_utc() + t054.CANDIDATE_TTL + datetime.timedelta(hours=1)
    assert expires >= t054.now_utc() + t054.CANDIDATE_TTL - datetime.timedelta(hours=1)

    # A candidate is not a memory yet.
    assert t054.memory_rows(t054_stack.dbname) == []


def test_a_retried_proposal_returns_the_same_candidate(t054_stack) -> None:
    _owner, _run, lease = t054.owned_run(t054_stack)
    first = propose_ok(t054_stack, lease, text=TEXT, logical_key=LOGICAL_KEY)
    artifacts_before = t054.memory_artifact_count(t054_stack.dbname)

    again = propose_ok(t054_stack, lease, text=TEXT, logical_key=LOGICAL_KEY)
    assert again["id"] == first["id"], (first, again)
    assert len(t054.candidate_rows(t054_stack.dbname)) == 1
    assert t054.memory_artifact_count(t054_stack.dbname) == artifacts_before

    # The same logical key with different text is a conflict, not a second row.
    conflict = t054.propose(t054_stack, lease, text="A different fact.", logical_key=LOGICAL_KEY)
    error = st.assert_error(conflict, 409, t054.IDEMPOTENCY_CONFLICT)
    assert error["details"]["field"] == "logical_key", error
    assert len(t054.candidate_rows(t054_stack.dbname)) == 1


def test_the_internal_capacity_is_worker_only_and_the_attempt_is_proved(t054_stack) -> None:
    _owner, _run, lease = t054.owned_run(t054_stack)

    # The contract capacity is worker: the worker certificate and the run's live
    # attempt token create the candidate.
    assert t054.propose(t054_stack, lease, logical_key="kv:worker").status == 201

    # A supervisor or maintenance certificate authenticates but may not propose.
    for role in ("supervisor", "maintenance"):
        refused = t054.propose(t054_stack, lease, role=role, logical_key="kv:" + role)
        st.assert_error(refused, 403, "forbidden")

    # A well-formed attempt token that is not this run's live lease is refused.
    stale = t054.propose(t054_stack, lease, attempt_token=str(uuid.uuid4()), logical_key="kv:stale")
    st.assert_error(stale, 403, "stale_attempt")
    assert len(t054.candidate_rows(t054_stack.dbname)) == 1

    # A run that is no longer running cannot mint another candidate.
    t054.set_run_state(t054_stack.dbname, lease["run_id"], "succeeded")
    stopped = t054.propose(t054_stack, lease, logical_key="kv:stopped")
    st.assert_error(stopped, 409, "run_not_running")
    assert len(t054.candidate_rows(t054_stack.dbname)) == 1

# ---------------------------------------------------------------------------
# listMemoryCandidates: owner-only, real body, server-enforced state
# ---------------------------------------------------------------------------
def test_the_candidate_list_is_owner_only_and_shows_the_real_body(t054_stack) -> None:
    owner, _run, lease = t054.owned_run(t054_stack)
    first = propose_ok(t054_stack, lease, text=TEXT, logical_key="agent:memory-node:memory:1")
    second = propose_ok(t054_stack, lease, text="The user works in Beijing.", logical_key="agent:memory-node:memory:2")

    session = owner.creator
    response = t054.list_candidates(session)
    assert response.status == 200, response.text
    body = response.json
    assert set(body) == {"items", "next_cursor"}, body
    assert len(body["items"]) == 2, body
    texts = {item["id"]: item["text"] for item in body["items"]}
    assert texts == {first["id"]: TEXT, second["id"]: "The user works in Beijing."}, texts
    for item in body["items"]:
        assert set(item) == {"id", "application_id", "text", "state", "expires_at"}, item
        assert item["state"] == "pending", item
        assert item["application_id"] == first["application_id"], item

    # The same list across two pages proves the keyset cursor is real.
    page = t054.list_candidates(session, limit=1)
    assert page.status == 200, page.text
    assert len(page.json["items"]) == 1, page.json
    assert page.json["next_cursor"], page.json
    follow = t054.list_candidates(session, limit=1, cursor=page.json["next_cursor"])
    assert follow.status == 200, follow.text
    assert len(follow.json["items"]) == 1, follow.json
    assert follow.json["items"][0]["id"] != page.json["items"][0]["id"], follow.json
    assert follow.json["next_cursor"] is None, follow.json

    # The application scope filter is real: another application shows nothing.
    assert t054.list_candidates(session, application_id=first["application_id"]).json["items"], \
        "the scope filter dropped the owner's own candidate"
    scoped = t054.list_candidates(session, application_id=str(uuid.uuid4()))
    assert scoped.status == 200, scoped.text
    assert scoped.json == {"items": [], "next_cursor": None}, scoped.json

    # The contract's validation boundaries.
    for query in ("?limit=0", "?limit=101", "?limit=abc", "?limit=", "?limit=-1"):
        error = st.assert_error(session.client.get("/api/v1/memory-candidates" + query),
                                422, "validation_failed")
        assert error["details"]["field"] == "limit", (query, error)
    invalid_scope = t054.list_candidates(session, application_id="not-a-uuid")
    error = st.assert_error(invalid_scope, 422, "validation_failed")
    assert error["details"]["field"] == "application_id", error
    bad_cursor = t054.list_candidates(session, cursor="not-a-cursor")
    st.assert_error(bad_cursor, 400, "invalid_cursor")

    # Another user and an administrator see only their own queue: no candidate
    # exists for them, so the list is empty rather than someone else's rows.
    stranger = t041.secondary_owner(t054_stack).creator
    assert t054.list_candidates(stranger).json == {"items": [], "next_cursor": None}
    admin = f00b.admin_session(t054_stack)
    assert t054.list_candidates(admin).json == {"items": [], "next_cursor": None}


def test_a_service_credential_cannot_touch_personal_memory(t054_stack) -> None:
    owner, _run, lease = t054.owned_run(t054_stack)
    candidate = propose_ok(t054_stack, lease, logical_key="kv:svc-check")
    admin = f00b.admin_session(t054_stack)
    bearer = t054.service_key_client(t054_stack, admin)

    st.assert_error(t054.list_candidates(bearer), 403, t054.PERSONAL_SCOPE_FORBIDDEN)
    st.assert_error(
        bearer.get("/api/v1/memory-candidates"), 403, t054.PERSONAL_SCOPE_FORBIDDEN
    )
    st.assert_error(t054.list_memories(bearer), 403, t054.PERSONAL_SCOPE_FORBIDDEN)
    st.assert_error(
        t054.create_memory(bearer, application_id=owner.application_id, text="x"),
        403, t054.PERSONAL_SCOPE_FORBIDDEN,
    )
    st.assert_error(t054.decide(bearer, candidate["id"]), 403, t054.PERSONAL_SCOPE_FORBIDDEN)

    # The same key is a fully valid credential for a non-personal read.
    assert bearer.get("/api/v1/auth/me").status == 200
    # And nothing was written by any refused call.
    assert t054.memory_rows(t054_stack.dbname) == []
    assert len(t054.candidate_rows(t054_stack.dbname)) == 1

# ---------------------------------------------------------------------------
# decideMemoryCandidate: one memory per confirm, reject/expiry create nothing
# ---------------------------------------------------------------------------
def decide_ok(session, candidate_id: str, *, decision: str = "confirm", **kwargs) -> dict:
    response = t054.decide(session, candidate_id, decision=decision, **kwargs)
    assert response.status == 200, response.text
    return response.json


def test_a_confirm_creates_exactly_one_memory_and_repeats_return_it(t054_stack) -> None:
    owner, _run, lease = t054.owned_run(t054_stack)
    proposed = propose_ok(t054_stack, lease, text=TEXT, logical_key="kv:confirm")
    candidate = t054.candidate_row(t054_stack.dbname, proposed["id"])
    assert candidate is not None

    session = owner.creator
    first = decide_ok(session, proposed["id"], idempotency_key="confirm-key-1")
    assert set(first) == {"candidate_id", "state", "memory_id"}, first
    assert first["candidate_id"] == proposed["id"], first
    assert first["state"] == "confirmed", first
    memory_id = first["memory_id"]
    assert isinstance(memory_id, str), first

    # Exactly one memory row, tied to the candidate, confirmed by its owner.
    memories = t054.memory_rows(t054_stack.dbname)
    assert len(memories) == 1, memories
    memory = memories[0]
    assert memory["id"] == memory_id, memory
    assert memory["candidate_id"] == proposed["id"], memory
    assert memory["owner_id"] == owner.creator.principal_id, memory
    assert memory["application_id"] == proposed["application_id"], memory
    assert memory["revision"] == 1, memory
    assert memory["confirmed_by"] == owner.creator.principal_id, memory
    assert memory["confirmed_at"], memory
    # The memory body is the candidate's sealed body: confirming duplicates no
    # personal bytes, and the text is really readable from the sealed object.
    assert memory["body_artifact_id"] == candidate["body_artifact_id"], memory
    assert t054.memory_text(t054_stack, memory) == TEXT

    # The candidate is now confirmed, and the stored state agrees.
    assert t054.candidate_row(t054_stack.dbname, proposed["id"])["state"] == "confirmed"
    listed = t054.list_candidates(session).json["items"]
    assert [item["state"] for item in listed] == ["confirmed"], listed

    # A second confirm with a NEW Idempotency-Key (the page's retry/refresh) is
    # still the same memory: no second row, no second artifact, no second receipt.
    artifacts_after_first = t054.memory_artifact_count(t054_stack.dbname)
    again = decide_ok(session, proposed["id"], idempotency_key="confirm-key-2")
    assert again == first, (first, again)
    # A replay of the SAME key returns the recorded body as well.
    replayed = decide_ok(session, proposed["id"], idempotency_key="confirm-key-1")
    assert replayed == first, (first, replayed)
    assert len(t054.memory_rows(t054_stack.dbname)) == 1
    assert t054.memory_artifact_count(t054_stack.dbname) == artifacts_after_first

    # The same key with the opposite decision is a conflict, not a second answer.
    conflict = t054.decide(session, proposed["id"], decision="reject", idempotency_key="confirm-key-1")
    st.assert_error(conflict, 409, t054.IDEMPOTENCY_CONFLICT)
    # And a reject after a confirm is refused with the aggregate's own code.
    refused = t054.decide(session, proposed["id"], decision="reject", idempotency_key="confirm-key-3")
    st.assert_error(refused, 409, t054.CANDIDATE_CONFIRMED)
    assert len(t054.memory_rows(t054_stack.dbname)) == 1

    # The confirm raised the memory cache epoch in the same transaction.
    epoch = st.sql(
        t054_stack.dbname,
        "SELECT revision FROM platform.policy_epochs WHERE scope = "
        f"'memory:{owner.creator.principal_id}:{proposed['application_id']}'",
    )
    assert epoch == "1", epoch


def test_the_decision_input_boundaries_are_located_and_leak_no_existence(t054_stack) -> None:
    owner, _run, lease = t054.owned_run(t054_stack)
    proposed = propose_ok(t054_stack, lease, logical_key="kv:boundaries")

    # Missing Idempotency-Key: located 422 before any row is touched.
    missing = t054.decide(owner.creator, proposed["id"], idempotency_key=False)
    error = st.assert_error(missing, 422, "validation_failed")
    assert error["details"]["field"] == "Idempotency-Key", error
    # Invalid decision vocabulary.
    invalid = t054.decide(owner.creator, proposed["id"], decision="maybe")
    error = st.assert_error(invalid, 422, "validation_failed")
    assert error["details"]["field"] == "decision", error
    # Unknown id and a malformed id are both the same 404.
    st.assert_error(t054.decide(owner.creator, str(uuid.uuid4())), 404, t054.RESOURCE_NOT_FOUND)
    st.assert_error(t054.decide(owner.creator, "not-a-uuid"), 404, t054.RESOURCE_NOT_FOUND)

    # Another user and an administrator cannot see or decide this candidate.
    stranger = t041.secondary_owner(t054_stack).creator
    st.assert_error(t054.decide(stranger, proposed["id"]), 404, t054.RESOURCE_NOT_FOUND)
    admin = f00b.admin_session(t054_stack)
    st.assert_error(t054.decide(admin, proposed["id"]), 404, t054.RESOURCE_NOT_FOUND)
    assert t054.candidate_row(t054_stack.dbname, proposed["id"])["state"] == "pending"
    assert t054.memory_rows(t054_stack.dbname) == []


def test_a_reject_creates_nothing_and_a_later_confirm_is_refused(t054_stack) -> None:
    owner, _run, lease = t054.owned_run(t054_stack)
    proposed = propose_ok(t054_stack, lease, text=TEXT, logical_key="kv:reject")
    session = owner.creator

    first = decide_ok(session, proposed["id"], decision="reject", idempotency_key="reject-key-1")
    assert first == {"candidate_id": proposed["id"], "state": "rejected", "memory_id": None}, first
    assert t054.memory_rows(t054_stack.dbname) == []
    assert t054.candidate_row(t054_stack.dbname, proposed["id"])["state"] == "rejected"
    # Rejecting twice is idempotent and still creates nothing.
    again = decide_ok(session, proposed["id"], decision="reject", idempotency_key="reject-key-2")
    assert again == first, (first, again)
    assert t054.memory_rows(t054_stack.dbname) == []
    # A later confirm cannot resurrect a rejected candidate.
    refused = t054.decide(session, proposed["id"], decision="confirm", idempotency_key="reject-key-3")
    st.assert_error(refused, 409, t054.CANDIDATE_REJECTED)
    assert t054.memory_rows(t054_stack.dbname) == []
    # No reject wrote a second body artifact and no memory cache epoch: nothing
    # became recallable.
    assert len(t054.memory_artifact_ids(t054_stack.dbname, "memory_candidate")) == 1
    epoch = st.sql(
        t054_stack.dbname,
        "SELECT count(*) FROM platform.policy_epochs WHERE scope LIKE 'memory:%'",
    )
    assert epoch == "0", epoch


def test_expiry_is_enforced_by_the_server_not_by_a_status_field(t054_stack) -> None:
    owner, _run, lease = t054.owned_run(t054_stack)
    live = propose_ok(t054_stack, lease, text=TEXT, logical_key="kv:expiry-live")
    doomed = propose_ok(t054_stack, lease, text="An older proposal.", logical_key="kv:expiry-doomed")

    # The two candidates differ only in their server deadline.
    t054.set_expiry(t054_stack.dbname, doomed["id"], delta_seconds=-60)
    t054.set_expiry(t054_stack.dbname, live["id"], delta_seconds=3600)
    assert t054.candidate_row(t054_stack.dbname, doomed["id"])["state"] == "pending"

    # The list reports the real state: the read transitions the stored row, so the
    # database and the API agree rather than the page having to infer expiry.
    listed = {item["id"]: item["state"] for item in t054.list_candidates(owner.creator).json["items"]}
    assert listed == {doomed["id"]: "expired", live["id"]: "pending"}, listed
    assert t054.candidate_row(t054_stack.dbname, doomed["id"])["state"] == "expired"

    # Confirming or rejecting the expired candidate is refused and creates nothing.
    for decision in ("confirm", "reject"):
        refused = t054.decide(owner.creator, doomed["id"], decision=decision,
                              idempotency_key="expired-" + decision)
        st.assert_error(refused, 409, t054.CANDIDATE_EXPIRED)
    assert t054.memory_rows(t054_stack.dbname) == []
    # The failed decisions left no decision receipt behind (the debug run's own
    # idempotency receipt is unrelated and still there).
    assert t054.decision_receipt_count(t054_stack.dbname) == 0

    # The same decision path accepts the candidate whose deadline is still real.
    confirmed = decide_ok(owner.creator, live["id"], idempotency_key="live-confirm")
    assert confirmed["state"] == "confirmed", confirmed
    assert len(t054.memory_rows(t054_stack.dbname)) == 1

# ---------------------------------------------------------------------------
# createMemory: the explicit "remember this" path (no candidate)
# ---------------------------------------------------------------------------
def test_an_explicit_remember_writes_one_sealed_memory_without_a_candidate(t054_stack) -> None:
    owner = t041.owner(t054_stack)
    application_id = owner.application_id
    session = owner.creator

    response = t054.create_memory(session, application_id=application_id, text=TEXT)
    assert response.status == 201, response.text
    body = response.json
    assert set(body) == {"id", "application_id", "revision", "text"}, body
    assert body["application_id"] == application_id, body
    assert body["revision"] == 1, body
    assert body["text"] == TEXT, body

    # No candidate was created by this path.
    assert t054.candidate_rows(t054_stack.dbname) == []

    memories = t054.memory_rows(t054_stack.dbname)
    assert len(memories) == 1, memories
    memory = memories[0]
    assert memory["id"] == body["id"], memory
    assert memory["candidate_id"] is None, memory
    assert memory["owner_id"] == owner.creator.principal_id, memory
    assert memory["application_id"] == application_id, memory
    assert memory["revision"] == 1, memory
    assert memory["confirmed_by"] == owner.creator.principal_id, memory

    artifact = t054.artifact_row(t054_stack.dbname, memory["body_artifact_id"])
    assert artifact["state"] == "ready", artifact
    assert artifact["purpose"] == "memory_body", artifact
    assert artifact["media_type"].startswith("text/plain"), artifact
    assert artifact["owner_id"] == owner.creator.principal_id, artifact
    assert artifact["sha256"] == st.sha256_hex(TEXT), artifact
    assert t054.memory_text(t054_stack, memory) == TEXT

    # The memory is immediately listable by its owner, and only by its owner.
    listed = t054.list_memories(session)
    assert listed.status == 200, listed.text
    assert listed.json["items"] == [body], listed.json
    assert listed.json["next_cursor"] is None, listed.json

    # The direct write raised the same cache epoch the candidate confirm raises.
    epoch = st.sql(
        t054_stack.dbname,
        "SELECT revision FROM platform.policy_epochs WHERE scope = "
        f"'memory:{owner.creator.principal_id}:{application_id}'",
    )
    assert epoch == "1", epoch


def test_create_memory_validates_the_contract_input_and_the_scope_acl(t054_stack) -> None:
    owner = t041.owner(t054_stack)
    session = owner.creator
    other = t041.secondary_owner(t054_stack)

    def rejected(body: dict, status: int, code: str, field: str | None = None):
        response = session.client.post("/api/v1/memories", body=body, csrf=session.csrf)
        error = st.assert_error(response, status, code)
        if field is not None:
            assert error["details"]["field"] == field, (body, error)
        return error

    rejected({"application_id": owner.application_id, "text": "x"}, 422, "validation_failed", "confirmed")
    rejected({"application_id": owner.application_id, "text": "x", "confirmed": False},
             422, "validation_failed", "confirmed")
    rejected({"application_id": owner.application_id, "text": "", "confirmed": True},
             422, "validation_failed", "text")
    rejected({"application_id": owner.application_id, "text": "   ", "confirmed": True},
             422, "validation_failed", "text")
    rejected({"application_id": owner.application_id, "text": "x" * 4001, "confirmed": True},
             422, "validation_failed", "text")
    rejected({"text": "x", "confirmed": True}, 422, "validation_failed", "application_id")
    rejected({"application_id": "not-a-uuid", "text": "x", "confirmed": True},
             422, "validation_failed", "application_id")
    # Unknown and another user's application are the same 404.
    rejected({"application_id": str(uuid.uuid4()), "text": "x", "confirmed": True},
             404, t054.RESOURCE_NOT_FOUND)
    rejected({"application_id": other.application_id, "text": "x", "confirmed": True},
             404, t054.RESOURCE_NOT_FOUND)

    # A field outside the contract is refused by the strict decoder.
    outside = session.client.post(
        "/api/v1/memories",
        body={"application_id": owner.application_id, "text": "x", "confirmed": True, "extra": 1},
        csrf=session.csrf,
    )
    assert outside.status == 400, outside.text
    assert t054.memory_rows(t054_stack.dbname) == []
    assert len(t054.memory_artifact_ids(t054_stack.dbname, "memory_body")) == 0


# ---------------------------------------------------------------------------
# listMemories: the real read model that replaces F01's empty list
# ---------------------------------------------------------------------------
def test_the_memory_list_is_the_owners_real_read_model(t054_stack) -> None:
    owner = t041.owner(t054_stack)
    other = t041.secondary_owner(t054_stack)

    first = t054.create_memory(owner.creator, application_id=owner.application_id, text=TEXT).json
    second = t054.create_memory(
        owner.creator, application_id=owner.application_id, text="The user works in Beijing."
    ).json
    other_memory = t054.create_memory(
        other.creator, application_id=other.application_id, text="Another user's fact."
    ).json

    listed = t054.list_memories(owner.creator)
    assert listed.status == 200, listed.text
    ids = [item["id"] for item in listed.json["items"]]
    assert set(ids) == {first["id"], second["id"]}, ids
    assert other_memory["id"] not in ids
    for item in listed.json["items"]:
        assert set(item) == {"id", "application_id", "revision", "text"}, item
        assert item["revision"] == 1, item

    # The other user sees only their own memory.
    theirs = t054.list_memories(other.creator).json["items"]
    assert [item["id"] for item in theirs] == [other_memory["id"]], theirs
    # The administrator is not exempt: no ownership, no rows.
    admin = f00b.admin_session(t054_stack)
    assert t054.list_memories(admin).json == {"items": [], "next_cursor": None}

    # The application scope filter is part of the same WHERE as the page cut.
    scoped = t054.list_memories(owner.creator, application_id=owner.application_id)
    assert len(scoped.json["items"]) == 2, scoped.json
    empty_scope = t054.list_memories(owner.creator, application_id=str(uuid.uuid4()))
    assert empty_scope.json == {"items": [], "next_cursor": None}, empty_scope.json

    # Keyset pagination over the real rows.
    page = t054.list_memories(owner.creator, limit=1)
    assert len(page.json["items"]) == 1, page.json
    assert page.json["next_cursor"], page.json
    follow = t054.list_memories(owner.creator, limit=1, cursor=page.json["next_cursor"])
    assert len(follow.json["items"]) == 1, follow.json
    assert follow.json["items"][0]["id"] != page.json["items"][0]["id"], follow.json
    assert follow.json["next_cursor"] is None, follow.json

    # The contract's validation boundaries, including the F01 blank-limit rule.
    for query in ("?limit=0", "?limit=101", "?limit=abc", "?limit=", "?limit=-1"):
        error = st.assert_error(owner.creator.client.get("/api/v1/memories" + query),
                                422, "validation_failed")
        assert error["details"]["field"] == "limit", (query, error)
    error = st.assert_error(
        t054.list_memories(owner.creator, application_id="not-a-uuid"), 422, "validation_failed"
    )
    assert error["details"]["field"] == "application_id", error
    st.assert_error(t054.list_memories(owner.creator, cursor="not-a-cursor"), 400, "invalid_cursor")


def test_a_fresh_human_has_an_honest_empty_memory_list(t054_stack) -> None:
    owner = t041.owner(t054_stack)
    response = t054.list_memories(owner.creator)
    assert response.status == 200, response.text
    assert response.json == {"items": [], "next_cursor": None}
    assert response.header("Cache-Control") == "no-store"

# ---------------------------------------------------------------------------
# failure, retry and recovery boundaries
# ---------------------------------------------------------------------------
def test_missing_memory_bytes_fail_closed_and_recover(t054_stack) -> None:
    owner = t041.owner(t054_stack)
    created = t054.create_memory(
        owner.creator, application_id=owner.application_id, text=TEXT
    ).json
    memory = t054.memory_row(t054_stack.dbname, created["id"])
    artifact = t054.artifact_row(t054_stack.dbname, memory["body_artifact_id"])

    # Deleting the sealed object must never turn into "empty memory text": the
    # read model fails closed.
    t054.remove_object(t054_stack, artifact["object_key"])
    st.assert_error(t054.list_memories(owner.creator), 503, "object_store_unavailable")

    # Restoring the exact bytes recovers the same row (no rewrite, no new memory).
    t054.write_object(t054_stack, artifact["object_key"], TEXT.encode("utf-8"))
    recovered = t054.list_memories(owner.creator)
    assert recovered.status == 200, recovered.text
    assert [item["text"] for item in recovered.json["items"]] == [TEXT], recovered.json
    assert len(t054.memory_rows(t054_stack.dbname)) == 1


def test_a_confirm_never_commits_a_memory_without_readable_bytes(t054_stack) -> None:
    owner, _run, lease = t054.owned_run(t054_stack)
    proposed = propose_ok(t054_stack, lease, text=TEXT, logical_key="kv:lost-body")
    candidate = t054.candidate_row(t054_stack.dbname, proposed["id"])
    artifact = t054.artifact_row(t054_stack.dbname, candidate["body_artifact_id"])
    t054.remove_object(t054_stack, artifact["object_key"])

    refused = t054.decide(owner.creator, proposed["id"], idempotency_key="lost-body-1")
    st.assert_error(refused, 503, "object_store_unavailable")
    assert t054.candidate_row(t054_stack.dbname, proposed["id"])["state"] == "pending"
    assert t054.memory_rows(t054_stack.dbname) == []
    assert t054.decision_receipt_count(t054_stack.dbname) == 0

    # Recovery: with the bytes back, the same pending candidate confirms once.
    t054.write_object(t054_stack, artifact["object_key"], TEXT.encode("utf-8"))
    confirmed = decide_ok(owner.creator, proposed["id"], idempotency_key="lost-body-2")
    assert confirmed["state"] == "confirmed", confirmed
    assert len(t054.memory_rows(t054_stack.dbname)) == 1


def test_a_proposal_without_the_object_store_fails_closed(t054_stack) -> None:
    # The API is started with a real object store by the stack fixture; the
    # failure is observed by deleting the sealed body of an existing candidate,
    # which makes the list (the only reader) fail closed rather than fabricate.
    owner, _run, lease = t054.owned_run(t054_stack)
    proposed = propose_ok(t054_stack, lease, text=TEXT, logical_key="kv:list-lost-body")
    candidate = t054.candidate_row(t054_stack.dbname, proposed["id"])
    artifact = t054.artifact_row(t054_stack.dbname, candidate["body_artifact_id"])
    t054.remove_object(t054_stack, artifact["object_key"])
    st.assert_error(t054.list_candidates(owner.creator), 503, "object_store_unavailable")
    t054.write_object(t054_stack, artifact["object_key"], TEXT.encode("utf-8"))
    assert t054.list_candidates(owner.creator).status == 200

def test_two_concurrent_proposals_for_one_logical_key_return_one_candidate(t054_stack) -> None:
    _owner, _run, lease = t054.owned_run(t054_stack)
    results = []

    def propose_once() -> None:
        results.append(t054.propose(t054_stack, lease, text=TEXT, logical_key="kv:race"))

    threads = [threading.Thread(target=propose_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(result.status for result in results) == [201, 201], [
        (result.status, result.text) for result in results
    ]
    assert len({result.json["id"] for result in results}) == 1, [r.json for r in results]
    assert len(t054.candidate_rows(t054_stack.dbname)) == 1
    assert len(t054.memory_artifact_ids(t054_stack.dbname, "memory_candidate")) == 1


def test_two_concurrent_confirms_with_different_keys_create_one_memory(t054_stack) -> None:
    owner, _run, lease = t054.owned_run(t054_stack)
    proposed = propose_ok(t054_stack, lease, text=TEXT, logical_key="kv:race-confirm")
    results = []

    def confirm_once(key: str) -> None:
        results.append(t054.decide(owner.creator, proposed["id"], idempotency_key=key))

    threads = [
        threading.Thread(target=confirm_once, args=("race-key-1",)),
        threading.Thread(target=confirm_once, args=("race-key-2",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(result.status for result in results) == [200, 200], [
        (result.status, result.text) for result in results
    ]
    memory_ids = {result.json["memory_id"] for result in results}
    assert len(memory_ids) == 1, [r.json for r in results]
    assert len(t054.memory_rows(t054_stack.dbname)) == 1

