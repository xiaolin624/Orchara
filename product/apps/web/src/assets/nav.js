/*
 * The single source of truth for Orchara's web navigation and for the controls
 * that only some roles may use.
 *
 * Before this file every page carried its own hand-written <nav> list, so a
 * reader was offered "管理台" and a plain user was offered the creator forms;
 * the server refused them, but the page looked like it had granted the access.
 * Now the list is defined once, filtered by the identity returned by
 * GET /api/v1/auth/me, and rendered on every page:
 *
 *   - anonymous  -> 状态 / 登录 / 激活账号
 *   - human      -> the catalogue, authoring, personal-data and admin entries
 *                   their role set actually allows (管理台 only for admin, the
 *                   "应用与创作" label only for creator)
 *   - service    -> no browser entries that belong to personal data or
 *                   authoring (a service principal has no roles and the
 *                   personal-data endpoints answer 403 personal_scope_forbidden)
 *
 * It also applies declarative role gates to the page: an element marked
 * data-requires-role="creator" (or "admin") is hidden unless the current
 * identity holds that role, and data-requires-kind="user" is hidden for a
 * service principal. Hiding is feedback only -- the server check stays the
 * authorization (internal/api/authz.go); this file is the interface telling the
 * truth about what the server will accept.
 *
 * A refresh (a new page load) always re-reads GET /api/v1/auth/me because the
 * identity cache lives in app.js and a page load starts with an empty cache.
 * After an in-session refusal whose status is 403 (a role the caller no longer
 * holds), refresh() re-reads the identity and re-renders, so the page converges
 * without waiting for a manual refresh.
 *
 * Plain classic script, no build step, loaded after app.js on every page.
 */
