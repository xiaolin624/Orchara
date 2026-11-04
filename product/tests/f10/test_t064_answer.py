"""T064 acceptance: an answer cites only this retrieval, and only what is readable now.

Every test drives the real stack: the previous ticket's PostgreSQL database, the
compiled ``cmd/api`` binary behind real HTTP with real sealed object bytes, the
real ``document_ingest`` maintenance process, and a second real API process for
the documented dev-stub answer-generator faults. The API is never trusted as its
own oracle: chunk provenance, source hashes and the revocation tombstone are read
from PostgreSQL and from the sealed files.
"""

from __future__ import annotations

import json
import threading

from support import f00b, t055, t064
from support.fixtures import t055_stack  # noqa: F401 - pytest fixture

QUERY = "华北地区十月的销售额是多少"
EMBEDDING_REVISION = "local-hash-v1"


def setup_ready_kb(stack, log_name: str, *, login: str = "t064.alice"):
    """One owner, one KB, one document that reached ready through the real job."""
    admin = f00b.admin_session(stack)
    owner = f00b.invite_and_activate(stack, admin, login, password=f00b.USER_PASSWORD)
    base_id = t055.create_knowledge_base(owner, login + " base").json["id"]
    artifact_id = t055.prepare_source(stack, owner, t055.SAMPLE_DOCUMENT)
    log_path = stack.server.log_path.parent / f"maintenance-t064-{log_name}.log"
    with t055.ingest_pools(stack, log_path):
        accepted = t055.ingest_document(owner, base_id, artifact_id)
        assert accepted.status == 202, accepted.text
        document_id = accepted.json["id"]
        view = t055.wait_for_document_state(owner, document_id, {"ready", "failed"})
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    return owner, base_id, artifact_id, document_id


def test_an_answer_cites_only_this_retrieval_and_every_citation_opens(t055_stack) -> None:
    stack = t055_stack
    alice, base_id, artifact_id, document_id = setup_ready_kb(stack, "answer")

    response = t064.answer(alice, base_id, QUERY, top_k=3)
    assert response.status == 200, response.text
    payload = response.json
    assert payload["outcome"] == t064.OUTCOME_ANSWERED

    # The generation step is labelled as a stub, never presented as a model.
    generation = payload["answer"]["generation"]
    assert generation == {
        "kind": t064.ANSWER_GENERATOR_KIND,
        "version": t064.ANSWER_GENERATOR_VERSION,
        "stub": True,
        "model_provider": t064.ANSWER_MODEL_PROVIDER,
    }, generation
    assert payload["answer"]["text"].strip(), payload["answer"]

    # "This retrieval" is independently re-read through the existing retrieval
    # endpoint: the answer's citation set must be a subset of it, in rank order.
    retrieved = t055.retrieval_query(alice, base_id, QUERY, top_k=3)
    assert retrieved.status == 200, retrieved.text
    retrieval_items = retrieved.json["items"]
    assert retrieval_items
    assert payload["retrieval"]["chunk_ids"] == [item["chunk_id"] for item in retrieval_items]
    assert payload["retrieval"]["embedding_model_revision"] == EMBEDDING_REVISION
    assert payload["retrieval"]["top_k"] == 3
    assert payload["retrieval"]["acl_epoch"] == t064.knowledge_base_revision(stack.dbname, base_id)

    citations = t064.citations_of(payload)
    assert 1 <= len(citations) <= t064.ANSWER_CITE_LIMIT, citations
    by_chunk = {item["chunk_id"]: item for item in retrieval_items}
    assert [citation["chunk_id"] for citation in citations] == [
        item["chunk_id"] for item in retrieval_items[: len(citations)]
    ], citations
    assert "华北地区" in citations[0]["text"], citations[0]

    # Provenance is exact: the API's citation must agree with the retrieval item,
    # with the chunk row in PostgreSQL and with the sealed source bytes.
    source = t055.artifact_row(stack.dbname, artifact_id)
    source_bytes = (stack.object_dir / source["object_key"]).read_bytes()
    source_text = source_bytes.decode("utf-8")
    runes = list(source_text)
    for citation in citations:
        chunk_id = citation["chunk_id"]
        assert chunk_id in by_chunk, citation
        item = by_chunk[chunk_id]
        assert citation["document_id"] == item["document_id"] == document_id
        assert int(citation["document_revision"]) == int(item["document_revision"]) == 1
        assert int(citation["chunk_no"]) == int(item["chunk_no"])
        assert citation["span"] == item["span"]
        assert citation["citation"] == item["citation"]
        assert citation["text"] == item["text"]
        assert float(citation["score"]) == float(item["score"])

        row = t064.chunk_row(stack.dbname, chunk_id)
        assert row["document_id"] == document_id
        assert int(row["document_revision"]) == 1
        assert int(row["chunk_no"]) == int(citation["chunk_no"])
        assert row["body_artifact_id"]

        # Independent truth: the citation's source hash and rune span resolve to
        # the sealed source bytes, not to another response from the API.
        assert t055.sha256_hex(source_bytes) == citation["citation"]["source_sha256"]
        start, end = int(citation["span"]["start_char"]), int(citation["span"]["end_char"])
        assert "".join(runes[start:end]) == citation["text"]
        assert int(citation["span"]["start_line"]) == source_text[:start].count("\n") + 1

        # The extractive answer quotes the chunk it cites.
        excerpt = citation["text"].strip()
        assert excerpt[: min(120, len(excerpt))] in payload["answer"]["text"], citation

        # Opening the citation now returns exactly the cited body.
        opened = t055.get_citation(alice, chunk_id)
        assert opened.status == 200, opened.text
        assert opened.json["text"] == citation["text"]
        assert opened.json["citation"] == citation["citation"]
        assert opened.json["document_id"] == document_id


