"""F11 test package: shared real-stack fixtures.

T069's stack is the T029/F13 shape: a throwaway PostgreSQL 16 database migrated by
the real ``cmd/admin`` binary, the compiled ``cmd/api`` behind real HTTP with the
real internal mTLS listener, the real filesystem object store with HMAC-signed
links, the T025 deployment inputs (so a real debug run can be frozen/claimed) and
the compiled maintenance process (so artifacts and documents can really be
finalized/ingested). It is exactly the stack the F09/F10 suites use, with the
documented execution-slot capacity raised the way an operator would.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from support.runtime_stack import Stack
from support import f00b, t024, t029  # noqa: E402
from support import stack as st  # noqa: E402
from support.certs import make_stack_certificates, unique_internal_addr  # noqa: E402
from support.fixtures import *  # noqa: E402,F401,F403

CAPACITY = 16


@pytest.fixture
def t069_stack(tmp_path, fresh_database, go_binaries, api_server) -> Stack:
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)
    st.sql(
        dbname,
        "UPDATE platform.quota_counters SET capacity = "
        f"{CAPACITY} WHERE scope = 'global' AND dimension = 'execution_slots'",
    )

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    server = api_server(
        dsn,
        extra_env=t029.api_extra_env(object_dir, certificates, signing_key=signing_key),
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
    f00b.wait_for_internal(stack)
    return stack
