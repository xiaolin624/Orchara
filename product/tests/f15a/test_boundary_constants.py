"""T053/F15A: the probe's numbers and vocabulary are pinned to the product.

A probe that invents its own size cap or assertion ids would drift away from the
platform it is supposed to qualify. These tests read the real sources:

* `internal/objectstore/objectstore.go` — `MaxObjectBytes`;
* `contracts/openapi.json` — `size_bytes.maximum`;
* the probe's own constant and assertion catalogue.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
PROBE = PRODUCT_ROOT / "probes" / "f15a_objectstore" / "object_store_probe.py"
ASSERTIONS = PRODUCT_ROOT / "probes" / "f15a_objectstore" / "f15a_probe" / "assertions.py"
GO_STORE = PRODUCT_ROOT / "internal" / "objectstore" / "objectstore.go"
GO_PROBE = PRODUCT_ROOT / "probes" / "f15a_objectstore" / "localfs_probe" / "main.go"
CONTRACT = PRODUCT_ROOT / "contracts" / "openapi.json"

EXPECTED_ASSERTIONS = [
    "anonymous_read_denied",
    "anonymous_put_denied",
    "anonymous_list_denied",
    "out_of_range_key_denied",
    "sealed_put_denied",
    "staging_overwrite_preserves_sealed",
    "short_lived_signature_expiry",
    "proxy_download_boundary",
    "delete_semantics",
    "versions_immutable",
]


def test_product_cap_is_the_same_number_everywhere() -> None:
    go_source = GO_STORE.read_text(encoding="utf-8")
    match = re.search(r"MaxObjectBytes\s+int64\s*=\s*(\d+)\s*<<\s*(\d+)", go_source)
    assert match, "internal/objectstore.MaxObjectBytes definition not found"
    go_value = int(match.group(1)) << int(match.group(2))

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    maxima = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "size_bytes" and isinstance(value, dict) and "maximum" in value:
                    maxima.add(value["maximum"])
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(contract)
    assert maxima == {20 * 1024 * 1024}, maxima

    python_source = ASSERTIONS.read_text(encoding="utf-8")
    match = re.search(r"MAX_OBJECT_BYTES\s*=\s*(\d+)\s*<<\s*(\d+)", python_source)
    assert match, "probe MAX_OBJECT_BYTES definition not found"
    python_value = int(match.group(1)) << int(match.group(2))

    assert go_value == 20 * 1024 * 1024 == python_value


def test_go_analogue_uses_the_product_package_not_a_copy() -> None:
    source = GO_PROBE.read_text(encoding="utf-8")
    assert '"orchara/platform/internal/objectstore"' in source
    assert "objectstore.MaxObjectBytes" in source
    # The Go analogue must not carry its own cap.
    assert not re.search(r"const\s+\w*MaxObjectBytes\s*=", source)


def test_assertion_catalogue_is_exactly_the_ticket_vocabulary() -> None:
    sys.path.insert(0, str(PROBE.parent))
    from f15a_probe import assertions  # noqa: PLC0415

    ids = [function.__name__ for function in assertions.ASSERTIONS]
    assert ids == EXPECTED_ASSERTIONS
    assert list(assertions.ASSERTION_TITLES) == EXPECTED_ASSERTIONS


def test_cli_exposes_no_credential_arguments() -> None:
    completed = subprocess.run(
        [sys.executable, str(PROBE), "--help"],
        cwd=PRODUCT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert completed.returncode == 0
    help_text = completed.stdout.lower()
    for forbidden in ("--secret-key", "--access-key", "--session-token"):
        assert forbidden not in help_text
    for expected in (
        "--out",
        "--emit",
        "--timeout",
        "--short-ttl",
        "--expiry-skew",
        "--endpoint",
        "--bucket",
        "--tenant-prefix",
    ):
        assert expected in help_text
    assert "F15A_S3_SECRET_KEY" in completed.stdout
