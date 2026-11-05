/*
 * F02B/T068 review page helpers.
 *
 * The page is a real client of the official paths:
 *
 *   GET  /api/v1/reviews?cursor&limit     -> {items, next_cursor}
 *   GET  /api/v1/reviews/{id}             -> frozen review (snapshot)
 *   POST /api/v1/reviews/{id}/decision    -> 200 PublicationDecisionResult
 *
 * The helper functions are pure so the Node page test executes the same bytes a
 * browser receives. The review page renders the server's frozen snapshot; it
 * never invents a destination, a capability or a version.
 */

function reviewsListPath(cursor, limit) {
  const query = [];
  if (cursor) query.push('cursor=' + encodeURIComponent(String(cursor)));
  if (limit !== undefined && limit !== null) query.push('limit=' + encodeURIComponent(String(limit)));
  return '/api/v1/reviews' + (query.length ? '?' + query.join('&') : '');
}

function reviewDetailPath(reviewID) {
  return '/api/v1/reviews/' + encodeURIComponent(String(reviewID));
}

function reviewDecisionPath(reviewID) {
  return reviewDetailPath(reviewID) + '/decision';
}

/** /reviews/{id} -> id; /reviews -> null. Only a UUID is accepted. */
function reviewIDFromPath(pathname) {
  const match = /^\/reviews\/([^/]+)\/?$/.exec(String(pathname || ''));
  if (!match) return null;
  const value = decodeURIComponent(match[1]).toLowerCase();
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)
    ? value
    : null;
}

function reviewDecisionBody(decision, manifestSha256, reason) {
  if (decision !== 'approve' && decision !== 'reject') {
    return { ok: false, error: '决定必须是 approve 或 reject' };
  }
  const hash = String(manifestSha256 === undefined || manifestSha256 === null ? '' : manifestSha256)
    .trim()
    .toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(hash)) {
    return { ok: false, error: 'manifest_sha256 必须是冻结的 64 位十六进制 hash' };
  }
  const text = String(reason === undefined || reason === null ? '' : reason).trim();
  if (!text) {
    return { ok: false, error: '必须填写决定原因（记录在审核历史）' };
  }
  if (text.length > 2000) {
    return { ok: false, error: '决定原因最多 2000 个字符' };
  }
  return { ok: true, body: { decision: decision, manifest_sha256: hash, reason: text } };
}

function canReview(identity) {
  const roles = identity && Array.isArray(identity.roles) ? identity.roles : [];
  return roles.indexOf('admin') >= 0;
}

/** The queue sorted for review: submitted first, then most recent. */
function describeReviewQueue(items) {
  const list = Array.isArray(items) ? items.slice() : [];
  const order = { submitted: 0, approved: 1, rejected: 2 };
  list.sort((left, right) => {
    const leftOrder = order[left.state] === undefined ? 9 : order[left.state];
    const rightOrder = order[right.state] === undefined ? 9 : order[right.state];
    if (leftOrder !== rightOrder) return leftOrder - rightOrder;
    return String(right.id).localeCompare(String(left.id));
  });
  return list.map((item) => ({
    id: String(item.id || ''),
    applicationID: String(item.application_id || ''),
    state: String(item.state || ''),
    manifestSha256: String(item.manifest_sha256 || ''),
  }));
}

function describeDecision(result) {
  const value = result && typeof result === 'object' ? result : {};
  return {
    id: String(value.id || ''),
    state: String(value.state || ''),
    versionID: value.version_id ? String(value.version_id) : null,
    text:
      value.state === 'approved'
        ? '已批准：不可变版本 ' + String(value.version_id || '')
        : '已拒绝：创作者可以继续编辑草稿',
  };
}

function describePublicationFailure(failure) {
  if (!failure) return '请求失败';
  const parts = [String(failure.code || 'error')];
  if (failure.message) parts.push(String(failure.message));
  if (failure.details && failure.details.state) parts.push('状态：' + String(failure.details.state));
  if (failure.details && Array.isArray(failure.details.errors)) {
    parts.push('依赖：' + failure.details.errors.map((entry) => String(entry.code)).join(', '));
  }
  return parts.join(' — ');
}

