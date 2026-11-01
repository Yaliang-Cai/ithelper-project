"""Reproducible SCRIPTED browser verification (no live model/embedding calls).

Run with a Python that has Playwright installed, e.g. global `python` on the
development host. Alternatively: pip install playwright && playwright install
chromium. The HTTP server always uses the repository .venv dependencies.
Artifacts go to ignored runtime/browser/<timestamp>; shared usage.sqlite is
never opened by this script or its injected server factory.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / 'runtime' / 'browser' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env = {**os.environ, 'HELPDESK_BROWSER_WORLD': str(output), 'PYTHONPATH': str(ROOT)}
    log = (output / 'server.log').open('w', encoding='utf-8')
    server = subprocess.Popen([str(ROOT / '.venv/Scripts/python.exe'), '-m', 'uvicorn', 'scripts.browser_fixture:factory', '--factory', '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT, env=env, stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    summary = {'mode': 'scripted_model_real_agent_http_browser', 'paid_calls': 0, 'checks': []}
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + '/health', timeout=1).close(); break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError('Fixture server failed; inspect server.log')
                time.sleep(.1)
        with sync_playwright() as pw:
            executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
            if not executable and not Path(pw.chromium.executable_path).exists():
                cached = sorted((Path(os.environ.get('LOCALAPPDATA', '')) / 'ms-playwright').glob('chromium-*/chrome-win64/chrome.exe'))
                executable = str(cached[-1]) if cached else None
            browser = pw.chromium.launch(**({'executable_path': executable} if executable else {}))
            context = browser.new_context(viewport={'width': 1365, 'height': 950})
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            page_errors = []
            page.on('pageerror', lambda error: page_errors.append(str(error)))

            def login(username):
                page.goto(base)
                expect(page.locator('#login-panel')).to_be_visible()
                page.select_option('#username', username)
                page.get_by_role('button', name='登录', exact=True).click()
                expect(page.locator('#identity')).to_contain_text(username)

            def logout():
                page.get_by_role('button', name='退出登录').click()
                expect(page.locator('#login-panel')).to_be_visible()

            login('alice.sales')
            page.get_by_role('button', name='新建聊天').click()
            page.locator('#message').fill('VPN 无法连接，同时申请 snowflake.prod.analytics 只读权限。')
            page.get_by_role('button', name='发送', exact=True).click()
            expect(page.locator('#messages')).to_contain_text('这是脚本浏览器验证', timeout=20000)
            expect(page.locator('#issues')).to_contain_text('snowflake.prod.analytics')
            expect(page.locator('#messages img')).to_have_count(0)
            cid = page.locator('#history').input_value()
            assert cid.startswith('CHAT-')
            page.get_by_role('button', name='拒绝此操作').click()
            expect(page.locator('#issues')).to_contain_text('已拒绝')
            expect(page.locator('#issues')).to_contain_text('待审批')
            summary['checks'].extend(['login', 'queued chat', 'safe untrusted text', 'disruptive confirmation denied'])
            page.get_by_role('button', name='收起聊天').click()
            expect(page.locator('#chat-layout')).to_be_hidden()
            page.select_option('#history', cid)
            expect(page.locator('#messages')).to_contain_text('这是脚本浏览器验证')
            page.screenshot(path=str(output / 'employee-desktop.png'), full_page=True)
            assert page.request.get(base + '/debug/runs').status == 403
            assert page.request.get(base + '/approvals').status == 403
            summary['checks'].extend(['close/reopen own chat', 'employee debug/approver isolation'])

            # Simulate lost message response after server has committed. The
            # UI must retry the exact stable logical key without a second turn.
            submissions = []
            def lose_response(route):
                submissions.append(route.request.post_data_json)
                if len(submissions) == 1:
                    route.fetch(); route.abort('failed')
                else:
                    route.continue_()
            page.route('**/conversations/*/messages', lose_response)
            page.locator('#message').fill('保留这条消息用于传输重试')
            page.get_by_role('button', name='发送', exact=True).click()
            expect(page.locator('#error')).to_be_visible()
            page.wait_for_timeout(1800)
            expect(page.locator('#error')).to_contain_text('发送结果尚未确认')
            page.get_by_role('button', name='重试发送', exact=True).click()
            expect(page.locator('#message')).to_have_value('')
            assert submissions[0]['idempotency_key'] == submissions[1]['idempotency_key']
            page.unroute('**/conversations/*/messages', lose_response)
            summary['checks'].append('lost-response stable message retry')
            logout()
            login('pat.approver')
            page.get_by_role('link', name='资源审批').click()
            expect(page.locator('#approvals')).to_contain_text('snowflake.prod.analytics')
            expect(page.locator('#approvals')).to_contain_text('alice.sales')
            page.get_by_role('button', name='批准只读申请').click()
            expect(page.locator('#approvals')).to_contain_text('已批准')
            page.screenshot(path=str(output / 'approver.png'), full_page=True)
            summary['checks'].append('separate approver identity exact resource')
            logout()
            login('alice.sales')
            page.select_option('#history', cid)
            expect(page.locator('#issues')).to_contain_text('已执行（模拟）', timeout=20000)
            expect(page.locator('#issues')).to_contain_text('已拒绝')
            expect(page.locator('#progress')).to_contain_text('本轮未完成', timeout=20000)
            summary['checks'].extend(['approval resumes original employee issue', 'failed approval run preserves actual committed action card'])
            page.locator('#message').fill('fixture failure')
            page.get_by_role('button', name='发送', exact=True).click()
            expect(page.locator('#messages')).to_contain_text('fixture failure', timeout=20000)
            expect(page.locator('#progress')).to_contain_text('本轮未完成', timeout=20000)
            summary['checks'].append('failed run truthful display')
            page.route('**/conversations/' + cid, lambda route: route.abort('failed'))
            page.get_by_role('button', name='刷新', exact=True).click()
            expect(page.locator('#error')).to_contain_text('读取进度失败')
            page.unroute('**/conversations/' + cid)
            page.get_by_role('button', name='刷新', exact=True).click()
            summary['checks'].append('polling transport failure display and recovery')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output / 'employee-mobile.png'), full_page=True)
            summary['checks'].append('390px mobile no horizontal overflow')
            logout()
            login('dev.debug')
            page.get_by_role('link', name='调试记录').click()
            expect(page.locator('#debug-output')).to_contain_text('trace')
            text = page.locator('#debug-output').inner_text()
            assert 'fixture-private-reasoning' not in text and 'reasoning_content' not in text
            page.screenshot(path=str(output / 'debug.png'), full_page=True)
            assert not page_errors, page_errors
            summary['checks'].append('protected debug context/tool trace and redaction')
            context.tracing.stop(path=str(output / 'trace.zip'))
            browser.close()
        summary['status'] = 'passed'
    except Exception as exc:
        summary.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill(); server.wait()
        log.close()
        print(json.dumps({'status': summary.get('status'), 'mode': summary['mode'], 'checks': len(summary['checks']), 'artifacts': str(output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
