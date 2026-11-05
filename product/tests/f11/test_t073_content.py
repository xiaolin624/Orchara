"""F11/T073: the grantee-only diagnostic content door.

Cycle 2: reading the body under a live grant, the per-read audit row with
run/attempt/operation correlation, and the distinct refusal codes for
out-of-scope, wrong-purpose, expired, revoked and tombstoned states. Real stack:
compiled API over HTTP, real sealed bytes created by a real worker upload and a
real maintenance finalize, real PostgreSQL rows read back independently.
"""

from __future__ import annotations

import datetime
import json
import uuid

from support import t073
from support import stack as st

MARKER = "T073-CONTENT-MARKER-"


def _setup_run_with_two_outputs(stack):
    owner = t073.owner(stack)
    admin = t073.admin_session(stack)
    _owner, run, lease = t073.claimed_run(stack, owner)
    content_a = b"" + (MARKER + run["id"] + "-A\n").encode()
    content_b = b"" + (MARKER + run["id"] + "-B\n").encode()
    artifact_a = t073.upload_output(stack, lease, content_a)
    artifact_b = t073.upload_output(stack, lease, content_b)
    return owner, admin, run, lease, artifact_a, artifact_b, content_a, content_b


def test_a_granted_read_serves_the_real_bytes_and_audits_run_attempt_operation(
    t069_stack,
) -> None:
    stack = t069_stack
    owner, admin, run, lease, artifact_a, _artifact_b, content_a, _content_b = (
        _setup_run_with_two_outputs(stack)
    )
    # A real capability operation attempt shares the live attempt token, so the
    # audit's operation_id/attempt_token are join keys, not caller claims.
    operation_id = t073.tool_operation(stack, lease)

    grant = t073.create_grant_ok(
        owner.creator,
        run["id"],
        admin.principal_id,
        purposes=[t073.GRANTED_PURPOSES[0]],
        object_scope=[t073.artifact_scope(artifact_a)],
    )
    read = t073.read_content(admin, grant["id"], t073.artifact_scope(artifact_a))
    assert read.status == 200, read.text
    # Positive control: the marker really is the served body.
    assert read.raw == content_a, (read.raw, content_a)
    assert read.header("Content-Type") == "application/octet-stream"
    assert read.header("Content-Disposition") == "attachment"
    assert read.header("Cache-Control") == "no-store"
    assert read.header("X-Diagnostic-Grant") == grant["id"]
    assert int(read.header("Content-Length")) == len(content_a)

    rows = t073.content_read_rows(stack.dbname)
    assert len(rows) == 1, rows
    metadata = t073.audit_metadata(rows[0])
    assert set(metadata) == t073.CONTENT_READ_AUDIT_KEYS, metadata
    assert metadata["grant_id"] == grant["id"]
    assert metadata["purpose"] == t073.GRANTED_PURPOSES[0]
    assert metadata["run_id"] == run["id"]
    assert metadata["resource_type"] == "artifact"
    assert metadata["resource_id"] == artifact_a
    assert metadata["operation_id"] == operation_id
    assert metadata["attempt_token"] == lease["attempt_token"]
    assert metadata["request_id"] == read.header("X-Request-ID")
    assert metadata["request_id"], metadata
    assert metadata["outcome"] == "served"
    assert metadata["error_code"] is None
    assert rows[0]["actor_id"] == admin.principal_id

    # Every read is audited: the second read appends a second row, not a replay.
    second = t073.read_content(admin, grant["id"], t073.artifact_scope(artifact_a))
    assert second.status == 200, second.text
    assert second.raw == content_a
    assert len(t073.content_read_rows(stack.dbname)) == 2


