"""F07/T078 acceptance: delegated child Runs, their results and their replay.

Every case drives the compiled API, the real internal mTLS listener, real
PostgreSQL and the real ``cmd/supervisor exec`` binary. A child Run is always
created by the contract ``spawnChild`` endpoint; the tests never insert one.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

from support import f02a, f00b, t025, t027, t029, t033, t035, t068, t075, t078
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    t029_stack,
)


def _setup(stack, *, versions: int = 1, input_value: dict | None = None):
    admin, creator = t075.creator(stack)
    children = [
        t078.create_child_version(stack, creator, version_no=1) for _ in range(versions)
    ]
    definition = t078.parent_definition([child["id"] for child in children])
    application_id, parent = t078.debug_parent(
        stack,
        creator,
        definition,
        input_value
        or {"source": "parent-source", "history": "PARENT-HISTORY-MARKER"},
    )
    return admin, creator, children, application_id, parent


def test_spawn_replay_conflict_closure_and_the_sealed_mapped_input(t029_stack) -> None:
    admin, creator, children, application_id, parent = _setup(t029_stack, versions=2)
    lease = t078.claim_run(t029_stack, run_id=parent["id"])
    token = lease["attempt_token"]

    first = t078.spawn(
        t029_stack, parent["id"], attempt_token=token, logical_key="invocation-1",
        application_version_id=children[0]["id"], input={"source": "one"},
    )
    assert first.status == 202, first.text
    child = first.json
    assert set(child) == {
        "id", "state", "revision", "application_version_id", "parent_run_id",
        "output_artifact_id", "error_code", "kind", "execution_snapshot_id", "phase",
    }, child
    assert child["parent_run_id"] == parent["id"], child
    assert child["kind"] == "published", child
    assert child["application_version_id"] == children[0]["id"], child
    assert child["state"] == "queued", child

    # The exact canonical mapped input is sealed; nothing from the parent input or
    # history crossed the boundary.
    receipt = t078.run_children_row(t029_stack.dbname, parent["id"], "invocation-1")
    assert receipt is not None
    assert json.loads(receipt["io_mapping"]) == {"node_id": "child", "fields": ["source"]}
    sealed_input = t078.artifact_bytes(t029_stack, receipt["input_artifact_id"])
    assert json.loads(sealed_input) == {"source": "one"}
    assert sealed_input == b'{"source":"one"}', sealed_input
    assert b"PARENT-HISTORY-MARKER" not in sealed_input
    assert receipt["input_sha256"] == hashlib.sha256(sealed_input).hexdigest()
    assert receipt["depth"] == 1
    assert receipt["child_version_id"] == children[0]["id"]
    assert len(receipt["input_schema_sha256"]) == 64
    assert len(receipt["output_schema_sha256"]) == 64
    child_row = t078.run_row(t029_stack.dbname, child["id"])
    assert child_row["owner_id"] == creator.principal_id, child_row
    assert child_row["checkpoint_thread_id"] != t078.run_row(
        t029_stack.dbname, parent["id"]
    )["checkpoint_thread_id"]

    # Replay: same key, same version, same input -> the original Run, no new row.
    replay = t078.spawn(
        t029_stack, parent["id"], attempt_token=token, logical_key="invocation-1",
        application_version_id=children[0]["id"], input={"source": "one"},
    )
    assert replay.status == 202, replay.text
    assert replay.json == child, replay.json
    assert t078.child_count(t029_stack.dbname, parent["id"]) == 1
    assert len(t078.run_children_rows(t029_stack.dbname, parent["id"])) == 1

    # Same key, different input -> 409 child_invocation_conflict at field input.
    conflict_input = t078.spawn(
        t029_stack, parent["id"], attempt_token=token, logical_key="invocation-1",
        application_version_id=children[0]["id"], input={"source": "two"},
    )
    assert conflict_input.status == 409, conflict_input.text
    assert conflict_input.json["error"]["code"] == "child_invocation_conflict", conflict_input.text
    assert conflict_input.json["error"]["details"]["field"] == "input", conflict_input.text

    # Same key, different version -> 409 at field application_version_id.
    conflict_version = t078.spawn(
        t029_stack, parent["id"], attempt_token=token, logical_key="invocation-1",
        application_version_id=children[1]["id"], input={"source": "one"},
    )
    assert conflict_version.status == 409, conflict_version.text
    assert conflict_version.json["error"]["code"] == "child_invocation_conflict", conflict_version.text
    assert conflict_version.json["error"]["details"]["field"] == "application_version_id"

    # A version the frozen parent manifest does not close over is refused 403.
    outsider = t078.create_child_version(t029_stack, creator, version_no=1)
    outside = t078.spawn(
        t029_stack, parent["id"], attempt_token=token, logical_key="invocation-2",
        application_version_id=outsider["id"], input={"source": "one"},
    )
    assert outside.status == 403, outside.text
    assert outside.json["error"]["code"] == "child_not_in_dependency_closure", outside.text

    # A stale/forged attempt token cannot create a child.
    stale = t078.spawn(
        t029_stack, parent["id"], attempt_token=f00b.new_uuid(), logical_key="invocation-3",
        application_version_id=children[0]["id"], input={"source": "one"},
    )
    assert stale.status == 409, stale.text
    assert stale.json["error"]["code"] == "stale_attempt", stale.text
    assert t078.child_count(t029_stack.dbname, parent["id"]) == 1


def test_supervisor_delegates_then_returns_the_child_result(t029_stack) -> None:
    admin, creator, children, application_id, parent = _setup(t029_stack)
    parent_id = parent["id"]

    # 1. The real supervisor claims the parent, spawns the child and parks itself.
    first, _ = t078.execute_once(t029_stack)
    assert first["status"] == "executed", first
    assert first["run_id"] == parent_id, first
    assert first["outcome"] == "waiting_children", first
    waiting = t078.waiting_row(t029_stack.dbname, parent_id)
    assert waiting is not None and waiting["kind"] == "children", waiting
    assert waiting["checkpoint_id"].startswith("child:"), waiting
    child_id = waiting["checkpoint_id"].split(":", 1)[1]
    assert t078.child_run_ids(t029_stack.dbname, parent_id) == [child_id]
    child_row = t078.run_row(t029_stack.dbname, child_id)
    assert child_row["state"] == "queued", child_row
    assert child_row["owner_id"] == creator.principal_id, child_row

    # 2. The next real supervisor invocation claims the child and runs the child
    # graph (start -> end) without touching the parent.
    second, _ = t078.execute_once(t029_stack)
    assert second["status"] == "executed", second
    assert second["run_id"] == child_id, second
    assert second["outcome"] == "succeeded", second
    child_row = t078.wait_run_state(t029_stack.dbname, child_id, {"succeeded"})
    assert child_row["output_artifact_id"], child_row
    child_output = json.loads(
        creator.client.get(
            f"/api/v1/artifacts/{child_row['output_artifact_id']}/content"
        ).raw
    )
    assert child_output == {"echo": "parent-source"}, child_output
    assert len(t078.attempt_rows(t029_stack.dbname, child_id)) == 1

    # 3. The documented T035 bridge supplies the children resolution; F07/T081
    # owns the automatic child-terminal hook. The one resume job is created only
    # after the child is terminal and the parent's exit was confirmed.
    resolved = t078.resolve_children_wait(t029_stack, parent_id, child_id)
    assert resolved.status == 200, resolved.text
    resumed = t078.wait_for_resume_job(t029_stack.dbname, parent_id)
    assert resumed["decision"] is not None

    # 4. The resume attempt re-enters call_app, replays the *same* child and reads
    # its structured result; the parent finishes with the child's output.
    third, _ = t078.execute_once(t029_stack)
    assert third["status"] == "executed", third
    assert third["run_id"] == parent_id, third
    assert third["outcome"] == "succeeded", third
    parent_row = t078.run_row(t029_stack.dbname, parent_id)
    assert parent_row["state"] == "succeeded", parent_row
    parent_output = json.loads(
        creator.client.get(
            f"/api/v1/artifacts/{parent_row['output_artifact_id']}/content"
        ).raw
    )
    assert parent_output == {"echo": "parent-source"}, parent_output

    # No second child was created by the replay, and the parent executed it once.
    assert t078.child_count(t029_stack.dbname, parent_id) == 1
    assert len(t078.run_children_rows(t029_stack.dbname, parent_id)) == 1
    assert len(t078.attempt_rows(t029_stack.dbname, parent_id)) == 2

    # 5. The owner sees the independent child timeline and its structured result.
    child_read = creator.client.get(f"/api/v1/runs/{child_id}")
    assert child_read.status == 200, child_read.text
    assert child_read.json["parent_run_id"] == parent_id
    assert child_read.json["state"] == "succeeded"
    children_read = creator.client.get(f"/api/v1/runs/{parent_id}/children")
    assert children_read.status == 200, children_read.text
    items = children_read.json["items"]
    assert [item["id"] for item in items] == [child_id], items
    assert set(items[0]) == {
        "id", "state", "revision", "application_version_id", "parent_run_id",
        "output_artifact_id", "error_code", "kind", "execution_snapshot_id", "phase",
    }, items[0]
    # Another principal cannot read the parent's children.
    other = f00b.invite_and_activate(
        t029_stack, admin, "t078-other-" + f00b.new_uuid()[:8], roles=("user",)
    )
    assert other.client.get(f"/api/v1/runs/{parent_id}/children").status == 404


def test_two_concurrent_children_are_isolated_runs_and_threads(t029_stack) -> None:
    admin, creator, children, application_id, parent = _setup(t029_stack)
    lease = t078.claim_run(t029_stack, run_id=parent["id"])
    token = lease["attempt_token"]

    def spawn(key: str, source: str):
        return t078.spawn(
            t029_stack, parent["id"], attempt_token=token, logical_key=key,
            application_version_id=children[0]["id"], input={"source": source},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda item: spawn(*item), [("key-a", "alpha"), ("key-b", "beta")]))
    assert [response.status for response in responses] == [202, 202], [r.text for r in responses]
    first_child, second_child = responses[0].json, responses[1].json
    assert first_child["id"] != second_child["id"]
    assert first_child["parent_run_id"] == second_child["parent_run_id"] == parent["id"]

    # Both execute as real, independent attempts with their own thread and output.
    reports = []
    for _ in range(2):
        report, _ = t078.execute_once(t029_stack)
        assert report["outcome"] == "succeeded", report
        reports.append(report)
    assert {report["run_id"] for report in reports} == {first_child["id"], second_child["id"]}
    first_row = t078.run_row(t029_stack.dbname, first_child["id"])
    second_row = t078.run_row(t029_stack.dbname, second_child["id"])
    assert first_row["checkpoint_thread_id"] != second_row["checkpoint_thread_id"]
    assert first_row["output_artifact_id"] != second_row["output_artifact_id"]
    assert len(t078.attempt_rows(t029_stack.dbname, first_child["id"])) == 1
    assert len(t078.attempt_rows(t029_stack.dbname, second_child["id"])) == 1
    outputs = {
        json.loads(creator.client.get(f"/api/v1/artifacts/{row['output_artifact_id']}/content").raw)["echo"]
        for row in (first_row, second_row)
    }
    assert outputs == {"alpha", "beta"}, outputs


def test_cannot_borrow_the_child_authors_permissions(t029_stack) -> None:
    admin, creator = t075.creator(t029_stack)
    author = f00b.invite_and_activate(
        t029_stack, admin, "t078-author-" + f00b.new_uuid()[:8], roles=("creator",)
    )
    child = t078.create_child_version(t029_stack, author)
    grant_id = f02a.grant_read(admin, creator.principal_id, child["application_id"])
    definition = t078.parent_definition([child["id"]])
    application_id, parent = t078.debug_parent(
        t029_stack, creator, definition, {"source": "granted-source"}
    )
    lease = t078.claim_run(t029_stack, run_id=parent["id"])

    # The caller's *current* ACL is re-checked at spawn: revoking the grant after
    # the parent Run was created closes the door immediately.
    revoked = f02a.revoke_grant(admin, grant_id)
    assert revoked.status in (200, 204), revoked.text
    denied = t078.spawn(
        t029_stack, parent["id"], attempt_token=lease["attempt_token"],
        logical_key="delegation-1", application_version_id=child["id"],
        input={"source": "granted-source"},
    )
    assert denied.status == 403, denied.text
    assert denied.json["error"]["code"] == "child_not_visible", denied.text
    assert t078.child_count(t029_stack.dbname, parent["id"]) == 0

    grant_again = f02a.grant_read(admin, creator.principal_id, child["application_id"])
    assert grant_again
    allowed = t078.spawn(
        t029_stack, parent["id"], attempt_token=lease["attempt_token"],
        logical_key="delegation-1", application_version_id=child["id"],
        input={"source": "granted-source"},
    )
    assert allowed.status == 202, allowed.text
    child_row = t078.run_row(t029_stack.dbname, allowed.json["id"])
    # The child runs as the caller, never as the child application's author.
    assert child_row["owner_id"] == creator.principal_id, child_row
    assert child_row["owner_id"] != author.principal_id, child_row
    # The child's own frozen manifest is its only capability surface.
    assert child_row["application_version_id"] == child["id"]
    assert child_row["execution_snapshot_id"] != t078.run_row(
        t029_stack.dbname, parent["id"]
    )["execution_snapshot_id"]


def test_a_nonconforming_child_result_fails_the_child_not_the_parent(t029_stack) -> None:
    admin, creator, children, application_id, parent = _setup(t029_stack)
    lease = t078.claim_run(t029_stack, run_id=parent["id"])
    spawned = t078.spawn(
        t029_stack, parent["id"], attempt_token=lease["attempt_token"],
        logical_key="schema-check", application_version_id=children[0]["id"],
        input={"source": "schema-check"},
    )
    assert spawned.status == 202, spawned.text
    child_id = spawned.json["id"]

    child_lease = t078.claim_run(t029_stack)
    assert child_lease["run_id"] == child_id, child_lease
    artifacts = t033.artifact_client(t029_stack, "worker")
    bad = b'{"bogus":1}'
    artifact_id, _sha = t033.upload_run_artifact(
        t029_stack, artifacts, child_lease,
        content=bad, purpose="final_output", media_type="application/json", relation="output",
    )
    supervisor = t029.execution_client(t029_stack, "supervisor")
    proposed = supervisor.submit_result(
        child_id,
        attempt_token=child_lease["attempt_token"],
        suggested_state="succeeded",
        output_artifact_id=artifact_id,
    )
    assert proposed.status == 200, proposed.text
    exited = supervisor.confirm_exit(
        child_lease["attempt_token"],
        execution_unit_id="t078-unit",
        evidence=t029.exit_evidence(child_lease),
    )
    assert exited.status == 200, exited.text
    child_row = t078.wait_run_state(t029_stack.dbname, child_id, {"failed"})
    assert child_row["error_code"] == "child_output_schema_invalid", child_row
    assert child_row["output_artifact_id"] is None, child_row
    # The parent is untouched: it never received a failure and stays running.
    parent_row = t078.run_row(t029_stack.dbname, parent["id"])
    assert parent_row["state"] == "running", parent_row
    assert parent_row["error_code"] is None, parent_row


def test_real_publication_pins_the_child_and_closes_t075(t029_stack) -> None:
    """T075's partial: publish a real dependent version, then a newer child.

    The publication transaction, the frozen manifest bytes, the version and the
    ``version_dependencies`` edge are real. The five-category report is T068's
    guard-accepted fixture (real evaluation/case/run rows; terminal states seeded,
    as documented for that fixture); a *genuine sandbox* report for a call_app
    parent still needs the T081 child-terminal resume hook.
    """
    admin, creator = t075.creator(t029_stack)
    child_application = t025.create_application_or_fail(creator, "t078 published child")
    saved = t075.save_definition(creator, child_application["id"], t078.child_definition())
    assert saved.status == 200, saved.text
    child_v1 = t078.publish_version(t029_stack, creator, child_application["id"])

    parent_application, parent_v1 = t078.publish_application(
        t029_stack, creator, "t078 published parent",
        t078.parent_definition([child_v1]),
    )
    parent_row = t075.version_row(t029_stack.dbname, parent_v1)
    parent_manifest = json.loads(parent_row["manifest"])
    assert parent_manifest["subapp_versions"] == [child_v1], parent_manifest
    assert t075.dependency_edges(t029_stack.dbname, parent_v1) == [child_v1]
    parent_artifact = parent_row["manifest_artifact_id"]
    parent_bytes = t075.artifact_bytes(t029_stack, parent_artifact)
    assert hashlib.sha256(parent_bytes).hexdigest() == parent_row["manifest_sha256"]

    # A newer child version with a stricter input schema (requires `region`).
    stricter = t078.child_definition()
    stricter["input_schema"]["properties"]["region"] = {"type": "string"}
    stricter["input_schema"]["required"] = ["source", "region"]
    saved_v2 = t075.save_definition(creator, child_application["id"], stricter)
    assert saved_v2.status == 200, saved_v2.text
    child_v2 = t078.publish_version(t029_stack, creator, child_application["id"])
    assert child_v2 != child_v1

    # The parent version, its sealed bytes and its dependency edge are unchanged.
    assert t075.version_row(t029_stack.dbname, parent_v1) == parent_row
    assert t075.artifact_bytes(t029_stack, parent_artifact) == parent_bytes
    assert t075.dependency_edges(t029_stack.dbname, parent_v1) == [child_v1]
    assert t075.dependency_edges(t029_stack.dbname, child_v1) == []
    fetched = creator.client.get(f"/api/v1/versions/{parent_v1}")
    assert fetched.status == 200, fetched.text
    assert fetched.json["manifest_sha256"] == parent_row["manifest_sha256"]

    # Switching the parent draft to the newer child is refused by the mapping
    # gate (the new required field is not mapped): the old pin is never replaced
    # by "latest".
    draft = t025.get_draft(creator, parent_application)
    assert draft.status == 200, draft.text
    switched = t078.parent_definition([child_v2])
    saved_switch = t075.save_definition(creator, parent_application, switched)
    assert saved_switch.status == 200, saved_switch.text
    refused = t025.validate(creator, parent_application)
    assert refused.status == 200, refused.text
    errors = t075.errors_of(refused)
    t075.assert_error(
        errors, code="io_mapping_missing_field", pointer="/nodes/1/inputs/region",
        node_id="child",
    )

    # The really published child v1 is runnable: spawnChild resolves it through
    # the publication-created version row and creates a child Run.
    debug_application, parent_run = t078.debug_parent(
        t029_stack, creator, t078.parent_definition([child_v1]), {"source": "published"}
    )
    lease = t078.claim_run(t029_stack, run_id=parent_run["id"])
    spawned = t078.spawn(
        t029_stack, parent_run["id"], attempt_token=lease["attempt_token"],
        logical_key="published-child", application_version_id=child_v1,
        input={"source": "published"},
    )
    assert spawned.status == 202, spawned.text
    assert spawned.json["application_version_id"] == child_v1


def test_published_app_delegate_uses_only_spawn_child(t029_stack) -> None:
    from platform_worker.agents import (
        ChildApp,
        HTTPDelegateTransport,
        PublishedAppDelegate,
        ToolExecutionError,
    )
    from pathlib import Path

    _, creator, children, application_id, parent = _setup(t029_stack)
    lease = t078.claim_run(t029_stack, run_id=parent["id"])
    certificates = t029_stack.certificates
    worker = certificates.client("worker")
    transport = HTTPDelegateTransport(
        certificates.internal_url,
        ca_file=str(certificates.ca.cert),
        cert_file=str(worker.cert),
        key_file=str(worker.key),
    )
    delegate = PublishedAppDelegate(
        transport,
        run_id=parent["id"],
        attempt_token=lease["attempt_token"],
        children=[ChildApp(
            tool_name="call_app_1",
            application_version_id=children[0]["id"],
            output_schema={"type": "object", "properties": {"echo": {"type": "string"}},
                           "required": ["echo"]},
        )],
    )
    # The first call spawns a real child and reports a waiting outcome.
    try:
        delegate.execute("call_app_1", {"source": "delegate-source"})
    except ToolExecutionError as exc:
        assert exc.waiting is True, exc.code
        assert exc.code == "children_pending", exc.code
    else:  # pragma: no cover - a live child must not return success
        raise AssertionError("a live child was reported as a completed tool call")
    child_ids = t078.child_run_ids(t029_stack.dbname, parent["id"])
    assert len(child_ids) == 1, child_ids

    # Execute the child with the real supervisor, then the same invocation path
    # replays the same child and returns its structured result.
    report, _ = t078.execute_once(t029_stack)
    assert report["run_id"] == child_ids[0] and report["outcome"] == "succeeded", report
    result = delegate.execute("call_app_1", {"source": "delegate-source"})
    assert result == {"echo": "delegate-source"}, result
    assert t078.child_run_ids(t029_stack.dbname, parent["id"]) == child_ids

    # A different invocation path is a different logical key: a new, independent
    # child Run, never a reuse by application id.
    for path in ("call-2", "call-3"):
        try:
            delegate.execute_at(path, "call_app_1", {"source": path})
        except ToolExecutionError as exc:
            assert exc.waiting is True, exc.code
    assert len(t078.child_run_ids(t029_stack.dbname, parent["id"])) == 3

    # The guard: the adapter has no database path and the platform is the only
    # scheduler. This is a static boundary test, not behavioural evidence.
    source = Path("worker/platform_worker/agents/delegate.py").read_text(encoding="utf-8")
    for forbidden in ("psycopg", "asyncpg", "sqlalchemy", "sqlite3", "INSERT INTO", "platform.runs"):
        assert forbidden not in source, forbidden
    assert "spawn_child" in source and "child_result" in source


def test_agent_subapp_tool_creates_a_platform_child(t029_stack) -> None:
    """`agent.subapp_versions`: the model's tool call is a real spawnChild.

    The agent node (no python node) declares exactly one allowed sub-application
    version; the dev-stub provider plays a model that calls the first declared
    tool with `{}`. The executor must not create a Run itself: the child below is
    a platform Run made by spawnChild, with the per-call logical key.
    """
    admin, creator = t075.creator(t029_stack)
    model_version = t025.seed_model_version(t029_stack.dbname, creator.principal_id)
    child = t078.create_child_version(
        t029_stack, creator, definition=t078.agent_child_definition()
    )
    application_id, parent = t078.debug_parent(
        t029_stack,
        creator,
        t078.agent_parent_definition(model_version, child["id"]),
        {},
    )
    first, _ = t078.execute_once(t029_stack)
    assert first["status"] == "executed", first
    assert first["run_id"] == parent["id"], first
    assert first["outcome"] == "waiting_children", first
    child_ids = t078.child_run_ids(t029_stack.dbname, parent["id"])
    assert len(child_ids) == 1, child_ids
    child_row = t078.run_row(t029_stack.dbname, child_ids[0])
    assert child_row["application_version_id"] == child["id"], child_row
    assert child_row["owner_id"] == creator.principal_id, child_row
    receipt = t078.run_children_rows(t029_stack.dbname, parent["id"])[0]
    assert receipt["parent_invocation_key"] == "agent:analysis:subapp:stub-call-1", receipt
    assert json.loads(receipt["io_mapping"])["node_id"] == "analysis", receipt
    assert json.loads(receipt["io_mapping"])["fields"] == [], receipt
    sealed_input = t078.artifact_bytes(t029_stack, receipt["input_artifact_id"])
    assert sealed_input == b"{}", sealed_input
