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
            colors = page.evaluate("Object.fromEntries(['--text','--muted','--primary','--surface'].map(k => [k, getComputedStyle(document.documentElement).getPropertyValue(k).trim()]))")
            pairs = {'body text': (colors['--text'], colors['--surface']), 'secondary text': (colors['--muted'], colors['--surface']), 'primary button': ('#ffffff', colors['--primary'])}
            report['contrast'] = {name: round(contrast(*pair), 2) for name, pair in pairs.items()}
            assert min(report['contrast'].values()) >= 4.5, report['contrast']
            report['checks'].append('primary, secondary and button text meet 4.5:1 contrast')
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
                assert page.locator('body').evaluate("el => getComputedStyle(el).backgroundColor") == 'rgb(246, 247, 251)', name
                page.screenshot(path=str(screenshots / (name + '-desktop.png')), full_page=True)
                report['pages'].append({'page': name, 'route': route, 'desktop': 'passed', 'state': 'not-found error' if name == 'evaluations' else 'real dev-stub API'})
                print('[ui] desktop ' + name, flush=True)
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
            for name in ('chat', 'approvals', 'runs', 'personal-data', 'memories', 'knowledge', 'apps', 'login', 'activate'):
                page.goto(stack.base_url + routes[name])
                page.wait_for_timeout(180)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'), name
                page.screenshot(path=str(screenshots / (name + '-mobile.png')), full_page=True)
                next(item for item in report['pages'] if item['page'] == name)['mobile'] = 'passed'
                print('[ui] mobile ' + name, flush=True)
            page.goto(stack.base_url + '/chat')
            page.locator('#sidebar-toggle').click()
            assert page.locator('#app-sidebar').evaluate('el => !el.inert')
            page.keyboard.press('Escape')
            assert page.locator('#app-sidebar').evaluate('el => el.inert')
            report['checks'].append('mobile navigation opens and Escape closes it')
            page.emulate_media(reduced_motion='reduce')
            assert page.locator('#app-sidebar').evaluate("el => getComputedStyle(el).transitionDuration") == '0s'
            report['checks'].append('reduced motion disables sidebar transitions')
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
