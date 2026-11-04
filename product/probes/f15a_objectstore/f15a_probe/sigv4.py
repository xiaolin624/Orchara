"""AWS Signature Version 4 primitives for the S3 compatibility probe.

The probe must speak the same authentication protocol a real S3 deployment
enforces, otherwise ``anonymous_read_denied`` and the presigned-URL checks would
be measuring our own stub instead of the candidate. The implementation follows
the S3 API reference:

* canonical URI: the path is already percent-encoded (the client builds it from
  object keys); S3 does **not** normalise ``.``/``..`` segments, so neither do we;
* canonical query: encoded name/value pairs sorted by encoded name then value;
* canonical headers: lowercased names, values space-collapsed and trimmed,
  sorted by name; the signed-header list is derived from the same map;
* string to sign: ``AWS4-HMAC-SHA256`` + timestamp + scope + SHA-256 of the
  canonical request;
* signing key: HMAC chain ``AWS4<secret>`` -> date -> region -> service.

Correctness is pinned to AWS's published test-suite vectors in
``tests/f15a/test_sigv4_vectors.py``; this module is not its own oracle.

Presigned URLs use ``UNSIGNED-PAYLOAD`` because an S3 client cannot hash the body
of a URL it hands to someone else. The signature covers method, path, query and
the host header, which is why a URL minted for key A cannot fetch key B.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence
from urllib.parse import urlsplit

ALGORITHM = "AWS4-HMAC-SHA256"
UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"
_UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def uri_encode(value: str, *, encode_slash: bool = True) -> str:
    """Percent-encode per SigV4: only ``A-Z a-z 0-9 - _ . ~`` stay literal."""
    safe = _UNRESERVED if encode_slash else _UNRESERVED + "/"
    pieces: list[str] = []
    for raw in value.encode("utf-8"):
        char = chr(raw)
        if char in safe:
            pieces.append(char)
        else:
            pieces.append(f"%{raw:02X}")
    return "".join(pieces)


def canonical_uri(path: str) -> str:
    """Encode a path for signing, preserving ``/`` and *never* collapsing ``..``."""
    if not path.startswith("/"):
        path = "/" + path
    return uri_encode(path, encode_slash=False)


def canonical_query(pairs: Iterable[tuple[str, str]] | Mapping[str, str]) -> str:
    items = list(pairs.items()) if isinstance(pairs, Mapping) else list(pairs)
    encoded = [(uri_encode(str(name)), uri_encode(str(value))) for name, value in items]
    encoded.sort()
    return "&".join(f"{name}={value}" for name, value in encoded)


def canonical_headers(headers: Mapping[str, str]) -> tuple[str, str]:
    normalised: dict[str, str] = {}
    for name, value in headers.items():
        key = name.strip().lower()
        if not key:
            continue
        normalised[key] = " ".join(str(value).split())
    names = sorted(normalised)
    block = "".join(f"{name}:{normalised[name]}\n" for name in names)
    return block, ";".join(names)


def canonical_request(
    *,
    method: str,
    path: str,
    query: str,
    headers: Mapping[str, str],
    payload_hash: str,
) -> tuple[str, str]:
    """Return ``(canonical_request, signed_headers)``."""
    block, signed = canonical_headers(headers)
    request = "\n".join([method.upper(), path, query, block, signed, payload_hash])
    return request, signed


def credential_scope(amz_date: str, region: str, service: str) -> str:
    return f"{amz_date[:8]}/{region}/{service}/aws4_request"


def string_to_sign(amz_date: str, scope: str, request: str) -> str:
    return "\n".join([ALGORITHM, amz_date, scope, sha256_hex(request.encode("utf-8"))])


def signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    key = ("AWS4" + secret_key).encode("utf-8")
    for part in (date_stamp, region, service, "aws4_request"):
        key = hmac.new(key, part.encode("utf-8"), hashlib.sha256).digest()
    return key


def signature(secret_key: str, date_stamp: str, region: str, service: str, to_sign: str) -> str:
    mac = hmac.new(
        signing_key(secret_key, date_stamp, region, service),
        to_sign.encode("utf-8"),
        hashlib.sha256,
    )
    return mac.hexdigest()


@dataclass(frozen=True)
class HeaderSignature:
    """Everything one header-signed request needs, plus the intermediates."""

    headers: dict[str, str]
    signed_headers: str
    canonical_request: str
    string_to_sign: str
    signature: str

    def request_headers(self) -> dict[str, str]:
        return dict(self.headers)


def sign_headers(
    *,
    method: str,
    host: str,
    path: str,
    query_pairs: Sequence[tuple[str, str]] = (),
    extra_headers: Mapping[str, str] | None = None,
    payload: bytes = b"",
    access_key: str,
    secret_key: str,
    region: str,
    service: str = "s3",
    amz_date: str,
    session_token: str | None = None,
) -> HeaderSignature:
    """Sign one request with SigV4 header authentication.

    ``path`` must already be in canonical (percent-encoded) form; the caller
    builds it from the object key so the URL and the signature name the same
    bytes. The payload hash always covers the real body (never a placeholder).
    """
    payload_hash = sha256_hex(payload)
    headers: dict[str, str] = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    for name, value in (extra_headers or {}).items():
        headers[name.strip().lower()] = value
    if session_token:
        headers["x-amz-security-token"] = session_token

    query = canonical_query(query_pairs)
    request, signed_headers = canonical_request(
        method=method, path=path, query=query, headers=headers, payload_hash=payload_hash
    )
    scope = credential_scope(amz_date, region, service)
    to_sign = string_to_sign(amz_date, scope, request)
    signed = signature(secret_key, amz_date[:8], region, service, to_sign)
    authorization = (
        f"{ALGORITHM} Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signed}"
    )
    send = dict(headers)
    send["authorization"] = authorization
    return HeaderSignature(
        headers=send,
        signed_headers=signed_headers,
        canonical_request=request,
        string_to_sign=to_sign,
        signature=signed,
    )


def presign_url(
    *,
    scheme: str,
    host: str,
    path: str,
    access_key: str,
    secret_key: str,
    region: str,
    amz_date: str,
    expires: int,
    service: str = "s3",
    session_token: str | None = None,
) -> str:
    """Mint a query-signed GET URL valid for ``expires`` seconds from ``amz_date``."""
    if expires <= 0:
        raise ValueError("presigned URL expiry must be positive")
    scope = credential_scope(amz_date, region, service)
    params: list[tuple[str, str]] = [
        ("X-Amz-Algorithm", ALGORITHM),
        ("X-Amz-Credential", f"{access_key}/{scope}"),
        ("X-Amz-Date", amz_date),
        ("X-Amz-Expires", str(int(expires))),
        ("X-Amz-SignedHeaders", "host"),
    ]
    if session_token:
        params.append(("X-Amz-Security-Token", session_token))
    query = canonical_query(params)

    request, _ = canonical_request(
        method="GET",
        path=path,
        query=query,
        headers={"host": host},
        payload_hash=UNSIGNED_PAYLOAD,
    )
    to_sign = string_to_sign(amz_date, scope, request)
    signed = signature(secret_key, amz_date[:8], region, service, to_sign)
    return f"{scheme}://{host}{path}?{query}&X-Amz-Signature={signed}"


def url_host(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return host
