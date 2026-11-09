/* Shared presentation only. Business state and authorization remain in page/API code. */
(function (global) {
  'use strict';
  var paths = {
    chat: ['M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z'],
    grid: ['M3 3h7v7H3z', 'M14 3h7v7h-7z', 'M3 14h7v7H3z', 'M14 14h7v7h-7z'],
    workflow: ['M5 3h5v5H5z', 'M14 16h5v5h-5z', 'M7.5 8v8a2 2 0 0 0 2 2H14', 'M10 5.5h6.5a2 2 0 0 1 2 2V12', 'm16 10 2.5 2.5L21 10'],
    run: ['m9 5 11 7-11 7V5Z', 'M4 5v14'],
    check: ['M9 12l2 2 4-4', 'M12 3 3 7v5c0 5 9 9 9 9s9-4 9-9V7l-9-4Z'],
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
    plus: ['M12 5v14', 'M5 12h14'],
    spark: ['m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z'],
    search: ['M11 3a8 8 0 1 0 0 16 8 8 0 0 0 0-16Z', 'm17 17 4 4'],
    arrow: ['M5 12h14', 'm13 6 6 6-6 6'],
  };
  function icon(name) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.65');
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
  var navigation = {
    '/chat': ['chat', '工作空间'], '/catalog': ['grid', '工作空间'],
    '/runs': ['run', '工作空间'], '/approvals': ['check', '工作空间'],
    '/apps': ['workflow', '创作与资源'], '/knowledge': ['book', '创作与资源'],
    '/tools': ['tool', '创作与资源'], '/models': ['model', '创作与资源'],
    '/memories': ['brain', '个人空间'], '/personal-data': ['folder', '个人空间'],
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
        if (info[1] !== group) {
          group = info[1];
          var heading = document.createElement('span');
          heading.className = 'nav-group';
          heading.textContent = group;
          nav.insertBefore(heading, link);
        }
        var label = link.textContent;
        var text = document.createElement('span');
        text.className = 'nav-label';
        text.textContent = label;
        link.replaceChildren(icon(info[0]), text);
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
    var sidebar = document.getElementById('app-sidebar');
    var scrim = document.getElementById('nav-scrim');
    var workspace = document.querySelector('.app-workspace');
    function closeMenu() {
      document.body.classList.remove('nav-open');
      if (toggle) toggle.setAttribute('aria-expanded', 'false');
      if (sidebar && global.matchMedia('(max-width: 720px)').matches) sidebar.inert = true;
      if (workspace) workspace.inert = false;
    }
    if (toggle) {
      toggle.addEventListener('click', function () {
        if (global.matchMedia('(max-width: 720px)').matches) {
          var opened = document.body.classList.toggle('nav-open');
          toggle.setAttribute('aria-expanded', String(opened));
          if (sidebar) sidebar.inert = !opened;
          if (workspace) workspace.inert = opened;
          if (opened && sidebar) sidebar.querySelector('a').focus();
        } else {
          var collapsed = document.body.classList.toggle('sidebar-collapsed');
          toggle.setAttribute('aria-expanded', String(!collapsed));
          toggle.setAttribute('aria-label', collapsed ? '展开侧边导航' : '收起侧边导航');
        }
      });
    }
    if (scrim) scrim.addEventListener('click', closeMenu);
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && document.body.classList.contains('nav-open')) { closeMenu(); toggle.focus(); }
    });
    var media = global.matchMedia('(max-width: 720px)');
    function adaptNavigation() {
      document.body.classList.remove('sidebar-collapsed', 'nav-open');
      if (sidebar) sidebar.inert = media.matches;
      if (workspace) workspace.inert = false;
      if (toggle) toggle.setAttribute('aria-expanded', String(!media.matches));
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
