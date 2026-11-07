"""T101/J5: the named service-account journey.

Real entry points only: an administrator creates the human, grants the resource,
creates the named service principal and issues scoped API keys through
``/api/v1/admin/**``; every authenticated call is an HTTP request carrying nothing
but ``Authorization: Bearer``. The truth is read back independently from
``platform.api_keys`` (only the SHA-256 is stored), from the response of the
authorization layer, and from the key row after the responsible human is disabled.

The journey's central claim is the three-way gate the ticket's AC2 names:
a call succeeds only when the **scope** is present, the **resource ACL** grants it,
and the **responsible human** is still enabled. Removing any one of the three
denies the very same call.
"""

from __future__ import annotations

import datetime
import hashlib
import json

from support import f00b, f02a
from support import stack as st

import t101_support

SCOPE_READ = "application:read"
SCOPE_WRITE = "application:write"


def _rfc3339(seconds_from_now: int) -> str:
    instant = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=seconds_from_now
    )
    return instant.strftime("%Y-%m-%dT%H:%M:%SZ")


def _key_row(dbname: str, key_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text, principal_id::text, key_hash, to_json(scopes)::text AS scopes, "
        "expires_at::text, revoked_at::text "
        "FROM platform.api_keys "
        f"WHERE id = {st.quote_literal(key_id)}::uuid",
    )
    assert len(rows) == 1, rows
    row = dict(rows[0])
    row["scopes"] = json.loads(row["scopes"])
    return row


def _create_key(admin, principal_id: str, scopes: list[str], expires_in: int = 7200):
    response = admin.client.post(
        "/api/v1/admin/api-keys",
        body={
            "principal_id": principal_id,
            "scopes": scopes,
            "expires_at": _rfc3339(expires_in),
        },
        csrf=admin.csrf,
    )
    assert response.status == 201, response.text
    return response


