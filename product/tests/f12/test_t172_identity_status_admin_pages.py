"""T8 (GitHub #172): identity, status and admin pages at delivery quality.

The four pages this ticket owns are the ones a person meets before they can use
the product (/login, /activate), the one that says whether the platform is
usable (/status) and the one that governs everyone else (/admin). The static
seam below parses the committed bytes of the four pages plus their committed
styles; the execution seam runs each page's real inline ``<script>`` under Node
with a minimal DOM shim, exactly like ``test_t095`` and ``test_t165``.

What this file proves:

* login: the brand narrative + form split, all five failure codes with distinct
  tones, and a submit that disables itself and reports the API's own code;
* activate: the one-time token flow, the always-visible password rules, the
  mismatch/success states;
* status: the overview strip, the four health cards, the ``#checks`` list and
  the honest "unknown is not healthy" rule (a stub environment is never sold as
  production acceptance);
* admin: five sections over the unchanged panel ids, destructive actions
  separated and confirmed before the request, and an audit section that names
  the real audited actions instead of inventing a log UI.

What it does not prove: browser layout, real reflow at 375px, or real contrast
in rendered pixels. Those stay in ``tools/verify_frontend.py`` (real Chrome).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from support import stack as st

WEB = st.WEB_DIR
NODE = shutil.which("node")

LOGIN_CODES = [
    "invalid_credentials",
    "rate_limited",
    "principal_disabled",
    "session_revoked",
    "session_expired",
]
ACTIVATE_CODES = [
    "activation_invalid",
    "activation_expired",
    "activation_consumed",
    "validation_failed",
]
AUDITED_ACTIONS = [
    "user.invited",
    "roles.updated",
    "grant.created",
    "grant.revoked",
    "service_account.created",
    "api_key.created",
    "api_key.revoked",
    "principal.disabled",
    "principal.enabled",
    "auth.login",
    "auth.logout",
    "user.activated",
]


def read_page(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def read_asset(name: str) -> str:
    return (WEB / "assets" / name).read_text(encoding="utf-8")


def page_style(name: str) -> str:
    match = re.search(r"<style>(.*?)</style>", read_page(name), re.S)
    return match.group(1) if match else ""


def combined_css(name: str) -> str:
    return read_asset("design.css") + "\n" + page_style(name)


def tone_values(css: str, codes: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for code in codes:
        rule = re.search(r'\[data-code="' + re.escape(code) + r'"\]\s*\{([^}]*)\}', css)
        assert rule, f"no tone rule for failure code {code}"
        value = re.search(r"--tone-soft:\s*([^;]+);", rule.group(1))
        assert value, f"the tone rule for {code} does not set --tone-soft"
        values[code] = value.group(1).strip()
    return values


# ---------------------------------------------------------------------------
# 1. login.html: brand narrative + form column, failure-code language
# ---------------------------------------------------------------------------
def test_login_page_splits_the_brand_story_from_the_form_column() -> None:
    html = read_page("login.html")
    for marker in (
        'class="auth-layout"',
        'class="auth-story"',
        'class="auth-story-copy"',
        'class="auth-rule"',  # the short terracotta rule from direction C
        'class="auth-main"',
        'class="auth-mobile-brand"',
        'id="login-form"',
        'id="login"',
        'id="password"',
        'id="login-status"',
        'role="status"',
        'autocomplete="current-password"',
        'data-auth="optional"',
        'href="/activate"',
    ):
        assert marker in html, f"login.html is missing {marker!r}"

    # Three capability notes, each a real product fact (not sample counters).
    points = re.search(r'<ul class="auth-points">(.*?)</ul>', html, re.S)
    assert points, "login.html has no capability list"
    assert points.group(1).count("<li>") == 3, points.group(1)
    for claim in ("统一编排", "可审计的运行", "团队级发布"):
        assert claim in points.group(1), claim

    # The form column is the 340px rail the ticket asks for, not a 900px slab.
    # The T8 block appends the override; the cascade makes the last rule win.
    css = combined_css("login.html")
    columns = re.findall(r"\.auth-main \.page-content\s*\{([^}]*)\}", css)
    assert columns, "the auth form column width is not stated"
    assert any(re.search(r"max-width:\s*340px", body) for body in columns), columns

    # 375px: the story is hidden and a compact brand block takes its place.
    mobile = re.search(r"@media \(max-width: 720px\)\s*\{(.*?)\n\}", css, re.S)
    assert mobile, "no phone breakpoint is defined for the auth frame"
    assert "auth-story" in mobile.group(1) and "display: none" in mobile.group(1)
    assert "auth-mobile-brand" in mobile.group(1)


def test_login_page_names_every_failure_code_with_a_distinct_tone() -> None:
    html = read_page("login.html")
    css = combined_css("login.html")
    for code in LOGIN_CODES:
        assert code in html, f"login.html does not explain {code}"
        assert f'data-code="{code}"' in html, f"login.html does not expose {code} as a tone"
    # The five explanations the page exists to give, in the API's own words.
    for phrase in ("登录名或密码不正确", "失败次数过多", "已被禁用", "已被撤销", "已过期"):
        assert phrase in html, f"login.html lost the {phrase!r} explanation"
    tones = tone_values(css, LOGIN_CODES)
    assert len(set(tones.values())) == len(LOGIN_CODES), (
        "the five failure codes must use five distinct colour tones: " + repr(tones)
    )
    # The live status element carries the tone of the code it just received.
    assert "loginStatus.setAttribute('data-code'" in html
    assert "LOGIN_TONES" in html


def test_login_submit_disables_the_button_and_explains_the_api_code(
    node_binary: str, tmp_path
) -> None:
    scenario = r"""
  function buildLogin(search) {
    const page = createPage({ pathname: '/login', search: search || '' });
    page.make('form', 'login-form', { method: 'post' });
    page.make('input', 'login', { name: 'login' });
    page.make('input', 'password', { name: 'password', type: 'password' });
    const status = page.make('p', 'login-status', { role: 'status' });
    const submit = page.make('button', '', { class: 'btn btn-primary btn-block auth-submit', type: 'submit' });
    page.context.api = async function (requestPath) {
      page.requests.push({ path: requestPath });
      throw Object.assign(new Error('login rejected'), { code: 'invalid_credentials', status: 401 });
    };
    page.context.identity = async function () { return { kind: 'user', roles: ['user'] }; };
    page.context.OrcharaNav = { isHuman: function () { return true; } };
    page.runInline('login.html');
    return { page: page, status: status, submit: submit };
  }

  const login = buildLogin('');
  login.page.byId.get('login').value = 't8.reader';
  login.page.byId.get('password').value = 'correct horse battery staple';
  login.page.byId.get('login-form').submit();
  assert.strictEqual(login.submit.disabled, true,
    'the submit button must be disabled while the request is in flight');
  assert.ok(login.status.textContent.indexOf('正在登录') !== -1,
    'the page must say it is working: ' + login.status.textContent);
  assert.strictEqual(login.page.byId.get('login-form').getAttribute('aria-busy'), 'true');
  await flush();
  assert.strictEqual(login.submit.disabled, false,
    'a failed login must give the button back instead of leaving the form dead');
  assert.ok(login.status.className.indexOf('bad') !== -1,
    'a refused login is a failure state, not a success one');
  assert.strictEqual(login.status.getAttribute('data-code'), 'invalid_credentials');
  assert.ok(login.status.textContent.indexOf('登录名或密码不正确') !== -1,
    'the API code must be explained on the page: ' + login.status.textContent);
  assert.strictEqual(login.page.requests.length, 1, 'exactly one login request is sent');

  // The ?error= entry reason is still explained, and it is the same tone map.
  const disabled = buildLogin('?error=principal_disabled');
  assert.ok(disabled.status.textContent.indexOf('已被禁用') !== -1,
    'principal_disabled must be explained: ' + disabled.status.textContent);
  assert.strictEqual(disabled.status.getAttribute('data-code'), 'principal_disabled');
  const revoked = buildLogin('?error=session_revoked');
  assert.ok(revoked.status.textContent.indexOf('已被撤销') !== -1,
    'session_revoked must be explained: ' + revoked.status.textContent);
  assert.strictEqual(disabled.page.navigations.length + revoked.page.navigations.length, 0,
    'explaining an anonymous entry reason must not redirect to itself');
  process.stdout.write('t8 login ok\n');
