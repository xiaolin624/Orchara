"""T101/J1: the request -> Run -> result journey.

The published version is produced the real way once per stack: the genuine
five-category evaluation runner (WSL2/runsc supervisor) writes a passed report,
the creator submits the publication, and an administrator approves it through the
contract review route. The request itself is the specification's fixed-version
entry (``POST /api/v1/requests`` with an explicit ``application_version_id`` 閳?IMPLEMENTATION-DETAILS 鎼?), not a shortcut: the request, its sealed input, the
dispatched Run, the supervisor execution and the result bytes are all real.

``J1b`` executes the known product gap on purpose. F08 seals the chat message
verbatim as the Run input (``internal/requests/submit.go:131`` -> ``:646-690``)
while the executor requires a JSON object
(``internal/presetexec/linear.go:177-187``), so a natural-language request is
accepted and dispatched but its Run necessarily fails with ``node_input_invalid``.
That journey is recorded as ``blocked`` (an F08<->F04B product gap), never skipped.
"""

from __future__ import annotations

import hashlib
import json

from support import execution_support as execution
from support import f00b, f02a, t031, t033, t042, t049, t068
from support import stack as st

TASK_DOCUMENT = json.dumps(
    {"source": "sales_sample.csv", "message": "閹跺﹨绻栨禒浠嬫敘閸烆喗妲戠紒鍡樼湽閹粯鍨氶幎銉ユ啞"},
    ensure_ascii=False,
)
NATURAL_LANGUAGE = "閹跺﹨绻栨禒浠嬫敘閸烆喗妲戠紒鍡樼湽閹粯鍨氶幎銉ユ啞"

_PUBLISHED: dict[tuple, dict] = {}


