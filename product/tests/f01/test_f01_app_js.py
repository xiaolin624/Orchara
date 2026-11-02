"""Execute the shared page JavaScript instead of asserting substrings only.

Until this file, the page tests proved that ``assets/app.js`` and the inline
``<script>`` blocks *look* right, but no JavaScript engine ever ran them. That is
now split in two:

* the shared helper (``assets/app.js``) is executed under Node with a stubbed
  ``fetch``: success JSON, the contract error envelope, a non-JSON error body,
  the request options (``X-CSRF-Token``/``Content-Type``/``credentials``/
  ``redirect``), ``escapeHTML``, the cached ``identity()``/``csrf()`` and
  ``logout()`` are all observed as behaviour, not as source text;
* every inline ``<script>`` block of the five served pages is parsed by
  ``node --check`` after extraction to a temporary file.

What is still not run is a DOM/browser: there is no headless browser driving the
forms, so the structural page tests in ``test_t015_pages.py``/``test_t017_pages.py``/
``test_t019_pages.py``/``test_t020_pages.py``/``test_t021_pages.py`` remain. That
limitation is recorded in the T017 report.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from conftest import WEB_DIR

NODE = shutil.which("node")
APP_JS = WEB_DIR / "assets" / "app.js"

INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)

PAGES = ("login.html", "admin.html", "activate.html", "apps.html", "index.html")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip(
            "node is not available on PATH; app.js execution and inline-script "
            "syntax checks cannot run (the structural page tests still do)"
        )
    return NODE


APP_JS_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const appPath = %s;
const source = fs.readFileSync(appPath, 'utf8');

function load(fetchImpl) {
  const calls = [];
  const context = {
    fetch: async (path, init) => {
      calls.push({ path, init });
      return fetchImpl(path, init);
    },
    window: { location: { assign() {} } },
    console,
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: appPath });
  return { context, calls };
}

function jsonResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
  };
}

function textResponse(status, body) {
  return { ok: status >= 200 && status < 300, status, text: async () => body };
}

async function expectApiError(promise, expected) {
  try {
    await promise;
  } catch (error) {
    for (const [key, value] of Object.entries(expected)) {
      assert.strictEqual(error[key], value, key + ': ' + error[key] + ' !== ' + value);
    }
    return error;
  }
  throw new Error('expected the api call to fail');
}

(async () => {
  // Success JSON is parsed; a plain read carries no body and no CSRF header.
  {
    const { context, calls } = load(async () => jsonResponse(200, { items: [] }));
    const payload = await context.api('/api/v1/applications');
    assert.strictEqual(JSON.stringify(payload), '{"items":[]}');
    assert.strictEqual(calls.length, 1);
    assert.strictEqual(calls[0].path, '/api/v1/applications');
    const init = calls[0].init;
    assert.strictEqual(init.method, 'GET');
    assert.strictEqual(init.headers.Accept, 'application/json');
    assert.strictEqual(init.credentials, 'same-origin');
    assert.strictEqual(init.redirect, 'error');
    assert.strictEqual(init.cache, 'no-store');
    assert.strictEqual(init.body, undefined);
    assert.strictEqual(init.headers['Content-Type'], undefined);
    assert.strictEqual(init.headers['X-CSRF-Token'], undefined);
  }

  // The contract error envelope becomes a thrown error with code/status/requestId.
  {
    const { context } = load(async () => jsonResponse(403, {
      error: {
        code: 'forbidden',
        message: 'admin role is required',
        request_id: 'req-42',
      },
    }));
    await expectApiError(context.api('/api/v1/admin/users'), {
      code: 'forbidden',
      status: 403,
      requestId: 'req-42',
      message: 'admin role is required',
    });
  }

  // A non-JSON error body falls back to http_<status>, not to a parse error.
  {
    const { context } = load(async () => textResponse(502, '<html>bad gateway</html>'));
    await expectApiError(context.api('/api/status'), {
      code: 'http_502',
      status: 502,
      requestId: null,
      message: 'HTTP 502',
    });
  }

  // A write sends JSON, the CSRF header and the fixed request options.
  {
    const { context, calls } = load(async () => jsonResponse(201, { id: 'g1', state: 'granted' }));
    const payload = await context.api('/api/v1/admin/grants', {
      method: 'POST',
      body: { principal_id: 'p1' },
      csrf: 'csrf-token-value',
    });
    assert.strictEqual(JSON.stringify(payload), '{"id":"g1","state":"granted"}');
    const init = calls[0].init;
    assert.strictEqual(init.method, 'POST');
    assert.strictEqual(init.headers['Content-Type'], 'application/json');
    assert.strictEqual(init.headers['X-CSRF-Token'], 'csrf-token-value');
    assert.strictEqual(init.body, JSON.stringify({ principal_id: 'p1' }));
    assert.strictEqual(init.credentials, 'same-origin');
    assert.strictEqual(init.redirect, 'error');
  }

  // escapeHTML covers all five replacements and the nullish cases.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    assert.strictEqual(
      context.escapeHTML('& < > " \''),
      '&amp; &lt; &gt; &quot; &#39;',
    );
    assert.strictEqual(context.escapeHTML(null), '');
    assert.strictEqual(context.escapeHTML(undefined), '');
    assert.strictEqual(context.escapeHTML(0), '0');
  }

  // identity() caches exactly one /auth/me read; csrf() returns its token.
  {
    const { context, calls } = load(async (path) => {
      assert.strictEqual(path, '/api/v1/auth/me');
      return jsonResponse(200, { principal_id: 'p1', csrf_token: 'csrf-1' });
    });
    const first = await context.identity();
    const second = await context.identity();
    assert.strictEqual(first, second);
    assert.strictEqual(calls.length, 1);
    assert.strictEqual(await context.csrf(), 'csrf-1');
    assert.strictEqual(calls.length, 1);
  }

  // logout() posts the CSRF token taken from the cached identity.
  {
    const { context, calls } = load(async (path) => {
      if (path === '/api/v1/auth/me') {
        return jsonResponse(200, { principal_id: 'p1', csrf_token: 'csrf-2' });
      }
      if (path === '/api/v1/auth/logout') {
        return textResponse(204, '');
      }
      throw new Error('unexpected path ' + path);
    });
    assert.strictEqual(await context.logout(), true);
    const logoutCall = calls.find((call) => call.path === '/api/v1/auth/logout');
    assert.ok(logoutCall, 'logout did not POST /api/v1/auth/logout');
    assert.strictEqual(logoutCall.init.method, 'POST');
    assert.strictEqual(logoutCall.init.headers['X-CSRF-Token'], 'csrf-2');
    assert.strictEqual(logoutCall.init.credentials, 'same-origin');
  }

  process.stdout.write('app.js behaviour ok\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
"""


def test_app_js_is_executed_under_node_with_a_stubbed_fetch(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "app-js-harness.js"
    harness.write_text(APP_JS_HARNESS % json.dumps(str(APP_JS)), encoding="utf-8")
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, (
        "assets/app.js did not behave as the pages require:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "app.js behaviour ok" in completed.stdout, completed.stdout


@pytest.mark.parametrize("page", PAGES)
def test_inline_scripts_parse_with_node(
    node_binary: str, tmp_path, page: str
) -> None:
    html = (WEB_DIR / page).read_text(encoding="utf-8")
    blocks = INLINE_SCRIPT.findall(html)
    assert blocks, f"{page} has no inline script block"

    for index, block in enumerate(blocks):
        script = tmp_path / f"{page}.{index}.js"
        script.write_text(block, encoding="utf-8")
        completed = subprocess.run(
            [node_binary, "--check", str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert completed.returncode == 0, (
            f"{page} inline script #{index} failed node --check:\n"
            f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )
