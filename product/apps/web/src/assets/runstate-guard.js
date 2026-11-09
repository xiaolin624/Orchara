/*
 * C6.01 (#155): one complete readiness check for the shared run-state module.
 *
 * Why this file exists
 * --------------------
 * `/chat`, `/runs` and `/debug` load `/assets/runstream.js` before their page
 * scripts. If that request 404s, the script fails to parse, or the script parses
 * but does not export the full surface, the page used to keep running with its
 * page-local run-state rules — two different meanings for the same Run facts,
 * silently decided by how the page happened to be loaded (C6 design, deletion
 * table "旧 F03/F08 等 harness").
 *
 * This asset is loaded *after* the shared script and *before* the page script,
 * so it can tell the difference without depending on the thing it checks:
 *
 *   - 404 / parse error  -> no `OrcharaRunState` namespace at all (`missing`);
 *   - incomplete export  -> namespace exists but lacks required operations.
 *
 * The failure contract is the Q7 decision: show exactly "运行状态暂不可用",
 * pause the affected realtime observation (each page script asks `ready` before
 * starting a poll/SSE tick) and the controls marked `data-runstate-paused`.
 * Navigation, reload and independent page functions are never touched, and a
 * raw Run snapshot is never rendered as substitute state.
 *
 * When the dependency is ready this file changes nothing: `apply()` returns
 * immediately and the normal path stays byte-for-byte the page's existing one.
 */
(function (global) {
  'use strict';

  const MESSAGE = '运行状态暂不可用';

  // The complete explicit interface (see the OrcharaRunState namespace in
  // assets/runstream.js). `interfaceVersion` is a fixed number, not a range:
  // pages and their guard ship together, so a version mismatch is a dependency
  // failure rather than something to adapt silently.
  const REQUIRED = Object.freeze([
    Object.freeze({ name: 'interfaceVersion', type: 'number' }),
    Object.freeze({ name: 'view', type: 'function' }),
    Object.freeze({ name: 'failure', type: 'function' }),
    Object.freeze({ name: 'correct', type: 'function' }),
    Object.freeze({ name: 'snapshotCorrection', type: 'function' }),
    Object.freeze({ name: 'snapshotPath', type: 'function' }),
    Object.freeze({ name: 'safeSnapshotPath', type: 'function' }),
    Object.freeze({ name: 'isTerminal', type: 'function' }),
    Object.freeze({ name: 'stateLabel', type: 'function' }),
    Object.freeze({ name: 'phaseLabel', type: 'function' }),
    Object.freeze({ name: 'cancelAcceptedText', type: 'function' }),
    Object.freeze({ name: 'handlingText', type: 'function' }),
    Object.freeze({ name: 'shouldPoll', type: 'function' }),
    Object.freeze({ name: 'childSummary', type: 'function' }),
    Object.freeze({ name: 'artifactReadout', type: 'function' }),
    Object.freeze({ name: 'states', type: 'object' }),
    Object.freeze({ name: 'phases', type: 'object' }),
  ]);

  const ALL_NAMES = Object.freeze(REQUIRED.map((entry) => entry.name));

  /** Read the explicit interface without trusting (or touching) the page DOM. */
  function check(scope) {
    const root = scope && typeof scope === 'object' ? scope : global;
    const api = root.OrcharaRunState;
    if (!api || typeof api !== 'object') {
      return Object.freeze({
        ready: false,
        reason: 'missing',
        message: MESSAGE,
        missing: ALL_NAMES.slice(),
        interfaceVersion: 0,
      });
    }
    const missing = [];
    for (const entry of REQUIRED) {
      const value = api[entry.name];
      const present =
        entry.name === 'interfaceVersion'
          ? value === 1
          : typeof value === entry.type;
      if (!present) {
        missing.push(entry.name);
      }
    }
    if (missing.length > 0) {
      return Object.freeze({
        ready: false,
        reason: 'incomplete',
        message: MESSAGE,
        missing: Object.freeze(missing),
        interfaceVersion:
          typeof api.interfaceVersion === 'number' ? api.interfaceVersion : 0,
      });
    }
    return Object.freeze({
      ready: true,
      reason: 'ok',
      message: MESSAGE,
      missing: Object.freeze([]),
      interfaceVersion: api.interfaceVersion,
    });
  }

  function reasonLine(state) {
    if (state.reason === 'incomplete') {
      return '共享状态脚本未完整导出（缺少 ' + state.missing.join('、') + '）';
    }
    return '共享状态脚本未加载（404 或语法错误）';
  }

  /**
   * Apply the failure contract to a page: one explicit banner, and every
   * control the page marked `data-runstate-paused` disabled. Ready pages are
   * left completely alone. Idempotent, and safe to call from a page bootstrap.
   */
  function apply(doc) {
    if (status.ready || !doc || typeof doc.getElementById !== 'function') {
      return status;
    }
    const banner = doc.getElementById('runstate-status');
    if (banner) {
      banner.hidden = false;
      banner.textContent =
        MESSAGE + '：' + reasonLine(status) +
        '。已暂停运行状态观察与相关操作；导航和重新加载仍可使用。';
      banner.className = 'status bad';
      banner.setAttribute('data-runstate-status', 'unavailable');
      // The exact Q7 message, separate from the explanatory suffix, so acceptance
      // can assert the literal text rather than only a prefix.
      banner.setAttribute('data-runstate-message', MESSAGE);
      banner.setAttribute('data-runstate-reason', status.reason);
      banner.setAttribute('role', 'alert');
    }
    const controls =
      typeof doc.querySelectorAll === 'function'
        ? doc.querySelectorAll('[data-runstate-paused]')
        : [];
    for (let index = 0; index < controls.length; index += 1) {
      const control = controls[index];
      if (!control) {
        continue;
      }
      control.disabled = true;
      if (typeof control.setAttribute === 'function') {
        control.setAttribute('aria-disabled', 'true');
        control.setAttribute('data-runstate-paused-applied', 'true');
      }
    }
    return status;
  }

  const status = Object.freeze(check(global));

  const guard = Object.freeze({
    MESSAGE: MESSAGE,
    REQUIRED: REQUIRED,
    status: status,
    ready: status.ready,
    check: check,
    apply: apply,
  });

  global.OrcharaRunStateGuard = guard;

  // The page scripts load after this asset, so the static controls already
  // exist; the banner and the paused controls must be right before any page
  // code runs. A missing document (pure Node harness) is a no-op.
  if (typeof document !== 'undefined') {
    apply(document);
  }
})(typeof globalThis !== 'undefined' ? globalThis : window);