def test_an_empty_knowledge_base_returns_an_empty_answer_not_an_error(t055_stack) -> None:
    admin = f00b.admin_session(t055_stack)
    alice = f00b.invite_and_activate(t055_stack, admin, "t064.empty", password=f00b.USER_PASSWORD)
    base_id = t055.create_knowledge_base(alice, "t064 empty").json["id"]

    response = t064.answer(alice, base_id, QUERY)
    assert response.status == 200, response.text
    payload = response.json
    assert payload["outcome"] == t064.OUTCOME_NO_RESULTS
    assert payload["answer"]["text"] == ""
    assert payload["answer"]["citations"] == []
    assert payload["retrieval"]["chunk_ids"] == []
    assert payload["retrieval"]["top_k"] == 8
    assert payload["answer"]["generation"]["stub"] is True


def test_a_foreign_principal_and_an_administrator_cannot_ask(t055_stack) -> None:
    stack = t055_stack
    admin = f00b.admin_session(stack)
    alice, base_id, _artifact_id, _document_id = setup_ready_kb(stack, "acl")
    bob = f00b.invite_and_activate(stack, admin, "t064.bob", password=f00b.USER_PASSWORD)

    for caller in (bob, admin):
        refused = t064.answer(caller, base_id, QUERY)
        assert refused.status == 404, refused.text
        assert refused.json["error"]["code"] == "resource_not_found"
        assert "answer" not in refused.json
        assert "citations" not in refused.json

    unknown = t064.answer(alice, f00b.new_uuid(), QUERY)
    assert unknown.status == 404, unknown.text
    malformed = t064.answer(alice, "not-a-uuid", QUERY)
    assert malformed.status == 404, malformed.text


def test_the_answer_surface_needs_csrf_for_sessions_and_the_read_scope_for_api_keys(
    t055_stack,
) -> None:
    stack = t055_stack
    admin = f00b.admin_session(stack)
    alice, base_id, _artifact_id, _document_id = setup_ready_kb(stack, "scope")

    no_csrf = t064.answer(alice, base_id, QUERY, csrf=False)
    assert no_csrf.status == 403, no_csrf.text
    assert no_csrf.json["error"]["code"] == "csrf_invalid"

    service_id = t055.create_service_account(admin, "t064 service", alice.principal_id)
    _key_id, read_secret = t055.create_api_key(admin, service_id, ["application:read"])
    _write_key_id, write_secret = t055.create_api_key(
        admin, service_id, ["application:write"]
    )
    http = stack.client()
    read_headers = {"Authorization": f"Bearer {read_secret}"}
    write_headers = {"Authorization": f"Bearer {write_secret}"}

    # The scope is refused before any resource lookup: a write-scoped key may not
    # ask, even on a KB the same service account owns.
    created = http.post(
        "/api/v1/knowledge-bases", body={"name": "t064 keyed"}, headers=write_headers
    )
    assert created.status == 201, created.text
    keyed_base = created.json["id"]
    refused = http.post(
        t064.answer_path(keyed_base),
        body={"query": QUERY},
        headers=write_headers,
    )
    assert refused.status == 403, refused.text
    assert refused.json["error"]["code"] == "insufficient_scope"

    # A read-scoped key passes the scope check and reaches the owner ACL: it may
    # ask over the service account's own (empty) KB and gets no_results.
    allowed = http.post(
        t064.answer_path(keyed_base),
        body={"query": QUERY},
        headers=read_headers,
    )
    assert allowed.status == 200, allowed.text
    assert allowed.json["outcome"] == t064.OUTCOME_NO_RESULTS

    # The same read key is scope-accepted but ACL-refused on another owner's KB:
    # 404, not 403, so the surface cannot be used to enumerate knowledge bases.
    foreign = http.post(
        t064.answer_path(base_id),
        body={"query": QUERY},
        headers=read_headers,
    )
    assert foreign.status == 404, foreign.text
    assert foreign.json["error"]["code"] == "resource_not_found"


