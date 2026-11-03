/*
 * F05/T037 tools page helpers.
 *
 * Plain JavaScript, no framework and no build step (same style as app.js and
 * runs.js). Every function that takes server data returns text/ids; the page
 * builds DOM nodes with textContent, so a tool schema or review reason can never
 * become markup.
 *
 * The contract ToolVersion projection is only seven fields. Build logs/SBOMs,
 * build errors and review wording are read from the documented non-contract
 * `GET /api/v1/tools/{id}/build` endpoint. This file never invents a field.
 */

function toolsPath() {
  return '/api/v1/tools';
}

function toolPath(toolId) {
  return '/api/v1/tools/' + encodeURIComponent(String(toolId || ''));
}

function toolBuildPath(toolId) {
  return toolPath(toolId) + '/build';
}

function toolReviewPath(toolId) {
  return toolPath(toolId) + '/review';
}

function toolRevokePath(toolId) {
  return toolPath(toolId) + '/revoke';
}

function artifactDownloadPath(artifactId) {
  return '/api/v1/artifacts/' + encodeURIComponent(String(artifactId || '')) + '/download';
}

function artifactContentPath(artifactId) {
  return '/api/v1/artifacts/' + encodeURIComponent(String(artifactId || '')) + '/content';
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Parse one JSON textarea into a required JSON object.
 * Returns {ok:true, value} or {ok:false, error} — never throws for UI input.
 */
function parseJSONObject(label, text) {
  const source = String(text === undefined || text === null ? '' : text).trim();
  if (!source) {
    return { ok: false, error: label + ' 必须填写一个 JSON 对象' };
  }
  let value;
  try {
    value = JSON.parse(source);
  } catch (error) {
    return { ok: false, error: label + ' 不是合法 JSON' };
  }
  if (!isPlainObject(value)) {
    return { ok: false, error: label + ' 必须是 JSON 对象' };
  }
  return { ok: true, value };
}

/**
 * Build the exact ToolInput body from the form. Connectors are deliberately
 * empty: F06 owns connector versions and the server refuses a non-empty list
 * instead of silently dropping it.
 */
function buildSubmitBody(fields) {
  const values = fields || {};
  const artifactId = String(values.sourceArtifactId || '').trim();
  if (!artifactId) {
    return { ok: false, error: 'source_artifact_id 不能为空' };
  }
  const inputSchema = parseJSONObject('input_schema', values.inputSchema);
  if (!inputSchema.ok) {
    return inputSchema;
  }
  const outputSchema = parseJSONObject('output_schema', values.outputSchema);
  if (!outputSchema.ok) {
    return outputSchema;
  }
  const dependencyLock = parseJSONObject('dependency_lock', values.dependencyLock);
  if (!dependencyLock.ok) {
    return dependencyLock;
  }
  const riskClass = String(values.riskClass || '').trim();
  if (!['pure', 'read', 'write'].includes(riskClass)) {
    return { ok: false, error: 'risk_class 必须是 pure、read 或 write' };
  }
  return {
    ok: true,
    body: {
      source_artifact_id: artifactId,
      input_schema: inputSchema.value,
      output_schema: outputSchema.value,
      dependency_lock: dependencyLock.value,
      risk_class: riskClass,
      connector_version_ids: [],
    },
  };
}

/** The reviewTool body; an approval/rejection must bind the manifest hash. */
function reviewBody(decision, manifestSha256, reason) {
  if (!['approve', 'reject'].includes(decision)) {
    return { ok: false, error: 'decision 必须是 approve 或 reject' };
  }
  const manifest = String(manifestSha256 || '').trim().toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(manifest)) {
    return { ok: false, error: 'manifest_sha256 必须是 64 位十六进制内容哈希' };
  }
  const text = String(reason || '').trim();
  if (!text) {
    return { ok: false, error: '审核意见不能为空' };
  }
  return { ok: true, body: { decision: decision, manifest_sha256: manifest, reason: text } };
}

