"""The ten assertions that decide whether a candidate S3-compatible store is usable.

Each assertion returns a stable id, a machine verdict (``passed`` / ``failed`` /
``blocked`` / ``not_run``), a reason and redacted evidence. Verdicts are only
``passed`` when every check ran against the live endpoint and held; a missing
prerequisite is ``blocked`` (and the process exits 2), never a silent pass.

Nothing here writes a credential, an object body or a presigned URL's signature
into the evidence. Sizes, SHA-256 digests, HTTP status codes and probe-generated
key names are safe to record.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from .config import ProbeEnvironmentError, Target
from .httpclient import Response, S3Client, object_path

#: Mirrors internal/objectstore.MaxObjectBytes and contracts/openapi.json
#: size_bytes.maximum (20971520). Pinned by tests/f15a/test_boundary_constants.py.
MAX_OBJECT_BYTES = 20 << 20

PASSED = "passed"
FAILED = "failed"
BLOCKED = "blocked"
NOT_RUN = "not_run"

DENIED_STATUSES = {400, 401, 403}


def _denied(response: Response) -> bool:
    return response.status in DENIED_STATUSES


def _not_found(response: Response) -> bool:
    return response.status == 404


def _ok(response: Response) -> bool:
    return 200 <= response.status < 300


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _body(tag: str, size: int) -> bytes:
    seed = hashlib.sha256(tag.encode("utf-8")).digest()
    return (seed * ((size // len(seed)) + 1))[:size]


@dataclass
class Check:
    name: str
    ok: bool
    detail: dict

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class AssertionResult:
    id: str
    title: str
    status: str
    reason: str
    checks: list[Check] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    duration_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "reason": self.reason,
            "checks": [check.as_dict() for check in self.checks],
            "evidence": self.evidence,
            "duration_ms": self.duration_ms,
        }

    @property
    def failed(self) -> bool:
        return self.status == FAILED

    @property
    def blocked(self) -> bool:
        return self.status == BLOCKED


@dataclass
class ProbeContext:
    target: Target
    client: S3Client
    run_id: str
    created_keys: list[tuple[str, str]] = field(default_factory=list)
    bodies: dict[str, bytes] = field(default_factory=dict)

    def body(self, tag: str, size: int) -> bytes:
        if tag not in self.bodies:
            self.bodies[tag] = _body(f"{self.run_id}:{tag}", size)
        return self.bodies[tag]

    def pair(self) -> tuple[str, str]:
        name = secrets.token_hex(32)
        staging = f"{self.target.tenant_prefix}/staging/{name}"
        sealed = f"{self.target.tenant_prefix}/sealed/{name}"
        self.created_keys.extend([("staging", staging), ("sealed", sealed)])
        return staging, sealed

    def staging_key(self) -> str:
        staging, _ = self.pair()
        return staging

    def sealed_key(self) -> str:
        _, sealed = self.pair()
        return sealed

    def require_provisioning(self) -> None:
        if not self.target.provision_configured:
            raise _Blocked(
                "provisioning credentials are not configured; the probe needs them to "
                "seed sealed objects and to verify boundaries"
            )

    def seed_sealed(self, key: str, body: bytes) -> Response:
        self.require_provisioning()
        response = self.client.request("PUT", key, body=body, credential="provision")
        if not _ok(response):
            raise ProbeEnvironmentError(
                "provisioning_setup_failed",
                f"provisioning credential could not write {key}: HTTP {response.status}",
            )
        return response


class _Blocked(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _result(
    assertion_id: str,
    title: str,
    started: float,
    checks: list[Check],
    evidence: dict,
    *,
    blocked_reason: str | None = None,
    not_run_reason: str | None = None,
) -> AssertionResult:
    duration_ms = int((time.monotonic() - started) * 1000)
    if blocked_reason is not None:
        status, reason = BLOCKED, blocked_reason
    elif not_run_reason is not None:
        status, reason = NOT_RUN, not_run_reason
    else:
        failed = [check.name for check in checks if not check.ok]
        if failed:
            status = FAILED
            reason = "failed checks: " + ", ".join(failed)
        else:
            status = PASSED
            reason = "all checks held"
    return AssertionResult(
        id=assertion_id,
        title=title,
        status=status,
        reason=reason,
        checks=checks,
        evidence=evidence,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# 1-3: anonymous access
# ---------------------------------------------------------------------------
def anonymous_read_denied(ctx: ProbeContext) -> AssertionResult:
    started = time.monotonic()
    title = "anonymous GET of a sealed key is denied and never returns the body"
    try:
        ctx.require_provisioning()
        _, sealed = ctx.pair()
        body = ctx.body("anonymous-read", 96)
        ctx.seed_sealed(sealed, body)
        response = ctx.client.request("GET", sealed, anonymous=True)
        checks = [
            Check(
                "denied_with_401_or_403",
                response.status in (401, 403),
                {"status": response.status},
            ),
            Check(
                "no_object_body",
                response.body_sha256() != _sha(body) and response.body != body,
                {
                    "response_body_sha256": response.body_sha256(),
                    "object_sha256": _sha(body),
                    "response_body_bytes": len(response.body),
                },
            ),
            Check(
                "not_a_listing",
                not response.body.startswith(b"<ListBucketResult"),
                {"body_is_listing": response.body.startswith(b"<ListBucketResult")},
            ),
        ]
        evidence = {
            "sealed_key": sealed,
            "http_status": response.status,
            "response_body_sha256": response.body_sha256(),
            "response_body_bytes": len(response.body),
            "object_sha256": _sha(body),
        }
        return _result("anonymous_read_denied", title, started, checks, evidence)
    except _Blocked as exc:
        return _result("anonymous_read_denied", title, started, [], {}, blocked_reason=exc.reason)


def anonymous_put_denied(ctx: ProbeContext) -> AssertionResult:
    started = time.monotonic()
    title = "unsigned PUT is denied and creates no staging object"
    staging = ctx.staging_key()
    body = ctx.body("anonymous-put", 64)
    response = ctx.client.request("PUT", staging, body=body, anonymous=True)
    after = ctx.client.request("GET", staging, credential="runtime")
    checks = [
        Check(
            "denied_with_401_or_403",
            response.status in (401, 403),
            {"status": response.status},
        ),
        Check(
            "object_was_not_created",
            after.status == 404,
            {"signed_get_status_after_anonymous_put": after.status},
        ),
    ]
    return _result(
        "anonymous_put_denied",
        title,
        started,
        checks,
        {
            "staging_key": staging,
            "http_status": response.status,
            "follow_up_signed_get_status": after.status,
        },
    )


def anonymous_list_denied(ctx: ProbeContext) -> AssertionResult:
    started = time.monotonic()
    title = "unsigned bucket listing is denied and returns no key list"
    response = ctx.client.request(
        "GET", None, query_pairs=[("list-type", "2")], anonymous=True
    )
    checks = [
        Check(
            "denied_with_401_or_403",
            response.status in (401, 403),
            {"status": response.status},
        ),
        Check(
            "no_listing_body",
            b"<ListBucketResult" not in response.body,
            {"body_bytes": len(response.body)},
        ),
    ]
    return _result(
        "anonymous_list_denied",
        title,
        started,
        checks,
        {"http_status": response.status, "body_bytes": len(response.body)},
    )


# ---------------------------------------------------------------------------
# 4: key-path boundary
# ---------------------------------------------------------------------------
def out_of_range_key_denied(ctx: ProbeContext) -> AssertionResult:
    started = time.monotonic()
    title = "keys outside the tenant prefix, traversal and cross-key URLs are contained"
    try:
        ctx.require_provisioning()
        target = ctx.target
        tenant = target.tenant_prefix
        checks: list[Check] = []
        evidence: dict = {"tenant_prefix": tenant, "cases": {}}

        name = secrets.token_hex(32)
        outside_key = f"not-orchara/staging/{name}"
        ctx.created_keys.append(("outside", outside_key))
        outside = ctx.client.request(
            "PUT", outside_key, body=b"outside", credential="runtime"
        )
        outside_after = ctx.client.request("GET", outside_key, credential="provision")
        checks.append(
            Check(
                "outside_prefix_put_denied",
                outside.status in (401, 403),
                {"status": outside.status},
            )
        )
        checks.append(
            Check(
                "outside_prefix_not_created",
                outside_after.status == 404,
                {"provisioning_get_status": outside_after.status},
            )
        )
        evidence["cases"]["outside_prefix"] = {
            "put_status": outside.status,
            "object_after": outside_after.status,
        }

        sibling_key = f"orchara-other-tenant/staging/{name}"
        ctx.created_keys.append(("sibling", sibling_key))
        sibling = ctx.client.request(
            "PUT", sibling_key, body=b"sibling", credential="runtime"
        )
        sibling_after = ctx.client.request("GET", sibling_key, credential="provision")
        checks.append(
            Check(
                "sibling_prefix_put_denied",
                sibling.status in (401, 403),
                {"status": sibling.status},
            )
        )
        checks.append(
            Check(
                "sibling_prefix_not_created",
                sibling_after.status == 404,
                {"provisioning_get_status": sibling_after.status},
            )
        )
        evidence["cases"]["sibling_prefix"] = {
            "put_status": sibling.status,
            "object_after": sibling_after.status,
        }

        traversal_key = f"{tenant}/staging/../sealed/{name}"
        ctx.created_keys.append(("traversal", traversal_key))
        traversal = ctx.client.request(
            "PUT", traversal_key, body=b"traversal", credential="runtime"
        )
        sealed_target = f"{tenant}/sealed/{name}"
        ctx.created_keys.append(("sealed", sealed_target))
        sealed_after = ctx.client.request("GET", sealed_target, credential="provision")
        checks.append(
            Check(
                "traversal_cannot_write_the_sealed_namespace",
                sealed_after.status == 404,
                {
                    "traversal_put_status": traversal.status,
                    "sealed_target_get_status": sealed_after.status,
                    "note": (
                        "a literal-key store may answer 2xx and keep the literal key; "
                        "only escape into the sealed key is a violation"
                    ),
                },
            )
        )
        evidence["cases"]["traversal"] = {
            "put_status": traversal.status,
            "sealed_target_get_status": sealed_after.status,
        }

        absolute_key = f"/not-orchara/sealed/{name}"
        ctx.created_keys.append(("absolute", absolute_key))
        absolute = ctx.client.request(
            "PUT", absolute_key, body=b"absolute", credential="runtime"
        )
        absolute_after = ctx.client.request("GET", absolute_key, credential="provision")
        checks.append(
            Check(
                "absolute_key_put_denied",
                absolute.status in (401, 403),
                {"status": absolute.status},
            )
        )
        checks.append(
            Check(
                "absolute_key_not_created",
                absolute_after.status == 404,
                {"provisioning_get_status": absolute_after.status},
            )
        )
        evidence["cases"]["absolute"] = {
            "put_status": absolute.status,
            "object_after": absolute_after.status,
        }

        _, sealed_a = ctx.pair()
        _, sealed_b = ctx.pair()
        body_a = ctx.body("cross-key-a", 64)
        body_b = ctx.body("cross-key-b", 64)
        ctx.seed_sealed(sealed_a, body_a)
        ctx.seed_sealed(sealed_b, body_b)
        signed_url = ctx.client.presign_get(sealed_a, expires=300, credential="runtime")
        parts = urlsplit(signed_url)
        tampered = urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                object_path(target.bucket, sealed_b, target.base_path),
                parts.query,
                "",
            )
        )
        crossed = ctx.client.get_url(tampered)
        checks.append(
            Check(
                "signed_url_for_one_key_cannot_fetch_another",
                crossed.status in (400, 401, 403)
                and crossed.body_sha256() != _sha(body_b),
                {
                    "http_status": crossed.status,
                    "returned_other_objects_body": crossed.body_sha256() == _sha(body_b),
                },
            )
        )
        evidence["cases"]["cross_key_signed_url"] = {
            "http_status": crossed.status,
            "other_key_body_returned": crossed.body_sha256() == _sha(body_b),
        }
        return _result("out_of_range_key_denied", title, started, checks, evidence)
    except _Blocked as exc:
        return _result("out_of_range_key_denied", title, started, [], {}, blocked_reason=exc.reason)


# ---------------------------------------------------------------------------
# 5-6: sealed write boundary and staging/sealed separation
# ---------------------------------------------------------------------------
def sealed_put_denied(ctx: ProbeContext) -> AssertionResult:
    started = time.monotonic()
    title = "the runtime credential cannot PUT into the sealed prefix"
    try:
        ctx.require_provisioning()
        _, sealed = ctx.pair()
        body = ctx.body("sealed-put", 80)
        response = ctx.client.request("PUT", sealed, body=body, credential="runtime")
        after = ctx.client.request("GET", sealed, credential="provision")
        checks = [
            Check(
                "runtime_put_denied",
                response.status in (401, 403),
                {"status": response.status},
            ),
            Check(
                "sealed_object_was_not_created",
                after.status == 404,
                {"provisioning_get_status": after.status},
            ),
        ]
        return _result(
            "sealed_put_denied",
            title,
            started,
            checks,
            {
                "sealed_key": sealed,
                "http_status": response.status,
                "object_after": after.status,
            },
        )
    except _Blocked as exc:
        return _result("sealed_put_denied", title, started, [], {}, blocked_reason=exc.reason)


def staging_overwrite_preserves_sealed(ctx: ProbeContext) -> AssertionResult:
    started = time.monotonic()
    title = "overwriting a staging key with different bytes cannot change the sealed object"
    try:
        ctx.require_provisioning()
        staging, sealed = ctx.pair()  # same object name on purpose: a prefix bug collides
        sealed_body = ctx.body("sealed-stable", 128)
        first = ctx.body("staging-first", 80)
        second = ctx.body("staging-second", 144)
        ctx.seed_sealed(sealed, sealed_body)
        sealed_before = ctx.client.request("GET", sealed, credential="provision")
        put_one = ctx.client.request("PUT", staging, body=first, credential="runtime")
        put_two = ctx.client.request("PUT", staging, body=second, credential="runtime")
        sealed_after = ctx.client.request("GET", sealed, credential="provision")
        staging_after = ctx.client.request("GET", staging, credential="runtime")
        checks = [
            Check(
                "sealed_seeded_and_readable",
                _ok(sealed_before) and sealed_before.body_sha256() == _sha(sealed_body),
                {"status": sealed_before.status},
            ),
            Check(
                "staging_overwrite_accepted",
                _ok(put_one) and _ok(put_two),
                {"first_put_status": put_one.status, "second_put_status": put_two.status},
            ),
            Check(
                "sealed_bytes_unchanged",
                sealed_after.body_sha256() == _sha(sealed_body),
                {
                    "sealed_sha256_before": sealed_before.body_sha256(),
                    "sealed_sha256_after": sealed_after.body_sha256(),
                },
            ),
            Check(
                "staging_holds_the_latest_bytes",
                _ok(staging_after) and staging_after.body_sha256() == _sha(second),
                {
                    "staging_get_status": staging_after.status,
                    "staging_sha256": staging_after.body_sha256(),
                },
            ),
        ]
        return _result(
            "staging_overwrite_preserves_sealed",
            title,
            started,
            checks,
            {
                "staging_key": staging,
                "sealed_key": sealed,
                "sealed_sha256": _sha(sealed_body),
                "staging_first_sha256": _sha(first),
                "staging_second_sha256": _sha(second),
                "sealed_get_status": sealed_after.status,
                "staging_get_status": staging_after.status,
            },
        )
    except _Blocked as exc:
        return _result(
            "staging_overwrite_preserves_sealed", title, started, [], {}, blocked_reason=exc.reason
        )


# ---------------------------------------------------------------------------
# 7: short-lived signature
# ---------------------------------------------------------------------------
def short_lived_signature_expiry(ctx: ProbeContext) -> AssertionResult:
    started = time.monotonic()
    title = "a presigned GET works within its TTL and is rejected after it expires"
    try:
        ctx.require_provisioning()
        _, sealed = ctx.pair()
        body = ctx.body("short-ttl", 96)
        ctx.seed_sealed(sealed, body)
        url = ctx.client.presign_get(
            sealed, expires=ctx.target.short_ttl, credential="runtime"
        )
        before = ctx.client.get_url(url)
        waited = ctx.target.short_ttl + ctx.target.expiry_skew + 1.25
        time.sleep(waited)
        after = ctx.client.get_url(url)
        checks = [
            Check(
                "accepted_before_expiry",
                before.status == 200 and before.body_sha256() == _sha(body),
                {"status": before.status, "body_sha256": before.body_sha256()},
            ),
            Check(
                "rejected_after_expiry",
                after.status in (400, 401, 403),
                {"status": after.status},
            ),
            Check(
                "no_body_after_expiry",
                after.body_sha256() != _sha(body),
                {"body_sha256": after.body_sha256()},
            ),
        ]
        return _result(
            "short_lived_signature_expiry",
            title,
            started,
            checks,
            {
                "sealed_key": sealed,
                "ttl_seconds": ctx.target.short_ttl,
                "clock_skew_allowance_seconds": ctx.target.expiry_skew,
                "waited_seconds": round(waited, 3),
                "before_status": before.status,
                "after_status": after.status,
            },
        )
    except _Blocked as exc:
        return _result(
            "short_lived_signature_expiry", title, started, [], {}, blocked_reason=exc.reason
        )


# ---------------------------------------------------------------------------
# 8: proxy download boundary
# ---------------------------------------------------------------------------
def product_download_decision(content_length: int | None, product_cap: int) -> dict:
    """Mirror the platform's proxy-download limit decision (no streaming over cap)."""
    if content_length is None:
        return {"decidable": False, "allowed": False, "reason": "Content-Length is missing"}
    if content_length > product_cap:
        return {
            "decidable": True,
            "allowed": False,
            "reason": f"object is {content_length} bytes, over the {product_cap} byte cap",
        }
    return {"decidable": True, "allowed": True, "reason": "within the product cap"}


