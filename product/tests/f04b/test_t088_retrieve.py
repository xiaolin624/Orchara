"""F04B/T088 acceptance: the retrieve node feeds real knowledge citations downstream.

Every case goes through the product's real path: a real draft saved through the
public API (validated by the Go workflow compiler), a real debug submission, the
compiled ``cmd/supervisor exec --once`` claiming the attempt and calling the new
non-contract internal retrieval route over real mTLS, the API deriving the run
owner from the database and running the F10 knowledge service's ACL-filtered Query
against real PostgreSQL, real ingested documents (``support/t055``), the run-scoped
mTLS artifact routes, the owner-only node-results read and the real citation route.

Nothing is mocked. The embedding model is the deployment's documented deterministic
``local-hash-v1`` stub (no provider credentials exist in this environment); the
knowledge service, ACL, chunks, spans and citations are real rows.
"""

from __future__ import annotations

import json

import pytest

from support import f00b, t025, t033, t042, t055, t064, t083, t088
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t055_stack,
)

QUERY = "华北地区十月的销售额是多少"

DOCUMENT_B = (
    "# 售后政策\n"
    "\n"
    "华东地区十一月的退款申请会在两个工作日内处理。\n"
    "\n"
    "退款需提供订单号；超过三十天的申请不再受理。\n"
)


@pytest.fixture(scope="module", autouse=True)
def supervisor_config() -> None:
    # The compiled supervisor validates its sandbox deployment even when the
    # claimed graph contains no sandbox node (retrieve runs entirely in the API).
    if not t033.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: the compiled supervisor's sandbox profile "
            "cannot be built; build the rootfs with deploy/sandbox/build_python_rootfs.sh"
        )


def make_user(stack, admin, login: str):
    # These owners author an application/draft, so they are creators.
    return f00b.invite_and_activate(
        stack, admin, login, roles=("creator",), password=f00b.USER_PASSWORD
    )


def assert_run_succeeded(report: dict) -> None:
    assert report["status"] == "executed", report
    assert report["outcome"] == "succeeded", report
    assert report.get("error_code", "") == "", report
    assert report["run_state"] == "succeeded", report
    # A retrieve-only graph never starts a sandbox unit.
    assert report.get("sandbox") is None, report


def item_key_set(item: dict) -> set[str]:
    return set(item)


