"""Local S3-compatible fixture servers for the F15A probe's discrimination proof.

Two launchers use this module: ``compliant_server.py`` (obeys the policy the
probe asserts) and ``violating_server.py`` (deliberately breaks one or more rules
so the probe must reject it).

**These fixtures are not a candidate S3 and not acceptance evidence.** They are
the instrument that proves the probe has teeth: the same probe process that
passes the compliant fixture must fail the violating one, per assertion.

Implemented S3 subset (path-style): object GET/HEAD/PUT/DELETE, bucket listing
(``?list-type=2``), bucket versioning query, SigV4 header auth, SigV4 query auth
(presigned GET), Range/206, 416, and the policy boundary the platform relies on:

* ``runtime`` may read sealed objects and read/write/delete staging objects;
* ``runtime`` may never write or delete sealed objects;
* everything outside ``<tenant-prefix>/`` is denied to ``runtime``;
* anonymous requests are denied for reads, writes and listings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping
from urllib.parse import unquote, urlsplit

import fixture_auth

ALL_VIOLATIONS = (
    "anonymous_read_denied",
    "anonymous_put_denied",
    "anonymous_list_denied",
    "out_of_range_key_denied",
    "sealed_put_denied",
    "staging_overwrite_preserves_sealed",
    "short_lived_signature_expiry",
    "proxy_download_boundary",
    "delete_semantics",
)

XMLNS = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'


def _error_xml(code: str, message: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Error><Code>{code}</Code><Message>{message}</Message></Error>"
    ).encode("utf-8")


@dataclass
class FixtureConfig:
    bucket: str
    tenant_prefix: str
    credentials: dict[str, tuple[str, str]]
    violations: set[str] = field(default_factory=set)
    transient_failures: int = 0
    verbose: bool = False


class FixtureState:
    def __init__(self, config: FixtureConfig) -> None:
        self.config = config
        self.objects: dict[str, bytes] = {}
        self.pending_transient = config.transient_failures

    # -- policy ------------------------------------------------------------
    def runtime_allowed(self, action: str, key: str) -> bool:
        tenant = self.config.tenant_prefix
        if "out_of_range_key_denied" in self.config.violations and not key.startswith(
            tenant + "/sealed/"
        ):
            # The broken implementation checks authentication but not the
            # tenant prefix for everything except the sealed namespace.
            return True
        if key.startswith(tenant + "/staging/"):
            return action in {"GET", "HEAD", "PUT", "DELETE"}
        if key.startswith(tenant + "/sealed/"):
            if "sealed_put_denied" in self.config.violations:
                return action in {"GET", "HEAD", "PUT"}
            return action in {"GET", "HEAD"}
        return False

    def storage_key(self, key: str) -> str:
        if "out_of_range_key_denied" in self.config.violations:
            # A reverse proxy that collapses ``..`` *after* the policy check.
            segments = [segment for segment in key.split("/") if segment not in ("", ".")]
            resolved: list[str] = []
            for segment in segments:
                if segment == "..":
                    if resolved:
                        resolved.pop()
                else:
                    resolved.append(segment)
            return "/".join(resolved)
        return key

    def staging_put_leaks_into_sealed(self, key: str, body: bytes) -> None:
        """The staging-overwrite violation: a same-named sealed object is overwritten."""
        if "staging_overwrite_preserves_sealed" not in self.config.violations:
            return
        tenant = self.config.tenant_prefix
        if not key.startswith(tenant + "/staging/"):
            return
        sealed_counterpart = tenant + "/sealed/" + key.rsplit("/", 1)[-1]
        if sealed_counterpart in self.objects:
            self.objects[sealed_counterpart] = body

    def take_transient(self) -> bool:
        if self.pending_transient > 0:
            self.pending_transient -= 1
            return True
        return False

    def list_xml(self) -> bytes:
        entries = "".join(
            f"<Contents><Key>{name}</Key><Size>{len(body)}</Size></Contents>"
            for name, body in sorted(self.objects.items())
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<ListBucketResult {XMLNS}><Name>{self.config.bucket}</Name>"
            f"<KeyCount>{len(self.objects)}</KeyCount><IsTruncated>false</IsTruncated>"
            f"{entries}</ListBucketResult>"
        ).encode("utf-8")


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "f15a-fixture/1.0"

    @property
    def state(self) -> FixtureState:
        return self.server.state  # type: ignore[attr-defined]

    # -- HTTP verbs ---------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        if self.state.config.verbose:
            super().log_message(format, *args)

    # -- dispatch -----------------------------------------------------------
    def _dispatch(self, method: str) -> None:
        try:
            self._dispatch_inner(method)
        except Exception as exc:  # noqa: BLE001 - fixture must answer, not traceback
            if self.state.config.verbose:
                print(f"fixture error: {exc!r}", file=sys.stderr, flush=True)
            self._send(500, _error_xml("InternalError", "fixture failure"))

    def _read_body(self) -> bytes:
        raw = self.headers.get("content-length") or "0"
        try:
            length = int(raw)
        except ValueError:
            length = 0
        if length < 0 or length > 32 * 1024 * 1024:
            return b""
        return self.rfile.read(length)

    def _route(self) -> tuple[str, str]:
        parsed = urlsplit(self.path)
        raw_path = parsed.path
        if not raw_path.startswith("/"):
            raise ValueError("path must be absolute")
        parts = raw_path[1:].split("/", 1)
        bucket = unquote(parts[0])
        key = unquote(parts[1]) if len(parts) > 1 else ""
        return bucket, key

    def _dispatch_inner(self, method: str) -> None:
        parsed = urlsplit(self.path)
        raw_query = parsed.query
        bucket, key = self._route()
        if bucket != self.state.config.bucket:
            self._send(404, _error_xml("NoSuchBucket", "bucket does not exist"))
            return

        body = self._read_body() if method == "PUT" else b""
        try:
            auth = fixture_auth.verify_request(
                method=method,
                path=parsed.path,
                raw_query=raw_query,
                headers=self.headers,
                body=body,
                credentials=self.state.config.credentials,
                enforce_presigned_expiry=(
                    "short_lived_signature_expiry" not in self.state.config.violations
                ),
            )
        except fixture_auth.AuthError as exc:
            self._send(403, _error_xml("AccessDenied", str(exc)))
            return

        if auth.kind == "anonymous":
            if not self._anonymous_allowed(method, key):
                self._send(403, _error_xml("AccessDenied", "anonymous access is denied"))
                return

        query = dict(
            pair.split("=", 1) for pair in raw_query.split("&") if "=" in pair
        )
        if key == "":
            self._bucket_operation(method, query)
            return
        self._object_operation(method, key, body, auth.kind)

    def _anonymous_allowed(self, method: str, key: str) -> bool:
        violations = self.state.config.violations
        if key == "":
            return "anonymous_list_denied" in violations
        if method in ("GET", "HEAD"):
            return "anonymous_read_denied" in violations
        if method == "PUT":
            return "anonymous_put_denied" in violations
        return False

    # -- bucket operations --------------------------------------------------
    def _bucket_operation(self, method: str, query: Mapping[str, str]) -> None:
        if method != "GET":
            self._send(400, _error_xml("InvalidRequest", "unsupported bucket operation"))
            return
        if "versioning" in query:
            self._send(
                200,
                f'<?xml version="1.0" encoding="UTF-8"?><VersioningConfiguration {XMLNS}/>'.encode(
                    "utf-8"
                ),
            )
            return
        self._send(200, self.state.list_xml())

    # -- object operations --------------------------------------------------
    def _object_operation(self, method: str, key: str, body: bytes, credential_kind: str) -> None:
        state = self.state
        if credential_kind == "runtime":
            if not state.runtime_allowed(method, key):
                self._send(403, _error_xml("AccessDenied", "credential is not allowed here"))
                return
        elif credential_kind == "anonymous":
            pass  # already authorised by _anonymous_allowed
        # provisioner: unrestricted

        if method == "PUT":
            if state.take_transient():
                self._send(503, _error_xml("SlowDown", "try again"))
                return
            state.staging_put_leaks_into_sealed(key, body)
            state.objects[state.storage_key(key)] = body
            self._send(204, b"")
            return
        if method in ("GET", "HEAD"):
            self._get_object(method, key)
            return
        if method == "DELETE":
            self._delete_object(key)
            return
        self._send(400, _error_xml("InvalidRequest", "unsupported method"))

    def _get_object(self, method: str, key: str) -> None:
        state = self.state
        stored = state.objects.get(state.storage_key(key))
        if stored is None:
            self._send(404, _error_xml("NoSuchKey", "object does not exist"))
            return
        size = len(stored)
        range_header = self.headers.get("range")
        ignore_range = "proxy_download_boundary" in state.config.violations
        if range_header and not ignore_range:
            parsed = _parse_range(range_header, size)
            if parsed is None:
                self._send(
                    416,
                    _error_xml("InvalidRange", "range is not satisfiable"),
                    extra_headers={"Content-Range": f"bytes */{size}"},
                )
                return
            start, end = parsed
            chunk = stored[start : end + 1]
            self._send(
                206,
                chunk,
                content_type="application/octet-stream",
                extra_headers={
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Accept-Ranges": "bytes",
                },
            )
            return
        extra: dict[str, str] = {"Accept-Ranges": "bytes", "ETag": _etag(stored)}
        if method == "HEAD" and "proxy_download_boundary" in state.config.violations:
            # A download path that cannot know the size before streaming it.
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Connection", "close")
            self.send_header("ETag", extra["ETag"])
            self.end_headers()
            self.close_connection = True
            return
        self._send(200, stored, content_type="application/octet-stream", extra_headers=extra)

    def _delete_object(self, key: str) -> None:
        state = self.state
        stored_key = state.storage_key(key)
        if "delete_semantics" in state.config.violations:
            if stored_key not in state.objects:
                self._send(404, _error_xml("NoSuchKey", "object does not exist"))
                return
            self._send(204, b"")  # claims success but leaves the object readable
            return
        state.objects.pop(stored_key, None)
        self._send(204, b"")

    # -- responses ----------------------------------------------------------
    def _send(
        self,
        status: int,
        body: bytes,
        *,
        content_type: str = "application/xml",
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        if status == 204:
            self.send_header("Connection", "close")
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.close_connection = True
            return
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)
        self.close_connection = True


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    if not header.startswith("bytes="):
        return None
    spec = header[len("bytes=") :].split(",")[0].strip()
    start_raw, _, end_raw = spec.partition("-")
    try:
        if start_raw == "":
            suffix = int(end_raw)
            if suffix <= 0:
                return None
            start = max(0, size - suffix)
            return start, size - 1
        start = int(start_raw)
        if start >= size:
            return None
        end = int(end_raw) if end_raw else size - 1
    except ValueError:
        return None
    if end < start:
        return None
    return start, min(end, size - 1)


def _etag(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, state: FixtureState) -> None:
        self.state = state
        super().__init__(address, FixtureHandler)


def build_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--bucket", default="probe-bucket")
    parser.add_argument("--tenant-prefix", default="orchara-probe")
    parser.add_argument("--runtime-access-key", default="probe-runtime-credential")
    parser.add_argument(
        "--runtime-secret-key", default="fixture-runtime-secret-not-a-credential"
    )
    parser.add_argument("--provision-access-key", default="probe-provision-credential")
    parser.add_argument(
        "--provision-secret-key", default="fixture-provision-secret-not-a-credential"
    )
    parser.add_argument(
        "--violate",
        action="append",
        choices=list(ALL_VIOLATIONS) + ["all"],
        default=[],
        help="deliberately break this rule (repeatable)",
    )
    parser.add_argument(
        "--transient-failures",
        type=int,
        default=0,
        help="answer the next N provisioning PUTs with 503 SlowDown",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def make_config(args: argparse.Namespace) -> FixtureConfig:
    violations = set(args.violate)
    if "all" in violations:
        violations = set(ALL_VIOLATIONS)
    return FixtureConfig(
        bucket=args.bucket,
        tenant_prefix=args.tenant_prefix.strip("/"),
        credentials={
            args.runtime_access_key: (args.runtime_secret_key, "runtime"),
            args.provision_access_key: (args.provision_secret_key, "provisioner"),
        },
        violations=violations,
        transient_failures=args.transient_failures,
        verbose=args.verbose,
    )


def serve(args: argparse.Namespace) -> int:
    state = FixtureState(make_config(args))
    server = FixtureServer((args.host, args.port), state)
    port = server.server_address[1]
    print(
        json.dumps(
            {
                "fixture": "f15a-s3",
                "listening": f"http://{args.host}:{port}",
                "bucket": args.bucket,
                "tenant_prefix": state.config.tenant_prefix,
                "violations": sorted(state.config.violations),
                "transient_failures": state.config.transient_failures,
                "fixture_secrets_are_test_literals": True,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(description: str, argv: list[str] | None = None) -> int:
    args = build_parser(description).parse_args(argv)
    return serve(args)