/* snapshot rendering shared with the publish page's shape */
function snapshotBlocks(snapshot) {
  const value = snapshot && typeof snapshot === 'object' ? snapshot : {};
  const card = value.purpose_card && typeof value.purpose_card === 'object' ? value.purpose_card : {};
  const stringList = (input) => (Array.isArray(input) ? input.map((item) => String(item)) : []);
  const tools = Array.isArray(value.tool_capabilities) ? value.tool_capabilities : [];
  const authorCases = value.author_cases && typeof value.author_cases === 'object' ? value.author_cases : {};
  return {
    manifestSha256: String(value.manifest_sha256 || ''),
    reportID: String(value.report_id || ''),
    reportStatus: String(value.report_status || ''),
    purpose: {
      purpose: String(card.purpose || ''),
      includes: stringList(card.includes),
      excludes: stringList(card.excludes),
      requiredInputs: stringList(card.required_inputs),
      expectedOutputs: stringList(card.expected_outputs),
      examples: stringList(card.examples),
    },
    modelDestinations: (Array.isArray(value.model_destinations) ? value.model_destinations : []).map((entry) => ({
      key: String(entry.model_profile_version_id || ''),
      label: String(entry.provider || 'unknown') + ' / ' + String(entry.model_id || 'unknown'),
      destination: String(entry.data_destination || 'unspecified'),
    })),
    tools: tools.map((entry) => ({
      key: String(entry.tool_version_id || ''),
      risk: String(entry.risk_class || 'unknown'),
      read: entry.read === true,
      write: entry.write === true,
      network: entry.network === true,
      image: String(entry.image_digest || ''),
    })),
    subapps: (Array.isArray(value.subapp_versions) ? value.subapp_versions : []).map((entry) => ({
      key: String(entry.version_id || ''),
      label:
        String(entry.application_name || entry.application_id || '') +
        ' v' +
        String(entry.version_no === undefined ? '?' : entry.version_no),
      hash: String(entry.manifest_sha256 || ''),
    })),
    connectors: (Array.isArray(value.connector_policies) ? value.connector_policies : []).map((entry) => ({
      key: String(entry.version_id || ''),
      hash: String(entry.policy_sha256 || ''),
    })),
    authorCases: {
      satisfied: authorCases.satisfied === true,
      caseCount: Number(authorCases.case_count || 0),
      minimum: Number(authorCases.minimum_cases || 5),
      categories: stringList(authorCases.categories),
      missing: stringList(authorCases.missing),
    },
    anyWrite: tools.some((entry) => entry.write === true),
    anyNetwork: tools.some((entry) => entry.network === true),
  };
}

