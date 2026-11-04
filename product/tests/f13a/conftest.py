"""F13A test package: the shared real-stack fixtures plus the T052 stack.

The fixtures live in ``tests/support/fixtures.py`` (real PostgreSQL, real
compiled binaries, real HTTP, real object store, real signed links, the real
maintenance process and the real supervisor executor). ``tests/support/t052.py``
holds the T052 helpers.

``t052_stack`` is the T024/T026 shape (object store, signing links, internal
mTLS) *plus* the compiler inputs a draft freeze needs: an evaluation submission
compiles the frozen snapshot, so the API must know the worker image digest and
the other compiler identities exactly like ``f06_stack`` does. It is the same
real server, not a mock.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from support import f00b, t024, t025, t026  # noqa: E402
from support import stack as st  # noqa: E402
from support.certs import make_stack_certificates, unique_internal_addr  # noqa: E402
from support.fixtures import *  # noqa: E402,F401,F403


@pytest.fixture
def t052_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> t026.T026Stack:
    """The F13A stack: objects + signed links + mTLS + compiler identities."""
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    compiler_inputs = t025.api_extra_env(None)
    server = api_server(
        dsn,
        extra_env=t024.api_extra_env(
            object_dir, certificates, signing_key=signing_key, extra=compiler_inputs
        ),
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
def t056_stack(t052_stack) -> t026.T026Stack:
    """T056 runs on the T052 stack: the same real server, database and binaries."""
    return t052_stack
