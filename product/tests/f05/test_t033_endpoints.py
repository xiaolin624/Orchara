"""T033: the run-scoped artifact endpoints (real mTLS, real rows, real bytes).

Each route is exercised with the certificate capacity the contract declares
(worker), a supervisor certificate (403), a mismatched role header (403), a
revoked/superseded attempt token (409 stale_attempt), a terminal run (409
run_terminal) and a foreign run's artifact (404). The artifact bytes are real:
they are uploaded through the signed staging link, finalized by the real
``artifact_finalize`` handler and read back through the signed sealed link.
"""

from __future__ import annotations

import hashlib
import ssl

import pytest

from support import f00b, t028, t029, t031
from support import stack as st
from support import t033
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)

ACCOUNT_UNAVAILABLE = (ssl.SSLError, ConnectionError, OSError)


def scoped_routes(run_id: str, artifact_id: str) -> list[tuple[str, str, dict]]:
    token = "00000000-0000-4000-8000-000000000000"
    return [
        ("POST", f"/internal/v1/runs/{run_id}/artifact-uploads",
         {"attempt_token": token, "media_type": "application/json",
          "size_bytes": 10, "purpose": "final_output"}),
        ("POST", f"/internal/v1/runs/{run_id}/artifacts/{artifact_id}/complete",
         {"attempt_token": token, "sha256": "a" * 64}),
        ("POST", f"/internal/v1/runs/{run_id}/artifact-downloads",
         {"attempt_token": token, "artifact_id": artifact_id}),
        ("POST", f"/internal/v1/runs/{run_id}/artifact-status",
         {"attempt_token": token, "artifact_id": artifact_id}),
        ("POST", f"/internal/v1/runs/{run_id}/artifacts",
         {"attempt_token": token, "artifact_id": artifact_id, "relation": "output"}),
    ]


def test_each_scoped_route_requires_the_worker_capacity(t033_stack) -> None:
    _owner, lease, artifact = t033.claimed_with_sales_csv(t033_stack)
    routes = scoped_routes(lease["run_id"], artifact)

    for method, path, body in routes:
        supervisor = t033.artifact_client(t033_stack, "supervisor")
        response = supervisor.request(method, path, body)
        assert response.status == 403, (path, response.status, response.text)
        assert response.json["error"]["code"] == "forbidden", response.json

        maintenance = t033.artifact_client(t033_stack, "maintenance")
        assert maintenance.request(method, path, body).status == 403, path

        # Certificate says worker, header claims supervisor: refused.
        mismatched = t033.artifact_client(t033_stack, "worker")
        assert mismatched.request(method, path, body, header_role="supervisor").status == 403, path

        # No client certificate: the TLS listener refuses the handshake.
        anonymous = t033.RunArtifactClient(
            t033_stack.certificates.internal_url, t033_stack.certificates.ca.cert, None
        )
        with pytest.raises(ACCOUNT_UNAVAILABLE):
            anonymous.request(method, path, body)


