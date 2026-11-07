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

/*
 * F12/T098: the two expiration mechanisms are not interchangeable.
 *
 * A capability approval (operation_id present) has no background sweeper: the
 * decision path is the only place that observes the deadline, so the row keeps
 * saying `pending` until someone decides (409 approval_expired). A node approval
 * (operation_id IS NULL) is swept by the API process (default 30s): the row moves
 * to `expired` on its own and a parked Run fails with approval_expired. A page
 * that collapses the two would tell the user the opposite of what happens.
 */
const APPROVAL_EXPIRY_TEXT = Object.freeze({
  capability:
    '过期语义（能力审批 capability）：服务端没有后台过期扫描；只要没人操作，审批会一直显示“待审批”。'
    + '只有你点击批准或拒绝时，服务端才用数据库时钟判定过期并返回 409 approval_expired。'
    + '过期后这条审批不能复活，本页不会偷偷重试；需要重新提交产生这次操作的任务。',
  node:
    '过期语义（节点审批 node）：服务端后台 sweeper 会按封存窗口（默认 30 秒）自动过期；'
    + '到期后审批变为“已过期”，等待中的 Run 以 approval_expired 失败，不需要你再操作。'
    + '过期后不能复活，需要重新提交任务。',
  expired:
    '这条审批已过期：决定没有保存，也不能复活。下一步：重新提交产生这次操作/任务；本页不提供“恢复旧审批”。',
  invalidated:
    '这条审批已失效（Run 已取消或进入终态）：没有决定窗口，也不会复活；需要时请重新提交任务。',
});

/** Which expiration mechanism this approval follows, from the sealed proposal. */
function approvalExpiryKind(proposalKind) {
  return String(proposalKind || '') === 'node_approval' ? 'node' : 'capability';
}

/**
 * The explanation the detail panel shows for one approval.
 *
 * `state` wins over `kind`: an expired/invalidated row is already a fact, and the
 * page must not describe a decision window that no longer exists.
 */
function approvalExpiryNote(approval, proposalKind) {
  const state = String((approval && approval.state) || '');
  const kind = approvalExpiryKind(proposalKind);
  if (state === 'expired') {
    return { visible: true, kind: kind, state: state, tone: 'bad', text: APPROVAL_EXPIRY_TEXT.expired };
  }
  if (state === 'invalidated') {
    return { visible: true, kind: kind, state: state, tone: 'bad', text: APPROVAL_EXPIRY_TEXT.invalidated };
  }
  if (state === 'pending') {
    return { visible: true, kind: kind, state: state, tone: 'pending', text: APPROVAL_EXPIRY_TEXT[kind] };
  }
  return { visible: false, kind: kind, state: state, tone: '', text: '' };
}

/**
 * A failed decision is not retried behind the user's back. Each stable code gets
 * its own next step; `reload` means the server state must be re-read before the
 * page shows anything again.
 */
