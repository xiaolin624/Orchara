"""Shared support for T095 (GitHub #96): one pristine real stack + principals.

Why this module builds its own stack instead of reusing the package fixture
``f12_stack``: that fixture runs the T093 conformance gate, which creates a real
Run and claims a live execution lease. Reusing it would make the role and
revocation observations read a database the gate has already mutated. The brief
for T095 explicitly forbids that reuse; the ``t033_stack`` fixture in
``tests/support/fixtures.py`` is function-scoped and would rebuild the whole
database for every parametrized row of the entry table, so this module builds a
module-scoped stack with the same real pieces (isolated PostgreSQL database,
compiled ``cmd/api``, real internal mTLS listener, real object directory).

Nothing here is a mock: every assertion in the T095 REST tests goes through a
real HTTP socket to that process.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TESTS_ROOT.parent
for entry in (TESTS_ROOT, PRODUCT_ROOT / "worker", PRODUCT_ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from support.runtime_stack import Stack
from support import f00b, f02a, stack as st, t024, t029, t033  # noqa: E402
from support.certs import make_stack_certificates, unique_internal_addr  # noqa: E402


@pytest.fixture(scope="module")
def t095_stack(tmp_path_factory: pytest.TempPathFactory, go_binaries: dict[str, Path]):
    """One migrated + bootstrapped database behind one real API process."""
    tmp_path = tmp_path_factory.mktemp("t095-stack")
    try:
        database = st.create_database("orchara_t095_")
    except RuntimeError as exc:  # pragma: no cover - environment missing
        pytest.skip(f"isolated development cluster is not running: {exc}")
    dsn, dbname = database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    env = t029.api_extra_env(object_dir, certificates, signing_key=signing_key)
    # The approval sweeper is not exercised here; keep it out of the way so a
    # parked approval cannot add nondeterminism to a role observation.
    env["ORCHARA_APPROVAL_SWEEP_INTERVAL"] = "5m"
    server = st.start_api(
        go_binaries["api"],
        dsn,
        web_dir=st.WEB_DIR,
        profile="dev-stub",
        extra_env=env,
        log_path=tmp_path / "api.log",
    )
    stack = Stack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        object_dir=object_dir,
        certificates=certificates,
        binaries=go_binaries,
        signing_key=signing_key,
    )
    try:
        f00b.wait_for_internal(stack)
    except Exception:
        server.stop()
        st.drop_database(dbname)
        raise
    try:
        yield stack
    finally:
        server.stop()
        st.drop_database(dbname)


class T095Resources:
    """The principals and resources the entry table and revocation tests share.

    Each principal is separate on purpose: the role-update test promotes one
    principal, the revocation test disables another, and the entry table must
    keep observing the *original* roles of ``reader``.
    """

    def __init__(
        self,
        stack,
        admin: f00b.Session,
        creator: f00b.Session,
        reader: f00b.Session,
        promoted: f00b.Session,
        revoked: f00b.Session,
        granted: f00b.Session,
        app_ids: list[str],
        shared_app_id: str,
        reader_grant_id: str,
        granted_grant_id: str,
        service_principal_id: str,
        service_key: str,
        reader_principal_id: str,
        creator_principal_id: str,
        promoted_principal_id: str,
        disabled_service_principal_id: str,
        disabled_service_key: str,
    ) -> None:
        self.stack = stack
        self.admin = admin
        self.creator = creator
        self.reader = reader
        self.promoted = promoted
        self.revoked = revoked
        self.granted = granted
        self.app_ids = app_ids
        self.shared_app_id = shared_app_id
        self.reader_grant_id = reader_grant_id
        self.granted_grant_id = granted_grant_id
        self.service_principal_id = service_principal_id
        self.service_key = service_key
        self.reader_principal_id = reader_principal_id
        self.creator_principal_id = creator_principal_id
        self.promoted_principal_id = promoted_principal_id
        # A service principal + key for the 403 principal_disabled branch. A
        # browser session of a disabled user observes 401 session_revoked first
        # (the session is revoked in the same transaction); an API key observes
        # the principal state, which is the 403 branch the pages must also handle.
        self.disabled_service_principal_id = disabled_service_principal_id
        self.disabled_service_key = disabled_service_key

    def path(self, template: str) -> str:
        """Interpolate the resource ids into one entry-table path."""
        return template.format(
            app_id=self.shared_app_id,
            reader_id=self.reader_principal_id,
            creator_id=self.creator_principal_id,
            grant_id=self.reader_grant_id,
        )


@pytest.fixture(scope="module")
def t095_resources(t095_stack) -> T095Resources:
    stack = t095_stack
    admin = f00b.admin_session(stack)
    creator = f00b.invite_and_activate(
        stack, admin, "t095.creator", roles=("user", "creator")
    )
    reader = f00b.invite_and_activate(stack, admin, "t095.reader", roles=("user",))
    promoted = f00b.invite_and_activate(stack, admin, "t095.promoted", roles=("user",))
    revoked = f00b.invite_and_activate(stack, admin, "t095.revoked", roles=("user",))
    granted = f00b.invite_and_activate(stack, admin, "t095.granted", roles=("user",))

    # Two applications owned by the creator: two pages of one item each.
    first = f02a.create_application_or_fail(creator, "t095 shared application")
    second = f02a.create_application_or_fail(creator, "t095 second application")
    shared_app_id = first["id"]
    granted_app_id = second["id"]

    reader_grant_id = f02a.grant_read(admin, reader.principal_id, shared_app_id)
    granted_grant_id = f02a.grant_read(admin, granted.principal_id, granted_app_id)

    service_principal_id = f02a.create_service_account(
        admin, "t095 service account", admin.principal_id
    )
    _key_id, service_key = f02a.create_api_key(
        admin, service_principal_id, ["application:read"]
    )
    disabled_service_principal_id = f02a.create_service_account(
        admin, "t095 to-be-disabled service", admin.principal_id
    )
    _disabled_key_id, disabled_service_key = f02a.create_api_key(
        admin, disabled_service_principal_id, ["application:read"]
    )

    return T095Resources(
        stack=stack,
        admin=admin,
        creator=creator,
        reader=reader,
        promoted=promoted,
        revoked=revoked,
        granted=granted,
        app_ids=[first["id"], second["id"]],
        shared_app_id=shared_app_id,
        reader_grant_id=reader_grant_id,
        granted_grant_id=granted_grant_id,
        service_principal_id=service_principal_id,
        service_key=service_key,
        reader_principal_id=reader.principal_id,
        creator_principal_id=creator.principal_id,
        promoted_principal_id=promoted.principal_id,
        disabled_service_principal_id=disabled_service_principal_id,
        disabled_service_key=disabled_service_key,
    )


@dataclass(frozen=True)
class EntryRow:
    """One row of the 入口 -> 端点 -> 负例主体 -> 状态码 table.

    ``path`` is ``str.format``-interpolated with the resource ids, so the same
    table can be read as documentation and executed as the parametrized tests.
    """

    entry: str
    method: str
    path: str
    principal: str  # reader | anonymous | service_key | promoted
    status: int
    code: str
    body: dict | None = None
    csrf: bool = True


# The negative-principal half of AC1: hiding an entry in the navigation must not
# be the thing that protects the endpoint. Every row below calls the endpoint the
# hidden UI entry would call, with a principal that does not hold the capability.
ENTRY_TABLE: tuple[EntryRow, ...] = (
    EntryRow(
        "管理台·邀请用户",
        "POST",
        "/api/v1/admin/users",
        "reader",
        403,
        "forbidden",
        {"login": "t095.escalation", "display_name": "Escalation", "roles": ["user"]},
    ),
    EntryRow(
        "管理台·创建授权",
        "POST",
        "/api/v1/admin/grants",
        "reader",
        403,
        "forbidden",
        {
            "principal_id": "00000000-0000-4000-8000-000000000000",
            "resource_type": "application",
            "resource_id": "00000000-0000-4000-8000-000000000000",
            "action": "read",
        },
    ),
    EntryRow(
        "管理台·修改角色",
        "PUT",
        "/api/v1/admin/users/{reader_id}/roles",
        "reader",
        403,
        "forbidden",
        {"roles": ["admin", "user"]},
    ),
    EntryRow(
        "管理台·创建服务账号",
        "POST",
        "/api/v1/admin/service-accounts",
        "reader",
        403,
        "forbidden",
        {"display_name": "escalation", "responsible_user_id": "{reader_id}"},
    ),
    EntryRow(
        "管理台·创建 API key",
        "POST",
        "/api/v1/admin/api-keys",
        "reader",
        403,
        "forbidden",
        {
            "principal_id": "{reader_id}",
            "scopes": ["application:read"],
            "expires_at": "2099-01-01T00:00:00Z",
        },
    ),
    EntryRow(
        "管理台·禁用主体",
        "PATCH",
        "/api/v1/admin/principals/{creator_id}",
        "reader",
        403,
        "forbidden",
        {"disabled": True, "reason": "t095 should never reach the service"},
    ),
    EntryRow(
        "管理台·撤销授权",
        "DELETE",
        "/api/v1/admin/grants/{grant_id}",
        "reader",
        403,
        "forbidden",
    ),
    EntryRow(
        "管理台·模型端点目录",
        "GET",
        "/api/v1/admin/model-endpoints",
        "reader",
        403,
        "forbidden",
    ),
    EntryRow(
        "创作·新建应用",
        "POST",
        "/api/v1/applications",
        "reader",
        403,
        "creator_role_required",
        {"name": "t095 should not be created"},
    ),
    EntryRow(
        "创作·保存草稿",
        "PUT",
        "/api/v1/applications/{app_id}/draft",
        "reader",
        403,
        "creator_role_required",
        {"definition": {}, "layout": {}, "capability_card": {}},
    ),
    EntryRow(
        "创作·校验应用",
        "POST",
        "/api/v1/applications/{app_id}/validate",
        "reader",
        403,
        "creator_role_required",
        {},
    ),
    EntryRow(
        "创作·私有调试",
        "POST",
        "/api/v1/applications/{app_id}/debug-runs",
        "reader",
        403,
        "creator_role_required",
        {},
    ),
    EntryRow(
        "创作·发布预览",
        "POST",
        "/api/v1/applications/{app_id}/publication-preview",
        "reader",
        403,
        "creator_role_required",
        {},
    ),
    EntryRow(
        "创作·提交发布",
        "POST",
        "/api/v1/applications/{app_id}/submit",
        "reader",
        403,
        "creator_role_required",
        {},
    ),
    EntryRow(
        "创作·提交评测",
        "POST",
        "/api/v1/applications/{app_id}/evaluations",
        "reader",
        403,
        "creator_role_required",
        {},
    ),
    EntryRow(
        "个人数据·记忆列表（服务凭据）",
        "GET",
        "/api/v1/memories",
        "service_key",
        403,
        "personal_scope_forbidden",
    ),
    EntryRow(
        "个人数据·诊断授权列表（服务凭据）",
        "GET",
        "/api/v1/diagnostic-grants",
        "service_key",
        403,
        "personal_scope_forbidden",
    ),
    EntryRow(
        "个人数据·创建诊断授权（服务凭据）",
        "POST",
        "/api/v1/diagnostic-grants",
        "service_key",
        403,
        "personal_scope_forbidden",
        {},
        csrf=False,
    ),
    EntryRow(
        "未认证·应用列表",
        "GET",
        "/api/v1/applications",
        "anonymous",
        401,
        "unauthenticated",
    ),
    EntryRow(
        "未认证·管理端点",
        "GET",
        "/api/v1/admin/model-endpoints",
        "anonymous",
        401,
        "unauthenticated",
    ),
)


def client_for(resources: T095Resources, principal: str):
    """The real HTTP client for one negative principal."""
    if principal == "anonymous":
        return resources.stack.client()
    if principal == "service_key":
        return resources.stack.client()
    session = {
        "reader": resources.reader,
        "creator": resources.creator,
        "promoted": resources.promoted,
    }[principal]
    return session.client


def call_entry(resources: T095Resources, row: EntryRow) -> "st.Response":
    client = client_for(resources, row.principal)
    path = resources.path(row.path)
    headers = None
    if row.principal == "service_key":
        headers = {"Authorization": "Bearer " + resources.service_key}
    kwargs: dict = {}
    if row.body is not None:
        kwargs["body"] = row.body
    if headers is not None:
        kwargs["headers"] = headers
    if row.csrf and row.principal in {"reader", "creator", "promoted"}:
        kwargs["csrf"] = {
            "reader": resources.reader.csrf,
            "creator": resources.creator.csrf,
            "promoted": resources.promoted.csrf,
        }[row.principal]
    return client.request(row.method, path, **kwargs)


def assert_refused(response: "st.Response", status: int, code: str) -> None:
    """Assert the contract refusal shape, not only the status number."""
    assert response.status == status, (
        f"expected {status}, got {response.status}: {response.text}"
    )
    body = response.json
    assert isinstance(body, dict) and "error" in body, response.text
    error = body["error"]
    assert error.get("code") == code, response.text
    assert error.get("message"), response.text
    assert error.get("request_id"), response.text
