#!/usr/bin/env python3
"""Real-browser UI regression and screenshots for the full product redesign.

Requires the development toolchain and Python Playwright with Chrome installed.
Starts an isolated, uniquely named dev-stub database via the normal DevStack.
Credentials stay in memory; screenshots contain only generated sample data.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import secrets
import sys
import uuid
from pathlib import Path

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / 'deploy/dev'))
from bringup import DevStack  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def contrast(first: str, second: str) -> float:
    def luminance(color: str) -> float:
        rgb = [int(color.strip().lstrip('#')[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + .05) / (dark + .05)


def css_color(color: str) -> str:
    """Translate a #rrggbb design token into the browser's computed rgb() form."""
    value = color.strip().lstrip('#')
    assert len(value) == 6, f"the design token is not a 6-digit hex colour: {color}"
    return 'rgb({}, {}, {})'.format(*(int(value[i:i + 2], 16) for i in (0, 2, 4)))


def verify(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    screenshots = out / 'screenshots'
    screenshots.mkdir(exist_ok=True)
    password = secrets.token_urlsafe(32)
    run_id = uuid.uuid4().hex[:12]
    report = {'profile': 'dev-stub', 'data': 'generated UI review fixtures', 'pages': [], 'checks': [], 'page_errors': []}
    stack = DevStack.start(
        database='orchara_ui_' + run_id,
        state_root=PRODUCT / '.devstack' / 'ui-review',
        admin_password=password,
    )
    try:
        root = stack.http.get('/')
        assert root.status == 303 and root.header('Location') == '/chat'
        report['checks'].append('root redirects to /chat')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, device_scale_factor=1)
            page = context.new_page()
            page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
            page.goto(stack.base_url + '/login')
            page.screenshot(path=str(screenshots / 'login-desktop.png'), full_page=True)
            page.locator('#login').fill(stack.admin_login)
            page.locator('#password').fill(password)
            page.locator('#login-form button[type="submit"]').click()
            page.wait_for_url('**/chat')
            page.locator('#account-label').filter(has_text='我的工作空间').wait_for()
            report['checks'].append('real login enters /chat')
            tokens = page.evaluate(
                "Object.fromEntries("
                "['--bg','--text','--text-2','--muted','--accent','--accent-ink','--accent-soft',"
                "'--surface','--surface-2','--on-accent','--success','--success-soft',"
                "'--warning','--warning-soft','--danger','--danger-soft','--info','--info-soft',"
                "'--teal','--teal-soft']"
                ".map(k => [k, getComputedStyle(document.documentElement).getPropertyValue(k).trim()]))"
            )
            pairs = {
                'body text': (tokens['--text'], tokens['--surface']),
                'secondary text': (tokens['--text-2'], tokens['--surface']),
                'muted text': (tokens['--muted'], tokens['--surface']),
                'muted on soft': (tokens['--muted'], tokens['--surface-2']),
                'secondary on soft': (tokens['--text-2'], tokens['--surface-2']),
                'primary button': (tokens['--on-accent'], tokens['--accent']),
                'accent ink on soft': (tokens['--accent-ink'], tokens['--accent-soft']),
                'success on soft': (tokens['--success'], tokens['--success-soft']),
                'warning on soft': (tokens['--warning'], tokens['--warning-soft']),
                'danger on soft': (tokens['--danger'], tokens['--danger-soft']),
                'info on soft': (tokens['--info'], tokens['--info-soft']),
                'teal on soft': (tokens['--teal'], tokens['--teal-soft']),
            }
            report['tokens'] = tokens
            report['contrast'] = {name: round(contrast(*pair), 2) for name, pair in pairs.items()}
            assert min(report['contrast'].values()) >= 4.5, report['contrast']
            report['checks'].append(
                'text, secondary, muted, button, accent-ink and status soft pairs meet 4.5:1 contrast'
            )
            # The shell background is whatever --bg says; the page must not
            # hard-code a colour that drifts from the token.
            expected_background = css_color(tokens['--bg'])
            me = context.request.get(stack.base_url + '/api/v1/auth/me').json()
            csrf = me['csrf_token']
            # Bootstrap intentionally grants admin, not creator. Grant the
            # generated reviewer the role via the real admin endpoint.
            changed = context.request.put(
                stack.base_url + '/api/v1/admin/users/' + me['principal_id'] + '/roles',
                data={'roles': ['user', 'creator', 'admin']}, headers={'X-CSRF-Token': csrf},
            )
            assert changed.ok, changed.status
            page.goto(stack.base_url + '/apps')
            page.locator('#create-app-card').wait_for(state='visible')
            page.locator('#create-app-name').fill('销售数据分析助手')
            page.locator('#create-app-submit').click()
            page.wait_for_url('**/edit')
            app_id = page.url.split('/apps/')[1].split('/')[0]
            page.locator('.node-library').wait_for()
            page.locator('[data-node-type="model"]').click()
            page.locator('.config-panel').filter(has_text='模型').wait_for()
            # T9 (#173): one validate entry (toolbar, keeping #canvas-validate at
            # runtime) and one save action in the inspector footer.
            assert page.locator('#canvas-validate').count() == 1
            assert page.locator('.page-heading #canvas-validate').count() == 1
            assert page.locator('.config-panel #canvas-validate').count() == 0
            assert page.locator('.config-panel #canvas-save-draft').count() == 1
            assert page.locator('.page-heading #draft-save').count() == 1
            report['checks'].append(
                'editor keeps a single validate entry and saves from the inspector'
            )
            report['checks'].append('create application and add a model node through real UI')
            # A real concurrent save must keep the local draft and offer resolution.
            draft_response = context.request.get(stack.base_url + '/api/v1/applications/' + app_id + '/draft')
            draft = draft_response.json()
            update = context.request.put(
                stack.base_url + '/api/v1/applications/' + app_id + '/draft',
                data={k: draft[k] for k in ('definition', 'layout', 'capability_card')},
                headers={'X-CSRF-Token': csrf, 'If-Match': draft_response.headers['etag']},
            )
            assert update.ok, (update.status, update.text())
            page.locator('#draft-save').click()
            page.locator('#conflict-panel').wait_for(state='visible')
            page.screenshot(path=str(screenshots / 'editor-conflict-desktop.png'), full_page=True)
            report['checks'].append('concurrent server save shows actual conflict without overwriting local draft')
            # Other cards are real persisted drafts, never fake published apps.
            for name in ('团队知识问答', '报告审阅助手'):
                created = context.request.post(stack.base_url + '/api/v1/applications', data={'name': name}, headers={'X-CSRF-Token': csrf})
                assert created.ok
            routes = {
                'index': '/status', 'login': '/login', 'activate': '/activate',
                'apps': '/apps', 'catalog': '/catalog', 'editor': f'/apps/{app_id}/edit',
                'debug': f'/apps/{app_id}/debug', 'chat': '/chat', 'runs': '/runs',
                'approvals': '/approvals', 'tools': '/tools', 'models': '/models',
                'attachments': '/attachments', 'knowledge': '/knowledge', 'memories': '/memories',
                'personal-data': '/personal-data', 'evaluate': f'/apps/{app_id}/evaluate',
                'evaluations': '/evaluations/' + str(uuid.uuid4()),
                'publish': f'/apps/{app_id}/publish', 'reviews': '/reviews',
                'versions': f'/apps/{app_id}/versions', 'admin': '/admin',
            }
            for name, route in routes.items():
                response = page.goto(stack.base_url + route)
                assert response.status == 200, (name, response.status)
                page.locator('#main-content').wait_for()
                if name not in ('login', 'activate'):
                    page.locator('nav[data-orchara-nav] .nav-label').first.wait_for()
                    assert page.locator('nav[data-orchara-nav] a[aria-current="page"]').count() == 1, name
                page.wait_for_timeout(180)
                assert page.locator('h1').count() == 1, name
                assert page.locator('link[href="/assets/design.css"]').count() == 1, name
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'), name
                assert page.locator('body').evaluate("el => getComputedStyle(el).backgroundColor") == expected_background, (name, expected_background)
                # T9 (#173): the technical notes are one flat disclosure at the
                # very end of the page, never a card between header and content.
                usage = page.locator('details.usage-notes')
                if name in ('chat', 'editor'):
                    assert usage.count() == 0, name
                else:
                    assert usage.count() == 1, name
                    assert page.evaluate(
                        """() => {
                            const details = document.querySelector('details.usage-notes');
                            const main = document.querySelector('main.page-content');
                            return Boolean(details && main && details.parentElement === main
                                && main.lastElementChild === details);
                        }"""
                    ), (name, 'usage notes are not the last block of main')
                    assert page.locator('details.usage-notes').evaluate(
                        "el => getComputedStyle(el).borderStyle") != 'dashed', name
                    assert page.locator('details.usage-notes').evaluate(
                        "el => parseFloat(getComputedStyle(el).paddingLeft)") == 0, name
                # T9 (#173): the topbar "new application" action has no context on
                # the admin console or the platform status page.
                topbar_new = page.locator('.topbar-new')
                if name in ('admin', 'index'):
                    assert topbar_new.count() == 1, name
                    assert topbar_new.evaluate(
                        "el => getComputedStyle(el).display") == 'none', name
                elif name not in ('login', 'activate'):
                    assert topbar_new.count() == 1, name
                    assert topbar_new.evaluate(
                        "el => getComputedStyle(el).display") != 'none', name
                page.screenshot(path=str(screenshots / (name + '-desktop.png')), full_page=True)
                report['pages'].append({'page': name, 'route': route, 'desktop': 'passed', 'state': 'not-found error' if name == 'evaluations' else 'real dev-stub API'})
                print('[ui] desktop ' + name, flush=True)
            page.goto(stack.base_url + '/apps')
            page.locator('#apps-list .app-row').first.wait_for()

            def focus_ring(selector: str) -> None:
                # A keyboard event first: programmatic focus only matches
                # :focus-visible while the last input modality is the keyboard.
                page.keyboard.press('Tab')
                page.evaluate(
                    "sel => { const el = document.querySelector(sel);"
                    " if (!el) throw new Error('missing ' + sel);"
                    " el.focus({ preventScroll: true }); }",
                    selector,
                )
                state = page.evaluate(
                    """sel => {
                        const el = document.querySelector(sel);
                        const style = getComputedStyle(el);
                        return {visible: el.matches(':focus-visible'),
                                outline: style.outlineStyle,
                                width: parseFloat(style.outlineWidth)};
                    }""",
                    selector,
                )
                assert state['visible'], (selector, state)
                assert state['outline'] != 'none' and state['width'] >= 2, (selector, state)

            focus_ring('[data-apps-filter-value="draft"]')
            focus_ring('#apps-list .app-row summary')
            report['checks'].append(
                'segmented filters and row disclosures show a keyboard focus ring'
            )
            # The row's ID disclosure wraps at the title indent, not under the
            # disclosure marker, and never overflows the row.
            page.locator('#apps-list .app-row summary').first.click()
            layout = page.locator('#apps-list .app-row .app-row-id').first.evaluate(
                """el => {
                    const label = el.querySelector('.app-row-id-label').getBoundingClientRect();
                    const code = el.querySelector('code').getBoundingClientRect();
                    const box = el.getBoundingClientRect();
                    return {idLeft: box.left, labelLeft: label.left,
                            codeLeft: code.left, scrollWidth: el.scrollWidth,
                            clientWidth: el.clientWidth};
                }"""
            )
            assert abs(layout['codeLeft'] - layout['idLeft']) < 1, layout
            assert layout['scrollWidth'] <= layout['clientWidth'] + 1, layout
            page.screenshot(
                path=str(screenshots / 'apps-row-expanded-desktop.png'), full_page=True
            )
            report['checks'].append(
                'application row ID disclosure aligns with the title indent without overflow'
            )
            # Main conversation journey and both sidebar modes.
            page.goto(stack.base_url + '/chat')
            page.keyboard.press('Tab')
            assert page.locator('.skip-link').evaluate('el => el === document.activeElement')
            report['checks'].append('keyboard navigation begins with a visible skip-to-content link')
            page.locator('#conversation-create').click()
            page.locator('#chat-panel').wait_for(state='visible')
            assert page.locator('#chat-empty').is_hidden()
            page.locator('#chat-message').fill('请分析本月销售数据，生成按地区汇总的报告。')
            page.screenshot(path=str(screenshots / 'chat-compose-desktop.png'), full_page=True)
            page.locator('#sidebar-toggle').click()
            assert page.locator('body').evaluate("el => el.classList.contains('sidebar-collapsed')")
            page.locator('#sidebar-toggle').click()
            report['checks'].append('conversation creation, composer and desktop sidebar toggle')
            page.set_viewport_size({'width': 375, 'height': 812})
            tabbar_pages = (
                'chat', 'approvals', 'runs', 'personal-data', 'memories', 'knowledge',
            )
            for name, route in routes.items():
                page.goto(stack.base_url + route)
                page.wait_for_timeout(150)
                assert page.evaluate(
                    'document.documentElement.scrollWidth <= innerWidth + 2'), name
                if name in tabbar_pages:
                    # Scroll to the end and prove the last block still sits above
                    # the fixed tab bar (52px + safe area + 16px clearance).
                    clearance = page.evaluate(
                        """() => {
                            const bar = document.querySelector('.mobile-tabbar');
                            if (!bar) return { tabbar: false };
                            // The shared stylesheet scrolls smoothly; turn that
                            // off for the measurement so the scroll is final.
                            const root = document.documentElement;
                            const previous = root.style.scrollBehavior;
                            root.style.scrollBehavior = 'auto';
                            window.scrollTo(0, root.scrollHeight);
                            const barTop = bar.getBoundingClientRect().top;
                            let lastBottom = 0;
                            for (const el of document.querySelectorAll(
                                    'main.page-content > *')) {
                                const rect = el.getBoundingClientRect();
                                if (rect.height > 0) {
                                    lastBottom = Math.max(lastBottom, rect.bottom);
                                }
                            }
                            root.style.scrollBehavior = previous;
                            return { tabbar: true, barTop: barTop,
                                     lastBottom: lastBottom,
                                     ok: lastBottom <= barTop + 1 };
                        }"""
                    )
                    assert clearance['tabbar'], name
                    assert clearance['ok'], (name, clearance)
                    # Primary actions a phone user must tap keep a 44px target.
                    heights = page.evaluate(
                        """() => {
                            const out = {};
                            const selectors = ['.mobile-tabbar a', '.segmented button',
                                '#chat-submit', '#chat-cancel', '#approve-button',
                                '#reject-button', '#upload-submit', '#chat-message'];
                            for (const sel of selectors) {
                                const el = document.querySelector(sel);
                                if (!el || el.closest('[hidden]')) continue;
                                const rect = el.getBoundingClientRect();
                                if (rect.height > 0) out[sel] = rect.height;
                            }
                            return out;
                        }"""
                    )
                    for selector, height in heights.items():
                        assert height >= 44, (name, selector, height)
                    if name == 'chat':
                        # The start screen is taller than a phone; its internal
                        # column must scroll and the last step must end above the
                        # fixed bar once scrolled.
                        start = page.evaluate(
                            """() => {
                                const el = document.querySelector('.chat-workspace');
                                const flow = document.querySelector('.chat-empty__flow');
                                if (!el || !flow) return null;
                                el.scrollTop = el.scrollHeight;
                                return {overflowY: getComputedStyle(el).overflowY,
                                        flowBottom: flow.getBoundingClientRect().bottom,
                                        barTop: document.querySelector('.mobile-tabbar')
                                            .getBoundingClientRect().top};
                            }"""
                        )
                        assert start, 'chat start screen is missing'
                        assert start['overflowY'] in ('auto', 'scroll'), start
                        assert start['flowBottom'] <= start['barTop'] + 1, start
                        page.evaluate(
                            "const el = document.querySelector('.chat-workspace');"
                            " if (el) el.scrollTop = 0;"
                        )
                    page.evaluate('window.scrollTo(0, 0)')
                page.screenshot(path=str(screenshots / (name + '-mobile.png')), full_page=False)
                next(item for item in report['pages'] if item['page'] == name)['mobile'] = 'passed'
                print('[ui] mobile ' + name, flush=True)
            report['checks'].append(
                'all 22 pages fit a 375px viewport; tabbar pages keep the last block above the bar'
            )
            # Evidence for the tabbar clearance itself.
            page.goto(stack.base_url + '/chat')
            page.wait_for_timeout(150)
            page.evaluate(
                """() => {
                    const root = document.documentElement;
                    root.style.scrollBehavior = 'auto';
                    const scrollers = [root, document.scrollingElement,
                        document.querySelector('.chat-workspace'),
                        document.querySelector('.chat-columns')];
                    for (const el of scrollers) {
                        if (el && el.scrollHeight > el.clientHeight) {
                            el.scrollTop = el.scrollHeight;
                        }
                    }
                }"""
            )
            page.screenshot(path=str(screenshots / 'chat-bottom-mobile.png'), full_page=False)
            page.evaluate('window.scrollTo(0, 0)')
            page.goto(stack.base_url + '/chat')
            page.locator('#sidebar-toggle').click()
            assert page.locator('#app-sidebar').evaluate('el => !el.inert')
            page.keyboard.press('Escape')
            assert page.locator('#app-sidebar').evaluate('el => el.inert')
            report['checks'].append('mobile navigation opens and Escape closes it')
            focus_ring('.mobile-tabbar a[aria-current="page"]')
            report['checks'].append('mobile tab bar shows a keyboard focus ring')
            page.emulate_media(reduced_motion='reduce')
            page.evaluate(
                """() => {
                    const probe = document.createElement('div');
                    probe.className = 'skeleton';
                    probe.id = 't9-motion-probe';
                    document.body.appendChild(probe);
                }"""
            )
            motion = page.evaluate(
                """() => {
                    const read = (sel) => {
                        const el = document.querySelector(sel);
                        const style = getComputedStyle(el);
                        return {transition: style.transitionDuration,
                                animation: style.animationDuration};
                    };
                    return {sidebar: read('#app-sidebar'),
                            button: read('.page-content button'),
                            skeleton: read('#t9-motion-probe')};
                }"""
            )
            for part, values in motion.items():
                assert values['transition'] == '0s', (part, values)
                assert values['animation'] == '0s', (part, values)
            page.evaluate("document.getElementById('t9-motion-probe').remove()")
            report['checks'].append(
                'reduced motion disables sidebar, button and skeleton transitions/animations'
            )
            page.set_viewport_size({'width': 844, 'height': 390})
            for name in ('chat', 'approvals', 'runs', 'personal-data'):
                page.goto(stack.base_url + routes[name])
                page.wait_for_timeout(180)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'), name
                next(item for item in report['pages'] if item['page'] == name)['landscape'] = 'passed'
            page.screenshot(path=str(screenshots / 'personal-data-landscape.png'), full_page=True)
            report['checks'].append('conversation, approval, run and personal-data layouts fit phone landscape')
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.goto(stack.base_url + '/chat')
            page.locator('a[data-orchara-logout] svg').click()
            page.wait_for_url('**/login')
            refused = context.request.get(stack.base_url + '/api/v1/auth/me')
            assert refused.status == 401
            report['checks'].append('clicking logout SVG revokes the real session')
            browser.close()
        assert not report['page_errors'], report['page_errors']
        report['result'] = 'passed'
    except Exception as error:
        report['result'] = 'failed'
        report['failure'] = str(error)
        raise
    finally:
        report['checked_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        (out / 'browser-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
        stack.stop()
    print('[ui] full-product browser verification passed', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=PRODUCT / 'reports/ui-redesign')
    verify(parser.parse_args().out.resolve())
