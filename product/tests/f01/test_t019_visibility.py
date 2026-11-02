"""T019 acceptance 1+3: A cannot enumerate B, and an admin/author is not a reader.

Every application row here is a real ``platform.applications`` row seeded through
psql (F02A owns createApplication; see the module docstring of ``t019_stack``).
The list and the direct read are real HTTP against the compiled API, and every
denial is a 404 with the same code and message as a genuinely missing id, so the
endpoint cannot be used to learn which ids exist.
"""

from __future__ import annotations

from conftest import assert_error
from t019_stack import (  # noqa: F401 - fixtures are used through their names
    DEMO_VERSION_ID,
    Stack,
    admin_session,
    api_server,
    application_ids,
    fresh_database,
    get_application,
    go_binaries,
    invite_and_activate,
    list_applications,
    new_uuid,
    seed_application,
    stack,
)


def test_a_cannot_enumerate_b_applications(stack: Stack) -> None:
    admin = admin_session(stack)
    alice = invite_and_activate(stack, admin, "vis.alice", roles=("creator",))
    bob = invite_and_activate(stack, admin, "vis.bob", roles=("creator",))
    alice_app = seed_application(stack, alice.principal_id, "alice-application", age_minutes=2)
    bob_app = seed_application(
        stack, bob.principal_id, "bob-application", age_minutes=1, active_version_id=DEMO_VERSION_ID
    )

    alice_list = list_applications(alice)
    assert alice_list.status == 200, alice_list.text
    assert application_ids(alice_list) == [alice_app], alice_list.text
    assert "bob-application" not in alice_list.text
    assert bob_app not in alice_list.text

    # Alice's own row is readable; Bob's is a 404 that does not echo anything
    # about the row (no name, no id, no existence hint).
    assert get_application(alice, alice_app).status == 200
    denied = get_application(alice, bob_app)
    error = assert_error(denied, 404, "resource_not_found")
    assert "bob-application" not in denied.text
    assert bob_app not in denied.text
    assert error["message"]

    # Bob sees his own row, not Alice's: the rule is symmetric.
    bob_list = list_applications(bob)
    assert application_ids(bob_list) == [bob_app]
    assert alice_app not in bob_list.text
    assert_error(get_application(bob, alice_app), 404, "resource_not_found")
    assert get_application(bob, bob_app).status == 200


def test_an_administrator_is_not_an_automatic_content_reader(stack: Stack) -> None:
    admin = admin_session(stack)
    bob = invite_and_activate(stack, admin, "vis.reader.bob", roles=("user",))
    bob_app = seed_application(stack, bob.principal_id, "bob-private-application")

    # The bootstrap administrator owns nothing: the list is honestly empty even
    # though the caller holds the admin role.
    admin_list = list_applications(admin)
    assert admin_list.status == 200, admin_list.text
    assert admin_list.json == {"items": [], "next_cursor": None}
    assert_error(get_application(admin, bob_app), 404, "resource_not_found")

    # Bob still reads his own row; the administrator's refusal changed nothing.
    assert get_application(bob, bob_app).status == 200


def test_an_author_cannot_read_another_creators_application(stack: Stack) -> None:
    admin = admin_session(stack)
    first_author = invite_and_activate(stack, admin, "vis.author.one", roles=("creator",))
    second_author = invite_and_activate(stack, admin, "vis.author.two", roles=("creator",))
    first_app = seed_application(stack, first_author.principal_id, "first-author-application", age_minutes=2)
    second_app = seed_application(stack, second_author.principal_id, "second-author-application", age_minutes=1)

    second_list = list_applications(second_author)
    assert application_ids(second_list) == [second_app]
    assert first_app not in second_list.text
    assert_error(get_application(second_author, first_app), 404, "resource_not_found")
    assert get_application(second_author, second_app).status == 200


def test_missing_and_unauthorized_are_indistinguishable(stack: Stack) -> None:
    admin = admin_session(stack)
    alice = invite_and_activate(stack, admin, "vis.same.alice", roles=("user",))
    bob = invite_and_activate(stack, admin, "vis.same.bob", roles=("user",))
    bob_app = seed_application(stack, bob.principal_id, "bob-hidden-application")

    unauthorized = get_application(alice, bob_app)
    missing = get_application(alice, new_uuid())
    unauthorized_error = assert_error(unauthorized, 404, "resource_not_found")
    missing_error = assert_error(missing, 404, "resource_not_found")
    assert unauthorized_error["code"] == missing_error["code"]
    assert unauthorized_error["message"] == missing_error["message"]
    assert unauthorized.text.replace(unauthorized_error["request_id"], "") == missing.text.replace(
        missing_error["request_id"], ""
    )

    # The list cannot be probed with a cursor pointing at another user's row
    # either: an empty page is exactly an empty page.
    assert list_applications(alice).json == {"items": [], "next_cursor": None}


def test_an_archived_application_is_invisible_even_to_its_owner(stack: Stack) -> None:
    admin = admin_session(stack)
    owner = invite_and_activate(stack, admin, "vis.archive.owner", roles=("creator",))
    live_app = seed_application(stack, owner.principal_id, "live-application", age_minutes=2)
    archived_app = seed_application(
        stack, owner.principal_id, "archived-application", age_minutes=1, archived=True
    )

    listed = list_applications(owner)
    assert application_ids(listed) == [live_app]
    assert archived_app not in listed.text
    assert_error(get_application(owner, archived_app), 404, "resource_not_found")