def test_the_object_scope_is_a_snapshot_and_an_absent_header_is_derived_once(
    t069_stack,
) -> None:
    stack = t069_stack
    owner, admin, run, lease, artifact_a, artifact_b, content_a, _content_b = (
        _setup_run_with_two_outputs(stack)
    )

    # No X-Diagnostic-Object-Scope header: the platform snapshots the run's
    # current objects once, at grant time.
    derived = t073.create_grant_ok(
        owner.creator,
        run["id"],
        admin.principal_id,
        object_scope=None,
    )
    stored = t073.grant_row(stack.dbname, derived["id"])
    scope = t073.grant_object_scope(stored)
    assert t073.artifact_scope(artifact_a) in scope, scope
    assert t073.artifact_scope(artifact_b) in scope, scope

    # An object uploaded after the grant is not silently added to the snapshot.
    late = t073.upload_output(stack, lease, b"late t073 body\n")
    assert t073.artifact_scope(late) not in t073.grant_object_scope(
        t073.grant_row(stack.dbname, derived["id"])
    )
    refused = t073.read_content(admin, derived["id"], t073.artifact_scope(late))
    st.assert_error(refused, 403, t073.CODE_SCOPE)

    # The explicit snapshot is what the user chose, not the whole run.
    explicit = t073.create_grant_ok(
        owner.creator,
        run["id"],
        admin.principal_id,
        object_scope=[t073.artifact_scope(artifact_a)],
    )
    assert t073.grant_object_scope(t073.grant_row(stack.dbname, explicit["id"])) == [
        t073.artifact_scope(artifact_a)
    ]
    served = t073.read_content(admin, explicit["id"], t073.artifact_scope(artifact_a))
    assert served.status == 200 and served.raw == content_a, served.text


def test_out_of_scope_and_wrong_purpose_reads_are_refused_with_distinct_codes(
    t069_stack,
) -> None:
    stack = t069_stack
    owner, admin, run, lease, artifact_a, artifact_b, content_a, content_b = (
        _setup_run_with_two_outputs(stack)
    )
    grant = t073.create_grant_ok(
        owner.creator,
        run["id"],
        admin.principal_id,
        purposes=[t073.GRANTED_PURPOSES[0]],
        object_scope=[t073.artifact_scope(artifact_a)],
    )

    # Artifact B belongs to the same run but is outside the chosen object scope.
    out_of_scope = t073.read_content(admin, grant["id"], t073.artifact_scope(artifact_b))
    assert st.assert_error(out_of_scope, 403, t073.CODE_SCOPE)["code"] == t073.CODE_SCOPE
    assert content_b not in out_of_scope.raw
    assert content_a not in out_of_scope.raw

    # A purpose the owner did not grant is refused even for an in-scope object.
    wrong_purpose = t073.read_content(
        admin, grant["id"], t073.artifact_scope(artifact_a), purpose=t073.OTHER_PURPOSE
    )
    st.assert_error(wrong_purpose, 403, t073.CODE_PURPOSE)
    assert content_a not in wrong_purpose.raw

    # A malformed object/purpose is a located 422, never a read.
    malformed_object = admin.client.get(
        t073.content_path(grant["id"], "memory:not-a-uuid", t073.GRANTED_PURPOSES[0])
    )
    error = st.assert_error(malformed_object, 422, t073.CODE_VALIDATION)
    assert error["details"]["field"] == "object", error
    malformed_purpose = admin.client.get(
        t073.content_path(grant["id"], t073.artifact_scope(artifact_a), "Not A Token")
    )
    error = st.assert_error(malformed_purpose, 422, t073.CODE_VALIDATION)
    assert error["details"]["field"] == "purpose", error

    # The owner of the run is not the grantee and is not an administrator at all:
    # the credential-shaped admin check runs before any grant lookup.
    owner_attempt = t073.read_content(owner.creator, grant["id"], t073.artifact_scope(artifact_a))
    st.assert_error(owner_attempt, 403, "forbidden")

    # A second administrator who is not the named grantee learns nothing: the
    # same 404 as a missing grant, so a grant id cannot be used to enumerate.
    other_admin = t073.second_admin(stack, admin)
    st.assert_error(
        t073.read_content(other_admin, grant["id"], t073.artifact_scope(artifact_a)),
        404,
        t073.CODE_RESOURCE_NOT_FOUND,
    )

    # A well-formed but unknown grant id is the same 404.
    st.assert_error(
        t073.read_content(admin, str(uuid.uuid4()), t073.artifact_scope(artifact_a)),
        404,
        t073.CODE_RESOURCE_NOT_FOUND,
    )

    # The refusals are audited with their own outcome and code; the served read is
    # not. Exactly one content byte stream happened (none in this test).
    rows = t073.content_read_rows(stack.dbname)
    assert len(rows) == 2, rows
    codes = sorted(t073.audit_metadata(row)["error_code"] for row in rows)
    assert codes == [t073.CODE_PURPOSE, t073.CODE_SCOPE], codes
    assert all(t073.audit_metadata(row)["outcome"] == "refused" for row in rows)
    assert all(t073.audit_metadata(row)["request_id"] for row in rows)
    assert all(t073.audit_metadata(row)["run_id"] == run["id"] for row in rows)


