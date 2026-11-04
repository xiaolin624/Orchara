"""F15A/T057 acceptance package: the shared real-stack fixtures.

The suite starts the deployment through the *deliverable* bring-up entry
(``deploy/dev/bringup.py``) rather than a test-only lookalike, against a real
PostgreSQL database, real compiled binaries and real HTTP. The smoke runs once
against that environment through the real public API, and the individual tests
assert the recorded facts plus the database state behind them.

``support.fixtures`` is imported exactly like the other ticket packages so the
shared session fixtures are available; the T057 fixtures add only the deploy-entry
wiring on top.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DEV = PRODUCT_ROOT / "deploy" / "dev"
if str(DEPLOY_DEV) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DEV))

from support.fixtures import *  # noqa: E402,F401,F403

ADMIN_PASSWORD = "t057-admin-" + secrets.token_urlsafe(18)


@pytest.fixture(scope="session")
def dev_stack(tmp_path_factory: pytest.TempPathFactory):
    """A clean database + real API + real maintenance process from the deliverable entry."""
    import bringup

    state_root = tmp_path_factory.mktemp("t057-devstack")
    stack = bringup.DevStack.start(
        state_root=state_root,
        database="orchara_t057_" + secrets.token_hex(6),
        fresh=True,
        admin_password=ADMIN_PASSWORD,
    )
    try:
        yield stack
    finally:
        stack.stop()


@pytest.fixture(scope="session")
def smoke_evidence(dev_stack) -> dict:
    """Run the sales/failure/cancel/model-profile smoke once through the real API."""
    import smoke

    certificates = dev_stack.certificates
    assert certificates is not None
    supervisor_cert, supervisor_key = certificates.client("supervisor")
    internal = smoke.InternalAccess(
        url=dev_stack.internal_url,
        ca=str(certificates.ca_cert),
        client_cert=str(supervisor_cert),
        client_key=str(supervisor_key),
        role="supervisor",
    )
    evidence = smoke.run(
        base_url=dev_stack.base_url,
        admin_login=dev_stack.admin_login,
        admin_password=dev_stack.admin_password,
        internal=internal,
        environment=dev_stack.facts(),
        command="pytest tests/acceptance (T057 smoke fixture)",
        require_node=True,
    )
    return evidence
