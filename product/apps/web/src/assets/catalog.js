/*
 * F08/T077 catalog page helpers: plain JavaScript, no framework, no build step.
 *
 * The page calls the documented non-contract `GET /api/v1/catalog/search`
 * (ADR 0026). These functions are pure so the whole client contract (path
 * encoding, card projection, retrieval wording, empty state) is executable under
 * Node without a DOM, exactly like the other F08/F13A page helpers.
 *
 * The page never derives authority from the projection: it renders only what the
 * server returned, and it says "回退检索（索引落后）" whenever the server's
 * `retrieval.source` says the authorized full-text fallback answered.
 */

/** The search path; both values are always encoded by this helper. */
function catalogSearchPath(query, cursor) {
  const parts = [];
  const text = String(query === undefined || query === null ? '' : query).trim();
  if (text) {
    parts.push('q=' + encodeURIComponent(text));
  }
  if (cursor) {
    parts.push('cursor=' + encodeURIComponent(String(cursor)));
  }
  return '/api/v1/catalog/search' + (parts.length ? '?' + parts.join('&') : '');
}

/** The capability card the server returned, normalised without inventing fields. */
function catalogCard(item) {
  const card = (item && item.capability_card) || {};
  const strings = (value) =>
    Array.isArray(value) ? value.filter((entry) => typeof entry === 'string') : [];
  return {
    purpose: typeof card.purpose === 'string' ? card.purpose : '',
    includes: strings(card.includes),
    excludes: strings(card.excludes),
    requiredInputs: strings(card.required_inputs),
    expectedOutputs: strings(card.expected_outputs),
    examples: strings(card.examples),
  };
}

/** The version label of one hit; never guesses a version number. */
function catalogVersionLabel(item) {
  const versionNo = item && Number.isInteger(item.version_no) ? item.version_no : 0;
  return versionNo > 0 ? 'v' + versionNo : '版本号未知';
}

/** One hit rendered as plain facts; the page builds nodes from these fields. */
function describeCatalogHit(item) {
  const card = catalogCard(item);
  const name = (item && item.application_name) || '';
  const versionLabel = catalogVersionLabel(item);
  return {
    title: (name || '未命名应用') + ' · ' + versionLabel,
    applicationID: (item && item.application_id) || '',
    versionID: (item && item.version_id) || '',
    versionNo: item && Number.isInteger(item.version_no) ? item.version_no : 0,
    versionLabel,
    purpose: card.purpose,
    includes: card.includes,
    excludes: card.excludes,
    requiredInputs: card.requiredInputs,
    expectedOutputs: card.expectedOutputs,
    examples: card.examples,
  };
}

/**
 * How one page was produced. The label is the user-visible honesty requirement:
 * a fallback answer must never read like an indexed one.
 */
function describeRetrieval(retrieval) {
  const source = (retrieval && retrieval.source) || '';
  const stale = Boolean(retrieval && retrieval.stale);
  const indexState = (retrieval && retrieval.index_state) || 'unknown';
  const rechecked = Number.isInteger(retrieval && retrieval.rechecked)
    ? retrieval.rechecked
    : 0;
  const rejected = Number.isInteger(retrieval && retrieval.rejected_by_recheck)
    ? retrieval.rejected_by_recheck
    : 0;
  let label;
  let explanation;
  if (source === 'index') {
    label = '授权索引';
    explanation = '本页来自目录索引；返回前已逐条复核当前授权与撤销状态。';
  } else if (source === 'fallback') {
    label = '回退检索（索引落后）';
    explanation = '索引尚不完整：本页改为在你有权读取的版本上做全文检索，并再次核对授权与撤销。';
  } else {
    label = '未知检索来源';
    explanation = '服务器没有说明本页来自索引还是全文回退，不能当作索引结果。';
  }
  return {
    source,
    stale,
    label,
    indexState,
    rechecked,
    rejectedByRecheck: rejected,
    explanation,
  };
}

/** The empty-page sentence; distinguishes "nothing readable" from "no match". */
function describeCatalogEmpty(retrieval, count) {
  if (count > 0) {
    return '';
  }
  const described = describeRetrieval(retrieval);
  if (described.source === 'fallback') {
    return '授权范围内没有匹配的已发布版本；当前是回退检索，索引可能尚未覆盖最新发布。';
  }
  if (described.source === 'index') {
    return '授权范围内没有匹配的已发布版本。';
  }
  return '尚未取得检索结果，请检查登录状态后重试。';
}

/** The count line above the result list. */
function describeCatalogCount(count, hasNext) {
  if (count <= 0) {
    return '';
  }
  return '本页 ' + count + ' 个可读版本' + (hasNext ? '，还有下一页。' : '。');
}

/** A link a user can carry to the chat request form; T079 owns the router. */
function catalogSubmitTarget(item) {
  const versionID = (item && item.version_id) || '';
  return versionID ? '/chat?version=' + encodeURIComponent(versionID) : '/chat';
}

/** The form body is never sent; it only drives the URL helper. */
function catalogSearchRequest(query) {
  return { path: catalogSearchPath(query, null), method: 'GET' };
}