def test_an_expired_grant_is_refused_after_its_deadline(t069_stack) -> None:
    stack = t069_stack
    owner, admin, run, lease, artifact_a, _artifact_b, content_a, _content_b = (
        _setup_run_with_two_outputs(stack)
    )
    # A real six-second window with a >=1.5s post-deadline margin: no race.
    deadline = t073.now_utc() + datetime.timedelta(seconds=6)
    grant = t073.create_grant_ok(
        owner.creator,
        run["id"],
        admin.principal_id,
        expires_at=t073.rfc3339(deadline),
        object_scope=[t073.artifact_scope(artifact_a)],
    )
    alive = t073.read_content(admin, grant["id"], t073.artifact_scope(artifact_a))
    assert alive.status == 200 and alive.raw == content_a, alive.text

    t073.sleep_past(deadline, margin=1.5)
    expired = t073.read_content(admin, grant["id"], t073.artifact_scope(artifact_a))
    st.assert_error(expired, 403, t073.CODE_GRANT_EXPIRED)
    assert content_a not in expired.raw

    items = [item for item in t073.list_grant_items(admin) if item["id"] == grant["id"]]
    assert len(items) == 1 and items[0]["state"] == t073.EXPIRED, items

    rows = t073.content_read_rows(stack.dbname)
    assert len(rows) == 2, rows
    served = [t073.audit_metadata(row) for row in rows if t073.audit_metadata(row)["outcome"] == "served"]
    refused = [t073.audit_metadata(row) for row in rows if t073.audit_metadata(row)["outcome"] == "refused"]
    assert len(served) == 1 and len(refused) == 1, rows
    assert refused[0]["error_code"] == t073.CODE_GRANT_EXPIRED, refused[0]
    assert refused[0]["grant_id"] == grant["id"], refused[0]


def test_a_revoked_grant_is_refused_immediately_and_forever(t069_stack) -> None:
    stack = t069_stack
    owner, admin, run, lease, artifact_a, _artifact_b, content_a, _content_b = (
        _setup_run_with_two_outputs(stack)
    )
    grant = t073.create_grant_ok(
        owner.creator,
        run["id"],
        admin.principal_id,
        object_scope=[t073.artifact_scope(artifact_a)],
    )
    served = t073.read_content(admin, grant["id"], t073.artifact_scope(artifact_a))
    assert served.status == 200 and served.raw == content_a, served.text

    assert t073.revoke_grant(owner.creator, grant["id"]).status == 204
    refused = t073.read_content(admin, grant["id"], t073.artifact_scope(artifact_a))
    st.assert_error(refused, 403, t073.CODE_GRANT_REVOKED)
    assert content_a not in refused.raw

    # Revocation is terminal: another attempt is refused with the same code.
    again = t073.read_content(admin, grant["id"], t073.artifact_scope(artifact_a))
    st.assert_error(again, 403, t073.CODE_GRANT_REVOKED)

    rows = t073.content_read_rows(stack.dbname)
    assert len(rows) == 3, rows
    outcomes = [t073.audit_metadata(row)["outcome"] for row in rows]
    assert outcomes.count("served") == 1 and outcomes.count("refused") == 2, outcomes
    assert all(
        t073.audit_metadata(row)["error_code"] == t073.CODE_GRANT_REVOKED
        for row in rows
        if t073.audit_metadata(row)["outcome"] == "refused"
    )


