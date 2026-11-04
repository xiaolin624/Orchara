"""T039: the admin model page and its client helpers (served + Node-executed).

The structural assertions are about what the browser actually receives from the
compiled API. ``models.js`` is then executed under Node with the same public
functions the page calls, so the registration payload, the "cannot be used by a
shared application" warning and the report projection are observed behaviour
rather than source text.

Still ``not_run``: a DOM/browser driving the page (same boundary as T023/T027/
T029/T031/T032/T033/T037).
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


def test_the_models_page_is_served_with_register_list_and_report_sections(f06_stack) -> None:
    response = f06_stack.server.http.get("/models")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="register-card"',
        'id="register-form"',
        'id="model-name"',
        'id="model-provider"',
        'id="model-id"',
        'id="model-endpoint"',
        'id="model-credential"',
        'id="model-destination"',
        'id="model-config"',
        'id="models-list"',
        'id="report-card"',
        "/assets/models.js",
        "不能进入共享应用",
        "manifest_sha256",
    ):
        assert marker in body, f"models.html is missing {marker!r}"
    assert '<form id="register-form" method="post">' in body
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "object_key" not in body


def test_models_js_is_served_with_the_client_helpers(f06_stack) -> None:
    response = f06_stack.server.http.get("/assets/models.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function modelsPath(",
        "function modelPath(",
        "function modelValidatePath(",
        "function modelEndpointsPath(",
        "function credentialRefsPath(",
        "function buildRegisterBody(",
        "function describeModel(",
        "function describeReport(",
        "function describeValidation(",
        "function canRegister(",
        "function validateKey(",
    ):
        assert marker in body, f"models.js is missing {marker!r}"
    assert "object_key" not in body
    assert "localStorage" not in body


def test_admin_page_links_to_the_model_page(f06_stack) -> None:
    response = f06_stack.server.http.get("/admin")
    assert response.status == 200, response.text
    assert 'href="/models"' in response.text


def test_models_js_behaviour_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "models-page.js"
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
vm.runInContext(fs.readFileSync(webDir + '/assets/models.js', 'utf8'), context);

// Paths are built from the id, never from a caller URL.
assert.strictEqual(context.modelsPath(), '/api/v1/models');
assert.strictEqual(context.modelPath('m 1/x'), '/api/v1/models/m%201%2Fx');
assert.strictEqual(context.modelValidatePath('m1'), '/api/v1/models/m1/validate');
assert.strictEqual(context.modelEndpointsPath(), '/api/v1/admin/model-endpoints');
assert.strictEqual(context.credentialRefsPath(), '/api/v1/admin/credential-refs');
assert.strictEqual(
  context.evaluationReportPath('r1'),
  '/api/v1/evaluation-reports/r1',
);

// The register body is the exact ModelInput; references come from the catalog.
const built = context.buildRegisterBody({
  name: ' candidate one ',
  provider: 'stub',
  modelId: 'stub-model-1',
  endpointRef: 'stub-endpoint',
  secretRef: 'stub-credential',
  dataDestination: 'local-stub',
  config: '{"kind":"generation","protocol":"chat_compatible","context_window":8192,"max_output_tokens":1024,"timeout_ms":30000,"price":{"currency":"USD","input_per_million":"0","output_per_million":"0"}}',
});
assert.strictEqual(built.ok, true, built.error);
assert.deepStrictEqual(Object.keys(built.body).sort(), [
  'config', 'data_destination', 'endpoint_ref', 'model_id', 'name',
  'provider', 'secret_ref',
]);
assert.strictEqual(built.body.name, 'candidate one');
assert.strictEqual(built.body.endpoint_ref, 'stub-endpoint');
assert.strictEqual(built.body.config.timeout_ms, 30000);

const rejected = context.buildRegisterBody({ name: 'x', provider: 'stub', modelId: 'm', endpointRef: '', secretRef: 'c', dataDestination: 'd', config: '{}' });
assert.strictEqual(rejected.ok, false);
assert.ok(rejected.error.includes('endpoint'));

// A pending model says it cannot be used by a shared application.
const pending = context.describeModel({
  id: 'm1', name: 'candidate', model_id: 'stub-model-1',
  validation_state: 'pending', data_destination: 'local-stub',
  evaluation_report_id: null,
});
assert.strictEqual(pending.usable, false);
assert.ok(pending.warning.includes('不能被共享应用引用'), pending.warning);
assert.strictEqual(pending.stateText, '待验证');

const passed = context.describeModel({
  id: 'm2', name: 'candidate', model_id: 'stub-model-1',
  validation_state: 'passed', data_destination: 'local-stub',
  evaluation_report_id: 'r1',
});
assert.strictEqual(passed.usable, true);
assert.strictEqual(passed.warning, '');
assert.strictEqual(passed.reportPath, '/api/v1/evaluation-reports/r1');

// The report projection shows the fixed profile, manifest and checks.
const report = context.describeReport({
  report_version: 1, kind: 'model_compatibility',
  model_profile_version_id: 'm1', manifest_sha256: 'a'.repeat(64),
  call_id: 'c1', fixture_artifact_id: 'f1', fixture_sha256: 'b'.repeat(64),
  check: { name: 'structured_output', passed: false, reason: 'structured_output' },
  compatible: false, provider: 'stub', stub: true,
});
assert.strictEqual(report.ok, true);
assert.strictEqual(report.status, 'failed');
assert.strictEqual(report.compatible, false);
assert.strictEqual(report.modelProfileVersionId, 'm1');
assert.strictEqual(report.manifestSha256, 'a'.repeat(64));
assert.strictEqual(report.checkPassed, false);
assert.ok(report.checkText.includes('structured_output'));
assert.ok(report.checkText.includes('未通过'));

assert.strictEqual(context.canRegister({ roles: ['admin'] }), true);
assert.strictEqual(context.canRegister({ roles: ['creator'] }), false);
assert.strictEqual(context.canValidate({ roles: ['admin'] }), true);

process.stdout.write('models.js page behaviour ok\n');
""".replace("__WEB_DIR__", repr(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, (
        f"node failed:\n{completed.stdout}\n{completed.stderr}"
    )
    assert "models.js page behaviour ok" in completed.stdout
