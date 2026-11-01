"""T019 acceptance 2: a grant is visible on the next read and a revoke denies it.

The whole file runs against a real PostgreSQL database and a real compiled API
process. The decision cache lives in that process, so these tests also prove that
the epoch bump (not a restart) is what makes a revocation visible.

The second test pins the cache itself: it crafts an expiry directly in the
database *without* bumping the epoch, so only a cached allow can still answer
200; a second API process on the same database (no cache) must answer 404.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from conftest import assert_error, sql, sql_rows
from t019_stack import (  # noqa: F401 - fixtures are used through their names
    APPLICATION,
    DEMO_VERSION_ID,
    READ,
    Stack,
    admin_session,
    api_server,
    audit_events,
    epoch_revision,
    fresh_database,
    get_application,
    go_binaries,
    grant_read,
    grant_read_or_fail,
    invite_and_activate,
    list_applications,
    revoke_grant,
    seed_application,
    stack,
    viewer_with_cookie,
)


def test_a_grant_appears_on_the_next_read_and_a_revoke_denies_the_next_one(
    stack: Stack,
) -> None:
    admin = admin_session(stack)
    owner = invite_and_activate(stack, admin, "grant.owner", roles=("creator",))
    reader = invite_and_activate(stack, admin, "grant.reader", roles=("user",))
    application_id = seed_application(
        stack, owner.principal_id, "shared-application", active_version_id=DEMO_VERSION_ID
    )

    # Before the grant: invisible in the list, 404 on the direct read.
    before_list = list_applications(reader)
    assert before_list.status == 200, before_list.text
    assert before_list.json == {"items": [], "next_cursor": None}
    assert_error(get_application(reader, application_id), 404, "resource_not_found")

    revision_before = epoch_revision(stack.dbname)
    created = grant_read(admin, reader.principal_id, application_id)
    assert created.status == 201, created.text
    assert set(created.json) == {"id", "state"}, created.json
    assert created.json["state"] == "granted"
    grant_id = created.json["id"]
    assert epoch_revision(stack.dbname) == revision_before + 1, (
        "the grant did not bump the acl policy epoch exactly once"
    )

    # The grant is a real row naming the tuple, with no expiry.
    stored = sql_rows(
        stack.dbname,
        "SELECT principal_id::text AS principal_id, resource_type, resource_id::text AS resource_id, "
        "action, expires_at::text AS expires_at FROM platform.resource_grants "
        f"WHERE id = '{grant_id}'::uuid",
    )
    assert stored == [
        {
            "principal_id": reader.principal_id,
            "resource_type": APPLICATION,
            "resource_id": application_id,
            "action": READ,
            "expires_at": None,
        }
    ], stored

    expected_item = {
        "id": application_id,
        "name": "shared-application",
        "active_version_id": DEMO_VERSION_ID,
    }
    after_list = list_applications(reader)
    assert after_list.status == 200
    assert after_list.json == {"items": [expected_item], "next_cursor": None}
    direct = get_application(reader, application_id)
    assert direct.status == 200, direct.text
    assert direct.json == expected_item
    assert direct.header("Cache-Control") == "no-store"

    # Two sequential reads before the revoke: both allowed. The next test proves
    # the second one was served from the in-process decision cache.
    assert get_application(reader, application_id).status == 200

    revoked = revoke_grant(admin, grant_id)
    assert revoked.status == 204, revoked.text
    assert revoked.raw == b"", "204 must not carry a body"
    assert revoked.header("Cache-Control") == "no-store"
    assert epoch_revision(stack.dbname) == revision_before + 2, (
        "grant + revoke must bump the epoch by exactly two revisions"
    )

    # The very next list and read in the same process are refused: this exercises
    # the epoch-keyed cache, not a process restart.
    assert list_applications(reader).json == {"items": [], "next_cursor": None}
    assert_error(get_application(reader, application_id), 404, "resource_not_found")

    # Audit: one creation and one revocation, both naming the tuple, no secrets.
    events = audit_events(stack.dbname)
    created_events = [event for event in events if event["action"] == "grant.created"]
    revoked_events = [event for event in events if event["action"] == "grant.revoked"]
    assert len(created_events) == 1, events
    assert len(revoked_events) == 1, events
    creation = created_events[0]
    assert creation["actor_id"] == admin.principal_id
    assert creation["resource_type"] == APPLICATION
    assert creation["resource_id"] == application_id
    metadata = json.loads(creation["metadata"])
    assert metadata == {
        "principal_id": reader.principal_id,
        "resource_type": APPLICATION,
        "resource_id": application_id,
        "action": READ,
    }, metadata
    revocation = json.loads(revoked_events[0]["metadata"])
    assert revocation["principal_id"] == reader.principal_id
    assert revocation["action"] == READ
    assert "expires_at" not in revocation


def test_the_second_read_is_answered_from_the_in_process_decision_cache(
    stack: Stack, api_server
) -> None:
    admin = admin_session(stack)
    owner = invite_and_activate(stack, admin, "cache.owner", roles=("creator",))
    reader = invite_and_activate(stack, admin, "cache.reader", roles=("user",))
    application_id = seed_application(stack, owner.principal_id, "cached-application")
    grant_id = grant_read_or_fail(admin, reader.principal_id, application_id)

    assert get_application(reader, application_id).status == 200
    assert get_application(reader, application_id).status == 200
    revision = epoch_revision(stack.dbname)

    # Make the stored grant expired *without* touching the epoch. Any fresh
    # decision must now deny; only the cached allow can still answer 200.
    sql(
        stack.dbname,
        "UPDATE platform.resource_grants "
        f"SET expires_at = now() - interval '1 minute' WHERE id = '{grant_id}'::uuid",
    )
    cached = get_application(reader, application_id)
    assert cached.status == 200, (
        "the second read did not come from the decision cache: "
        f"{cached.status} {cached.text}"
    )
    assert epoch_revision(stack.dbname) == revision, "the cache proof changed the epoch"

    # A second API process over the same database has no cache to serve, so the
    # same request must be refused there. That isolates "cache served it".
    second_server = api_server(stack.dsn)
    fresh = viewer_with_cookie(second_server.base_url, reader.cookie)
    assert_error(fresh.get(f"/api/v1/applications/{application_id}"), 404, "resource_not_found")

    # Revoking bumps the epoch, making the cached allow unreachable in the first
    # process too - still without a restart.
    assert revoke_grant(admin, grant_id).status == 204
    assert_error(get_application(reader, application_id), 404, "resource_not_found")


def test_an_expiring_grant_is_not_served_from_cache_after_its_expiry(stack: Stack) -> None:
    admin = admin_session(stack)
    owner = invite_and_activate(stack, admin, "expiry.owner", roles=("creator",))
    reader = invite_and_activate(stack, admin, "expiry.reader", roles=("user",))
    application_id = seed_application(stack, owner.principal_id, "expiring-application")

    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    grant_read_or_fail(admin, reader.principal_id, application_id, expires_at=expires_at)

    first = get_application(reader, application_id)
    assert first.status == 200, first.text
    revision = epoch_revision(stack.dbname)

    # Wait past the grant's own expiry. No epoch bump happens here: only the
    # cached allow's expires_at can retire the decision.
    time.sleep(5.4)
    assert epoch_revision(stack.dbname) == revision
    assert_error(get_application(reader, application_id), 404, "resource_not_found")
    assert list_applications(reader).json == {"items": [], "next_cursor": None}
