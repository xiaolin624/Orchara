"""F06/T122 real-stack helpers: the OpenAI-compatible chat-completions adapter.

The stack is the F06/T041 real stack (throwaway PostgreSQL migrated with the real
``cmd/admin`` chain, the compiled ``cmd/api`` behind real HTTP, the real mTLS
capability endpoint, the real filesystem object store and the real T025 golden
manifest) with the one deployment fact this ticket adds: ``ORCHARA_MODEL_ENDPOINT``
points at a real loopback HTTP server whose answers each test controls.

Nothing about the traffic is faked: the Go adapter speaks the actual
chat-completions wire protocol over TCP, the fake provider records exactly the
request it received, and every ledger/artifact assertion reads PostgreSQL rows
and sealed object bytes. The credential value exists only in this process's
environment for the duration of the API process; it is never written to the
repository, a fixture, a report or the API log.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from support import t024, t041
from support import stack as st

PROVIDER = "openai-compatible"
MODEL_ID = "fake-vendor-model-1"
ENDPOINT_ID = "t122-fake-endpoint"
CREDENTIAL_ID = "t122-fake-credential"
MISSING_CREDENTIAL_ID = "t122-unset-credential"
DESTINATION = "t122-fake-local"
SECRET_ENV = "T122_MODEL_SECRET"

MODEL_PROFILE_ID = t041.GOLDEN_MODEL_ID


@dataclass
class T122Stack:
    """The T024 stack plus the loopback provider and its per-run secret."""

    stack: t024.T024Stack
    provider: "FakeProvider"
    secret: str

    @property
    def dbname(self) -> str:
        return self.stack.dbname

    @property
    def object_dir(self):
        return self.stack.object_dir

    @property
    def server(self):
        return self.stack.server


# ---------------------------------------------------------------------------
# deployment configuration
# ---------------------------------------------------------------------------
def catalog_env() -> dict[str, str]:
    """A deployer catalog whose endpoint is the loopback fake provider."""
    endpoints = [{
        "id": ENDPOINT_ID,
        "data_destination": DESTINATION,
        "credential_ref": CREDENTIAL_ID,
        "description": "T122 loopback fake provider",
    }]
    credentials = [
        {
            "id": CREDENTIAL_ID,
            "description": "T122 fake provider key (resolved from the deployment env)",
            "env": SECRET_ENV,
        },
        {
            # Registered, but its environment variable is never set: the adapter
            # must fail closed instead of inventing a credential.
            "id": MISSING_CREDENTIAL_ID,
            "description": "T122 credential whose process environment is unset",
            "env": "T122_UNSET_MODEL_SECRET",
        },
    ]
    return {
        "ORCHARA_MODEL_ENDPOINTS": json.dumps(endpoints),
        "ORCHARA_CREDENTIAL_REFS": json.dumps(credentials),
    }


def model_config(**overrides) -> dict:
    """The registered config of the fake real model."""
    value = {
        "kind": "generation",
        "protocol": "chat_compatible",
        "context_window": 8192,
        "max_output_tokens": 512,
        "timeout_ms": 5000,
        "price": {
            "currency": "USD",
            "input_per_million": "1.00",
            "output_per_million": "2.00",
        },
    }
    value.update(overrides)
    return value


def new_secret() -> str:
    return "t122-secret-" + secrets.token_urlsafe(24)


def register_model(
    dbname: str,
    *,
    provider: str = PROVIDER,
    model_id: str = MODEL_ID,
    endpoint_ref: str = ENDPOINT_ID,
    secret_ref: str = CREDENTIAL_ID,
    config_value: dict | None = None,
    data_destination: str = DESTINATION,
    model_profile_id: str = MODEL_PROFILE_ID,
) -> None:
    """Point the run's frozen golden model profile at the fake real provider.

    The sealed execution manifest references model profile versions by id only,
    so updating the row before the run is created is exactly how an administrator
    would have registered this candidate. The row's references are real catalog
    ids (registration validates them); a test that wants the provider's backstop
    can still write an unregistered reference here.
    """
    config_json = json.dumps(model_config() if config_value is None else config_value)
    st.sql(
        dbname,
        "UPDATE platform.model_profile_versions SET "
        f"provider = {st.quote_literal(provider)}, "
        f"model_id = {st.quote_literal(model_id)}, "
        f"endpoint_ref = {st.quote_literal(endpoint_ref)}, "
        f"secret_ref = {st.quote_literal(secret_ref)}, "
        f"data_destination = {st.quote_literal(data_destination)}, "
        f"config = {st.quote_literal(config_json)}::jsonb "
        f"WHERE id = {st.quote_literal(model_profile_id)}::uuid",
    )


# ---------------------------------------------------------------------------
# the local fake HTTP provider
# ---------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            body = {"_raw": raw.decode("utf-8", "replace")}
        record = {
            "method": self.command,
            "path": self.path,
            "headers": {name.lower(): value for name, value in self.headers.items()},
            "body": body,
        }
        owner: "FakeProvider" = self.server.owner  # type: ignore[attr-defined]
        owner.records.append(record)
        result = owner.responder(record, self)
        if result is None:
            # The responder wrote the response itself (SSE / abrupt close).
            return
        status, headers, payload = result
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def finish(self) -> None:
        try:
            super().finish()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def log_message(self, *_args) -> None:  # keep pytest output clean
        return


class _Server(ThreadingHTTPServer):
    daemon_threads = True


class FakeProvider:
    """A real loopback HTTP server whose chat-completions answers tests control."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self.responder = self._default_responder
        self._httpd = _Server(("127.0.0.1", 0), _Handler)
        self._httpd.owner = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    # -- inspection ---------------------------------------------------------
    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def requests(self) -> list[dict]:
        return list(self.records)

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    # -- answer programming -------------------------------------------------
    def _default_responder(self, _record, _handler):
        return self.completion(content="fake answer", usage=(11, 7))

    def completion(
        self,
        *,
        content: str = "fake answer",
        tool_calls: list[dict] | None = None,
        usage: tuple[int, int] | None = (11, 7),
        delay: float = 0.0,
    ) -> tuple[int, dict, bytes]:
        payload: dict = {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": MODEL_ID,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
        }
        if tool_calls is not None:
            payload["choices"][0]["message"]["tool_calls"] = tool_calls
        if usage is not None:
            payload["usage"] = {
                "prompt_tokens": usage[0],
                "completion_tokens": usage[1],
                "total_tokens": usage[0] + usage[1],
            }
        if delay:
            time.sleep(delay)
        return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode()

    def respond_once(self, **kwargs) -> None:
        self.responder = lambda _record, _handler: self.completion(**kwargs)

    def respond_status(self, status: int, body: str = "") -> None:
        payload = body or json.dumps({"error": {"message": "provider refused"}})
        self.responder = lambda _record, _handler: (
            status,
            {"Content-Type": "application/json"},
            payload.encode(),
        )

    def respond_raw(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.responder = lambda _record, _handler: (
            status,
            {"Content-Type": content_type},
            body,
        )

    def respond_sequence(self, responders: list) -> None:
        """Answer each successive request from the list (the last answer repeats)."""
        state = {"index": 0}
        lock = threading.Lock()

        def responder(record, handler):
            with lock:
                index = min(state["index"], len(responders) - 1)
                state["index"] += 1
            return responders[index](record, handler)

        self.responder = responder

    def stream_responder(self, frames: list[dict], *, abort_after: int | None = None):
        """A callable that answers with a real SSE stream.

        A completed stream ends with the documented ``data: [DONE]``; ``abort_after``
        limits how many data frames are written before the socket is closed without
        a finish_reason/[DONE], which is a real half answer, not a mocked cancel.
        """
        encoded = [json.dumps(frame) for frame in frames]

        def responder(_record, handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Cache-Control", "no-cache")
            handler.send_header("Connection", "close")
            handler.end_headers()
            written = 0
            for payload in encoded:
                if abort_after is not None and written >= abort_after:
                    break
                try:
                    handler.wfile.write(f"data: {payload}\n\n".encode())
                    handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return None
                written += 1
            if abort_after is None:
                try:
                    handler.wfile.write(b"data: [DONE]\n\n")
                    handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
            handler.close_connection = True
            try:
                handler.connection.close()
            except OSError:
                pass
            return None

        return responder

    def respond_stream(self, frames: list[dict], *, abort_after: int | None = None) -> None:
        self.responder = self.stream_responder(frames, abort_after=abort_after)


def stream_frame(
    *,
    content: str | None = None,
    tool_calls: list[dict] | None = None,
    finish_reason: str | None = None,
    usage: tuple[int, int] | None = None,
) -> dict:
    """One documented chat-completions SSE data object."""
    if usage is not None:
        return {
            "choices": [],
            "usage": {
                "prompt_tokens": usage[0],
                "completion_tokens": usage[1],
                "total_tokens": usage[0] + usage[1],
            },
        }
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    choice: dict = {"index": 0, "delta": delta}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return {"choices": [choice]}


# ---------------------------------------------------------------------------
# ledger / artifact reads
# ---------------------------------------------------------------------------
def operation_count(dbname: str, run_id: str) -> int:
    return len(t041.operation_rows(dbname, run_id))


def model_calls(dbname: str, job_id: str) -> list[dict]:
    return t024.model_calls(dbname, job_id)


def sealed_bytes(stack, artifact_id: str) -> bytes:
    artifact = t041.artifact_row(stack.dbname, artifact_id)
    return (stack.object_dir / artifact["object_key"]).read_bytes()


def status_responder(status: int, body: str = ""):
    """A callable that answers a fixed HTTP status (for retry-count tests)."""
    payload = body or json.dumps({"error": {"message": "provider refused"}})
    return lambda _record, _handler: (
        status,
        {"Content-Type": "application/json"},
        payload.encode(),
    )


__all__ = [
    "CREDENTIAL_ID",
    "DESTINATION",
    "ENDPOINT_ID",
    "FakeProvider",
    "MISSING_CREDENTIAL_ID",
    "MODEL_ID",
    "MODEL_PROFILE_ID",
    "PROVIDER",
    "SECRET_ENV",
    "T122Stack",
    "catalog_env",
    "model_config",
    "model_calls",
    "new_secret",
    "operation_count",
    "register_model",
    "sealed_bytes",
    "status_responder",
    "stream_frame",
]
