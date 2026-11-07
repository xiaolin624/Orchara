"""F06/T122 acceptance: the OpenAI-compatible chat-completions adapter.

Every assertion here is observed through a real surface: the run-scoped
capability endpoint over mTLS with a real worker certificate, real PostgreSQL
operations/attempts/receipts, real sealed object bytes, and a real loopback HTTP
provider that records exactly what crossed the wire. The deployer catalog is real
environment JSON; the vendor credential exists only in the API process
environment and in the outbound ``Authorization`` header.

The *real vendor* end-to-end run is not executed: this machine has no approved
vendor endpoint, credential or quota, so that part is recorded as ``blocked`` in
the ticket report and never simulated with the fake provider.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from decimal import Decimal

import pytest

from support import f00b, t024, t025, t039, t041, t044, t122
from support import stack as st
from support.certs import make_stack_certificates, unique_internal_addr


# ---------------------------------------------------------------------------
# the real stack
# ---------------------------------------------------------------------------
def _build_stack(*, tmp_path, fresh_database, go_binaries, api_server, endpoint: bool):
    provider = t122.FakeProvider()
    secret = t122.new_secret()
    try:
        dsn, dbname = fresh_database
        st.migrate_or_fail(go_binaries["admin"], dsn)
        st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)
        object_dir = tmp_path / "objects"
        object_dir.mkdir()
        internal_addr, _port = unique_internal_addr()
        certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
        signing_key = t024.new_signing_key()
        env = t024.api_extra_env(
            object_dir,
            certificates,
            signing_key=signing_key,
            extra=t025.api_extra_env(None),
        )
        # The deployer-registered model catalog. The credential entry names the
        # deployment environment variable; the catalog itself carries no value.
        env.update(t122.catalog_env())
        if endpoint:
            env["ORCHARA_MODEL_ENDPOINT"] = provider.url + "/v1"
            env["ORCHARA_MODEL_SECRET_REF"] = "env:" + t122.SECRET_ENV
            env[t122.SECRET_ENV] = secret
        server = api_server(dsn, extra_env=env)
        stack = t024.T024Stack(
            server=server,
            dsn=dsn,
            dbname=dbname,
            object_dir=object_dir,
            certificates=certificates,
            binaries=go_binaries,
            signing_key=signing_key,
        )
        f00b.wait_for_internal(stack)
        yield t122.T122Stack(stack=stack, provider=provider, secret=secret)
    finally:
        provider.close()


@pytest.fixture
def t122_stack(tmp_path, fresh_database, go_binaries, api_server):
    yield from _build_stack(
        tmp_path=tmp_path,
        fresh_database=fresh_database,
        go_binaries=go_binaries,
        api_server=api_server,
        endpoint=True,
    )


@pytest.fixture
def t122_stack_without_endpoint(tmp_path, fresh_database, go_binaries, api_server):
    yield from _build_stack(
        tmp_path=tmp_path,
        fresh_database=fresh_database,
        go_binaries=go_binaries,
        api_server=api_server,
        endpoint=False,
    )


def claimed_run(harness: t122.T122Stack):
    """Seed the golden owner/model row, register the fake real provider, claim a run."""
    owner = t041.owner(harness.stack)
    t122.register_model(harness.dbname)
    _, run, lease = t041.claimed_run(harness.stack, run_owner=owner)
    return owner, run, lease


def invoke_text(harness: t122.T122Stack, lease: dict, *, operation_id: str, logical_key: str = "call-1"):
    worker = t041.worker_client(harness.stack)
    return t041.invoke(
        worker,
        lease,
        operation_id=operation_id,
        logical_key=logical_key,
        params=t041.model_params(
            messages=[{"role": "user", "content": "Summarize the sales file."}]
        ),
    )


def assert_no_secret(harness: t122.T122Stack, *rendered: str) -> None:
    for text in rendered:
        assert harness.secret not in text, "the vendor credential leaked into an observable surface"
        assert "Bearer " + harness.secret not in text


# ---------------------------------------------------------------------------
# AC1: one real HTTP call, the response mapped and the result sealed
# ---------------------------------------------------------------------------
def test_chat_completion_is_dispatched_metered_and_sealed(t122_stack) -> None:
    owner, run, lease = claimed_run(t122_stack)
    t122_stack.provider.respond_once(content="the vendor answer", usage=(11, 7))
    operation_id = t041.new_uuid()

    response = invoke_text(t122_stack, lease, operation_id=operation_id)
    assert response.status == 200, response.text
    body = response.json
    assert body["state"] == "succeeded"
    data = body["data"]
    assert data["stub"] is False
    assert data["text"] == "the vendor answer"
    assert data["tool_calls"] == []
    assert data["usage"] == {"input_tokens": 11, "output_tokens": 7}
    assert data["model_profile_version_id"] == t041.GOLDEN_MODEL_ID

    # Exactly one physical request crossed the wire, addressed by the documented
    # append rule, carrying the registered model id, messages and the credential
    # only in the Authorization header.
    requests = t122_stack.provider.requests()
    assert len(requests) == 1, requests
    request = requests[0]
    assert request["method"] == "POST"
    assert request["path"] == "/v1/chat/completions"
    assert request["headers"]["authorization"] == "Bearer " + t122_stack.secret
    assert request["body"]["model"] == t122.MODEL_ID
    assert [message["content"] for message in request["body"]["messages"]] == [
        "Summarize the sales file."
    ]
    for name, value in request["headers"].items():
        if name == "authorization":
            continue
        assert t122_stack.secret not in value, name

    attempts = t041.attempt_rows(t122_stack.dbname, operation_id)
    assert len(attempts) == 1, attempts
    assert attempts[0]["state"] == "succeeded"
    assert attempts[0]["tokens_in"] == 11 and attempts[0]["tokens_out"] == 7
    # 11 input tokens at 1.00/million + 7 output tokens at 2.00/million.
    assert Decimal(attempts[0]["charged_amount"]) == Decimal("0.00002500")
    assert attempts[0]["settlement_state"] == "settled"

    receipt = t041.receipt_document(t122_stack.stack, body["receipt_artifact_id"])
    assert receipt["text"] == "the vendor answer"
    assert receipt["stub"] is False
    artifact = t041.artifact_row(t122_stack.dbname, body["receipt_artifact_id"])
    sealed = t122.sealed_bytes(t122_stack.stack, body["receipt_artifact_id"])
    assert artifact["sha256"] == hashlib.sha256(sealed).hexdigest()
    assert artifact["size_bytes"] == len(sealed)

    ledger = t041.ledger_items(owner.creator, run["id"])
    assert len(ledger) == 1, ledger
    assert ledger[0]["stub"] is False
    assert ledger[0]["tokens_in"] == 11 and ledger[0]["tokens_out"] == 7
    assert Decimal(ledger[0]["charged_amount"]) == Decimal("0.00002500")
    # One reproducible evidence line: the exact artifact hash, the params hash
    # and the metered usage of this real-provider call.
    print("T122-EVIDENCE " + json.dumps({
        "git_head": os.environ.get("T122_GIT_HEAD", "n/a"),
        "profile": "dev-stub + explicit ORCHARA_MODEL_ENDPOINT (loopback fake provider)",
        "provider": t122.PROVIDER,
        "model_id": t122.MODEL_ID,
        "model_profile_version_id": t041.GOLDEN_MODEL_ID,
        "receipt_artifact_sha256": artifact["sha256"],
        "receipt_size_bytes": artifact["size_bytes"],
        "params_sha256": t041.operation_rows(t122_stack.dbname, run["id"])[0]["params_sha256"],
        "tokens_in": attempts[0]["tokens_in"],
        "tokens_out": attempts[0]["tokens_out"],
        "charged_amount": attempts[0]["charged_amount"],
        "price_version": attempts[0]["price_version"],
    }, sort_keys=True))
    assert_no_secret(
        t122_stack,
        response.text,
        json.dumps(receipt),
        json.dumps(ledger),
        t122_stack.server.logs(),
    )


def test_tool_calls_and_structured_output_are_mapped(t122_stack) -> None:
    t041.set_tool_schema(t122_stack.dbname, t041.CITY_SCHEMA)
    _, _, lease = claimed_run(t122_stack)
    t122_stack.provider.respond_once(
        content='{"answer":"42"}',
        tool_calls=[{
            "id": "call_1",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"city":"Paris"}'},
        }],
        usage=(5, 3),
    )
    worker = t041.worker_client(t122_stack.stack)
    response = t041.invoke(
        worker,
        lease,
        operation_id=t041.new_uuid(),
        params=t041.model_params(
            tools=[{"name": "lookup", "capability_version": t041.GOLDEN_TOOL_ID}],
            response_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["answer"],
                "properties": {"answer": {"type": "string"}},
            },
        ),
    )
    assert response.status == 200, response.text
    data = response.json["data"]
    assert data["tool_calls"] == [
        {"id": "call_1", "name": "lookup", "arguments": {"city": "Paris"}}
    ]
    assert data["structured"] == {"answer": "42"}
    assert data["stub"] is False

    request = t122_stack.provider.requests()[0]
    tools = request["body"]["tools"]
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "lookup"
    assert request["body"]["response_format"]["type"] == "json_schema"
    # The internal capability_version is a platform reference, not wire data.
    assert t041.GOLDEN_TOOL_ID not in json.dumps(request["body"])


# ---------------------------------------------------------------------------
# AC2: streaming maps deltas/finish/usage and never concatenates a half answer
# ---------------------------------------------------------------------------
def test_model_stream_maps_deltas_and_seals_once(t122_stack) -> None:
    _, run, lease = claimed_run(t122_stack)
    t122_stack.provider.respond_stream([
        t122.stream_frame(content="Hel"),
        t122.stream_frame(content="lo"),
        t122.stream_frame(content="", finish_reason="stop"),
        t122.stream_frame(usage=(9, 4)),
    ])
    operation_id = t041.new_uuid()
    response = t044.stream(
        t041.worker_client(t122_stack.stack),
        lease,
        operation_id=operation_id,
        params=t041.model_params(),
    )
    assert response.status == 200, response.text
    assert "text/event-stream" in (response.header("Content-Type") or "")
    frames = t044.parse_sse(response.text)
    assert [frame["type"] for frame in frames] == ["delta", "delta", "final"], frames
    assert t044.delta_text(frames) == "Hello"
    final = t044.finals(frames)[0]
    assert final["data"]["stub"] is False
    assert final["data"]["text"] == "Hello"
    assert final["data"]["tool_calls"] == []
    assert final["data"]["usage"] == {"input_tokens": 9, "output_tokens": 4}
    assert final["usage"] == final["data"]["usage"]

    attempts = t041.attempt_rows(t122_stack.dbname, operation_id)
    assert len(attempts) == 1 and attempts[0]["state"] == "succeeded"
    assert attempts[0]["tokens_in"] == 9 and attempts[0]["tokens_out"] == 4
    assert Decimal(attempts[0]["charged_amount"]) == Decimal("0.00001700")

    receipt = t041.receipt_document(t122_stack.stack, final["receipt_artifact_id"])
    assert receipt["text"] == "Hello" and receipt["stub"] is False
    requests = t122_stack.provider.requests()
    assert len(requests) == 1, requests
    assert requests[0]["body"]["stream"] is True
    assert requests[0]["body"]["stream_options"] == {"include_usage": True}
    assert requests[0]["headers"]["accept"] == "text/event-stream"


def test_half_stream_is_transient_and_never_seals_a_partial_answer(t122_stack) -> None:
    _, run, lease = claimed_run(t122_stack)
    provider = t122_stack.provider
    first = [t122.stream_frame(content="partial-1"), t122.stream_frame(content="partial-2")]
    second = [
        t122.stream_frame(content="Hel"),
        t122.stream_frame(content="lo"),
        t122.stream_frame(content="", finish_reason="stop"),
        t122.stream_frame(usage=(9, 4)),
    ]
    provider.respond_sequence([
        provider.stream_responder(first, abort_after=2),
        provider.stream_responder(second),
    ])
    operation_id = t041.new_uuid()
    response = t044.stream(
        t041.worker_client(t122_stack.stack),
        lease,
        operation_id=operation_id,
        params=t041.model_params(),
    )
    assert response.status == 200, response.text
    frames = t044.parse_sse(response.text)
    assert [frame["type"] for frame in frames] == [
        "delta", "delta", "error", "delta", "delta", "final",
    ], frames
    generations = t044.generation_ids(frames)
    assert len(generations) == 2, generations
    assert frames[2]["error"]["code"] == "provider_transient"
    assert frames[2]["retryable"] is True
    final = t044.finals(frames)[0]
    assert final["generation_id"] == generations[1]
    assert final["data"]["text"] == "Hello"
    assert final["data"]["text"] != t044.delta_text(frames, generations[0]) + "Hello"

    attempts = t041.attempt_rows(t122_stack.dbname, operation_id)
    assert len(attempts) == 2, attempts
    assert [row["state"] for row in attempts] == ["failed", "succeeded"]
    assert attempts[0]["error_code"] == "provider_transient"
    assert attempts[0]["result_artifact_id"] is None
    assert attempts[0]["id"] == generations[0] and attempts[1]["id"] == generations[1]
    assert t044.receipt_artifact_count(t122_stack.dbname, run["id"]) == 1
    receipt = t041.receipt_document(t122_stack.stack, final["receipt_artifact_id"])
    assert receipt["text"] == "Hello"
    assert "partial" not in receipt["text"]
    assert len(t122_stack.provider.requests()) == 2


# ---------------------------------------------------------------------------
# AC3: retry classification (429/5xx retried and bounded, 4xx never retried)
# ---------------------------------------------------------------------------
def test_429_is_retried_within_the_three_attempt_ceiling(t122_stack) -> None:
    _, _, lease = claimed_run(t122_stack)
    provider = t122_stack.provider
    provider.respond_sequence([
        t122.status_responder(429),
        t122.status_responder(429),
        lambda _record, _handler: provider.completion(content="recovered", usage=(4, 2)),
    ])
    operation_id = t041.new_uuid()
    response = invoke_text(t122_stack, lease, operation_id=operation_id)
    assert response.status == 200, response.text
    assert response.json["data"]["text"] == "recovered"
    assert len(provider.requests()) == 3

    attempts = t041.attempt_rows(t122_stack.dbname, operation_id)
    assert [row["attempt_no"] for row in attempts] == [1, 2, 3]
    assert [row["state"] for row in attempts] == ["failed", "failed", "succeeded"]
    assert attempts[0]["error_code"] == "provider_transient"
    assert attempts[1]["error_code"] == "provider_transient"
    assert attempts[2]["charged_amount"] is not None


def test_5xx_exhausts_the_bounded_retries_and_fails_honestly(t122_stack) -> None:
    _, run, lease = claimed_run(t122_stack)
    t122_stack.provider.respond_status(503)
    operation_id = t041.new_uuid()
    response = invoke_text(t122_stack, lease, operation_id=operation_id)
    assert response.status == 503, response.text
    assert response.json["error"]["code"] == "provider_transient"

    assert len(t122_stack.provider.requests()) == 3
    attempts = t041.attempt_rows(t122_stack.dbname, operation_id)
    assert len(attempts) == 3
    assert [row["state"] for row in attempts] == ["failed", "failed", "failed"]
    assert all(row["error_code"] == "provider_transient" for row in attempts)
    assert all(row["charged_amount"] is None for row in attempts)
    operations = t041.operation_rows(t122_stack.dbname, run["id"])
    assert len(operations) == 1
    assert operations[0]["state"] == "failed"
    assert operations[0]["last_error_code"] == "provider_transient"
    assert t044.receipt_artifact_count(t122_stack.dbname, run["id"]) == 0


def test_401_is_not_retried_and_never_echoes_the_credential(t122_stack) -> None:
    owner, run, lease = claimed_run(t122_stack)
    t122_stack.provider.respond_status(
        401, body=json.dumps({"error": {"message": "invalid key " + t122_stack.secret}})
    )
    operation_id = t041.new_uuid()
    response = invoke_text(t122_stack, lease, operation_id=operation_id)
    assert response.status == 503, response.text
    assert response.json["error"]["code"] == "provider_permission_denied"

    assert len(t122_stack.provider.requests()) == 1
    attempts = t041.attempt_rows(t122_stack.dbname, operation_id)
    assert len(attempts) == 1
    assert attempts[0]["state"] == "failed"
    assert attempts[0]["error_code"] == "provider_permission_denied"
    ledger = t041.ledger_items(owner.creator, run["id"])
    assert_no_secret(t122_stack, response.text, json.dumps(ledger), t122_stack.server.logs())


# ---------------------------------------------------------------------------
# AC5: malformed and oversized protocol answers are terminal, not retried
# ---------------------------------------------------------------------------
def test_malformed_and_oversized_answers_are_terminal(t122_stack) -> None:
    _, _, lease = claimed_run(t122_stack)
    malformed_id = t041.new_uuid()
    t122_stack.provider.respond_raw(200, b"{not json")
    malformed = invoke_text(t122_stack, lease, operation_id=malformed_id)
    assert malformed.status == 503, malformed.text
    assert malformed.json["error"]["code"] == "provider_response_invalid"
    assert len(t122_stack.provider.requests()) == 1
    malformed_attempts = t041.attempt_rows(t122_stack.dbname, malformed_id)
    assert len(malformed_attempts) == 1 and malformed_attempts[0]["state"] == "failed"

    oversized_id = t041.new_uuid()
    giant = b'{"choices":[{"message":{"content":"' + b"a" * (8 * 1024 * 1024 + 1) + b'"}}]}'
    t122_stack.provider.respond_raw(200, giant)
    oversized = invoke_text(t122_stack, lease, operation_id=oversized_id, logical_key="call-2")
    assert oversized.status == 503, oversized.text
    assert oversized.json["error"]["code"] == "provider_response_too_large"
    assert len(t122_stack.provider.requests()) == 2
    oversized_attempts = t041.attempt_rows(t122_stack.dbname, oversized_id)
    assert len(oversized_attempts) == 1 and oversized_attempts[0]["state"] == "failed"


# ---------------------------------------------------------------------------
# AC4: fail closed without an approved destination or a resolvable credential
# ---------------------------------------------------------------------------
def test_unregistered_destination_and_unresolvable_credential_fail_closed(t122_stack) -> None:
    owner = t041.owner(t122_stack.stack)
    t122.register_model(t122_stack.dbname, endpoint_ref="t122-rogue-endpoint")
    _, run, lease = t041.claimed_run(t122_stack.stack, run_owner=owner)

    destination_id = t041.new_uuid()
    destination = invoke_text(t122_stack, lease, operation_id=destination_id)
    assert destination.status == 503, destination.text
    assert destination.json["error"]["code"] == "provider_destination_not_approved"
    assert t122.operation_count(t122_stack.dbname, run["id"]) == 0
    assert t122_stack.provider.requests() == []

    # A registered credential whose deployment environment is unset is still an
    # unconfigured provider: fail closed, no ledger row, no request.
    t122.register_model(
        t122_stack.dbname,
        endpoint_ref=t122.ENDPOINT_ID,
        secret_ref=t122.MISSING_CREDENTIAL_ID,
    )
    credential_id = t041.new_uuid()
    credential = invoke_text(t122_stack, lease, operation_id=credential_id)
    assert credential.status == 503, credential.text
    assert credential.json["error"]["code"] == "provider_not_configured"
    assert t122.operation_count(t122_stack.dbname, run["id"]) == 0
    assert t122_stack.provider.requests() == []

    # A protocol this build does not speak is adapter_unavailable, never a call.
    t122.register_model(
        t122_stack.dbname,
        config_value=t122.model_config(protocol="embedding_compatible", kind="embedding"),
    )
    protocol_id = t041.new_uuid()
    protocol = invoke_text(t122_stack, lease, operation_id=protocol_id)
    assert protocol.status == 503, protocol.text
    assert protocol.json["error"]["code"] == "adapter_unavailable"
    assert t122.operation_count(t122_stack.dbname, run["id"]) == 0
    assert t122_stack.provider.requests() == []


def test_missing_endpoint_is_provider_not_configured_without_a_ledger_row(
    t122_stack_without_endpoint,
) -> None:
    harness = t122_stack_without_endpoint
    # The golden owner seeds the model profile row; the registration then points
    # it at the fake real provider before the run is compiled.
    owner = t041.owner(harness.stack)
    t122.register_model(harness.dbname)
    _, run, lease = t041.claimed_run(harness.stack, run_owner=owner)
    operation_id = t041.new_uuid()
    response = invoke_text(harness, lease, operation_id=operation_id)
    assert response.status == 503, response.text
    assert response.json["error"]["code"] == "provider_not_configured"
    assert t122.operation_count(harness.dbname, run["id"]) == 0
    assert t041.attempt_rows(harness.dbname, operation_id) == []
    assert harness.provider.requests() == []


# ---------------------------------------------------------------------------
# administrator registration -> validation flow -> real provider call
# ---------------------------------------------------------------------------
def test_admin_registered_real_model_validates_through_the_provider(t122_stack) -> None:
    harness = t122_stack
    admin = f00b.admin_session(harness.stack)
    harness.provider.respond_once(content='{"ok":true}', usage=(4, 2))
    model = t039.register_or_fail(
        admin,
        provider=t122.PROVIDER,
        model_id=t122.MODEL_ID,
        endpoint_ref=t122.ENDPOINT_ID,
        secret_ref=t122.CREDENTIAL_ID,
        data_destination=t122.DESTINATION,
        config=t039.config(),
    )
    accepted = t039.validate(admin, model["id"], key="t122-" + secrets.token_hex(8))
    assert accepted.status == 202, accepted.text

    completed, reports, finalizer_log = t039.run_validation(harness.stack)
    assert completed.returncode == 0, (
        f"maintenance failed:\n{completed.stdout}\n{completed.stderr}\n{finalizer_log}"
    )
    job_reports = f00b.job_reports(reports)
    assert len(job_reports) == 1, job_reports
    assert job_reports[0]["result"] == "succeeded", job_reports

    calls = t024.model_calls(harness.dbname, job_reports[0]["job_id"])
    assert len(calls) == 1, calls
    assert calls[0]["state"] == "succeeded"
    assert calls[0]["tokens_in"] == 4 and calls[0]["tokens_out"] == 2

    requests = harness.provider.requests()
    assert len(requests) == 1, requests
    assert requests[0]["body"]["model"] == t122.MODEL_ID
    assert requests[0]["body"]["response_format"] == {"type": "json_object"}
    assert requests[0]["headers"]["authorization"] == "Bearer " + harness.secret

    got = t039.get_model(admin, model["id"])
    assert got.status == 200, got.text
    assert got.json["validation_state"] == "passed", got.json
    assert got.json["evaluation_report_id"], got.json
    document = t039.report_document(harness.stack, got.json["evaluation_report_id"])
    assert document["compatible"] is True, document
    assert document["stub"] is False, document
    assert document["check"] == {
        "name": "structured_output", "passed": True, "reason": "",
    }, document["check"]
    assert_no_secret(harness, json.dumps(document), harness.server.logs())