def test_a_revoked_document_cannot_be_read_through_a_citation_after_the_answer(
    t055_stack,
) -> None:
    stack = t055_stack
    alice, base_id, _artifact_id, document_id = setup_ready_kb(stack, "revoke")

    answered = t064.answer(alice, base_id, QUERY)
    assert answered.status == 200, answered.text
    citations = t064.citations_of(answered.json)
    assert citations

    # The rows and sealed bytes still exist; only the current-authorization gate
    # can make the old body unreadable, so this is not "the data was deleted".
    chunk_rows_before = t055.chunk_rows(stack.dbname, document_id, 1)
    assert chunk_rows_before

    t064.revoke_document(stack.dbname, document_id)
    assert t055.document_row(stack.dbname, document_id)["state"] == "deleted"
    assert len(t055.chunk_rows(stack.dbname, document_id, 1)) == len(chunk_rows_before)

    for citation in citations:
        body = t055.artifact_row(stack.dbname, t064.chunk_row(
            stack.dbname, citation["chunk_id"]
        )["body_artifact_id"])
        assert body["state"] == "ready"
        refused = t055.get_citation(alice, citation["chunk_id"])
        assert refused.status == 404, refused.text
        assert refused.json["error"]["code"] == "resource_not_found"
        # No old body, no old citation document, no cached answer text.
        assert "text" not in refused.json
        assert "citation" not in refused.json
        assert "answer" not in refused.json

    # A retry after revocation is a fresh retrieval: empty, not the old answer.
    retried = t064.answer(alice, base_id, QUERY)
    assert retried.status == 200, retried.text
    assert retried.json["outcome"] == t064.OUTCOME_NO_RESULTS
    assert retried.json["answer"]["citations"] == []
    assert retried.json["answer"]["text"] == ""


def test_a_chunk_pointing_at_another_principals_sealed_body_is_never_answered(
    t055_stack,
) -> None:
    stack = t055_stack
    admin = f00b.admin_session(stack)
    alice, base_id, _artifact_id, document_id = setup_ready_kb(stack, "foreign-body")
    bob = f00b.invite_and_activate(stack, admin, "t064.bob.body", password=f00b.USER_PASSWORD)

    # A real ready artifact owned by Bob, and a real chunk row in Alice's ready
    # document that points at it. Only the SQL owner predicate stands between the
    # answer and another principal's sealed bytes.
    bob_artifact_id = t055.prepare_source(stack, bob, b"bob private knowledge bytes\n")
    bob_artifact = t055.artifact_row(stack.dbname, bob_artifact_id)
    assert bob_artifact["state"] == "ready"
    assert bob_artifact["owner_id"] == bob.principal_id

    rows = t055.chunk_rows(stack.dbname, document_id, 1)
    template = rows[0]
    citation = t055.citation_of(template)
    citation["chunk_no"] = 999
    forged_id = f00b.new_uuid()
    t064.insert_forged_chunk(
        stack.dbname,
        chunk_id=forged_id,
        document_id=document_id,
        document_revision=1,
        chunk_no=999,
        body_artifact_id=bob_artifact_id,
        citation=citation,
        template_chunk_id=template["id"],
    )
    assert t064.chunk_row(stack.dbname, forged_id)["body_artifact_id"] == bob_artifact_id
    assert json.loads(t064.chunk_row(stack.dbname, forged_id)["citation"])["chunk_no"] == 999

    retrieved = t055.retrieval_query(alice, base_id, "退款政策", top_k=20)
    assert retrieved.status == 200, retrieved.text
    assert all(item["chunk_id"] != forged_id for item in retrieved.json["items"])

    for caller in (alice, bob):
        refused = t055.get_citation(caller, forged_id)
        assert refused.status == 404, refused.text
        assert "text" not in refused.json

    answered = t064.answer(alice, base_id, "退款政策")
    assert answered.status == 200, answered.text
    assert t064.OUTCOME_ANSWERED == answered.json["outcome"]
    assert all(
        citation["chunk_id"] != forged_id for citation in t064.citations_of(answered.json)
    )
    # Every citation the answer did return is still directly readable by Alice.
    for citation in t064.citations_of(answered.json):
        assert t055.get_citation(alice, citation["chunk_id"]).status == 200


