'use strict';

// F06/T050: the approval page behaviour.
//
// The page never invents a proposal: it lists the caller's approvals, reads the
// sealed server proposal through the existing artifact-content ACL (the F04A
// approver is the run owner, so the artifact owner check is the right one) and
// renders the exact target/params/hash/destination/expiry. Approve and reject are
// two explicit buttons with no default selection; a decision carries the exact
// params hash and a fresh Idempotency-Key.

function approvalsListPath(cursor, limit) {
  const query = [];
  if (cursor !== undefined && cursor !== null && String(cursor) !== '') {
    query.push('cursor=' + encodeURIComponent(String(cursor)));
  }
  if (limit !== undefined && limit !== null && String(limit) !== '') {
    query.push('limit=' + encodeURIComponent(String(limit)));
  }
  return '/api/v1/approvals' + (query.length ? '?' + query.join('&') : '');
}

function approvalPath(approvalID) {
  return '/api/v1/approvals/' + encodeURIComponent(String(approvalID || ''));
}

function decisionPath(approvalID) {
  return approvalPath(approvalID) + '/decision';
}

function proposalContentPath(artifactID) {
  return '/api/v1/artifacts/' + encodeURIComponent(String(artifactID || '')) + '/content';
}

function approvalStateText(state) {
  switch (state) {
    case 'pending': return '待审批';
    case 'approved': return '已批准';
    case 'rejected': return '已拒绝';
    case 'expired': return '已过期';
    case 'invalidated': return '已失效';
    default: return state ? String(state) : '未知';
  }
}

/** The list/detail projection of one contract Approval. */
function describeApproval(approval) {
  const value = approval || {};
  const state = String(value.state || '');
  return {
    id: String(value.id || ''),
    runID: String(value.run_id || ''),
    state: state,
    stateText: approvalStateText(state),
    isPending: state === 'pending',
    proposalArtifactID: String(value.proposal_artifact_id || ''),
    paramsSHA256: String(value.params_sha256 || ''),
    paramsHashShort: String(value.params_sha256 || '').slice(0, 12),
    expiresAt: String(value.expires_at || ''),
  };
}

function proposalTargetText(target) {
  if (!target) return '';
  const method = String(target.method || '').toUpperCase();
  const origin = String(target.origin || '');
  const path = String(target.path || '');
  const line = [method, origin + path].filter((part) => part !== '').join(' ');
  return line;
}

function proposalQueryText(target) {
  if (!target || !target.query) return '';
  const names = Object.keys(target.query).sort();
  if (names.length === 0) return '';
  return names.map((name) => name + '=' + String(target.query[name])).join('&');
}

function proposalSourcesText(proposal) {
  const value = proposal || {};
  const lines = [];
  if (value.snapshot_id) lines.push('execution_snapshot ' + value.snapshot_id);
  if (value.snapshot_sha256) lines.push('snapshot_sha256 ' + value.snapshot_sha256);
  if (value.manifest_sha256) lines.push('manifest_sha256 ' + value.manifest_sha256);
  if (value.policy_sha256) lines.push('connector_policy_sha256 ' + value.policy_sha256);
  if (value.capability_version) {
    lines.push('capability ' + String(value.capability_kind || '') + ' ' + value.capability_version);
  }
  return lines.join('\n');
}

/**
 * The page's one rendering of the sealed server proposal. Only facts the server
 * wrote are exposed; a missing member renders empty rather than a guess.
 */
function describeProposal(proposal) {
  const value = proposal || {};
  const target = value.target || {};
  return {
    runID: String(value.run_id || ''),
    operationID: String(value.operation_id || ''),
    logicalKey: String(value.logical_key || ''),
    nodeKey: String(value.node_key || ''),
    targetText: proposalTargetText(target),
    queryText: proposalQueryText(target),
    pathPrefix: String(target.path_prefix || ''),
    method: String(target.method || '').toUpperCase(),
    paramsText: value.params === undefined ? '' : JSON.stringify(value.params, null, 2),
    paramsSHA256: String(value.params_sha256 || ''),
    destinationText: String(value.data_destination || ''),
    riskText: String(value.risk_class || ''),
    sourcesText: proposalSourcesText(value),
    expiresAt: String(value.expires_at || ''),
    approvalVersion: Number(value.approval_version || 0),
  };
}

/**
 * The exact decision body. A page bug that tries to decide without an explicit
 * approve/reject or without the shown params hash fails here instead of sending a
 * request the server would have to reject.
 */
function decisionPayload(decision, paramsSHA256) {
  if (decision !== 'approve' && decision !== 'reject') {
    throw new Error('a decision must be an explicit approve or reject');
  }
  const hash = String(paramsSHA256 || '');
  if (hash === '') {
    throw new Error('the decision must carry the approval params_sha256');
  }
  return { decision: decision, params_sha256: hash };
}

function decisionConfirmationText(decision) {
  if (decision === 'approve') {
    return '批准只允许这次操作继续：恢复后的尝试仍会重新校验权限与预算，最多派发一次。' +
      '批准不会赋予额外权限，也不能撤销已经发生的外部写入。';
  }
  return '拒绝会让等待中的 Run 以 approval_rejected 失败。拒绝不会撤销已经发生的外部写入。';
}

