"""Credential redaction for F12 evidence and report output.

The review of ``integration/f12`` found a one-time API key and a connector
secret header value committed in ``reports/f12/T101-journeys.json``. The
credential must never reach a report again, so redaction lives at the sources
that produce evidence: ``tests/f12/t100_support.py`` (journey writer),
``tests/f12/t101_support.py`` (journey record/writer) and
``tools/f12_api_gate.py`` (conformance report). This module is shared by all
three; it is deliberately dependency-free so the gate can still run as a plain
script.

Rules (the minimum the review required):

* an API key of the documented shape (``orchara_`` + at least eight key
  characters) becomes ``orchara_<redacted>``;
* a JSON member whose name is a credential name (``secret``, ``token``,
  ``password``, ``api_key``, ``authorization``, ``cookie``, the ``x-...``
  header spellings, and the ``*_secret`` / ``*_token`` / ``*_password`` /
  ``*_api_key`` families) has its value replaced by ``<redacted>``;
* request-header values for ``x-connector-secret``, ``authorization``,
  ``cookie`` and ``x-api-key`` become ``<redacted>``, including when the header
  block is embedded as an escaped JSON string inside a detail field;
* ``Authorization: Bearer <credential>`` and ``platform_session=<value>``
  cookies are redacted as well.

Redaction is idempotent: ``orchara_<redacted>`` contains no character from the
key alphabet after the prefix, so running it twice is safe.
"""

from __future__ import annotations

import json
import re
from typing import Any

REDACTED = "<redacted>"
_NOT_JSON = object()

# The documented one-time secret prefix. Eight characters is the floor the
# review asked for; real keys are 43 characters after the prefix.
API_KEY_RE = re.compile(r"orchara_[A-Za-z0-9_\-]{8,}")
BEARER_RE = re.compile(r"(?i)\b(Bearer)(\s+)[A-Za-z0-9._~+/=\-]{8,}")
COOKIE_RE = re.compile(r"(?i)\b(platform_session|csrf_token)=([^;\s\"']+)")

# ``"secret": "value"`` and the escaped form ``\"secret\":\"value\"`` that
# appears when a JSON body is embedded in a detail string.
KEY_VALUE_RE = re.compile(
    r'(?P<quote>\\?")(?P<key>[A-Za-z0-9_.\-]+)(?P=quote)(?P<sep>\s*:\s*)'
    r'(?P<value>\\?"(?:[^"\\]|\\.)*\\?")'
)

# A plain ``x-connector-secret: value`` line (a header dump that is not JSON).
PLAIN_HEADER_RE = re.compile(
    r"(?i)\b(x-connector-secret|x-api-key|authorization|cookie)"
    r"(\s*:\s*)(?!\\?[\"'])([^\s,;\"']+)"
)

_SENSITIVE_NAMES = frozenset(
    {
        "secret",
        "secrets",
        "token",
        "password",
        "passwd",
        "api_key",
        "authorization",
        "cookie",
        "cookies",
        "set_cookie",
        "set-cookie",
        "platform_session",
        "x_connector_secret",
        "x_api_key",
        "x_csrf_token",
        "csrf_token",
    }
)
_SENSITIVE_SUFFIXES = (
    "_secret",
    "_token",
    "_password",
    "_passwd",
    "_api_key",
    "_authorization",
    "_cookie",
)


def is_sensitive_key(name: Any) -> bool:
    """Whether a JSON member/header name denotes a credential."""
    text = str(name or "").strip().lower().replace("-", "_")
    if not text:
        return False
    return text in _SENSITIVE_NAMES or text.endswith(_SENSITIVE_SUFFIXES)


def redact_text(text: str) -> str:
    """Redact credentials in one free-form string (idempotent).

    A detail field is usually a JSON document (or a JSON document embedded as a
    string). Parsing it first and redacting the structure is the only reliable
    way to follow escaped quotes; the regex passes below are the fallback for
    text that is not valid JSON.
    """
    if not isinstance(text, str) or not text:
        return text
    stripped = text.strip()
    if stripped[:1] in "[{\"":
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = _NOT_JSON
        if parsed is not _NOT_JSON:
            return json.dumps(redact_value(parsed), ensure_ascii=False)

    redacted = API_KEY_RE.sub("orchara_" + REDACTED, text)
    redacted = BEARER_RE.sub(lambda match: match.group(1) + match.group(2) + REDACTED, redacted)
    redacted = COOKIE_RE.sub(lambda match: match.group(1) + "=" + REDACTED, redacted)

    def replace_member(match: re.Match[str]) -> str:
        key = match.group("key")
        if not is_sensitive_key(key):
            return match.group(0)
        quote = match.group("quote")
        value = quote + REDACTED + quote
        return quote + key + quote + match.group("sep") + value

    redacted = KEY_VALUE_RE.sub(replace_member, redacted)
    redacted = PLAIN_HEADER_RE.sub(
        lambda match: match.group(1) + match.group(2) + REDACTED, redacted
    )
    return redacted


def redact_value(value: Any) -> Any:
    """Recursively redact a JSON-shaped value (dicts, lists, strings).

    The returned structure contains no credential value. Non-string scalars,
    numbers and booleans are unchanged; a credential-named member keeps its
    name so a reader can see that a value was deliberately withheld.
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            if is_sensitive_key(key):
                result[key] = REDACTED if item is not None else None
            else:
                result[key] = redact_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value


# A descriptive alias used by the journey writers.
redact_payload = redact_value


# Patterns the regression test uses to prove the committed evidence is clean.
LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api-key", re.compile(r"(?<![A-Za-z0-9_])orchara_[A-Za-z0-9_\-]{20,}")),
    (
        "connector-header",
        re.compile(
            r"(?i)\\?(?:\"|')?x-connector-secret\\?(?:\"|')?\s*:\s*"
            r"\\?(?:\"|')(?!<redacted>)"
        ),
    ),
    (
        "credential-member",
        re.compile(
            r'(?i)\\?"(secret|token|password|api_key|authorization|cookie|'
            r'x-api-key|x-connector-secret)\\?"\s*:\s*\\?"(?!<redacted>)'
        ),
    ),
    ("bearer", re.compile(r"(?i)\bBearer\s+(?!<redacted>)[A-Za-z0-9._~+/=\-]{8,}")),
    (
        "session-cookie",
        re.compile(r"(?i)\bplatform_session=(?!<redacted>)[^;\s\"']+"),
    ),
)


def find_violations(text: str) -> list[dict[str, Any]]:
    """Return every credential-shaped leak in ``text`` (empty means clean)."""
    violations: list[dict[str, Any]] = []
    for name, pattern in LEAK_PATTERNS:
        for match in pattern.finditer(text):
            violations.append({"rule": name, "match": match.group(0)[:160]})
    return violations
