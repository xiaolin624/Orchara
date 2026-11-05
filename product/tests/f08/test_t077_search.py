"""F08/T077: authorized catalog search through the real HTTP surface.

The ACL and the publication filter must be inside the SQL that produces each page,
before ranking and LIMIT: an invisible or revoked row may never consume a slot,
hide a visible row, or reach the caller as a description. These tests use real
published versions (compiler-produced manifests), the real compiled admin rebuild
and the real compiled API; every expectation is read back from PostgreSQL.
"""

from __future__ import annotations

from support import f00b, f02a, f08, t068, t077
from support import stack as st


def test_search_returns_only_authorized_published_versions_before_ranking(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    stranger = f08.account(stack)

    alpha = t077.publish_version(stack, owner.session, purpose="quarterly sales report alpha")
    beta = t077.publish_version(stack, owner.session, purpose="sales report beta dashboard")
    gamma = t077.publish_version(
        stack,
        owner.session,
        purpose="invoice matching",
        includes=["sales report ingestion"],
    )
    # A stranger's exact-match purpose must never outrank the caller's own rows:
    # the ACL predicate is in the SQL before ORDER BY/LIMIT, not a post-filter.
    foreign = t077.publish_version(
        stack, stranger.session, purpose="sales report exact match that must stay invisible"
    )

    report = t077.rebuild_ok(stack, "--all")
    assert report["state"] == "ready", report
    assert report["indexed"] == 4, report
    assert report["projection_epoch"] >= 1, report
    assert len(t077.entry_rows(stack.dbname)) == 4

    body = t077.search_ok(t077.search(owner.session, "sales report", limit=1))
    # Two of the caller's versions rank on the purpose text; whichever the total
    # keyset order puts first, the stranger's exact-match purpose is never it.
    assert len(body["items"]) == 1, body
    assert body["items"][0]["version_id"] in {alpha.version_id, beta.version_id}, body
    item = body["items"][0]
    assert item["application_id"] in {alpha.application_id, beta.application_id}
    assert item["version_no"] == 1
    assert item["manifest_sha256"] in {alpha.manifest_sha256, beta.manifest_sha256}
    assert item["capability_card"] in (alpha.card, beta.card)
    assert body["retrieval"]["source"] == "index", body["retrieval"]
    assert body["retrieval"]["stale"] is False, body["retrieval"]
    assert body["retrieval"]["index_state"] == "ready", body["retrieval"]
    assert body["retrieval"]["rechecked"] == 1, body["retrieval"]
    assert body["retrieval"]["rejected_by_recheck"] == 0, body["retrieval"]
    assert body["next_cursor"] is not None, body
    # The invisible description never crosses the wire, not even as a hidden field.
    response = t077.search(owner.session, "sales report", limit=1)
    assert foreign.purpose not in response.text
    assert "must stay invisible" not in response.text
    assert stranger.principal_id not in response.text

    # Ranking is a deterministic two-tier order: the purpose match precedes the
    # includes-only match, then the keyset (version_no DESC, id DESC) is total.
    page_one = t077.search_ok(t077.search(owner.session, "sales report", limit=2))
    assert page_one["retrieval"]["source"] == "index"
    first_ids = [item["version_id"] for item in page_one["items"]]
    assert set(first_ids) <= {alpha.version_id, beta.version_id}, first_ids
    assert page_one["next_cursor"] is not None, page_one
    page_two = t077.search_ok(
        t077.search(owner.session, "sales report", cursor=page_one["next_cursor"])
    )
    assert [item["version_id"] for item in page_two["items"]] == [gamma.version_id], page_two
    assert page_two["next_cursor"] is None, page_two
    assert set(first_ids + [item["version_id"] for item in page_two["items"]]) == {
        alpha.version_id,
        beta.version_id,
        gamma.version_id,
    }

    # A query that matches only the invisible row returns an empty authorized page,
    # and the pagination surface stays contract-shaped (items/next_cursor only).
    invisible_only = t077.search_ok(t077.search(owner.session, "stay invisible"))
    assert invisible_only["items"] == []
    assert invisible_only["next_cursor"] is None
    # A wildcard in a user query stays a literal, not "match everything".
    for wildcard in ("%", "_"):
        assert t077.items_of(t077.search(owner.session, wildcard)) == [], wildcard

    # An unexpired read grant is a real authorization source: the same projection
    # row becomes visible to the grantee, and revoking the grant removes it again
    # without any rebuild. The record owner is unchanged.
    grantee = f08.account(stack)
    admin = f00b.admin_session(stack)
    grant_id = f02a.grant_read(admin, grantee.principal_id, alpha.application_id)
    granted = t077.search_ok(t077.search(grantee.session, "sales report"))
    assert alpha.version_id in {item["version_id"] for item in granted["items"]}, granted
    assert alpha.purpose in t077.search(grantee.session, "sales report").text
    f02a.revoke_grant(admin, grant_id)
    revoked_grant = t077.search_ok(t077.search(grantee.session, "sales report"))
    assert revoked_grant["items"] == [], revoked_grant
    assert alpha.purpose not in t077.search(grantee.session, "sales report").text

    listing = owner.session.client.get("/api/v1/applications")
    assert listing.status == 200, listing.text
    assert set(listing.json) == {"items", "next_cursor"}, listing.json
    for application in listing.json["items"]:
        assert set(application) == {"id", "name", "active_version_id"}, application

    # Bad pagination inputs stay located errors on the non-contract search surface.
    st.assert_error(
        t077.search(owner.session, "sales", cursor="not-a-cursor"), 400, t077.ERROR_INVALID_CURSOR
    )
    st.assert_error(
        t077.search(owner.session, "sales", limit=0), 422, t077.ERROR_VALIDATION
    )
    st.assert_error(
        t077.search(owner.session, "sales", limit=101), 422, t077.ERROR_VALIDATION
    )
    st.assert_error(
        t077.search(owner.session, "x" * 201), 422, t077.ERROR_VALIDATION
    )


def test_search_never_returns_revoked_foreign_or_archived_versions(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    stranger = f08.account(stack)

    good = t077.publish_version(stack, owner.session, purpose="catalog good purpose")
    revoked = t077.publish_version(stack, owner.session, purpose="SECRET-REVOKED description")
    archived = t077.publish_version(stack, owner.session, purpose="SECRET-ARCHIVED description")
    foreign = t077.publish_version(stack, stranger.session, purpose="SECRET-FOREIGN description")

    t077.rebuild_ok(stack, "--all")
    assert t077.state_row(stack.dbname)["state"] == "ready"

    # The authoritative state changes after the projection was built, exactly the
    # lag this ticket must survive: the projection rows below are deliberately
    # stale/mismatched and the tests own them.
    f08.revoke_version(stack.dbname, revoked.version_id, owner.principal_id)
    t068.archive_application(stack.dbname, archived.application_id)
    for version in (revoked, archived, foreign):
        t077.insert_projection_row(stack.dbname, version, search_text=version.purpose)
    secret_rows = [row for row in t077.entry_rows(stack.dbname) if "SECRET-" in row["search_text"]]
    assert len(secret_rows) == 3, secret_rows

    body = t077.search_ok(t077.search(owner.session, "catalog"))
    assert [item["version_id"] for item in body["items"]] == [good.version_id], body
    assert body["retrieval"]["source"] == "index", body["retrieval"]
    assert body["retrieval"]["rechecked"] == 1, body["retrieval"]

    for query in ("SECRET-REVOKED", "SECRET-ARCHIVED", "SECRET-FOREIGN", "SECRET-"):
        leak = t077.search(owner.session, query)
        assert t077.items_of(leak) == [], (query, leak.text)
        for secret in (
            "SECRET-REVOKED",
            "SECRET-ARCHIVED",
            "SECRET-FOREIGN",
            "description",
        ):
            assert secret not in leak.text, (query, secret, leak.text)

    # The invisible versions cannot be started either: the stale projection row
    # never becomes a candidate, and the run path re-checks the authority.
    conversation_id = f08.conversation(owner.session)
    st.assert_error(
        f08.submit_request(
            owner.session,
            conversation_id,
            "run the revoked version",
            version_id=revoked.version_id,
            key=f00b.new_uuid(),
        ),
        409,
        t077.ERROR_VERSION_REVOKED,
    )
    st.assert_error(
        f08.submit_request(
            owner.session,
            conversation_id,
            "run the archived version",
            version_id=archived.version_id,
            key=f00b.new_uuid(),
        ),
        409,
        t077.ERROR_VERSION_ARCHIVED,
    )
    st.assert_error(
        f08.submit_request(
            owner.session,
            conversation_id,
            "run the foreign version",
            version_id=foreign.version_id,
            key=f00b.new_uuid(),
        ),
        404,
        t077.ERROR_RESOURCE_NOT_FOUND,
    )
    assert f08.request_rows(stack.dbname, conversation_id) == []


def test_stale_index_falls_back_to_authorized_full_text_and_rechecks_authorization(
    f08_stack,
) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    before = t077.publish_version(stack, owner.session, purpose="invoice archive searchable")
    t077.rebuild_ok(stack, "--all")

    # A fresh version whose metadata is not in the projection makes the index lag:
    # the hash/count comparison notices it and search must use the authorized
    # full text instead of silently serving a short index page. A second unindexed
    # version keeps the index genuinely stale after the first one is revoked, so
    # the fallback path (and its second check) is really exercised.
    fresh = t077.publish_version(stack, owner.session, purpose="receipt searchable metadata")
    pending = t077.publish_version(stack, owner.session, purpose="ledger searchable receipt")
    assert t077.entry_rows(stack.dbname, version_id=fresh.version_id) == []
    assert t077.entry_rows(stack.dbname, version_id=pending.version_id) == []

    fallback = t077.search_ok(t077.search(owner.session, "searchable"))
    assert fallback["retrieval"]["source"] == "fallback", fallback["retrieval"]
    assert fallback["retrieval"]["stale"] is True, fallback["retrieval"]
    got = {item["version_id"] for item in fallback["items"]}
    assert got == {before.version_id, fresh.version_id, pending.version_id}, fallback
    fresh_item = next(item for item in fallback["items"] if item["version_id"] == fresh.version_id)
    assert fresh_item["capability_card"] == fresh.card
    assert fallback["retrieval"]["rejected_by_recheck"] == 0, fallback["retrieval"]

    # Revoking the freshly published version is still caught by the second
    # authorization check: the fallback query considers the authorized scope, and
    # the per-row authoritative re-check refuses the now-revoked candidate.
    f08.revoke_version(stack.dbname, fresh.version_id, owner.principal_id)
    after = t077.search_ok(t077.search(owner.session, "searchable"))
    assert {item["version_id"] for item in after["items"]} == {
        before.version_id,
        pending.version_id,
    }, after
    assert after["retrieval"]["source"] == "fallback", after["retrieval"]
    assert after["retrieval"]["stale"] is True, after["retrieval"]
    assert after["retrieval"]["rejected_by_recheck"] >= 1, after["retrieval"]
    assert fresh.purpose not in t077.search(owner.session, "searchable").text

    # Afterwards a real rebuild makes the index authoritative again.
    t077.rebuild_ok(stack, "--all")
    indexed = t077.search_ok(t077.search(owner.session, "searchable"))
    assert indexed["retrieval"]["source"] == "index", indexed["retrieval"]
    assert {item["version_id"] for item in indexed["items"]} == {
        before.version_id,
        pending.version_id,
    }, indexed
    assert indexed["retrieval"]["rejected_by_recheck"] == 0, indexed["retrieval"]
