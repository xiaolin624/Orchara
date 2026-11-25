"""T9 (GitHub #173): cross-page polish for mobile, accessibility and motion.

These assertions are static/contract-shaped: they pin the presentation decisions
the final polish ticket makes so a later page edit cannot silently undo them.
The real-browser half (viewport overflow, tabbar clearance, contrast,
reduced-motion and focus rings) lives in ``tools/verify_frontend.py`` and is run
with a real Chrome + DevStack; nothing here claims that run happened.
"""

from __future__ import annotations

import re

import pytest

from support import stack as st

WEB = st.WEB_DIR

PAGES = sorted(path.name for path in WEB.glob("*.html"))

# The 20 pages that document themselves with the shared usage disclosure.
USAGE_PAGES = [name for name in PAGES if name not in {"chat.html", "editor.html"}]

# Six pages load mobile.css and carry the bottom tab bar.
TABBAR_PAGES = (
    "chat.html",
    "approvals.html",
    "runs.html",
    "personal-data.html",
    "memories.html",
    "knowledge.html",
)

# Visible form labels that used to expose raw API field names. Every visible
# label must now start in Chinese and, where the field name is useful for API
# cross-checking, carry it as a small mono hint instead.
FORM_LABEL_PAGES = (
    "models.html",
    "tools.html",
    "attachments.html",
    "knowledge.html",
    "publish.html",
    "admin.html",
    "evaluate.html",
    "editor.html",
    "reviews.html",
    "debug.html",
    "personal-data.html",
)

# Empty states that are static markup and must use the shared anatomy. The
# second value is the exact marker attribute the page script toggles.
EMPTY_STATES = (
    ("apps.html", "id", "apps-empty"),
    ("catalog.html", "id", "catalog-empty"),
    ("approvals.html", "id", "approvals-empty"),
    ("runs.html", "id", "runs-empty"),
    ("memories.html", "id", "candidates-empty"),
    ("memories.html", "id", "memories-empty"),
    ("personal-data.html", "id", "memories-empty"),
    ("personal-data.html", "id", "documents-empty"),
    ("personal-data.html", "id", "grants-empty"),
    ("tools.html", "id", "tools-empty"),
    ("evaluate.html", "id", "case-empty"),
    ("versions.html", "data", "data-versions-empty"),
    ("reviews.html", "data", "data-reviews-empty"),
    ("models.html", "data", "data-t6-models-empty"),
    ("knowledge.html", "data", "data-t6-docs-empty"),
    ("attachments.html", "data", "data-t6-artifact-empty"),
)


