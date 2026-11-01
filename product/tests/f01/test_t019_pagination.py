"""T019 acceptance: the ACL filter runs before pagination.

The applications are seeded with explicitly backdated ``created_at`` values so
"older" and "newer" are facts, not timing luck. The ACL predicate is part of the
same SQL WHERE as the cursor and LIMIT, so an invisible row can never consume a
page slot: with ``limit=1`` the walk must yield exactly the visible rows, in
created_at order, with no empty page and a cursor that continues seamlessly.
"""

from __future__ import annotations

from conftest import assert_error
from t019_stack import (  # noqa: F401 - fixtures are used through their names
    Stack,
    admin_session,
    api_server,
    application_ids,
    fresh_database,
    go_binaries,
    grant_read_or_fail,
    invite_and_activate,
    list_applications,
    seed_application,
    seed_applications_for_series,
    stack,
)


def test_the_acl_filter_runs_before_the_page_is_cut(stack: Stack) -> None:
    admin = admin_session(stack)
    reader = invite_and_activate(stack, admin, "page.reader", roles=("creator",))
    other = invite_and_activate(stack, admin, "page.other", roles=("creator",))

    # Interleaved by created_at, newest first: other hides 5m and 7m, reader
    # owns 6m/8m/10m and is granted 9m. So the visible order is:
    #   reader-new (6m), reader-mid (8m), other-granted (9m), reader-old (10m).
    hidden_newest = seed_application(stack, other.principal_id, "other-hidden-newest", age_minutes=5)
    owned_new = seed_application(stack, reader.principal_id, "reader-new", age_minutes=6)
    hidden_mid = seed_application(stack, other.principal_id, "other-hidden-mid", age_minutes=7)
    owned_mid = seed_application(stack, reader.principal_id, "reader-mid", age_minutes=8)
    granted = seed_application(stack, other.principal_id, "other-granted", age_minutes=9)
    owned_old = seed_application(stack, reader.principal_id, "reader-old", age_minutes=10)

    # Exactly one granted row. The newest row of all is invisible: an
    # OFFSET/LIMIT-then-filter implementation would return an empty first page.
    grant_read_or_fail(admin, reader.principal_id, granted)

    expected = [owned_new, owned_mid, granted, owned_old]
    seen: list[str] = []
    cursors: list[str] = []
    cursor = None
    while True:
        page = list_applications(reader, cursor=cursor, limit=1)
        assert page.status == 200, page.text
        items = page.json["items"]
        next_cursor = page.json["next_cursor"]
        if not items:
            assert next_cursor is None, "an empty page appeared before the end of the list"
            break
        assert len(items) == 1, page.json
        assert set(items[0]) == {"id", "name", "active_version_id"}
        seen.append(items[0]["id"])
        if next_cursor is None:
            break
        cursors.append(next_cursor)
        cursor = next_cursor

    assert seen == expected, seen
    assert hidden_newest not in seen and hidden_mid not in seen
    # One cursor per page boundary after the first: four rows -> three cursors.
    assert len(cursors) == 3, cursors
    # Following a cursor twice does not lose or repeat rows.
    replay = list_applications(reader, cursor=cursors[0], limit=1)
    assert application_ids(replay) == [owned_mid]


def test_the_default_page_is_twenty_and_the_cursor_continues(stack: Stack) -> None:
    admin = admin_session(stack)
    reader = invite_and_activate(stack, admin, "page.twenty", roles=("creator",))
    seed_applications_for_series(stack, reader.principal_id, 25)

    first = list_applications(reader)
    assert first.status == 200
    assert len(first.json["items"]) == 20, first.json
    assert first.json["next_cursor"], "a 25-row list with a 20-row page must have a cursor"

    second = list_applications(reader, cursor=first.json["next_cursor"])
    assert second.status == 200
    assert len(second.json["items"]) == 5, second.json
    assert second.json["next_cursor"] is None

    first_ids = application_ids(first)
    second_ids = application_ids(second)
    assert len(set(first_ids + second_ids)) == 25
    # Newest first: created_at decreases down the list.
    names = [item["name"] for item in first.json["items"] + second.json["items"]]
    assert names == [f"series-{index}" for index in range(1, 26)], names


def test_limit_can_fetch_everything_in_one_page(stack: Stack) -> None:
    admin = admin_session(stack)
    reader = invite_and_activate(stack, admin, "page.hundred", roles=("creator",))
    seed_applications_for_series(stack, reader.principal_id, 25)

    page = list_applications(reader, limit=100)
    assert page.status == 200
    assert len(page.json["items"]) == 25
    assert page.json["next_cursor"] is None


def test_a_granted_row_inside_a_hidden_run_is_still_reachable(stack: Stack) -> None:
    # The invisible rows are *newer* than every visible one: an OFFSET-then-filter
    # implementation would return an empty first page and never reach the grant.
    admin = admin_session(stack)
    reader = invite_and_activate(stack, admin, "page.granted.reader", roles=("creator",))
    other = invite_and_activate(stack, admin, "page.granted.other", roles=("creator",))

    oldest_visible = seed_application(stack, reader.principal_id, "reader-oldest", age_minutes=10)
    hidden_one = seed_application(stack, other.principal_id, "hidden-one", age_minutes=2)
    hidden_two = seed_application(stack, other.principal_id, "hidden-two", age_minutes=1)
    assert hidden_one != hidden_two

    grant_read_or_fail(admin, reader.principal_id, hidden_one)
    expected = [hidden_one, oldest_visible]

    seen: list[str] = []
    cursor = None
    for _ in range(5):
        page = list_applications(reader, cursor=cursor, limit=1)
        assert page.status == 200
        for item in page.json["items"]:
            seen.append(item["id"])
        cursor = page.json["next_cursor"]
        if cursor is None:
            break
    assert seen == expected, seen
    assert hidden_two not in seen
