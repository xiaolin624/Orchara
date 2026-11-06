"""T098 (GitHub #99) real-stack acceptance: conflicts, expiry, deletion, citations.

Every assertion here reads the **real** stack: a throwaway PostgreSQL 16 database
migrated by the real ``cmd/admin`` chain, the compiled ``cmd/api`` on a loopback
socket, the real internal mTLS listener (supervisor/worker/maintenance), the real
object store, and the real ``cmd/maintenance`` handlers for upload finalize and
knowledge ingestion.

The real HTTP bodies are then fed to the **committed** page bytes through
``_page_probe``: the page rules under test are the ones a browser would execute,
not a re-implementation in the test. This is not Playwright / not a browser DOM;
CSS layout is asserted statically in ``test_t098_mobile_pages.py`` and marked
``not_run`` in the report.

The stack runs one API process with a test-sized ``ORCHARA_APPROVAL_SWEEP_INTERVAL``
(100ms). That is the deliberate contrast the ticket asks for: a node approval is
swept automatically, while a capability approval is only observed lazily on the
decision path. The interval is deployment configuration, never a request value.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from support import f00b, f02a, f08, t024, t029, t033, t035, t041, t048, t050, t054, t055, t058, t064, t079, t087
from support import stack as st
from support.certs import make_stack_certificates, unique_internal_addr

NODE = shutil.which("node")

PAGE_PROBE = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const webDir = __WEB_DIR__;
const calls = __CALLS__;
const out = {};
for (const call of calls) {
  const context = {
    console: console,
    document: { addEventListener() {}, getElementById() { return null; } },
  };
  vm.createContext(context);
  vm.runInContext(
    fs.readFileSync(webDir + '/assets/' + call.asset, 'utf8'),
    context,
    { filename: call.asset },
  );
  const fn = context[call.fn];
  if (typeof fn !== 'function') throw new Error(call.fn + ' is not defined in ' + call.asset);
  const args = Array.isArray(call.args) ? call.args : [call.payload];
  out[call.label] = fn.apply(null, args);
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the committed page bytes cannot be executed")
    return NODE


@pytest.fixture(scope="module")
def t098_stack(tmp_path_factory: pytest.TempPathFactory, go_binaries: dict[str, Path]):
    """One migrated + bootstrapped database behind one real API process."""
    tmp_path = tmp_path_factory.mktemp("t098-stack")
    try:
        database = st.create_database("orchara_t098_")
    except RuntimeError as exc:  # pragma: no cover - environment missing
        pytest.skip(f"isolated development cluster is not running: {exc}")
    dsn, dbname = database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    env = t029.api_extra_env(object_dir, certificates, signing_key=signing_key)
    # The ticket's two-expiry-semantics assertion needs a sweep short enough to
    # observe; production keeps the documented 30s default.
    env["ORCHARA_APPROVAL_SWEEP_INTERVAL"] = "100ms"
    server = st.start_api(
        go_binaries["api"],
        dsn,
        web_dir=st.WEB_DIR,
        profile="dev-stub",
        extra_env=env,
        log_path=tmp_path / "api.log",
    )
    stack = t033.T033Stack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        object_dir=object_dir,
        certificates=certificates,
        binaries=go_binaries,
        signing_key=signing_key,
    )
    try:
        f00b.wait_for_internal(stack)
    except Exception:
        server.stop()
        st.drop_database(dbname)
        raise
    try:
        yield stack
    finally:
        server.stop()
        st.drop_database(dbname)


def _page_probe(node_binary: str, tmp_path: Path, calls: list[dict]) -> dict:
    """Execute committed page functions with the real HTTP bodies as input."""
    script = PAGE_PROBE.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))).replace(
        "__CALLS__", json.dumps(calls, ensure_ascii=False)
    )
    path = tmp_path / "t098-page-probe.js"
    path.write_text(script, encoding="utf-8", newline="\n")
    completed = subprocess.run(
        [node_binary, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=120
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


def _failure_body(response, *, status: int) -> dict:
    """The error object ``apiFull`` turns into a thrown failure for this response."""
    body = response.json
    assert isinstance(body, dict) and "error" in body, response.text
    error = body["error"]
    return {
        "status": status,
        "code": error.get("code"),
        "message": error.get("message"),
        "details": error.get("details"),
    }


def _conversation(session) -> str:
    return f08.conversation(session)


def _account(stack, login: str):
    return f08.account(stack, login=login)


@dataclass
class Catalog:
    """One published sales application and one user who may use it."""

    publisher: object
    user: object
    version: dict


@pytest.fixture(scope="module")
def t098_catalog(t098_stack) -> Catalog:
    stack = t098_stack
    publisher = _account(stack, "t098.publisher")
    user = _account(stack, "t098.chat")
    t079.seed_route_control_model(stack, publisher.principal_id)
    version = t077_publish(stack, publisher.session)
    admin = f00b.admin_session(stack)
    f02a.grant_read(admin, user.principal_id, version.application_id)
    return Catalog(publisher=publisher, user=user, version=version)


def t077_publish(stack, session):
    from support import t077

    return t077.publish_version(
        stack,
        session,
        name="T098 mobile request app",
        purpose="summarise sales",
        includes=["aggregation"],
        excludes=["external writes"],
        required_inputs=["sales csv"],
        expected_outputs=["report"],
        examples=["monthly report", "weekly digest"],
    )


def _capability_write(stack, owner):
    """Park one real capability operation behind an approval.

    Mirrors ``tests/f06/test_t050_approval.py::prepare_write``: a real loopback
    connector, a real registered write policy bound into the tool, a real claimed
    run and the real ``invokeCapability`` mTLS call. Returns (server, approval).
    """
    local_server = t048.LocalConnectorServer()
    local_server.respond_json({"ok": True, "write_id": "t098-w-1"})
    policy = t050.write_policy(stack, owner.admin, origin=local_server.url)
    t048.bind_tool_connectors(stack.dbname, [policy["id"]])
    _, _run, lease = t041.claimed_run(stack, run_owner=owner)
    invoked = t050.invoke_write(
        stack,
        lease,
        policy_id=policy["id"],
        params=t050.default_params(),
        logical_key="t098:capability:call-1",
    )
    assert invoked.status == 200, invoked.text
    body = invoked.json
    assert body["state"] == "awaiting_approval", body
    approval = t050.approval_row(stack.dbname, body["approval_id"])
    assert approval is not None, body
    return local_server, approval


# ---------------------------------------------------------------------------
# 1. conflicts: stale If-Match (412) and same-key/different-body (409)
# ---------------------------------------------------------------------------
def test_a_stale_if_match_is_a_located_conflict_and_never_overwrites(
    t098_stack, node_binary, tmp_path
) -> None:
    stack = t098_stack
    admin = f00b.admin_session(stack)
    owner = f00b.invite_and_activate(
        stack, admin, "t098.memory-owner", roles=("user", "creator"),
        password=f00b.USER_PASSWORD,
    )
    application_id = t058.new_application(owner, name="t098 memory application")
    memory = t058.create_memory_ok(owner, application_id, "The user's first note.")
    current = t058.detail(owner, memory["id"])
    assert current.status == 200, current.text
    assert t058.etag_of(current) == t058.expected_etag(1)

    stale = t058.update(owner, memory["id"], text="a stale write", if_match=t058.expected_etag(0))
    assert stale.status == 412, stale.text
    assert stale.json["error"]["code"] == "precondition_failed", stale.text
    details = stale.json["error"]["details"]
    assert details["current_etag"] == t058.expected_etag(1), details
    assert details["current_revision"] == 1, details
    # The stale write did not land.
    unchanged = t058.detail(owner, memory["id"])
    assert unchanged.json["revision"] == 1, unchanged.json
    assert unchanged.json["text"] == "The user's first note.", unchanged.json

    applied = t058.update(owner, memory["id"], text="the current write", if_match=t058.expected_etag(1))
    assert applied.status == 200, applied.text
    assert applied.json["revision"] == 2, applied.json

    # The real 412 body drives the committed page rule: keep the user's text,
    # adopt the server's current ETag, and only write again on an explicit retry.
    probe = _page_probe(
        node_binary,
        tmp_path,
        [
            {
                "label": "conflict",
                "asset": "memories.js",
                "fn": "preconditionPlan",
                "payload": _failure_body(stale, status=412),
            }
        ],
    )
    conflict = probe["conflict"]
    assert conflict["kind"] == "conflict", conflict
    assert conflict["currentEtag"] == '"1"', conflict
    assert "版本冲突" in conflict["message"], conflict
    assert "静默覆盖" in conflict["message"], conflict


def test_the_same_request_key_with_a_different_body_is_a_explicit_409(
    t098_stack, t098_catalog: Catalog, node_binary, tmp_path
) -> None:
    stack = t098_stack
    user = t098_catalog.user
    conversation_id = _conversation(user.session)
    key = "t098-idempotency-conflict-key"
    first = f08.submit_request(
        user.session, conversation_id, "summarise this sales report", key=key
    )
    assert first.status == 202, first.text
    request = f08.request_of(first)

    conflicting = f08.submit_request(
        user.session, conversation_id, "a different body under the same key", key=key
    )
    assert conflicting.status == 409, conflicting.text
    assert conflicting.json["error"]["code"] == "idempotency_conflict", conflicting.text

    # Exactly one request exists; the first body is the one the key names.
    rows = f08.request_rows(stack.dbname, conversation_id)
    assert len(rows) == 1, rows
    assert rows[0]["id"] == request["id"], rows

    # The committed chat rule turns the real 409 into "new key / do not replay".
    probe = _page_probe(
        node_binary,
        tmp_path,
        [
            {
                "label": "conflict",
                "asset": "chat.js",
                "fn": "describeSubmitFailure",
                "payload": _failure_body(conflicting, status=409),
            }
        ],
    )
    failure = probe["conflict"]
    assert failure["reuseKey"] is False, failure
    assert "idempotency_conflict" in failure["text"], failure
    assert "新任务" in failure["text"], failure
    assert "旧键" in failure["text"], failure


# ---------------------------------------------------------------------------
# 2. expiry: a closed interaction and a no-match request both explain themselves
# ---------------------------------------------------------------------------
def test_an_expired_interaction_and_a_no_match_request_are_failed_and_explained(
    t098_stack, t098_catalog: Catalog, node_binary, tmp_path
) -> None:
    stack = t098_stack
    user = t098_catalog.user
    # The sales card requires one input: without an attachment the server asks,
    # and the interaction's real 15-minute deadline is moved into the past.
    conversation_id = _conversation(user.session)
    submitted = f08.submit_request(user.session, conversation_id, "summarise this csv")
    request = f08.request_of(submitted)
    routed = t079.route_once(stack)
    assert routed.completed.returncode == 0, routed.stdout
    waiting = t079.request_state(stack.dbname, request["id"])
    assert waiting["state"] == "awaiting_input", waiting
    interaction = t079.pending_interaction_row(stack.dbname, request["id"])
    assert interaction and interaction["kind"] == "input", interaction

    t079.expire_interaction(stack, request["id"], seconds=5)
    expired_reply = t079.reply(
        user.session,
        request["id"],
        {"interaction_id": interaction["id"], "message": "an expired reply"},
    )
    assert expired_reply.status == 409, expired_reply.text
    assert expired_reply.json["error"]["code"] == "interaction_closed", expired_reply.text

    failed = t079.request_state(stack.dbname, request["id"])
    assert failed["state"] == "failed", failed
    assert failed["error_code"] == "interaction_expired", failed
    assert f08.run_rows(stack.dbname, conversation_id) == []
    read = f08.get_request(user.session, request["id"])
    assert read.status == 200, read.text
    assert read.json["error_code"] == "interaction_expired", read.json
    assert read.json["interaction"] is None, read.json

    # A request that no application matches is failed with a next step, not left
    # spinning and not forced into an unrelated application.
    second_conversation = _conversation(user.session)
    no_match = f08.request_of(
        f08.submit_request(user.session, second_conversation, "写一首关于春天的诗")
    )
    assert t079.route_once(stack).completed.returncode == 0
    no_match_row = t079.request_state(stack.dbname, no_match["id"])
    assert no_match_row["state"] == "failed", no_match_row
    assert no_match_row["error_code"] == "no_matching_application", no_match_row
    assert t079.candidate_rows(stack.dbname, no_match["id"]) == []
    assert f08.run_rows(stack.dbname, second_conversation) == []
    no_match_read = f08.get_request(user.session, no_match["id"])
    assert no_match_read.status == 200, no_match_read.text

    probe = _page_probe(
        node_binary,
        tmp_path,
        [
            {
                "label": "expired",
                "asset": "chat.js",
                "fn": "describeRequestFailure",
                "payload": read.json,
            },
            {
                "label": "no_match",
                "asset": "chat.js",
                "fn": "describeRequestFailure",
                "payload": no_match_read.json,
            },
        ],
    )
    assert probe["expired"]["visible"] is True, probe
    assert "过期" in probe["expired"]["text"], probe["expired"]
    assert "重新提交" in probe["expired"]["text"], probe["expired"]
    assert probe["no_match"]["visible"] is True, probe
    assert "未找到匹配的应用" in probe["no_match"]["text"], probe["no_match"]
    assert "应用目录" in probe["no_match"]["text"], probe["no_match"]


# ---------------------------------------------------------------------------
# 3. AC2: a revoked/deleted citation is 404 and the page clears the old body
# ---------------------------------------------------------------------------
def test_a_deleted_document_stops_the_citation_immediately_and_shows_the_job(
    t098_stack, node_binary, tmp_path
) -> None:
    stack = t098_stack
    admin = f00b.admin_session(stack)
    owner = f00b.invite_and_activate(
        stack, admin, "t098.knowledge", roles=("user",), password=f00b.USER_PASSWORD
    )
    base = t055.create_knowledge_base(owner, "t098 knowledge base")
    assert base.status == 201, base.text
    base_id = base.json["id"]
    empty_base = t055.create_knowledge_base(owner, "t098 empty knowledge base")
    assert empty_base.status == 201, empty_base.text
    empty_base_id = empty_base.json["id"]
    artifact_id = t055.prepare_source(stack, owner, t055.SAMPLE_DOCUMENT)
    log_path = stack.server.log_path.parent / "maintenance-t098-knowledge.log"
    with t055.ingest_pools(stack, log_path):
        accepted = t055.ingest_document(owner, base_id, artifact_id)
        assert accepted.status == 202, accepted.text
        document_id = accepted.json["id"]
        document = t055.wait_for_document_state(owner, document_id, {"ready", "failed"})
    assert document["state"] == "ready", (document, t055.document_row(stack.dbname, document_id))

    retrieved = t055.retrieval_query(owner, base_id, "退款政策")
    assert retrieved.status == 200, retrieved.text
    assert retrieved.json["items"], retrieved.json
    chunk = retrieved.json["items"][0]
    chunk_id = chunk["chunk_id"]
    opened = t055.get_citation(owner, chunk_id)
    assert opened.status == 200, opened.text
    assert opened.json["text"] == chunk["text"], opened.json

    # An empty knowledge base is a normal empty result: retrieval returns no
    # items and the grounded answer is the explicit `no_results` outcome, not an
    # error and not a spinner.
    empty = t055.retrieval_query(owner, empty_base_id, "任意问题")
    assert empty.status == 200, empty.text
    assert empty.json["items"] == [], empty.json
    empty_answer = t064.answer(owner, empty_base_id, "任意问题")
    assert empty_answer.status == 200, empty_answer.text
    assert empty_answer.json["outcome"] == "no_results", empty_answer.json
    assert empty_answer.json["answer"]["citations"] == [], empty_answer.json

    deleted = owner.client.request(
        "DELETE", f"/api/v1/knowledge-documents/{document_id}", csrf=owner.csrf
    )
    assert deleted.status == 202, deleted.text
    job = deleted.json
    assert set(job) == {"id", "state", "resource_id"}, job
    assert job["resource_id"] == document_id, job
    assert job["state"] in ("pending", "running", "completed"), job

    # The citation route re-checks current readability: a deleted source is 404
    # with no old body, and retrieval no longer returns it.
    refused = t055.get_citation(owner, chunk_id)
    assert refused.status == 404, refused.text
    assert refused.json["error"]["code"] == "resource_not_found", refused.text
    assert "text" not in refused.json and "citation" not in refused.json, refused.text
    after = t055.retrieval_query(owner, base_id, "退款政策")
    assert after.status == 200, after.text
    assert after.json["items"] == [], after.json

    # The 202 is followed by a real, readable deletion job (not silent success).
    job_read = owner.client.get(f"/api/v1/deletions/{job['id']}")
    assert job_read.status == 200, job_read.text
    assert job_read.json["state"] in ("pending", "running", "completed", "failed"), job_read.json
    assert job_read.json["remaining_scopes"], job_read.json

    document_after = t055.get_document(owner, document_id)
    assert document_after.status == 404, document_after.text
    assert document_after.json["error"]["code"] == "resource_not_found", document_after.text

    failure = _failure_body(refused, status=404)
    probe = _page_probe(
        node_binary,
        tmp_path,
        [
            {
                "label": "citation",
                "asset": "knowledge.js",
                "fn": "citationOpenFailure",
                "payload": failure,
            },
            {
                "label": "retrieval",
                "asset": "knowledge.js",
                "fn": "describeRetrieval",
                "payload": empty.json,
            },
        ],
    )
    citation = probe["citation"]
    assert citation["clearBody"] is True, citation
    assert "引用不可用" in citation["text"], citation
    assert "不会回退" in citation["text"], citation
    assert "重新检索" in citation["text"], citation
    assert probe["retrieval"]["empty"] is True, probe["retrieval"]
    assert "下一步" in probe["retrieval"]["emptyNextText"], probe["retrieval"]


# ---------------------------------------------------------------------------
# 4. the two approval expiry mechanisms are opposite and both are real
# ---------------------------------------------------------------------------
def test_capability_and_node_approvals_expire_by_different_mechanisms(
    t098_stack, node_binary, tmp_path
) -> None:
    stack = t098_stack

    # -- capability approval: no sweeper; the decision path observes the deadline
    capability_owner = t041.owner(stack)
    local_server = None
    try:
        local_server, capability = _capability_write(stack, capability_owner)
        capability_id = capability["id"]
        assert capability["operation_id"] is not None, capability
        t050.point_expiry_past(stack.dbname, capability_id)
        # Let many sweep passes run; a capability row must not be swept.
        time.sleep(0.6)
        capability_view = t050.get_approval(capability_owner.creator, capability_id)
        assert capability_view.status == 200, capability_view.text
        assert capability_view.json["state"] == "pending", capability_view.json
        refused = t050.decide(
            capability_owner.creator,
            capability_id,
            params_sha256=capability_view.json["params_sha256"],
        )
        assert refused.status == 409, refused.text
        assert refused.json["error"]["code"] == "approval_expired", refused.text
        capability_row = t050.approval_row(stack.dbname, capability_id)
        assert capability_row["state"] == "expired", capability_row
    finally:
        if local_server is not None:
            local_server.close()

    # -- node approval: the real API sweeper expires the row and fails the Run
    node_owner, _application_id, run = t087.queue_approval_run(
        stack,
        t087.approval_definition(),
        owner=capability_owner,
        name="t098 node approval",
    )
    lease = t087.claim_next_run(stack, execution_unit_id="t098-node-unit")
    assert lease["run_id"] == run["id"], (lease, run)
    prepared_node = t087.prepare_node_approval(stack, lease)
    assert prepared_node.status == 201, prepared_node.text
    node_approval = prepared_node.json
    node_id = node_approval["id"]
    assert node_approval["state"] == "pending", node_approval

    worker = t029.execution_client(stack, "worker")
    waiting = t035.submit_waiting(
        worker, lease, checkpoint_id="approval:" + node_id
    )
    assert waiting.status == 200, waiting.text
    exited = t035.confirm_waiting_exit(stack, lease, execution_unit_id="t098-node-unit")
    assert exited.status == 200, exited.text
    parked = t050.run_row(stack.dbname, run["id"])
    assert parked["state"] == "waiting_approval", parked

    t050.point_expiry_past(stack.dbname, node_id)
    deadline = time.monotonic() + 15.0
    failed = None
    while time.monotonic() < deadline:
        failed = t050.run_row(stack.dbname, run["id"])
        if failed["state"] == "failed":
            break
        time.sleep(0.05)
    assert failed is not None and failed["state"] == "failed", failed
    assert failed["error_code"] == "approval_expired", failed
    node_row = t050.approval_row(stack.dbname, node_id)
    assert node_row["state"] == "expired", node_row
    assert node_row["decision_key"] == "sweeper:expired", node_row
    late = t050.decide(
        node_owner.creator, node_id, params_sha256=node_approval["params_sha256"]
    )
    assert late.status == 409, late.text
    assert late.json["error"]["code"] == "approval_expired", late.text

    probe = _page_probe(
        node_binary,
        tmp_path,
        [
            {
                "label": "capability_pending",
                "asset": "approvals.js",
                "fn": "approvalExpiryNote",
                "args": [{"state": "pending"}, "capability_proposal"],
            },
            {
                "label": "node_expired",
                "asset": "approvals.js",
                "fn": "approvalExpiryNote",
                "args": [{"state": "expired"}, "node_approval"],
            },
            {
                "label": "late_decision",
                "asset": "approvals.js",
                "fn": "describeDecisionFailure",
                "payload": _failure_body(late, status=409),
            },
        ],
    )
    # approvalExpiryNote takes (approval, proposalKind); the probe passes the
    # arguments explicitly so the capability/node branches are the ones tested.
    assert probe["capability_pending"]["visible"] is True, probe
    assert "没有后台过期扫描" in probe["capability_pending"]["text"], probe["capability_pending"]
    assert "approval_expired" in probe["capability_pending"]["text"], probe["capability_pending"]
    assert probe["node_expired"]["visible"] is True, probe
    assert "已过期" in probe["node_expired"]["text"], probe["node_expired"]
    assert "不能复活" in probe["node_expired"]["text"], probe["node_expired"]
    assert "不提供复活" in probe["late_decision"]["text"], probe["late_decision"]
    assert "重新提交" in probe["late_decision"]["text"], probe["late_decision"]


# ---------------------------------------------------------------------------
# 5. personal data: immediate memory deletion + an explicit candidate decision
# ---------------------------------------------------------------------------
def test_memory_deletion_is_immediate_and_a_candidate_is_never_pre_decided(
    t098_stack, node_binary, tmp_path
) -> None:
    stack = t098_stack
    admin = f00b.admin_session(stack)
    owner = f00b.invite_and_activate(
        stack, admin, "t098.personal", roles=("user", "creator"), password=f00b.USER_PASSWORD
    )
    application_id = t058.new_application(owner, name="t098 personal application")
    memory = t058.create_memory_ok(owner, application_id, "The user's private note.")
    readable = t058.detail(owner, memory["id"])
    assert readable.status == 200, readable.text
    assert readable.json["text"] == "The user's private note.", readable.json

    deleted = t058.delete(owner, memory["id"])
    assert deleted.status == 202, deleted.text
    job = deleted.json
    assert set(job) == {"id", "state", "resource_id"}, job
    assert job["resource_id"] == memory["id"], job
    assert job["state"] in ("pending", "running", "completed"), job

    # Immediate stop of access: the next read is a 404 and the list is empty,
    # before any physical cleanup has completed.
    refused = t058.detail(owner, memory["id"])
    assert refused.status == 404, refused.text
    assert refused.json["error"]["code"] == "resource_not_found", refused.text
    listed = t058.list_memories(owner)
    assert listed.status == 200, listed.text
    assert [item["id"] for item in listed.json["items"]] == [], listed.json

    # The job stays observable after the 202: a real state and remaining scopes.
    job_read = owner.client.get(f"/api/v1/deletions/{job['id']}")
    assert job_read.status == 200, job_read.text
    assert job_read.json["state"] == job["state"], job_read.json
    assert job_read.json["remaining_scopes"], job_read.json

    probe = _page_probe(
        node_binary,
        tmp_path,
        [
            {
                "label": "job",
                "asset": "personal-data.js",
                "fn": "describeDeletionJob",
                "payload": job_read.json,
            }
        ],
    )
    view = probe["job"]
    assert "立即停止访问" in view["accessStoppedText"], view
    assert "tombstone" in view["accessStoppedText"], view
    assert "异步物理清理" in view["cleanupPolicyText"], view
    assert "备份" in view["cleanupPolicyText"], view
    assert view["stateText"] in ("待处理", "执行中", "已完成", "失败"), view

    # -- a real worker proposal is a candidate, not a saved memory -------------
    candidate_owner = t041.secondary_owner(stack)
    _owner, _run, lease = t041.claimed_run(stack, run_owner=candidate_owner)
    proposed = t054.propose(
        stack,
        lease,
        text="The user prefers concise weekly summaries.",
        logical_key="t098:memory:1",
    )
    assert proposed.status == 201, proposed.text
    candidate = proposed.json
    assert set(candidate) == {"id", "application_id", "text", "state", "expires_at"}, candidate
    assert candidate["state"] == "pending", candidate
    assert candidate["application_id"] == candidate_owner.application_id, candidate

    listed_candidates = t054.list_candidates(candidate_owner.creator)
    assert listed_candidates.status == 200, listed_candidates.text
    items = [item for item in listed_candidates.json["items"] if item["id"] == candidate["id"]]
    assert len(items) == 1, listed_candidates.json
    listed_candidate = items[0]
    # The contract body carries no preset decision or selection field.
    assert set(listed_candidate) == {"id", "application_id", "text", "state", "expires_at"}, (
        listed_candidate
    )
    # Nothing is saved until an explicit decision arrives.
    assert t054.list_memories(candidate_owner.creator).json["items"] == []

    candidate_probe = _page_probe(
        node_binary,
        tmp_path,
        [
            {
                "label": "candidate",
                "asset": "memories.js",
                "fn": "describeCandidate",
                "payload": listed_candidate,
            }
        ],
    )
    projected = candidate_probe["candidate"]
    assert projected["defaultDecision"] == "none", projected
    assert projected["isPending"] is True, projected
    assert candidate_owner.application_id in projected["saveScopeText"], projected
    assert "确认前不可召回" in projected["sourceText"], projected
    assert "服务端封存" in projected["interactionText"], projected

    decision = t054.decide(
        candidate_owner.creator, candidate["id"], decision="confirm",
        idempotency_key="t098-memory-candidate-decision",
    )
    assert decision.status == 200, decision.text
    assert decision.json["state"] == "confirmed", decision.json
    saved = t054.list_memories(candidate_owner.creator)
    assert saved.status == 200, saved.text
    assert [item["id"] for item in saved.json["items"]] == [decision.json["memory_id"]], saved.json