def test_upload_complete_status_register_round_trip(t033_stack) -> None:
    owner, lease, _artifact = t033.claimed_with_sales_csv(t033_stack)
    client = t033.artifact_client(t033_stack, "worker")
    run_id, token = lease["run_id"], lease["attempt_token"]
    content = b'{"total":"250.00","regions":{"east":"200.00"},"order_count":3}\n'

    created = client.create_upload(
        run_id,
        {"attempt_token": token, "media_type": "application/json",
         "size_bytes": len(content), "purpose": "final_output"},
    )
    assert created.status == 201, created.text
    ticket = created.json
    assert set(ticket) == {"artifact_id", "upload_url", "expires_at"}, ticket

    # Provenance was written in the same transaction; the artifact is owned by
    # the run owner and still uploading until the worker transfers bytes.
    provenance = t033.provenance_row(t033_stack.dbname, ticket["artifact_id"])
    assert provenance is not None, provenance
    assert provenance["run_id"] == run_id
    assert provenance["attempt_token"] == token
    assert provenance["purpose"] == "final_output"
    row = t033.artifact_row(t033_stack.dbname, ticket["artifact_id"])
    assert row["owner_id"] == owner.creator.principal_id
    assert row["state"] == "uploading"

    # A foreign run cannot address this artifact through its own path.
    other_lease = t033.claim_run_for_owner(
        t033_stack, owner, execution_unit_id="unit-other"
    )
    foreign = client.status(
        other_lease["run_id"],
        {"attempt_token": other_lease["attempt_token"], "artifact_id": ticket["artifact_id"]},
    )
    assert foreign.status == 404, foreign.text
    assert foreign.json["error"]["code"] == "artifact_not_found"
    foreign_register = client.register(
        other_lease["run_id"],
        {"attempt_token": other_lease["attempt_token"], "artifact_id": ticket["artifact_id"],
         "relation": "output"},
    )
    assert foreign_register.status == 404, foreign_register.text

    put = t033.put_link(ticket["upload_url"], content)
    assert put.status in (200, 204), put.status
    sha = hashlib.sha256(content).hexdigest()

    # Registering before the finalize guard activates the artifact is refused.
    early = client.register(
        run_id, {"attempt_token": token, "artifact_id": ticket["artifact_id"],
                 "relation": "output"},
    )
    assert early.status == 409, early.text
    assert early.json["error"]["code"] == "artifact_not_ready"

    completed = client.complete(
        run_id, ticket["artifact_id"], {"attempt_token": token, "sha256": sha}
    )
    assert completed.status == 202, completed.text
    assert completed.json["state"] == "scanning"

    # Same-hash replay is idempotent; a different hash is a conflict.
    replay = client.complete(
        run_id, ticket["artifact_id"], {"attempt_token": token, "sha256": sha}
    )
    assert replay.status == 202, replay.text
    conflict = client.complete(
        run_id, ticket["artifact_id"], {"attempt_token": token, "sha256": "b" * 64}
    )
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == "artifact_conflict"

    t028.finalize_once(t033_stack)
    ready = t033.wait_scoped_status(client, run_id, token, ticket["artifact_id"], {"ready"})
    assert ready["state"] == "ready", ready

    registered = client.register(
        run_id, {"attempt_token": token, "artifact_id": ticket["artifact_id"],
                 "relation": "output"},
    )
    assert registered.status == 200, registered.text
    t033.assert_artifact_shape(registered.json)
    assert registered.json["media_type"] == "application/json"
    assert registered.json["size_bytes"] == len(content)

    # The same registration replays; a different relation conflicts.
    replay_register = client.register(
        run_id, {"attempt_token": token, "artifact_id": ticket["artifact_id"],
                 "relation": "output"},
    )
    assert replay_register.status == 200, replay_register.text
    different = client.register(
        run_id, {"attempt_token": token, "artifact_id": ticket["artifact_id"],
                 "relation": "artifact"},
    )
    assert different.status == 409, different.text
    assert different.json["error"]["code"] == "artifact_conflict"
    links = [link for link in t033.run_artifact_links(t033_stack.dbname, run_id)
             if link["artifact_id"] == ticket["artifact_id"]]
    assert len(links) == 1 and links[0]["relation"] == "output", links
    assert links[0]["attempt_token"] == token

    # Upload validation is located and closed.
    bad_purpose = client.create_upload(
        run_id,
        {"attempt_token": token, "media_type": "application/json",
         "size_bytes": 1, "purpose": "tool_summary"},
    )
    assert bad_purpose.status == 422, bad_purpose.text
    assert bad_purpose.json["error"]["details"]["field"] == "purpose"
    bad_media = client.create_upload(
        run_id,
        {"attempt_token": token, "media_type": "application/zip",
         "size_bytes": 1, "purpose": "node_output"},
    )
    assert bad_media.status == 422, bad_media.text
    assert bad_media.json["error"]["details"]["field"] == "media_type"


