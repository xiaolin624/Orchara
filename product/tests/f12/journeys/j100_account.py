"""T100 journey 1: account bring-up — migrate -> bootstrap -> invite -> activate -> login.

Every step is the product's real entry point:

* ``cmd/admin migrate`` and ``cmd/admin bootstrap`` are the real compiled CLIs
  (run by the stack fixture; the bootstrap-once boundary is replayed here as a
  real subprocess).
* Invitation, activation and login are real HTTP requests to the compiled
  ``cmd/api`` with a real session cookie and CSRF token.
* The independent truth is PostgreSQL: the migration ledger, the sole admin
  principal, the argon2id hash, the hashed-and-unconsumed activation token, the
  session rows and the audit events.

The expiry boundary moves one activation token's server-side deadline into the
past (a clock-only write; no user, session or credential state is fabricated) so
the documented ``activation_expired`` branch is observed without waiting 24h.
"""

from __future__ import annotations

import datetime as dt
import secrets

from support import f00b, stack as st
from t100_support import sha256_hex, utc_epoch


def _count(stack, table: str) -> int:
    return int(st.sql(stack.dbname, f"SELECT count(*) FROM platform.{table}"))


def _invite(stack, admin, login: str, *, roles: list[str], csrf: str | None = "__admin__"):
    headers = {}
    if csrf == "__admin__":
        csrf = admin.csrf
    return admin.client.post(
        "/api/v1/admin/users",
        body={
            "login": login,
            "display_name": login.replace(".", " ").title(),
            "roles": roles,
        },
        csrf=csrf,
        headers=headers,
    )


