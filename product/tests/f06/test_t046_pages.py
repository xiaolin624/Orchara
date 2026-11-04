"""F06/T046: the decimal budget projection on the runs page.

The structural assertions inspect what the browser actually receives from the
compiled API. ``runs.js`` is then executed under Node with the same public helper
the page calls: amounts are displayed verbatim from the ledger strings and an
overrun is visibly not a clean success. The DOM/browser driving boundary remains
``not_run`` (no browser driver), exactly like the other page tickets.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from support import stack as st
from support.fixtures import f06_stack  # noqa: F401 - pytest fixture

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_runs_page_has_the_budget_projection(f06_stack) -> None:
    response = f06_stack.server.http.get("/runs")
    assert response.status == 200, response.text
    body = response.text
    assert 'id="capability-panel"' in body
    assert 'id="budget-status"' in body
    assert "F06/T046" in body
    assert "超支" in body


def test_runs_js_has_the_budget_helpers(f06_stack) -> None:
    response = f06_stack.server.http.get("/assets/runs.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function describeRunBudget(",
        "function budgetStatus(",
        "reserved_amount",
        "remaining_amount",
        "overrun",
        "budget-status",
    ):
        assert marker in body, f"runs.js is missing {marker!r}"


def test_budget_helpers_execute_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "capability-budget-page.js"
    harness.write_text(
        r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = { console, window: { location: { assign() {} } } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/runs.js', 'utf8'), context);

// A normal budget: the exact decimal strings are displayed verbatim, never
// re-computed with binary floating point.
const normal = context.describeRunBudget({
  items: [],
  budget: {
    budget_limit: '1.00000000',
    reserved_amount: '0.00823100',
    charged_amount: '0.00048000',
    remaining_amount: '0.99128900',
    currency: 'USD',
    overrun: false,
  },
});
assert.strictEqual(normal.known, true);
assert.strictEqual(normal.overrun, false);
assert.strictEqual(normal.limit, '1.00000000');
assert.strictEqual(normal.reserved, '0.00823100');
assert.strictEqual(normal.charged, '0.00048000');
assert.strictEqual(normal.remaining, '0.99128900');
assert.ok(normal.text.includes('预算 1.00000000 USD'), normal.text);
assert.ok(normal.text.includes('已结算 0.00048000'), normal.text);
assert.ok(!normal.text.includes('超支'), normal.text);

// An overrun is flagged by the server projection, never hidden.
const overrun = context.describeRunBudget({
  budget: {
    budget_limit: '0.01000000',
    reserved_amount: '0.00000000',
    charged_amount: '0.01050000',
    remaining_amount: '0.00000000',
    currency: 'USD',
    overrun: true,
  },
});
assert.strictEqual(overrun.overrun, true);
assert.ok(overrun.text.includes('已超支'), overrun.text);

// An older/partial body is shown as unavailable instead of inventing a budget.
const missing = context.describeRunBudget({ items: [] });
assert.strictEqual(missing.known, false);
assert.strictEqual(missing.overrun, false);
assert.ok(missing.text.includes('不可用'), missing.text);

// The existing operation projection still renders beside the budget.
const ledger = context.describeCapabilityLedger({ items: [{ id: 'op', attempts: 1 }] });
assert.strictEqual(ledger.count, 1);
assert.ok(ledger.text.includes('1'), ledger.text);

process.stdout.write('budget page behaviour ok\n');
""".replace(
            "__WEB_DIR__", repr(str(st.WEB_DIR))
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "budget page behaviour ok" in completed.stdout
