/*
 * Orchara web helper: plain JavaScript, no framework, no build step.
 *
 * `api` always sends JSON, never follows a redirect (a redirect on an auth
 * endpoint is a bug), and turns the contract error envelope
 * {"error":{"code","message","request_id","details"}} into a throwable carrying
 * `code`, `status`, `requestId` and `details`, so a page can show the API's own
 * error code instead of "something failed".
 *
 * `apiFull` is the same request with the response headers exposed. It exists for
 * the draft editor, where the ETag response header is part of the contract (a
 * save must echo it in If-Match). `api` is the payload-only wrapper every other
 * page uses, so adding header access changed no existing call site's shape.
 *
 * `identity`, `csrf`, `requireLogin` and `logout` are the session helpers every
 * logged-in page uses:
 *
 *   - `identity()` asks GET /api/v1/auth/me and caches the answer for this page,
 *     which is what makes a refresh work: the HttpOnly session cookie is all the
 *     browser keeps, nothing is remembered in the page or in storage;
 *   - `csrf()` returns the token from that identity. It is sent as the
 *     X-CSRF-Token header on every unsafe request, never in a URL;
 *   - `requireLogin()` sends the browser to /login when the session is gone;
 *   - `handleAuthFailure()` is the shared 401/403 exit: a session that is gone,
 *     a disabled principal and a disabled responsible user all leave the page
 *     instead of leaving stale rows on screen. It is also called from `apiFull`
 *     for data requests, so a page that forgot its own 401 branch still cannot
 *     present a refused read as success;
 *   - `logout()` posts /api/v1/auth/logout with the CSRF token and clears the
 *     cached identity.
 *
 * A 403 that is a *role* refusal (`forbidden`, `creator_role_required`) is not a
 * session failure: the caller stays on the page, but the navigation is asked to
 * re-read the identity and re-render (nav.js `refresh()`), so an entry the
 * server has just refused stops being advertised.
 *
 * `escapeHTML` exists for the places a page has to build markup; prefer
 * textContent where possible.
 */
async function apiFull(path, options = {}) {
  const method = options.method || 'GET';
  const headers = { Accept: 'application/json' };
  const init = { method, headers, credentials: 'same-origin', cache: 'no-store', redirect: 'error' };
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(options.body);
  }
  if (options.headers) {
    // Caller-supplied headers (the draft editor's If-Match) are additive: they
    // can never replace the fixed Accept/Content-Type semantics above.
    Object.assign(headers, options.headers);
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
    failure.details = detail.details || null;
    // Central exit for data requests: a lost session or a disabled principal
    // must leave the page (and drop the rows it was showing) even when the
    // calling page has no 401 branch of its own. Role refusals stay on the page
    // but ask the navigation to re-read the identity.
    handleApiAuthFailure(path, failure);
    throw failure;
  }
  return { payload, status: response.status, headers: response.headers };
}

async function api(path, options = {}) {
  const result = await apiFull(path, options);
  return result.payload;
}

/** One rejection that means the browser must go back to /login. */
function isAuthFailure(error) {
  if (!error) {
    return false;
  }
  if (error.status === 401) {
    return true;
  }
  return (
    error.status === 403 &&
    (error.code === 'principal_disabled' || error.code === 'responsible_disabled')
  );
}

/**
 * The login URL for one auth failure. A disabled principal gets a reason the
 * login page can explain; a revoked or expired session gets its own stable code
 * so the login page can say why the previous page stopped, instead of leaving
 * the user to guess.
 */
function loginURLFor(error) {
  if (
    error &&
    error.status === 403 &&
    (error.code === 'principal_disabled' || error.code === 'responsible_disabled')
  ) {
    return '/login?error=principal_disabled';
  }
  if (
    error &&
    error.status === 401 &&
    (error.code === 'session_revoked' || error.code === 'session_expired')
  ) {
    return '/login?error=' + encodeURIComponent(error.code);
  }
  return '/login';
}

