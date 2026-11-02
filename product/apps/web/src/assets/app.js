/*
 * Orchara web helper: plain JavaScript, no framework, no build step.
 *
 * `api` always sends JSON, never follows a redirect (a redirect on an auth
 * endpoint is a bug), and turns the contract error envelope
 * {"error":{"code","message","request_id"}} into a throwable carrying `code`,
 * so a page can show the API's own error code instead of "something failed".
 *
 * `identity`, `csrf`, `requireLogin` and `logout` are the session helpers every
 * logged-in page uses:
 *
 *   - `identity()` asks GET /api/v1/auth/me and caches the answer for this page,
 *     which is what makes a refresh work: the HttpOnly session cookie is all the
 *     browser keeps, nothing is remembered in the page or in storage;
 *   - `csrf()` returns the token from that identity. It is sent as the
 *     X-CSRF-Token header on every unsafe request, never in a URL;
 *   - `requireLogin()` redirects to /login when the session is gone;
 *   - `logout()` posts /api/v1/auth/logout with the CSRF token and clears the
 *     cached identity.
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

let identityCache = null;

/**
 * The current identity from GET /api/v1/auth/me, cached per page load.
 *
 * `force` re-reads it (after sign-in, for example). A failed read clears the
 * cache so a later call retries instead of replaying a stale rejection.
 */
async function identity(force = false) {
  if (force || !identityCache) {
    identityCache = api('/api/v1/auth/me').catch((error) => {
      identityCache = null;
      throw error;
    });
  }
  return identityCache;
}

/** The per-session CSRF token, or a clear failure for a credential without one. */
async function csrf() {
  const current = await identity();
  if (!current.csrf_token) {
    const failure = new Error('this credential carries no CSRF token');
    failure.code = 'csrf_unavailable';
    throw failure;
  }
  return current.csrf_token;
}

/**
 * The identity a page needs before it may render account data.
 *
 * A 401 means the session is gone: the browser is sent to the login page, and
 * the promise still rejects so the caller stops rendering. Any other failure
 * (a 5xx, a network error) is left to the caller to display.
 */
async function requireLogin() {
  try {
    return await identity();
  } catch (error) {
    if (error.status === 401) {
      window.location.assign('/login');
    }
    throw error;
  }
}

/** Revoke this session and forget the cached identity. The caller navigates. */
async function logout() {
  const current = await identity();
  await api('/api/v1/auth/logout', { method: 'POST', csrf: current.csrf_token });
  identityCache = null;
  return true;
}
