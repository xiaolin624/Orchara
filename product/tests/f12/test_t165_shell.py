"""T1 (GitHub #165): the shared design system and the 22-page shell.

This file is the T1 acceptance seam. It is deliberately split in two means:

* static seams -- the committed bytes of ``assets/design.css``, ``assets/ui.js``,
  ``assets/nav.js``, ``assets/mobile.css`` and the 22 HTML pages are parsed and
  asserted. The design tokens, the contrast pairs, the component vocabulary, the
  exact ``<script src="/assets/x.js"></script>`` form and the business ids each
  page already owns are contractual here.
* one execution seam -- ``assets/ui.js`` is executed as the committed bytes under
  Node with a minimal DOM shim (the same technique as ``test_t095``), because the
  icon library and the navigation decoration are JavaScript, not markup.

What this file does not prove: browser layout, real reflow at 375px, or contrast
in a rendered pixel. Those stay in ``tools/verify_frontend.py`` (real Chrome).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from support import stack as st

WEB = st.WEB_DIR
NODE = shutil.which("node")

PAGES = sorted(WEB.glob("*.html"))
AUTH_PAGES = {"login.html", "activate.html"}
PHONE_PAGES = {
    "chat.html": "page-chat",
    "approvals.html": "page-approvals",
    "runs.html": "page-runs",
    "personal-data.html": "page-personal-data",
    "memories.html": "page-memories",
    "knowledge.html": "page-knowledge",
}
SCOPE_PAGES = {"chat.html", "runs.html", "approvals.html", "editor.html", "debug.html"}
TABBAR_LINKS = {
    "/chat": "会话",
    "/apps": "应用",
    "/runs": "运行",
    "/approvals": "审批",
    "/personal-data": "我的",
}

# The business id inventory each page already owns. The shell rewrite may change
# markup, but it must not rename, drop or invent a business id. Shell ids are
# excluded because T1 owns those. These digests were taken from the integration
# baseline (62ef8b9) before the shell was rewritten.
BUSINESS_ID_DIGESTS = {
    "activate.html": "f2278a92458aeca1",
    "admin.html": "15af3f94c9eabe15",
    "approvals.html": "3f04d708bf093efb",
    "apps.html": "4f094a1a684c36d1",
    "attachments.html": "2e3f866bd15dd2d7",
    "catalog.html": "c37a5a87a8ab19ec",
    "chat.html": "369ce594282f95a8",
    "debug.html": "90de7751f71f3991",
    "editor.html": "6f26e130f84a62a2",
    "evaluate.html": "5a79241dc4b2fa83",
    "evaluations.html": "f18e377cf896b6de",
    "index.html": "80b9882866435bad",
    "knowledge.html": "b6b9ef92a73c7a8e",
    "login.html": "7b0cd0eadc9b5804",
    "memories.html": "f9e954c22b76757d",
    "models.html": "83ae8b11ecfaec3d",
    "personal-data.html": "ecfc2b453dc8cae2",
    "publish.html": "e23162de7818a21d",
    "reviews.html": "4009bbe701b5b819",
    "runs.html": "ba4c8d50a0eac0b1",
    "tools.html": "c8d76a43b91f25a7",
    "versions.html": "3bc5f32747046d72",
}
SHELL_IDS = {
    "app-sidebar",
    "nav-scrim",
    "sidebar-toggle",
    "sidebar-collapse",
    "main-content",
    "account-label",
    "account-role",
    # T11 (#175): the phone context sheet is part of the shared phone shell, not
    # a business fact; registering it keeps the business-id digest unchanged.
    "chat-context",
}

REQUIRED_TOKENS = [
    # surfaces and text
    "--bg", "--surface", "--surface-2", "--surface-3", "--border", "--border-strong",
    "--text", "--text-2", "--muted",
    # brand
    "--accent", "--accent-hover", "--accent-ink", "--accent-soft", "--teal", "--teal-soft",
    # semantic
    "--success", "--success-soft", "--warning", "--warning-soft", "--danger", "--danger-soft", "--info",
    # shape and density
    "--radius-sm", "--radius", "--radius-lg", "--shadow-card", "--shadow-pop", "--sidebar", "--topbar",
    # type scale
    "--fs-11", "--fs-12", "--fs-13", "--fs-14", "--fs-16", "--fs-20", "--fs-24", "--font-mono",
    # motion and layers
    "--ease", "--dur-1", "--dur-2", "--dur-3",
    "--z-sticky", "--z-drawer", "--z-overlay", "--z-toast",
]

# (foreground, background, minimum ratio) -- spec frontend-polish.md section 2.1.
CONTRAST_PAIRS = [
    ("--text", "--surface", 12.0),
    ("--text-2", "--surface", 7.0),
    ("--muted", "--surface", 5.0),
    ("--accent-ink", "--accent-soft", 4.5),
    ("--teal", "--teal-soft", 4.5),
    ("--success", "--success-soft", 4.5),
    ("--warning", "--warning-soft", 4.5),
    ("--danger", "--danger-soft", 4.5),
]
WHITE_ON_ACCENT_MIN = 4.5


def read_page(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def read_asset(name: str) -> str:
    return (WEB / "assets" / name).read_text(encoding="utf-8")


def root_tokens(css: str) -> dict[str, str]:
    match = re.search(r":root\s*\{(.*?)\}", css, re.S)
    assert match, "design.css has no :root token block"
    block = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S)
    tokens: dict[str, str] = {}
    for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", block):
        tokens[name] = value.strip()
    return tokens


def css_value(tokens: dict[str, str], name: str, seen: set[str] | None = None) -> str:
    value = tokens[name]
    var = re.fullmatch(r"var\(\s*(--[a-z0-9-]+)\s*\)", value)
    if var:
        guard = seen or set()
        assert var.group(1) not in guard, f"{name} resolves through a token cycle"
        guard.add(var.group(1))
        return css_value(tokens, var.group(1), guard)
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), f"{name} must resolve to a 6-digit hex for the contrast check: {value}"
    return value


def contrast(first: str, second: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
        return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


# ---------------------------------------------------------------------------
# 1. tokens and contrast
# ---------------------------------------------------------------------------
def test_design_css_is_the_single_token_source_with_the_warm_palette() -> None:
    css = read_asset("design.css")
    tokens = root_tokens(css)
    missing = [name for name in REQUIRED_TOKENS if name not in tokens]
    assert not missing, f"design tokens are missing: {missing}"
    # The exact palette is the selected direction C surface/text/brand set.
    assert tokens["--bg"] == "#f7f4ee"
    assert tokens["--surface"] == "#fffdf9"
    assert tokens["--text"] == "#211d18"
    assert tokens["--accent"] == "#b4530a"
    assert tokens["--sidebar"] == "232px"
    assert tokens["--topbar"] == "52px"
    # Backwards compatibility: old pages keep --primary and it maps to the accent.
    assert re.fullmatch(r"var\(\s*--accent\s*\)", tokens["--primary"])
    assert re.fullmatch(r"var\(\s*--accent-hover\s*\)", tokens["--primary-hover"])
    assert re.fullmatch(r"var\(\s*--accent-soft\s*\)", tokens["--primary-soft"])


def test_every_required_contrast_pair_meets_the_spec_minimum() -> None:
    tokens = root_tokens(read_asset("design.css"))
    measured = {}
    for foreground, background, minimum in CONTRAST_PAIRS:
        ratio = contrast(css_value(tokens, foreground), css_value(tokens, background))
        measured[f"{foreground} on {background}"] = round(ratio, 2)
        assert ratio >= minimum, f"{foreground} on {background} is {ratio:.2f}:1, needs {minimum}:1"
    white_accent = contrast("#ffffff", css_value(tokens, "--accent"))
    measured["#ffffff on --accent"] = round(white_accent, 2)
    assert white_accent >= WHITE_ON_ACCENT_MIN, measured


def test_components_use_tokens_and_do_not_pile_up_important() -> None:
    css = read_asset("design.css")
    match = re.search(r":root\s*\{.*?\}", css, re.S)
    assert match
    body = css[: match.start()] + css[match.end() :]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    raw_hex = sorted(set(re.findall(r"#[0-9a-fA-F]{3,8}\b", body)))
    assert not raw_hex, f"component layer still uses raw hex colours: {raw_hex}"
    assert css.count("!important") <= 8, "the component layer relies on !important instead of specificity"
    # Layers are ordered: tokens, base, shell, components, page specialisation,
    # responsive, reduced motion, and an append zone for later tickets.
    order = [
        ":root {",
        "/* 2. base",
        "/* 3. shell",
        "/* 4. components",
        "/* 5. page specialisation",
        "/* 6. responsive",
        "/* 7. reduced motion",
        "/* 8. page extension zone",
    ]
    positions = [css.find(marker) for marker in order]
    assert all(position >= 0 for position in positions), positions
    assert positions == sorted(positions), positions


def test_the_component_vocabulary_is_defined() -> None:
    css = read_asset("design.css")
    selectors = [
        ".btn", ".btn-primary", ".btn-secondary", ".btn-ghost", ".btn-danger",
        ".btn-icon", ".btn-small", ".field", ".field-label", ".field-help", ".field-error",
        ".select", ".check", ".card", ".card-head", ".table-scroll", ".pill",
        ".segmented", ".toolbar", ".breadcrumb", ".page-heading", ".page-meta",
        ".empty-state", ".skeleton", ".inline-note", ".avatar", "kbd", ".divider",
        ".stat-chip", ".steps", ".step", ".progress", ".toast", ".tooltip", ".sr-only",
    ]
    missing = [selector for selector in selectors if selector not in css]
    assert not missing, f"component selectors are missing: {missing}"
    for tone in ("success", "warning", "danger", "muted", "accent", "info"):
        assert f'data-tone="{tone}"' in css, f"pill tone {tone} is not defined"
    for shape in ("dot", "ring", "check", "square", "alert", "dash"):
        assert f'data-shape="{shape}"' in css, f"pill shape {shape} is not defined"
    # T11 (#175): the shared phone shell (tab bar, safe area, 44px targets) is
    # declared once in design.css, which every page loads; mobile.css keeps the
    # six-page legacy layout and its own 44px/16px rules.
    shell = read_asset("design.css")
    assert ".mobile-tabbar" in shell
    assert "safe-area-inset-bottom" in shell
    assert "44px" in shell
    mobile = read_asset("mobile.css")
    assert "min-height: 44px" in mobile
    assert "font-size: 16px" in mobile


def test_status_semantics_keep_the_legacy_class_contract() -> None:
    css = read_asset("design.css")
    for legacy in (".status", ".status.ok", ".status.bad", ".status.pending", ".status.warn"):
        assert legacy in css, legacy
    # The legacy tone classes stay usable together with the new pill vocabulary.
    assert ".pill" in css
    assert 'data-shape="check"' in css


# ---------------------------------------------------------------------------
# 2. the 22-page shell
# ---------------------------------------------------------------------------
def test_the_product_still_has_exactly_22_pages() -> None:
    assert len(PAGES) == 22, [page.name for page in PAGES]
    assert {page.name for page in PAGES} == set(BUSINESS_ID_DIGESTS)


@pytest.mark.parametrize("page", [path.name for path in PAGES])
def test_every_page_has_one_h1_one_main_and_one_design_stylesheet(page: str) -> None:
    html = read_page(page)
    assert len(re.findall(r"<h1\b", html)) == 1, page
    assert 'id="main-content"' in html, page
    assert re.search(r'<main class="page-content" id="main-content" tabindex="-1">', html), page
    assert html.count('href="/assets/design.css"') == 1, page
    assert 'class="skip-link"' in html and 'href="#main-content"' in html, page
    if page not in AUTH_PAGES:
        assert 'data-orchara-nav="true"' in html, page
    assert 'class="page-heading"' in html, page
    assert 'class="page-meta"' in html, page
    assert 'class="page-sections"' in html, page


@pytest.mark.parametrize("page", sorted(set(BUSINESS_ID_DIGESTS) - AUTH_PAGES))
def test_desktop_pages_share_the_sidebar_and_topbar_shell(page: str) -> None:
    html = read_page(page)
    assert 'class="app-sidebar" id="app-sidebar"' in html, page
    assert 'id="sidebar-toggle"' in html, page
    assert 'id="sidebar-collapse"' in html, page
    assert 'id="nav-scrim"' in html, page
    assert 'class="app-workspace"' in html, page
    assert 'class="workspace-bar"' in html, page
    assert 'class="breadcrumb"' in html, page
    assert 'class="topbar-search"' in html, page
    assert 'class="topbar-actions"' in html, page
    assert 'class="todo-pill"' in html, page
    assert 'id="account-label"' in html and 'id="account-role"' in html, page
    # The shared navigation lives inside the sidebar, not in a second list.
    aside = html[html.index('id="app-sidebar"'): html.index("</aside>")]
    assert 'data-orchara-nav="true"' in aside, page
    # The search entry is an honest link to the real catalog search, not a dead
    # input or a fake keyboard shortcut.
    search = re.search(r'<a class="topbar-search"[^>]*href="([^"]+)"', html)
    assert search and search.group(1) == "/catalog", page
    assert "<input" not in html[html.index('class="topbar-search"') : html.index('class="topbar-search"') + 400], page
    # The "new" entry reaches a real page; it is creator-gated because the create
    # form only exists for creators.
    assert 'data-requires-role="creator"' in html, page
    # No fabricated todo count: the approval entry carries a label, not a number.
    todo = re.search(r'<a class="todo-pill"[^>]*>(.*?)</a>', html, re.S)
    assert todo and re.fullmatch(r"审批", re.sub(r"<[^>]+>", "", todo.group(1)).strip()), page


@pytest.mark.parametrize("page", sorted(AUTH_PAGES))
def test_auth_pages_keep_the_optional_auth_shell_without_a_sidebar(page: str) -> None:
    html = read_page(page)
    assert 'data-auth="optional"' in html, page
    assert 'class="auth-layout"' in html, page
    assert 'class="auth-story"' in html and 'class="auth-main"' in html, page
    assert 'id="app-sidebar"' not in html, page
    assert 'id="sidebar-toggle"' not in html, page
    assert 'class="workspace-bar"' not in html, page
    assert 'class="segmented"' in html, page
    assert 'data-static-navigation="true"' in html, page


@pytest.mark.parametrize("page", [path.name for path in PAGES])
def test_business_ids_are_unchanged(page: str) -> None:
    import hashlib

    html = read_page(page)
    ids = sorted({value for value in re.findall(r'\bid="([^"]+)"', html) if value not in SHELL_IDS})
    digest = hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]
    assert digest == BUSINESS_ID_DIGESTS[page], (
        page,
        ids,
    )


@pytest.mark.parametrize("page", [path.name for path in PAGES])
def test_shared_script_tags_keep_the_exact_form_and_order(page: str) -> None:
    html = read_page(page)
    tags = re.findall(r'<script[^>]*\bsrc="[^"]*"[^>]*></script>', html)
    expected = re.compile(r'<script src="/assets/[a-z0-9-]+\.js"></script>')
    for tag in tags:
        assert expected.fullmatch(tag), f"{page}: a script tag changed shape: {tag}"
    assets = [re.search(r'src="/assets/([a-z0-9-]+\.js)"', tag).group(1) for tag in tags]
    assert assets[:3] == ["ui.js", "app.js", "nav.js"], (page, assets)
    assert len(assets) == len(set(assets)), f"{page}: a shared script is loaded twice: {assets}"
    if page == "chat.html":
        assert assets[3:5] == ["runstream.js", "runstate-guard.js"], assets
    if page == "editor.html":
        assert assets[3:] == ["canvas.js", "editor.js"], assets
    if page in ("runs.html", "debug.html"):
        assert assets.index("runstream.js") < assets.index("runstate-guard.js") < assets.index(
            "runs.js"
        ), assets


@pytest.mark.parametrize("page", sorted(PHONE_PAGES))
def test_phone_pages_keep_their_page_class_and_get_the_bottom_navigation(page: str) -> None:
    html = read_page(page)
    assert f'class="{PHONE_PAGES[page]}"' in html, page
    assert 'href="/assets/mobile.css"' in html, page
    assert 'class="mobile-tabbar"' in html, page
    tabbar = html[html.index('class="mobile-tabbar"'):]
    tabbar = tabbar[: tabbar.index("</div>")]
    links = dict(re.findall(r'href="([^"]+)"[^>]*>\s*(?:<[^>]+>\s*)*([^<]+)', tabbar))
    for href, label in TABBAR_LINKS.items():
        assert href in links, (page, href, links)
    assert tabbar.count('aria-current="page"') == 1, page


@pytest.mark.parametrize("page", sorted(SCOPE_PAGES))
def test_phone_scope_boundary_note_is_kept_verbatim(page: str) -> None:
    html = read_page(page)
    assert 'data-mobile-scope="no-full-canvas-editing"' in html, page
    note = html[html.index('data-mobile-scope="no-full-canvas-editing"') :]
    note = note[: note.index("</p>")]
    assert "画布" in note and ("不承诺" in note or "不在" in note), page


@pytest.mark.parametrize("page", [path.name for path in PAGES])
def test_mobile_stylesheet_order_is_untouched(page: str) -> None:
    html = read_page(page)
    if page not in PHONE_PAGES:
        return
    if "</style>" in html:
        assert html.index("</style>") < html.index('href="/assets/mobile.css"'), page
    else:
        assert html.index('href="/assets/canvas.css"') < html.index('href="/assets/mobile.css"'), page
    assert html.index('href="/assets/mobile.css"') < html.index('href="/assets/design.css"'), page


def test_the_new_components_are_used_by_at_least_one_page() -> None:
    usage = {
        "btn-primary": r'class="[^"]*\bbtn-primary\b',
        "btn-secondary": r'class="[^"]*\bbtn-secondary\b',
        "btn-ghost": r'class="[^"]*\bbtn-ghost\b',
        "btn-danger": r'class="[^"]*\bbtn-danger\b',
        "btn-icon": r'class="[^"]*\bbtn-icon\b',
        "btn-small": r'class="[^"]*\bbtn-small\b',
        "stat-chip": r'class="[^"]*\bstat-chip\b',
        "segmented": r'class="[^"]*\bsegmented\b',
        "skeleton": r'class="[^"]*\bskeleton\b',
        "mobile-tabbar": r'class="[^"]*\bmobile-tabbar\b',
        "table-scroll": r'class="table-scroll"',
        "empty-state": r'class="[^"]*\bempty-state\b',
        "pill": r'<span class="pill" data-tone="[a-z]+" data-shape="[a-z]+"',
    }
    pages = {name: read_page(name) for name in (path.name for path in PAGES)}
    for component, pattern in usage.items():
        assert any(re.search(pattern, html) for html in pages.values()), (
            component,
            [name for name, html in pages.items() if re.search(pattern, html)],
        )
    # The pill ships both encodings on the page that uses it: colour + shape + text.
    editor = pages["editor.html"]
    pill = re.search(r'<span class="pill"[^>]*>(.*?)</span>', editor, re.S)
    assert pill and pill.group(1).strip() == "草稿", editor


# ---------------------------------------------------------------------------
# 3. ui.js: icon library and navigation decoration, executed as committed bytes
# ---------------------------------------------------------------------------
UI_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');

const webDir = __WEB_DIR__;

class El {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.children = [];
    this.attributes = {};
    this._text = '';
    this.className = '';
    this.hidden = false;
    this.title = '';
    this.id = '';
    this.classList = {
      _el: this,
      add: function () { for (const c of arguments) this._el._classes().add(c); },
      remove: function () { for (const c of arguments) this._el._classes().delete(c); },
      toggle: function (c) {
        const set = this._el._classes();
        if (set.has(c)) { set.delete(c); return false; }
        set.add(c); return true;
      },
      contains: function (c) { return this._el._classes().has(c); },
    };
  }
  _classes() {
    const set = new Set(String(this.className || '').split(/\s+/).filter(Boolean));
    const proxy = {
      add: (c) => { set.add(c); this.className = [...set].join(' '); },
      delete: (c) => { set.delete(c); this.className = [...set].join(' '); },
      has: (c) => set.has(c),
    };
    return proxy;
  }
  get textContent() {
    if (this.children.length) {
      return this.children.map((c) => (c && c.textContent !== undefined ? c.textContent : '')).join('');
    }
    return this._text;
  }
  set textContent(value) { this._text = String(value == null ? '' : value); this.children = []; }
  appendChild(child) { this.children.push(child); return child; }
  append() { for (const node of arguments) this.appendChild(node); }
  prepend(node) { this.children.unshift(node); }
  insertBefore(node, before) {
    const at = this.children.indexOf(before);
    if (at === -1) this.children.push(node); else this.children.splice(at, 0, node);
    return node;
  }
  replaceChildren() {
    this.children = [];
    this._text = '';
    for (const node of arguments) this.appendChild(node);
  }
  setAttribute(name, value) {
    this.attributes[String(name)] = String(value);
    if (name === 'class') this.className = String(value);
    if (name === 'id') this.id = String(value);
    if (name === 'title') this.title = String(value);
  }
  getAttribute(name) {
    if (name === 'class') return this.className || null;
    if (name === 'title') return this.title || this.attributes.title || null;
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }
  hasAttribute(name) { return this.getAttribute(name) !== null; }
  querySelectorAll(selector) {
    const out = [];
    const walk = (node) => {
      for (const child of node.children || []) {
        if (!child || !child.tagName) continue;
        out.push(child);
        walk(child);
      }
    };
    walk(this);
    if (!selector.startsWith('.')) return out.filter((el) => el.tagName === selector.toUpperCase());
    const cls = selector.slice(1);
    return out.filter((el) => String(el.className).split(/\s+/).includes(cls));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  focus() { this.focused = true; }
  addEventListener() {}
}

const registry = [];
const byId = new Map();
function make(tag, id) {
  const el = new El(tag);
  if (id) { el.setAttribute('id', id); byId.set(id, el); }
  registry.push(el);
  return el;
}

const document = {
  body: new El('body'),
  createElement: (tag) => make(tag),
  createElementNS: (ns, tag) => make(tag),
  getElementById: (id) => byId.get(id) || null,
  querySelector: () => null,
  querySelectorAll: (selector) => {
    if (selector === '[data-icon]') return [];
    return [];
  },
  addEventListener: () => {},
};
const context = {
  console,
  document,
  location: { pathname: '/chat', assign: () => {} },
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  MutationObserver: class { observe() {} },
};
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(webDir, 'assets', 'ui.js'), 'utf8'), context);
const ui = context.OrcharaUI;
assert.ok(ui && typeof ui.icon === 'function', 'OrcharaUI.icon is missing');

const required = [
  'chat', 'grid', 'workflow', 'run', 'check', 'brain', 'book', 'folder', 'file', 'tool',
  'model', 'settings', 'pulse', 'logout', 'user', 'panel', 'plus', 'spark', 'search',
  'arrow', 'chevron', 'chevron-down', 'chevron-up', 'chevron-left', 'chevron-right',
  'close', 'x', 'more', 'filter', 'sort', 'upload', 'download', 'refresh', 'play',
  'pause', 'stop', 'clock', 'copy', 'external', 'alert', 'info', 'help',
];
const seen = {};
for (const name of required) {
  const svg = ui.icon(name);
  assert.strictEqual(svg.tagName, 'SVG', name + ' did not render an <svg>');
  assert.strictEqual(svg.getAttribute('viewBox'), '0 0 24 24', name);
  assert.strictEqual(svg.getAttribute('stroke-width'), '1.6', name);
  assert.strictEqual(svg.getAttribute('stroke-linecap'), 'round', name);
  assert.strictEqual(svg.getAttribute('stroke-linejoin'), 'round', name);
  assert.strictEqual(svg.getAttribute('aria-hidden'), 'true', name);
  assert.strictEqual(svg.getAttribute('class'), 'ui-icon', name);
  assert.ok(svg.children.length >= 1, name + ' has no path');
  for (const child of svg.children) {
    assert.ok(child.getAttribute('d') && child.getAttribute('d').length > 3, name + ' empty path');
  }
  seen[name] = svg.children.map((child) => child.getAttribute('d')).join('|');
}
const unique = new Set(Object.values(seen));
assert.ok(unique.size >= 30, 'at least 30 distinct icons are required, got ' + unique.size);
// An unknown name falls back to the grid icon (the documented contract).
const fallback = ui.icon('definitely-not-an-icon');
assert.strictEqual(
  fallback.children.map((child) => child.getAttribute('d')).join('|'),
  seen.grid,
  'unknown icon names must fall back to grid',
);

// decorateNavigation keeps the documented DOM contract.
const nav = make('nav', 'main-nav');
nav.setAttribute('data-orchara-nav', 'true');
for (const [href, label] of [
  ['/chat', '会话'], ['/catalog', '用途目录检索'], ['/runs', '运行'],
  ['/apps', '应用与创作'], ['/admin', '管理台'], ['/login', '退出'],
]) {
  const link = make('a');
  link.setAttribute('href', href);
  link.textContent = label;
  nav.appendChild(link);
}
make('span', 'account-label');
make('span', 'account-role');
context.document.querySelector = (selector) => (selector === '[data-orchara-nav]' ? nav : null);
ui.decorateNavigation({ roles: ['admin', 'creator'], kind: 'user' });
const labels = nav.children.filter((c) => c.tagName === 'A').map((c) => c.textContent);
assert.deepStrictEqual(labels, ['会话', '用途目录检索', '运行', '应用与创作', '管理台', '退出'], labels);
const groups = nav.children.filter((c) => String(c.className).split(/\s+/).includes('nav-group')).map((c) => c.textContent);
assert.deepStrictEqual(groups, ['工作空间', '创作与资源', '管理', '账号'], groups);
for (const link of nav.children.filter((c) => c.tagName === 'A')) {
  assert.strictEqual(link.children[0].tagName, 'SVG', 'every entry gets a leading icon');
  assert.strictEqual(String(link.children[1].className), 'nav-label', 'the label keeps its class');
  assert.strictEqual(link.getAttribute('title'), link.children[1].textContent, 'title mirrors the label');
}
const current = nav.children.filter((c) => c.tagName === 'A' && c.getAttribute('aria-current') === 'page');
assert.strictEqual(current.length, 1, 'exactly one aria-current entry');
assert.strictEqual(current[0].getAttribute('href'), '/chat');
assert.strictEqual(byId.get('account-label').textContent, '我的工作空间');
assert.strictEqual(byId.get('account-role').textContent, '管理员 · 创作者');
process.stdout.write('ui contract ok\n');
"""


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the shared UI bytes cannot be executed")
    return NODE