def proxy_download_boundary(ctx: ProbeContext) -> AssertionResult:
    started = time.monotonic()
    title = "size/Content-Length and Range behaviour are reproducible for a proxy download"
    try:
        ctx.require_provisioning()
        _, big = ctx.pair()
        size = MAX_OBJECT_BYTES + 1024
        big_body = ctx.body("oversized", size)
        ctx.seed_sealed(big, big_body)
        ctx.created_keys.append(("sealed", big))
        head = ctx.client.request("HEAD", big, credential="provision")
        content_length = head.content_length()
        decision = product_download_decision(content_length, MAX_OBJECT_BYTES)

        range_response = ctx.client.request(
            "GET",
            big,
            credential="provision",
            extra_headers={"range": "bytes=0-1023"},
            read_limit=4096,
        )
        expected_prefix = big_body[:1024]
        content_range = range_response.header("content-range")
        unsatisfiable = ctx.client.request(
            "GET",
            big,
            credential="provision",
            extra_headers={"range": f"bytes={size}-"},
            read_limit=4096,
        )
        checks = [
            Check(
                "head_reports_content_length",
                content_length is not None,
                {"content_length": content_length, "expected": size},
            ),
            Check(
                "head_content_length_is_exact",
                content_length == size,
                {"content_length": content_length, "expected": size},
            ),
            Check(
                "proxy_refuses_an_oversized_object",
                decision["decidable"] and decision["allowed"] is False,
                {
                    "decision": decision,
                    "product_cap_bytes": MAX_OBJECT_BYTES,
                    "note": "the probe never streams the full oversized body",
                },
            ),
            Check(
                "range_request_returns_206_and_exact_bytes",
                range_response.status == 206
                and range_response.body == expected_prefix
                and content_range == f"bytes 0-1023/{size}",
                {
                    "status": range_response.status,
                    "content_range": content_range,
                    "body_bytes": len(range_response.body),
                },
            ),
            Check(
                "unsatisfiable_range_returns_416",
                unsatisfiable.status == 416
                and unsatisfiable.header("content-range") == f"bytes */{size}",
                {
                    "status": unsatisfiable.status,
                    "content_range": unsatisfiable.header("content-range"),
                },
            ),
        ]

        # Honest observation, not a pass claim: can a leaked pre-expiry URL be
        # revoked while the key must stay servable? S3 has no revocation API;
        # deleting the object is the only way to stop the URL, and the URL stays
        # validly signed for the key. We demonstrate that by deleting and
        # re-materialising the key, then fetching the same URL again.
        _, revocable_key = ctx.pair()
        revocable_body = ctx.body("revocation", 64)
        rewritten_body = ctx.body("revocation-rewritten", 80)
        ctx.seed_sealed(revocable_key, revocable_body)
        revocation_url = ctx.client.presign_get(
            revocable_key, expires=300, credential="runtime"
        )
        delete_response = ctx.client.request(
            "DELETE", revocable_key, credential="provision"
        )
        after_delete = ctx.client.get_url(revocation_url)
        rewrite = ctx.client.request(
            "PUT", revocable_key, body=rewritten_body, credential="provision"
        )
        after_rewrite = ctx.client.get_url(revocation_url)
        still_authorizes_key = (
            rewrite.ok
            and after_rewrite.status == 200
            and after_rewrite.body_sha256() == _sha(rewritten_body)
        )
        evidence = {
            "sealed_key": big,
            "object_bytes": size,
            "product_cap_bytes": MAX_OBJECT_BYTES,
            "head_status": head.status,
            "head_content_length": content_length,
            "proxy_decision": decision,
            "range_status": range_response.status,
            "range_content_range": content_range,
            "unsatisfiable_range_status": unsatisfiable.status,
            "pre_expiry_url_revocation": {
                "delete_status": delete_response.status,
                "get_after_delete_status": after_delete.status,
                "rewrite_status": rewrite.status,
                "get_after_rewrite_status": after_rewrite.status,
                "pre_expiry_url_still_authorizes_key": still_authorizes_key,
                "conclusion": (
                    "a leaked pre-expiry URL cannot be revoked while the key is servable; "
                    "the platform must proxy downloads to enforce immediate revocation"
                    if still_authorizes_key
                    else "the candidate did not re-materialise the key, or it rejected the "
                    "presigned URL after the object was touched; revocation is not claimed"
                ),
            },
        }
        if still_authorizes_key:
            evidence["pre_expiry_url_revocation"]["requires_proxy_download"] = True
        return _result("proxy_download_boundary", title, started, checks, evidence)
    except _Blocked as exc:
        return _result("proxy_download_boundary", title, started, [], {}, blocked_reason=exc.reason)


