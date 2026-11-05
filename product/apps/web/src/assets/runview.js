/*
 * F04B/T083 run view: the node-state table of a debug Run.
 *
 * This is a classic browser script (no build step, no module loader): the debug
 * page loads it after `app.js` and before `debug.js`. The pure functions at the
 * top are also executed by `tests/f04b/runview.test.mjs` under Node with a `vm`
 * context, so the shipped bytes are what the tests assert.
 *
 * Data surface (documented in reports/f04b/T083-runview.md; **not** a contract
 * change):
 *
 *   - contract SSE `node.status` events: `{node_id, state, attempt_no}` with
 *     state in pending/running/succeeded/failed/skipped (waiting is reserved for
 *     T087). `skipped` is an unselected branch, never a failure.
 *   - non-contract `GET /api/v1/runs/{id}/node-results` (owner-only) points at
 *     the `kind="data"` artifact each node registered.
 *   - the condition node's data artifact is the non-contract convention
 *     `{"port": "yes|no|...", "matched_case": 0|1|...|"default"}`.
 *   - the join node's data artifact is the COMPILER-CONTRACT envelope
 *     `{"results": {"<source>": {"status": ..., "value": {...}, "error_code": ...}}}`.
 *   - the artifact bytes are read through the contract route
 *     `GET /api/v1/artifacts/{id}/content` (owner-only).
 *   - the run's terminal state and `error_code` come from `GET /api/v1/runs/{id}`.
 *
 * Nothing here invents progress: a state is only rendered from a committed event,
 * from a committed Run terminal state, or is labelled as inferred from the
 * registered node results (the SSE replay window can be gone: 410).
 */

// State ranks inside one attempt: a later -- or replayed -- frame must never
// demote a committed fact, and a fail-fast failure outranks a success.
const RUNVIEW_STATE_RANK = {
  pending: 1,
  waiting: 2,
  running: 3,
  skipped: 4,
  succeeded: 5,
  failed: 6,
};

const RUNVIEW_STATE_TEXT = {
  pending: '等待',
  waiting: '等待中',
  running: '运行中',
  skipped: '已跳过',
  succeeded: '成功',
  failed: '失败',
};

/**
 * Fold committed `node.status` events into `{node_id: {state, attempt_no}}`.
 *
 * Events may be out of order and repeated (a reconnect with Last-Event-ID
 * replays frames the page already applied), so the fold is by attempt first and
 * by state rank second, not "last write wins". An unknown state, a missing node
 * id or a non-node.status event is ignored: the page only renders facts the
 * server committed.
 */
function aggregateNodeStates(events) {
  const aggregated = {};
  const source = Array.isArray(events) ? events : [];
  for (const event of source) {
    if (!event || typeof event !== 'object' || event.type !== 'node.status') {
      continue;
    }
    const payload = event.payload;
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      continue;
    }
    const nodeID = String(payload.node_id || '');
    const state = String(payload.state || '');
    if (nodeID === '' || !Object.prototype.hasOwnProperty.call(RUNVIEW_STATE_RANK, state)) {
      continue;
    }
    const attemptNo = Number(payload.attempt_no);
    const attempt = Number.isSafeInteger(attemptNo) && attemptNo >= 0 ? attemptNo : 0;
    // The contract node.status payload has no error_code; a future extension is
    // only meaningful for a failed node, so a code on any other state is dropped.
    const errorCode =
      state === 'failed' && typeof payload.error_code === 'string' && payload.error_code !== ''
        ? payload.error_code
        : '';
    const current = aggregated[nodeID];
    if (!current) {
      aggregated[nodeID] = { state: state, attempt_no: attempt };
      if (errorCode !== '') {
        aggregated[nodeID].error_code = errorCode;
      }
      continue;
    }
    if (attempt > current.attempt_no) {
      // A new attempt supersedes the previous attempt entirely: its outcome and
      // error code are no longer this node's fact.
      aggregated[nodeID] = { state: state, attempt_no: attempt };
      if (errorCode !== '') {
        aggregated[nodeID].error_code = errorCode;
      }
      continue;
    }
    if (attempt === current.attempt_no && RUNVIEW_STATE_RANK[state] > RUNVIEW_STATE_RANK[current.state]) {
      current.state = state;
    }
    if (errorCode !== '' && !current.error_code) {
      current.error_code = errorCode;
    }
  }
  return aggregated;
}
