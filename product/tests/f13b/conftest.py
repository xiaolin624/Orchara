"""F13B test package: the real publication stack (T103).

The stack is the same real one F13A/T052 and F02B/T068 use: a throwaway
PostgreSQL database migrated by the real ``cmd/admin`` binary, the compiled
``cmd/api`` with a real object store, HMAC signed links and the internal mTLS
listener, the real ``cmd/maintenance`` pools and the real ``cmd/supervisor exec``
executor. A ``start -> end`` application executes without the WSL2 sandbox, so
the T103 hard-vs-quality evidence can be produced without depending on the
shared sandbox being free.
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
def t103_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> t026.T026Stack:
    """T052's real stack (objects, signed links, mTLS, compiler identities).

    ``ORCHARA_EXECUTION_LEASE_TTL`` is the T056/T068 test-only margin: the T103
    crafted-report case runs are driven by the same real supervisor and must not
    lose a lease to a stalled heartbeat while other suites share the host. It
    changes no product rule; the sandbox/evaluation timeouts still bound the work.
    """
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    compiler_inputs = t025.api_extra_env(None)
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