def test_a_generator_that_answers_without_a_citation_is_refused(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    alice, base_id, _artifact_id, _document_id = setup_ready_kb(stack, "fault-uncited")
    normal = t064.answer(alice, base_id, QUERY)
    assert normal.status == 200 and normal.json["outcome"] == t064.OUTCOME_ANSWERED

    with t064.faulted_answer_server(
        api_server, stack, tmp_path / "fault-uncited-certs", "uncited"
    ) as server:
        faulted = t064.login_on(server, "t064.alice", f00b.USER_PASSWORD)
        refused = t064.answer(faulted, base_id, QUERY)

    assert refused.status == 422, refused.text
    error = refused.json["error"]
    assert error["code"] == t064.OUTCOME_WITHOUT_CITATION
    assert error["details"]["outcome"] == t064.OUTCOME_WITHOUT_CITATION
    assert "answer" not in refused.json
    assert "citations" not in refused.json


def test_a_generator_that_invents_a_citation_is_refused(t055_stack, api_server, tmp_path) -> None:
    stack = t055_stack
    alice, base_id, _artifact_id, _document_id = setup_ready_kb(stack, "fault-foreign")

    with t064.faulted_answer_server(
        api_server, stack, tmp_path / "fault-foreign-certs", "foreign-chunk"
    ) as server:
        faulted = t064.login_on(server, "t064.alice", f00b.USER_PASSWORD)
        refused = t064.answer(faulted, base_id, QUERY)

    assert refused.status == 422, refused.text
    error = refused.json["error"]
    assert error["code"] == t064.OUTCOME_CITATION_NOT_FOUND
    assert error["details"]["outcome"] == t064.OUTCOME_CITATION_NOT_FOUND
    assert error["details"]["chunk_id"] == t064.FAULT_FOREIGN_CHUNK_ID
    assert "answer" not in refused.json
    assert "citations" not in refused.json


def test_a_document_revoked_while_the_answer_is_generating_is_refused(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    alice, base_id, _artifact_id, document_id = setup_ready_kb(stack, "revocation-race")
    chunk_id = t055.chunk_rows(stack.dbname, document_id, 1)[0]["id"]

    # `slow=5s` is the documented dev-stub fault: retrieval has completed and the
    # stub holds the draft for five seconds before validation. The revocation is
    # committed inside that window (>4s of margin), so the read-time re-check must
    # refuse instead of returning a citation that can no longer be opened.
    with t064.faulted_answer_server(
        api_server, stack, tmp_path / "fault-slow-certs", "slow=5s"
    ) as server:
        faulted = t064.login_on(server, "t064.alice", f00b.USER_PASSWORD)
        result: dict[str, object] = {}

        def ask() -> None:
            result["response"] = t064.answer(faulted, base_id, QUERY)

        worker = threading.Thread(target=ask, daemon=True)
        worker.start()
        server.wait_for_log("knowledge answer stub: slow fault waiting", timeout=30.0)
        t064.revoke_document(stack.dbname, document_id)
        worker.join(timeout=30.0)
        assert not worker.is_alive(), "the answer request never finished"

    response = result["response"]
    assert response.status == 409, response.text
    error = response.json["error"]
    assert error["code"] == t064.OUTCOME_CITATION_UNREADABLE
    assert error["details"]["outcome"] == t064.OUTCOME_CITATION_UNREADABLE
    assert error["details"]["chunk_id"] == chunk_id
    assert "answer" not in response.json

    # The same source is unreadable through the citation path afterwards.
    assert t055.get_citation(alice, chunk_id).status == 404
    assert t064.answer(alice, base_id, QUERY).json["outcome"] == t064.OUTCOME_NO_RESULTS


def test_answer_request_validation_rejects_bad_bounds_and_unknown_fields(t055_stack) -> None:
    admin = f00b.admin_session(t055_stack)
    alice = f00b.invite_and_activate(t055_stack, admin, "t064.bounds", password=f00b.USER_PASSWORD)
    base_id = t055.create_knowledge_base(alice, "t064 bounds").json["id"]

    # top_k=0 means "use the default" (the retrieval body contract); an explicit
    # out-of-range value is a validation failure on the same field.
    defaulted = t064.answer(alice, base_id, QUERY, top_k=0)
    assert defaulted.status == 200, defaulted.text
    assert defaulted.json["outcome"] == t064.OUTCOME_NO_RESULTS
    assert defaulted.json["retrieval"]["top_k"] == 8

    for top_k in (51, -1):
        refused = t064.answer(alice, base_id, QUERY, top_k=top_k)
        assert refused.status == 422, refused.text
        assert refused.json["error"]["code"] == "validation_failed"
        assert refused.json["error"]["details"]["field"] == "top_k"

    empty = t064.answer(alice, base_id, "   ")
    assert empty.status == 422, empty.text
    assert empty.json["error"]["details"]["field"] == "query"

    unknown = t064.answer(alice, base_id, QUERY, body={"query": QUERY, "citations": []})
    assert unknown.status == 400, unknown.text
    assert unknown.json["error"]["code"] == "invalid_json"
