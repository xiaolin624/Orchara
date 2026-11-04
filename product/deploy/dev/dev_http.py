#!/usr/bin/env python3
"""A tiny real-HTTP client shared by the F15A dev bring-up scripts.

It is deliberately small and standard-library only: the dev tools must be
runnable from a clean Python 3.12 without installing the test dependencies.
Cookies are kept in a real cookie jar and redirects are not followed, so a
3xx/401/404 is observed as the server returned it.
"""

from __future__ import annotations

import http.cookiejar
import json
import socket
import time
import urllib.error
import urllib.request

SESSION_COOKIE = "platform_session"


class Response:
    def __init__(self, status: int, headers, raw: bytes) -> None:
        self.status = status
        self.headers = headers
        self.raw = raw

    @property
    def text(self) -> str:
        return self.raw.decode("utf-8", errors="replace")

    @property
    def json(self):
        if not self.text.strip():
            return None
        return json.loads(self.text)

    def header(self, name: str) -> str | None:
        return self.headers.get(name)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class HttpClient:
    """Cookie-aware HTTP client that returns every response, including errors."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect()
        )
        self.requests: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        body=None,
        raw_body: str | bytes | None = None,
        csrf: str | None = None,
        headers: dict[str, str] | None = None,
        absolute: bool = False,
        timeout: float = 60.0,
    ) -> Response:
        url = path if absolute else (path if path.startswith("/") else "/" + path)
        if not absolute:
            url = self.base_url + url
        self.requests.append((method, url))
        request_headers = dict(headers or {})
        data = None
        if raw_body is not None:
            data = raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body
            request_headers.setdefault("Content-Type", "application/json")
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        if csrf is not None:
            request_headers["X-CSRF-Token"] = csrf
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return Response(response.status, response.headers, response.read())
        except urllib.error.HTTPError as exc:
            return Response(exc.code, exc.headers, exc.read())

    def get(self, path: str, **kwargs) -> Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> Response:
        return self.request("PUT", path, **kwargs)

    def cookie(self, name: str = SESSION_COOKIE) -> str | None:
        for cookie in self.jar:
            if cookie.name == name:
                return cookie.value
        return None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_http(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
            time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {url}; last error: {last}")


def error_of(response: Response) -> dict:
    body = response.json
    if not isinstance(body, dict) or "error" not in body:
        raise AssertionError(f"expected a contract error envelope, got HTTP {response.status}: {response.text[:500]}")
    return body["error"]


def expect_error(response: Response, status: int, code: str, what: str) -> dict:
    assert response.status == status, f"{what}: expected HTTP {status}, got {response.status}: {response.text[:500]}"
    error = error_of(response)
    assert error.get("code") == code, f"{what}: expected error code {code!r}, got {error!r}"
    assert error.get("message"), f"{what}: the error has no message: {error!r}"
    return error
