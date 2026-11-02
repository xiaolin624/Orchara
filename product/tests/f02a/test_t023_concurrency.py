"""T023 acceptance: concurrent editing must never silently overwrite.

Two real sessions of the same creator hold the same ETag and save different
drafts at the same time. The database CAS (`WHERE revision = <If-Match>`) can
let exactly one succeed; the other must receive 412 with the current revision,
and the stored row must be exactly the winner's content.
"""

from __future__ import annotations

import concurrent.futures

from support import f02a as f
from support.fixtures import (  # noqa: F401 - pytest fixtures
    f02a_stack,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _same_user_second_session(stack, login: str):
    # The first session was created by invite_and_activate; logging in again is
    # a second real session cookie, which is what two browser tabs produce.
    return f.session_for(stack, login, f.USER_PASSWORD)


def _payload(marker: str) -> dict:
    return {
        "definition": {"dsl_version": "1.0", "nodes": [{"id": marker, "type": "start"}]},
        "layout": {"viewport": {"x": 0, "y": 0, "zoom": 1}},
        "capability_card": {},
    }


def test_two_writers_with_the_same_etag_get_exactly_one_200_and_one_412(f02a_stack) -> None:
    admin = f.admin_session(f02a_stack)
    owner = f.invite_and_activate(f02a_stack, admin, "t023-race-owner")
    application = f.create_application_or_fail(owner, "race")
    other = _same_user_second_session(f02a_stack, "t023-race-owner")
    etag = f.get_draft(owner, application["id"]).header("ETag")
    assert etag == '"1"'

    payloads = {"first-tab": _payload("first-tab"), "second-tab": _payload("second-tab")}

    def save(session, payload):
        return session, payload, f.save_draft(
            session, application["id"], etag,
            definition=payload["definition"],
            layout=payload["layout"],
            capability_card=payload["capability_card"],
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(save, owner, payloads["first-tab"]),
            pool.submit(save, other, payloads["second-tab"]),
        ]
        results = [future.result() for future in futures]

    statuses = sorted(response.status for _session, _payload, response in results)
    assert statuses == [200, 412], [
        (response.status, response.text) for _s, _p, response in results
    ]

    winner = next(item for item in results if item[2].status == 200)
    loser = next(item for item in results if item[2].status == 412)
    assert loser[2].json["error"]["details"] == {"current_revision": 2, "current_etag": '"2"'}

    stored = f.get_draft(owner, application["id"])
    assert stored.status == 200
    assert stored.header("ETag") == '"2"'
    assert stored.json == winner[1], "the stored draft is not exactly the winner's payload"
    assert stored.json != loser[1], "the loser's content was silently merged"

    row = f.draft_row(f02a_stack.dbname, application["id"])
    assert row["revision"] == 2
    assert row["execution_revision"] == 2


def test_a_sequential_stale_writer_is_refused_and_the_row_is_unchanged(f02a_stack) -> None:
    admin = f.admin_session(f02a_stack)
    owner = f.invite_and_activate(f02a_stack, admin, "t023-sequential-owner")
    application = f.create_application_or_fail(owner, "sequential")
    other = _same_user_second_session(f02a_stack, "t023-sequential-owner")
    etag = f.get_draft(owner, application["id"]).header("ETag")

    winner = f.save_draft(
        owner, application["id"], etag,
        definition=_payload("winner")["definition"],
        layout=_payload("winner")["layout"], capability_card={},
    )
    assert winner.status == 200, winner.text
    before = f.draft_row(f02a_stack.dbname, application["id"])

    loser = f.save_draft(
        other, application["id"], etag,
        definition=_payload("loser")["definition"],
        layout=_payload("loser")["layout"], capability_card={},
    )
    assert loser.status == 412, loser.text
    assert f.draft_row(f02a_stack.dbname, application["id"]) == before
    assert f.get_draft(owner, application["id"]).json["definition"] == _payload("winner")["definition"]
