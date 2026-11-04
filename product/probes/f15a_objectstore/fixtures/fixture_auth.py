"""Independent SigV4 verifier for the local fixture servers (stdlib only).

This is deliberately a second implementation: the probe's signer is pinned to
AWS's published vectors, and the fixture rebuilds the canonical request from the
raw HTTP request before comparing signatures. A bug in the probe's request
assembly therefore shows up as a 403 from the fixture instead of cancelling out.

The fixtures are **test doubles for discrimination**, never acceptance evidence
for a real S3 candidate.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping
from urllib.parse import parse_qsl

ALGORITHM = "AWS4-HMAC-SHA256"
UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"
CLOCK_SKEW_SECONDS = 900  # AWS's documented request-date tolerance


class AuthError(Exception):
    pass


def encode(value: str, *, keep_slash: bool = False) -> str:
    safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~")
    if keep_slash:
        safe.add("/")
    return "".join(
        chr(byte) if chr(byte) in safe else f"%{byte:02X}" for byte in value.encode("utf-8")
    )


def canonical_query(raw_query: str) -> str:
    pairs = parse_qsl(raw_query, keep_blank_values=True)
    encoded = [(encode(name), encode(value)) for name, value in pairs]
    encoded.sort()
    return "&".join(f"{name}={value}" for name, value in encoded)


def canonical_headers(headers: Mapping[str, str], signed_names: list[str]) -> tuple[str, str]:
    ordered = sorted(name.lower() for name in signed_names)
    lines = []
    for name in ordered:
        raw = headers.get(name, "")
        lines.append(f"{name}:{' '.join(raw.split())}\n")
    return "".join(lines), ";".join(ordered)


def canonical_request(
    method: str,
    path: str,
    query: str,
    headers: Mapping[str, str],
    signed_names: list[str],
    payload_hash: str,
) -> tuple[str, str]:
    block, signed = canonical_headers(headers, signed_names)
    return "\n".join([method.upper(), path, query, block, signed, payload_hash]), signed


def scope(amz_date: str, region: str, service: str) -> str:
    return f"{amz_date[:8]}/{region}/{service}/aws4_request"


def signing_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    key = ("AWS4" + secret).encode("utf-8")
    for part in (date_stamp, region, service, "aws4_request"):
        key = hmac.new(key, part.encode("utf-8"), hashlib.sha256).digest()
    return key


def signature(secret: str, date_stamp: str, region: str, service: str, to_sign: str) -> str:
    return hmac.new(
        signing_key(secret, date_stamp, region, service),
        to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def string_to_sign(amz_date: str, credential_scope: str, request: str) -> str:
    return "\n".join(
        [ALGORITHM, amz_date, credential_scope, hashlib.sha256(request.encode("utf-8")).hexdigest()]
    )


@dataclass(frozen=True)
class VerifiedRequest:
    kind: str  # anonymous | runtime | provisioner
    presigned: bool
    access_key: str | None
    expires_at: float | None


def _parse_amz_date(value: str) -> float:
    try:
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise AuthError("invalid X-Amz-Date") from exc
    return parsed.timestamp()


def classify_access_key(access_key: str, credentials: Mapping[str, tuple[str, str]]) -> str:
    entry = credentials.get(access_key)
    if entry is None:
        raise AuthError("unknown access key")
    return entry[1]


def verify_header_auth(
    *,
    method: str,
    path: str,
    raw_query: str,
    headers: Mapping[str, str],
    body: bytes,
    credentials: Mapping[str, tuple[str, str]],
    now: float,
) -> VerifiedRequest:
    authorization = headers.get("authorization")
    if not authorization:
        return VerifiedRequest("anonymous", False, None, None)
    if not authorization.startswith(ALGORITHM + " "):
        raise AuthError("unsupported authorization algorithm")
    fields: dict[str, str] = {}
    for part in authorization[len(ALGORITHM) + 1 :].split(","):
        name, _, value = part.strip().partition("=")
        fields[name] = value
    try:
        access_key = fields["Credential"].split("/")[0]
        signed_headers = fields["SignedHeaders"].split(";")
        provided = fields["Signature"]
    except KeyError as exc:
        raise AuthError("authorization is missing a required field") from exc
    entry = credentials.get(access_key)
    if entry is None:
        raise AuthError("unknown access key")
    secret, kind = entry

    payload_hash = headers.get("x-amz-content-sha256", "")
    actual_hash = hashlib.sha256(body).hexdigest()
    if payload_hash != actual_hash:
        raise AuthError("x-amz-content-sha256 does not match the body")
    request, signed = canonical_request(
        method, path, canonical_query(raw_query), headers, signed_headers, payload_hash
    )
    if signed != ";".join(sorted(signed_headers)):
        raise AuthError("SignedHeaders is not canonical")
    amz_date = headers.get("x-amz-date", "")
    request_time = _parse_amz_date(amz_date)
    if abs(now - request_time) > CLOCK_SKEW_SECONDS:
        raise AuthError("request date is outside the clock-skew allowance")
    credential_scope = fields["Credential"].split("/", 1)[1]
    expected = signature(
        secret,
        amz_date[:8],
        credential_scope.split("/")[1],
        credential_scope.split("/")[2],
        string_to_sign(amz_date, credential_scope, request),
    )
    if not hmac.compare_digest(expected, provided):
        raise AuthError("signature mismatch")
    return VerifiedRequest(kind, False, access_key, request_time)


def verify_presigned(
    *,
    method: str,
    path: str,
    raw_query: str,
    headers: Mapping[str, str],
    credentials: Mapping[str, tuple[str, str]],
    now: float,
    enforce_expiry: bool = True,
) -> VerifiedRequest:
    params = dict(parse_qsl(raw_query, keep_blank_values=True))
    if params.get("X-Amz-Algorithm") != ALGORITHM:
        raise AuthError("unsupported presigned algorithm")
    try:
        credential = params["X-Amz-Credential"]
        amz_date = params["X-Amz-Date"]
        expires = int(params["X-Amz-Expires"])
        signed_names = params["X-Amz-SignedHeaders"].split(";")
        provided = params["X-Amz-Signature"]
    except (KeyError, ValueError) as exc:
        raise AuthError("presigned URL is missing a required parameter") from exc
    access_key = credential.split("/")[0]
    entry = credentials.get(access_key)
    if entry is None:
        raise AuthError("unknown access key")
    secret, kind = entry
    credential_scope = credential.split("/", 1)[1]
    if credential_scope.split("/")[2] != "s3":
        raise AuthError("presigned service is not s3")

    # The signature input is the canonical query without X-Amz-Signature.
    pairs = sorted(
        (encode(name), encode(value))
        for name, value in params.items()
        if name != "X-Amz-Signature"
    )
    query = "&".join(f"{name}={value}" for name, value in pairs)
    request, _ = canonical_request(
        method, path, query, headers, signed_names, UNSIGNED_PAYLOAD
    )
    expected = signature(
        secret,
        amz_date[:8],
        credential_scope.split("/")[1],
        credential_scope.split("/")[2],
        string_to_sign(amz_date, credential_scope, request),
    )
    if not hmac.compare_digest(expected, provided):
        raise AuthError("presigned signature mismatch")
    request_time = _parse_amz_date(amz_date)
    expires_at = request_time + expires
    if enforce_expiry and now > expires_at:
        raise AuthError("presigned URL has expired")
    return VerifiedRequest(kind, True, access_key, expires_at)


def verify_request(
    *,
    method: str,
    path: str,
    raw_query: str,
    headers: Mapping[str, str],
    body: bytes,
    credentials: Mapping[str, tuple[str, str]],
    now: float | None = None,
    enforce_presigned_expiry: bool = True,
) -> VerifiedRequest:
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    has_presigned = "X-Amz-Signature" in raw_query
    if has_presigned:
        return verify_presigned(
            method=method,
            path=path,
            raw_query=raw_query,
            headers=headers,
            credentials=credentials,
            now=now,
            enforce_expiry=enforce_presigned_expiry,
        )
    return verify_header_auth(
        method=method,
        path=path,
        raw_query=raw_query,
        headers=headers,
        body=body,
        credentials=credentials,
        now=now,
    )
