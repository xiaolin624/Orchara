"""F08/T077: the catalog page and its Node-executable helpers.

Structural assertions are about what the real compiled API serves; the second test
executes ``catalog.js`` under Node with the same bytes a browser receives. No DOM is
driven: that is the same boundary T023/T027/T033/T074 use for page logic.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from support import stack as st

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_the_catalog_page_is_served_with_the_real_surfaces(f08_stack) -> None:
    response = f08_stack.server.http.get("/catalog")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="catalog-search-form"',
        'id="catalog-query"',
        'id="catalog-search-submit"',
        'id="catalog-retrieval"',
        'id="catalog-status"',
        'id="catalog-count"',
        'id="catalog-results"',
        'id="catalog-empty"',
        'id="catalog-next"',
        "用途目录",
        "回退检索（索引落后）",
        "/assets/catalog.js",
    ):
        assert marker in body, f"catalog.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "innerHTML" not in body

    asset = f08_stack.server.http.get("/assets/catalog.js")
    assert asset.status == 200, asset.text
    script = asset.text
    for marker in (
        "function catalogSearchPath(",
        "function catalogCard(",
        "function catalogVersionLabel(",
        "function describeCatalogHit(",
        "function describeRetrieval(",
        "function describeCatalogEmpty(",
        "function describeCatalogCount(",
        "function catalogSubmitTarget(",
        "function catalogSearchRequest(",
        "/api/v1/catalog/search",
    ):
        assert marker in script, f"catalog.js is missing {marker!r}"
    for forbidden in ("object_key", "ORCHARA_", "secret", "BEGIN PRIVATE"):
        assert forbidden not in script, forbidden

    # The existing applications page links to the new surface and keeps its own
    # contract-shaped list untouched.
    apps = f08_stack.server.http.get("/apps")
    assert apps.status == 200, apps.text
    assert 'href="/catalog"' in apps.text


def test_catalog_js_helpers_execute_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "catalog-helpers.js"
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
vm.runInContext(fs.readFileSync(webDir + '/assets/catalog.js', 'utf8'), context);

// The path is built only by the helper; both values are always encoded.
assert.strictEqual(context.catalogSearchPath('', null), '/api/v1/catalog/search');
assert.strictEqual(context.catalogSearchPath('sales', null), '/api/v1/catalog/search?q=sales');
assert.strictEqual(
  context.catalogSearchPath('销售 报表', null),
  '/api/v1/catalog/search?q=' + encodeURIComponent('销售 报表'));
const withCursor = context.catalogSearchPath('a b/c', 's1|0|2|x y');
assert.ok(withCursor.indexOf('q=a%20b%2Fc') >= 0, withCursor);
assert.ok(withCursor.indexOf('cursor=s1%7C0%7C2%7Cx%20y') >= 0, withCursor);
assert.strictEqual(context.catalogSearchRequest('sales').path, '/api/v1/catalog/search?q=sales');
assert.strictEqual(context.catalogSearchRequest('sales').method, 'GET');

// One hit is projected from the six-member card only; nothing is invented.
const item = {
  application_id: 'a1', application_name: '销售助手', version_id: 'v1', version_no: 3,
  manifest_sha256: 'f'.repeat(64),
  capability_card: {
    purpose: '汇总销售报表', includes: ['聚合'], excludes: ['外部写入'],
    required_inputs: ['销售 csv'], expected_outputs: ['report'],
    examples: ['月度报表', '周报'],
  },
};
assert.strictEqual(context.catalogVersionLabel(item), 'v3');
assert.strictEqual(context.catalogVersionLabel({ version_no: 0 }), '版本号未知');
const described = context.describeCatalogHit(item);
assert.strictEqual(described.title, '销售助手 · v3');
assert.strictEqual(described.versionLabel, 'v3');
assert.strictEqual(described.purpose, '汇总销售报表');
assert.strictEqual(JSON.stringify(described.includes), JSON.stringify(['聚合']));
assert.strictEqual(JSON.stringify(described.requiredInputs), JSON.stringify(['销售 csv']));
assert.strictEqual(JSON.stringify(described.expectedOutputs), JSON.stringify(['report']));
assert.strictEqual(JSON.stringify(described.examples), JSON.stringify(['月度报表', '周报']));
assert.strictEqual(described.versionID, 'v1');
const missing = context.describeCatalogHit(null);
assert.strictEqual(missing.purpose, '');
assert.strictEqual(missing.versionLabel, '版本号未知');
assert.strictEqual(context.catalogSubmitTarget(item), '/chat?version=v1');
assert.strictEqual(context.catalogSubmitTarget({ version_id: 'a b/c' }), '/chat?version=a%20b%2Fc');
assert.strictEqual(context.catalogSubmitTarget(null), '/chat');

// Retrieval wording is the honesty boundary: an index page and a fallback page
// must never read the same.
const index = context.describeRetrieval({
  source: 'index', stale: false, index_state: 'ready',
  projection_epoch: 2, rechecked: 3, rejected_by_recheck: 0,
});
assert.strictEqual(index.label, '授权索引');
assert.strictEqual(index.stale, false);
assert.ok(index.explanation.indexOf('复核') >= 0, index.explanation);
const fallback = context.describeRetrieval({
  source: 'fallback', stale: true, index_state: 'rebuilding',
  projection_epoch: 2, rechecked: 3, rejected_by_recheck: 1,
});
assert.strictEqual(fallback.label, '回退检索（索引落后）');
assert.strictEqual(fallback.stale, true);
assert.strictEqual(fallback.rejectedByRecheck, 1);
assert.ok(fallback.explanation.indexOf('索引') >= 0, fallback.explanation);
const unknown = context.describeRetrieval(null);
assert.strictEqual(unknown.label, '未知检索来源');
assert.strictEqual(unknown.rechecked, 0);

// Empty/count text distinguishes "no match" from "never loaded".
assert.ok(
  context.describeCatalogEmpty({ source: 'fallback', stale: true }, 0).indexOf('回退检索') >= 0);
assert.ok(context.describeCatalogEmpty({ source: 'index', stale: false }, 0).indexOf('没有匹配') >= 0);
assert.strictEqual(context.describeCatalogEmpty({ source: 'index' }, 2), '');
assert.ok(context.describeCatalogCount(2, true).indexOf('还有下一页') >= 0);
assert.ok(context.describeCatalogCount(2, false).indexOf('还有下一页') < 0);
assert.strictEqual(context.describeCatalogCount(0, false), '');

process.stdout.write('catalog.js helper behaviour ok\n');
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
        "catalog.js helpers did not behave as the contract requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