"""
    run_scenario(node_binary, tmp_path, "t8-login", scenario, "t8 login ok")


# ---------------------------------------------------------------------------
# 2. activate.html: one-time token, password rules, success and failure
# ---------------------------------------------------------------------------
def test_activate_page_keeps_the_flow_and_the_password_rules_visible() -> None:
    html = read_page("activate.html")
    for marker in (
        'id="activate-form"',
        'id="activation-token"',
        'id="password"',
        'id="confirm-password"',
        'id="activate-status"',
        'id="activate-success"',
        'role="status"',
        'minlength="12"',
        'href="/login"',
        'data-auth="optional"',
        'body: { activation_token: activationToken, password }',
    ):
        assert marker in html, f"activate.html is missing {marker!r}"
    assert html.count("<form") == 1, "activate.html must keep exactly one form"
    assert re.search(r'<form id="activate-form"[^>]*method="post"', html), "the form must post"
    assert re.search(r'<textarea[^>]*id="activation-token"', html), "the token is a textarea"

    # The rules are always on screen, not an error that appears after a refusal.
    rules = re.search(r'<ul class="password-rules">(.*?)</ul>', html, re.S)
    assert rules, "activate.html has no standing password rules"
    assert "至少 12" in rules.group(1) and "不能与登录名相同" in rules.group(1), rules.group(1)
    success = re.search(r'id="activate-success"(.*?)</div>', html, re.S)
    assert success and "去登录" in success.group(1), "the success state needs its 去登录 action"

    # The four failure reasons the API really returns are named and toned.
    css = combined_css("activate.html")
    for code in ACTIVATE_CODES:
        assert f'data-code="{code}"' in html, f"activate.html does not explain {code}"
    assert "令牌无效" in html and "已过期" in html and "已被使用" in html
    tones = tone_values(css, ACTIVATE_CODES)
    assert len(set(tones.values())) == len(ACTIVATE_CODES), repr(tones)


def test_activate_page_reports_a_mismatch_and_the_success_state(
    node_binary: str, tmp_path
) -> None:
    scenario = r"""
  const page = createPage({ pathname: '/activate' });
  page.make('form', 'activate-form', { method: 'post' });
  page.make('textarea', 'activation-token');
  page.make('input', 'password', { type: 'password' });
  page.make('input', 'confirm-password', { type: 'password' });
  const status = page.make('p', 'activate-status', { role: 'status' });
  const success = page.make('div', 'activate-success', { hidden: '' });
  const submit = page.make('button', '', { class: 'auth-submit', type: 'submit' });
  page.context.api = async function (requestPath, options) {
    page.requests.push({ path: requestPath, body: options && options.body });
    return { id: '11111111-1111-4111-8111-111111111111', state: 'activated' };
  };
  page.runInline('activate.html');

  page.byId.get('activation-token').value = 'one-time-token';
  page.byId.get('password').value = 'correct horse battery staple';
  page.byId.get('confirm-password').value = 'a different password';
  page.byId.get('activate-form').submit();
  await flush();
  assert.strictEqual(page.requests.length, 0,
    'a mismatched confirmation must not spend the one-time token on the server');
  assert.ok(status.textContent.indexOf('两次输入的密码不一致') !== -1, status.textContent);
  assert.ok(status.className.indexOf('bad') !== -1, status.className);

  page.byId.get('confirm-password').value = 'correct horse battery staple';
  page.byId.get('activate-form').submit();
  assert.strictEqual(submit.disabled, true,
    'the activation button must be disabled while the request is in flight');
  await flush();
  assert.strictEqual(page.requests.length, 1, 'one activation request is sent');
  assert.strictEqual(page.requests[0].path, '/api/v1/auth/activate');
  assert.strictEqual(page.requests[0].body.activation_token, 'one-time-token');
  assert.strictEqual(page.byId.get('activate-form').hidden, true,
    'the one-time form is replaced by the success state');
  assert.strictEqual(success.hidden, false, 'the success state must be visible');
  assert.strictEqual(page.byId.get('activation-token').value, '',
    'the spent token must be cleared from the page');
  assert.ok(status.textContent.indexOf('激活成功') !== -1, status.textContent);
  assert.ok(status.className.indexOf('ok') !== -1, status.className);
  assert.strictEqual(submit.disabled, false);
  process.stdout.write('t8 activate ok\n');
