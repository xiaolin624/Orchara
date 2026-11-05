"""F07/T078: the DeepAgents delegation adapter.

``PublishedAppDelegate`` is the only bridge an SDK agent has to another
application. It implements the existing :class:`platform_worker.agents.agent.ToolExecutor`
protocol over a narrow transport:

* ``spawn_child`` posts the contract's ``spawnChild`` body (worker mTLS +
  the parent attempt token) and returns the contract ``Run``;
* ``child_result`` reads the non-contract terminal-result projection.

The adapter never creates a Run itself, never imports a database driver and never
reads another Run's artifact: the platform's ``spawnChild`` endpoint is the
scheduler, and it re-proves the frozen closure, the caller's ACL and the child
version at spawn time.

Replay and concurrency follow EXECUTION.md §7: the logical key is derived
deterministically from the run and the caller's invocation path (the SDK tool-call
identity), so a retried attempt replays the original child, while two concurrent
calls from different invocation paths are two independent children. A live child
is a ``waiting`` tool outcome; a failed required child is a typed tool failure with
the platform's stable code.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .agent import ToolExecutionError
from .schema import validate_value


class DelegateTransport(Protocol):
    """The narrow platform seam. No retry, no generic request method."""

    def spawn_child(
        self,
        run_id: str,
        *,
        attempt_token: str,
        logical_key: str,
        application_version_id: str,
        input: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def child_result(
        self, run_id: str, child_run_id: str, *, attempt_token: str
    ) -> dict[str, Any]:
        ...


class DelegateTransportError(Exception):
    """A platform refusal with its stable contract code."""

    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class ChildApp:
    """One fixed child application the SDK agent may call.

    ``tool_name`` is what the model sees (derived by the platform from the sealed
    ``subapp_versions``); ``application_version_id`` is the frozen version; the
    optional ``output_schema`` is the child version's frozen output schema, used
    to validate a returned result locally before the loop trusts it.
    """

    tool_name: str
    application_version_id: str
    output_schema: dict[str, Any] | None = None


class PublishedAppDelegate:
    """The ``task``-style tool executor backed by the platform's spawnChild."""

    def __init__(
        self,
        transport: DelegateTransport,
        *,
        run_id: str,
        attempt_token: str,
        children: list[ChildApp],
        invocation_path: str = "default",
    ) -> None:
        self._transport = transport
        self._run_id = str(run_id or "")
        self._attempt_token = str(attempt_token or "")
        self._children = {child.tool_name: child for child in children}
        if len(self._children) != len(children):
            raise ValueError("declared child tool names must be unique")
        self._invocation_path = str(invocation_path or "default")

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        return self.execute_at(self._invocation_path, name, arguments)

    def execute_at(
        self, invocation_path: str, name: str, arguments: dict[str, Any]
    ) -> Any:
        child = self._children.get(str(name))
        if child is None:
            raise ToolExecutionError("invalid_tool_call", message="undeclared child tool")
        if not isinstance(arguments, dict):
            raise ToolExecutionError("invalid_tool_call", message="child input must be an object")
        logical_key = "call_app:" + str(invocation_path or "default") + ":" + str(name)
        if len(logical_key) > 200:
            raise ToolExecutionError("invalid_tool_call", message="invocation path is too long")
        try:
            run = self._transport.spawn_child(
                self._run_id,
                attempt_token=self._attempt_token,
                logical_key=logical_key,
                application_version_id=child.application_version_id,
                input=arguments,
            )
        except DelegateTransportError as exc:
            raise ToolExecutionError(exc.code or "child_spawn_failed") from None
        if not isinstance(run, dict) or not run.get("id"):
            raise ToolExecutionError("child_spawn_failed", message="the platform returned no child Run")
        state = str(run.get("state") or "")
        if state == "succeeded":
            try:
                result = self._transport.child_result(
                    self._run_id, str(run["id"]), attempt_token=self._attempt_token
                )
            except DelegateTransportError as exc:
                raise ToolExecutionError(exc.code or "child_result_failed") from None
            output = (result or {}).get("output")
            if not isinstance(output, dict):
                raise ToolExecutionError(
                    "child_result_failed", message="the child returned no structured object"
                )
            if child.output_schema is not None:
                problems = validate_value(child.output_schema, output)
                if problems:
                    raise ToolExecutionError(
                        "child_output_schema_invalid",
                        message="the child result fails its frozen output schema",
                    )
            return output
        if state in ("failed", "cancelled"):
            code = str(run.get("error_code") or "child_required_failed")
            raise ToolExecutionError(code)
        # queued/running/waiting_*: the parent must hand over, not fail. A
        # dedicated ``waiting`` flag keeps this out of the uncertain/reconciliation
        # path in run_agent.
        raise ToolExecutionError("children_pending", waiting=True)


class HTTPDelegateTransport:
    """A real mTLS transport for the two internal routes (no external dependency).

    ``base_url`` is the internal listener; the client certificate must carry the
    ``worker`` organization and ``X-Internal-Role: worker`` is set, exactly like the
    contract's spawnChild security requirement.
    """

    def __init__(
        self,
        base_url: str,
        *,
        ca_file: str,
        cert_file: str,
        key_file: str,
        role: str = "worker",
        timeout: float = 30.0,
    ) -> None:
        context = ssl.create_default_context(cafile=ca_file)
        context.load_cert_chain(certfile=cert_file, keyfile=key_file)
        self._base_url = base_url.rstrip("/")
        self._context = context
        self._role = role
        self._timeout = timeout

    def spawn_child(
        self,
        run_id: str,
        *,
        attempt_token: str,
        logical_key: str,
        application_version_id: str,
        input: dict[str, Any],
    ) -> dict[str, Any]:
        return self._post(
            "/internal/v1/runs/" + _escape(run_id) + "/children",
            {
                "attempt_token": attempt_token,
                "logical_key": logical_key,
                "application_version_id": application_version_id,
                "input": input,
            },
        )

    def child_result(
        self, run_id: str, child_run_id: str, *, attempt_token: str
    ) -> dict[str, Any]:
        return self._post(
            "/internal/v1/runs/"
            + _escape(run_id)
            + "/children/"
            + _escape(child_run_id)
            + "/result",
            {"attempt_token": attempt_token},
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self._base_url + path,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Internal-Role": self._role,
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout, context=self._context
            ) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise _error_from_body(exc.code, exc.read()) from None
        if not body.strip():
            return {}
        decoded = json.loads(body.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise DelegateTransportError(502, "invalid_response", "the platform returned a non-object")
        return decoded


def _error_from_body(status: int, body: bytes) -> DelegateTransportError:
    code = "internal_error"
    message = ""
    try:
        envelope = json.loads(body.decode("utf-8"))
        error = envelope.get("error") or {}
        code = str(error.get("code") or code)
        message = str(error.get("message") or "")
    except (ValueError, AttributeError):
        code = "invalid_response"
    return DelegateTransportError(status, code, message)


def _escape(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")


__all__ = [
    "ChildApp",
    "DelegateTransport",
    "DelegateTransportError",
    "HTTPDelegateTransport",
    "PublishedAppDelegate",
]
