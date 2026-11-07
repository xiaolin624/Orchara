"""Credential-leak regression tests for the F12 evidence files.

The review found a live one-time API key and an ``x-connector-secret`` header
value committed under ``reports/f12/``. The fix is at the source (the T100/T101
recorders and the gate's report builder); this test independently scans the
committed JSON so a future recorder regression fails the suite.

The first test is deliberately written against a planted sample so the scanner
itself is proven able to fail; the third test is the red→green evidence for the
real leak (it failed on ``integration/f12`` before the evidence regeneration).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = PRODUCT_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from evidence_redaction import (  # noqa: E402
    find_violations,
    redact_text,
    redact_value,
)

REPORTS = PRODUCT_ROOT / "reports" / "f12"
PLANTED_KEY = "orchara_" + "gATwXXrqGSnsivZO-TKlZhrsVT-hexcFHkVyO5Upop4"
PLANTED_CONNECTOR = "t101-connector-secret-5c2e8a"


def test_the_scanner_detects_the_planted_leak_shapes() -> None:
    """The scanner must go red on exactly the shapes that leaked."""
    sample = json.dumps(
        {
            "detail": {
                "secret": PLANTED_KEY,
                "headers": {"x-connector-secret": PLANTED_CONNECTOR},
            }
        }
    )
    rules = {violation["rule"] for violation in find_violations(sample)}
    assert "api-key" in rules, sample
    assert "connector-header" in rules, sample
    assert "credential-member" in rules, sample


def test_redaction_removes_every_planted_leak_shape() -> None:
    sample = json.dumps(
        {
            "id": "65108fc8-d105-47a2-9ddb-3908b1f0bcda",
            "secret": PLANTED_KEY,
            "headers": {
                "x-connector-secret": PLANTED_CONNECTOR,
                "authorization": "Bearer session-token-value",
                "cookie": "platform_session=session-token-value",
            },
        }
    )
    redacted = redact_text(sample)
    assert find_violations(redacted) == [], redacted
    # A JSON member named ``secret`` is redacted whole (the stricter rule); a
    # bare key in free text keeps the documented prefix shape.
    assert '"secret": "<redacted>"' in redacted
    assert "orchara_<redacted>" in redact_text("key=" + PLANTED_KEY)
    # Idempotence: a second pass must not change the result.
    assert redact_text(redacted) == redacted

    # The escaped-JSON detail form (a body embedded in a string) is covered too.
    escaped = json.dumps({"detail": sample})
    assert find_violations(redact_text(escaped)) == []


def test_recursive_redaction_covers_nested_members_and_keeps_shape() -> None:
    payload = {
        "secret": PLANTED_KEY,
        "api_key": "another-value",
        "facts": {"activation_token": "token-value", "safe": "orchara-db-name"},
        "headers": [{"x-api-key": "key-value"}, {"authorization": "Bearer abcdefgh"}],
    }
    redacted = redact_value(payload)
    assert redacted["secret"] == "<redacted>"
    assert redacted["api_key"] == "<redacted>"
    assert redacted["facts"]["activation_token"] == "<redacted>"
    assert redacted["facts"]["safe"] == "orchara-db-name"
    assert find_violations(json.dumps(redacted)) == []


def test_committed_f12_evidence_contains_no_credential_shapes() -> None:
    offenders: list[str] = []
    files = sorted(REPORTS.glob("*.json"))
    assert files, "no F12 evidence JSON files exist"
    for path in files:
        text = path.read_text(encoding="utf-8")
        for violation in find_violations(text):
            offenders.append(f"{path.name}: {violation['rule']}: {violation['match']}")
    assert offenders == [], "credential shapes in committed evidence:\n" + "\n".join(offenders)
