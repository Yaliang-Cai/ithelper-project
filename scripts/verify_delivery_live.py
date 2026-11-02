"""Small PAID live HTTP/browser smoke; run from repository root.

Requires optional Playwright. Start normal app first. Uses the original shared
ledger, two fresh employee messages at most, no simulator/grader/oracle.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def ledger():
    from app.config import Settings
    with sqlite3.connect(f'file:{(ROOT / "runtime/usage.sqlite").as_posix()}?mode=ro', uri=True) as db:
        spent, reserved, count = db.execute('SELECT COALESCE(SUM(spent),0),COALESCE(SUM(CASE WHEN spent IS NULL THEN reserved ELSE 0 END),0),COUNT(*) FROM costs').fetchone()
    return dict(spent_usd=spent / 1e9, reserved_usd=reserved / 1e9, requests=count, limit_usd=Settings.load(ROOT/'.env').llm_budget_usd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://127.0.0.1:8000')
    parser.add_argument('--resume', help='Recover an already submitted smoke chat without resending')
    parser.add_argument('--budget-before', type=Path, help='Original pre-smoke safe ledger JSON for recovery')
    args = parser.parse_args()
    if not args.base.startswith('http://127.0.0.1:'):
        raise ValueError('Loopback URL required')
    current_ledger = ledger()
    before = json.loads(args.budget_before.read_text()) if args.budget_before else current_ledger
    if current_ledger['spent_usd'] + current_ledger['reserved_usd'] > 2.85:
        raise RuntimeError('Preserve remaining interactive demo funds')
    output = ROOT / 'runtime/delivery' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output.mkdir(parents=True)
    with sync_playwright() as pw:
        cached = sorted((Path(os.environ.get('LOCALAPPDATA', '')) / 'ms-playwright').glob('chromium-*/chrome-win64/chrome.exe'))
        executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or (str(cached[-1]) if cached else None)
        browser = pw.chromium.launch(**({'executable_path': executable} if executable else {}))
        context = browser.new_context(viewport={'width': 1365, 'height': 1000})
        page = context.new_page(); errors = []
        page.on('pageerror', lambda e: errors.append(type(e).__name__))
        page.goto(args.base)
        page.select_option('#username', 'alice.sales')
        page.get_by_role('button', name='登录', exact=True).click()
        expect(page.locator('#identity')).to_contain_text('alice.sales')
        if args.resume:
            page.select_option('#history', args.resume)
        else:
            page.get_by_role('button', name='新建聊天').click()
        page.wait_for_function("document.getElementById('history').value.startsWith('CHAT-')")
        cid = page.locator('#history').input_value()
        def wait_state():
            for _ in range(150):
                state = page.request.get(args.base + '/conversations/' + cid).json()
                if state['events'] and all(e['status'] not in ('queued', 'running') for e in state['events']):
                    expect(page.locator('#messages')).to_contain_text(state['messages'][-1]['content'])
                    return state
                time.sleep(1)
            raise RuntimeError('Smoke event still in progress; do not resubmit blindly')
        def submit(text):
            page.locator('#message').fill(text)
            page.get_by_role('button', name='发送', exact=True).click()
            expect(page.locator('#message')).to_have_value('')
            return wait_state()
        state = wait_state() if args.resume else submit('今天在公司电脑登录 Okta 时提示账号锁定。请帮我检查并解锁我自己的账号，再核对后台是否已经解除锁定。')
        if len(state['events']) == 1 and state['actions'] and state['runs'][-1]['status'] == 'completed':
            state = submit('我刚才重新试了，现在已经能够登录 Okta。')
        page.screenshot(path=str(output / 'employee-live.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(output / 'employee-mobile.png'), full_page=True)
        page.reload()
        page.select_option('#history', cid)
        expect(page.locator('#messages')).to_contain_text(state['messages'][-1]['content'])
        browser.close()
    after = ledger()
    evidence = dict(mode='live_agent_real_http_browser_developer_authored_fresh_inputs', simulator='none', grader='none', formal_benchmark=False,
        timestamp=datetime.now(timezone.utc).isoformat(), model='deepseek-flash', embedding_model='BAAI/bge-m3', actor='alice.sales',
        recovered_browser_driver=bool(args.resume),
        business_layer='real_program_logic_on_synthetic_enterprise_state', conversation_id=cid, state=state, browser_errors=errors,
        checks=['real login', 'fresh free-text input', 'persisted event/worker/Agent', 'actual state/receipt cards', '390px no overflow', 'reload own history'],
        budget_before=before, budget_after=after, request_delta=after['requests']-before['requests'], settled_estimate_delta_usd=round(after['spent_usd']-before['spent_usd'],9),
        runtime_hashes={p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / 'app').rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    (output / 'smoke.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    public = ROOT / 'docs/evidence'; public.mkdir(exist_ok=True)
    (public / 'delivery-smoke.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(output=str(output), conversation_id=cid, runs=[dict(status=r['status'], error_code=r['error_code'], mode=r['mode']) for r in state['runs']], actions=len(state['actions']), all_resolved=state['all_resolved'], budget_after=after, request_delta=evidence['request_delta'], cost=evidence['settled_estimate_delta_usd'])))


if __name__ == '__main__':
    main()