# ---------------------------------------------------------------------------
# 1. real citations, doc/version/span preserved, downstream step opens them
# ---------------------------------------------------------------------------
def test_retrieve_returns_real_citations_and_passes_them_downstream(t055_stack) -> None:
    stack = t055_stack
    admin = f00b.admin_session(stack)
    alice = make_user(stack, admin, "t088.alice")
    base_id, document_id = t088.ready_knowledge_base(
        stack, alice, "t088 sales", t055.SAMPLE_DOCUMENT
    )

    owner, application_id, run = t088.submit_run(
        stack,
        alice,
        t088.single_retrieve_definition([base_id]),
        input_value={"question": QUERY},
        name="t088 single retrieve",
    )
    # The same fixture the executor is about to run passes publish validation.
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )

    completed, report = t088.run_once(stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert_run_succeeded(report)
    state = t033.wait_run_state(stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state

    # Exactly one node result (the search data artifact; the end node seals the
    # final output artifact, which is not a node result row).
    items = t083.node_results(owner.creator, run["id"], expected=1)
    assert [item["node_id"] for item in items] == ["search"], items
    assert items[0]["kind"] == "data" and items[0]["label"] == "results", items
    output = t083.node_data_document(owner.creator, items, "search")

    # The documented non-contract output shape, exactly.
    assert set(output) == {"items", "citations", "count"}, output
    assert output["count"] == len(output["items"]) >= 1
    item = output["items"][0]
    assert item_key_set(item) == t088.ITEM_KEYS, item
    projection = output["citations"][0]
    assert item_key_set(projection) == t088.CITATION_KEYS, projection

    # Independent truth: the PostgreSQL chunk row and its stored citation.
    db_chunk = t088.chunk_row(
        stack, document_id, int(item["document_revision"]), int(item["chunk_no"])
    )
    db_citation = t055.citation_of(db_chunk)
    assert item["chunk_id"] == db_chunk["id"], item
    assert item["document_id"] == document_id, item
    assert int(item["document_revision"]) == int(db_chunk["document_revision"]), item
    assert item["document_state"] == "ready", item
    assert int(item["chunk_no"]) == int(db_chunk["chunk_no"]), item
    assert item["span"] == db_citation["span"], (item["span"], db_citation["span"])
    assert int(item["span"]["start_line"]) >= 1 and int(item["span"]["start_char"]) >= 0
    assert item["citation"] == db_citation, (item["citation"], db_citation)
    assert item["text"] == t088.chunk_body_text(
        stack, document_id, int(db_chunk["document_revision"]), int(db_chunk["chunk_no"])
    )
    assert isinstance(item["score"], (int, float))

    # The citations projection is the lightweight view of the same item.
    assert projection == {
        "chunk_id": item["chunk_id"],
        "document_id": item["document_id"],
        "document_revision": item["document_revision"],
        "span": item["span"],
        "text": item["text"],
    }, projection

    # No framework/vendor internals as platform fields: no top-level embedding,
    # object key, signed URL or vector. The stored citation document is forwarded
    # verbatim, so its own provenance fields (including the embedding *model
    # revision* string) stay intact.
    assert "embedding" not in item, item
    serialized = json.dumps(output)
    for forbidden in ("object_key", "upload_url", "download_url", "signed_url", "vector"):
        assert forbidden not in serialized, (forbidden, serialized[:400])

    # Opening the citation through the owner route returns the same real source.
    opened = t055.get_citation(owner.creator, item["chunk_id"])
    assert opened.status == 200, opened.text
    body = opened.json
    assert body["chunk_id"] == item["chunk_id"]
    assert body["document_id"] == document_id
    assert int(body["document_revision"]) == int(db_chunk["document_revision"])
    assert int(body["chunk_no"]) == int(db_chunk["chunk_no"])
    assert body["document_state"] == "ready"
    assert body["span"] == item["span"]
    assert body["citation"] == item["citation"]
    assert body["text"] == item["text"]

    # The downstream end node received the citations unchanged.
    final = t088.final_output_document(owner.creator, stack.dbname, run["id"])
    assert set(final) == {"citations", "count"}, final
    assert final["citations"] == output["citations"], final
    assert final["count"] == output["count"], final

    states = t088.node_states(stack.dbname, run["id"])
    assert states == {
        "start": ["pending", "running", "succeeded"],
        "search": ["pending", "running", "succeeded"],
        "end": ["pending", "running", "succeeded"],
    }, states


# ---------------------------------------------------------------------------
# 2. no results is a succeeded node with empty items, never an error
# ---------------------------------------------------------------------------
def test_retrieve_without_any_retrievable_chunk_succeeds_with_empty_items(t055_stack) -> None:
    stack = t055_stack
    admin = f00b.admin_session(stack)
    alice = make_user(stack, admin, "t088.alice")
    # A real, owner-visible knowledge base with no ingested document: the SQL
    # candidate set is empty. (With readable chunks the F10 ranking always returns
    # top_k — the platform has no relevance threshold — so the contract's "no
    # results ⇒ empty, never an error" is exercised on the empty candidate set.)
    base_id = t088.create_knowledge_base(alice, "t088 empty")

    owner, _application_id, run = t088.submit_run(
        stack,
        alice,
        t088.single_retrieve_definition([base_id]),
        input_value={"question": QUERY},
        name="t088 no results",
    )
    completed, report = t088.run_once(stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert_run_succeeded(report)

    items = t083.node_results(owner.creator, run["id"], expected=1)
    output = t083.node_data_document(owner.creator, items, "search")
    assert output == {"items": [], "citations": [], "count": 0}, output

    final = t088.final_output_document(owner.creator, stack.dbname, run["id"])
    assert final == {"citations": [], "count": 0}, final
    states = t088.node_states(stack.dbname, run["id"])
    assert states["search"] == ["pending", "running", "succeeded"], states


# ---------------------------------------------------------------------------
# 3. ACL: a foreign knowledge base is excluded silently; all-foreign is empty
# ---------------------------------------------------------------------------
def test_a_foreign_knowledge_base_never_reaches_another_owners_run(t055_stack) -> None:
    stack = t055_stack
    admin = f00b.admin_session(stack)
    alice = make_user(stack, admin, "t088.alice")
    bob = make_user(stack, admin, "t088.bob")
    alice_base, alice_document = t088.ready_knowledge_base(
        stack, alice, "t088 alice", t055.SAMPLE_DOCUMENT, log_name="maintenance-t088-alice.log"
    )
    bob_base, bob_document = t088.ready_knowledge_base(
        stack, bob, "t088 bob", DOCUMENT_B, log_name="maintenance-t088-bob.log"
    )
    bob_chunk_ids = [
        chunk["id"] for chunk in t055.chunk_rows(stack.dbname, bob_document)
    ]
    assert bob_chunk_ids

    # Alice's run names both bases. The foreign one is silently excluded: the
    # output never contains Bob's document or chunk ids.
    owner, _application_id, run = t088.submit_run(
        stack,
        alice,
        t088.single_retrieve_definition([alice_base, bob_base]),
        input_value={"question": QUERY},
        name="t088 acl mixed",
    )
    completed, report = t088.run_once(stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert_run_succeeded(report)
    items = t083.node_results(owner.creator, run["id"], expected=1)
    output = t083.node_data_document(owner.creator, items, "search")
    assert output["count"] >= 1, output
    assert {item["document_id"] for item in output["items"]} == {alice_document}, output
    serialized = json.dumps(output)
    assert bob_document not in serialized
    for chunk_id in bob_chunk_ids:
        assert chunk_id not in serialized

    # Naming only the foreign base is the zero-readable case: an empty successful
    # result, never a 404/403 and never an error.
    owner2, _application2, run2 = t088.submit_run(
        stack,
        alice,
        t088.single_retrieve_definition([bob_base]),
        input_value={"question": QUERY},
        name="t088 acl foreign only",
    )
    completed2, report2 = t088.run_once(stack)
    assert completed2.returncode == 0, (completed2.stderr, report2)
    assert_run_succeeded(report2)
    items2 = t083.node_results(owner2.creator, run2["id"], expected=1)
    output2 = t083.node_data_document(owner2.creator, items2, "search")
    assert output2 == {"items": [], "citations": [], "count": 0}, output2
    assert bob_document not in json.dumps(output2)


# ---------------------------------------------------------------------------
# 4. the internal route itself: owner from the DB, ACL counters, role/token
# ---------------------------------------------------------------------------
def test_the_internal_route_derives_the_owner_and_counts_the_acl(t055_stack) -> None:
    stack = t055_stack
    admin = f00b.admin_session(stack)
    alice = make_user(stack, admin, "t088.alice")
    bob = make_user(stack, admin, "t088.bob")
    alice_base, alice_document = t088.ready_knowledge_base(
        stack, alice, "t088 alice", t055.SAMPLE_DOCUMENT, log_name="maintenance-t088-alice.log"
    )
    bob_base, bob_document = t088.ready_knowledge_base(
        stack, bob, "t088 bob", DOCUMENT_B, log_name="maintenance-t088-bob.log"
    )

    # Queue a real run as Alice and claim its live attempt; the route is called
    # with that attempt token, never with an owner id.
    owner, _application_id, run = t088.submit_run(
        stack,
        alice,
        t088.single_retrieve_definition([alice_base]),
        input_value={"question": QUERY},
        name="t088 route",
    )
    lease = t088.claim_one_run(stack)
    assert lease["run_id"] == run["id"], (lease, run)
    token = lease["attempt_token"]
    body = {
        "attempt_token": token,
        "knowledge_base_ids": [alice_base, bob_base],
        "query": QUERY,
        "top_k": 5,
    }

    client = t088.retrieval_client(stack, "worker")
    response = client.retrieve(run["id"], body)
    assert response.status == 200, response.text
    payload = response.json
    assert set(payload) == {"items", "requested", "authorized"}, payload
    assert payload["requested"] == 2, payload
    assert payload["authorized"] == 1, payload
    assert payload["items"], payload
    assert {item["document_id"] for item in payload["items"]} == {alice_document}
    # The merged answer is ordered by score DESC (the stable chunk_id tie-break is
    # exercised by the Go merge unit test).
    scores = [item["score"] for item in payload["items"]]
    assert scores == sorted(scores, reverse=True), scores
    for item in payload["items"]:
        # The internal route's item is exactly the documented platform projection.
        assert set(item) == t088.ITEM_KEYS, item
    serialized = json.dumps(payload)
    assert bob_document not in serialized
    assert "object_key" not in serialized
    assert all("embedding" not in item for item in payload["items"])

    # Zero readable bases: 200 with requested/authorized and an empty list.
    foreign = dict(body, knowledge_base_ids=[bob_base, f00b.new_uuid()])
    response = client.retrieve(run["id"], foreign)
    assert response.status == 200, response.text
    assert response.json == {"items": [], "requested": 2, "authorized": 0}, response.json

    # A supervisor certificate is authenticated but not this capacity.
    supervisor = t088.retrieval_client(stack, "supervisor")
    refused = supervisor.retrieve(run["id"], body)
    assert refused.status == 403, refused.text
    assert refused.json["error"]["code"] == "forbidden"

    # A superseded/absent attempt token is the documented stale_attempt 409.
    stale_body = dict(body, attempt_token=f00b.new_uuid())
    stale = client.retrieve(run["id"], stale_body)
    assert stale.status == 409, stale.text
    assert stale.json["error"]["code"] == "stale_attempt"

    # Malformed body shapes are located 422s, not 500s.
    for patch, field in (
        ({"knowledge_base_ids": []}, "knowledge_base_ids"),
        ({"knowledge_base_ids": [alice_base, alice_base]}, "knowledge_base_ids"),
        ({"query": "   "}, "query"),
        ({"top_k": 0}, "top_k"),
        ({"top_k": 21}, "top_k"),
    ):
        invalid = client.retrieve(run["id"], dict(body, **patch))
        assert invalid.status == 422, (patch, invalid.text)
        assert invalid.json["error"]["code"] == "validation_failed", invalid.text
        assert invalid.json["error"]["details"]["field"] == field, (patch, invalid.json)


# ---------------------------------------------------------------------------
# 5. revoked/deleted knowledge: no results now, old citations are unavailable
# ---------------------------------------------------------------------------
def test_revoked_knowledge_returns_nothing_and_the_old_citation_is_unavailable(
    t055_stack,
) -> None:
    stack = t055_stack
    admin = f00b.admin_session(stack)
    alice = make_user(stack, admin, "t088.alice")
    base_id, document_id = t088.ready_knowledge_base(
        stack, alice, "t088 revoke", t055.SAMPLE_DOCUMENT
    )
    chunks = t055.chunk_rows(stack.dbname, document_id)
    assert chunks
    old_chunk_id = chunks[0]["id"]

    # The source is readable before revocation.
    before = t055.get_citation(alice, old_chunk_id)
    assert before.status == 200, before.text

    t064.revoke_document(stack.dbname, document_id)

    # The old citation route is 404 and serves no cached body.
    after = t055.get_citation(alice, old_chunk_id)
    assert after.status == 404, after.text

    owner, _application_id, run = t088.submit_run(
        stack,
        alice,
        t088.single_retrieve_definition([base_id]),
        input_value={"question": QUERY},
        name="t088 revoked",
    )
    completed, report = t088.run_once(stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert_run_succeeded(report)
    items = t083.node_results(owner.creator, run["id"], expected=1)
    output = t083.node_data_document(owner.creator, items, "search")
    assert output == {"items": [], "citations": [], "count": 0}, output
    assert old_chunk_id not in json.dumps(output)


# ---------------------------------------------------------------------------
# 6. two parallel retrieve nodes never cross their results
# ---------------------------------------------------------------------------
def test_two_parallel_retrieve_nodes_keep_their_own_documents(t055_stack) -> None:
    stack = t055_stack
    admin = f00b.admin_session(stack)
    alice = make_user(stack, admin, "t088.alice")
    left_base, left_document = t088.ready_knowledge_base(
        stack, alice, "t088 left", t055.SAMPLE_DOCUMENT, log_name="maintenance-t088-left.log"
    )
    right_base, right_document = t088.ready_knowledge_base(
        stack, alice, "t088 right", DOCUMENT_B, log_name="maintenance-t088-right.log"
    )

    owner, _application_id, run = t088.submit_run(
        stack,
        alice,
        t088.parallel_retrieve_definition(left_base, right_base),
        input_value={"left": "华北地区十月的销售额", "right": "退款申请处理时限"},
        name="t088 parallel",
    )
    completed, report = t088.run_once(stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert_run_succeeded(report)

    items = t083.node_results(owner.creator, run["id"], expected=3)
    left = t083.node_data_document(owner.creator, items, "left_search")
    right = t083.node_data_document(owner.creator, items, "right_search")
    assert {item["document_id"] for item in left["items"]} == {left_document}, left
    assert {item["document_id"] for item in right["items"]} == {right_document}, right
    assert {item["document_id"] for item in left["citations"]} == {left_document}, left
    assert {item["document_id"] for item in right["citations"]} == {right_document}, right
    assert right_document not in json.dumps(left)
    assert left_document not in json.dumps(right)

    joined = t083.node_data_document(owner.creator, items, "join")["results"]
    assert joined["left_search"]["status"] == "succeeded", joined
    assert joined["right_search"]["status"] == "succeeded", joined
    assert joined["left_search"]["value"] == left, joined
    assert joined["right_search"]["value"] == right, joined

    states = t088.node_states(stack.dbname, run["id"])
    for node_id in ("left_search", "right_search", "join"):
        assert states[node_id] == ["pending", "running", "succeeded"], states