function describeDecisionFailure(error) {
  const status = Number((error && error.status) || 0);
  const code = String((error && error.code) || '');
  const message = String((error && error.message) || '决定提交失败');
  const table = {
    approval_expired:
      '审批已过期（approval_expired）：决定没有保存。'
      + '下一步：重新提交产生这次操作的任务；本页不提供复活，也不会自动重试。',
    approval_not_pending:
      '审批已不可决定（approval_not_pending）：它已被决定、取消或过期。'
      + '请以服务端当前状态为准；本页不会自动复活。',
    approval_params_mismatch:
      '提案已变化（approval_params_mismatch）：页面上的参数哈希不是服务端当前的。'
      + '请重新打开详情核对真实目标与参数后再决定，不要照旧点确认。',
    decision_conflict:
      '已有并发的决定（decision_conflict）：请刷新详情，以服务端保存的那一次为准，本页不覆盖它。',
    approval_not_found:
      '审批不存在或不属于你（approval_not_found）：不会显示他人的提案，也不会自动重试。',
    approver_mismatch:
      '你不是这条审批的指定审批人（approver_mismatch）：需要由指定的人类审批人决定，本页不会绕过。',
    permission_revoked:
      '当前权限已被撤销（permission_revoked）：你的决定没有保存；请让仍获授权的人处理或重新提交任务。',
    policy_revoked:
      '连接器策略已被撤销（policy_revoked）：这次操作不能再继续，页面不会自动重试。',
  };
  return {
    code: code,
    status: status,
    expired: code === 'approval_expired' || code === 'approval_not_pending',
    reload: Boolean(code),
    text: table[code] || ('决定提交失败 [' + (code || ('HTTP ' + status)) + '] ' + message
      + '；请刷新详情，本页不会自动重试。'),
  };
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
    // Filled from the sealed proposal once the detail is loaded; the contract
    // Approval itself has no kind/title field.
    proposalKind: '',
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

// F04B/T087: a node approval is a business decision, not a capability proposal.
// The sealed node document carries the node identity, its display title and the
// definition node list; the page renders those real facts through the same
// artifact-content ACL as a capability proposal.
function nodeProposalSourcesText(proposal) {
  const value = proposal || {};
  const lines = [];
  if (value.node_id) {
    lines.push('approval node ' + value.node_id + ' (' + String(value.node_key || '') + ')');
  }
  if (Array.isArray(value.definition_nodes) && value.definition_nodes.length > 0) {
    lines.push('definition nodes ' + value.definition_nodes.join(', '));
  }
  if (value.snapshot_id) lines.push('execution_snapshot ' + value.snapshot_id);
  if (value.snapshot_sha256) lines.push('snapshot_sha256 ' + value.snapshot_sha256);
  if (value.manifest_sha256) lines.push('manifest_sha256 ' + value.manifest_sha256);
  return lines.join('\n');
}

/**
 * The page's one rendering of the sealed server proposal. Only facts the server
 * wrote are exposed; a missing member renders empty rather than a guess. A node
 * approval (kind=node_approval) renders its title/node/business-decision copy; a
 * capability proposal keeps exactly the T050 rendering.
 */
function describeProposal(proposal) {
  const value = proposal || {};
  const kind = String(value.kind || '');
  if (kind === 'node_approval') {
    const title = String(value.title || '');
    return {
      kind: kind,
      title: title,
      nodeID: String(value.node_id || ''),
      nodeKey: String(value.node_key || ''),
      runID: String(value.run_id || ''),
      operationID: '',
      logicalKey: '',
      targetText: '人工审批 · ' + (title || '未命名审批') + '（' + String(value.node_key || '') + '）',
      queryText: '',
      pathPrefix: '',
      method: '',
      paramsText: value.proposal === undefined ? '' : JSON.stringify(value.proposal, null, 2),
      paramsSHA256: String(value.params_sha256 || ''),
      destinationText: '业务决定（不授权后续外部写）',
      riskText: '业务审批',
      sourcesText: nodeProposalSourcesText(value),
      expiresAt: String(value.expires_at || ''),
      approvalVersion: Number(value.approval_version || 0),
    };
  }
  const target = value.target || {};
  return {
    kind: kind,
    title: '',
    nodeID: '',
    nodeKey: String(value.node_key || ''),
    runID: String(value.run_id || ''),
    operationID: String(value.operation_id || ''),
    logicalKey: String(value.logical_key || ''),
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

function decisionConfirmationText(decision, kind) {
  if (String(kind || '') === 'node_approval') {
    if (decision === 'approve') {
      return '批准只是业务决定：恢复后继续本次运行，但不会授权任何后续外部写工具；'
        + '后续写操作仍需要各自的 operation 审批。';
    }
    return '拒绝会让等待中的 Run 以 approval_rejected 失败。拒绝不会撤销已经发生的外部写入。';
  }
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
    displayedApproval.proposalKind = proposal.kind;
    const expiry = approvalExpiryNote(approval, proposal.kind);
    const expiryNote = document.getElementById('approval-expiry-note');
    if (expiryNote) {
      expiryNote.hidden = !expiry.visible;
      expiryNote.textContent = expiry.text;
      expiryNote.className = 'hint' + (expiry.tone === 'bad' ? ' status bad' : '');
      expiryNote.dataset.expiryKind = expiry.kind;
      expiryNote.dataset.expiryState = expiry.state;
    }
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
  if (!window.confirm(decisionConfirmationText(decision, displayedApproval.proposalKind))) return;
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
    const plan = describeDecisionFailure(error);
    const code = plan.code ? '[' + plan.code + '] ' : '';
    status.textContent = plan.text;
    status.className = 'status bad';
    document.getElementById('approve-button').disabled = true;
    document.getElementById('reject-button').disabled = true;
    if (plan.reload && displayedApproval) {
      // Re-read the server state so the page stops describing the decision
      // window the server just closed; the failure copy stays on screen.
      try {
        await loadApprovalDetail(displayedApproval.id);
        status.textContent = code + plan.text;
        status.className = 'status bad';
        document.getElementById('approve-button').disabled = true;
        document.getElementById('reject-button').disabled = true;
      } catch (reloadError) {
        // The refusal is already displayed; a failed re-read is not a second
        // decision and must not re-enable the buttons.
      }
    }
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
