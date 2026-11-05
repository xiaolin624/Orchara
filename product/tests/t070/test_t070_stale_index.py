"""T070: a stale catalog index cannot start a revoked version.

A catalog/index result can only supply a candidate version id. The authoritative
``catalog.ResolveRunnableVersion`` read is executed at the run entry over the live
database, and the non-contract ``GET /api/v1/versions/{id}/runnable`` route is the
thin HTTP surface over exactly that function (T074 calls the Go function directly,
never the projection). This test remembers a version id the way a stale index
would and proves the guard refuses it after revocation, with distinct errors for
not-visible / archived / revoked.
"""

from __future__ import annotations

import uuid

from support import t068, t070
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)


def test_a_stale_index_version_id_is_refused_by_the_authoritative_guard(t070_stack) -> None:
    owner = t070.sales_owner(t070_stack, login="t070-stale")
    admin = t070.admin_session(t070_stack)
    sealed = t070.sealed_manifest(t070_stack, owner.creator, owner.application_id)
    version = t070.seed_version(
        t070_stack, owner_id=owner.creator.principal_id,
        application_id=owner.application_id, sealed=sealed,
    )
    t070.activate_version(t070_stack.dbname, owner.application_id, version["id"])

    # What a catalog index would hold: the id it saw when the version was live.
    stale_index_entry = version["id"]
    before = t070.runnable_version(owner.creator, stale_index_entry)
    assert before.status == 200, before.text
    assert before.json["id"] == version["id"], before.text
    assert before.json["manifest_sha256"] == version["manifest_sha256"], before.text

    # A caller without read access cannot use the guard to enumerate versions.
    foreign = t070.creator(t070_stack, login="t070-stale-foreign-" + uuid.uuid4().hex[:8])
    hidden = t070.runnable_version(foreign, stale_index_entry)
    assert hidden.status == 404, hidden.text
    assert hidden.json["error"]["code"] == "resource_not_found", hidden.text

    revoked = t070.revoke_version(admin, version["id"], reason="stale index must not start it")
    assert revoked.status == 200, revoked.text

    # The stale id is refused by the authoritative read, not by a projection.
    refused = t070.runnable_version(owner.creator, stale_index_entry)
    assert refused.status == 409, refused.text
    assert refused.json["error"]["code"] == "version_revoked", refused.text

    missing = t070.runnable_version(owner.creator, str(uuid.uuid4()))
    assert missing.status == 404, missing.text

    # The same guard distinguishes an archived application.
    t070.save_capability_card(owner.creator, owner.application_id, purpose="archived sales")
    second = t070.sealed_manifest(t070_stack, owner.creator, owner.application_id)
    live = t070.seed_version(
        t070_stack, owner_id=owner.creator.principal_id,
        application_id=owner.application_id, sealed=second, version_no=2,
    )
    t070.activate_version(t070_stack.dbname, owner.application_id, live["id"])
    assert t070.runnable_version(owner.creator, live["id"]).status == 200
    t068.archive_application(t070_stack.dbname, owner.application_id)
    archived = t070.runnable_version(owner.creator, live["id"])
    assert archived.status == 409, archived.text
    assert archived.json["error"]["code"] == "version_archived", archived.text
