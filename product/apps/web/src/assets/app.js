/*
 * Orchara web helper: plain JavaScript, no framework, no build step.
 *
 * `api` always sends JSON, never follows a redirect (a redirect on an auth
 * endpoint is a bug), and turns the contract error envelope
 * {"error":{"code","message","request_id"}} into a throwable carrying `code`,
 * so a page can show the API's own error code instead of "something failed".
 *
 * `escapeHTML` exists for the places a page has to build markup; prefer
 * textContent where possible.
 */
async function api(path, options = {}) {
  const method = options.method || 'GET';
  const headers = { Accept: 'application/json' };
  const init = { method, headers, credentials: 'same-origin', cache: 'no-store', redirect: 'error' };
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(options.body);
  }
  if (options.csrf) {
    headers['X-CSRF-Token'] = options.csrf;
  }

  const response = await fetch(path, init);
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (parseError) {
      payload = null;
    }
  }

  if (!response.ok) {
    const detail = (payload && payload.error) || {};
    const failure = new Error(detail.message || ('HTTP ' + response.status));
    failure.code = detail.code || ('http_' + response.status);
    failure.status = response.status;
    failure.requestId = detail.request_id || null;
    throw failure;
  }
  return payload;
}

function escapeHTML(value) {
  const replacements = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(value === undefined || value === null ? '' : value).replace(
    /[&<>"']/g,
    (character) => replacements[character],
  );
}
