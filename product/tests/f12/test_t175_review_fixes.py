"""T11 (GitHub #175): the two-axis review fixes.

These assertions pin the fixes the review round makes, so a later page edit
cannot quietly undo them:

1. ``aria-current="page"`` is unique **per navigation landmark**, not per page.
2. Closing the phone drawer (scrim or Escape) resets ``aria-expanded``.
3. The metadata type floor is 11px, everywhere.
4. A phone can open the conversation context (run/queue/outputs/destination
   handoff) as a sheet built from the existing rail DOM, with no new request.
5. Every visible phone control keeps a 44px touch target, on all 22 pages.
6. The debug entry assertion is exact again, not a substring.
7. The canvas control accent is one canvas-local token.
8. The phone shell is declared once, with a layer token instead of ``z-index:30``.
9. The two accepted trade-offs are recorded in the spec.

The real-browser half (landmark counts, tap-target boxes, the drawer state)
lives in ``tools/verify_frontend.py``; nothing here claims that run happened.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from support import stack as st

WEB = st.WEB_DIR
DOCS = st.REPO_ROOT / "docs" / "frontend-polish.md"
PAGES = sorted(path.name for path in WEB.glob("*.html"))


class Landmarks(HTMLParser):
    """Collect every ``nav`` / ``[role="navigation"]`` and its current marks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.open: list[dict] = []
        self.landmarks: list[dict] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav" or attrs.get("role") == "navigation":
            self.open.append(
                {
                    "tag": tag,
                    "label": attrs.get("aria-label") or attrs.get("class") or "",
                    "depth": len(self.stack),
                    "current": [],
                }
            )
        if self.open and attrs.get("aria-current") is not None:
            self.open[-1]["current"].append(attrs.get("aria-current"))
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        while self.open and self.open[-1]["depth"] >= len(self.stack):
            self.landmarks.append(self.open.pop())