/* ------------------------------------------------------------------------- */
/* page wiring (browser only)                                                 */
/* ------------------------------------------------------------------------- */
if (typeof document !== 'undefined' && typeof window !== 'undefined') {
  const pathname = window.location ? window.location.pathname : '';
  const detailID = reviewIDFromPath(pathname);

  const setStatus = (element, text, kind) => {
    if (!element) return;
    element.textContent = text || '';
    element.className = 'status' + (kind ? ' ' + kind : '');
  };
  const fill = (id, text) => {
    const element = document.getElementById(id);
    if (element) element.textContent = text || '';
  };
  const listFill = (id, items) => {
    const element = document.getElementById(id);
    if (!element) return;
    element.textContent = '';
    items.forEach((item) => {
      const entry = document.createElement('li');
      entry.textContent = item;
      element.appendChild(entry);
    });
  };
  const tagsFill = (id, items) => {
    const element = document.getElementById(id);
    if (!element) return;
    element.textContent = '';
    items.forEach((item) => {
      const tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = item;
      element.appendChild(tag);
    });
  };
  const renderList = (payload) => {
    const rows = document.getElementById('reviews-rows');
    if (!rows) return;
    rows.textContent = '';
    describeReviewQueue(payload && payload.items).forEach((item) => {
      const row = document.createElement('tr');
      const cells = [item.id, item.applicationID, item.state, item.manifestSha256];
      cells.forEach((value) => {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.appendChild(cell);
      });
      const actionCell = document.createElement('td');
      const link = document.createElement('a');
      link.href = '/reviews/' + encodeURIComponent(item.id);
      link.textContent = '查看快照';
      actionCell.appendChild(link);
      row.appendChild(actionCell);
      rows.appendChild(row);
    });
  };
  const renderDetail = (detail) => {
    const view = snapshotBlocks(detail.snapshot);
    const card = document.getElementById('review-detail-card');
    if (card) card.hidden = false;
    fill('review-id', detail.id);
    fill('review-state', detail.state);
    fill('review-hash', detail.manifest_sha256);
    fill('review-report', view.reportStatus + ' / ' + detail.report_id);
    fill(
      'review-author-cases',
      view.authorCases.satisfied
        ? '五类作者案例已满足（' + view.authorCases.caseCount + ' 例）'
        : '缺少：' + view.authorCases.missing.join(', '),
    );
    fill('review-purpose', view.purpose.purpose);
    fill('review-includes', view.purpose.includes.join('；'));
    fill('review-excludes', view.purpose.excludes.join('；'));
    fill('review-required-inputs', view.purpose.requiredInputs.join('；'));
    fill('review-expected-outputs', view.purpose.expectedOutputs.join('；'));
    fill('review-examples', view.purpose.examples.join('；'));
    listFill(
      'review-model-destinations',
      view.modelDestinations.map((entry) => entry.label + ' — 数据去向：' + entry.destination),
    );
    const tools = document.getElementById('review-tools');
    if (tools) {
      tools.textContent = '';
      view.tools.forEach((tool) => {
        const row = document.createElement('tr');
        [
          tool.key,
          tool.risk,
          tool.read ? '读取' : '不读取',
          tool.write ? '写入' : '不写入',
          tool.network ? '联网' : '不联网',
          tool.image,
        ].forEach((value) => {
          const cell = document.createElement('td');
          cell.textContent = value;
          row.appendChild(cell);
        });
        tools.appendChild(row);
      });
    }
    listFill('review-subapps', view.subapps.map((entry) => entry.label + ' — ' + entry.hash));
    listFill('review-connectors', view.connectors.map((entry) => entry.key + ' — ' + entry.hash));
    if (view.anyWrite || view.anyNetwork) {
      const warning = document.getElementById('review-write-warning');
      if (warning) warning.hidden = false;
    }
    return view;
  };

  const boot = async () => {
    let identity;
    try {
      identity = await requireLogin();
      if (!identity) return;
    } catch (failure) {
      setStatus(document.getElementById('reviews-status'), describePublicationFailure(failure), 'bad');
      return;
    }
    const admin = canReview(identity);
    let cursor = null;

    const loadList = async (nextCursor) => {
      const status = document.getElementById('reviews-status');
      setStatus(status, '正在读取审核列表…', 'pending');
      try {
        const payload = await api(reviewsListPath(nextCursor, 20));
        renderList(payload);
        cursor = payload ? payload.next_cursor : null;
        const more = document.getElementById('reviews-more');
        if (more) more.hidden = !cursor;
        setStatus(status, '共 ' + ((payload && payload.items && payload.items.length) || 0) + ' 条。', 'ok');
      } catch (failure) {
        setStatus(status, describePublicationFailure(failure), 'bad');
      }
    };

    if (!detailID) {
      await loadList(null);
      const refresh = document.getElementById('reviews-refresh');
      if (refresh) refresh.addEventListener('click', () => loadList(null));
      const next = document.getElementById('reviews-next');
      if (next) next.addEventListener('click', () => loadList(cursor));
      return;
    }

    const status = document.getElementById('reviews-status');
    try {
      const detail = await api(reviewDetailPath(detailID));
      renderDetail(detail);
      setStatus(status, '已读取冻结快照。', 'ok');
      if (admin && detail.state === 'submitted') {
        const card = document.getElementById('review-decision-card');
        if (card) card.hidden = false;
        const hashInput = document.getElementById('review-decision-hash');
        if (hashInput) hashInput.value = detail.manifest_sha256;
      }
      const form = document.getElementById('review-decision-form');
      if (form) {
        form.addEventListener('submit', async (event) => {
          event.preventDefault();
          const decisionStatus = document.getElementById('review-decision-status');
          const body = reviewDecisionBody(
            document.getElementById('review-decision').value,
            document.getElementById('review-decision-hash').value,
            document.getElementById('review-decision-reason').value,
          );
          if (!body.ok) {
            setStatus(decisionStatus, body.error, 'bad');
            return;
          }
          setStatus(decisionStatus, '正在提交决定…', 'pending');
          try {
            const result = await api(reviewDecisionPath(detailID), {
              method: 'POST',
              body: body.body,
              csrf: await csrf(),
              headers: { 'Idempotency-Key': newReviewIdempotencyKey() },
            });
            const described = describeDecision(result);
            const panel = document.getElementById('review-decision-result');
            if (panel) panel.hidden = false;
            fill('review-result-state', described.state);
            fill('review-result-version', described.versionID || '—');
            fill('review-state', described.state);
            setStatus(decisionStatus, described.text, 'ok');
          } catch (failure) {
            setStatus(decisionStatus, describePublicationFailure(failure), 'bad');
          }
        });
      }
    } catch (failure) {
      setStatus(status, describePublicationFailure(failure), 'bad');
    }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
}

function newReviewIdempotencyKey() {
  if (typeof crypto !== 'undefined' && crypto && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  const bytes = [];
  for (let index = 0; index < 16; index += 1) {
    bytes.push(Math.floor(Math.random() * 256));
  }
  return bytes.map((value) => value.toString(16).padStart(2, '0')).join('');
}