# ---------------------------------------------------------------------------
# 9: delete semantics
# ---------------------------------------------------------------------------
def delete_semantics(ctx: ProbeContext) -> AssertionResult:
    started = time.monotonic()
    title = "delete needs credentials, is idempotent, yields 404 and spares sibling keys"
    try:
        ctx.require_provisioning()
        _, sealed_a = ctx.pair()
        _, sealed_b = ctx.pair()
        body_a = ctx.body("delete-a", 64)
        body_b = ctx.body("delete-b", 96)
        ctx.seed_sealed(sealed_a, body_a)
        ctx.seed_sealed(sealed_b, body_b)

        runtime_delete = ctx.client.request("DELETE", sealed_a, credential="runtime")
        first = ctx.client.request("DELETE", sealed_a, credential="provision")
        after_first = ctx.client.request("GET", sealed_a, credential="provision")
        second = ctx.client.request("DELETE", sealed_a, credential="provision")
        after_second = ctx.client.request("GET", sealed_a, credential="provision")
        sibling = ctx.client.request("GET", sealed_b, credential="provision")

        checks = [
            Check(
                "runtime_cannot_delete_sealed",
                runtime_delete.status in (401, 403),
                {"status": runtime_delete.status},
            ),
            Check(
                "first_delete_succeeds",
                first.status in (200, 204),
                {"status": first.status},
            ),
            Check(
                "deleted_object_is_404",
                after_first.status == 404,
                {"get_status": after_first.status},
            ),
            Check(
                "repeated_delete_is_idempotent",
                second.status in (200, 204) and after_second.status == 404,
                {"second_status": second.status, "get_status": after_second.status},
            ),
            Check(
                "sibling_sealed_object_is_untouched",
                sibling.status == 200 and sibling.body_sha256() == _sha(body_b),
                {"status": sibling.status, "body_sha256": sibling.body_sha256()},
            ),
        ]
        return _result(
            "delete_semantics",
            title,
            started,
            checks,
            {
                "deleted_key": sealed_a,
                "sibling_key": sealed_b,
                "runtime_delete_status": runtime_delete.status,
                "first_delete_status": first.status,
                "get_after_delete_status": after_first.status,
                "second_delete_status": second.status,
                "sibling_get_status": sibling.status,
                "sibling_sha256": _sha(body_b),
            },
        )
    except _Blocked as exc:
        return _result("delete_semantics", title, started, [], {}, blocked_reason=exc.reason)


