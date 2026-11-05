/*
 * F02B/T068 publication page helpers.
 *
 * The page is a real client of the official paths:
 *
 *   POST /api/v1/applications/{id}/publication-preview -> shared-definition preview
 *   POST /api/v1/applications/{id}/submit              -> 202 Accepted (submitApplication)
 *   GET  /api/v1/reviews/{review_id}                   -> frozen review snapshot
 *
 * Everything below is pure so the Node page test executes the same bytes a
 * browser receives: paths are built from ids with encodeURIComponent, the submit
 * body is exactly the contract's two members, and the purpose card / model
 * destinations / tool read-write-network / pinned sub-versions are rendered from
 * the server's snapshot fields. No value is invented when a list is empty.
 */

function publicationPreviewPath(applicationID) {
  return '/api/v1/applications/' + encodeURIComponent(String(applicationID)) + '/publication-preview';
}

function publicationSubmitPath(applicationID) {
  return '/api/v1/applications/' + encodeURIComponent(String(applicationID)) + '/submit';
}

function publicationReviewPath(reviewID) {
  return '/api/v1/reviews/' + encodeURIComponent(String(reviewID));
}

function applicationPath(applicationID) {
  return '/api/v1/applications/' + encodeURIComponent(String(applicationID));
}

function sha256Hex(value) {
  const text = String(value === undefined || value === null ? '' : value).trim().toLowerCase();
  return /^[0-9a-f]{64}$/.test(text) ? text : null;
}

function uuidValue(value) {
  const text = String(value === undefined || value === null ? '' : value).trim().toLowerCase();
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(text) ? text : null;
}

/** submitApplication's contract body is exactly these two members. */
function publicationSubmitBody(expectedManifestSha256, evaluationReportID) {
  const hash = sha256Hex(expectedManifestSha256);
  if (!hash) {
    return { ok: false, error: '需要当前内容的 64 位 manifest SHA-256（先加载发布摘要）' };
  }
  const report = uuidValue(evaluationReportID);
  if (!report) {
    return { ok: false, error: '需要已通过的可信评测报告 UUID' };
  }
  return { ok: true, body: { expected_manifest_sha256: hash, evaluation_report_id: report } };
}

