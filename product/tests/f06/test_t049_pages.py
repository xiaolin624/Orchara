"""F04A/T049: the apps page offers "从销售模板创建" and its request helper is
executed under Node.

The structural assertions inspect what the browser receives from the compiled
API. ``app.js`` is then executed under Node with the same public helpers the page
uses, so the instantiate request path/method/body is observed behaviour rather
than source text.

The DOM/browser boundary is the same as every other page ticket: ``not_run`` (no
browser driver); the helper is pure and Node-tested.
"""

from __future__ import annotations

import json
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


def test_apps_page_offers_the_sales_template_action(f06_stack) -> None:
    response = f06_stack.server.http.get("/apps")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="create-from-template"',
        "从销售模板创建",
        "salesTemplateInstantiateRequest",
        "F04A/T049",
        "F02B",
        "/assets/app.js",
    ):
        assert marker in body, f"apps.html is missing {marker!r}"
    # The action copies a private draft; it must not claim to publish a version.
    assert "共享版本属于 F02B" in body

    served = f06_stack.server.http.get("/assets/app.js")
    assert served.status == 200, served.text
    assert "function salesTemplateInstantiateRequest(" in served.text, served.text


def test_debug_page_is_served_for_the_copied_application(f06_stack) -> None:
    response = f06_stack.server.http.get(
        "/apps/11111111-1111-4111-8111-111111111111/debug"
    )
    assert response.status == 200, response.text
    body = response.text
    assert "/assets/debug.js" in body
    assert "调试" in body


def test_sales_template_request_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "t049-app-template.js"
    harness.write_text(
        r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const context = { console, window: { location: { assign() {} } } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(__WEB_DIR__ + '/assets/app.js', 'utf8'), context);

// The public path encodes the template id and never accepts a URL.
assert.strictEqual(
  context.salesTemplateInstantiateRequest('sales').path,
  '/api/v1/templates/sales/applications',
);
const request = context.salesTemplateInstantiateRequest('sales');
assert.strictEqual(request.path, '/api/v1/templates/sales/applications');
assert.strictEqual(request.method, 'POST');
assert.deepStrictEqual(Object.keys(request.body), []);
assert.strictEqual(
  context.salesTemplateInstantiateRequest('sales/../x').path,
  '/api/v1/templates/sales%2F..%2Fx/applications',
);
assert.strictEqual(
  context.salesTemplateInstantiateRequest(undefined).path,
  '/api/v1/templates/sales/applications',
);

// An empty template id is refused instead of producing a collection URL.
assert.throws(() => context.salesTemplateInstantiateRequest('   '), /template id/);

process.stdout.write('app.js template request behaviour ok\n');
""".replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, (
        "app.js template request helper did not behave as required:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "app.js template request behaviour ok" in completed.stdout, completed.stdout


def test_run_pages_expose_report_download_and_stub_label(f06_stack) -> None:
    runs = f06_stack.server.http.get("/runs")
    assert runs.status == 200, runs.text
    assert 'id="node-results-panel"' in runs.text, runs.text
    assert "stub 标记" in runs.text, runs.text

    runs_js = f06_stack.server.http.get("/assets/runs.js")
    assert runs_js.status == 200, runs_js.text
    for marker in ("stubLabel", "artifactDownloadPath", "artifactContentPath"):
        assert marker in runs_js.text, f"runs.js is missing {marker!r}"
