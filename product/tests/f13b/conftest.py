"""F13B/T102 corpus tests: offline only.

These tests freeze the internal evaluation corpus. They need the product root
(``contracts/``, ``tools/``) and the tests root (the F13A independent sales
reference) importable; they never start PostgreSQL, the real stack, a sandbox or
a model.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TESTS_ROOT.parent

for entry in (str(PRODUCT_ROOT / "tools"), str(TESTS_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)
