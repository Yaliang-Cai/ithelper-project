"""Controlled-clock browser regression of the real UI, fixture HTTP data only.

Run with optional Playwright Python/browser installation; no model or ledger.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    with sync_playwright() as pw:
        cached = sorted((Path(os.environ.get('LOCALAPPDATA', '')) / 'ms-playwright').glob('chromium-*/chrome-win64/chrome.exe'))
        executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or (str(cached[-1]) if cached else None)
        browser = pw.chromium.launch(**({'executable_path': executable} if executable else {}))
        page = browser.new_page()
        now = 1800000000
        state = dict(messages=[], issues=[dict(id='ISSUE-a', goal='VPN', status='resolved')], confirmations=[dict(id='CONFIRM-a', issue_id='ISSUE-a', decision='pending', target='vpn.alice.sales', impact='Disconnect session', expires_at=now + 2)], approvals=[], handoffs=[], actions=[], evidence=[], events=[], runs=[], unassigned_feedback=[])
        state['approvals'] = [dict(id='APPROVAL-a', issue_id='ISSUE-a', status='pending', resource_id='snowflake.prod.analytics', permission='read', expires_at=now + 2)]
        def route(r):
            path = r.request.url.split('test.local')[-1]
            if path == '/me':
                r.fulfill(json=dict(user=dict(id='alice.sales', name='Alice', roles=[]), csrf_token='fixture'))
            elif path == '/conversations':
                r.fulfill(json=[dict(id='CHAT-a', title='Clock regression', created_at=now)])
            elif path == '/conversations/CHAT-a':
                r.fulfill(json=state)
            else:
                target = ROOT / 'app/ui' / (path.removeprefix('/ui/') if path.startswith('/ui/') else 'index.html')
                r.fulfill(path=str(target))
        page.route('http://test.local/**', route)
        page.clock.install(time=datetime.fromtimestamp(now, timezone.utc))
        page.clock.pause_at(datetime.fromtimestamp(now, timezone.utc))
        page.goto('http://test.local/')
        page.select_option('#history', 'CHAT-a')
        expect(page.get_by_role('button', name='确认并允许中断')).to_be_visible()
        expect(page.get_by_role('button', name='取消该申请')).to_be_visible()
        page.clock.fast_forward(3100)
        expect(page.get_by_role('button', name='确认并允许中断')).to_have_count(0)
        expect(page.get_by_role('button', name='取消该申请')).to_have_count(0)
        expect(page.locator('#issues')).to_contain_text('已过期')
        state['unassigned_feedback'] = [dict(source_message_id='MSG-a', candidate_issue_ids=['ISSUE-a'])]
        page.clock.fast_forward(1600)
        expect(page.locator('#issues')).to_contain_text('反馈范围待澄清')
        expect(page.locator('#issues')).to_contain_text('上次验证：已确认解决')
        state['unassigned_feedback'] = []
        page.clock.fast_forward(1600)
        expect(page.locator('#issues')).not_to_contain_text('反馈范围待澄清')
        browser.close()
    print(json.dumps(dict(status='passed', mode='fixture_http_real_ui_controlled_clock', paid_calls=0, checks=['idle expiry removes authority controls', 'uncertainty labels last verified status', 'clarified feedback removes notice'])))


if __name__ == '__main__':
    main()