def _run_row(dbname: str, run_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text, state, error_code, kind, "
        "application_version_id::text AS application_version_id, "
        "input_artifact_id::text AS input_artifact_id, owner_id::text AS owner_id, "
        "finished_at::text AS finished_at "
        "FROM platform.runs "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def published_application(stack) -> dict:
    """Publish the sales application through the real evaluation/submit/approve chain."""
    key = (id(stack), stack.dbname)
    if key in _PUBLISHED:
        return _PUBLISHED[key]
    log_path = stack.server.log_path.parent / "t101-publication-pools.log"
    report = t068.genuine_sales_report(stack, login="t101.publisher", log_path=log_path)
    session = report["session"]
    application_id = report["application_id"]
    manifest_sha = report["manifest_sha256"]
    submitted = t068.submit_publication(
        session,
        application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report["report"]["id"],
    )
    assert submitted.status == 202, submitted.text
    admin = f00b.admin_session(stack)
    approved = t068.decided_review(
        admin,
        submitted.json["id"],
        decision="approve",
        manifest_sha256=manifest_sha,
    )
    assert approved.status == 200, approved.text
    version_id = approved.json["version_id"]
    assert version_id, approved.json
    _PUBLISHED[key] = {
        "session": session,
        "admin": admin,
        "application_id": application_id,
        "version_id": version_id,
        "manifest_sha256": manifest_sha,
        "evaluation_report_id": report["report"]["id"],
        "evaluation_status": report["report"]["status"],
        "cases": len(report["sealed"].get("cases", [])),
        "log_path": str(log_path),
    }
    return _PUBLISHED[key]


def _sealed_request_input(stack, request: dict) -> tuple[dict, bytes]:
    """Read the request's sealed input from the durable row and the object store."""
    from support import f08

    row = f08.request_row(stack.dbname, request["id"])
    artifact = f08.artifact_row(stack.dbname, row["input_artifact_id"])
    return artifact, (stack.object_dir / artifact["object_key"]).read_bytes()


def f08_artifact(dbname: str, artifact_id: str) -> dict:
    from support import f08

    return f08.artifact_row(dbname, artifact_id)


def run_request_to_result(stack, record) -> dict:
    from support import f08

    record.command(
        "python -m pytest tests/f12/test_t101_journeys.py -k request_to_result"
    )
    published = published_application(stack)
    admin = f00b.admin_session(stack)
    record.command(
        "real publish: POST /applications/{id}/evaluations (5 cases, supervisor) -> "
        "POST /applications/{id}/submit -> POST /reviews/{id}/decision (admin approve)"
    )
    record.note("published_version_id", published["version_id"])
    record.note("evaluation_cases", published["cases"])
    record.note("evaluation_report_id", published["evaluation_report_id"])
    record.note("evaluation_status", published["evaluation_status"])
    record.truth_source("sealed request input artifact bytes + platform.runs row via psql")
    record.truth_source("summary.csv bytes checked against tests/support/t049.py Decimal truth")

    requester = f00b.invite_and_activate(stack, admin, "t101.requester", roles=("user",))
    grant_id = f02a.grant_read(admin, requester.principal_id, published["application_id"])
    record.http("POST", "/api/v1/admin/grants (read the published application)", None)
    csv_id = t033.upload_artifact(stack, requester, t033.SALES_CSV)
    record.http("POST", "/api/v1/artifacts/uploads + PUT + complete + finalize", None)

    conversation = f08.conversation(requester)
    record.http("POST", "/api/v1/conversations", None)
    request_key = f00b.new_uuid()
    submitted = f08.submit_request(
        requester,
        conversation,
        TASK_DOCUMENT,
        artifact_ids=[csv_id],
        version_id=published["version_id"],
        key=request_key,
    )
    record.http("POST", "/api/v1/requests (explicit application_version_id)", submitted)
    request = f08.request_of(submitted)
    record.check(
        "the explicit-version request dispatches in the same transaction",
        submitted.status == 202 and request["state"] == "dispatched" and request["run_id"],
        request,
    )
    record.check(
        "the response names the pinned application version",
        submitted.header("X-Request-Application-Version-Id") == published["version_id"],
        submitted.header_values("X-Request-Application-Version-Id"),
    )
    request_row = f08.request_row(stack.dbname, request["id"])
    record.check(
        "the durable request row pins the requested version and the sealed message",
        request_row["requested_version_id"] == published["version_id"]
        and request_row["run_id"] == request["run_id"],
        request_row,
    )
    artifact, sealed = _sealed_request_input(stack, request)
    record.check(
        "the Run input is the exact sealed task document, byte for byte",
        sealed.decode("utf-8") == TASK_DOCUMENT
        and artifact["sha256"] == hashlib.sha256(TASK_DOCUMENT.encode("utf-8")).hexdigest(),
        {"artifact": artifact, "sealed_prefix": sealed[:80]},
    )

    completed, report = execution.execute_once(stack)
    record.command("cmd/supervisor exec --once (real WSL2/runsc execution of the dispatched Run)")
    record.check(
        "the real supervisor executes the dispatched Run to success",
        completed.returncode == 0 and report.get("outcome") == "succeeded",
        {"returncode": completed.returncode, "report": report},
    )
    run_id = request["run_id"]
    run = _run_row(stack.dbname, run_id)
    record.check(
        "the durable Run is a published version Run owned by the requester and succeeded",
        run["state"] == "succeeded"
        and run["error_code"] is None
        and run["kind"] == "published"
        and run["application_version_id"] == published["version_id"]
        and run["owner_id"] == requester.principal_id,
        run,
    )

    # -- result: the owner reads it, and the bytes match an independent truth --
    results = t042.wait_node_results(requester, run_id, expected=4)
    resume = t031.get_run(requester, run_id)
    record.http("GET", f"/api/v1/runs/{run_id}", resume)
    record.check(
        "the result is readable through the contract Run view",
        resume.status == 200 and resume.json["state"] == "succeeded",
        resume.text,
    )
    read_request = f08.get_request(requester, request["id"])
    record.http("GET", f"/api/v1/requests/{request['id']}", read_request)
    record.check(
        "the request view returns the dispatched state and the Run id",
        read_request.status == 200
        and read_request.json["state"] == "dispatched"
        and read_request.json["run_id"] == run_id,
        read_request.text,
    )
    by_label = {item["label"]: item for item in results["items"] if item["label"]}
    record.check(
        "the node results include the summary.csv and summary.json outputs",
        "summary.csv" in by_label and "summary.json" in by_label,
        sorted(by_label),
    )
    csv_artifact = by_label["summary.csv"]["artifact_id"]
    download = t033.artifact_content(requester, csv_artifact)
    record.http("GET", f"/api/v1/artifacts/{csv_artifact}/content", download)
    record.check(
        "the CSV download is 200 and its bytes are the documented summary",
        download.status == 200 and download.raw == t033.EXPECTED_SUMMARY_CSV,
        download.raw[:200],
    )
    t049.check_summary_csv(download.raw)
    record.truth_source("tests/support/t049.py:check_summary_csv (independent Decimal recomputation)")
    artifact_fact = f08_artifact(stack.dbname, csv_artifact)
    record.check(
        "the recorded artifact hash is the exact downloaded bytes",
        artifact_fact["sha256"] == st.sha256_hex(download.raw),
        artifact_fact,
    )
    output_id = t033.output_artifact_id(stack.dbname, run_id)
    record.check(
        "the Run has a linked final output artifact",
        output_id is not None,
        {"output_artifact_id": output_id},
    )
    output = json.loads(t033.artifact_content(requester, output_id).raw)
    t049.check_summary(output["summary"])

    # -- cross-principal boundary ------------------------------------------
    foreign_run = published["session"].client.get(f"/api/v1/runs/{run_id}")
    record.http("GET", f"/api/v1/runs/{run_id} (publisher)", foreign_run)
    st.assert_error(foreign_run, 404, "resource_not_found")
    foreign_artifact = published["session"].client.get(
        f"/api/v1/artifacts/{csv_artifact}/content"
    )
    st.assert_error(foreign_artifact, 404, "resource_not_found")
    anonymous = stack.client()
    anonymous_run = anonymous.get(f"/api/v1/runs/{run_id}")
    st.assert_error(anonymous_run, 401, "unauthenticated")
    record.check(
        "the publisher and an anonymous caller cannot read the requester's Run or result",
        foreign_run.status == 404
        and foreign_artifact.status == 404
        and anonymous_run.status == 401,
        {"publisher_run": foreign_run.status, "anonymous": anonymous_run.status},
    )

    # -- duplicate delivery: the committed Idempotency-Key replays the request -
    replay = f08.submit_request(
        requester,
        conversation,
        TASK_DOCUMENT,
        artifact_ids=[csv_id],
        version_id=published["version_id"],
        key=request_key,
    )
    record.http("POST", "/api/v1/requests (same Idempotency-Key)", replay)
    record.check(
        "a repeated delivery replays the committed request and creates no second Run",
        replay.status == 202
        and replay.json["id"] == request["id"]
        and len(f08.request_rows(stack.dbname, conversation)) == 1
        and len(f08.run_rows(stack.dbname, conversation)) == 1,
        {"replay": replay.json, "rows": len(f08.request_rows(stack.dbname, conversation))},
    )

    # -- failure boundary: a revoked version refuses a *fresh* request ------
    from support import f08 as f08mod

    second = f08mod.publish_version(
        stack,
        published["session"],
        name="t101 revoked-version boundary",
        definition=t042.linear_definition(input_schema=t068.TOOL_INPUT_SCHEMA),
    )
    f02a.grant_read(admin, requester.principal_id, second.application_id)
    f08mod.revoke_version(
        stack.dbname, second.version_id, published["session"].principal_id,
        reason="t101: failure boundary",
    )
    boundary_conversation = f08mod.conversation(requester)
    refused = f08mod.submit_request(
        requester,
        boundary_conversation,
        TASK_DOCUMENT,
        artifact_ids=[csv_id],
        version_id=second.version_id,
        key=f00b.new_uuid(),
    )
    record.http("POST", "/api/v1/requests (revoked version)", refused)
    st.assert_error(refused, 409, f08mod.ERROR_VERSION_REVOKED)
    record.check(
        "a revoked version refuses a fresh request and creates nothing",
        refused.status == 409
        and f08mod.request_rows(stack.dbname, boundary_conversation) == []
        and f08mod.run_rows(stack.dbname, boundary_conversation) == [],
        refused.text,
    )
    record.check(
        "the failed request created no second Run for the primary conversation",
        len(f08mod.run_rows(stack.dbname, conversation)) == 1,
    )

    record.note(
        "journey",
        {
            "request_id": request["id"],
            "run_id": run_id,
            "version_id": published["version_id"],
            "csv_sha256": artifact_fact["sha256"],
            "output_artifact_id": output_id,
            "grant_id": grant_id,
            "execution": report.get("outcome"),
        },
    )
    return {
        "request_id": request["id"],
        "run_id": run_id,
        "version_id": published["version_id"],
        "csv_artifact_id": csv_artifact,
        "output_artifact_id": output_id,
    }


def run_natural_language(stack, record) -> dict:
    """Execute the documented F08<->F04B gap and record it as blocked."""
    from support import f08

    record.command(
        "python -m pytest tests/f12/test_t101_journeys.py -k natural_language"
    )
    record.truth_source(
        "sealed request_input artifact bytes (the chat text) under the real object directory"
    )
    record.truth_source("platform.runs row: state/error_code/finished_at via psql")
    record.truth_source("GET /api/v1/runs/{id}/node-results (empty data rows for the rejected input)")
    published = published_application(stack)
    admin = f00b.admin_session(stack)
    requester = f00b.invite_and_activate(stack, admin, "t101.nl-requester", roles=("user",))
    f02a.grant_read(admin, requester.principal_id, published["application_id"])
    csv_id = t033.upload_artifact(stack, requester, t033.SALES_CSV)
    conversation = f08.conversation(requester)
    submitted = f08.submit_request(
        requester,
        conversation,
        NATURAL_LANGUAGE,
        artifact_ids=[csv_id],
        version_id=published["version_id"],
        key=f00b.new_uuid(),
    )
    record.http("POST", "/api/v1/requests (natural-language message, explicit version)", submitted)
    request = f08.request_of(submitted)
    record.check(
        "the natural-language request is accepted and dispatched (no test-side rejection)",
        submitted.status == 202 and request["state"] == "dispatched" and request["run_id"],
        request,
    )
    artifact, sealed = _sealed_request_input(stack, request)
    record.check(
        "F08 sealed the chat text verbatim as the Run input",
        sealed.decode("utf-8") == NATURAL_LANGUAGE
        and artifact["purpose"] == "request_input",
        {"artifact": artifact, "sealed": sealed},
    )

    completed, report = execution.execute_once(stack)
    record.command("cmd/supervisor exec --once (the dispatched natural-language Run)")
    run_id = request["run_id"]
    run = _run_row(stack.dbname, run_id)
    record.check(
        "the Run fails with node_input_invalid before any sandbox output",
        run["state"] == "failed"
        and run["error_code"] == "node_input_invalid"
        and run["finished_at"] is not None,
        {"run": run, "supervisor": report},
    )
    record.check(
        "the failed Run produced no output artifact",
        t033.output_artifact_id(stack.dbname, run_id) is None,
        {"output_artifact_id": t033.output_artifact_id(stack.dbname, run_id)},
    )
    node_read = requester.client.get(f"/api/v1/runs/{run_id}/node-results")
    record.http("GET", f"/api/v1/runs/{run_id}/node-results (failed Run)", node_read)
    record.check(
        "the node-results read model has no data rows for the rejected input",
        node_read.status == 200 and node_read.json["items"] == [],
        node_read.text[:300],
    )
    record.block(
        "F08<->F04B product gap: the chat message is sealed verbatim as the Run input "
        "(internal/requests/submit.go:131 -> :646-690) while the executor requires a JSON "
        "object (internal/presetexec/linear.go:177-187); the Run fails with "
        "node_input_invalid before any sandbox work. The ticket's journey is recorded as "
        "blocked; the JSON-task-document path in J1 is the specification's fixed-version "
        "entry, not a test-side workaround."
    )
    record.note(
        "gap",
        {
            "run_state": run["state"],
            "error_code": run["error_code"],
            "sealed_input_is_plain_text": sealed.decode("utf-8") == NATURAL_LANGUAGE,
            "supervisor": {
                "outcome": report.get("outcome"),
                "error_code": report.get("error_code"),
            },
            "second_gap_not_run": (
                "natural-language *routing* (no explicit application_version_id) also needs a "
                "route_control model that cannot be registered through the public API "
                "(internal/modelregistry/modelregistry.go:92-102); only SQL seeding exists."
            ),
        },
    )
    return {
        "request_id": request["id"],
        "run_id": run_id,
        "state": run["state"],
        "error_code": run["error_code"],
    }