"""
    run_scenario(node_binary, tmp_path, "t8-activate", scenario, "t8 activate ok")


# ---------------------------------------------------------------------------
# 3. index.html (/status): overview, health cards, honest unknown
# ---------------------------------------------------------------------------
def test_status_page_has_the_overview_health_cards_and_environment_facts() -> None:
    html = read_page("index.html")
    for marker in (
        "status-overview",
        "data-overall-status",
        "data-profile-badge",
        'id="stamp"',
        'data-health="api"',
        'data-health="database"',
        'data-health="maintenance"',
        'data-health="objects"',
        'id="live"',
        'id="ready"',
        'id="checks"',
        'id="version"',
        'id="uptime"',
        'id="environment-profile"',
        'id="environment-stub"',
        'data-auth="optional"',
        "/health/live",
        "/api/status",
        "status === 'alive'",
        "运行中",
        "href=\"/login\"",
        'src="/assets/nav.js"',
    ):
        assert marker in html, f"index.html is missing {marker!r}"

    # dev-stub is described for what it is; the page never implies acceptance.
    assert "不代表真实模型" in html or "非真实模型" in html
    assert "生产验收" in html
    assert "未纳入检查" in html, "maintenance/object storage must be shown as unchecked, not healthy"
    # The dependency names the API actually answers with today.
    assert "database" in html
    # The refresh stays a real request cycle, not a fabricated countdown.
    assert "setInterval(refresh, 3000)" in html


def test_status_page_never_reports_unknown_as_healthy(node_binary: str, tmp_path) -> None:
    scenario = r"""
  function jsonResponse(payload) {
    return { ok: true, status: 200, json: async function () { return payload; } };
  }
  function buildStatus(fetchImpl) {
    const page = createPage({ pathname: '/status', fetch: fetchImpl });
    page.make('span', 'live');
    page.make('span', 'ready');
    page.make('span', 'version');
    page.make('span', 'uptime');
    page.make('ul', 'checks');
    page.make('dd', 'stamp');
    page.make('span', 'environment-profile');
    page.make('span', 'environment-stub');
    const overall = page.make('strong', '', { 'data-overall-status': '' });
    const badge = page.make('span', '', { 'data-profile-badge': '', class: 'pill' });
    page.make('span', '', { 'data-health-state': 'api', class: 'pill' });
    const database = page.make('span', '', { 'data-health-state': 'database', class: 'pill' });
    page.make('span', '', { 'data-health-state': 'maintenance', class: 'pill' });
    page.make('span', '', { 'data-health-state': 'objects', class: 'pill' });
    page.make('button', '', { 'data-refresh': '' });
    page.runInline('index.html');
    return { page: page, overall: overall, badge: badge, database: database };
  }

  // All healthy: the page may say so, and it names the stub environment.
  const healthy = buildStatus(async function (requestPath) {
    if (requestPath === '/health/live') {
      return jsonResponse({ status: 'alive', version: '0.0.1-test', uptime_seconds: 65 });
    }
    return jsonResponse({
      status: 'ready', profile: 'dev-stub', stub_results: true,
      checks: { database: { status: 'ok' } },
    });
  });
  await flush();
  assert.strictEqual(healthy.overall.textContent, '全部正常', healthy.overall.textContent);
  assert.strictEqual(healthy.badge.textContent, 'dev-stub', healthy.badge.textContent);
  assert.strictEqual(healthy.badge.getAttribute('data-tone'), 'warning',
    'a dev-stub environment is a caveat, not a neutral badge');
  assert.strictEqual(healthy.database.getAttribute('data-tone'), 'success');
  assert.ok(healthy.page.byId.get('stamp').textContent.indexOf('最后更新') !== -1,
    healthy.page.byId.get('stamp').textContent);
  assert.ok(healthy.page.byId.get('environment-stub').textContent.indexOf('stub 结果') !== -1);
  assert.strictEqual(healthy.page.byId.get('checks').children.length, 1);

  // A failed dependency is degradation, and the database card turns red.
  const degraded = buildStatus(async function (requestPath) {
    if (requestPath === '/health/live') {
      return jsonResponse({ status: 'alive', version: '0.0.1-test', uptime_seconds: 65 });
    }
    return jsonResponse({
      status: 'not_ready', profile: 'dev-stub', stub_results: true,
      checks: { database: { status: 'unavailable', detail: 'dependency check failed' } },
    });
  });
  await flush();
  assert.strictEqual(degraded.overall.textContent, '部分降级', degraded.overall.textContent);
  assert.strictEqual(degraded.database.getAttribute('data-tone'), 'danger');
  assert.strictEqual(degraded.page.byId.get('checks').children[0].getAttribute('data-tone'), 'danger');

  // An unreachable status API is unknown. Unknown must never read as healthy.
  const unreachable = buildStatus(async function () { throw new Error('connection refused'); });
  await flush();
  assert.notStrictEqual(unreachable.overall.textContent, '全部正常',
    'unknown must not be written as normal: ' + unreachable.overall.textContent);
  assert.ok(unreachable.overall.textContent.indexOf('未知') !== -1,
    unreachable.overall.textContent);
  assert.notStrictEqual(unreachable.overall.getAttribute('data-tone'), 'success');
  assert.ok(unreachable.page.byId.get('stamp').textContent.indexOf('请求失败') !== -1,
    unreachable.page.byId.get('stamp').textContent);
  assert.strictEqual(unreachable.page.byId.get('live').textContent, '无法连接');
  process.stdout.write('t8 status ok\n');
