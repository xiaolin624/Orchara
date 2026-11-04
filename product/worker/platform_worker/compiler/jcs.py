#!/usr/bin/env python3
"""RFC 8785 JSON Canonicalization Scheme (JCS) — Python side of P01/T003.

Implemented from the RFC text rather than by wrapping ``json.dumps(sort_keys=True)``,
because several JCS rules differ from Python's defaults:

* object properties sort by **UTF-16 code units**, not by code point;
* numbers serialise per ECMAScript ``Number::toString`` (so ``1e+30``, ``0.002``,
  ``-0`` becomes ``0``);
* strings keep non-ASCII characters **unescaped** and reject lone surrogates;
* duplicate property names, ``NaN``/``Infinity`` and invalid UTF-8 are rejected.

Checked against the RFC's own samples (see ``../vectors.json``).
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from typing import Any

__all__ = [
    "JCSError",
    "canonicalize_bytes",
    "canonicalize_text",
    "canonicalize_value",
    "sha256_hex",
]


class JCSError(ValueError):
    """A rejected input, carrying a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# parsing with I-JSON constraints
# --------------------------------------------------------------------------
def _reject_constant(name: str) -> Any:
    raise JCSError("non_finite", f"{name} is not permitted in JSON")


def _pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JCSError("duplicate_key", f"duplicate property name {key!r}")
        result[key] = value
    return result


def parse_bytes(raw: bytes) -> Any:
    """Parse UTF-8 JSON bytes, enforcing the I-JSON constraints JCS relies on."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JCSError("invalid_utf8", str(exc)) from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_pairs_hook,
            parse_constant=_reject_constant,
        )
    except JCSError:
        raise
    except json.JSONDecodeError as exc:
        raise JCSError("invalid_json", str(exc)) from exc


# --------------------------------------------------------------------------
# ECMAScript number serialisation (ECMA-262 7.1.12.1, "Note 2")
# --------------------------------------------------------------------------
def format_number(value: float) -> str:
    """Serialise a finite IEEE-754 double exactly as ECMAScript does."""
    if math.isnan(value) or math.isinf(value):
        raise JCSError("non_finite", "NaN and Infinity are not permitted in JSON")
    if value == 0:
        return "0"  # covers -0.0
    if value < 0:
        return "-" + format_number(-value)

    # Shortest round-trip digits. repr() is shortest, but may use either the
    # plain or the exponent form; normalise both into (digits, n) where
    # value == digits * 10 ** (n - len(digits)).
    text = repr(float(value))
    mantissa, _, exponent = text.partition("e")
    exp = int(exponent) if exponent else 0
    int_part, _, frac_part = mantissa.partition(".")
    digits = (int_part + frac_part).lstrip("0")
    leading_zeros = len(int_part + frac_part) - len(digits)
    n = len(int_part) - leading_zeros + exp
    digits = digits.rstrip("0") or "0"
    k = len(digits)

    if 0 < n <= 21:
        if k <= n:
            return digits + "0" * (n - k)
        return digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + digits
    sign = "+" if n - 1 >= 0 else "-"
    body = digits[0] if k == 1 else digits[0] + "." + digits[1:]
    return f"{body}e{sign}{abs(n - 1)}"


# --------------------------------------------------------------------------
# string serialisation
# --------------------------------------------------------------------------
_SHORT_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
}


def format_string(value: str) -> str:
    out: list[str] = ['"']
    for char in value:
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            raise JCSError("invalid_unicode", f"lone surrogate U+{code:04X} in string")
        if code < 0x20:
            out.append(_SHORT_ESCAPES.get(code, f"\\u{code:04x}"))
        elif char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


# --------------------------------------------------------------------------
# canonicalisation
# --------------------------------------------------------------------------
def _utf16_sort_key(name: str) -> bytes:
    # UTF-16-BE byte comparison is order-equivalent to UTF-16 code-unit compare.
    return name.encode("utf-16-be")


def canonicalize_value(value: Any) -> str:
    """Serialise an already-parsed value into canonical JSON text."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return format_string(value)
    if isinstance(value, bool):  # pragma: no cover - handled above
        return "true" if value else "false"
    if isinstance(value, int):
        return format_number(float(value))
    if isinstance(value, float):
        return format_number(value)
    if isinstance(value, list):
        return "[" + ",".join(canonicalize_value(item) for item in value) + "]"
    if isinstance(value, dict):
        rendered: dict[str, str] = {}
        for key in value:
            if not isinstance(key, str):
                raise JCSError("invalid_json", "object property names must be strings")
            # Validate before sorting: a lone surrogate must be rejected with a
            # JCSError, not blow up inside the UTF-16 sort key.
            rendered[key] = format_string(key)
        ordered = sorted(value, key=_utf16_sort_key)
        return (
            "{"
            + ",".join(
                f"{rendered[key]}:{canonicalize_value(value[key])}" for key in ordered
            )
            + "}"
        )
    raise JCSError("invalid_json", f"unsupported value type {type(value).__name__}")


def canonicalize_text(text: str) -> str:
    return canonicalize_value(parse_bytes(text.encode("utf-8")))


def canonicalize_bytes(raw: bytes) -> bytes:
    return canonicalize_bytes_inner(raw)


def canonicalize_bytes_inner(raw: bytes) -> bytes:
    return canonicalize_value(parse_bytes(raw)).encode("utf-8")


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="JCS canonicalizer (RFC 8785)")
    parser.add_argument("path", nargs="?", help="input JSON file; stdin when omitted")
    args = parser.parse_args(argv)

    if args.path:
        from pathlib import Path

        raw = Path(args.path).read_bytes()
    else:
        raw = sys.stdin.buffer.read()

    try:
        canonical = canonicalize_value(parse_bytes(raw))
    except JCSError as exc:
        json.dump({"error": exc.code, "message": exc.message}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 1
    payload = canonical.encode("utf-8")
    json.dump(
        {"canonical": canonical, "sha256": sha256_hex(payload), "length": len(payload)},
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
