"""F02B test package: the real publication stack.

The stack is F13A/T052's real one (throwaway PostgreSQL migrated by ``cmd/admin``,
the compiled ``cmd/api`` with a real object store + signed links + mTLS, the real
``cmd/maintenance`` pools and the real ``cmd/supervisor exec`` executor). A
``start -> end`` application executes without the WSL2 sandbox, so the publication
tests can produce a genuine frozen manifest and a genuine guarded evaluation
report without depending on the shared sandbox being free.
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
def t068_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> t026.T026Stack:
    """The F02B stack: T052's real stack (objects, signed links, mTLS, compiler)."""
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    compiler_inputs = t025.api_extra_env(None)
    # T056's margin, reused: under several concurrent suites a sandbox attempt can
    # pause long enough that a 30s execution lease expires mid-heartbeat and the
    # supervisor abandons a run the environment was only slow to start. A 300s
    # lease does not change any product rule; the sandbox timeout still bounds the
    # work and the evaluation case timeout still records a genuine not_run.
    compiler_inputs["ORCHARA_EXECUTION_LEASE_TTL"] = "300s"
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