function revokeBody(reason) {
  const text = String(reason || '').trim();
  if (!text) {
    return { ok: false, error: '撤销原因不能为空' };
  }
  return { ok: true, body: { reason: text } };
}

/** The contract ToolVersion projection, described without inventing fields. */
function describeToolVersion(tool) {
  const value = tool || {};
  const state = String(value.state || '');
  const labels = {
    submitted: '已提交，等待构建',
    building: '隔离构建中',
    ready: '构建完成（ready）',
    failed: '构建失败',
    revoked: '已撤销',
  };
  return {
    id: String(value.id || ''),
    state: state,
    stateText: labels[state] || ('未知状态 ' + state),
    approved: value.approved === true,
    approvedText: value.approved === true ? '已批准共享' : '未批准（仅作者私有调试可用）',
    manifestSha256: String(value.manifest_sha256 || ''),
    digest: value.image_digest ? String(value.image_digest) : '',
    ready: state === 'ready',
    reviewable: state === 'ready',
    revocable: state === 'ready',
    buildFactsPath: toolBuildPath(value.id),
  };
}

/**
 * Describe the non-contract build facts. `build_log`/`sbom` are artifact ids; the
 * page turns them into download links, never object keys. A failed build keeps
 * its log for diagnosis; an unbuilt tool has no facts yet.
 */
function describeBuildFacts(facts) {
  const value = facts || {};
  const state = String(value.state || '');
  const lines = [];
  if (value.build_started_at) {
    lines.push('构建开始 ' + value.build_started_at);
  }
  if (value.build_finished_at) {
    lines.push('构建结束 ' + value.build_finished_at);
  }
  if (value.build_bundle_digest) {
    lines.push('内容包摘要 ' + value.build_bundle_digest);
  }
  if (value.build_error_code) {
    lines.push('构建错误码 ' + value.build_error_code);
  }
  if (value.review_decision) {
    lines.push('审核 ' + value.review_decision + (value.review_reason ? '：' + value.review_reason : ''));
  }
  if (value.revoked_at) {
    lines.push('撤销 ' + value.revoked_at + (value.revoke_reason ? '：' + value.revoke_reason : ''));
  }
  return {
    state: state,
    stateLine: lines.length ? lines.join(' · ') : '尚无构建/审核记录',
    buildErrorCode: value.build_error_code ? String(value.build_error_code) : '',
    bundleDigest: value.build_bundle_digest ? String(value.build_bundle_digest) : '',
    logArtifactId: value.build_log_artifact_id ? String(value.build_log_artifact_id) : '',
    sbomArtifactId: value.sbom_artifact_id ? String(value.sbom_artifact_id) : '',
    logPath: value.build_log_artifact_id ? artifactDownloadPath(value.build_log_artifact_id) : '',
    sbomPath: value.sbom_artifact_id ? artifactDownloadPath(value.sbom_artifact_id) : '',
    reviewDecision: value.review_decision ? String(value.review_decision) : '',
  };
}

/** The admin review queue: ready, not-yet-approved tools, newest first. */
function describeReviewQueue(items) {
  const list = Array.isArray(items) ? items : [];
  const pending = list.filter((item) => item && item.state === 'ready' && item.approved !== true);
  return {
    count: pending.length,
    ids: pending.map((item) => String(item.id || '')),
    text: pending.length === 0 ? '没有待审核的 ready 工具。' : pending.length + ' 个 ready 工具待审核',
    items: pending,
  };
}

function canSubmit(identity) {
  return Boolean(identity && Array.isArray(identity.roles) && identity.roles.includes('creator'));
}

function canReview(identity) {
  return Boolean(identity && Array.isArray(identity.roles) && identity.roles.includes('admin'));
}

function describeFailure(error) {
  if (!error) {
    return '未知错误';
  }
  return '[' + (error.code || 'error') + '] ' + (error.message || String(error));
}