def test_old_and_superseded_attempts_cannot_upload_or_register(t033_stack) -> None:
    owner, lease, _artifact = t033.claimed_with_sales_csv(t033_stack)
    client = t033.artifact_client(t033_stack, "worker")
    run_id, token = lease["run_id"], lease["attempt_token"]

    # An exit with started=false releases the job; a new attempt is claimed.
    exited = t029.execution_client(t033_stack, "supervisor").confirm_exit(
        token,
        execution_unit_id=lease["execution_unit_id"] if "execution_unit_id" in lease else "t033-e2e",
        evidence=t033.exit_evidence(lease, started=False),
    )
    assert exited.status == 200, exited.text
    second = t033.execution_client(t033_stack, "supervisor").claim_job(
        worker_id="t033-supervisor", execution_unit_id="t033-second-attempt",
        supported_compilers=[t031.COMPILER_VERSION],
    )
    assert second.status == 200, second.text
    second_lease = second.json
    assert second_lease["run_id"] == run_id, second_lease
    assert second_lease["attempt_token"] != token

    # The old (revoked) token is refused on every route.
    old_body = {"attempt_token": token, "media_type": "application/json",
                "size_bytes": 2, "purpose": "node_output"}
    old_upload = client.create_upload(run_id, old_body)
    assert old_upload.status == 409, old_upload.text
    assert old_upload.json["error"]["code"] == "stale_attempt"
    old_register = client.register(
        run_id, {"attempt_token": token, "artifact_id": t033.new_uuid(), "relation": "output"}
    )
    assert old_register.status == 409, old_register.text
    assert old_register.json["error"]["code"] == "stale_attempt"

    # An attempt that is no longer the job's current token is refused even while
    # its own lease is still live: the token alone is not authority. (The
    # database's one-live-attempt-per-run index makes a *live* attempt that is
    # not current impossible to create honestly, so the current-token binding is
    # what the test breaks.)
    st.sql(
        t033_stack.dbname,
        "UPDATE platform.jobs SET current_attempt_token = NULL WHERE id = "
        f"{st.quote_literal(second_lease['job_id'])}::uuid",
    )
    superseded = client.create_upload(
        run_id,
        {"attempt_token": second_lease["attempt_token"], "media_type": "application/json",
         "size_bytes": 2, "purpose": "node_output"},
    )
    assert superseded.status == 409, superseded.text
    assert superseded.json["error"]["code"] == "stale_attempt"

    # A malformed token/run id is the same refusal, not a 500.
    assert client.create_upload(run_id, {"attempt_token": "not-a-uuid", "media_type":
                                         "application/json", "size_bytes": 1,
                                         "purpose": "node_output"}).status == 409


def test_download_and_status_are_limited_to_the_runs_inputs(t033_stack) -> None:
    owner, lease, csv_artifact = t033.claimed_with_sales_csv(t033_stack)
    client = t033.artifact_client(t033_stack, "worker")
    run_id, token = lease["run_id"], lease["attempt_token"]

    # Both the canonical run input and the uploaded task input are inputs.
    canonical = t033.run_state_row(t033_stack.dbname, run_id)["input_artifact_id"]
    for artifact_id in (canonical, csv_artifact):
        status = client.status(run_id, {"attempt_token": token, "artifact_id": artifact_id})
        assert status.status == 200, (artifact_id, status.text)
        download = client.download(run_id, {"attempt_token": token, "artifact_id": artifact_id})
        assert download.status == 200, download.text
        assert set(download.json) == {"url", "expires_at"}, download.json
        payload = t033.get_link(download.json["url"])
        assert payload.status == 200, payload.status
        if artifact_id == csv_artifact:
            assert payload.raw == t033.SALES_CSV
            expected = hashlib.sha256(t033.SALES_CSV).hexdigest()
            assert t033.artifact_row(t033_stack.dbname, artifact_id)["sha256"] == expected

    # An output of the same run is not an input: the execution protocol may not
    # read its own results back.
    output_id, _sha = t033.upload_run_artifact(
        t033_stack, client, lease, content=b'{"total":"250.00"}\n',
        purpose="final_output", relation="output",
    )
    output_download = client.download(
        run_id, {"attempt_token": token, "artifact_id": output_id}
    )
    assert output_download.status == 404, output_download.text
    assert output_download.json["error"]["code"] == "artifact_not_found"
    output_status = client.status(
        run_id, {"attempt_token": token, "artifact_id": output_id}
    )
    assert output_status.status == 200, output_status.text

    foreign_download = client.download(
        run_id, {"attempt_token": token, "artifact_id": t033.new_uuid()}
    )
    assert foreign_download.status == 404, foreign_download.text