def run(stack, record) -> dict:
    record.command("python -m pytest tests/f12/test_t101_journeys.py -k service_account")
    admin = f00b.admin_session(stack)
    record.http("POST", "/api/v1/auth/login (admin)", None)

    # -- humans and one readable resource ----------------------------------
    responsible = f00b.invite_and_activate(
        stack, admin, "t101.svc.responsible", roles=("user",)
    )
    record.command(
        "POST /api/v1/admin/users -> POST /api/v1/auth/activate -> POST /api/v1/auth/login"
    )
    owner = f00b.invite_and_activate(
        stack, admin, "t101.svc.owner", roles=("creator",)
    )
    application = f02a.create_application_or_fail(owner, "t101 service-account target")["id"]
    record.http("POST", "/api/v1/applications", None)
    record.truth_source(
        "platform.api_keys (key_hash only, scopes, expires_at, revoked_at) via psql"
    )

    # -- the human administrator creates the service principal and its key ---
    created = admin.client.post(
        "/api/v1/admin/service-accounts",
        body={
            "display_name": "t101 service reader",
            "responsible_user_id": responsible.principal_id,
        },
        csrf=admin.csrf,
    )
    record.http("POST", "/api/v1/admin/service-accounts", created)
    record.check("service account created", created.status == 201, created.text)
    service_id = created.json["id"]
    record.check(
        "service account is a named principal with an active state",
        created.json.get("state") == "active" and service_id != responsible.principal_id,
        created.json,
    )

    key_response = _create_key(admin, service_id, [SCOPE_READ])
    record.http("POST", "/api/v1/admin/api-keys", key_response)
    record.check(
        "scoped key issued",
        key_response.status == 201,
        t101_support.redact_value(key_response.json),
    )
    key_id = key_response.json["id"]
    secret = key_response.json["secret"]
    record.check(
        "the secret is returned once under the documented prefix",
        secret.startswith("orchara_") and len(secret) == len("orchara_") + 43,
        {"length_ok": len(secret) == len("orchara_") + 43},
    )

    row = _key_row(stack.dbname, key_id)
    record.check(
        "only the secret's SHA-256 is stored (the plaintext never reaches the database)",
        row["key_hash"] == hashlib.sha256(secret.encode()).hexdigest()
        and secret not in json.dumps(row),
        {"hash_matches": row["key_hash"] == hashlib.sha256(secret.encode()).hexdigest()},
    )
    record.check(
        "the stored scope set is exactly the requested scope set",
        row["scopes"] == [SCOPE_READ],
        row["scopes"],
    )
    record.note("api_key_id", key_id)

    reader = f02a.bearer_client(stack, secret)

    # -- scope is real and roles are empty; csrf is absent -------------------
    me = reader.get("/api/v1/auth/me")
    record.http("GET", "/api/v1/auth/me (bearer)", me)
    record.check("the key authenticates", me.status == 200, me.text)
    record.check(
        "a service credential is kind=service with no roles and no CSRF token",
        me.json.get("kind") == "service"
        and me.json.get("roles") == []
        and me.json.get("csrf_token") is None,
        me.json,
    )

    # -- ACL: default deny even with the read scope -------------------------
    listing = reader.get("/api/v1/applications")
    record.http("GET", "/api/v1/applications (bearer, no grant)", listing)
    record.check(
        "with the read scope but no grant the catalog is empty",
        listing.status == 200 and listing.json == {"items": [], "next_cursor": None},
        listing.json,
    )
    denied = reader.get(f"/api/v1/applications/{application}")
    record.http("GET", f"/api/v1/applications/{application} (bearer, no grant)", denied)
    st.assert_error(denied, 404, "resource_not_found")
    record.check(
        "with the read scope but no grant the detail is the same 404 as a missing id",
        denied.status == 404,
        denied.text,
    )

    # -- scope before ACL: a write-scoped key is refused on the read route ----
    write_key_response = _create_key(admin, service_id, [SCOPE_WRITE])
    record.http("POST", "/api/v1/admin/api-keys (application:write)", write_key_response)
    writer = f02a.bearer_client(stack, write_key_response.json["secret"])
    wrong_scope = writer.get(f"/api/v1/applications/{application}")
    record.http("GET", f"/api/v1/applications/{application} (write-scoped key)", wrong_scope)
    st.assert_error(wrong_scope, 403, "insufficient_scope")
    record.check(
        "a write-scoped key is refused before the ACL (403 insufficient_scope)",
        wrong_scope.status == 403,
        wrong_scope.text,
    )

    # -- the grant is the ACL half: same call, now allowed -------------------
    grant_id = f02a.grant_read(admin, service_id, application)
    record.http("POST", "/api/v1/admin/grants", None)
    allowed = reader.get(f"/api/v1/applications/{application}")
    record.http("GET", f"/api/v1/applications/{application} (bearer, granted)", allowed)
    record.check(
        "scope + ACL together allow the same call that was 404 without the grant",
        allowed.status == 200 and allowed.json["id"] == application,
        allowed.text,
    )

    # -- the reverse direction: the read key cannot write -------------------
    read_write = reader.post(
        "/api/v1/applications", body={"name": "t101 forbidden write"}
    )
    record.http("POST", "/api/v1/applications (read-scoped key)", read_write)
    st.assert_error(read_write, 403, "insufficient_scope")
    record.check(
        "the read-scoped key is refused on the write route",
        read_write.status == 403,
        read_write.text,
    )
    write_allowed = writer.post(
        "/api/v1/applications", body={"name": "t101 service-owned application"}
    )
    record.http("POST", "/api/v1/applications (write-scoped key)", write_allowed)
    record.check(
        "the write-scoped key creates an application (scope is necessary and sufficient here)",
        write_allowed.status == 201,
        write_allowed.text,
    )

    # -- human-only endpoints: personal memory is never a service scope -----
    memories = reader.get("/api/v1/memories")
    record.http("GET", "/api/v1/memories (bearer)", memories)
    st.assert_error(memories, 403, "personal_scope_forbidden")
    record.check(
        "the personal-memory endpoint refuses a service credential with the personal-data code",
        memories.status == 403,
        memories.text,
    )
    memory_write = reader.post(
        "/api/v1/memories",
        body={"application_id": application, "text": "x", "confirmed": True},
    )
    st.assert_error(memory_write, 403, "personal_scope_forbidden")
    record.check(
        "the write side of personal memory is refused by the same credential rule",
        memory_write.status == 403,
        memory_write.text,
    )

    # -- revocation takes effect on the next call ---------------------------
    revoked_key_response = _create_key(admin, service_id, [SCOPE_READ])
    revoked = f02a.bearer_client(stack, revoked_key_response.json["secret"])
    record.check(
        "control: the second read key works before revocation",
        revoked.get("/api/v1/auth/me").status == 200,
    )
    revoke = admin.client.request(
        "DELETE", f"/api/v1/admin/api-keys/{revoked_key_response.json['id']}", csrf=admin.csrf
    )
    record.http("DELETE", "/api/v1/admin/api-keys/{key_id}", revoke)
    record.check("revocation accepted", revoke.status == 204, revoke.text)
    after_revoke = revoked.get("/api/v1/auth/me")
    record.http("GET", "/api/v1/auth/me (revoked key)", after_revoke)
    st.assert_error(after_revoke, 401, "invalid_key")
    record.check(
        "a revoked key is refused on the very next call",
        after_revoke.status == 401,
        after_revoke.text,
    )

    # -- expiry is a credential fact, not a cached decision -----------------
    expiring = _create_key(admin, service_id, [SCOPE_READ], expires_in=3)
    expiring_client = f02a.bearer_client(stack, expiring.json["secret"])
    before = expiring_client.get("/api/v1/auth/me")
    record.check(
        "control: the short-lived key works before its deadline",
        before.status == 200,
        before.text,
    )
    import time as _time

    _time.sleep(3.5)
    after_expiry = expiring_client.get("/api/v1/auth/me")
    record.http("GET", "/api/v1/auth/me (expired key)", after_expiry)
    st.assert_error(after_expiry, 401, "invalid_key")
    record.check(
        "an expired key is refused with the same invalid_key shape",
        after_expiry.status == 401,
        after_expiry.text,
    )
    record.command("sleep 3.5s (the real key deadline passes; nothing is injected)")

    # -- disabling the responsible human kills the still-valid secret -------
    # The official PATCH is SECURITY.md §1's concrete rule: disabling a
    # responsible person also revokes the keys of the service accounts it owns,
    # so the next call is `401 invalid_key` and the key row itself carries the
    # revocation instant. (The separate `403 responsible_disabled` code is what
    # the identity layer says when a key row survives a bypass disable; that
    # branch cannot be reached through this API because it revokes first.)
    key_before = _key_row(stack.dbname, key_id)
    disabled = admin.client.request(
        "PATCH",
        f"/api/v1/admin/principals/{responsible.principal_id}",
        body={"disabled": True, "reason": "t101: responsible human offboarded"},
        csrf=admin.csrf,
    )
    record.http(
        "PATCH", f"/api/v1/admin/principals/{responsible.principal_id}", disabled
    )
    record.check("the responsible human is disabled", disabled.status == 200, disabled.text)
    orphaned = reader.get(f"/api/v1/applications/{application}")
    record.http(
        "GET", f"/api/v1/applications/{application} (responsible disabled)", orphaned
    )
    record.check(
        "the still-valid secret is dead on the next call after its responsible human is disabled",
        orphaned.status == 401,
        orphaned.text,
    )
    st.assert_error(orphaned, 401, "invalid_key")
    key_after = _key_row(stack.dbname, key_id)
    record.check(
        "the disable transaction really revoked the service key (independent key row)",
        key_after["revoked_at"] is not None
        and key_before["revoked_at"] is None
        and key_after["expires_at"] == key_before["expires_at"],
        {"revoked_at": key_after["revoked_at"], "expires_at": key_after["expires_at"]},
    )
    record.note(
        "responsible_disable_semantics",
        "official PATCH revokes the responsible person's service keys (SECURITY.md §1) "
        "-> 401 invalid_key with revoked_at set; the 403 responsible_disabled branch "
        "needs a key row that survives the disable and is not reachable via this API",
    )

    # -- the three conditions, stated as one matrix -------------------------
    record.note(
        "three_way_gate",
        {
            "scope": "application:read present on the credential",
            "acl": f"unexpired read grant {grant_id} on {application}",
            "human": "responsible user enabled",
            "all_three_allow": allowed.status,
            "missing_acl": denied.status,
            "wrong_scope": wrong_scope.status,
            "responsible_disabled": orphaned.status,
        },
    )
    record.note(
        "ac2",
        {
            "verdict": "partial",
            "proven": [
                "scope: the same call is 403 insufficient_scope with a write-scoped key",
                "ACL: the same call is 404 without the read grant and 200 with it",
                "human: disabling the responsible human revokes the key (401 invalid_key)",
            ],
            "not_proven": (
                "no single external write exercises API-key scope + resource ACL + a human "
                "approval at the same time: J5 is a read journey, J2's approval is a browser "
                "session decision over an internal capability call"
            ),
            "product_support": (
                "the product can bind a service-owned Run's approval to its responsible human "
                "(internal/approvals/service.go:187-207 approverForOwnerTx), so this is a "
                "coverage gap in the delivered journeys, not a confirmed product gap"
            ),
            "remaining_not_run": (
                "publish a version whose definition contains an approval-gated write node, "
                "submit it with a write-scoped service key that also holds the application "
                "grant, then have the responsible human approve and assert exactly one egress"
            ),
        },
    )
    return {
        "service_principal_id": service_id,
        "responsible_principal_id": responsible.principal_id,
        "api_key_id": key_id,
        "grant_id": grant_id,
        "application_id": application,
        "all_three_status": allowed.status,
    }
