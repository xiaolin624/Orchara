"""The worker-side controlled-model adapter (F06/T041).

A worker never talks to a model vendor. It calls the platform capability endpoint
with a run id, the live attempt token, a model profile version id and the request
params; the server authorizes the call against the run's sealed manifest, meters
one physical attempt and returns the validated platform projection.

Rules this module enforces:

* **One physical call per generate.** ``PlatformChatModel.generate`` invokes the
  transport exactly once and returns, raises, or raises. There is no retry loop,
  no backoff and no "try the fallback" branch: the Go server is the only retry
  authority and every retry it makes is a separate ledger attempt.
* **The provider object never escapes.** The adapter validates the projection
  (tool-call ids non-empty and unique, names declared, arguments an object that
  satisfies the declared tool input schema) and returns a typed
  :class:`Generation`. A malformed answer is :class:`PlatformValidationError`.
* **Platform errors stay platform errors.** A server error code is preserved
  (:class:`PlatformCapabilityError`) without a provider URL, credential or body.

``CapabilityTransport`` is the seam: production (T042/T043) implements it over the
worker mTLS client; tests inject a fake that counts physical calls. No
``langchain_core`` dependency is required - the adapter is a minimal callable
model object, which is also what an injected LangGraph node needs.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .schema import validate_value


class PlatformError(Exception):
    """Base shape of a typed platform failure. ``code`` is a stable platform code."""

    def __init__(self, code: str, message: str = "", *, status: int | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status = status


class PlatformCapabilityError(PlatformError):
    """The capability endpoint returned a typed platform failure."""


class PlatformValidationError(PlatformError):
    """The answer (or the request) is not a valid platform projection."""


class CapabilityTransport(Protocol):
    """One real capability request. It must not retry on its own."""

    def invoke(self, run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        ...


class StreamingCapabilityTransport(Protocol):
    """One real streaming capability request (F06/T044).

    ``stream`` performs exactly one physical HTTP stream and calls ``on_frame``
    for each contract SSE frame. It has no retry loop: the Go server owns retries
    and each of its retries is a separate ledger attempt/generation.
    """

    def stream(
        self,
        run_id: str,
        body: dict[str, Any],
        on_frame: Callable[[dict[str, Any]], None],
    ) -> None:
        ...


@dataclass(frozen=True)
class DeclaredTool:
    """One tool version the model call may use, with the input schema to check."""

    name: str
    capability_version: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Generation:
    text: str
    tool_calls: tuple[ToolCall, ...]
    structured: dict[str, Any] | None
    usage: dict[str, Any] | None
    model_profile_version_id: str | None
    stub: bool


def _uuid(value: str | None, field_name: str) -> str:
    text = (value or "").strip()
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError) as exc:
        raise PlatformValidationError("validation_failed", f"{field_name} must be a UUID") from exc


class PlatformChatModel:
    """The controlled model adapter: one capability call per ``generate``."""

    def __init__(
        self,
        transport: CapabilityTransport,
        *,
        run_id: str,
        attempt_token: str,
        capability_version: str,
        capability_kind: str = "model",
    ) -> None:
        self.transport = transport
        self.run_id = _uuid(run_id, "run_id")
        self.attempt_token = _uuid(attempt_token, "attempt_token")
        self.capability_version = _uuid(capability_version, "capability_version")
        if capability_kind != "model":
            raise PlatformValidationError(
                "validation_failed", "PlatformChatModel only invokes model capabilities"
            )
        self.capability_kind = capability_kind

    def generate(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[DeclaredTool] | None = None,
        response_schema: dict[str, Any] | None = None,
        max_output_tokens: int | None = None,
        logical_key: str | None = None,
        operation_id: str | None = None,
    ) -> Generation:
        """Perform exactly one physical model call and return the validated answer."""
        declared = {tool.name: tool for tool in (tools or [])}
        if len(declared) != len(tools or []):
            raise PlatformValidationError("invalid_tool_call", "tool names must be unique")

        body = self._request_body(
            messages=messages,
            tools=tools or [],
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
            logical_key=logical_key,
            operation_id=operation_id,
        )
        # The one physical call. Do not wrap this in a retry: the server owns
        # retries and each of its retries is its own ledger attempt.
        try:
            response = self.transport.invoke(self.run_id, body)
        except PlatformError:
            raise
        except Exception as exc:  # transport failure: the attempt may or may not exist
            raise PlatformCapabilityError(
                "transport_error", f"the capability transport failed ({type(exc).__name__})"
            ) from exc

        if not isinstance(response, dict):
            raise PlatformValidationError(
                "provider_response_invalid", "the capability response is not an object"
            )
        state = str(response.get("state") or "")
        if state != "succeeded":
            code = str(response.get("error_code") or "capability_failed")
            raise PlatformCapabilityError(code, f"the capability did not succeed ({state or 'unknown'})")
        data = response.get("data")
        if not isinstance(data, dict):
            raise PlatformValidationError(
                "provider_response_invalid", "the capability answer has no data object"
            )
        return self._validate_generation(data, declared)

    def _request_body(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[DeclaredTool],
        response_schema: dict[str, Any] | None,
        max_output_tokens: int | None,
        logical_key: str | None,
        operation_id: str | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"task": "agent", "messages": messages}
        if tools:
            params["tools"] = [
                {"name": tool.name, "capability_version": tool.capability_version}
                for tool in tools
            ]
        if response_schema is not None:
            params["response_schema"] = response_schema
        if max_output_tokens is not None:
            params["max_output_tokens"] = max_output_tokens
        return {
            "attempt_token": self.attempt_token,
            "operation_id": _uuid(operation_id or str(uuid.uuid4()), "operation_id"),
            "logical_key": logical_key or f"agent-{uuid.uuid4()}",
            "capability_version": self.capability_version,
            "params": params,
            "capability_kind": self.capability_kind,
        }

    def stream_generate(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[DeclaredTool] | None = None,
        response_schema: dict[str, Any] | None = None,
        max_output_tokens: int | None = None,
        logical_key: str | None = None,
        operation_id: str | None = None,
        on_frame: Callable[[dict[str, Any]], None] | None = None,
    ) -> Generation:
        """Stream exactly one physical model call (F06/T044).

        Frames are forwarded to ``on_frame`` for a temporary preview. The returned
        :class:`Generation` is built **only** from the terminal validated ``final``
        frame of the current generation: a text/tool draft from an earlier failed
        generation is discarded when the server starts the next generation, and a
        tool call is never executed from a streamed fragment.
        """
        declared = {tool.name: tool for tool in (tools or [])}
        if len(declared) != len(tools or []):
            raise PlatformValidationError("invalid_tool_call", "tool names must be unique")
        stream_method = getattr(self.transport, "stream", None)
        if not callable(stream_method):
            raise PlatformValidationError(
                "streaming_transport_unavailable",
                "the capability transport does not implement the streaming protocol",
            )
        body = self._request_body(
            messages=messages,
            tools=tools or [],
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
            logical_key=logical_key,
            operation_id=operation_id,
        )

        current_generation: str | None = None
        draft_text = ""
        had_delta = False
        terminal_error = None
        final_frame: dict[str, Any] | None = None

        def handle(frame: dict[str, Any]) -> None:
            nonlocal current_generation, draft_text, had_delta, terminal_error, final_frame
            if not isinstance(frame, dict):
                raise PlatformValidationError(
                    "provider_response_invalid", "the model stream frame is not an object"
                )
            frame_type = frame.get("type")
            generation = frame.get("generation_id")
            operation = frame.get("operation_id")
            if frame_type not in {"delta", "tool_delta", "final", "error"}:
                raise PlatformValidationError(
                    "provider_response_invalid", "the model stream frame has no known type"
                )
            if not isinstance(generation, str) or not generation:
                raise PlatformValidationError(
                    "provider_response_invalid", "every stream frame needs a generation_id"
                )
            if not isinstance(operation, str) or not operation:
                raise PlatformValidationError(
                    "provider_response_invalid", "every stream frame needs an operation_id"
                )
            if generation != current_generation:
                # A new generation replaces the old draft. The old text is never
                # appended to the new one, and old tool fragments are forgotten.
                current_generation = generation
                draft_text = ""
                had_delta = False
                terminal_error = None
                final_frame = None
            if on_frame is not None:
                on_frame(frame)
            if frame_type == "delta":
                text = frame.get("text", "")
                if not isinstance(text, str):
                    raise PlatformValidationError(
                        "provider_response_invalid", "a delta frame text must be a string"
                    )
                draft_text += text
                had_delta = True
            elif frame_type == "error":
                if not frame.get("retryable"):
                    error = frame.get("error") or {}
                    code = error.get("code") if isinstance(error, dict) else None
                    terminal_error = str(code or "capability_failed")
                # A retryable error is not terminal: the next generation begins.
            elif frame_type == "final":
                final_frame = frame

        try:
            stream_method(self.run_id, body, handle)
        except PlatformError:
            raise
        except Exception as exc:  # transport failure: never invent a retry
            raise PlatformCapabilityError(
                "transport_error", f"the streaming transport failed ({type(exc).__name__})"
            ) from exc

        if terminal_error is not None:
            raise PlatformCapabilityError(terminal_error, "the model stream failed")
        if final_frame is None:
            raise PlatformCapabilityError(
                "stream_incomplete", "the model stream ended without a final frame"
            )
        data = final_frame.get("data")
        if not isinstance(data, dict):
            raise PlatformValidationError(
                "provider_response_invalid", "the final frame has no data object"
            )
        generation = self._validate_generation(data, declared)
        if had_delta and draft_text != generation.text:
            raise PlatformValidationError(
                "provider_response_invalid",
                "the streamed draft does not match the validated final content",
            )
        return generation

    def _validate_generation(
        self, data: dict[str, Any], declared: dict[str, DeclaredTool]
    ) -> Generation:
        text = data.get("text", "")
        if not isinstance(text, str):
            raise PlatformValidationError("provider_response_invalid", "text must be a string")
        structured = data.get("structured")
        if structured is not None and not isinstance(structured, dict):
            raise PlatformValidationError(
                "provider_response_invalid", "structured must be an object"
            )
        usage = data.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise PlatformValidationError("provider_response_invalid", "usage must be an object")
        model_id = data.get("model_profile_version_id")
        if model_id is not None and not isinstance(model_id, str):
            raise PlatformValidationError(
                "provider_response_invalid", "model_profile_version_id must be a string"
            )
        stub = data.get("stub", False)
        if not isinstance(stub, bool):
            raise PlatformValidationError("provider_response_invalid", "stub must be a boolean")

        raw_calls = data.get("tool_calls", [])
        if not isinstance(raw_calls, list):
            raise PlatformValidationError(
                "provider_response_invalid", "tool_calls must be an array"
            )
        calls: list[ToolCall] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_calls):
            field_name = f"tool_calls/{index}"
            if not isinstance(raw, dict) or set(raw) != {"id", "name", "arguments"}:
                raise PlatformValidationError(
                    "invalid_tool_call",
                    f"{field_name} must contain exactly id, name and arguments",
                )
            call_id = raw.get("id")
            if not isinstance(call_id, str) or not call_id.strip():
                raise PlatformValidationError(
                    "invalid_tool_call", f"{field_name}/id must be a non-empty string"
                )
            if call_id in seen:
                raise PlatformValidationError(
                    "invalid_tool_call", f"{field_name}/id duplicates a tool-call id"
                )
            seen.add(call_id)
            name = raw.get("name")
            if not isinstance(name, str) or not name:
                raise PlatformValidationError(
                    "invalid_tool_call", f"{field_name}/name must be a non-empty string"
                )
            tool = declared.get(name)
            if tool is None:
                raise PlatformValidationError(
                    "invalid_tool_call", f"{field_name}/name is not a declared tool"
                )
            arguments = raw.get("arguments")
            if not isinstance(arguments, dict):
                raise PlatformValidationError(
                    "invalid_tool_call", f"{field_name}/arguments must be an object"
                )
            problems = validate_value(tool.input_schema, arguments)
            if problems:
                raise PlatformValidationError(
                    "invalid_tool_call",
                    f"{field_name}/arguments{problems[0]['field']}: {problems[0]['message']}",
                )
            calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
        return Generation(
            text=text,
            tool_calls=tuple(calls),
            structured=structured,
            usage=usage,
            model_profile_version_id=model_id,
            stub=stub,
        )
