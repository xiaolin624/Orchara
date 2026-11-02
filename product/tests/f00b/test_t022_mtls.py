"""T022 acceptance: the internal listener's mTLS and capacity rules.

The listener requires a chain-verified client certificate (TLS layer) and then
requires the certificate's Organization to be one of supervisor/maintenance/worker
*and* the X-Internal-Role header to repeat it exactly. Claim/heartbeat/result are
maintenance-capacity endpoints, so a supervisor or worker certificate is
authenticated but still refused 403.
"""

from __future__ import annotations

import subprocess

from support import f00b
from support import stack as st
from support.certs import make_ca, make_client_certificate
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
    t022_stack,
)


def test_a_client_certificate_is_required(t022_stack) -> None:
    no_certificate = f00b.InternalClient(
        t022_stack.certificates.internal_url, t022_stack.certificates.ca.cert, None
    )
    f00b.assert_handshake_refused(
        lambda: no_certificate.get("/health/live"), what="a request without a client certificate"
    )


def test_a_certificate_from_another_ca_is_refused(t022_stack, tmp_path) -> None:
    other_ca = make_ca(tmp_path / "other-ca", "Orchara unrelated CA")
    other_client = make_client_certificate(
        other_ca, tmp_path / "other-client", "maintenance"
    )
    client = f00b.InternalClient(
        t022_stack.certificates.internal_url, t022_stack.certificates.ca.cert, other_client
    )
    f00b.assert_handshake_refused(
        lambda: client.get("/health/live"), what="a certificate from an unrelated CA"
    )


def test_the_header_must_match_the_certificate(t022_stack) -> None:
    client = t022_stack.internal_client("maintenance")
    mismatch = client.claim(header_role="worker")
    assert mismatch.status == 403, mismatch.text
    assert mismatch.json["error"]["code"] == "forbidden"

    missing = client.claim(header_role=None)
    assert missing.status == 403, missing.text
    assert missing.json["error"]["code"] == "forbidden"

    unknown_header = client.claim(header_role="intruder")
    assert unknown_header.status == 403, unknown_header.text


def test_a_certificate_with_an_unknown_organization_is_refused(t022_stack, tmp_path) -> None:
    intruder = make_client_certificate(
        t022_stack.certificates.ca, tmp_path / "intruder", "intruder"
    )
    client = f00b.InternalClient(
        t022_stack.certificates.internal_url, t022_stack.certificates.ca.cert, intruder
    )
    response = client.claim(header_role="intruder")
    assert response.status == 403, response.text
    assert response.json["error"]["code"] == "forbidden"


def test_a_worker_or_supervisor_certificate_cannot_claim_work(t022_stack) -> None:
    for role in ("worker", "supervisor"):
        client = t022_stack.internal_client(role)
        claim = client.claim()
        assert claim.status == 403, (role, claim.text)
        assert claim.json["error"]["code"] == "forbidden"
        heartbeat = client.heartbeat(f00b.new_uuid(), f00b.new_uuid())
        assert heartbeat.status == 403, (role, heartbeat.text)
        result = client.result(
            f00b.new_uuid(), attempt_token=f00b.new_uuid(), state="succeeded"
        )
        assert result.status == 403, (role, result.text)


def test_the_maintenance_certificate_with_a_matching_header_is_accepted(t022_stack) -> None:
    client = t022_stack.internal_client("maintenance")
    response = client.claim()
    assert response.status == 204, response.text
    health = client.get("/health/live")
    assert health.status == 200


def test_the_internal_paths_are_not_on_the_public_listener(t022_stack) -> None:
    public = t022_stack.client()
    for method, path, body in (
        ("POST", "/internal/v1/background/jobs/claim", {
            "worker_id": "x", "supported_kinds": ["artifact_finalize"],
        }),
        ("POST", "/internal/v1/background/jobs/" + f00b.new_uuid() + "/heartbeat", {
            "attempt_token": f00b.new_uuid(),
        }),
    ):
        response = public.request(method, path, body=body, headers={"X-Internal-Role": "maintenance"})
        assert response.status == 404, response.text


def test_the_api_refuses_to_start_with_half_configured_or_unreadable_mtls(
    t022_stack, tmp_path
) -> None:
    api = t022_stack.binaries["api"]

    def start(extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
        env = st.admin_env(t022_stack.dsn)
        env["ORCHARA_HTTP_ADDR"] = f"127.0.0.1:{st.free_port()}"
        env["ORCHARA_WEB_DIR"] = str(st.WEB_DIR)
        env.update(extra)
        return subprocess.run(
            [str(api)], cwd=f00b.PRODUCT_ROOT, env=env,
            capture_output=True, text=True, timeout=60,
            stdin=subprocess.DEVNULL,
        )

    # An address without its certificate material is a configuration rejection.
    incomplete = start({
        "ORCHARA_INTERNAL_ADDR": f"127.0.0.1:{st.free_port()}",
        "ORCHARA_INTERNAL_SERVER_CERT": str(tmp_path / "missing.pem"),
    })
    assert incomplete.returncode != 0
    combined = incomplete.stdout + incomplete.stderr
    assert "configuration rejected" in combined, combined
    assert "ORCHARA_INTERNAL_SERVER_KEY" in combined, combined

    # Complete configuration with unreadable files is refused before either
    # listener starts serving.
    unreadable = start({
        "ORCHARA_INTERNAL_ADDR": f"127.0.0.1:{st.free_port()}",
        "ORCHARA_INTERNAL_SERVER_CERT": str(tmp_path / "no-cert.pem"),
        "ORCHARA_INTERNAL_SERVER_KEY": str(tmp_path / "no-key.pem"),
        "ORCHARA_INTERNAL_CLIENT_CA": str(tmp_path / "no-ca.pem"),
    })
    assert unreadable.returncode != 0
    combined = unreadable.stdout + unreadable.stderr
    assert "internal listener rejected" in combined, combined