function newPublicationIdempotencyKey() {
  if (typeof crypto !== 'undefined' && crypto && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  const bytes = [];
  for (let index = 0; index < 16; index += 1) {
    bytes.push(Math.floor(Math.random() * 256));
  }
  return bytes.map((value) => value.toString(16).padStart(2, '0')).join('');
}

function listOf(value) {
  return Array.isArray(value) ? value : [];
}

function stringsOf(value) {
  return listOf(value).map((item) => String(item));
}

/** One model destination row: where the author's content is sent. */
function describeModelDestination(entry) {
  const provider = String(entry.provider || 'unknown');
  const model = String(entry.model_id || 'unknown');
  const destination = String(entry.data_destination || 'unspecified');
  return {
    key: String(entry.model_profile_version_id || ''),
    label: provider + ' / ' + model,
    detail: '数据去向：' + destination,
  };
}

/** One tool capability row: read/write/network needs the admin must see. */
function describeToolCapability(entry) {
  const risk = String(entry.risk_class || 'unknown');
  return {
    key: String(entry.tool_version_id || ''),
    risk_class: risk,
    read: entry.read === true,
    write: entry.write === true,
    network: entry.network === true,
    image: String(entry.image_digest || ''),
    readText: entry.read === true ? '读取' : '不读取',
    writeText: entry.write === true ? '写入' : '不写入',
    networkText: entry.network === true ? '联网' : '不联网',
  };
}

function describeSubappVersion(entry) {
  const name = String(entry.application_name || entry.application_id || '');
  return {
    key: String(entry.version_id || ''),
    label: name + ' v' + String(entry.version_no === undefined ? '?' : entry.version_no),
    detail: String(entry.manifest_sha256 || ''),
  };
}

/** The frozen author-case coverage the admin uses as the publication gate. */
function describeAuthorCases(coverage) {
  const value = coverage && typeof coverage === 'object' ? coverage : {};
  return {
    satisfied: value.satisfied === true,
    caseCount: Number(value.case_count || 0),
    minimum: Number(value.minimum_cases || 5),
    categories: stringsOf(value.categories),
    missing: stringsOf(value.missing),
  };
}

/**
 * Turn a review snapshot into the exact blocks the page renders. A missing
 * member is an empty list, never an invented entry.
 */
function describeReviewSnapshot(snapshot) {
  const value = snapshot && typeof snapshot === 'object' ? snapshot : {};
  const card = value.purpose_card && typeof value.purpose_card === 'object' ? value.purpose_card : {};
  const tools = listOf(value.tool_capabilities).map(describeToolCapability);
  return {
    manifestSha256: String(value.manifest_sha256 || ''),
    executionRevision: Number(value.execution_revision || 0),
    draftRevision: Number(value.draft_revision || 0),
    reportID: String(value.report_id || ''),
    reportStatus: String(value.report_status || ''),
    reportSha256: String(value.report_sha256 || ''),
    purpose: {
      purpose: String(card.purpose || ''),
      includes: stringsOf(card.includes),
      excludes: stringsOf(card.excludes),
      requiredInputs: stringsOf(card.required_inputs),
      expectedOutputs: stringsOf(card.expected_outputs),
      examples: stringsOf(card.examples),
    },
    modelDestinations: listOf(value.model_destinations).map(describeModelDestination),
    toolCapabilities: tools,
    subappVersions: listOf(value.subapp_versions).map(describeSubappVersion),
    connectorPolicies: listOf(value.connector_policies).map((entry) => ({
      key: String(entry.version_id || ''),
      policySha256: String(entry.policy_sha256 || ''),
    })),
    authorCases: describeAuthorCases(value.author_cases),
    anyWrite: tools.some((tool) => tool.write),
    anyNetwork: tools.some((tool) => tool.network),
  };
}

/** A readable refusal: the contract's code and message, never a raw body. */
function describePublicationFailure(failure) {
  if (!failure) {
    return '请求失败';
  }
  const parts = [String(failure.code || 'error')];
  if (failure.message) {
    parts.push(String(failure.message));
  }
  if (failure.details && failure.details.field) {
    parts.push('字段：' + String(failure.details.field));
  }
  if (failure.details && Array.isArray(failure.details.missing_categories)) {
    parts.push('缺少作者案例：' + failure.details.missing_categories.join(', '));
  }
  return parts.join(' — ');
}

/* ------------------------------------------------------------------------- */
/* page wiring (browser only)                                                 */
/* ------------------------------------------------------------------------- */
if (typeof document !== 'undefined' && typeof window !== 'undefined') {
  const applicationID = (function fromPath(pathname) {
    const match = /^\/apps\/([^/]+)\/publish\/?$/.exec(String(pathname || ''));
    return match ? decodeURIComponent(match[1]) : null;
  })(window.location ? window.location.pathname : '');

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
  const renderPreview = (payload) => {
    const view = describeReviewSnapshot(payload || {});
    const panel = document.getElementById('publish-preview-panel');
    if (panel) panel.hidden = false;
    fill('publish-manifest-hash', view.manifestSha256);
    fill('publish-execution-revision', String(view.executionRevision));
    fill('publish-draft-revision', String(view.draftRevision));
    fill('publish-purpose', view.purpose.purpose);
    fill('publish-includes', view.purpose.includes.join('；'));
    fill('publish-excludes', view.purpose.excludes.join('；'));
    fill('publish-required-inputs', view.purpose.requiredInputs.join('；'));
    fill('publish-expected-outputs', view.purpose.expectedOutputs.join('；'));
    fill('publish-examples', view.purpose.examples.join('；'));
    listFill('publish-model-destinations', view.modelDestinations.map((entry) => entry.label + ' — ' + entry.detail));
    const toolsBody = document.getElementById('publish-tools');
    if (toolsBody) {
      toolsBody.textContent = '';
      view.toolCapabilities.forEach((tool) => {
        const row = document.createElement('tr');
        [tool.key, tool.risk_class, tool.readText, tool.writeText, tool.networkText, tool.image].forEach((value) => {
          const cell = document.createElement('td');
          cell.textContent = value;
          row.appendChild(cell);
        });
        toolsBody.appendChild(row);
      });
    }
    listFill('publish-subapps', view.subappVersions.map((entry) => entry.label + ' — ' + entry.detail));
    listFill('publish-connectors', view.connectorPolicies.map((entry) => entry.key + ' — ' + entry.policySha256));
    const hashInput = document.getElementById('publish-expected-hash');
    if (hashInput && !hashInput.value) hashInput.value = view.manifestSha256;
    return view;
  };

  const boot = async () => {
    if (!applicationID) {
      setStatus(document.getElementById('publish-status'), '地址缺少应用 id', 'bad');
      return;
    }
    try {
      const identity = await requireLogin();
      if (!identity) return;
    } catch (failure) {
      setStatus(document.getElementById('publish-status'), describePublicationFailure(failure), 'bad');
      return;
    }

    const previewButton = document.getElementById('publish-preview');
    if (previewButton) {
      previewButton.addEventListener('click', async () => {
        const status = document.getElementById('publish-preview-status');
        setStatus(status, '正在编译并读取共享定义…', 'pending');
        try {
          const payload = await api(publicationPreviewPath(applicationID), {
            method: 'POST',
            csrf: await csrf(),
          });
          renderPreview(payload);
          setStatus(status, '摘要已加载：请确认用途、数据去向与工具权限后提交。', 'ok');
        } catch (failure) {
          setStatus(status, describePublicationFailure(failure), 'bad');
        }
      });
    }

    const form = document.getElementById('publish-form');
    if (!form) return;
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const status = document.getElementById('publish-status');
      const body = publicationSubmitBody(
        document.getElementById('publish-expected-hash').value,
        document.getElementById('publish-report-id').value,
      );
      if (!body.ok) {
        setStatus(status, body.error, 'bad');
        return;
      }
      setStatus(status, '正在提交发布审核…', 'pending');
      try {
        const response = await fetch(publicationSubmitPath(applicationID), {
          method: 'POST',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            'X-CSRF-Token': await csrf(),
            'Idempotency-Key': newPublicationIdempotencyKey(),
          },
          body: JSON.stringify(body.body),
        });
        const text = await response.text();
        const payload = text ? JSON.parse(text) : null;
        if (!response.ok) {
          const detail = (payload && payload.error) || {};
          throw {
            code: detail.code,
            message: detail.message,
            details: detail.details,
          };
        }
        const panel = document.getElementById('publish-review-panel');
        if (panel) panel.hidden = false;
        fill('publish-review-id', payload.id);
        fill('publish-review-state', payload.state);
        const link = document.getElementById('publish-review-link');
        if (link) link.href = '/reviews/' + encodeURIComponent(payload.id);
        setStatus(
          status,
          payload.state === 'submitted'
            ? '已提交：管理员只会审核这份冻结内容。'
            : '相同内容的已有审核记录：' + payload.state,
          'ok',
        );
      } catch (failure) {
        setStatus(status, describePublicationFailure(failure), 'bad');
      }
    });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
}