def read_page(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def read_asset(name: str) -> str:
    return (WEB / "assets" / name).read_text(encoding="utf-8")


def landmarks_of(page: str) -> list[dict]:
    parser = Landmarks()
    parser.feed(read_page(page))
    return parser.landmarks


# ---------------------------------------------------------------------------
# 1. aria-current is unique per navigation landmark
# ---------------------------------------------------------------------------
def test_the_spec_defines_aria_current_per_navigation_landmark() -> None:
    spec = DOCS.read_text(encoding="utf-8")
    assert "每个导航地标" in spec, "the contract still says one aria-current per page"
    assert "最多一个" in spec, "the per-landmark maximum is not written"
    assert '`nav` / `[role="navigation"]`' in spec, "the landmarks are not named"
    assert '唯一 `aria-current="page"`' not in spec, "the old per-page wording survives"


def test_no_landmark_carries_two_current_page_marks() -> None:
    for page in PAGES:
        for landmark in landmarks_of(page):
            assert len(landmark["current"]) <= 1, (page, landmark)
        # The sidebar nav is rendered by nav.js; the static auth nav and the
        # breadcrumb are page bytes and must mark the current page once.
        static = [item for item in landmarks_of(page) if item["label"] not in {"主导航"}]
        for landmark in static:
            assert landmark["current"].count("page") == 1, (page, landmark)


def test_verify_frontend_counts_aria_current_per_landmark() -> None:
    verify = st.TOOLS_ROOT.joinpath("verify_frontend.py").read_text(encoding="utf-8")
    assert "nav, [role=\"navigation\"]" in verify, "the check is not landmark-scoped"
    assert 'querySelectorAll(\'[aria-current="page"]\')' in verify
    # The original, stricter main-navigation assertion is kept.
    assert "nav[data-orchara-nav] [aria-current=\"page\"]" in verify
    assert "T11 (#175)" in verify


# ---------------------------------------------------------------------------
# 2. the closed phone drawer reports aria-expanded="false"
# ---------------------------------------------------------------------------
def test_the_drawer_close_path_reports_collapsed() -> None:
    ui = read_asset("ui.js")
    match = re.search(r"function closeMenu\(\)\s*\{(.*?)\n    \}", ui, re.S)
    assert match, "ui.js has no closeMenu()"
    body = match.group(1)
    assert "setExpanded(false)" in body, "closing does not reset aria-expanded"
    assert "setExpanded(true)" not in body


def test_verify_frontend_proves_the_drawer_state_after_scrim_and_escape() -> None:
    verify = st.TOOLS_ROOT.joinpath("verify_frontend.py").read_text(encoding="utf-8")
    assert "get_attribute('aria-expanded')" in verify
    assert "'#nav-scrim').click(" in verify
    assert verify.count("aria-expanded') == 'false'") >= 2, (
        "both the scrim and Escape must be proven to reset aria-expanded"
    )


# ---------------------------------------------------------------------------
# 3. the 11px metadata floor
# ---------------------------------------------------------------------------
SUB_ELEVEN = re.compile(r"\bfont(?:-size)?\s*:\s*[^;{}]*\b(?:[0-9]|10)(?:\.\d+)?px")


def test_authored_metadata_never_goes_below_eleven_pixels() -> None:
    offenders: list[str] = []
    for path in sorted(WEB.rglob("*.html")) + sorted(WEB.rglob("*.css")):
        if path.name == "canvas.css" and path.parent.name == "assets":
            # Generated: it carries the vendored Vue Flow CSS; the authored
            # source is checked below.
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if SUB_ELEVEN.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    for path in sorted((WEB.parent / "canvas" / "src").rglob("*.css")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if SUB_ELEVEN.search(line):
                offenders.append(f"canvas/src/{path.name}:{number}: {line.strip()}")
    assert not offenders, offenders[:20]


def test_the_committed_canvas_stylesheet_only_carries_vendored_small_text() -> None:
    built = read_asset("canvas.css")
    # Vue Flow's own edge label is 10px. The authored canvas already overrides
    # `.vue-flow__edge-text` to the 11px token later in the same file, and no
    # other authored declaration goes below 11px.
    assert built.count("font-size: 10px") == 1, built.count("font-size: 10px")
    assert built.count("font-size: 9px") == 0
    # The authored override is the last `.vue-flow__edge-text` rule in the file.
    tail = built[built.rindex(".vue-flow__edge-text {"):]
    assert "font-size: var(--fs-11)" in tail[:600], "the 11px edge-label override is missing"


# ---------------------------------------------------------------------------
# 4. the phone context sheet
# ---------------------------------------------------------------------------
def test_the_chat_phone_context_sheet_reuses_the_existing_rail() -> None:
    html = read_page("chat.html")
    for marker in (
        'data-context-toggle="true"',
        'id="chat-context"',
        'data-context-close="true"',
        'class="chat-context__sheet-head"',
        "body.chat-context-open #chat-context.chat-context",
        "body.chat-context-open .chat-context-scrim",
    ):
        assert marker in html, f"chat.html is missing {marker!r}"
    # The sheet is the existing rail: the five reviewed sections are the same
    # nodes the desktop rail renders (no second, network-backed copy).
    aside = html[html.index('id="chat-context"'):]
    aside = aside[: aside.index("</aside>")]
    for marker in (
        "当前运行", "会话队列", "本次产出", "数据去向", "可交接应用",
        'id="request-panel"', 'id="queue-panel"', "context-destination",
        "context-handoff",
    ):
        assert marker in aside, f"the context sheet lost {marker!r}"
    js = read_asset("chat.js")
    match = re.search(r"function setContextDrawer\(document, open\)\s*\{(.*?)\n\}", js, re.S)
    assert match, "chat.js has no setContextDrawer()"
    body = match.group(1)
    assert "inert" in body, "the closed sheet is not inert"
    assert "aria-expanded" in body, "the toggle does not report the sheet state"
    assert "fetch(" not in body, "opening the sheet must not add a request"
    for marker in ("[data-context-toggle]", "[data-context-close]", "contextDrawerOpen(document)"):
        assert marker in js, f"chat.js does not wire {marker}"


# ---------------------------------------------------------------------------
# 5. 44px phone touch targets on every page
# ---------------------------------------------------------------------------
def test_the_shared_phone_shell_sets_the_touch_target_once() -> None:
    css = read_asset("design.css")
    mobile = css[css.index("/* T11 (#175)"):]
    for marker in (
        ".page-content a",
        ".page-content button",
        ".page-heading .btn",
        ".app-sidebar nav a",
        ".workspace-bar .topbar-account",
        "min-height: 44px",
    ):
        assert marker in mobile, f"the shared phone touch rule is missing {marker!r}"


def test_verify_frontend_measures_every_visible_control() -> None:
    verify = st.TOOLS_ROOT.joinpath("verify_frontend.py").read_text(encoding="utf-8")
    assert "querySelectorAll('a, button')" in verify
    assert "rect.height + 0.5 >= 44" in verify
    assert "skip-link" in verify, "the focus-only skip link must be named as the only exemption"


# ---------------------------------------------------------------------------
# 6. the exact debug-entry assertion
# ---------------------------------------------------------------------------
def test_the_debug_entry_copy_is_pinned_exactly() -> None:
    t027 = (st.PRODUCT_ROOT / "tests" / "f02a" / "test_t027_pages.py").read_text(
        encoding="utf-8"
    )
    assert '== "打开私有调试页"' in t027, "the debug entry assertion is still a substring"
    assert '"调试" in entry.group(1)' not in t027, "the weakened substring assertion survives"
    editor = read_page("editor.html")
    entry = re.search(r'<a[^>]*id="debug-entry"[^>]*>(.*?)</a>', editor, re.S)
    assert entry and entry.group(1).strip() == "打开私有调试页"
    js = read_asset("editor.js")
    assert "'/debug'" in js and "debug-entry" in js


# ---------------------------------------------------------------------------
# 7. the canvas control accent is one token
# ---------------------------------------------------------------------------
def test_the_canvas_control_accent_is_a_single_token() -> None:
    source = (WEB.parent / "canvas" / "src" / "canvas.css").read_text(encoding="utf-8")
    assert source.count("#7a5c2e") == 1, "the control accent is still repeated"
    assert "--canvas-node-control: #7a5c2e" in source
    assert source.count("var(--canvas-node-control)") == 4
    built = read_asset("canvas.css")
    assert "--canvas-node-control: #7a5c2e" in built
    assert built.count("#7a5c2e") == 1
    assert built.count("var(--canvas-node-control)") == 4


# ---------------------------------------------------------------------------
# 8. one phone shell definition, tokenized layers
# ---------------------------------------------------------------------------
def test_the_phone_shell_is_declared_once() -> None:
    design = read_asset("design.css")
    mobile = read_asset("mobile.css")
    assert ".mobile-tabbar" in design, "the shared shell lost the tab bar"
    assert re.search(r"\.mobile-tabbar\s*\{", mobile) is None, (
        "mobile.css still re-declares the shared tab bar"
    )
    assert "z-index: 30" not in design and "z-index: 30" not in mobile
    assert "--z-tabbar: 30" in design
    assert "z-index: var(--z-tabbar)" in design
    # The phone shell still owns the tab-bar clearance for the six bar pages.
    assert re.search(
        r'body\[data-tabbar="true"\]\s+\.page-content\s*\{[^}]*padding-bottom:\s*'
        r"calc\(52px \+ env\(safe-area-inset-bottom[^}]*\)",
        design,
    )


# ---------------------------------------------------------------------------
# 9. the accepted trade-offs are written down
# ---------------------------------------------------------------------------
def test_the_two_accepted_trade_offs_are_recorded() -> None:
    spec = DOCS.read_text(encoding="utf-8")
    assert "已知取舍" in spec
    assert "静态" in spec and ("侧栏" in spec or "顶栏" in spec)
    assert "readArtifactText" in spec and "fetchArtifactText" in spec
    chat = read_asset("chat.js")
    function_at = chat.index("async function readArtifactText")
    doc_block = chat[chat.rindex("/**", 0, function_at):function_at]
    assert "runs.js" in doc_block and "fetchArtifactText" in doc_block