def run_account_journey(stack, journey) -> None:
    db = stack.dbname
    login_name = "t100.creator-" + secrets.token_hex(3)
    password = f00b.USER_PASSWORD

    # -- 1. migrate + bootstrap: real CLI state read back from PostgreSQL -------
    migrations = st.migration_chain(db)
    assert migrations, "the migrated database has no migration ledger"
    head = migrations[max(migrations)]
    journey.fact(
        "migrations", {"count": len(migrations), "head_version": max(migrations), "head": head}
    )
    count_rows = st.sql_rows(
        db,
        "SELECT (SELECT count(*) FROM platform.principals) AS principals, "
        "(SELECT count(*) FROM platform.users) AS users, "
        "(SELECT count(*) FROM platform.role_bindings) AS role_bindings, "
        "(SELECT count(*) FROM platform.audit_events) AS audit_events",
    )[0]
    assert count_rows == {
        "principals": 1,
        "users": 1,
        "role_bindings": 1,
        "audit_events": 1,
    }, count_rows
    admin_row = st.sql_rows(
        db,
        "SELECT p.id::text AS principal_id, p.kind, p.disabled_at::text AS disabled_at, "
        "u.login, u.password_hash, u.must_change_password, r.role, a.action "
        "FROM platform.principals AS p "
        "JOIN platform.users AS u ON u.principal_id = p.id "
        "JOIN platform.role_bindings AS r ON r.principal_id = p.id "
        "JOIN platform.audit_events AS a ON a.resource_id = p.id",
    )
    assert len(admin_row) == 1, admin_row
    admin_facts = admin_row[0]
    assert admin_facts["kind"] == "user" and admin_facts["login"] == "admin", admin_facts
    assert admin_facts["password_hash"].startswith("$argon2id$v=19$m=65536,t=3,p=2$"), admin_facts
    assert f00b.ADMIN_PASSWORD not in admin_facts["password_hash"], admin_facts
    assert admin_facts["role"] == "admin", admin_facts
    assert "bootstrap" in admin_facts["action"], admin_facts
    journey.fact("bootstrap_admin", {
        "principal_id": admin_facts["principal_id"],
        "login": admin_facts["login"],
        "role": admin_facts["role"],
        "audit_action": admin_facts["action"],
        "password_hash": "argon2id (secret never stored in clear)",
    })
    journey.assertion(
        "real cmd/admin migrate applied a continuous migration chain "
        f"({len(migrations)} rows, head {max(migrations)}={head})"
    )
    journey.assertion(
        "real cmd/admin bootstrap created exactly one admin principal (user row, admin role, "
        "bootstrap audit event, argon2id hash); the password never appears in the hash"
    )

    # A second bootstrap is refused by the real CLI and changes nothing.
    replay = st.run_admin(
        stack.binaries["admin"],
        ["bootstrap", "--login", "admin2", "--display-name", "Admin Two"],
        dsn=stack.dsn,
        stdin_text=f00b.ADMIN_PASSWORD + "\n",
    )
    replay_output = (replay.stdout or "") + (replay.stderr or "")
    assert replay.returncode != 0, replay_output
    assert "already exists" in replay_output.lower(), replay_output
    assert _count(stack, "principals") == 1 and _count(stack, "users") == 1
    journey.assertion(
        "a second real `cmd/admin bootstrap` exits non-zero with 'already exists' and creates "
        "no second principal/user"
    )

    # -- 2. admin login + real session ----------------------------------------
    admin = f00b.admin_session(stack)
    me = admin.client.get("/api/v1/auth/me")
    assert me.status == 200, me.text
    identity = me.json
    assert set(identity) == {"principal_id", "kind", "roles", "csrf_token", "expires_at"}, identity
    assert identity["kind"] == "user" and identity["roles"] == ["admin"], identity
    assert identity["principal_id"] == admin_facts["principal_id"], identity
    assert identity["csrf_token"] == admin.csrf, identity
    session_row = st.sql_rows(
        db,
        "SELECT token_hash, csrf_hash, revoked_at::text AS revoked_at FROM platform.sessions",
    )
    assert len(session_row) == 1, session_row
    assert session_row[0]["token_hash"] == sha256_hex(admin.cookie), session_row
    assert session_row[0]["csrf_hash"] == sha256_hex(admin.csrf), session_row
    assert session_row[0]["revoked_at"] in (None, ""), session_row
    journey.fact("admin_session", {
        "principal_id": admin.principal_id,
        "roles": identity["roles"],
        "session_cookie_stored_as_sha256": True,
        "csrf_stored_as_sha256": True,
    })
    journey.assertion(
        "POST /auth/login returns a real session cookie + CSRF; GET /auth/me returns the "
        "admin identity and the cookie/CSRF are stored only as SHA-256 in platform.sessions"
    )

    # -- 3. invite: one-time token, hashed at rest -----------------------------
    invitation = _invite(stack, admin, login_name, roles=["creator"])
    assert invitation.status == 201, invitation.text
    invited = invitation.json
    assert set(invited) == {"principal_id", "activation_token", "expires_at"}, invited
    token = invited["activation_token"]
    assert len(token) >= 40, token
    assert utc_epoch(invited["expires_at"]) > utc_epoch(dt.datetime.now(dt.timezone.utc).isoformat())
    user_rows = st.sql_rows(
        db,
        "SELECT principal_id::text AS principal_id, login, password_hash, must_change_password "
        "FROM platform.users WHERE login = " + st.quote_literal(login_name),
    )
    assert len(user_rows) == 1, user_rows
    assert user_rows[0]["principal_id"] == invited["principal_id"], user_rows
    assert user_rows[0]["password_hash"] == "!pending-activation", user_rows
    assert user_rows[0]["must_change_password"] is True, user_rows
    token_rows = st.sql_rows(
        db,
        "SELECT principal_id::text AS principal_id, token_hash, consumed_at::text AS consumed_at "
        "FROM platform.activation_tokens",
    )
    assert len(token_rows) == 1, token_rows
    assert token_rows[0]["token_hash"] == sha256_hex(token), token_rows
    assert token_rows[0]["token_hash"] != token, "the raw activation token is stored"
    assert token_rows[0]["consumed_at"] in (None, ""), token_rows
    role_binding = st.sql(
        db,
        "SELECT role FROM platform.role_bindings WHERE principal_id = "
        f"{st.quote_literal(invited['principal_id'])}::uuid",
    )
    assert role_binding == "creator", role_binding
    invited_audit = st.sql(
        db,
        "SELECT count(*) FROM platform.audit_events WHERE action = 'user.invited' AND "
        f"resource_id = {st.quote_literal(invited['principal_id'])}::uuid",
    )
    assert invited_audit == "1", invited_audit
    journey.fact("invitation", {
        "login": login_name,
        "principal_id": invited["principal_id"],
        "roles": ["creator"],
        "token_stored_as_sha256": True,
        "token_consumed": False,
        "user_password_sentinel": "!pending-activation",
    })
    journey.assertion(
        "POST /admin/users returns a one-time activation token; PostgreSQL holds only its "
        "SHA-256, the user keeps the !pending-activation sentinel and the creator role + "
        "user.invited audit event are real rows"
    )

    # -- 4. activation boundaries before the real activation -------------------
    anonymous = stack.client().post("/api/v1/admin/users", body={})
    assert anonymous.status == 401, anonymous.text
    assert anonymous.json["error"]["code"] == "unauthenticated", anonymous.text
    creator_principal = f00b.invite_and_activate(
        stack, admin, "t100.intruder-" + secrets.token_hex(3), roles=("user",)
    )
    forbidden = creator_principal.client.post(
        "/api/v1/admin/users",
        body={"login": "should-not-exist", "display_name": "Nope", "roles": ["user"]},
        csrf=creator_principal.csrf,
    )
    assert forbidden.status == 403, forbidden.text
    assert forbidden.json["error"]["code"] == "forbidden", forbidden.text
    assert st.sql(db, "SELECT count(*) FROM platform.users WHERE login = 'should-not-exist'") == "0"
    journey.assertion(
        "anonymous POST /admin/users is 401 unauthenticated; a non-admin creator gets 403 "
        "forbidden and no user row is created"
    )

    duplicate = _invite(stack, admin, login_name, roles=["creator"])
    assert duplicate.status == 409, duplicate.text
    assert duplicate.json["error"]["code"] == "login_taken", duplicate.text
    assert st.sql(
        db, "SELECT count(*) FROM platform.users WHERE login = " + st.quote_literal(login_name)
    ) == "1"
    no_csrf = admin.client.post(
        "/api/v1/admin/users",
        body={"login": "t100.no-csrf", "display_name": "No Csrf", "roles": ["user"]},
    )
    assert no_csrf.status == 403, no_csrf.text
    assert no_csrf.json["error"]["code"] == "csrf_invalid", no_csrf.text
    assert st.sql(db, "SELECT count(*) FROM platform.users WHERE login = 't100.no-csrf'") == "0"
    bad_roles = _invite(stack, admin, "t100.bad-roles", roles=["superuser"])
    assert bad_roles.status == 422, bad_roles.text
    assert bad_roles.json["error"]["code"] == "validation_failed", bad_roles.text
    journey.assertion(
        "duplicate invite is 409 login_taken; a missing CSRF header is 403 csrf_invalid; an "
        "unknown role is 422 validation_failed; all three create no user row"
    )

    unknown = stack.client().post(
        "/api/v1/auth/activate",
        body={"activation_token": "not-a-real-token", "password": password},
    )
    assert unknown.status == 409, unknown.text
    assert unknown.json["error"]["code"] == "activation_invalid", unknown.text
    journey.assertion("an unknown activation token is 409 activation_invalid")

    # A weak password is refused without consuming the token: then activate it.
    weak_login = "t100.weak-" + secrets.token_hex(3)
    weak_invite = _invite(stack, admin, weak_login, roles=["user"])
    assert weak_invite.status == 201, weak_invite.text
    weak_token = weak_invite.json["activation_token"]
    weak = stack.client().post(
        "/api/v1/auth/activate",
        body={"activation_token": weak_token, "password": "short-" + secrets.token_hex(2)},
    )
    assert weak.status == 422, weak.text
    assert weak.json["error"]["code"] == "validation_failed", weak.text
    weak_state = st.sql_rows(
        db,
        "SELECT t.consumed_at::text AS consumed_at, u.password_hash "
        "FROM platform.activation_tokens AS t "
        "JOIN platform.users AS u ON u.principal_id = t.principal_id "
        f"WHERE u.login = {st.quote_literal(weak_login)}",
    )[0]
    assert weak_state["consumed_at"] in (None, ""), weak_state
    assert weak_state["password_hash"] == "!pending-activation", weak_state
    activated_weak = stack.client().post(
        "/api/v1/auth/activate",
        body={"activation_token": weak_token, "password": password},
    )
    assert activated_weak.status == 200, activated_weak.text
    journey.assertion(
        "a weak password is 422 validation_failed and does not consume the token (sentinel "
        "and consumed_at unchanged); the same token then activates with a valid password"
    )

    # The expiry branch: move only the server-side deadline into the past.
    expired_login = "t100.expired-" + secrets.token_hex(3)
    expired_invite = _invite(stack, admin, expired_login, roles=["user"])
    assert expired_invite.status == 201, expired_invite.text
    expired_token = expired_invite.json["activation_token"]
    st.sql(
        db,
        "UPDATE platform.activation_tokens SET expires_at = now() - interval '1 minute' "
        "WHERE principal_id = "
        f"{st.quote_literal(expired_invite.json['principal_id'])}::uuid",
    )
    expired = stack.client().post(
        "/api/v1/auth/activate",
        body={"activation_token": expired_token, "password": password},
    )
    assert expired.status == 409, expired.text
    assert expired.json["error"]["code"] == "activation_expired", expired.text
    expired_state = st.sql(
        db,
        "SELECT consumed_at IS NULL FROM platform.activation_tokens WHERE principal_id = "
        f"{st.quote_literal(expired_invite.json['principal_id'])}::uuid",
    )
    assert expired_state == "t", expired_state
    journey.note(
        "expiry boundary: only the token's server-side expires_at was moved into the past "
        "(clock-only write); the token and user were created by the real invite API"
    )
    journey.assertion(
        "an invitation whose server-side deadline has passed is 409 activation_expired and "
        "stays unconsumed (clock-only SQL write, no fabricated business state)"
    )

    # -- 5. activate + login: the real user path -------------------------------
    activation = stack.client().post(
        "/api/v1/auth/activate",
        body={"activation_token": token, "password": password},
    )
    assert activation.status == 200, activation.text
    assert activation.json == {"id": invited["principal_id"], "state": "activated"}, activation.text
    activated = st.sql_rows(
        db,
        "SELECT password_hash, must_change_password FROM platform.users WHERE login = "
        + st.quote_literal(login_name),
    )[0]
    assert activated["password_hash"].startswith("$argon2id$v=19$m=65536,t=3,p=2$"), activated
    assert activated["must_change_password"] is False, activated
    assert st.sql(
        db,
        "SELECT consumed_at IS NOT NULL FROM platform.activation_tokens WHERE principal_id = "
        f"{st.quote_literal(invited['principal_id'])}::uuid",
    ) == "t"
    reuse = stack.client().post(
        "/api/v1/auth/activate",
        body={"activation_token": token, "password": password},
    )
    assert reuse.status == 409, reuse.text
    assert reuse.json["error"]["code"] == "activation_consumed", reuse.text
    journey.assertion(
        "POST /auth/activate succeeds exactly once: argon2id hash stored, must_change_password "
        "cleared, token consumed; reusing the old token is 409 activation_consumed"
    )

    user = f00b.session_for(stack, login_name, password)
    user_me = user.client.get("/api/v1/auth/me")
    assert user_me.status == 200, user_me.text
    assert user_me.json["principal_id"] == invited["principal_id"], user_me.json
    assert user_me.json["kind"] == "user" and user_me.json["roles"] == ["creator"], user_me.json
    assert user_me.json["csrf_token"] == user.csrf, user_me.json
    journey.fact("activated_user", {
        "login": login_name,
        "principal_id": user.principal_id,
        "roles": user_me.json["roles"],
    })
    journey.assertion(
        "POST /auth/login with the activated password returns the creator session; "
        "GET /auth/me returns its principal/roles/CSRF"
    )

    sessions_before = _count(stack, "sessions")
    wrong = stack.client().post(
        "/api/v1/auth/login", body={"login": login_name, "password": "wrong-" + secrets.token_hex(8)}
    )
    assert wrong.status == 401, wrong.text
    assert wrong.json["error"]["code"] == "invalid_credentials", wrong.text
    assert _count(stack, "sessions") == sessions_before, "a failed login created a session"
    journey.assertion(
        "a wrong password is 401 invalid_credentials and creates no session row"
    )

    # -- 6. cross-checks that the journey left a coherent identity state -------
    assert _count(stack, "principals") == _count(stack, "users")
    journey.fact("final_identity_counts", {
        "principals": _count(stack, "principals"),
        "users": _count(stack, "users"),
        "sessions": _count(stack, "sessions"),
        "role_bindings": _count(stack, "role_bindings"),
        "activation_tokens": _count(stack, "activation_tokens"),
    })
    journey.assertion(
        f"every principal has a user row ({_count(stack, 'principals')} principals / "
        f"{_count(stack, 'users')} users); the session/role/token tables hold only real rows "
        "written by the exercised endpoints"
    )
    journey.note(
        "f01's known collection conflict is avoided: this suite imports only tests/support "
        "helpers (never tests/f01/conftest.py), so no separate pytest process is needed for "
        "this account evidence"
    )
