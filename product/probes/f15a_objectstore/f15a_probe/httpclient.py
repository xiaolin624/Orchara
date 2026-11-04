"""Minimal S3 HTTP client for the compatibility probe (stdlib only).

The client speaks path-style addressing (``/bucket/key``), SigV4 header auth and
query-signed GET URLs, and it never normalises an object key: ``..`` stays a
literal path segment so the probe can measure whether the *server* (or a proxy in
front of it) collapses it. That distinction is the whole point of the
``out_of_range_key_denied`` traversal case.

Network policy: only transport failures and 5xx answers are retried, and only a
bounded number of times. A 401/403/404/416 is an answer, not a transient error,
so it is returned to the assertion unchanged.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Sequence
from urllib.parse import urlsplit

from . import sigv4
from .config import ProbeEnvironmentError, Target

RETRYABLE_STATUS = {500, 502, 503, 504}
READ_LIMIT = 64 * 1024
RETRY_DELAY_SECONDS = 0.15


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes
    truncated: bool
    attempts: int
    elapsed_ms: int

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def content_length(self) -> int | None:
        raw = self.header("content-length")
        try:
            return int(raw) if raw is not None else None
        except ValueError:
            return None

    def body_sha256(self) -> str:
        return sigv4.sha256_hex(self.body)


def object_path(bucket: str, key: str | None, base_path: str = "") -> str:
    """Build the raw request path; never strips or collapses key segments."""
    path = base_path + "/" + sigv4.uri_encode(bucket)
    if key is None or key == "":
        return path
    encoded = "/".join(sigv4.uri_encode(segment) for segment in key.split("/"))
    return path + "/" + encoded


@dataclass
class Credential:
    access_key: str
    secret_key: str
    session_token: str | None = None


class S3Client:
    def __init__(self, target: Target) -> None:
        self.target = target
        self.request_count = 0
        self.retry_count = 0
        self.runtime = Credential(
            target.access_key, target.secret_key, target.session_token
        )
        self.provision = (
            Credential(
                target.provision_access_key or "",
                target.provision_secret_key or "",
                target.provision_session_token,
            )
            if target.provision_configured
            else None
        )

    # -- low level ---------------------------------------------------------
    def _connection(self) -> http.client.HTTPConnection:
        target = self.target
        if target.scheme == "https":
            context = ssl.create_default_context(cafile=target.ca_file)
            return http.client.HTTPSConnection(
                target.hostname,
                target.port,
                timeout=target.timeout,
                context=context,
            )
        return http.client.HTTPConnection(target.hostname, target.port, timeout=target.timeout)

    def _exchange(
        self,
        method: str,
        path: str,
        query: str,
        headers: Mapping[str, str] | None,
        body: bytes,
        read_limit: int,
    ) -> Response:
        target = self.target
        started = time.monotonic()
        url = path + (f"?{query}" if query else "")
        try:
            connection = self._connection()
            try:
                connection.request(method, url, body=body, headers=dict(headers or {}))
                raw = connection.getresponse()
                payload = raw.read(read_limit + 1) if method != "HEAD" else b""
                response_headers = {name.lower(): value for name, value in raw.getheaders()}
                status = raw.status
            finally:
                connection.close()
        except ssl.SSLError as exc:
            raise ProbeEnvironmentError(
                "endpoint_tls_error", f"TLS handshake failed: {exc.__class__.__name__}: {exc}"
            ) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ProbeEnvironmentError(
                "endpoint_unreachable",
                f"timed out after {target.timeout}s connecting to {target.endpoint}",
            ) from exc
        except (ConnectionError, OSError, http.client.HTTPException) as exc:
            raise ProbeEnvironmentError(
                "endpoint_unreachable",
                f"cannot reach {target.endpoint}: {exc.__class__.__name__}: {exc}",
            ) from exc
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return Response(
            status=status,
            headers=response_headers,
            body=payload[:read_limit],
            truncated=len(payload) > read_limit,
            attempts=1,
            elapsed_ms=elapsed_ms,
        )

    def _with_retries(self, operation) -> Response:
        attempts = 0
        last: Response | None = None
        while True:
            attempts += 1
            self.request_count += 1
            try:
                response = operation()
            except ProbeEnvironmentError:
                if attempts > self.target.retries:
                    raise
                self.retry_count += 1
                time.sleep(RETRY_DELAY_SECONDS * attempts)
                continue
            last = Response(
                status=response.status,
                headers=response.headers,
                body=response.body,
                truncated=response.truncated,
                attempts=attempts,
                elapsed_ms=response.elapsed_ms,
            )
            if response.status in RETRYABLE_STATUS and attempts <= self.target.retries:
                self.retry_count += 1
                time.sleep(RETRY_DELAY_SECONDS * attempts)
                continue
            return last

    # -- signed requests ---------------------------------------------------
    def request(
        self,
        method: str,
        key: str | None,
        *,
        query_pairs: Sequence[tuple[str, str]] = (),
        body: bytes = b"",
        credential: str = "runtime",
        anonymous: bool = False,
        extra_headers: Mapping[str, str] | None = None,
        read_limit: int = READ_LIMIT,
    ) -> Response:
        """Send one signed (or deliberately unsigned) request."""
        path = object_path(self.target.bucket, key, self.target.base_path)
        if anonymous:
            return self._with_retries(
                lambda: self._exchange(
                    method, path, sigv4.canonical_query(query_pairs), {}, body, read_limit
                )
            )
        creds = self.runtime if credential == "runtime" else self.provision
        if creds is None:
            raise ProbeEnvironmentError(
                "provisioning_credentials_missing",
                "this operation needs F15A_S3_PROVISION_ACCESS_KEY / _SECRET_KEY",
            )
        signature = sigv4.sign_headers(
            method=method,
            host=self.target.host_header,
            path=path,
            query_pairs=query_pairs,
            extra_headers=extra_headers,
            payload=body,
            access_key=creds.access_key,
            secret_key=creds.secret_key,
            region=self.target.region,
            service="s3",
            amz_date=amz_datetime(),
            session_token=creds.session_token,
        )
        return self._with_retries(
            lambda: self._exchange(
                method,
                path,
                sigv4.canonical_query(query_pairs),
                signature.request_headers(),
                body,
                read_limit,
            )
        )

    def presign_get(self, key: str, *, expires: int, credential: str = "runtime") -> str:
        creds = self.runtime if credential == "runtime" else self.provision
        if creds is None:
            raise ProbeEnvironmentError(
                "provisioning_credentials_missing",
                "this operation needs F15A_S3_PROVISION_ACCESS_KEY / _SECRET_KEY",
            )
        path = object_path(self.target.bucket, key, self.target.base_path)
        return sigv4.presign_url(
            scheme=self.target.scheme,
            host=self.target.host_header,
            path=path,
            access_key=creds.access_key,
            secret_key=creds.secret_key,
            region=self.target.region,
            service="s3",
            amz_date=amz_datetime(),
            expires=expires,
            session_token=creds.session_token,
        )

    def get_url(self, url: str, *, read_limit: int = READ_LIMIT) -> Response:
        """GET an absolute URL without re-signing it (presigned-URL checks)."""
        parts = urlsplit(url)
        path = parts.path
        query = parts.query
        connection = None
        started = time.monotonic()
        try:
            if parts.scheme == "https":
                context = ssl.create_default_context(cafile=self.target.ca_file)
                connection = http.client.HTTPSConnection(
                    parts.hostname, parts.port or 443, timeout=self.target.timeout, context=context
                )
            else:
                connection = http.client.HTTPConnection(
                    parts.hostname, parts.port or 80, timeout=self.target.timeout
                )
            connection.request("GET", path + (f"?{query}" if query else ""), headers={"host": self.target.host_header})
            raw = connection.getresponse()
            payload = raw.read(read_limit + 1)
            headers = {name.lower(): value for name, value in raw.getheaders()}
            status = raw.status
        except ssl.SSLError as exc:
            raise ProbeEnvironmentError("endpoint_tls_error", f"TLS handshake failed: {exc}") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ProbeEnvironmentError(
                "endpoint_unreachable", f"timed out reading {path} from {self.target.endpoint}"
            ) from exc
        except (ConnectionError, OSError, http.client.HTTPException) as exc:
            raise ProbeEnvironmentError(
                "endpoint_unreachable",
                f"cannot read {path} from {self.target.endpoint}: {exc.__class__.__name__}: {exc}",
            ) from exc
        finally:
            if connection is not None:
                connection.close()
        return Response(
            status=status,
            headers=headers,
            body=payload[:read_limit],
            truncated=len(payload) > read_limit,
            attempts=1,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )


def amz_datetime() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
