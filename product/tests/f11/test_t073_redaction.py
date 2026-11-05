"""F11/T073: redaction, failure evidence and "audit is not a content backup".

The sentinel marker is a unique per-test token embedded in the real sealed bytes;
the positive control (a 200 body containing it) proves the read really happened,
so "the marker is absent everywhere else" is meaningful and not a broken-path
tautology. Server logs are the real ``cmd/api`` stdout log file; audit rows are
read back from PostgreSQL verbatim.
"""

from __future__ import annotations

import json
import uuid

from support import f00b, t028, t073
from support import stack as st

MARKER = "T073-REDACTION-MARKER-"


def _marker_content(run_id: str, suffix: str) -> bytes:
    return (MARKER + run_id + "-" + suffix + "\n").encode()


def _assert_absent(haystack: str, marker: str, where: str) -> None:
    assert marker not in haystack, f"{where} leaked the content marker"


def test_a_granted_read_never_copies_content_into_audit_or_logs(t069_stack) -> None:
    stack = t069_stack
    owner = t073.owner(stack)
    admin = t073.admin_session(stack)
    _owner, run, lease = t073.claimed_run(stack, owner)
    content_a = _marker_content(run["id"], "served")
    artifact_a = t073.upload_output(stack, lease, content_a)
    grant = t073.create_grant_ok(
        owner.creator,
        run["id"],
        admin.principal_id,
        object_scope=[t073.artifact_scope(artifact_a)],
    )
    read = t073.read_content(admin, grant["id"], t073.artifact_scope(artifact_a))
    assert read.status == 200, read.text
    # Positive control: the unique marker exists in the content source and was
    # really served to the grantee.
    assert content_a in read.raw, "the positive control read did not return the marker"

    marker = MARKER + run["id"]
    _assert_absent(t073.all_audit_text(stack.dbname), marker, "audit_events")
    _assert_absent(t073.run_event_text(stack.dbname), marker, "run_events")
    _assert_absent(stack.server.logs(), marker, "cmd/api log")

    # The content-read row is the typed whitelist, not a body copy.
    rows = t073.content_read_rows(stack.dbname)
    assert len(rows) == 1, rows
    metadata = t073.audit_metadata(rows[0])
    assert set(metadata) == t073.CONTENT_READ_AUDIT_KEYS, metadata
    assert all(
        value is None or isinstance(value, (str, int)) for value in metadata.values()
    ), metadata
    for forbidden in ("body", "text", "content", "object_key", "sha256", "params"):
        assert forbidden not in metadata, (forbidden, metadata)

    # The raw grant row names ids and purposes only.
    grant_row = t073.grant_row(stack.dbname, grant["id"])
    _assert_absent(json.dumps(grant_row), marker, "diagnostic_grants")

    # The maintenance process that finalized the artifact also never logged the
    # body (it is given only a key, hash and size).
    completed, _reports = t028.run_finalize(stack)
    _assert_absent(completed.stdout + completed.stderr, marker, "maintenance process log")


def test_a_failed_granted_read_carries_no_content_or_credential(
    t069_stack,
) -> None:
    stack = t069_stack
    owner = t073.owner(stack)
    admin = t073.admin_session(stack)
    _owner, run, lease = t073.claimed_run(stack, owner)
    content_a = _marker_content(run["id"], "failed")
    artifact_a = t073.upload_output(stack, lease, content_a)
    grant = t073.create_grant_ok(
        owner.creator,
        run["id"],
        admin.principal_id,
        object_scope=[t073.artifact_scope(artifact_a)],
    )
    marker = MARKER + run["id"]

    # The row says ready; the sealed bytes are gone (the T071 purge outcome).
    t073.remove_sealed_bytes(stack, artifact_a)
    failed = t073.read_content(
        admin,
        grant["id"],
        t073.artifact_scope(artifact_a),
        headers={"X-T073-Credential-Sentinel": "t073-credential-" + uuid.uuid4().hex},
    )
    assert failed.status == 404, failed.text
    error = st.assert_error(failed, 404, t073.CODE_CONTENT_UNAVAILABLE)
    assert set(error) == {"code", "message", "request_id"}, error
    _assert_absent(failed.text, marker, "error response")
    # The credential sentinel is a request header; it must never enter the log.
    _assert_absent(failed.text, "t073-credential-", "error response")

    log = stack.server.logs()
    _assert_absent(log, marker, "cmd/api log")
    _assert_absent(log, "t073-credential-", "cmd/api log")
    assert admin.cookie not in log
    assert f00b.ADMIN_PASSWORD not in log, "the administrator credential reached the request log"

    rows = t073.content_read_rows(stack.dbname)
    assert len(rows) == 1, rows
    metadata = t073.audit_metadata(rows[0])
    assert metadata["outcome"] == "failed", metadata
    assert metadata["error_code"] == "diagnostic_content_missing", metadata
    assert metadata["request_id"] == failed.header("X-Request-ID"), metadata
    _assert_absent(json.dumps(metadata), marker, "content-read audit")

    # Request content is not echoed by an error either: a malformed object query
    # is a located 422 whose body carries the field name only.
    secret_object = "artifact:not-a-uuid-" + marker
    malformed = admin.client.get(
        t073.content_path(grant["id"], secret_object, t073.GRANTED_PURPOSES[0])
    )
    error = st.assert_error(malformed, 422, t073.CODE_VALIDATION)
    assert error["details"] == {"field": "object"}, error
    _assert_absent(malformed.text, marker, "422 response")
    # The request log records the path without the query string, so the malformed
    # value never enters it.
    _assert_absent(stack.server.logs(), marker, "cmd/api log after 422")


