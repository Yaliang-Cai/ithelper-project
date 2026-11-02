"""One PAID real-model UI demo in a fresh business world, never a benchmark.

Run from the repository root with a Python containing Playwright. The server
uses .venv, the current application factory and the original project ledger.
No reset, replay of a model answer, grader, simulator, auth trace or session dump.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CEILING = 2.95


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def ledger_rows():
    with sqlite3.connect(f'file:{(ROOT / "runtime/usage.sqlite").as_posix()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute('SELECT * FROM costs ORDER BY created,id')]


def factory():
    """Explicitly injected provider; only this demo has a smaller spend margin."""
    from app.budget import BudgetLedger
    from app.config import PROJECT_LEDGER, Settings
    from app.main import create_app
    from app.providers import DeepSeekClient
    world = Path(os.environ['HELPDESK_RECORD_WORLD'])
    settings = replace(Settings.load(ROOT / '.env'), runtime_dir=world)
    ceiling = min(float(os.environ.get('HELPDESK_RECORD_CEILING', CEILING)), settings.llm_budget_usd)
    ledger = BudgetLedger(PROJECT_LEDGER, ceiling)

    class RecordedClient(DeepSeekClient):
        def complete(self, *args, **kwargs):
            kwargs['purpose'] = os.environ.get('HELPDESK_RECORD_PURPOSE_PREFIX', '') + kwargs['purpose']
            response = super().complete(*args, **kwargs)
            with (world / 'provider-metadata.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'purpose': kwargs['purpose'], 'returned_model': response.get('model'),
                                         'usage': response.get('usage')}) + '\n')
            return response

    return create_app(settings, provider=RecordedClient(settings, ledger))


def main():
    from playwright.sync_api import sync_playwright, expect
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New output directory; existing paths are refused')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    world = ROOT / 'runtime/recordings' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    world.mkdir(parents=True, exist_ok=False)
    before_rows = ledger_rows()
    from app.config import Settings
    limit = Settings.load(ROOT / '.env').llm_budget_usd
    before_ids = {row['id'] for row in before_rows}
    def budget(rows):
        return {'spent_usd': sum(r['spent'] or 0 for r in rows) / 1e9,
                'reserved_usd': sum(r['reserved'] for r in rows if r['spent'] is None) / 1e9,
                'requests': len(rows), 'limit_usd': limit}
    before = budget(before_rows)
    assert before['reserved_usd'] == 0 and before['spent_usd'] < CEILING, 'No new calls: demo margin unavailable'
    save(world / 'budget-before.json', before)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    log = (world / 'server.log').open('w', encoding='utf-8')
    server = subprocess.Popen([str(ROOT / '.venv/Scripts/python.exe'), '-m', 'uvicorn',
        'scripts.record_demo:factory', '--factory', '--host', '127.0.0.1', '--port', str(port)],
        cwd=ROOT, env={**os.environ, 'HELPDESK_RECORD_WORLD': str(world)}, stdout=log, stderr=log,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    state = None
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + '/health', timeout=1).close(); break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError('Demo server failed; inspect private server.log')
                time.sleep(.1)
        with sync_playwright() as pw:
            cached = sorted((Path(os.environ.get('LOCALAPPDATA', '')) / 'ms-playwright').glob('chromium-*/chrome-win64/chrome.exe'))
            executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or (str(cached[-1]) if cached else None)
            browser = pw.chromium.launch(**({'executable_path': executable} if executable else {}))
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, record_video_dir=str(world / 'video'),
                                          record_video_size={'width': 1440, 'height': 1000})
            page = context.new_page(); errors = []
            page.on('pageerror', lambda error: errors.append(type(error).__name__))
            page.goto(base)
            page.wait_for_timeout(1500)
            page.select_option('#username', 'alice.sales')
            page.get_by_role('button', name='登录', exact=True).click()
            expect(page.locator('#identity')).to_contain_text('alice.sales')
            page.get_by_role('button', name='新建聊天').click()
            expect(page.locator('#history')).to_have_value(re.compile('^CHAT-'))
            cid = page.locator('#history').input_value()
            save(world / 'resume.json', {'base': base, 'conversation_id': cid, 'server_pid': server.pid})

            def submit(text):
                page.locator('#message').fill(text)
                page.wait_for_timeout(1800)
                page.get_by_role('button', name='发送', exact=True).click()
                expect(page.locator('#message')).to_have_value('')
                for _ in range(150):
                    state = page.request.get(base + '/conversations/' + cid).json()
                    if state['events'] and all(e['status'] not in ('queued', 'running') for e in state['events']):
                        save(world / 'last-state.json', state)
                        expect(page.locator('#messages')).to_contain_text(state['messages'][-1]['content'])
                        return state
                    time.sleep(1)
                raise RuntimeError('Event still in progress; inspect saved chat rather than resending')

            state = submit('早上好，我在办公室用公司的电脑登录 Okta，页面说账号已锁定。麻烦帮我恢复自己的登录，我还要赶着处理客户订单。')
            page.wait_for_timeout(5000)
            # Developer-authored synthetic feedback, sent only after an actual
            # unlock receipt. This is not a real employee or real Okta login.
            if state['actions'] and all(r['status'] == 'completed' for r in state['runs']):
                state = submit('我按你说的重新登录了一次，现在能进去了，谢谢。')
                page.wait_for_timeout(5000)
            page.screenshot(path=str(output / 'employee-v7.png'), full_page=True)
            page.reload()
            page.select_option('#history', cid)
            expect(page.locator('#messages')).to_contain_text(state['messages'][-1]['content'])
            page.wait_for_timeout(2500)
            video = page.video
            context.close()
            shutil.copyfile(video.path(), output / 'real-model-v7.webm')
            browser.close()
        from app.storage import unpack
        with sqlite3.connect(world / 'business.sqlite') as db:
            db.row_factory = sqlite3.Row
            runs = [unpack(row) for row in db.execute('SELECT * FROM runs WHERE conversation_id=? ORDER BY created_at', (cid,))]
        after_rows = ledger_rows()
        new_rows = [row for row in after_rows if row['id'] not in before_ids]
        actual_versions = [r['prompt_version'] for r in runs]
        assert actual_versions and set(actual_versions) == {'helpdesk-2026-11-02-v7-matched-followup'}, actual_versions
        evidence = {'mode': 'live_agent_real_http_browser', 'formal_benchmark': False, 'simulator': 'none', 'grader': 'none',
            'inputs': 'fresh developer-authored employee input and synthetic recovery feedback',
            'business_systems': 'synthetic enterprise; real local auth/queue/Agent/effects/receipts',
            'recording': 'continuous original-speed Playwright capture; no answer replay or cuts; no audio',
            'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'model': 'deepseek-flash', 'embedding_model': 'BAAI/bge-m3',
            'runtime_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'runtime_hashes': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (ROOT / 'app').rglob('*') if p.is_file() and '__pycache__' not in p.parts},
            'actual_run_prompt_versions': actual_versions, 'state': state,
            'runs': [{k: r.get(k) for k in ('id', 'status', 'mode', 'prompt_version', 'error_code', 'model_calls', 'tool_calls', 'usage', 'elapsed_seconds')} for r in runs],
            'tool_trace': [t for r in runs for t in r.get('trace', []) if t.get('kind') == 'tool'],
            'provider_calls': [json.loads(line) for line in (world / 'provider-metadata.jsonl').read_text().splitlines()],
            'browser_errors': errors, 'checks': ['real login', 'fresh free text', 'durable worker', 'receipt cards', 'history reload', 'actual run prompt_version'],
            'budget_before': before, 'budget_after': budget(after_rows), 'new_ledger_rows': new_rows,
            'request_delta': len(new_rows), 'cost_usd': sum(r['spent'] or 0 for r in new_rows) / 1e9,
            'demo_reservation_ceiling_usd': CEILING}
        save(output / 'demo-v7.json', evidence)
        print(json.dumps({'output': str(output), 'private_world': str(world), 'budget_after': evidence['budget_after'],
                          'cost_usd': evidence['cost_usd'], 'requests': len(new_rows), 'all_resolved': state['all_resolved'],
                          'prompt_versions': actual_versions, 'browser_errors': errors}), flush=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill(); server.wait(timeout=5)
        log.close()


if __name__ == '__main__':
    main()