"""
    run_scenario(node_binary, tmp_path, "t8-status", scenario, "t8 status ok")


# ---------------------------------------------------------------------------
# 4. admin.html: five sections, danger separation, audit truth
# ---------------------------------------------------------------------------
def test_admin_page_has_five_sections_over_the_unchanged_panels() -> None:
    html = read_page("admin.html")
    assert "<title>管理台 · Orchara</title>" in html
    for section in ("users", "invite", "grants", "service-accounts", "audit"):
        assert f'data-admin-section="{section}"' in html, f"admin.html has no {section} section"
    for heading in ("用户与角色", "邀请", "授权", "服务账号与 API Key", "审计"):
        assert heading in html, f"admin.html has no {heading!r} section heading"

    # Every business id the page already owned stays; the panels are the seam.
    for marker in (
        'id="auth-status"', 'id="identity-principal"', 'id="identity-roles"',
        'id="logout-button"', 'id="signin-form"', 'id="login"', 'id="password"',
        'id="invite-panel"', 'id="invite-form"', 'id="invite-login"',
        'id="invite-display-name"', 'id="invite-result"', 'id="invite-status"',
        'id="activation-token"', 'id="roles-panel"', 'id="roles-form"',
        'id="roles-principal"', 'id="roles-status"', 'id="grants-panel"',
        'id="grant-form"', 'id="grant-principal"', 'id="grant-resource"',
        'id="grant-action"', 'id="grant-expires"', 'id="grant-id"',
        'id="grant-result"', 'id="grant-status"', 'id="revoke-form"',
        'id="revoke-grant-id"', 'id="revoke-status"', 'id="service-accounts-panel"',
        'id="service-account-form"', 'id="sa-display-name"', 'id="sa-principal-id"',
        'id="sa-responsible"', 'id="sa-result"', 'id="sa-status"',
        'id="api-keys-panel"', 'id="api-key-form"', 'id="api-key-id"',
        'id="api-key-principal"', 'id="api-key-expires"',
        'id="api-key-scope-application-read"', 'id="api-key-result"',
        'id="api-key-secret"', 'id="api-key-status"', 'id="api-key-revoke-form"',
        'id="revoke-api-key-id"', 'id="api-key-revoke-status"',
        'id="principals-panel"', 'id="disable-form"', 'id="disable-principal"',
        'id="disable-reason"', 'id="disable-state"', 'id="disable-status"',
    ):
        assert marker in html, f"admin.html lost {marker!r}"
    # The nine real forms are unchanged (1 login + 1 activation + 9 admin = 11).
    assert html.count("<form") == 9, "admin.html must keep its nine forms"
    # A role change is stated to take effect immediately, not at next login.
    assert "角色变更立即生效" in html
    assert "立即生效" in html


def test_admin_page_separates_and_confirms_destructive_actions() -> None:
    html = read_page("admin.html")
    for danger in ("disable", "revoke-grant", "revoke-api-key"):
        assert f'data-danger="{danger}"' in html, f"no danger zone marker for {danger}"
    css = page_style("admin.html")
    zone = re.search(r"\.danger-zone\s*\{([^}]*)\}", css)
    assert zone, "admin.html has no .danger-zone style"
    assert "var(--danger" in zone.group(1), zone.group(1)
    assert zone.group(1).count("var(--danger") >= 1
    # Confirmation happens in the page before any write, for all three actions.
    assert html.count("confirm(") >= 3, "every destructive write needs a confirmation"
    for phrase in ("确认撤销授权", "确认撤销 API key", "确认禁用"):
        assert phrase in html, f"missing confirmation copy: {phrase}"
    # The result feedback written by the handlers is untouched.
    for marker in ("setStatus(revokeStatus", "setStatus(apiKeyRevokeStatus", "setStatus(disableStatus"):
        assert marker in html, marker


def test_admin_audit_section_names_real_audited_actions_without_faking_a_log() -> None:
    html = read_page("admin.html")
    audit = re.search(r'data-admin-section="audit"(.*?)$', html, re.S)
    assert audit, "admin.html has no audit section"
    block = audit.group(1)
    for action in AUDITED_ACTIONS:
        assert action in block, f"the audit section does not name the real action {action}"
    # No read API exists for audit events in this version; the page says so
    # instead of rendering a fabricated, undated log table.
    assert "没有提供审计记录的读取接口" in block
    assert "不展示" in block or "不伪造" in block
    # The source table is the platform audit table, and the reason field is the
    # human explanation that lands there.
    assert "audit_events" in block
    assert "原因必填" in html or "原因（必填" in html


def test_admin_confirmation_gate_blocks_unconfirmed_writes(node_binary: str, tmp_path) -> None:
    scenario = r"""
  const page = createPage({ pathname: '/admin' });
  for (const id of [
    'revoke-grant-id', 'revoke-api-key-id', 'disable-principal', 'disable-reason', 'disable-state',
  ]) {
    page.make('div', id);
  }
  page.context.requireLogin = async function () {
    return { principal_id: 'admin-1', kind: 'user', roles: ['admin'] };
  };
  page.context.csrf = async function () { return 'csrf-t8'; };
  page.context.logout = async function () { return true; };
  page.context.api = async function (requestPath, options) {
    page.requests.push({ path: requestPath, init: options || {} });
    if (requestPath.indexOf('/api/v1/admin/principals/') === 0) {
      return { id: 'principal-1', state: 'disabled' };
    }
    if (requestPath.indexOf('/api/v1/admin/api-keys/') === 0) {
      return { id: 'key-1', state: 'revoked' };
    }
    return { id: 'grant-1', state: 'revoked' };
  };
  page.runInline('admin.html');
  await flush();
  assert.ok(page.byId.get('identity-principal').textContent.indexOf('admin-1') !== -1,
    'the identity read is rendered');
  assert.strictEqual(page.byId.get('grants-panel').hidden, false,
    'an administrator sees the granted panels');

  page.byId.get('revoke-grant-id').value = 'grant-1';
  page.byId.get('revoke-api-key-id').value = 'key-1';
  page.byId.get('disable-principal').value = 'principal-1';
  page.byId.get('disable-reason').value = '离职交接';
  page.byId.get('disable-state').value = 'true';

  page.context.confirm = function () { return false; };
  page.byId.get('revoke-form').submit();
  page.byId.get('api-key-revoke-form').submit();
  page.byId.get('disable-form').submit();
  await flush();
  assert.strictEqual(page.requests.length, 0,
    'an unconfirmed destructive action must not reach the server: ' + JSON.stringify(page.requests));

  page.context.confirm = function () { return true; };
  page.byId.get('api-key-revoke-form').submit();
  await flush();
  assert.strictEqual(page.requests.length, 1);
  assert.strictEqual(page.requests[0].init.method, 'DELETE');
  assert.strictEqual(page.requests[0].path, '/api/v1/admin/api-keys/key-1');
  assert.strictEqual(page.requests[0].init.csrf, 'csrf-t8', 'the write still carries CSRF');
  assert.ok(page.byId.get('api-key-revoke-status').textContent.indexOf('已撤销 API key') !== -1,
    page.byId.get('api-key-revoke-status').textContent);

  page.byId.get('revoke-form').submit();
  await flush();
  assert.strictEqual(page.requests[1].path, '/api/v1/admin/grants/grant-1');
  assert.strictEqual(page.requests[1].init.method, 'DELETE');
  assert.ok(page.byId.get('revoke-status').textContent.indexOf('已撤销授权') !== -1,
    page.byId.get('revoke-status').textContent);

  page.byId.get('disable-form').submit();
  await flush();
  assert.strictEqual(page.requests[2].init.method, 'PATCH');
  assert.strictEqual(page.requests[2].path, '/api/v1/admin/principals/principal-1');
  // The body was built inside the vm realm, so compare the JSON shape.
  assert.strictEqual(
    JSON.stringify(page.requests[2].init.body),
    JSON.stringify({ disabled: true, reason: '离职交接' }),
  );
  assert.ok(page.byId.get('disable-status').textContent.indexOf('已禁用 principal principal-1') !== -1,
    page.byId.get('disable-status').textContent);
  process.stdout.write('t8 admin ok\n');
