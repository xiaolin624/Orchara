"""The restricted JSON-schema subset shared by the worker agent adapter.

The platform server validates tool arguments and structured responses against the
same subset in Go (``internal/capabilities/schema.go``). The worker re-checks the
answer it received - never trusts a provider-shaped object - and this module is
the small independent implementation of that check. Supported keywords:
``type`` (string/array form), ``properties``, ``required``,
``additionalProperties`` (bool or schema), ``items``, ``enum``. Unknown keywords
are ignored except an unknown ``type`` name, which is refused.

This is deliberately not a general JSON-Schema implementation: a provider answer
is only ever accepted against the subset the platform itself emits.
"""

from __future__ import annotations

import math
from typing import Any


def validate_value(schema: dict[str, Any] | None, value: Any) -> list[dict[str, str]]:
    """Return located problems (field/message); an empty list means valid."""
    problems: list[dict[str, str]] = []
    _validate(schema, value, "", 0, problems)
    return problems


def _validate(
    schema: dict[str, Any] | None,
    value: Any,
    path: str,
    depth: int,
    problems: list[dict[str, str]],
) -> None:
    if not schema:
        return
    if depth > 64:
        _add(problems, path, "schema nesting exceeds the supported depth")
        return
    if "type" in schema:
        types = _types(schema["type"])
        if not types:
            _add(problems, path, "schema type is not a supported string or string array")
            return
        if not any(_matches(value, wanted) for wanted in types):
            _add(problems, path, "must be of type " + "|".join(types))
            return
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list):
            _add(problems, path, "schema enum must be an array")
            return
        if not any(_json_equal(candidate, value) for candidate in enum):
            _add(problems, path, "must be one of the declared enum values")
    if isinstance(value, dict):
        _validate_object(schema, value, path, depth, problems)
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate(items, item, f"{path}/{index}", depth + 1, problems)


def _validate_object(
    schema: dict[str, Any],
    obj: dict[str, Any],
    path: str,
    depth: int,
    problems: list[dict[str, str]],
) -> None:
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
            _add(problems, path, "schema required must be an array of strings")
            return
        for name in required:
            if name not in obj:
                _add(problems, f"{path}/{name}", "is required")
    additional = schema.get("additionalProperties", None)
    for key in sorted(obj):
        if key in properties and isinstance(properties[key], dict):
            _validate(properties[key], obj[key], f"{path}/{key}", depth + 1, problems)
            continue
        if additional is False:
            _add(problems, f"{path}/{key}", "is not an allowed property")
        elif isinstance(additional, dict):
            _validate(additional, obj[key], f"{path}/{key}", depth + 1, problems)


def _types(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return list(raw)
    return []


def _matches(value: Any, wanted: str) -> bool:
    if wanted == "object":
        return isinstance(value, dict)
    if wanted == "array":
        return isinstance(value, list)
    if wanted == "string":
        return isinstance(value, str)
    if wanted == "boolean":
        return isinstance(value, bool)
    if wanted == "null":
        return value is None
    if wanted == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if wanted == "integer":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return math.trunc(value) == value
    return False


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    return type(left) is type(right) and left == right


def _add(problems: list[dict[str, str]], path: str, message: str) -> None:
    problems.append({"field": path or "/", "message": message})
