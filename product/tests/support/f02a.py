"""F02A/T023 stack helpers: drafts, versions and the creator editor.

The stack is the real one: a throwaway PostgreSQL database migrated with the
real ``cmd/admin migrate`` chain, the compiled ``cmd/api``/``cmd/admin`` binaries
and real HTTP over a socket. There is no object store in these tests because
T023 neither writes nor reads object bytes: version rows are seeded with a real
``platform.artifacts`` row (the FK target T023's schema requires) but no sealed
object, which is the honest state before T024's upload producer and T025's
manifest artifact write exist.

Two deliberate boundaries, documented in the T023 report:

* ``publication_reviews.report_id`` has no FK yet (T026 creates
  ``evaluation_reports``), so a seeded review carries a random uuid in that
  column. The test cannot and does not claim the report exists.
* The published version rows are seeded with SQL. T023 has no publish endpoint
  (F02B owns it); the read model is what this ticket delivers, so the test uses
  the real tables/constraints and only the absent HTTP write path is out of band.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from typing import NamedTuple

from support import f00b
from support import stack as st

ADMIN_LOGIN = "admin"
# The shared ``migrated_database`` fixture bootstraps the first administrator
# with the F00B helper, so T023 must sign in with that same generated password.
# Neither value is a repository credential: both are random per test process.
ADMIN_PASSWORD = f00b.ADMIN_PASSWORD
USER_PASSWORD = "t023-user-" + secrets.token_urlsafe(18)

DEFAULT_DEFINITION = {"dsl_version": "1.0", "nodes": []}
DEFAULT_LAYOUT = {"viewport": {"x": 0, "y": 0, "zoom": 1}}


@dataclass
class F02AStack:
    server: st.ApiServer
    dsn: str
    dbname: str
    binaries: dict

    def client(self) -> st.HttpClient:
        return st.HttpClient(self.server.base_url)


class Session(NamedTuple):
    client: st.HttpClient
    csrf: str
    principal_id: str
    cookie: str


class BearerClient(st.HttpClient):
    """A client that carries only an Authorization header."""

    def __init__(self, base_url: str, secret: str) -> None:
        super().__init__(base_url)
        self.secret = secret

    def request(self, method: str, path: str, **kwargs):  # noqa: ANN001, ANN003
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("Authorization", f"Bearer {self.secret}")
        return super().request(method, path, headers=headers, **kwargs)


def bearer_client(stack: F02AStack, secret: str) -> BearerClient:
    return BearerClient(stack.server.base_url, secret)


def new_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# identity: real login / invite / activate
# ---------------------------------------------------------------------------
def sign_in(stack: F02AStack, login: str, password: str) -> tuple[st.HttpClient, st.Response]:
    client = stack.client()
    response = client.post("/api/v1/auth/login", body={"login": login, "password": password})
    return client, response


def session_for(stack: F02AStack, login: str, password: str) -> Session:
    client, response = sign_in(stack, login, password)
    assert response.status == 200, response.text
    body = response.json
    assert body["csrf_token"], body
    cookie = client.cookie(st.SESSION_COOKIE)
    assert cookie, "login did not set the session cookie"
    return Session(client, body["csrf_token"], body["principal_id"], cookie)


def admin_session(stack: F02AStack) -> Session:
    return session_for(stack, ADMIN_LOGIN, ADMIN_PASSWORD)


def invite_and_activate(
    stack: F02AStack,
    admin: Session,
    login: str,
    *,
    roles: tuple[str, ...] = ("creator",),
    password: str = USER_PASSWORD,
) -> Session:
    invitation = admin.client.post(
        "/api/v1/admin/users",
        body={
            "login": login,
            "display_name": login.replace(".", " ").title(),
            "roles": list(roles),
        },
        csrf=admin.csrf,
    )
    assert invitation.status == 201, invitation.text
    activation = stack.client().post(
        "/api/v1/auth/activate",
        body={"activation_token": invitation.json["activation_token"], "password": password},
    )
    assert activation.status == 200, activation.text
    return session_for(stack, login, password)


def future_rfc3339(hours: float = 2) -> str:
    moment = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)
    return moment.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# applications and drafts over real HTTP
# ---------------------------------------------------------------------------
def create_application(session: Session, name: str = "sales report") -> st.Response:
    return session.client.post(
        "/api/v1/applications", body={"name": name}, csrf=session.csrf
    )


def create_application_or_fail(session: Session, name: str = "sales report") -> dict:
    response = create_application(session, name)
    assert response.status == 201, response.text
    body = response.json
    assert set(body) == {"id", "name", "active_version_id"}, body
    assert body["name"] == name.strip()
    assert body["active_version_id"] is None
    return body


def get_application(session: Session, application_id: str) -> st.Response:
    return session.client.get(f"/api/v1/applications/{application_id}")


def get_draft(session: Session, application_id: str) -> st.Response:
    return session.client.get(f"/api/v1/applications/{application_id}/draft")


def save_draft(
    session: Session,
    application_id: str,
    if_match: str | None,
    *,
    definition=None,
    layout=None,
    capability_card=None,
    raw_body: str | bytes | None = None,
) -> st.Response:
    headers = {}
    if if_match is not None:
        headers["If-Match"] = if_match
    if raw_body is not None:
        return session.client.request(
            "PUT",
            f"/api/v1/applications/{application_id}/draft",
            raw_body=raw_body,
            csrf=session.csrf,
            headers=headers,
        )
    body = {
        "definition": DEFAULT_DEFINITION if definition is None else definition,
        "layout": DEFAULT_LAYOUT if layout is None else layout,
        "capability_card": {} if capability_card is None else capability_card,
    }
    return session.client.request(
        "PUT",
        f"/api/v1/applications/{application_id}/draft",
        body=body,
        csrf=session.csrf,
        headers=headers,
    )


def full_card(*, purpose: str = "summarise sales", examples: tuple[str, ...] | None = None) -> dict:
    examples = examples if examples is not None else ("monthly report", "weekly digest")
    return {
        "purpose": purpose,
        "includes": ["aggregation"],
        "excludes": ["external writes"],
        "required_inputs": ["sales csv"],
        "expected_outputs": ["report"],
        "examples": list(examples),
    }


def list_versions(session: Session, application_id: str, *, cursor: str | None = None, limit=None) -> st.Response:
    query = []
    if cursor is not None:
        query.append("cursor=" + cursor)
    if limit is not None:
        query.append("limit=" + str(limit))
    suffix = ("?" + "&".join(query)) if query else ""
    return session.client.get(f"/api/v1/applications/{application_id}/versions{suffix}")


def get_version(session: Session, version_id: str) -> st.Response:
    return session.client.get(f"/api/v1/versions/{version_id}")


# ---------------------------------------------------------------------------
# grants over the real admin API
# ---------------------------------------------------------------------------
def grant_read(admin: Session, principal_id: str, application_id: str) -> str:
    response = admin.client.post(
        "/api/v1/admin/grants",
        body={
            "principal_id": principal_id,
            "resource_type": "application",
            "resource_id": application_id,
            "action": "read",
        },
        csrf=admin.csrf,
    )
    assert response.status == 201, response.text
    return response.json["id"]


def revoke_grant(admin: Session, grant_id: str) -> st.Response:
    return admin.client.request("DELETE", f"/api/v1/admin/grants/{grant_id}", csrf=admin.csrf)


# ---------------------------------------------------------------------------
# service accounts and scoped API keys (F02A extends the vocabulary with
# application:write; the bearer client is the real credential path)
# ---------------------------------------------------------------------------
def create_service_account(admin: Session, display_name: str, responsible_user_id: str) -> str:
    response = admin.client.post(
        "/api/v1/admin/service-accounts",
        body={"display_name": display_name, "responsible_user_id": responsible_user_id},
        csrf=admin.csrf,
    )
    assert response.status == 201, response.text
    return response.json["id"]


def create_api_key(admin: Session, principal_id: str, scopes: list[str]) -> tuple[str, str]:
    response = admin.client.post(
        "/api/v1/admin/api-keys",
        body={
            "principal_id": principal_id,
            "scopes": list(scopes),
            "expires_at": future_rfc3339(2),
        },
        csrf=admin.csrf,
    )
    assert response.status == 201, response.text
    return response.json["id"], response.json["secret"]


# ---------------------------------------------------------------------------
# database seeding / assertions
# ---------------------------------------------------------------------------
def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _jsonb(value) -> str:
    return _literal(json.dumps(value, ensure_ascii=False)) + "::jsonb"


def _seed_evaluation_report(dbname: str, owner_id: str, *, tag: str = "seed") -> tuple[str, str]:
    """Seed one real ``evaluation_reports`` row plus its ready result artifact.

    T026's 0013 turns ``publication_reviews.report_id`` into a real foreign key.
    Pre-T026 helpers used a random uuid there; the review/report seed now points
    at a real immutable report row. The artifact has no bytes because these
    tests never read them, and no evaluation job is implied by a report row.
    """
    report_id = new_uuid()
    artifact_id = new_uuid()
    object_key = "sealed/" + os.urandom(32).hex()
    st.sql(
        dbname,
        "INSERT INTO platform.artifacts "
        "(id, owner_id, object_key, purpose, media_type, size_bytes, state, sha256) VALUES ("
        f"{_literal(artifact_id)}::uuid, {_literal(owner_id)}::uuid, "
        f"{_literal(object_key)}, 'report', 'application/json', 2, 'ready', "
        f"{_literal(hashlib.sha256(('report-' + tag).encode()).hexdigest())})",
    )
    st.sql(
        dbname,
        "INSERT INTO platform.evaluation_reports "
        "(id, manifest_sha256, dataset_sha256, result_artifact_id, status) VALUES ("
        f"{_literal(report_id)}::uuid, "
        f"{_literal(hashlib.sha256(('manifest-' + tag).encode()).hexdigest())}, "
        f"{_literal(hashlib.sha256(('dataset-' + tag).encode()).hexdigest())}, "
        f"{_literal(artifact_id)}::uuid, 'passed')",
    )
    return report_id, artifact_id


def principal_id_for_login(dbname: str, login: str = ADMIN_LOGIN) -> str:
    return st.sql(
        dbname,
        "SELECT p.id::text FROM platform.principals AS p "
        "JOIN platform.users AS u ON u.principal_id = p.id "
        f"WHERE u.login = {_literal(login)}",
    )


def draft_row(dbname: str, application_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT application_id::text AS application_id, revision, execution_revision, "
        "definition::text AS definition, layout::text AS layout, "
        "capability_card::text AS capability_card, updated_at::text AS updated_at "
        "FROM platform.application_drafts "
        f"WHERE application_id = {_literal(application_id)}::uuid",
    )
    assert len(rows) == 1, rows
    row = rows[0]
    for key in ("definition", "layout", "capability_card"):
        row[key] = json.loads(row[key])
    return row


def application_row(dbname: str, application_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, name, "
        "active_version_id::text AS active_version_id, archived_at::text AS archived_at "
        "FROM platform.applications "
        f"WHERE id = {_literal(application_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def version_rows(dbname: str, application_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, application_id::text AS application_id, version_no, "
        "manifest_sha256, manifest::text AS manifest, review_id::text AS review_id, "
        "revoked_at::text AS revoked_at "
        "FROM platform.application_versions "
        f"WHERE application_id = {_literal(application_id)}::uuid "
        "ORDER BY version_no DESC",
    )


def seed_published_versions(
    dbname: str,
    application_id: str,
    *,
    owner_id: str,
    count: int = 3,
    manifest_tag: str = "seeded",
) -> list[dict]:
    """Seed real published-version rows plus their real supporting rows.

    The manifest artifact row is real (the schema requires the FK); its bytes
    are not written, because T023 has no manifest generator or object-store read
    (T025 owns both). Since T026's 0013 every ``publication_reviews.report_id``
    is a real FK, so each review points at a seeded ``evaluation_reports`` row
    (immutable report metadata with its own ready result artifact).
    """
    artifact_id = new_uuid()
    object_key = "sealed/" + os.urandom(32).hex()
    st.sql(
        dbname,
        "INSERT INTO platform.artifacts "
        "(id, owner_id, object_key, purpose, media_type, size_bytes, state, sha256) VALUES ("
        f"{_literal(artifact_id)}::uuid, {_literal(owner_id)}::uuid, "
        f"{_literal(object_key)}, 'version_manifest', 'application/json', 2, 'ready', "
        f"{_literal(hashlib.sha256(('manifest-' + application_id).encode()).hexdigest())})",
    )

    created: list[dict] = []
    for version_no in range(1, count + 1):
        version_id = new_uuid()
        review_id = new_uuid()
        report_id, _report_artifact_id = _seed_evaluation_report(
            dbname, owner_id, tag=f"{manifest_tag}-{version_no}"
        )
        manifest = {
            "manifest_version": "1.0",
            "application_id": application_id,
            "source_revision": version_no,
            "metadata": {"tag": f"{manifest_tag}-{version_no}"},
            "definition": {"nodes": [{"id": f"seeded-{version_no}"}]},
        }
        manifest_sha = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        st.sql(
            dbname,
            "INSERT INTO platform.publication_reviews "
            "(id, application_id, submitted_by, snapshot, manifest_sha256, report_id, "
            " state, reviewer_id, decided_at) VALUES ("
            f"{_literal(review_id)}::uuid, {_literal(application_id)}::uuid, "
            f"{_literal(owner_id)}::uuid, " + _jsonb(manifest) + ", "
            f"{_literal(manifest_sha)}, {_literal(report_id)}::uuid, 'approved', "
            f"{_literal(owner_id)}::uuid, now())",
        )
        st.sql(
            dbname,
            "INSERT INTO platform.application_versions "
            "(id, application_id, version_no, review_id, manifest, manifest_sha256, "
            " manifest_artifact_id, capability_card, compiler_version, worker_image_digest, "
            " state_schema_version) VALUES ("
            f"{_literal(version_id)}::uuid, {_literal(application_id)}::uuid, {version_no}, "
            f"{_literal(review_id)}::uuid, " + _jsonb(manifest) + ", "
            f"{_literal(manifest_sha)}, {_literal(artifact_id)}::uuid, '{{}}'::jsonb, "
            "'p05-1.0.0', " + _literal("sha256:" + "0" * 64) + ", 'orchara-state/1.0')",
        )
        created.append(
            {
                "id": version_id,
                "application_id": application_id,
                "version_no": version_no,
                "manifest": manifest,
                "manifest_sha256": manifest_sha,
                "review_id": review_id,
            }
        )
    latest = created[-1]["id"]
    st.sql(
        dbname,
        f"UPDATE platform.applications SET active_version_id = {_literal(latest)}::uuid "
        f"WHERE id = {_literal(application_id)}::uuid",
    )
    return created