def test_ui_js_icons_and_navigation_decoration_keep_their_contract(node_binary: str, tmp_path) -> None:
    script = UI_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
    harness = tmp_path / "t165-ui.js"
    harness.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "ui contract ok" in completed.stdout, completed.stdout


def test_nav_js_keeps_the_role_vocabulary_and_the_single_navigation_source() -> None:
    nav = read_asset("nav.js")
    labels = [
        "会话", "用途目录检索", "运行", "审批", "应用与创作", "应用（只读）", "知识库",
        "工具", "模型", "记忆", "个人数据", "附件", "管理台", "发布审核", "状态", "退出",
    ]
    for label in labels:
        assert f"'{label}'" in nav, label
    assert "data-orchara-logout" in nav
    assert "data-static-navigation" in nav
    # Visual grouping must not invent counts from no data source.
    assert "nav-count" not in nav, "no fabricated navigation counters in T1"


def test_verify_frontend_derives_the_shell_style_from_the_tokens() -> None:
    source = (st.PRODUCT_ROOT / "tools" / "verify_frontend.py").read_text(encoding="utf-8")
    assert "rgb(246, 247, 251)" not in source, "the old hard-coded background survives"
    assert "--bg" in source, "the background assertion must read the --bg token"
    assert "--text" in source and "--muted" in source and "--accent" in source
    assert ">= 4.5" in source or ">=4.5" in source
    # Behaviour assertions are not relaxed.
    for marker in ("skip-link", "inert", "reduced_motion", "data-orchara-logout", "#main-content"):
        assert marker in source, marker