def test_a_tombstoned_run_ends_the_grant_and_the_artifact_read_refuses(
    t069_stack,
) -> None:
    stack = t069_stack
    owner, admin, run, lease, artifact_a, _artifact_b, content_a, _content_b = (
        _setup_run_with_two_outputs(stack)
    )
    grant = t073.create_grant_ok(
        owner.creator,
        run["id"],
        admin.principal_id,
        object_scope=[t073.artifact_scope(artifact_a)],
    )
    # The tombstone T069 owns: the read predicate must re-read it, never trust a
    # grant that was minted while the run was alive.
    st.sql(
        stack.dbname,
        "UPDATE platform.runs SET deleted_at = now() "
        f"WHERE id = {st.quote_literal(run['id'])}::uuid",
    )
    refused = t073.read_content(admin, grant["id"], t073.artifact_scope(artifact_a))
    st.assert_error(refused, 403, t073.CODE_RUN_DELETED)
    assert content_a not in refused.raw

    rows = t073.content_read_rows(stack.dbname)
    assert len(rows) == 1, rows
    metadata = t073.audit_metadata(rows[0])
    assert metadata["error_code"] == t073.CODE_RUN_DELETED, metadata
    assert metadata["outcome"] == "refused", metadata


def test_an_admin_without_a_grant_sees_metadata_only_on_the_real_surfaces(
    t069_stack,
) -> None:
    stack = t069_stack
    owner, admin, run, lease, artifact_a, _artifact_b, content_a, _content_b = (
        _setup_run_with_two_outputs(stack)
    )
    # The owner's own read is the positive control that the marker exists and the
    # content path works at all.
    own_read = owner.creator.client.get(f"/api/v1/artifacts/{artifact_a}/content")
    assert own_read.status == 200, own_read.text
    assert own_read.raw == content_a

    # Every pre-existing administrator content surface refuses the body.
    body_responses = {
        "artifact": admin.client.get(f"/api/v1/artifacts/{artifact_a}"),
        "download": admin.client.get(f"/api/v1/artifacts/{artifact_a}/download"),
        "content": admin.client.get(f"/api/v1/artifacts/{artifact_a}/content"),
        "run": admin.client.get(f"/api/v1/runs/{run['id']}"),
        "run_artifacts": admin.client.get(f"/api/v1/runs/{run['id']}/artifacts"),
        "node_results": admin.client.get(f"/api/v1/runs/{run['id']}/node-results"),
    }
    for name, response in body_responses.items():
        assert response.status == 404, (name, response.status, response.text)
        assert content_a not in response.raw, name

    # The redacted background-job read is the metadata contract: four fields only.
    job = t073.background_job_for_artifact(stack.dbname, artifact_a)
    assert job is not None
    job_read = admin.client.get(f"/api/v1/background-jobs/{job['id']}")
    assert job_read.status == 200, job_read.text
    assert set(job_read.json) == {"id", "kind", "state", "error_code"}, job_read.json
    assert content_a not in job_read.raw

    # Without any grant the grantee-side list is empty; the content door has no
    # id it could open, and a guessed grant id is the located 404.
    assert t073.list_grant_items(admin) == []
    st.assert_error(
        t073.read_content(admin, str(uuid.uuid4()), t073.artifact_scope(artifact_a)),
        404,
        t073.CODE_RESOURCE_NOT_FOUND,
    )

    # The new metadata surface proves the boundary: after a grant exists the admin
    # can see the object ids/purposes/expiry, but no body without opening the
    # audited door, and the list body itself carries no content.
    grant = t073.create_grant_ok(
        owner.creator,
        run["id"],
        admin.principal_id,
        object_scope=[t073.artifact_scope(artifact_a)],
    )
    items = t073.list_grant_items(admin)
    assert len(items) == 1 and items[0]["id"] == grant["id"], items
    assert items[0]["object_scope"] == [t073.artifact_scope(artifact_a)], items
    assert content_a not in json.dumps(items).encode()

    # Once the grant exists the audited door opens exactly one body.
    served = t073.read_content(admin, grant["id"], t073.artifact_scope(artifact_a))
    assert served.status == 200 and served.raw == content_a, served.text
