/*
 * F02B/T070 versions page helpers.
 *
 * Plain JavaScript, no framework and no build step (same style as app.js and
 * tools.js). Every function that takes server data returns text; the page builds
 * DOM nodes with textContent, so an application name, a version hash or an API
 * error can never become markup.
 *
 * The contract's Version schema has no state/availability field, so a revoked
 * version is simply omitted from the version list and its detail read is 404.
 * The only availability signal the contract exposes is
 * `Application.active_version_id`; after a revocation it is null (never repointed
 * at another version) and the catalog says the application is unavailable.
 *
 * revokeVersion has no Idempotency-Key in the contract: the request body is
 * exactly {reason, stop_existing} and a repeated call is answered idempotently by
 * the server.
 */

function isUUIDish(value) {
  return /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(
    String(value || ''),
  );
}

/** The application a versions page is showing; path ids only, never a query string. */
function applicationIdFromVersionsPath(pathname) {
  const match = /^\/apps\/([^/]+)\/versions\/?$/.exec(String(pathname || ''));
  if (!match) {
    return null;
  }
  return isUUIDish(match[1]) ? match[1] : null;
}

function applicationPath(applicationId) {
  return '/api/v1/applications/' + encodeURIComponent(String(applicationId || ''));
}

function versionsPath(applicationId) {
  return applicationPath(applicationId) + '/versions';
}

function versionPath(versionId) {
  return '/api/v1/versions/' + encodeURIComponent(String(versionId || ''));
}

function revokeVersionPath(versionId) {
  return versionPath(versionId) + '/revoke';
}

function runnableVersionPath(versionId) {
  return versionPath(versionId) + '/runnable';
}

/** Only an administrator may revoke a published version. */
function canRevokeVersion(identity) {
  return Boolean(identity && Array.isArray(identity.roles) && identity.roles.includes('admin'));
}

/**
 * The exact revokeVersion body, or a located client-side refusal.
 * Returns {ok:true, body:{reason, stop_existing}} or {ok:false, error}.
 */
function revokeVersionBody(reason, stopExisting) {
  const trimmed = String(reason === undefined || reason === null ? '' : reason).trim();
  if (!trimmed) {
    return { ok: false, error: '撤销原因不能为空' };
  }
  if (Array.from(trimmed).length > 2000) {
    return { ok: false, error: '撤销原因最多 2000 个字符' };
  }
  if (typeof stopExisting !== 'boolean') {
    return { ok: false, error: 'stop_existing 必须是布尔值' };
  }
  return { ok: true, body: { reason: trimmed, stop_existing: stopExisting } };
}

function describeVersion(version) {
  const value = version || {};
  const number = value.version_no === undefined || value.version_no === null ? '?' : value.version_no;
  const hash = value.manifest_sha256 ? String(value.manifest_sha256).slice(0, 12) : '';
  return 'v' + number + (hash ? ' · ' + hash : '');
}

function describeVersionOptions(versions) {
  return (Array.isArray(versions) ? versions : []).map((version) => ({
    id: version.id,
    label: describeVersion(version),
  }));
}

/** The catalog's only availability signal: the active version pointer. */
function describeCatalogAvailability(application) {
  if (!application) {
    return '不可用：应用不存在或不可见';
  }
  return application.active_version_id
    ? '可用：当前版本 ' + application.active_version_id
    : '不可用：没有生效版本（未发布或已撤销）';
}

function describeRevokeResult(result) {
  const id = result && result.id ? String(result.id) : '';
  return '已撤销版本 ' + id + '；该版本不再出现在目录中，也不会被自动替换为其他版本。';
}

function describeRevocationFailure(error) {
  if (!error) {
    return '撤销失败：未知错误';
  }
  return '撤销失败 [' + (error.code || 'error') + '] ' + (error.message || String(error));
}
