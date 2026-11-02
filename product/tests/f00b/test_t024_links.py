"""T024 acceptance: the signed object-link surface and its refusals.

The token is the whole authority, so every failure mode must be a 404 that does
not reveal whether the object exists: expired, tampered, signed with another key,
the wrong operation for the route, and a PUT aimed at a sealed key. Oversized
bodies are 413 (a size refusal is not an existence leak). With no signing key the
API still starts, minting fails 503 object_signing_unavailable, and presented
links are refused.
"""

from __future__ import annotations

import time
import urllib.parse
from pathlib import Path

from support import f00b, t024
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    t024_stack,
)


def claim_job(stack: t024.T024Stack, *, candidate: dict | None = None, fixture_content: bytes | None = None):
    owner = t024.owner_id(stack.dbname)
    candidate = candidate or t024.seed_candidate(stack.dbname, stack.object_dir, owner)
    fixture = t024.seed_fixture(stack.dbname, stack.object_dir, owner, content=fixture_content)
    payload = f00b.enqueue_ok(
        stack,
        kind=t024.KIND_MODEL_VALIDATE,
        target_id=candidate["id"],
        owner_id=owner,
        dedup_key="t024-links:" + fixture.id,
        input_artifact_id=fixture.id,
    )
    return payload, candidate, fixture, owner


def link_url(server: st.ApiServer, signed_url: str) -> str:
    parsed = urllib.parse.urlsplit(signed_url)
    return server.base_url + parsed.path


def assert_link_refused(response: t024.LinkResponse, *, what: str) -> None:
    """Every invalid link is the platform's 404 vocabulary, not a private code.

    The status and the error code are both pinned: a refactor that drops the
    envelope (or invents a second 404 code) must fail here, not merely a test that
    checks "not 200".
    """
    assert response.status == 404, (what, response.status, response.raw)
    body = response.json
    assert isinstance(body, dict) and "error" in body, (what, response.raw)
    error = body["error"]
    assert error["code"] == "resource_not_found", (what, error)
    assert error["message"], error


def test_an_expired_link_is_refused(t024_stack, tmp_path: Path, api_server) -> None:
    server, certificates = t024.start_second_api(
        api_server, t024_stack, tmp_path / "certs-expiring", signing_key=t024_stack.signing_key, link_ttl="1s"
    )
    client = t024_stack.internal_client(certificates=certificates)
    payload, candidate, fixture, owner = claim_job(t024_stack)

    lease_response = client.claim(
        worker_id="t024-expiry", kinds=[t024.KIND_MODEL_VALIDATE]
    )
    assert lease_response.status == 200, lease_response.text
    lease = lease_response.json
    assert lease["job_id"] == payload["id"]

    ticket_response = client.upload(lease["job_id"], lease["attempt_token"], size_bytes=8)
    assert ticket_response.status == 201, ticket_response.text
    ticket = ticket_response.json
    assert ticket["upload_url"].startswith(server.base_url), "the link must be minted by the second API"

    # The lease's input_url was minted with the same short TTL.
    input_url = lease["input_url"]
    assert input_url and input_url.startswith(server.base_url), lease

    time.sleep(1.4)
    expired_put = t024.put_link(ticket["upload_url"], b"expired!")
    assert_link_refused(expired_put, what="an expired PUT link")
    expired_get = t024.get_link(input_url)
    assert_link_refused(expired_get, what="an expired GET link")


def test_tampered_swapped_and_sealed_puts_are_refused(t024_stack) -> None:
    job = t024.prepare_model_validate_job(t024_stack)
    ticket = t024_stack.internal_client().upload(
        job.job_id, job.attempt_token, size_bytes=len(CONTENT)
    ).json

    tampered = t024.put_link(t024.tamper_token(ticket["upload_url"]), t024.fixture_bytes())
    assert_link_refused(tampered, what="a tampered token")

    swapped = t024.get_link(ticket["upload_url"])
    assert_link_refused(swapped, what="a PUT token used for GET")

    assert t024.put_link(ticket["upload_url"], CONTENT).status == 204
    sha = t024.sha256_hex(CONTENT)
    assert t024_stack.internal_client().complete(
        job.job_id, ticket["artifact_id"], job.attempt_token, sha
    ).status == 202
    _done, reports = f00b.maintenance_once(t024_stack, "--kinds", t024.KIND_ARTIFACT_FINALIZE)
    assert f00b.job_reports(reports)[0]["result"] == "succeeded"
    download = t024_stack.internal_client().download(
        job.job_id, job.attempt_token, ticket["artifact_id"]
    )
    assert download.status == 200, download.text

    # A GET token (sealed key) can never be used as a PUT.
    sealed_put = t024.put_link(download.json["url"], CONTENT)
    assert_link_refused(sealed_put, what="a sealed GET token used for PUT")


def test_a_link_signed_with_another_key_is_refused(t024_stack, tmp_path: Path, api_server) -> None:
    other_key = t024.new_signing_key()
    server, _certificates = t024.start_second_api(
        api_server, t024_stack, tmp_path / "certs-other-key", signing_key=other_key
    )
    job = t024.prepare_model_validate_job(t024_stack)
    ticket = t024_stack.internal_client().upload(
        job.job_id, job.attempt_token, size_bytes=8
    ).json
    foreign = link_url(server, ticket["upload_url"])
    refused = t024.put_link(foreign, b"eight by")
    assert_link_refused(refused, what="a token signed with another key")


def test_an_oversized_upload_is_413_and_leaves_no_object(t024_stack) -> None:
    job = t024.prepare_model_validate_job(t024_stack)
    ticket = t024_stack.internal_client().upload(
        job.job_id, job.attempt_token, size_bytes=8
    ).json
    oversized = t024.put_link(ticket["upload_url"], b"one byte too many")
    assert oversized.status == 413, (oversized.status, oversized.raw)
    assert oversized.json["error"]["code"] == "payload_too_large", oversized.json

    row = f00b.artifact_row(t024_stack.dbname, ticket["artifact_id"])
    staging_path = t024_stack.object_dir / row["staging_object_key"]
    assert not staging_path.exists(), "an oversized write must not leave a partial object"
    assert row["state"] == "uploading"


def test_without_a_signing_key_the_api_starts_and_refuses_links(
    t024_stack, tmp_path: Path, api_server
) -> None:
    server, certificates = t024.start_second_api(
        api_server, t024_stack, tmp_path / "certs-no-signing", signing_key=None
    )
    # The process is up and serves ordinary routes.
    live = st.HttpClient(server.base_url).get("/health/live")
    assert live.status == 200, live.text

    payload, candidate, fixture, owner = claim_job(t024_stack)
    client = t024_stack.internal_client(certificates=certificates)
    lease = client.claim(worker_id="t024-no-signer", kinds=[t024.KIND_MODEL_VALIDATE]).json
    assert lease["job_id"] == payload["id"]
    assert lease["input_url"] is None, "an unsigned server must not invent a link"

    refused = client.upload(lease["job_id"], lease["attempt_token"], size_bytes=8)
    assert refused.status == 503, refused.text
    assert refused.json["error"]["code"] == "object_signing_unavailable"

    # A link minted by the configured API is refused by the unsigned one.
    job = t024.prepare_model_validate_job(t024_stack, dedup_key="t024-no-signer-second")
    ticket = t024_stack.internal_client().upload(job.job_id, job.attempt_token, size_bytes=8).json
    foreign_link = t024.put_link(link_url(server, ticket["upload_url"]), b"eight by")
    assert_link_refused(foreign_link, what="a link presented to the unsigned API")


CONTENT = b"sealed report bytes\n"