def test_audit_is_not_a_content_backup_after_revocation_and_purge(t069_stack) -> None:
    stack = t069_stack
    owner = t073.owner(stack)
    admin = t073.admin_session(stack)
    _owner, run, lease = t073.claimed_run(stack, owner)
    content_a = _marker_content(run["id"], "purged")
    artifact_a = t073.upload_output(stack, lease, content_a)
    marker = MARKER + run["id"]

    first = t073.create_grant_ok(
        owner.creator,
        run["id"],
        admin.principal_id,
        object_scope=[t073.artifact_scope(artifact_a)],
    )
    read = t073.read_content(admin, first["id"], t073.artifact_scope(artifact_a))
    assert read.status == 200 and content_a in read.raw, read.text
    assert t073.revoke_grant(owner.creator, first["id"]).status == 204

    # A second grant is alive at the moment the resource is tombstoned: the read
    # predicate must refuse the deleted artifact even though the grant is valid.
    second = t073.create_grant_ok(
        owner.creator,
        run["id"],
        admin.principal_id,
        object_scope=[t073.artifact_scope(artifact_a)],
    )
    deleted = owner.creator.client.request(
        "DELETE", f"/api/v1/artifacts/{artifact_a}", csrf=owner.creator.csrf
    )
    assert deleted.status == 202, deleted.text
    assert t073.artifact_row(stack.dbname, artifact_a)["deleted_at"] is not None
    # T071 owns the physical purge. Until it runs, the tombstone alone already
    # refuses the read; this test additionally removes the sealed bytes (the exact
    # state T071's object_store scope leaves) and asserts the file is really gone.
    object_key = t073.artifact_object_key(stack.dbname, artifact_a)
    t073.remove_sealed_bytes(stack, artifact_a)
    assert not (stack.object_dir / object_key).exists()
    refused = t073.read_content(admin, second["id"], t073.artifact_scope(artifact_a))
    assert refused.status == 404, refused.text
    st.assert_error(refused, 404, t073.CODE_CONTENT_UNAVAILABLE)

    # What the audit trail retains is bounded, typed metadata: it cannot rebuild
    # the body. Every value is a UUID/token/null, no key names content, and the
    # marker never appears.
    rows = t073.content_read_rows(stack.dbname)
    assert len(rows) == 2, rows
    for row in rows:
        metadata = t073.audit_metadata(row)
        assert set(metadata) == t073.CONTENT_READ_AUDIT_KEYS, metadata
        for key, value in metadata.items():
            assert value is None or isinstance(value, str), (key, value)
            if isinstance(value, str):
                assert len(value) <= 128, (key, value)
                assert "\n" not in value, (key, value)
        _assert_absent(json.dumps(metadata), marker, "content-read audit")
    outcomes = [t073.audit_metadata(row)["outcome"] for row in rows]
    assert outcomes == ["served", "refused"], outcomes
    refused_metadata = t073.audit_metadata(rows[1])
    assert refused_metadata["error_code"] == t073.CODE_CONTENT_UNAVAILABLE, refused_metadata
    _assert_absent(t073.all_audit_text(stack.dbname), marker, "audit_events")
    _assert_absent(stack.server.logs(), marker, "cmd/api log")