def test_terminal_and_foreign_runs_are_refused(t033_stack) -> None:
    owner, lease, _artifact = t033.claimed_with_sales_csv(t033_stack)
    client = t033.artifact_client(t033_stack, "worker")
    run_id, token = lease["run_id"], lease["attempt_token"]

    # A second run's token cannot act on this run.
    other = t033.claim_run_for_owner(t033_stack, owner, execution_unit_id="unit-foreign")
    foreign_token = other["attempt_token"]
    foreign = client.create_upload(
        run_id,
        {"attempt_token": foreign_token, "media_type": "application/json",
         "size_bytes": 2, "purpose": "node_output"},
    )
    assert foreign.status == 409, foreign.text
    assert foreign.json["error"]["code"] == "stale_attempt"

    # Terminal run: the token is still "current" but no artifact is accepted.
    st.sql(
        t033_stack.dbname,
        "UPDATE platform.runs SET state = 'succeeded', phase = 'idle', "
        f"finished_at = now() WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    for method, path, body in scoped_routes(run_id, t033.new_uuid()):
        body["attempt_token"] = token
        response = client.request(method, path, body)
        assert response.status == 409, (path, response.status, response.text)
        assert response.json["error"]["code"] == "run_terminal", (path, response.json)


# ---------------------------------------------------------------------------
# GET /api/v1/runs/{run_id}/artifacts
# ---------------------------------------------------------------------------
def test_list_run_artifacts_is_owner_only_with_a_stable_cursor(t033_stack) -> None:
    owner, lease, _artifact = t033.claimed_with_sales_csv(t033_stack)
    client = t033.artifact_client(t033_stack, "worker")
    run_id = lease["run_id"]

    final_id, _ = t033.upload_run_artifact(
        t033_stack, client, lease, content=b'{"total":"250.00"}\n',
        purpose="final_output", relation="output",
    )
    csv_id, _ = t033.upload_run_artifact(
        t033_stack, client, lease, content=b"region,total\neast,200.00\n",
        purpose="node_output", media_type="text/csv", relation="artifact",
    )
    log_id, _ = t033.upload_run_artifact(
        t033_stack, client, lease, content=b"stdout:\nok\n",
        purpose="log", media_type="text/plain", relation="log",
    )

    response = t033.list_run_artifacts(owner.creator, run_id)
    assert response.status == 200, response.text
    body = response.json
    assert set(body) == {"items", "next_cursor"}, body
    ids = [item["id"] for item in body["items"]]
    assert set(ids) == {final_id, csv_id}, ids
    assert log_id not in ids, "stdout/stderr must not be listed as run data"
    for item in body["items"]:
        t033.assert_artifact_shape(item)
    assert body["next_cursor"] is None, body

    # The cursor is stable and opaque: limit=1 returns one page at a time.
    first_page = t033.list_run_artifacts(owner.creator, run_id, limit=1)
    assert first_page.status == 200, first_page.text
    assert len(first_page.json["items"]) == 1
    cursor = first_page.json["next_cursor"]
    assert isinstance(cursor, str) and cursor
    second_page = t033.list_run_artifacts(owner.creator, run_id, limit=1, cursor=cursor)
    assert second_page.status == 200, second_page.text
    assert len(second_page.json["items"]) == 1
    assert second_page.json["items"][0]["id"] != first_page.json["items"][0]["id"]
    assert second_page.json["next_cursor"] is None
    assert t033.list_run_artifacts(owner.creator, run_id, cursor="not-a-cursor").status == 400

    # Another owner and an administrator (not the owner) see the same 404 as an
    # unknown run; the admin does not get a superuser read path.
    other = t033.minimal_owner(t033_stack)
    assert t033.list_run_artifacts(other.creator, run_id).status == 404
    assert t033.list_run_artifacts(owner.admin, run_id).status == 404
    unknown = t033.list_run_artifacts(owner.creator, t033.new_uuid())
    assert unknown.status == 404 and unknown.json["error"]["code"] == "resource_not_found"

    # Unauthenticated is 401, and the API-key scope is required.
    anonymous = st.HttpClient(t033_stack.server.base_url)
    assert anonymous.get(f"/api/v1/runs/{run_id}/artifacts").status == 401