(function (global) {
  'use strict';

  var ROLE_ADMIN = 'admin';
  var ROLE_CREATOR = 'creator';

  function rolesOf(identity) {
    if (!identity || !Array.isArray(identity.roles)) {
      return [];
    }
    return identity.roles.map(String);
  }

  function hasRole(identity, role) {
    return rolesOf(identity).indexOf(String(role)) !== -1;
  }

  /*
   * A session cookie always belongs to a human principal. A service principal is
   * reported as kind=service and must not be offered the pages the server
   * refuses for it (personal data, authoring).
   */
  function isHuman(identity) {
    if (!identity) {
      return false;
    }
    return identity.kind === undefined || identity.kind === 'user';
  }

  /** The navigation entries the current identity may see, in display order. */
  function navItemsFor(identity) {
    if (!identity) {
      return [
        { href: '/', label: '状态' },
        { href: '/login', label: '登录' },
        { href: '/activate', label: '激活账号' },
      ];
    }

    var items = [{ href: '/', label: '状态' }];
    if (isHuman(identity)) {
      items.push({
        href: '/apps',
        label: hasRole(identity, ROLE_CREATOR) ? '应用与创作' : '应用（只读）',
      });
      items.push({ href: '/catalog', label: '用途目录检索' });
      items.push({ href: '/runs', label: '运行' });
      items.push({ href: '/chat', label: '会话' });
      items.push({ href: '/approvals', label: '审批' });
      items.push({ href: '/memories', label: '记忆' });
      items.push({ href: '/knowledge', label: '知识库' });
      items.push({ href: '/personal-data', label: '个人数据' });
      items.push({ href: '/attachments', label: '附件' });
      items.push({ href: '/tools', label: '工具' });
      items.push({ href: '/models', label: '模型' });
    }
    if (hasRole(identity, ROLE_ADMIN)) {
      items.push({ href: '/admin', label: '管理台' });
    }
    items.push({ href: '/login', label: '退出', logout: true });
    return items;
  }

  /*
   * Pages that may be visited without a session declare data-auth="optional" on
   * <body> (/login, /activate, the status page). Every other page fails closed:
   * if the identity cannot be read, the page follows the session failure instead
   * of rendering an authenticated-looking skeleton.
   */
  function pageRequiresAuth() {
    if (typeof document === 'undefined' || !document.body) {
      return true;
    }
    if (typeof document.body.getAttribute !== 'function') {
      return true;
    }
    return document.body.getAttribute('data-auth') !== 'optional';
  }

  /*
   * A visible "退出" that only navigates to /login does not revoke the session:
   * chat.html had exactly that decorative link. The rendered logout anchor is
   * intercepted here and goes through app.js's logout(), which sends the CSRF
   * proof and revokes the server-side session.
   */
  function onNavigationClick(event) {
    var target = event && event.target;
    if (!target || typeof target.getAttribute !== 'function') {
      return;
    }
    if (target.getAttribute('data-orchara-logout') !== 'true') {
      return;
    }
    if (typeof event.preventDefault === 'function') {
      event.preventDefault();
    }
    var signingOut = typeof logout === 'function' ? logout() : Promise.resolve(false);
    signingOut.catch(function () {
      // A failed revoke is still a reason to leave the page: the localStorage
      // and the cookie are not ours to keep. The login page shows the state.
      return false;
    }).then(function () {
      if (global.location && typeof global.location.assign === 'function') {
        global.location.assign('/login');
      }
    });
  }

  /** Replace every <nav> with the entries this identity may see. */
  function renderNavigation(identity) {
    if (typeof document === 'undefined' || typeof document.querySelectorAll !== 'function') {
      return;
    }
    var items = navItemsFor(identity);
    var navs = document.querySelectorAll('nav');
    for (var navIndex = 0; navIndex < navs.length; navIndex += 1) {
      var nav = navs[navIndex];
      nav.replaceChildren();
      for (var itemIndex = 0; itemIndex < items.length; itemIndex += 1) {
        var item = items[itemIndex];
        var link = document.createElement('a');
        link.href = item.href;
        link.textContent = item.label;
        if (item.logout) {
          link.setAttribute('data-orchara-logout', 'true');
        }
        nav.appendChild(link);
      }
      var bound = nav.getAttribute && nav.getAttribute('data-orchara-bound') === 'true';
      if (!bound) {
        nav.addEventListener('click', onNavigationClick);
        nav.setAttribute('data-orchara-bound', 'true');
      }
    }
  }

  /**
   * Apply the declarative role gates.
   *
   * Elements are closed by default in the HTML (hidden attribute) and opened
   * only for an identity that holds the required role, so a failed identity read
   * never flashes a control the server would refuse.
   */
  function applyRoleVisibility(identity) {
    if (typeof document === 'undefined' || typeof document.querySelectorAll !== 'function') {
      return;
    }
    var gated = document.querySelectorAll('[data-requires-role]');
    for (var index = 0; index < gated.length; index += 1) {
      var element = gated[index];
      var required = element.getAttribute('data-requires-role');
      element.hidden = !hasRole(identity, required);
    }
    var humanOnly = document.querySelectorAll('[data-requires-kind="user"]');
    for (var humanIndex = 0; humanIndex < humanOnly.length; humanIndex += 1) {
      humanOnly[humanIndex].hidden = !isHuman(identity);
    }
  }

  function render(identity) {
    renderNavigation(identity);
    applyRoleVisibility(identity);
  }

  /** Re-read /auth/me and re-render; used after a 403 role refusal. */
  async function refresh() {
    var current = await identity(true);
    render(current);
    return current;
  }

  /*
   * Boot: one identity read per page load (app.js shares its promise with the
   * page's own requireLogin), then render. On a failure the navigation degrades
   * to the anonymous list; on a page that requires a session the shared
   * handleAuthFailure sends the browser to /login (with the disabled reason
   * when the server reported principal_disabled).
   */
  async function bootstrap() {
    try {
      var current = await identity();
      render(current);
      return current;
    } catch (error) {
      render(null);
      if (pageRequiresAuth() && typeof handleAuthFailure === 'function') {
        handleAuthFailure(error);
      }
      return null;
    }
  }

  global.OrcharaNav = {
    items: navItemsFor,
    navItemsFor: navItemsFor,
    hasRole: hasRole,
    isHuman: isHuman,
    render: render,
    renderNavigation: renderNavigation,
    applyRoleVisibility: applyRoleVisibility,
    refresh: refresh,
    bootstrap: bootstrap,
    pageRequiresAuth: pageRequiresAuth,
  };

  if (typeof document !== 'undefined' && typeof document.querySelectorAll === 'function') {
    bootstrap();
  }
})(typeof window !== 'undefined' ? window : this);
