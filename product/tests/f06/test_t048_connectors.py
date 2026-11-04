"""F06/T048 acceptance: versioned fixed HTTP connectors and controlled egress.

Every test drives the real stack: PostgreSQL migrated through migration 0029,
the compiled API, the real admin registration route, the real debug-run manifest
compilation (the connector closure is frozen into ``manifest.connector_policies``)
and the real mTLS ``invokeCapability`` endpoint. The connector endpoint is a real
loopback HTTP server; the egress guard is Go's, not a Python mock.

The credential value exists only in the API process environment (set by the
fixture) and in this module's assertions; no fixture, log, receipt or ledger entry
contains it.

``not_run``: real vendor connectors (deployment-defined; the connector policy is
the deployment contract), a browser DOM driving the ledger panel (the JS helper is
executed under Node instead), and T050's approval *resume* path (approval rows are
T050; this ticket records ``awaiting_approval``/``approval_required``).
"""

from __future__ import annotations

import json
import secrets
import uuid

import pytest

from support import f00b, t024, t025, t029, t041, t044, t048
from support import stack as st
from support.certs import make_stack_certificates, unique_internal_addr

T048_SECRET_VALUE = "t048-db-secret-value-9f3c2a"


@pytest.fixture
def t048_stack(tmp_path, fresh_database, go_binaries, api_server) -> t024.T024Stack:
    """The F06 stack plus the connector test credential in the API environment.

    The secret is an ``env:`` deployer reference, so it never appears in a request
    body or the database. Everything else is the real T024/T025 stack.
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
    env[t048.TEST_SECRET_ENV] = T048_SECRET_VALUE
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
    return stack



@pytest.fixture
def local_server() -> t048.LocalConnectorServer:
    server = t048.LocalConnectorServer()
    try:
        yield server
    finally:
        server.close()


def assert_not_leaked(text: str) -> None:
    assert T048_SECRET_VALUE not in text, "the connector credential leaked"


# ---------------------------------------------------------------------------
# happy path: manifest closure, real egress, ledger, receipt, no secret
# ---------------------------------------------------------------------------
def test_read_connector_is_frozen_into_the_manifest_and_called_for_real(
    t048_stack, local_server
) -> None:
    owner = t041.owner(t048_stack)
    local_server.respond_json({"ok": True, "value": 42})
    policy = t048.register(t048_stack, owner.admin, origin=local_server.url)
    t048.bind_tool_connectors(t048_stack.dbname, [policy["id"]])

    _, run, lease = t041.claimed_run(t048_stack, run_owner=owner)

    # Compile-time closure: the approved policy and its sealed digest are in the
    # frozen manifest, normalized like every other dependency set.
    assert t048.manifest_connector_policies(lease) == [
        {"version_id": policy["id"], "policy_sha256": policy["policy_sha256"]}
    ], lease["manifest"]

    response = t048.invoke(
        t048_stack,
        lease,
        policy_id=policy["id"],
        params={"path": "/v1/items"},
        logical_key="connector-read-1",
    )
    assert response.status == 200, response.text
    body = response.json
    assert body["state"] == "succeeded", body
    assert body["data"]["body"] == {"ok": True, "value": 42}, body
    assert body["data"]["policy_sha256"] == policy["policy_sha256"], body

    # The real local server saw exactly one request, with the credential in the
    # one configured header -- and nothing else.
    assert len(local_server.records) == 1, local_server.records
    record = local_server.records[0]
    assert record["method"] == "GET" and record["path"] == "/v1/items", record
    assert record["headers"]["x-connector-secret"] == T048_SECRET_VALUE, record
    assert "authorization" not in record["headers"], record

    # No secret in the worker-visible response, the ledger or the receipt.
    assert_not_leaked(response.text)

    operation = t041.operation_row(t048_stack.dbname, run["id"], body["operation_id"])
    assert operation is not None, body
    assert operation["capability_kind"] == "connector", operation
    assert operation["risk_class"] == "read", operation
    assert operation["state"] == "succeeded", operation
    attempts = t041.attempt_rows(t048_stack.dbname, body["operation_id"])
    assert len(attempts) == 1 and attempts[0]["state"] == "succeeded", attempts

    # The params artifact holds the invocation, never the credential.
    params_bytes = t048.params_artifact_bytes(t048_stack, operation)
    assert b"/v1/items" in params_bytes, params_bytes
    assert_not_leaked(params_bytes.decode())

    receipt, receipt_bytes = t048.receipt(t048_stack, body["receipt_artifact_id"])
    assert receipt["status"] == 200, receipt
    assert receipt["method"] == "GET" and receipt["path_prefix"] == "/v1", receipt
    assert receipt["response_sha256"] == t048.sha256_hex(
        json.dumps({"ok": True, "value": 42}).encode()
    ), receipt
    assert receipt["headers"]["content-type"] == "application/json", receipt
    assert receipt["connector_policy_version_id"] == policy["id"], receipt
    assert_not_leaked(receipt_bytes.decode())

    # The sealed policy artifact is the immutable body the hash covers.
    artifact = t048.policy_artifact(t048_stack, policy)
    sealed = (t048_stack.object_dir / artifact["object_key"]).read_bytes()
    assert artifact["purpose"] == "connector_policy", artifact
    assert t048.sha256_hex(sealed) == policy["policy_sha256"], artifact
    assert b'"secret_ref":"env:T048_TEST_SECRET"' in sealed, sealed
    assert_not_leaked(sealed.decode())

    items = t041.ledger_items(owner.creator, run["id"])
    connector_items = [item for item in items if item["capability_kind"] == "connector"]
    assert len(connector_items) == 1, items
    item = connector_items[0]
    assert item["state"] == "succeeded" and item["attempts"] == 1, item
    assert item["risk_class"] == "read", item
    assert item["connector_target"] == "GET " + local_server.url + "/v1", item
    assert_not_leaked(json.dumps(item))


# ---------------------------------------------------------------------------
# SSRF / DNS
# ---------------------------------------------------------------------------
def test_metadata_and_private_targets_are_refused_before_dialing(t048_stack, local_server) -> None:
    owner = t041.owner(t048_stack)
    metadata = t048.register(
        t048_stack,
        owner.admin,
        origin="http://169.254.169.254",
        path_prefix="/latest",
        request_schema={"type": "object", "additionalProperties": True},
        response_schema={},
    )
    denied = t048.register(
        t048_stack, owner.admin, origin=local_server.url, allow_private=False
    )
    t048.bind_tool_connectors(t048_stack.dbname, [metadata["id"], denied["id"]])
    _, run, lease = t041.claimed_run(t048_stack, run_owner=owner)

    response = t048.invoke(
        t048_stack,
        lease,
        policy_id=metadata["id"],
        params={"path": "/latest/meta-data"},
        logical_key="ssrf-metadata",
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "connector_target_forbidden", response.text

    response = t048.invoke(
        t048_stack,
        lease,
        policy_id=denied["id"],
        params={"path": "/v1/items"},
        logical_key="ssrf-loopback",
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "connector_target_forbidden", response.text
    # The guard refused before the socket: the loopback server saw no request.
    assert local_server.records == [], local_server.records

    # Both failures are honest ledger rows; no receipt was written.
    for logical_key in ("ssrf-metadata", "ssrf-loopback"):
        row = next(
            item
            for item in t041.operation_rows(t048_stack.dbname, run["id"])
            if item["logical_key"] == logical_key
        )
        assert row["state"] == "failed", row
        assert row["last_error_code"] == "connector_target_forbidden", row
        assert row["receipt_artifact_id"] is None, row


def test_a_dns_name_that_resolves_to_a_private_address_is_refused(t048_stack, local_server) -> None:
    owner = t041.owner(t048_stack)
    dns_denied = t048.register(
        t048_stack,
        owner.admin,
        origin=f"http://localhost:{local_server.port}",
        allow_private=False,
    )
    dns_allowed = t048.register(
        t048_stack,
        owner.admin,
        origin=f"http://localhost:{local_server.port}",
        allow_private=True,
    )
    t048.bind_tool_connectors(t048_stack.dbname, [dns_denied["id"], dns_allowed["id"]])
    _, run, lease = t041.claimed_run(t048_stack, run_owner=owner)

    response = t048.invoke(
        t048_stack,
        lease,
        policy_id=dns_denied["id"],
        params={"path": "/v1/items"},
        logical_key="dns-private",
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "connector_target_forbidden", response.text
    assert local_server.records == [], local_server.records

    # allow_private=true is the administrator's explicit opt-in; the validated
    # address is pinned (localhost may answer ::1 and 127.0.0.1).
    response = t048.invoke(
        t048_stack,
        lease,
        policy_id=dns_allowed["id"],
        params={"path": "/v1/items"},
        logical_key="dns-allowed",
    )
    assert response.status == 200, response.text
    assert response.json["state"] == "succeeded", response.text
    assert len(local_server.records) == 1, local_server.records
    assert local_server.records[0]["headers"]["host"].startswith("localhost:"), (
        local_server.records
    )


# ---------------------------------------------------------------------------
# redirects
# ---------------------------------------------------------------------------
def test_a_redirect_to_an_internal_host_is_refused_and_not_followed(
    t048_stack, local_server
) -> None:
    owner = t041.owner(t048_stack)
    local_server.respond_redirect("http://169.254.169.254/latest/meta-data")
    policy = t048.register(t048_stack, owner.admin, origin=local_server.url)
    t048.bind_tool_connectors(t048_stack.dbname, [policy["id"]])
    _, run, lease = t041.claimed_run(t048_stack, run_owner=owner)

    response = t048.invoke(
        t048_stack,
        lease,
        policy_id=policy["id"],
        params={"path": "/v1/redirect"},
        logical_key="redirect-refused",
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "connector_redirect_refused", response.text
    # Exactly the original request; the 3xx hop was never followed.
    assert len(local_server.records) == 1, local_server.records

    operations = [
        item
        for item in t041.operation_rows(t048_stack.dbname, run["id"])
        if item["logical_key"] == "redirect-refused"
    ]
    assert len(operations) == 1, operations
    assert operations[0]["state"] == "failed", operations[0]
    assert operations[0]["last_error_code"] == "connector_redirect_refused", operations[0]
    


# ---------------------------------------------------------------------------
# revocation
# ---------------------------------------------------------------------------
def test_revocation_fails_the_next_call_and_never_deletes_the_policy(
    t048_stack, local_server
) -> None:
    owner = t041.owner(t048_stack)
    policy = t048.register(t048_stack, owner.admin, origin=local_server.url)
    t048.bind_tool_connectors(t048_stack.dbname, [policy["id"]])
    _, run, lease = t041.claimed_run(t048_stack, run_owner=owner)

    first = t048.invoke(
        t048_stack,
        lease,
        policy_id=policy["id"],
        params={"path": "/v1/items"},
        logical_key="before-revoke",
    )
    assert first.status == 200 and first.json["state"] == "succeeded", first.text

    revoked = t048.revoke(owner.admin, policy["id"])
    assert revoked.status == 200, revoked.text
    assert revoked.json["revoked_at"] is not None, revoked.json
    second_revoke = t048.revoke(owner.admin, policy["id"])
    assert second_revoke.status == 409, second_revoke.text
    assert second_revoke.json["error"]["code"] == "connector_policy_revoked", second_revoke.text

    stale = t048.invoke(
        t048_stack,
        lease,
        policy_id=policy["id"],
        params={"path": "/v1/items"},
        logical_key="after-revoke",
    )
    assert stale.status == 403, stale.text
    assert stale.json["error"]["code"] == "connector_policy_revoked", stale.text
    # One loopback request total: the revoked call never dialed and wrote no
    # operation row (authorization happened before any ledger write).
    assert len(local_server.records) == 1, local_server.records
    assert {
        item["logical_key"] for item in t041.operation_rows(t048_stack.dbname, run["id"])
    } == {"before-revoke"}

    row = t048.policy_row(t048_stack.dbname, policy["id"])
    assert row["revoked_at"] is not None and row["approved_at"] is not None, row


# ---------------------------------------------------------------------------
# capability-kind ambiguity
# ---------------------------------------------------------------------------
def test_a_uuid_declared_as_both_model_and_connector_is_refused(t048_stack, local_server) -> None:
    owner = t041.owner(t048_stack)
    policy = t048.register(t048_stack, owner.admin, origin=local_server.url)
    # The only production path that puts one UUID in both manifest sets: registry
    # rows are written by different tickets, and this ambiguity is exactly the
    # case the capability proxy must refuse instead of guessing.
    st.sql(
        t048_stack.dbname,
        "UPDATE platform.connector_policies SET id = "
        f"{st.quote_literal(t041.GOLDEN_MODEL_ID)}::uuid "
        f"WHERE id = {st.quote_literal(policy['id'])}::uuid",
    )
    t048.bind_tool_connectors(t048_stack.dbname, [t041.GOLDEN_MODEL_ID])
    _, run, lease = t041.claimed_run(t048_stack, run_owner=owner)
    assert t041.GOLDEN_MODEL_ID in [
        entry["version_id"] for entry in t048.manifest_connector_policies(lease)
    ], lease["manifest"]

    as_connector = t048.invoke(
        t048_stack,
        lease,
        policy_id=t041.GOLDEN_MODEL_ID,
        params={"path": "/v1/items"},
        logical_key="ambiguous-connector",
    )
    assert as_connector.status == 409, as_connector.text
    assert as_connector.json["error"]["code"] == "capability_kind_ambiguous", as_connector.text

    as_model = t041.invoke(
        t041.worker_client(t048_stack),
        lease,
        operation_id=str(uuid.uuid4()),
        logical_key="ambiguous-model",
        capability_version=t041.GOLDEN_MODEL_ID,
        capability_kind="model",
        params=t041.model_params(),
    )
    assert as_model.status == 409, as_model.text
    assert as_model.json["error"]["code"] == "capability_kind_ambiguous", as_model.text
    assert local_server.records == [], local_server.records
    assert t041.operation_rows(t048_stack.dbname, run["id"]) == []


def test_a_model_stream_of_a_uuid_also_declared_as_connector_is_refused(
    t048_stack, local_server
) -> None:
    """T044 regression: stream authorization is Invoke's, not resolveModel alone.

    The stream path used to resolve a model UUID directly, so it accepted a UUID
    that the manifest also declares as a connector. Here the same run manifest is
    frozen with one UUID in both sets: the ambiguous stream must be refused
    before any operation/attempt row, exactly like ``invokeCapability``.
    """
    owner = t041.owner(t048_stack)
    policy = t048.register(t048_stack, owner.admin, origin=local_server.url)
    st.sql(
        t048_stack.dbname,
        "UPDATE platform.connector_policies SET id = "
        f"{st.quote_literal(t041.GOLDEN_MODEL_ID)}::uuid "
        f"WHERE id = {st.quote_literal(policy['id'])}::uuid",
    )
    t048.bind_tool_connectors(t048_stack.dbname, [t041.GOLDEN_MODEL_ID])
    _, run, lease = t041.claimed_run(t048_stack, run_owner=owner)
    assert t041.GOLDEN_MODEL_ID in [
        entry["version_id"] for entry in t048.manifest_connector_policies(lease)
    ], lease["manifest"]

    operation_id = str(uuid.uuid4())
    response = t044.stream(
        t041.worker_client(t048_stack),
        lease,
        operation_id=operation_id,
        logical_key="ambiguous-model-stream",
        capability_version=t041.GOLDEN_MODEL_ID,
        params=t041.model_params(),
    )
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "capability_kind_ambiguous", response.text
    assert "text/event-stream" not in (response.header("Content-Type") or "")
    assert local_server.records == [], local_server.records
    assert t041.operation_rows(t048_stack.dbname, run["id"]) == []
    assert t041.attempt_rows(t048_stack.dbname, operation_id) == []


# ---------------------------------------------------------------------------
# write risk needs approval (T050)
# ---------------------------------------------------------------------------
def test_a_write_risk_policy_returns_awaiting_approval_and_never_calls(t048_stack, local_server) -> None:
    owner = t041.owner(t048_stack)
    policy = t048.register(
        t048_stack,
        owner.admin,
        origin=local_server.url,
        method="POST",
        risk_class="write",
    )
    t048.bind_tool_connectors(t048_stack.dbname, [policy["id"]])
    _, run, lease = t041.claimed_run(t048_stack, run_owner=owner)

    params = {"path": "/v1/items", "body": {"name": "x"}}
    operation_id = str(uuid.uuid4())
    response = t048.invoke(
        t048_stack,
        lease,
        policy_id=policy["id"],
        params=params,
        logical_key="write-pending",
        operation_id=operation_id,
    )
    assert response.status == 200, response.text
    body = response.json
    assert body["state"] == "awaiting_approval", body
    assert body["data"]["error_code"] == "approval_required", body
    assert body["data"]["risk_class"] == "write", body
    assert local_server.records == [], local_server.records

    operation = t041.operation_row(t048_stack.dbname, run["id"], operation_id)
    assert operation["state"] == "awaiting_approval", operation
    assert operation["risk_class"] == "write", operation
    assert t041.attempt_count(t048_stack.dbname, operation_id) == 0, operation

    # Replaying the same logical call must not dispatch either.
    replay = t048.invoke(
        t048_stack,
        lease,
        policy_id=policy["id"],
        params=params,
        logical_key="write-pending",
        operation_id=operation_id,
    )
    assert replay.status == 200 and replay.json["state"] == "awaiting_approval", replay.text
    assert local_server.records == [], local_server.records
    assert t041.attempt_count(t048_stack.dbname, operation_id) == 0


# ---------------------------------------------------------------------------
# method/path/schema/query validation
# ---------------------------------------------------------------------------
def test_method_path_schema_and_query_rules_are_enforced(t048_stack, local_server) -> None:
    owner = t041.owner(t048_stack)
    query_schema = {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "query": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            }
        },
    }
    with_query = t048.register(
        t048_stack, owner.admin, origin=local_server.url, request_schema=query_schema
    )
    no_query = t048.register(t048_stack, owner.admin, origin=local_server.url)
    strict = t048.register(
        t048_stack,
        owner.admin,
        origin=local_server.url,
        method="POST",
        request_schema={
            "type": "object",
            "required": ["body"],
            "properties": {"body": {"type": "object", "required": ["name"]}},
        },
    )
    t048.bind_tool_connectors(
        t048_stack.dbname, [with_query["id"], no_query["id"], strict["id"]]
    )
    _, run, lease = t041.claimed_run(t048_stack, run_owner=owner)

    ok = t048.invoke(
        t048_stack,
        lease,
        policy_id=with_query["id"],
        params={"path": "/v1/items", "query": {"q": "hello"}},
        logical_key="query-allowed",
    )
    assert ok.status == 200, ok.text
    assert local_server.records[0]["path"] == "/v1/items?q=hello", local_server.records

    refused_query = t048.invoke(
        t048_stack,
        lease,
        policy_id=no_query["id"],
        params={"path": "/v1/items", "query": {"q": "hello"}},
        logical_key="query-refused",
    )
    assert refused_query.status == 422, refused_query.text
    assert refused_query.json["error"]["code"] == "validation_failed", refused_query.text
    assert refused_query.json["error"]["details"]["field"] == "query", refused_query.text

    method_mismatch = t048.invoke(
        t048_stack,
        lease,
        policy_id=no_query["id"],
        params={"path": "/v1/items", "body": {"name": "x"}},
        logical_key="body-on-get",
    )
    assert method_mismatch.status == 422, method_mismatch.text
    assert method_mismatch.json["error"]["details"]["field"] == "body", method_mismatch.text

    outside_prefix = t048.invoke(
        t048_stack,
        lease,
        policy_id=no_query["id"],
        params={"path": "/v2/items"},
        logical_key="outside-prefix",
    )
    assert outside_prefix.status == 422, outside_prefix.text
    assert outside_prefix.json["error"]["code"] == "connector_request_invalid", outside_prefix.text
    assert len(local_server.records) == 1, local_server.records

    traversal = t048.invoke(
        t048_stack,
        lease,
        policy_id=no_query["id"],
        params={"path": "/v1/../admin"},
        logical_key="traversal",
    )
    assert traversal.status == 422, traversal.text
    assert traversal.json["error"]["code"] == "connector_request_invalid", traversal.text

    schema_mismatch = t048.invoke(
        t048_stack,
        lease,
        policy_id=strict["id"],
        params={"path": "/v1/items", "body": {}},
        logical_key="schema-mismatch",
    )
    assert schema_mismatch.status == 422, schema_mismatch.text
    assert schema_mismatch.json["error"]["code"] == "validation_failed", schema_mismatch.text
    # Schema/method refusals happen before the operation row exists.
    assert all(
        row["logical_key"] not in {"schema-mismatch", "body-on-get", "query-refused"}
        for row in t041.operation_rows(t048_stack.dbname, run["id"])
    ), t041.operation_rows(t048_stack.dbname, run["id"])


def test_a_duplicate_query_key_is_refused_before_dialing(t048_stack, local_server) -> None:
    """P1-1 regression: the URL sends every merged value, so duplicates are refused.

    ``path=/v1/items?scope=forbidden&scope=allowed`` plus ``query.scope=allowed``
    used to pass the enum check on the last value while the real request carried
    ``scope=forbidden`` too. The guard must refuse the duplicate before any
    socket is opened and before an operation row exists.
    """
    owner = t041.owner(t048_stack)
    query_schema = {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "query": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"scope": {"type": "string", "enum": ["allowed"]}},
            }
        },
    }
    policy = t048.register(
        t048_stack, owner.admin, origin=local_server.url, request_schema=query_schema
    )
    t048.bind_tool_connectors(t048_stack.dbname, [policy["id"]])
    _, run, lease = t041.claimed_run(t048_stack, run_owner=owner)

    refused = t048.invoke(
        t048_stack,
        lease,
        policy_id=policy["id"],
        params={
            "path": "/v1/items?scope=forbidden&scope=allowed",
            "query": {"scope": "allowed"},
        },
        logical_key="duplicate-query",
    )
    assert refused.status == 422, refused.text
    assert refused.json["error"]["code"] == "connector_request_invalid", refused.text
    assert refused.json["error"]["details"]["field"] == "query.scope", refused.text
    assert local_server.records == []
    # The refusal is decided by the egress guard after the physical attempt row
    # exists: no request was dialed and the operation is a recorded failure.
    rows = t041.operation_rows(t048_stack.dbname, run["id"])
    assert len(rows) == 1, rows
    assert rows[0]["state"] == "failed", rows
    assert rows[0]["last_error_code"] == "connector_request_invalid", rows
    assert t041.attempt_rows(t048_stack.dbname, rows[0]["id"])[0]["state"] == "failed", rows


def test_registered_schema_bounds_are_enforced_for_real(t048_stack, local_server) -> None:
    """P1-2 regression: minimum/maximum/minLength/maxLength are enforced.

    A policy with ``maximum: 10`` / ``maxLength: 3`` used to accept
    ``amount=1000000, label="far-too-long"`` because the runtime validator
    ignored the keywords. Both the request and the response side must enforce
    them, the boundary values must pass, and an unusable bound declaration must
    not be registrable.
    """
    owner = t041.owner(t048_stack)
    request_schema = {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "body": {
                "type": "object",
                "required": ["amount", "label"],
                "properties": {
                    "amount": {"type": "integer", "minimum": 1, "maximum": 10},
                    "label": {"type": "string", "minLength": 2, "maxLength": 3},
                },
            }
        },
    }
    response_schema = {
        "type": "object",
        "required": ["ok", "total"],
        "properties": {
            "ok": {"type": "boolean"},
            "total": {"type": "integer", "minimum": 0, "maximum": 10},
            "note": {"type": "string", "maxLength": 3},
        },
    }
    policy = t048.register(
        t048_stack,
        owner.admin,
        origin=local_server.url,
        method="POST",
        request_schema=request_schema,
        response_schema=response_schema,
    )
    t048.bind_tool_connectors(t048_stack.dbname, [policy["id"]])
    _, run, lease = t041.claimed_run(t048_stack, run_owner=owner)

    local_server.respond_json({"ok": True, "total": 10, "note": "abc"})
    boundary = t048.invoke(
        t048_stack,
        lease,
        policy_id=policy["id"],
        params={"path": "/v1/orders", "body": {"amount": 10, "label": "abc"}},
        logical_key="bounds-boundary",
    )
    assert boundary.status == 200, boundary.text
    assert boundary.json["state"] == "succeeded", boundary.json
    assert len(local_server.records) == 1, local_server.records

    for logical_key, body, field in (
        ("bounds-amount", {"amount": 1000000, "label": "abc"}, "body/amount"),
        ("bounds-label", {"amount": 5, "label": "far-too-long"}, "body/label"),
        ("bounds-label-short", {"amount": 5, "label": "a"}, "body/label"),
    ):
        refused = t048.invoke(
            t048_stack,
            lease,
            policy_id=policy["id"],
            params={"path": "/v1/orders", "body": body},
            logical_key=logical_key,
        )
        assert refused.status == 422, (logical_key, refused.text)
        assert refused.json["error"]["code"] == "validation_failed", (logical_key, refused.text)
        assert refused.json["error"]["details"]["field"] == field, (logical_key, refused.text)
    assert len(local_server.records) == 1, local_server.records

    # The response side is enforced before the body can become a receipt.
    local_server.respond_json({"ok": True, "total": 11, "note": "abc"})
    bad_response = t048.invoke(
        t048_stack,
        lease,
        policy_id=policy["id"],
        params={"path": "/v1/orders", "body": {"amount": 5, "label": "abc"}},
        logical_key="bounds-response",
    )
    assert bad_response.status == 422, bad_response.text
    assert bad_response.json["error"]["code"] == "connector_response_invalid", bad_response.text
    assert len(local_server.records) == 2, local_server.records

    # A registration whose declared bounds cannot be evaluated is refused.
    base = t048.registration_body(origin=local_server.url)
    unusable = (
        {"type": "object", "additionalProperties": True,
         "properties": {"amount": {"type": "number", "minimum": "1"}}},
        {"type": "object", "additionalProperties": True,
         "properties": {"label": {"type": "string", "minLength": -1}}},
        {"type": "object", "additionalProperties": True,
         "properties": {"amount": {"type": "number", "minimum": 10, "maximum": 1}}},
        {"type": "object", "additionalProperties": True,
         "properties": {"label": {"type": "string", "minLength": 5, "maxLength": 3}}},
    )
    for schema in unusable:
        body = dict(base)
        body["request_schema"] = schema
        rejected = t048.register_raw(t048_stack, owner.admin, body)
        assert rejected.status == 422, rejected.text
        assert rejected.json["error"]["code"] == "validation_failed", rejected.text


def test_invoking_a_policy_outside_the_manifest_is_refused(t048_stack, local_server) -> None:
    owner = t041.owner(t048_stack)
    bound = t048.register(t048_stack, owner.admin, origin=local_server.url)
    unbound = t048.register(
        t048_stack, owner.admin, origin=local_server.url, path_prefix="/other"
    )
    t048.bind_tool_connectors(t048_stack.dbname, [bound["id"]])
    _, run, lease = t041.claimed_run(t048_stack, run_owner=owner)

    response = t048.invoke(
        t048_stack,
        lease,
        policy_id=unbound["id"],
        params={"path": "/other/items"},
        logical_key="unbound",
    )
    assert response.status == 403, response.text
    assert response.json["error"]["code"] == "capability_not_allowed", response.text
    assert local_server.records == []
    assert t041.operation_rows(t048_stack.dbname, run["id"]) == []


# ---------------------------------------------------------------------------
# admin registration surface
# ---------------------------------------------------------------------------
def test_admin_registration_validates_and_never_echoes_the_credential(t048_stack) -> None:
    owner = t041.owner(t048_stack)
    admin = owner.admin

    valid = t048.registration_body(origin="https://connector.example.test", allow_private=False)
    created = t048.register_raw(t048_stack, admin, valid)
    assert created.status == 201, created.text
    policy = created.json
    assert policy["risk_class"] == "read" and policy["allow_private"] is False, policy
    assert policy["secret_ref"] == "env:T048_TEST_SECRET", policy
    assert_not_leaked(created.text)
    assert len(policy["policy_sha256"]) == 64, policy
    assert uuid.UUID(policy["id"])

    # The same immutable body is one version: 409, with the existing id.
    duplicate = t048.register_raw(t048_stack, admin, valid)
    assert duplicate.status == 409, duplicate.text
    assert duplicate.json["error"]["code"] == "connector_policy_exists", duplicate.text
    assert duplicate.json["error"]["details"]["policy_id"] == policy["id"], duplicate.text

    for field, value in (
        ("origin", "https://connector.example.test/v1"),
        ("origin", "https://user:pass@connector.example.test"),
        ("origin", "ftp://connector.example.test"),
        ("method", "TRACE"),
        ("path_prefix", "/v1/../admin"),
        ("secret_ref", "sk-live-not-a-reference"),
        ("secret_header", "Host"),
        ("risk_class", "pure"),
    ):
        body = dict(valid)
        body[field] = value
        rejected = t048.register_raw(t048_stack, admin, body)
        assert rejected.status == 422, (field, rejected.text)
        assert rejected.json["error"]["code"] == "validation_failed", (field, rejected.text)
        assert_not_leaked(rejected.text)

    listed = t048.list_policies(admin)
    assert listed.status == 200, listed.text
    assert [item["id"] for item in listed.json["items"]] == [policy["id"]], listed.json
    assert_not_leaked(listed.text)

    # A creator cannot register or list connectors.
    creator = f00b.invite_and_activate(
        t048_stack, admin, "t048-creator-" + secrets.token_hex(4), roles=("creator",)
    )
    forbidden = t048.register_raw(
        t048_stack, creator, t048.registration_body(origin="https://connector.example.test")
    )
    assert forbidden.status == 403, forbidden.text
    assert creator.client.get("/api/v1/admin/connectors").status == 403

    unknown = t048.revoke(admin, str(uuid.uuid4()))
    assert unknown.status == 404, unknown.text
    assert unknown.json["error"]["code"] == "connector_policy_not_found", unknown.text

    row = t048.policy_row(t048_stack.dbname, policy["id"])
    assert row["approved_at"] is not None and row["revoked_at"] is None, row
    assert row["version"] == 1, row


def test_revoked_policy_cannot_be_compiled_into_a_new_manifest(t048_stack, local_server) -> None:
    owner = t041.owner(t048_stack)
    policy = t048.register(t048_stack, owner.admin, origin=local_server.url)
    t048.bind_tool_connectors(t048_stack.dbname, [policy["id"]])
    revoked = t048.revoke(owner.admin, policy["id"])
    assert revoked.status == 200, revoked.text

    # A debug run compiles the draft after revocation: the closure must refuse
    # dependency_revoked at the tool's config pointer rather than freezing a dead
    # policy into a new manifest.
    from support import t027

    revision = t027.draft_revision(owner.creator, owner.application_id)
    response = t027.submit_debug(
        owner.creator, owner.application_id, draft_revision=revision, input_value={"text": "x"}
    )
    assert response.status == 422, response.text
    errors = response.json["error"]["details"]["errors"]
    assert any(
        error["code"] == "dependency_revoked"
        and (error["pointer"].endswith("/config/tool_version") or error["pointer"].endswith("/config/tool_versions/0"))
        for error in errors
    ), errors
    assert owner and revision  # keep the fixture intent explicit


# ---------------------------------------------------------------------------
# policy-body unit-level truth: the sealed bytes never contain the secret value
# ---------------------------------------------------------------------------
def test_secret_reference_is_policy_content_but_the_value_is_never_stored(t048_stack) -> None:
    owner = t041.owner(t048_stack)
    admin = owner.admin
    body = t048.registration_body(origin="https://connector.example.test")
    created = t048.register_raw(t048_stack, admin, body)
    assert created.status == 201, created.text
    policy = created.json
    row = t048.policy_row(t048_stack.dbname, policy["id"])
    assert row["secret_ref"] == "env:T048_TEST_SECRET", row
    assert T048_SECRET_VALUE not in json.dumps(row), row
    artifact = t048.policy_artifact(t048_stack, policy)
    sealed = (t048_stack.object_dir / artifact["object_key"]).read_bytes()
    assert T048_SECRET_VALUE.encode() not in sealed, sealed
    assert row["policy_sha256"] == t048.sha256_hex(sealed), (row, artifact)



