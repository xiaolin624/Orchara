"""T027: Idempotency-Key semantics for debug submission.

One key identifies one canonical request: the same key + the same request replays
the stored 202 Run body without creating a row, the same key + a different request
is 409, and no key is 400. A committed receipt is the operation's permanent
receipt, so an expired key still replays instead of executing twice.
"""

from __future__ import annotations

import http.client
import json
import secrets
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from support import stack as st
from support import t027
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t027_stack,
)


def owner(t027_ctx):
    admin = t027.admin_session(t027_ctx)
    login = "t027-idem-" + secrets.token_hex(4)
    creator = t027.invite_and_activate(t027_ctx, admin, login, roles=("creator",))
    application_id = t027.seed_golden_draft(t027_ctx.dbname, creator.principal_id)
    return admin, creator, application_id, login


def receipts(t027_ctx, application_id: str) -> list[dict]:
    return t027.idempotency_rows(t027_ctx.dbname, t027.debug_path(application_id))


def sealed_files(t027_ctx) -> set[str]:
    return {entry.name for entry in (t027_ctx.object_dir / "sealed").iterdir()}


def test_replaying_the_same_key_and_body_returns_the_same_run(t027_stack) -> None:
    _admin, creator, application_id, _login = owner(t027_stack)
    key = t027.new_idempotency_key()
    body = t027.debug_body(input_value={"text": "hello"})
    first = t027.submit_debug(creator, application_id, key=key, input_value={"text": "hello"})
    assert first.status == 202, first.text
    run = first.json
    counts = t027.execution_rows(t027_stack.dbname, application_id)
    sealed_before = sealed_files(t027_stack)
    run_row = t027.run_row(t027_stack.dbname, run["id"])
    input_bytes = t027.object_bytes(
        t027_stack.object_dir,
        t027.artifact_row(t027_stack.dbname, run_row["input_artifact_id"])["object_key"],
    )

    replay = t027.submit_debug(creator, application_id, key=key, input_value={"text": "hello"})
    assert replay.status == 202, replay.text
    assert replay.json == first.json
    assert replay.header("X-Manifest-SHA256") == first.header("X-Manifest-SHA256")
    assert t027.execution_rows(t027_stack.dbname, application_id) == counts
    assert t027.execution_rows(t027_stack.dbname, application_id)["snapshots"] == 1
    # A replay does not even write a throwaway input object.
    assert sealed_files(t027_stack) == sealed_before
    assert t027.run_row(t027_stack.dbname, run["id"]) == run_row

    stored = receipts(t027_stack, application_id)
    assert len(stored) == 1, stored
    assert json.loads(stored[0]["response_json"]) == first.json
    # The body the test sent is the one the digest covers.
    assert body["draft_revision"] == 1

    # Re-reading the run after the replay is the same run.
    assert t027.get_run(creator, run["id"]).json == first.json


def test_a_replay_returns_the_original_run_even_after_the_draft_moved_on(t027_stack) -> None:
    _admin, creator, application_id, _login = owner(t027_stack)
    key = t027.new_idempotency_key()
    first = t027.submit_debug(creator, application_id, key=key)
    assert first.status == 202, first.text
    run = first.json

    draft = t027.get_draft(creator, application_id)
    saved = t027.save_draft(
        creator,
        application_id,
        draft.header("ETag"),
        definition=t027.p05_case("anchor-minimal-linear"),
        capability_card=t027.full_card(),
    )
    assert saved.status == 200, saved.text

    # The receipt describes an already-authorized operation: replay wins over the
    # current-revision rule and returns the frozen original run.
    replay = t027.submit_debug(creator, application_id, key=key, draft_revision=1)
    assert replay.status == 202, replay.text
    assert replay.json == run
    assert t027.execution_rows(t027_stack.dbname, application_id)["snapshots"] == 1

    # A *new* key naming the old revision is refused: an old revision cannot be
    # debugged again now that the draft moved on.
    stale = t027.submit_debug(creator, application_id, draft_revision=1)
    error = st.assert_error(stale, 409, "revision_conflict")
    assert error["details"]["current_revision"] == 2, error
    assert t027.execution_rows(t027_stack.dbname, application_id)["snapshots"] == 1


def test_the_same_key_with_a_different_request_is_409(t027_stack) -> None:
    _admin, creator, application_id, _login = owner(t027_stack)
    key = t027.new_idempotency_key()
    first = t027.submit_debug(creator, application_id, key=key, input_value={"text": "hello"})
    assert first.status == 202, first.text
    counts = t027.execution_rows(t027_stack.dbname, application_id)

    changed_input = t027.submit_debug(
        creator, application_id, key=key, input_value={"text": "different"}
    )
    error = st.assert_error(changed_input, 409, "idempotency_conflict")
    assert error["details"] == {"header": "Idempotency-Key"}, error

    changed_revision = t027.submit_debug(creator, application_id, key=key, draft_revision=2)
    st.assert_error(changed_revision, 409, "idempotency_conflict")
    assert t027.execution_rows(t027_stack.dbname, application_id) == counts
    assert len(receipts(t027_stack, application_id)) == 1