def read_page(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_the_product_still_has_the_22_design_pages() -> None:
    assert len(PAGES) == 22, PAGES


@pytest.mark.parametrize("page", USAGE_PAGES)
def test_each_page_ends_with_one_flat_usage_disclosure(page: str) -> None:
    html = read_page(page)
    # The text stays (tests assert substrings); only the placement changes.
    assert html.count('<details class="usage-notes">') == 1, page
    assert re.search(r"</details>\s*</main>", html), page
    assert html.rindex('<details class="usage-notes">') > html.rindex(
        'class="page-sections"'
    ), page
    # No standalone padded card: the shared stylesheet flattens it into one line.
    css = (WEB / "assets" / "design.css").read_text(encoding="utf-8")
    block = css[css.rindex("/* T9 (GitHub #173)"):]
    assert ".page-content .usage-notes" in block
    assert "border-top: 1px solid var(--border)" in block
    assert "padding: 8px 0 0" in block


@pytest.mark.parametrize("page", ["chat.html", "editor.html"])
def test_the_two_editor_like_pages_have_no_usage_disclosure(page: str) -> None:
    assert '<details class="usage-notes">' not in read_page(page), page


@pytest.mark.parametrize("page", TABBAR_PAGES)
def test_the_six_tabbar_pages_reserve_the_bottom_safe_area(page: str) -> None:
    html = read_page(page)
    assert 'data-tabbar="true"' in html, page
    assert 'class="mobile-tabbar"' in html, page
    css = (WEB / "assets" / "mobile.css").read_text(encoding="utf-8")
    match = re.search(
        r'body\[data-tabbar="true"\]\s+\.page-content\s*\{[^}]*padding-bottom:\s*'
        r"calc\(52px \+ env\(safe-area-inset-bottom[^}]*\)",
        css,
    )
    assert match, "mobile.css no longer reserves 52px + safe-area for the tabbar"


@pytest.mark.parametrize("page", FORM_LABEL_PAGES)
def test_every_visible_form_label_starts_in_chinese(page: str) -> None:
    html = read_page(page)
    for match in re.finditer(r"<label\b([^>]*)>(.*?)</label>", html, re.S):
        inner = re.sub(r"<input[^>]*>", "", match.group(2))
        visible = re.sub(r"<[^>]+>", "", inner)
        visible = re.sub(r"\s+", " ", visible).strip()
        if not visible:
            continue
        assert re.match(r"[\u4e00-\u9fff]", visible), (page, visible)


def test_the_models_form_names_chinese_fields_and_keeps_the_api_names_as_hints() -> None:
    html = read_page("models.html")
    for text in ("名称", "供应商", "模型 ID", "端点引用", "凭据引用", "数据去向", "模型配置"):
        assert text in html, text
    for field in (
        "name",
        "provider",
        "model_id",
        "endpoint_ref",
        "secret_ref",
        "data_destination",
        "config",
    ):
        assert f'<span class="field-api">{field}</span>' in html, field
    assert "登记为待验证（pending）" in html
    assert "登记为 pending" not in html
    # The enum keeps the original value in parentheses.
    assert "桩（stub" in html and "openai" in html


def test_the_tools_form_calls_the_fields_by_their_product_name() -> None:
    html = read_page("tools.html")
    for field in (
        "source_artifact_id",
        "risk_class",
        "input_schema",
        "output_schema",
        "dependency_lock",
    ):
        assert f'<span class="field-api">{field}</span>' in html, field
    assert "风险类别" in html and "纯函数（pure）" in html
    assert "依赖锁" in html and "输入 Schema" in html and "输出 Schema" in html


def test_the_editor_toolbar_keeps_only_the_application_and_the_revision() -> None:
    html = read_page("editor.html")
    # The page name is the accessible h1; the visible toolbar line is the
    # application reference plus the revision (the breadcrumb already names the
    # editor), so the page name is not printed twice.
    title = re.search(r'<div class="editor-title">(.*?)</div>', html, re.S)
    assert title, "editor.html has no toolbar title block"
    assert '<h1 class="sr-only">工作流编辑器</h1>' in title.group(1)
    assert 'class="editor-app-ref" data-editor-app-ref' in title.group(1)
    assert "工作流编辑器" not in re.sub(r"<h1[^>]*>.*?</h1>", "", title.group(1))
    # The only validate affordance is the toolbar one (it keeps the id at
    # runtime); the inspector offers save instead.
    assert html.count("校验草稿") == 1
    assert 'id="canvas-validate"' not in html, "the pinned HTML id inventory stays untouched"
    wiring = read_page("assets/editor.js")
    assert "validate.id = 'canvas-validate'" in wiring
    source = (WEB.parent / "canvas" / "src" / "main.js").read_text(encoding="utf-8")
    committed = (WEB / "assets" / "canvas.js").read_text(encoding="utf-8")
    for bundle in (source, committed):
        assert "校验草稿" not in bundle, "the inspector repeats the toolbar validate action"
        assert "保存草稿" in bundle, "the inspector has no save action"


def test_the_app_row_inline_id_wraps_and_lines_up_with_the_title() -> None:
    css = read_page("assets/design.css")
    block = css[css.rindex("/* T9 (GitHub #173)"):]
    for marker in (
        'body[data-page="apps"] .app-row-id',
        "font-family: var(--font-mono)",
        "font-size: var(--fs-11)",
        "overflow-wrap: anywhere",
        "app-row-id-label",
    ):
        assert marker in block, marker
    page = read_page("apps.html")
    assert "idLabel.textContent = '应用 ID'" in page
    assert "activeLabel.textContent = '生效版本'" in page


def test_the_topbar_new_application_action_is_hidden_where_it_has_no_context() -> None:
    css = read_page("assets/design.css")
    block = css[css.rindex("/* T9 (GitHub #173)"):]
    assert 'body[data-page="admin"] .topbar-new' in block
    assert 'body[data-page="index"] .topbar-new' in block
    assert "display: none" in block
    # The creator gate itself is untouched on the pages that keep the button.
    apps = read_page("apps.html")
    assert 'data-requires-role="creator"' in apps


@pytest.mark.parametrize("page,attr,value", EMPTY_STATES)
def test_empty_states_share_one_anatomy(page: str, attr: str, value: str) -> None:
    html = read_page(page)
    if attr == "id":
        pattern = r'<div\b[^>]*id="%s"[^>]*>(.*?)</div>' % re.escape(value)
    else:
        pattern = r'<div\b[^>]*%s[^>]*>(.*?)</div>' % re.escape(value)
    match = re.search(pattern, html, re.S)
    assert match, (page, value)
    body = match.group(1)
    assert 'class="empty-mark"' in body, (page, value)
    assert "data-icon=" in body, (page, value)
    assert re.search(r"<strong>[^<]+</strong>", body), (page, value)
    if page == "catalog.html":
        # The explanation is server-derived and written after the search lands.
        assert 'class="empty-explain" data-empty-text=""' in body, (page, value)
    else:
        assert re.search(r'<p class="empty-explain">[^<]+</p>', body), (page, value)
    assert 'class="empty-actions"' in body, (page, value)


def test_numbers_and_relative_time_keep_their_absolute_value() -> None:
    css = read_page("assets/design.css")
    assert "font-variant-numeric: tabular-nums" in css
    for selector in (".stat-value", "td", "th", "time"):
        assert selector in css, selector
    chat = read_page("assets/chat.js")
    assert "time.title = view.absolute" in chat
    assert "stamp.title = formatClock(message.created_at)" in chat


def test_custom_interactions_have_a_visible_focus_ring() -> None:
    css = read_page("assets/design.css")
    block = css[css.rindex("/* T9 (GitHub #173)"):]
    for selector in (
        ".mobile-tabbar a:focus-visible",
        ".segmented a:focus-visible",
        ".segmented button:focus-visible",
        ".page-content details summary:focus-visible",
        ".page-content .pill:focus-visible",
    ):
        assert selector in block, selector
    assert "outline: 2px solid var(--accent)" in block


def test_reduced_motion_turns_every_transition_and_animation_off() -> None:
    css = read_page("assets/design.css")
    block = css[css.rindex("@media (prefers-reduced-motion: reduce)"):]
    assert "transition: none !important" in block
    assert "animation: none !important" in block
    assert "scroll-behavior: auto !important" in block


def test_icon_only_buttons_keep_an_accessible_name() -> None:
    for page in PAGES:
        html = read_page(page)
        for match in re.finditer(r"<button\b([^>]*)>(.*?)</button>", html, re.S):
            attrs, inner = match.group(1), match.group(2)
            visible = re.sub(r"<[^>]+>", "", inner).strip()
            if visible:
                continue
            assert "aria-label=" in attrs, (page, attrs[:120])


def test_shared_copy_uses_full_width_chinese_parentheses() -> None:
    # Half-width brackets after Chinese read as a code comment.
    offenders = []
    for page in PAGES:
        html = read_page(page)
        main = html[html.index("<main"): html.index("</main>")]
        for match in re.finditer(r"[\u4e00-\u9fff]\s*\(([A-Za-z][^()]*)\)", main):
            offenders.append((page, match.group(0)))
    assert not offenders, offenders[:10]