/**
 * Empty every region a page marked as session data. A refused read must not
 * leave the rows from the previous successful read on screen.
 */
function clearAuthenticatedContent() {
  if (typeof document === 'undefined' || typeof document.querySelectorAll !== 'function') {
    return;
  }
  const regions = document.querySelectorAll('[data-auth-content]');
  for (const region of regions) {
    if (typeof region.replaceChildren === 'function') {
      region.replaceChildren();
    }
    // One-time secrets (an activation token, an API key) live in form values
    // rather than child nodes; clear those too.
    if (typeof region.value === 'string') {
      region.value = '';
    }
    region.hidden = true;
  }
}

let authFailureHandled = false;

/**
 * Handle a 401 / disabled-principal rejection once per page lifetime.
 *
 * Returns true when the rejection is an auth failure (whether or not the
 * navigation was already started), so a caller can stop rendering. Multi-page
 * flows (requireLogin, the page's own catch, the shared apiFull exit) all funnel
 * through here; the browser navigates at most once.
 */
function handleAuthFailure(error) {
  if (!isAuthFailure(error)) {
    return false;
  }
  if (!authFailureHandled) {
    authFailureHandled = true;
    clearAuthenticatedContent();
    window.location.assign(loginURLFor(error));
  }
  return true;
}

/** 403 codes that mean "your role changed", not "your session ended". */
const ROLE_REFUSAL_CODES = ['forbidden', 'creator_role_required', 'admin_role_required'];

/**
 * Ask the navigation to re-read GET /api/v1/auth/me after a role refusal.
 *
 * This is the in-session half of AC3: after an administrator removes a role, the
 * next refused action makes the page converge to the current identity instead of
 * keeping the old entries until a manual refresh.
 */
function refreshNavigationAfterRefusal() {
  if (typeof window === 'undefined' || !window.OrcharaNav) {
    return;
  }
  if (typeof window.OrcharaNav.refresh !== 'function') {
    return;
  }
  try {
    window.OrcharaNav.refresh().catch(() => {
      // The page already displays the refusal; a failed re-read is not a second
      // error to surface.
    });
  } catch (error) {
    // Rendering the refusal is the caller's job; scheduling must not throw.
  }
}

/**
 * The apiFull exit: auth failures navigate, role refusals re-render.
 *
 * The authentication endpoints are excluded: /login must be able to *show*
 * `invalid_credentials` or `principal_disabled` instead of redirecting to
 * itself.
 */
function handleApiAuthFailure(path, failure) {
  if (/^\/api\/v1\/auth\//.test(String(path || ''))) {
    return;
  }
  if (handleAuthFailure(failure)) {
    return;
  }
  if (failure.status === 403 && ROLE_REFUSAL_CODES.indexOf(failure.code) !== -1) {
    refreshNavigationAfterRefusal();
  }
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
 * A session failure (401, or a 403 for a disabled principal) sends the browser
 * to the login page through the shared handleAuthFailure, and the promise still
 * rejects so the caller stops rendering. Any other failure (a 5xx, a network
 * error) is left to the caller to display.
 */
async function requireLogin() {
  try {
    return await identity();
  } catch (error) {
    handleAuthFailure(error);
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

/**
 * F04A/T049: the request that copies the server-controlled sales template into
 * the caller's own application (a private draft, never a published version).
 *
 * Kept pure so the Node page test can execute it: the path is built with
 * encodeURIComponent and the body is exactly the empty object the non-contract
 * endpoint expects. The page then opens the new application's debug flow.
 */
function salesTemplateInstantiateRequest(templateId) {
  const id = String(templateId === undefined || templateId === null ? 'sales' : templateId).trim();
  if (!id) {
    throw new Error('a template id is required');
  }
  return {
    path: '/api/v1/templates/' + encodeURIComponent(id) + '/applications',
    method: 'POST',
    body: {},
  };
}
