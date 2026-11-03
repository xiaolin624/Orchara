"""Fixtures for the F00B/T022 and F02A/T023 tests (real PG, binaries, HTTP/mTLS)."""

from __future__ import annotations

from pathlib import Path

import pytest

from support import stack as st
from support import f00b, f02a, t024, t025, t026, t027, t028, t029, t030, t032, t033
from support.certs import ROLES, make_stack_certificates, unique_internal_addr


@pytest.fixture(scope="session")
def go_binaries(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    if not Path(st.GO).exists():
        pytest.skip("Go toolchain not available")
    out_dir = tmp_path_factory.mktemp("f00b-go-bin")
    return st.build_go_binaries(
        out_dir,
        {"api": "./cmd/api", "admin": "./cmd/admin", "maintenance": "./cmd/maintenance",
         "supervisor": "./cmd/supervisor", "sandboxprobe": "./cmd/sandboxprobe"},
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


@pytest.fixture
def t025_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> t025.T025Stack:
    """A migrated database behind a real API with an object store and the
    placeholder worker image digest.

    The object-store root lives under ``tmp_path``; the runtime lock is the P01
    lock file, so the manifest hash uses the real lock digest. No mTLS listener is
    started: validate is a public authenticated endpoint and does not need the
    internal protocol.
    """
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    server = api_server(dsn, extra_env=t025.api_extra_env(object_dir))
    return t025.T025Stack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        object_dir=object_dir,
        binaries=go_binaries,
    )


@pytest.fixture
def t026_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> t026.T026Stack:
    """A T024-shaped stack (signing links, internal mTLS, object store) for T026.

    The evaluation pipeline needs the same real primitives as T024: job-scoped
    signed uploads/downloads, the internal claim/result protocol and a real
    object store. Nothing T026-specific is faked here.
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

    stack = t026.T026Stack(
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
def t028_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> t028.T028Stack:
    """A T024-shaped stack for the user-upload path.

    T028 needs every real primitive at once: a migrated + bootstrapped database,
    a real API with the object store and HMAC link signing, the internal mTLS
    listener (used to claim a finalize job deterministically in the deletion
    race test) and the compiled maintenance process that runs the real handler.
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

    stack = t028.T028Stack(
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
def t030_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> t030.T030Stack:
    """A T028-shaped stack for the T030 preset tool path.

    The tool run needs the same real primitives as T028 (migrated database, real
    API with object store and signed links, real maintenance process) plus the
    real Python interpreter. No stack-specific knob is added: the preset tool's
    script path is resolved from the product root, exactly as in production.
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

    stack = t030.T030Stack(
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
def t032_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> t032.T032Stack:
    """A T029/T031-shaped stack with T032's event-stream deployment knobs.

    The three knobs are validated configuration (not per-request values): the
    production defaults are 24h retention, 500ms PostgreSQL polling and a 15s
    heartbeat comment; this fixture shortens polling/heartbeat so the real SSE
    tests finish quickly while keeping retention long enough that ordinary
    replay never expires.
    """
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    env = t029.api_extra_env(object_dir, certificates, signing_key=signing_key)
    env.update(
        {
            "ORCHARA_RUN_EVENT_RETENTION": "1h",
            "ORCHARA_RUN_EVENT_POLL_INTERVAL": "20ms",
            "ORCHARA_RUN_EVENT_HEARTBEAT_INTERVAL": "100ms",
        }
    )
    server = api_server(dsn, extra_env=env)

    stack = t032.T032Stack(
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
def t027_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> t027.T027Stack:
    """A migrated database behind a real API with a real object store.

    The debug submission seals the canonical manifest (T025) and the canonical
    test input (T027), so the object store is required. The deployment inputs are
    T025's placeholders (fixed worker image digest, real P01 runtime lock); the
    API needs no internal mTLS listener because debug is a public authenticated
    endpoint.
    """
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    server = api_server(dsn, extra_env=t025.api_extra_env(object_dir))
    return t027.T027Stack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        object_dir=object_dir,
        binaries=go_binaries,
    )


@pytest.fixture
def t029_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> t029.T029Stack:
    """A T024-shaped stack plus the T025 compiler input, for claim/heartbeat/exit.

    F03 needs every real primitive at once: a migrated + bootstrapped database,
    the internal mTLS listener (the only place claim/heartbeat/exit exist), the
    object store with HMAC link signing (the lease's manifest_url/input_url are
    real sealed GET links) and the T025 deployment input so ``debugDraft`` can
    freeze the checked-in golden manifest.
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
        dsn,
        extra_env=t029.api_extra_env(object_dir, certificates, signing_key=signing_key),
    )

    stack = t029.T029Stack(
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
def t033_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> t033.T033Stack:
    """T031's real stack for the T033 tool-debug path.

    Everything the executor needs is real: the internal mTLS listener (claim,
    heartbeat, result, exit and the five run-scoped artifact routes), the object
    store with HMAC link signing (the manifest and the CSV move through real
    signed links), the T025 deployment inputs so ``debugDraft`` freezes the golden
    manifest, and the compiled supervisor binary. The sandbox itself lives in WSL2
    and is exercised by the tests, not by this fixture.
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
        dsn,
        extra_env=t029.api_extra_env(object_dir, certificates, signing_key=signing_key),
    )

    stack = t033.T033Stack(
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


__all__ = [
    "ROLES",
    "fresh_database",
    "go_binaries",
    "migrated_database",
    "api_server",
    "t022_stack",
    "t024_stack",
    "f02a_stack",
    "t025_stack",
    "t026_stack",
    "t027_stack",
    "t028_stack",
    "t029_stack",
    "t030_stack",
    "t032_stack",
    "t033_stack",
]
