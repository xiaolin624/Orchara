/* Shared presentation only. Business state and authorization remain in page/API code. */
(function (global) {
  'use strict';
  /*
   * One 24x24 stroked icon set for the whole product (T1, #165): every path is
   * drawn on the same grid, stroke 1.6, round caps and joins. No external font,
   * image or emoji. Unknown names fall back to `grid`, which is the documented
   * contract (`OrcharaUI.icon(name)`).
   */
  var paths = {
    /* navigation */
    chat: ['M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z'],
    grid: ['M3 3h7v7H3z', 'M14 3h7v7h-7z', 'M3 14h7v7H3z', 'M14 14h7v7h-7z'],
    workflow: ['M5 3h5v5H5z', 'M14 16h5v5h-5z', 'M7.5 8v8a2 2 0 0 0 2 2H14', 'M10 5.5h6.5a2 2 0 0 1 2 2V12', 'm16 10 2.5 2.5L21 10'],
    run: ['m9 5 11 7-11 7V5Z', 'M4 5v14'],
    check: ['M20 6 9 17l-5-5'],
    brain: ['M12 18V5a3 3 0 0 0-5.8-1A4 4 0 0 0 3 10a4 4 0 0 0 2 7.5A3.5 3.5 0 0 0 12 18Z', 'M12 18V5a3 3 0 0 1 5.8-1A4 4 0 0 1 21 10a4 4 0 0 1-2 7.5A3.5 3.5 0 0 1 12 18Z', 'M6 10c2 0 3 1 3 3', 'M18 10c-2 0-3 1-3 3'],
    book: ['M12 5c-3-2-6-2-10-1v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-4-1-7-1-10 1Z', 'M12 5v15'],
    folder: ['M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z'],
    file: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z', 'M14 2v6h6', 'M8 13h8', 'M8 17h5'],
    tool: ['M14.7 6.3a5.5 5.5 0 0 0-7 7L3 18a2 2 0 0 0 3 3l4.7-4.7a5.5 5.5 0 0 0 7-7l-3 3-3-3 3-3Z'],
    model: ['M12 3 3 8l9 5 9-5-9-5Z', 'm3 12 9 5 9-5', 'm3 16 9 5 9-5'],
    settings: ['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z', 'm9 3-1 3-3 1-2 3 2 2v3l2 3 3 1 2 2 3-2 3-1 2-3v-3l2-2-2-3-3-1-1-3H9Z'],
    pulse: ['M2 12h5l3-8 4 16 3-8h5'],
    logout: ['M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4', 'm16 17 5-5-5-5', 'M21 12H9'],
    user: ['M20 21v-2a7 7 0 0 0-14 0v2', 'M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z'],
    panel: ['M3 4h18v16H3z', 'M9 4v16'],
    /* operations */
    plus: ['M12 5v14', 'M5 12h14'],
    spark: ['m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z'],
    search: ['M11 3a8 8 0 1 0 0 16 8 8 0 0 0 0-16Z', 'm17 17 4 4'],
    arrow: ['M5 12h14', 'm13 6 6 6-6 6'],
    close: ['M6 6l12 12', 'M18 6 6 18'],
    x: ['M6 6l12 12', 'M18 6 6 18'],
    more: ['M5 12h.01', 'M12 12h.01', 'M19 12h.01'],
    filter: ['M3 5h18l-7 8v5l-4 2v-7L3 5Z'],
    sort: ['M8 4v16', 'm4 8 4-4 4 4', 'M16 20V4', 'm12 16 4 4 4-4'],
    upload: ['M12 16V4', 'm6 10 6-6 6 6', 'M4 20h16'],
    download: ['M12 4v12', 'm6 10 6 6 6-6', 'M4 20h16'],
    refresh: ['M20 12a8 8 0 1 1-2.34-5.66', 'M20 4v6h-6'],
    play: ['m8 5 11 7-11 7V5Z'],
    pause: ['M9 5v14', 'M15 5v14'],
    stop: ['M6 6h12v12H6z'],
    clock: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z', 'M12 7v5l3 2'],
    copy: ['M8 8h12v12H8z', 'M16 4H4v12'],
    external: ['M14 4h6v6', 'M20 4l-9 9', 'M20 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h5'],
    alert: ['M12 4 3 20h18L12 4Z', 'M12 10v4', 'M12 17h.01'],
    info: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z', 'M12 11v5', 'M12 8h.01'],
    help: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z', 'M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7', 'M12 17h.01'],
    home: ['M4 10 12 3l8 7', 'M6 9v11h12V9'],
    key: ['M15.5 3a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11Z', 'm11.5 11.5-8.5 8.5v3h3v-2h2v-2h2l1.5-1.5'],
    lock: ['M6 10h12v10H6z', 'M9 10V7a3 3 0 0 1 6 0v3'],
    shield: ['M12 3 3 7v5c0 5 9 9 9 9s9-4 9-9V7l-9-4Z', 'm9 12 2 2 4-4'],
    database: ['M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3Z', 'M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6', 'M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3'],
    layers: ['m12 3 9 5-9 5-9-5 9-5Z', 'm3 12 9 5 9-5', 'm3 16 9 5 9-5'],
    terminal: ['M4 5h16v14H4z', 'm8 9 3 3-3 3', 'M13 15h4'],
    eye: ['M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z', 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z'],
    menu: ['M4 7h16', 'M4 12h16', 'M4 17h16'],
    star: ['m12 3 2.6 5.7 6.2.6-4.7 4.2 1.4 6.1L12 16.7 6.5 19.6l1.4-6.1L3.2 9.3l6.2-.6L12 3Z'],
    zap: ['m13 2-8 12h6l-1 8 8-12h-6l1-8Z'],
    chevron: ['m9 6 6 6-6 6'],
    'chevron-down': ['m6 9 6 6 6-6'],
    'chevron-up': ['m6 15 6-6 6 6'],
    'chevron-left': ['m15 6-6 6 6 6'],
    'chevron-right': ['m9 6 6 6-6 6']
  };
  function icon(name) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.6');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('class', 'ui-icon');
    svg.setAttribute('aria-hidden', 'true');
    (paths[name] || paths.grid).forEach(function (d) {
      var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', d);
      svg.appendChild(path);
    });
    return svg;
  }
  /*
   * Visual metadata per route. nav.js stamps data-nav-icon/data-nav-group on
   * every rendered anchor; this table is the fallback for markup rendered
   * without those attributes. The labels themselves live in nav.js only.
   */
  var navigation = {
    '/chat': ['chat', '工作空间'], '/catalog': ['grid', '工作空间'],
    '/runs': ['run', '工作空间'], '/approvals': ['shield', '工作空间'],
    '/apps': ['workflow', '创作与资源'], '/knowledge': ['book', '创作与资源'],
    '/tools': ['tool', '创作与资源'], '/models': ['model', '创作与资源'],
    '/memories': ['brain', '个人空间'], '/personal-data': ['database', '个人空间'],
    '/attachments': ['file', '个人空间'],
    '/admin': ['settings', '管理'], '/reviews': ['check', '管理'],
    '/status': ['pulse', '管理'], '/login': ['logout', '账号'], '/activate': ['user', '账号'],
  };
  function decorateNavigation(identity) {
    var nav = document.querySelector('[data-orchara-nav]');
    if (nav) {
      var group = '';
      Array.from(nav.querySelectorAll('a')).forEach(function (link) {
        var href = link.getAttribute('href');
        var info = navigation[href] || ['grid', '工作空间'];
        var iconName = link.getAttribute('data-nav-icon') || info[0];
        var groupName = link.getAttribute('data-nav-group') || info[1];
        if (groupName !== group) {
          group = groupName;
          var heading = document.createElement('span');
          heading.className = 'nav-group';
          heading.textContent = groupName;
          nav.insertBefore(heading, link);
        }
        var label = link.textContent;
        var text = document.createElement('span');
        text.className = 'nav-label';
        text.textContent = label;
        link.replaceChildren(icon(iconName), text);
        link.title = label;
        var current = global.location.pathname;
        var active = current === href || (href === '/apps' && (current.startsWith('/apps/') || current.startsWith('/evaluations/')))
          || (href === '/reviews' && current.startsWith('/reviews/'));
        if (active) link.setAttribute('aria-current', 'page');
      });
    }
    var account = document.getElementById('account-label');
    if (account) account.textContent = identity ? '我的工作空间' : '尚未登录';
    var role = document.getElementById('account-role');
    if (role) {
      var labels = { admin: '管理员', creator: '创作者', user: '使用者' };
      role.textContent = identity ? (identity.roles || []).map(function (r) { return labels[r] || r; }).join(' · ') || '服务账号' : '登录后开始使用';
    }
  }
  function boot() {
    document.querySelectorAll('[data-icon]').forEach(function (element) {
      element.prepend(icon(element.getAttribute('data-icon')));
    });
    var toggle = document.getElementById('sidebar-toggle');
    var collapse = document.getElementById('sidebar-collapse');
    var sidebar = document.getElementById('app-sidebar');
    var scrim = document.getElementById('nav-scrim');
    var workspace = document.querySelector('.app-workspace');
    var media = global.matchMedia('(max-width: 720px)');
    function setExpanded(expanded) {
      [toggle, collapse].forEach(function (button) {
        if (!button) return;
        button.setAttribute('aria-expanded', String(expanded));
      });
    }
    function closeMenu() {
      document.body.classList.remove('nav-open');
      // T11 (#175): a closed drawer is collapsed; leaving the toggle reporting
      // aria-expanded="true" told screen readers the menu was still open.
      setExpanded(false);
      if (sidebar && media.matches) sidebar.inert = true;
      if (workspace) workspace.inert = false;
    }
    function openMenu() {
      document.body.classList.add('nav-open');
      setExpanded(true);
      if (sidebar) {
        sidebar.inert = false;
        var first = sidebar.querySelector('a');
        if (first && typeof first.focus === 'function') first.focus();
      }
      if (workspace) workspace.inert = true;
    }
    function onToggleClick() {
      if (media.matches) {
        if (document.body.classList.contains('nav-open')) closeMenu(); else openMenu();
        return;
      }
      var collapsed = document.body.classList.toggle('sidebar-collapsed');
      setExpanded(!collapsed);
      var label = collapsed ? '展开侧边导航' : '收起侧边导航';
      [toggle, collapse].forEach(function (button) {
        if (button) button.setAttribute('aria-label', label);
      });
    }
    if (toggle) toggle.addEventListener('click', onToggleClick);
    if (collapse) collapse.addEventListener('click', onToggleClick);
    if (scrim) scrim.addEventListener('click', closeMenu);
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && document.body.classList.contains('nav-open')) {
        closeMenu();
        if (toggle && typeof toggle.focus === 'function') toggle.focus();
      }
    });
    function adaptNavigation() {
      document.body.classList.remove('sidebar-collapsed', 'nav-open');
      if (sidebar) sidebar.inert = media.matches;
      if (workspace) workspace.inert = false;
      setExpanded(!media.matches);
    }
    media.addEventListener('change', adaptNavigation);
    adaptNavigation();
    var emptyChat = document.getElementById('chat-empty');
    var chat = document.getElementById('chat-panel');
    if (emptyChat && chat) {
      var sync = function () { emptyChat.hidden = !chat.hidden; };
      new MutationObserver(sync).observe(chat, { attributes: true, attributeFilter: ['hidden'] });
      sync();
    }
  }
  global.OrcharaUI = { icon: icon, decorateNavigation: decorateNavigation };
  boot();
})(window);