def test_artifact_order_is_part_of_the_same_canonical_request(t027_stack) -> None:
    _admin, creator, application_id, _login = owner(t027_stack)
    first_artifact = t027.seed_artifact(t027_stack.dbname, creator.principal_id, content=b'{"a":1}')
    second_artifact = t027.seed_artifact(t027_stack.dbname, creator.principal_id, content=b'{"b":2}')
    key = t027.new_idempotency_key()
    first = t027.submit_debug(
        creator, application_id, key=key, artifact_ids=[first_artifact, second_artifact]
    )
    assert first.status == 202, first.text
    replay = t027.submit_debug(
        creator, application_id, key=key, artifact_ids=[second_artifact, first_artifact]
    )
    assert replay.status == 202, replay.text
    assert replay.json == first.json
    assert t027.execution_rows(t027_stack.dbname, application_id)["snapshots"] == 1


def test_the_idempotency_key_header_is_required_and_unambiguous(t027_stack) -> None:
    _admin, creator, application_id, _login = owner(t027_stack)
    path = t027.debug_path(application_id)
    body = json.dumps(t027.debug_body())

    missing = creator.client.post(
        path, raw_body=body, csrf=creator.csrf
    )
    st.assert_error(missing, 400, "idempotency_key_required")

    empty = creator.client.post(
        path, raw_body=body, csrf=creator.csrf, headers={"Idempotency-Key": "   "}
    )
    st.assert_error(empty, 400, "idempotency_key_required")

    duplicate = _post_with_two_idempotency_headers(t027_stack, creator, path, body)
    st.assert_error(duplicate, 400, "idempotency_key_required")

    t027.no_execution_rows(t027_stack.dbname, application_id)


def _post_with_two_idempotency_headers(t027_stack, session, path: str, body: str):
    """Send the same header twice through http.client (urllib cannot)."""
    parsed = urllib.parse.urlsplit(t027_stack.server.base_url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=30)
    try:
        connection.putrequest("POST", path)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Cookie", f"{st.SESSION_COOKIE}={session.cookie}")
        connection.putheader("X-CSRF-Token", session.csrf)
        connection.putheader("Idempotency-Key", "t027-first")
        connection.putheader("Idempotency-Key", "t027-second")
        encoded = body.encode("utf-8")
        connection.putheader("Content-Length", str(len(encoded)))
        connection.endheaders(encoded)
        response = connection.getresponse()
        payload = response.read()
        return st.Response(response.status, response.msg, payload)
    finally:
        connection.close()


def test_a_concurrent_duplicate_submission_creates_exactly_one_run(t027_stack) -> None:
    admin, creator, application_id, login = owner(t027_stack)
    # A second real session for the same principal (the receipt is principal-,
    # path- and method-scoped, not session-scoped).
    second_session = t027.session_for(t027_stack, login, t027.USER_PASSWORD)
    key = t027.new_idempotency_key()

    # Deterministic overlap: the trigger sleeps inside the first inserting
    # transaction, so the second submission really blocks on the unique receipt
    # key instead of racing in-process.
    st.sql(
        t027_stack.dbname,
        "CREATE OR REPLACE FUNCTION platform.t027_slow_receipt() RETURNS trigger AS $$ "
        "BEGIN PERFORM pg_sleep(0.75); RETURN NEW; END; $$ LANGUAGE plpgsql;",
    )
    st.sql(
        t027_stack.dbname,
        "CREATE TRIGGER t027_slow_receipt BEFORE INSERT ON platform.idempotency_keys "
        "FOR EACH ROW EXECUTE FUNCTION platform.t027_slow_receipt();",
    )
    barrier = threading.Barrier(2)

    def submit(session) -> st.Response:
        barrier.wait(timeout=30)
        return t027.submit_debug(session, application_id, key=key)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, creator), pool.submit(submit, second_session)]
            responses = [future.result(timeout=60) for future in futures]
    finally:
        st.sql(
            t027_stack.dbname,
            "DROP TRIGGER IF EXISTS t027_slow_receipt ON platform.idempotency_keys",
        )

    for response in responses:
        assert response.status == 202, response.text
    assert responses[0].json["id"] == responses[1].json["id"]
    counts = t027.execution_rows(t027_stack.dbname, application_id)
    assert counts["snapshots"] == 1 and counts["runs"] == 1 and counts["jobs"] == 1, counts
    assert len(receipts(t027_stack, application_id)) == 1
    assert t027.count(t027_stack.dbname, "artifacts", "purpose = 'run_input'") == 1


def test_an_expired_receipt_still_replays_and_never_executes_twice(t027_stack) -> None:
    _admin, creator, application_id, _login = owner(t027_stack)
    key = t027.new_idempotency_key()
    first = t027.submit_debug(creator, application_id, key=key)
    assert first.status == 202, first.text

    # API.md §1 recommends 24h; expiry is cleanup metadata, not permission to
    # repeat an operation. F07 owns deletion of old receipts.
    st.sql(
        t027_stack.dbname,
        "UPDATE platform.idempotency_keys SET expires_at = now() - interval '1 hour' "
        f"WHERE path = {st.quote_literal(t027.debug_path(application_id))}",
    )
    replay = t027.submit_debug(creator, application_id, key=key)
    assert replay.status == 202, replay.text
    assert replay.json == first.json
    assert t027.execution_rows(t027_stack.dbname, application_id)["snapshots"] == 1