# ---------------------------------------------------------------------------
# 10: object versioning (observational)
# ---------------------------------------------------------------------------
def versions_immutable(ctx: ProbeContext) -> AssertionResult:
    started = time.monotonic()
    title = "object versioning, when offered, keeps previous sealed bytes addressable"
    try:
        ctx.require_provisioning()
        response = ctx.client.request(
            "GET", None, query_pairs=[("versioning", "")], credential="provision"
        )
        if not _ok(response):
            return _result(
                "versions_immutable",
                title,
                started,
                [],
                {"versioning_status": response.status},
                not_run_reason=(
                    f"candidate did not expose bucket versioning (HTTP {response.status}); "
                    "version history is not asserted"
                ),
            )
        if b"<Status>Enabled</Status>" not in response.body:
            return _result(
                "versions_immutable",
                title,
                started,
                [],
                {"versioning_status": response.status, "versioning_enabled": False},
                not_run_reason="bucket versioning is not enabled; a candidate without versioning is not rejected for it",
            )
        _, sealed = ctx.pair()
        first_body = ctx.body("version-one", 64)
        second_body = ctx.body("version-two", 96)
        ctx.seed_sealed(sealed, first_body)
        ctx.client.request("PUT", sealed, body=second_body, credential="provision")
        listing = ctx.client.request(
            "GET",
            None,
            query_pairs=[("versions", ""), ("prefix", sealed)],
            credential="provision",
            read_limit=256 * 1024,
        )
        version_ids = re.findall(rb"<VersionId>([^<]+)</VersionId>", listing.body)
        if not version_ids:
            return _result(
                "versions_immutable",
                title,
                started,
                [],
                {"listing_status": listing.status},
                not_run_reason="versioning is enabled but no version ids were listed; behaviour is not asserted",
            )
        versioned = ctx.client.request(
            "GET",
            sealed,
            query_pairs=[("versionId", version_ids[-1].decode("utf-8"))],
            credential="provision",
        )
        checks = [
            Check(
                "old_version_is_addressable",
                versioned.status == 200 and versioned.body_sha256() == _sha(first_body),
                {"status": versioned.status, "body_sha256": versioned.body_sha256()},
            )
        ]
        return _result(
            "versions_immutable",
            title,
            started,
            checks,
            {"sealed_key": sealed, "version_ids_seen": len(version_ids)},
        )
    except _Blocked as exc:
        return _result("versions_immutable", title, started, [], {}, blocked_reason=exc.reason)