"""
    run_scenario(node_binary, tmp_path, "t8-admin", scenario, "t8 admin ok")


# ---------------------------------------------------------------------------
# Node harness: the real committed inline script, a minimal DOM shim
# ---------------------------------------------------------------------------
HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');

const webDir = __WEB_DIR__;

function camel(name) {
  return String(name).replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
}

class El {
  constructor(tag, id, attributes) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this._text = '';
    this.hidden = false;
    this.className = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.dataset = {};
    this.attributes = {};
    const attrs = attributes || {};
    for (const key of Object.keys(attrs)) this.setAttribute(key, attrs[key]);
  }
  get textContent() {
    if (this.children.length) {
      return this.children.map(function (child) {
        return child && child.textContent !== undefined ? child.textContent : '';
      }).join('');
    }
    return this._text;
  }
  set textContent(value) {
    this._text = String(value === undefined || value === null ? '' : value);
    this.children = [];
  }
  appendChild(child) { this.children.push(child); return child; }
  append() {
    for (let index = 0; index < arguments.length; index += 1) {
      const node = arguments[index];
      this.appendChild(typeof node === 'string' ? { nodeType: 3, textContent: node } : node);
    }
  }
  replaceChildren() {
    this.children = [];
    this._text = '';
    for (let index = 0; index < arguments.length; index += 1) {
      const node = arguments[index];
      this.appendChild(typeof node === 'string' ? { nodeType: 3, textContent: node } : node);
    }
  }
  addEventListener(type, handler) {
    (this._listeners[type] = this._listeners[type] || []).push(handler);
  }
  dispatch(type, event) {
    const payload = event || {};
    if (typeof payload.preventDefault !== 'function') payload.preventDefault = function () {};
    for (const handler of this._listeners[type] || []) handler(payload);
  }
  click() { this.dispatch('click'); }
  submit() { this.dispatch('submit'); }
  setAttribute(name, value) {
    const key = String(name);
    this.attributes[key] = String(value);
    if (key === 'hidden') this.hidden = true;
    if (key === 'class') this.className = String(value);
    if (key === 'id') this.id = String(value);
    if (key.indexOf('data-') === 0) this.dataset[camel(key.slice(5))] = String(value);
  }
  getAttribute(name) {
    if (name === 'hidden') return this.hidden ? '' : null;
    if (Object.prototype.hasOwnProperty.call(this.attributes, name)) return this.attributes[name];
    return null;
  }
  hasAttribute(name) { return this.getAttribute(name) !== null; }
}

function matches(element, selector) {
  let text = String(selector === undefined || selector === null ? '' : selector).trim();
  if (!text) return false;
  if (text.slice(-8) === ':checked') {
    text = text.slice(0, -8);
    if (!element.checked) return false;
  }
  const attribute = text.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
  if (attribute) {
    const value = element.getAttribute(attribute[1]);
    if (value === null) return false;
    return attribute[2] === undefined ? true : value === attribute[2];
  }
  if (text.charAt(0) === '#') return element.id === text.slice(1);
  if (text.charAt(0) === '.') return String(element.className).split(/\s+/).indexOf(text.slice(1)) !== -1;
  return element.tagName === text.toUpperCase();
}

function createPage(options) {
  const opts = options || {};
  const registry = [];
  const byId = new Map();
  function register(element) {
    registry.push(element);
    if (element.id) byId.set(element.id, element);
    return element;
  }
  function make(tag, id, attributes) { return register(new El(tag, id, attributes)); }
  function queryAll(selector) {
    const parts = String(selector === undefined || selector === null ? '' : selector)
      .split(',')
      .map(function (part) { return part.trim(); });
    return registry.filter(function (element) {
      return parts.some(function (part) { return matches(element, part); });
    });
  }
  const document = {
    body: make('body'),
    getElementById: function (id) { return byId.get(id) || make('div', id); },
    createElement: function (tag) { return make(tag); },
    querySelector: function (selector) { return queryAll(selector)[0] || null; },
    querySelectorAll: queryAll,
    addEventListener: function () {},
  };
  const navigations = [];
  const requests = [];
  const context = {
    console: console,
    document: document,
    URLSearchParams: URLSearchParams,
    location: {
      pathname: opts.pathname || '/',
      search: opts.search || '',
      assign: function (url) { navigations.push(String(url)); },
    },
    setTimeout: function () { return 0; },
    clearTimeout: function () {},
    setInterval: function () { return 0; },
    crypto: { randomUUID: function () { return 't8-idempotency-key'; } },
    confirm: function () { return true; },
    fetch: async function (requestPath, init) {
      requests.push({ path: String(requestPath), init: init || {} });
      assert.strictEqual(typeof opts.fetch, 'function', 'the scenario installed no fetch');
      return opts.fetch(String(requestPath), init || {});
    },
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);

  return {
    context: context,
    document: document,
    byId: byId,
    make: make,
    requests: requests,
    navigations: navigations,
    runFile: function (asset) {
      vm.runInContext(fs.readFileSync(path.join(webDir, 'assets', asset), 'utf8'), context);
    },
    runSource: function (source, label) {
      vm.runInContext(source, context, { filename: label || 'inline.js' });
    },
    runInline: function (htmlFile) {
      const html = fs.readFileSync(path.join(webDir, htmlFile), 'utf8');
      const pattern = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g;
      const blocks = [];
      let match;
      while ((match = pattern.exec(html)) !== null) blocks.push(match[1]);
      assert.ok(blocks.length > 0, 'no inline script in ' + htmlFile);
      this.runSource(blocks[blocks.length - 1], htmlFile);
    },
  };
}

async function flush(rounds) {
  for (let index = 0; index < (rounds || 40); index += 1) {
    await new Promise(function (resolve) { setImmediate(resolve); });
  }
}

async function main() {
__SCENARIO__
}

main().catch(function (error) {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
"""


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the committed page scripts cannot be executed")
    return NODE


def run_scenario(node_binary: str, tmp_path, name: str, scenario: str, marker: str) -> None:
    script = HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB))).replace("__SCENARIO__", scenario)
    harness = tmp_path / (name + ".js")
    harness.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert marker in completed.stdout, completed.stdout + completed.stderr
