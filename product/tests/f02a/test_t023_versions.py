"""T023 acceptance: list/detail/active version agree and never leak the draft.

The published rows are seeded with real SQL into the real tables (F02B owns the
publish write path; T023 owns the read model). Every read then goes through the
compiled API, so the ACL, the pagination and the contract shape are exercised
together.
"""

from __future__ import annotations

from support import f02a as f
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    f02a_stack,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _setup(stack, *, count: int = 3):
    admin = f.admin_session(stack)
    owner = f.invite_and_activate(stack, admin, "t023-version-owner")
    application = f.create_application_or_fail(owner, "versioned app")
    versions = f.seed_published_versions(
        stack.dbname, application["id"], owner_id=owner.principal_id, count=count
    )
    return admin, owner, application, versions


def test_application_list_and_version_detail_agree_with_the_seeded_rows(f02a_stack) -> None:
    _admin, owner, application, versions = _setup(f02a_stack)
    latest = versions[-1]

    detail = f.get_application(owner, application["id"])
    assert detail.status == 200, detail.text
    assert detail.json["active_version_id"] == latest["id"]

    listing = f.list_versions(owner, application["id"])
    assert listing.status == 200, listing.text
    ids = [item["id"] for item in listing.json["items"]]
    assert ids == [entry["id"] for entry in reversed(versions)], ids
    active_entry = next(item for item in listing.json["items"] if item["id"] == latest["id"])
    assert active_entry["version_no"] == latest["version_no"]
    assert active_entry["manifest_sha256"] == latest["manifest_sha256"]
    assert active_entry["manifest"] == latest["manifest"]

    version = f.get_version(owner, latest["id"])
    assert version.status == 200, version.text
    assert set(version.json) == {
        "id", "application_id", "version_no", "manifest_sha256", "manifest",
    }, version.json
    assert version.json == active_entry


def test_versions_are_newest_first_with_a_stable_cursor_and_bounded_limit(f02a_stack) -> None:
    _admin, owner, application, versions = _setup(f02a_stack, count=3)
    expected = [entry["id"] for entry in reversed(versions)]

    first = f.list_versions(owner, application["id"], limit=2)
    assert first.status == 200, first.text
    assert [item["id"] for item in first.json["items"]] == expected[:2]
    cursor = first.json["next_cursor"]
    assert isinstance(cursor, str) and cursor

    second = f.list_versions(owner, application["id"], cursor=cursor, limit=2)
    assert second.status == 200, second.text
    assert [item["id"] for item in second.json["items"]] == expected[2:]
    assert second.json["next_cursor"] is None

    # The cursor is a continuation, not a column-order query: replaying page one
    # gives the same rows, and the maximum limit is accepted.
    replay = f.list_versions(owner, application["id"], limit=100)
    assert replay.status == 200
    assert [item["id"] for item in replay.json["items"]] == expected
    assert replay.json["next_cursor"] is None

    for bad in ("0", "101", "abc"):
        response = f.list_versions(owner, application["id"], limit=bad)
        assert response.status == 422, f"limit={bad!r}: {response.text}"
        assert response.json["error"]["details"]["field"] == "limit"
    # An empty limit value follows the same convention as the applications list
    # (F01's ParseLimit): an omitted/blank value means the default.
    default_limit = f.list_versions(owner, application["id"], limit="")
    assert default_limit.status == 200, default_limit.text
    # The cursor is a continuation, not a column-order query: a malformed token
    # is a typed 400.
    invalid_cursor = f.list_versions(owner, application["id"], cursor="not-a-cursor")
    assert invalid_cursor.status == 400, invalid_cursor.text
    assert invalid_cursor.json["error"]["code"] == "invalid_cursor"


def test_an_application_without_versions_returns_an_honest_empty_page(f02a_stack) -> None:
    admin = f.admin_session(f02a_stack)
    owner = f.invite_and_activate(f02a_stack, admin, "t023-empty-version-owner")
    application = f.create_application_or_fail(owner, "never published")
    response = f.list_versions(owner, application["id"])
    assert response.status == 200, response.text
    assert response.json == {"items": [], "next_cursor": None}
    assert f.get_application(owner, application["id"]).json["active_version_id"] is None


def test_version_reads_never_expose_the_draft_body(f02a_stack) -> None:
    _admin, owner, application, versions = _setup(f02a_stack, count=2)
    sentinel = "draft-only-sentinel-4c1f"
    saved = f.save_draft(
        owner, application["id"], '"1"',
        definition={"dsl_version": "1.0", "nodes": [{"id": sentinel, "type": "start"}]},
        layout={"viewport": {"x": 0, "y": 0, "zoom": 1}},
        capability_card={},
    )
    assert saved.status == 200, saved.text

    listing = f.list_versions(owner, application["id"])
    detail = f.get_version(owner, versions[-1]["id"])
    for response in (listing, detail):
        assert response.status == 200
        assert sentinel not in response.text, "version read model leaked draft content"
    # The manifest itself may contain the published definition (that is the
    # execution manifest); the *draft* rows (layout, current definition) must not
    # be joined into the read model.
    for item in listing.json["items"]:
        assert set(item) == {"id", "application_id", "version_no", "manifest_sha256", "manifest"}
        assert "layout" not in item["manifest"]
    assert "layout" not in detail.text


def test_versions_of_an_invisible_or_archived_application_are_404(f02a_stack) -> None:
    admin, _owner, application, versions = _setup(f02a_stack, count=2)
    outsider = f.invite_and_activate(f02a_stack, admin, "t023-version-outsider")
    assert f.list_versions(outsider, application["id"]).status == 404
    assert f.get_version(outsider, versions[0]["id"]).status == 404

    st.sql(
        f02a_stack.dbname,
        "UPDATE platform.applications SET archived_at = now() "
        f"WHERE id = {st.quote_literal(application['id'])}::uuid",
    )
    # The owner still cannot read the archived application's versions.
    assert f.get_application(_owner, application["id"]).status == 404
    assert f.list_versions(_owner, application["id"]).status == 404
    assert f.get_version(_owner, versions[0]["id"]).status == 404


def test_a_granted_reader_sees_the_same_versions_and_revocation_is_immediate(f02a_stack) -> None:
    admin, _owner, application, versions = _setup(f02a_stack, count=2)
    reader = f.invite_and_activate(f02a_stack, admin, "t023-version-reader")
    grant_id = f.grant_read(admin, reader.principal_id, application["id"])

    listing = f.list_versions(reader, application["id"])
    assert listing.status == 200, listing.text
    assert [item["id"] for item in listing.json["items"]] == [
        entry["id"] for entry in reversed(versions)
    ]
    assert f.get_version(reader, versions[0]["id"]).status == 200

    assert f.revoke_grant(admin, grant_id).status == 204
    assert f.list_versions(reader, application["id"]).status == 404
    assert f.get_version(reader, versions[0]["id"]).status == 404