ASSERTIONS: tuple[Callable[[ProbeContext], AssertionResult], ...] = (
    anonymous_read_denied,
    anonymous_put_denied,
    anonymous_list_denied,
    out_of_range_key_denied,
    sealed_put_denied,
    staging_overwrite_preserves_sealed,
    short_lived_signature_expiry,
    proxy_download_boundary,
    delete_semantics,
    versions_immutable,
)

ASSERTION_TITLES: dict[str, str] = {
    "anonymous_read_denied": "anonymous GET of a sealed key is denied and never returns the body",
    "anonymous_put_denied": "unsigned PUT is denied and creates no staging object",
    "anonymous_list_denied": "unsigned bucket listing is denied and returns no key list",
    "out_of_range_key_denied": "keys outside the tenant prefix, traversal and cross-key URLs are contained",
    "sealed_put_denied": "the runtime credential cannot PUT into the sealed prefix",
    "staging_overwrite_preserves_sealed": "overwriting a staging key cannot change the sealed object",
    "short_lived_signature_expiry": "a presigned GET works within its TTL and is rejected after expiry",
    "proxy_download_boundary": "size/Content-Length and Range behaviour are reproducible for a proxy download",
    "delete_semantics": "delete needs credentials, is idempotent, yields 404 and spares siblings",
    "versions_immutable": "object versioning, when offered, keeps previous sealed bytes addressable",
}
