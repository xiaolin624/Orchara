"""F14/T090 test package: shared paths, freeze assets and one real-stack fixture.

The freeze/leakage tests are pure Python over committed files. The single
real-stack test replays the frozen routing corpus through the real REST routing
control plane; its fixture is the F02B/T068 stack shape used by T084, with no
sandbox execution in this ticket.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TESTS_ROOT.parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from support import f00b, t024, t025, t026  # noqa: E402
from support import stack as st  # noqa: E402
from support.certs import make_stack_certificates, unique_internal_addr  # noqa: E402
from support.fixtures import *  # noqa: E402,F401,F403


@pytest.fixture
def t090_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> t026.T026Stack:
    """The real stack for the T090 routing baseline (same shape as T084's).

    Migration, bootstrap, cookie+CSRF HTTP, object store, mTLS internal listener and
    the compiler identities the real publication/validate path needs. The routing
    baseline executes no sandbox Run, so no execution-lease knob is required.
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
