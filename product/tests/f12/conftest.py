"""F12/T093 test package: the external-client REST/SSE conformance gate.

The suite runs against the real stack used by the other F-series suites: a
throwaway PostgreSQL database migrated with the real ``cmd/admin`` chain, the
compiled ``cmd/api`` listening on a loopback socket, a real internal mTLS
listener with per-role client certificates, and the real object store. Every
API call is HTTP over a socket; no test imports a handler or a service.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TESTS_ROOT.parent

for entry in (TESTS_ROOT, PRODUCT_ROOT / "worker", PRODUCT_ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from support import f00b, stack as st, t024, t029, t033  # noqa: E402
from support.certs import make_stack_certificates, unique_internal_addr  # noqa: E402
from support.fixtures import *  # noqa: E402,F401,F403
from support.stack import WEB_DIR  # noqa: E402

import f12_api_gate as gate_module  # noqa: E402

CONTRACT = PRODUCT_ROOT / "contracts" / "openapi.json"
EVIDENCE = PRODUCT_ROOT / "reports" / "f12" / "T093-api-conformance.json"


@contextlib.contextmanager
def _fresh_stack(
    tmp_path_factory: pytest.TempPathFactory, go_binaries: dict[str, Path], label: str
):
    """One migrated + bootstrapped database behind one real API process."""
    tmp_path = tmp_path_factory.mktemp(f"f12-{label}-stack")
    try:
        database = st.create_database("orchara_f12_")
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
    # Keep the approval deadline sweep out of the way; the gate never publishes
    # a node approval and a 30s sweep would merely add nondeterminism.
    env["ORCHARA_APPROVAL_SWEEP_INTERVAL"] = "5m"
    server = st.start_api(
        go_binaries["api"],
        dsn,
        web_dir=WEB_DIR,
        profile="dev-stub",
        extra_env=env,
        log_path=tmp_path / "api.log",
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


@pytest.fixture(scope="session")
def f12_stack(
    tmp_path_factory: pytest.TempPathFactory, go_binaries: dict[str, Path]
) -> t033.T033Stack:
    with _fresh_stack(tmp_path_factory, go_binaries, "gate") as stack:
        yield stack


@pytest.fixture(scope="session")
def f12_cli_stack(
    tmp_path_factory: pytest.TempPathFactory, go_binaries: dict[str, Path]
) -> t033.T033Stack:
    """A second pristine stack for the CLI replay.

    The gate mutates the API it judges (it creates a real run and claims a live
    lease), so replaying the CLI against the same database would observe the
    session run's fixtures instead of a clean 106-operation baseline.
    """
    with _fresh_stack(tmp_path_factory, go_binaries, "cli") as stack:
        yield stack


def build_gate(stack: t033.T033Stack, *, contract: Path = CONTRACT) -> gate_module.Gate:
    options = gate_module.GateOptions(
        base_url=stack.server.base_url,
        admin_login=f00b.ADMIN_LOGIN,
        admin_password=f00b.ADMIN_PASSWORD,
        contract_path=contract,
        internal=gate_module.InternalOptions(
            url=stack.certificates.internal_url,
            ca_path=stack.certificates.ca.cert,
            cert_dir=stack.certificates.clients["worker"].cert.parent,
        ),
        expected_operations=gate_module.DEFAULT_EXPECTED_OPERATIONS,
        profile="dev-stub",
        product_root=PRODUCT_ROOT,
        timeout=30.0,
    )
    return gate_module.Gate(options)


@pytest.fixture(scope="session")
def f12_gate(f12_stack: t033.T033Stack) -> gate_module.Gate:
    """The gate, run once against the real stack; every test reads its report."""
    instance = build_gate(f12_stack)
    instance.run()
    return instance


@pytest.fixture(scope="session")
def f12_report(f12_gate: gate_module.Gate, tmp_path_factory: pytest.TempPathFactory) -> dict:
    report = f12_gate._report()  # noqa: SLF001 - the report builder is the gate's public result
    report_path = tmp_path_factory.mktemp("f12-report") / "T093-api-conformance.json"
    report_path.write_text(
        gate_module.json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if os.environ.get("F12_WRITE_EVIDENCE") == "1":
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(
            gate_module.json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return report