async function loadApprovalsPage(cursor) {
  const status = document.getElementById('approvals-status');
  status.textContent = '正在加载审批…';
  status.className = 'status pending';
  try {
    const body = await api(approvalsListPath(cursor, 20));
    const items = (body.items || []).map(describeApproval);
    const list = document.getElementById('approvals-list');
    list.replaceChildren();
    for (const item of items) {
      const row = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = '查看 ' + item.runID + '（' + item.stateText + '）';
      button.addEventListener('click', () => loadApprovalDetail(item.id));
      row.append(button);
      list.append(row);
    }
    document.getElementById('approvals-empty').hidden = items.length !== 0;
    const next = document.getElementById('approvals-next');
    nextCursor = body.next_cursor || null;
    next.hidden = !nextCursor;
    status.textContent = '共 ' + items.length + ' 条（待审批优先）。';
    status.className = 'status ok';
  } catch (error) {
    status.textContent = '审批列表不可用：' + (error && error.message ? error.message : '未知错误');
    status.className = 'status bad';
  }
}

let displayedApproval = null;
let nextCursor = null;

async function loadApprovalDetail(approvalID) {
  const detail = document.getElementById('approval-detail');
  const status = document.getElementById('decision-status');
  try {
    const approval = describeApproval(await api(approvalPath(approvalID)));
    displayedApproval = approval;
    detail.hidden = false;
    document.getElementById('detail-id').textContent = approval.id;
    document.getElementById('detail-run').textContent = approval.runID;
    document.getElementById('detail-state').textContent = approval.stateText;
    document.getElementById('detail-params-sha').textContent = approval.paramsSHA256;
    document.getElementById('detail-expires').textContent = approval.expiresAt;
    document.getElementById('detail-target').textContent = '正在读取服务端提案…';
    document.getElementById('detail-params').textContent = '';
    document.getElementById('detail-destination').textContent = '';
    document.getElementById('detail-sources').textContent = '';
    document.getElementById('detail-risk').textContent = '';
    document.getElementById('detail-proposal-sha').textContent = approval.proposalArtifactID;
    document.getElementById('approve-button').disabled = true;
    document.getElementById('reject-button').disabled = true;
    status.textContent = '';

    const response = await fetch(proposalContentPath(approval.proposalArtifactID), {
      credentials: 'same-origin',
      cache: 'no-store',
      redirect: 'error',
    });
    const text = await response.text();
    if (!response.ok) {
      throw new Error('提案读取失败：' + text.slice(0, 200));
    }
    const proposal = describeProposal(JSON.parse(text));
    document.getElementById('detail-target').textContent =
      proposal.targetText + (proposal.queryText ? '?' + proposal.queryText : '');
    document.getElementById('detail-params').textContent = proposal.paramsText;
    document.getElementById('detail-destination').textContent = proposal.destinationText;
    document.getElementById('detail-sources').textContent = proposal.sourcesText;
    document.getElementById('detail-risk').textContent = proposal.riskText;
    // Only a still-pending approval can be decided, and only after the exact
    // proposal is on screen. There is no preselected decision.
    const canDecide = approval.isPending && proposal.paramsSHA256 === approval.paramsSHA256;
    document.getElementById('approve-button').disabled = !canDecide;
    document.getElementById('reject-button').disabled = !canDecide;
    status.textContent = canDecide
      ? '请核对真实目标、参数与被写入的去向后再明确选择批准或拒绝。'
      : '该审批不可再决定（状态：' + approval.stateText + '）。';
    status.className = 'status ' + (canDecide ? 'pending' : 'bad');
  } catch (error) {
    status.textContent = '审批详情不可用：' + (error && error.message ? error.message : '未知错误');
    status.className = 'status bad';
  }
}

async function decideApproval(decision) {
  if (!displayedApproval) return;
  if (!window.confirm(decisionConfirmationText(decision))) return;
  const status = document.getElementById('decision-status');
  const button = document.getElementById(
    decision === 'approve' ? 'approve-button' : 'reject-button',
  );
  button.disabled = true;
  try {
    const body = decisionPayload(decision, displayedApproval.paramsSHA256);
    const key = (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : String(Date.now()) + '-' + Math.random().toString(16).slice(2);
    const updated = describeApproval(await api(decisionPath(displayedApproval.id), {
      method: 'POST',
      csrf: await csrf(),
      headers: { 'Idempotency-Key': key },
      body: body,
    }));
    displayedApproval = updated;
    document.getElementById('detail-state').textContent = updated.stateText;
    status.textContent = '已提交：' + updated.stateText +
      '。决定已保存；恢复仍由唯一 resume 事务在旧尝试退出后排队。';
    status.className = 'status ok';
    document.getElementById('approve-button').disabled = true;
    document.getElementById('reject-button').disabled = true;
    await loadApprovalsPage(null);
  } catch (error) {
    const code = error && error.code ? '[' + error.code + '] ' : '';
    status.textContent = code + (error && error.message ? error.message : '决定提交失败');
    status.className = 'status bad';
    button.disabled = false;
  }
}

if (typeof document !== 'undefined' && document.getElementById && document.getElementById('approvals-list')) {
  document.getElementById('approvals-next').addEventListener('click', () => {
    if (nextCursor) {
      loadApprovalsPage(nextCursor);
    }
  });
  document.getElementById('approve-button').addEventListener('click', () => decideApproval('approve'));
  document.getElementById('reject-button').addEventListener('click', () => decideApproval('reject'));
  loadApprovalsPage(null);
}
