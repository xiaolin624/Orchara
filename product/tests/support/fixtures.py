"""Fixtures for the F00B/T022 and F02A/T023 tests (real PG, binaries, HTTP/mTLS)."""

from __future__ import annotations

from pathlib import Path

import pytest

from support import stack as st
from support import f00b, f02a, t024
from support.certs import ROLES, make_stack_certificates, unique_internal_addr


@pytest.fixture(scope="session")
def go_binaries(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    if not Path(st.GO).exists():
        pytest.skip("Go toolchain not available")
    out_dir = tmp_path_factory.mktemp("f00b-go-bin")
    return st.build_go_binaries(
        out_dir,
        {"api": "./cmd/api", "admin": "./cmd/admin", "maintenance": "./cmd/maintenance"},
    )


@pytest.fixture
def fresh_database():
    try:
        database = st.create_database("orchara_f00b_")
    except RuntimeError:
        pytest.skip("isolated development cluster is not running (run tools/pg_dev.py init)")
    try:
        yield database
    finally:
        st.drop_database(database.name)


@pytest.fixture
def migrated_database(fresh_database, go_binaries):
    """A fresh database with the full migration chain and the first admin."""
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)
    st.set_worker_password(dbname)
    return fresh_database


@pytest.fixture
def api_server(tmp_path: Path, go_binaries: dict[str, Path]):
    """Factory fixture: ``api_server(dsn, extra_env=...)`` starts a real API."""
    from support.stack import WEB_DIR

    started: list[st.ApiServer] = []

    def _start(
        dsn: str,
        *,
        web_dir: Path = WEB_DIR,
        profile: str = "dev-stub",
        extra_env: dict[str, str] | None = None,
    ) -> st.ApiServer:
        server = st.start_api(
            go_binaries["api"],
            dsn,
            web_dir=web_dir,
            profile=profile,
            extra_env=extra_env,
            log_path=tmp_path / f"api-{len(started)}.log",
        )
        started.append(server)
        return server

    yield _start
    for server in reversed(started):
        server.stop()


@pytest.fixture
def t022_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> f00b.T022Stack:
    """One migrated + bootstrapped database behind one API with an mTLS listener.

    The object store root and all certificate material live under ``tmp_path`` so
    nothing leaks between tests and a failed test cannot poison the next one.
    """
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    server = api_server(dsn, extra_env=f00b.api_extra_env(object_dir, certificates))

    stack = f00b.T022Stack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        object_dir=object_dir,
        certificates=certificates,
        binaries=go_binaries,
    )
    f00b.wait_for_internal(stack)
    return stack


@pytest.fixture
def t024_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> t024.T024Stack:
    """A migrated + bootstrapped database behind one API with signing+mTLS.

    Everything secret (the HMAC signing key, certificate material) is generated
    under ``tmp_path`` for this test only and is never logged.
    """
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    server = api_server(
        dsn, extra_env=t024.api_extra_env(object_dir, certificates, signing_key=signing_key)
    )

    stack = t024.T024Stack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        object_dir=object_dir,
        certificates=certificates,
        binaries=go_binaries,
        signing_key=signing_key,
    )
    f00b.wait_for_internal(stack)
    return stack


@pytest.fixture
def f02a_stack(migrated_database, go_binaries, api_server) -> f02a.F02AStack:
    """A migrated + bootstrapped database behind a real API, without mTLS/objects.

    T023's draft and version paths need neither the internal listener nor the
    object store; starting without them is also the honest deployment shape for
    a ticket that has no object bytes to move.
    """
    dsn, dbname = migrated_database
    server = api_server(dsn)
    return f02a.F02AStack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        binaries=go_binaries,
    )


__all__ = [
    "ROLES",
    "fresh_database",
    "go_binaries",
    "migrated_database",
    "api_server",
    "t022_stack",
    "t024_stack",
    "f02a_stack",
]
