"""Record an operator-driven real UI walkthrough using numbered JSON commands.

Start with --output runtime/<new-directory>. Write commands/001.json etc. with
op=login/new_chat/send/open_chat/approve/finish. Results and snapshots are saved
after each command. All UI actions are real; employee wording is supplied by the
operator after reading the actual answer. No prerecorded model responses.
Use --offline only to check the recorder against the labelled browser fixture.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.request
import uuid

from scripts.record_demo import ROOT, ledger_rows, save


def ensure_finished(db_path, read_rows):
    with sqlite3.connect(f'file:{Path(db_path).resolve().as_posix()}?mode=ro',uri=True) as db:
        if db.execute("SELECT 1 FROM events WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise RuntimeError('Recording has active events; wait before finishing')
    # Read charges after observing an idle worker, so a just-finished failed
    # request cannot leave an unsettled reservation outside an earlier snapshot.
    if any(r['spent'] is None for r in read_rows()):
        raise RuntimeError('Recording has unsettled charges; inspect the ledger before finishing')


def main():
    from playwright.sync_api import sync_playwright, expect
    from app.config import Settings
    from app.storage import unpack
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-cost', type=float, default=1.0)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    if not 0 < args.max_cost <= 1:
        parser.error('This recording permits at most USD1 of additional reservations')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    commands=output/'commands'; commands.mkdir()
    results=output/'results'; results.mkdir()
    world=output/'world'; world.mkdir()
    before=[] if args.offline else ledger_rows()
    before_ids={r['id'] for r in before}
    purpose_prefix='walkthrough:'+uuid.uuid4().hex+':'
    def recording_rows(rows):
        return [r for r in rows if r['id'] not in before_ids and r['purpose'].startswith(purpose_prefix)]
    start_spend=sum(r['spent'] or 0 for r in before)/1e9
    if any(r['spent'] is None for r in before):
        raise RuntimeError('Unsettled requests exist; inspect the original ledger first')
    limit=Settings.load(ROOT/'.env').llm_budget_usd if not args.offline else 0
    ceiling=min(limit,start_spend+args.max_cost)
    save(output/'budget-before.json',{'spent_usd':start_spend,'requests':len(before),'limit_usd':limit,'recording_ceiling_usd':ceiling})
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    factory='scripts.browser_fixture:factory' if args.offline else 'scripts.record_demo:factory'
    env={**os.environ,'HELPDESK_RECORD_WORLD':str(world),'HELPDESK_RECORD_CEILING':str(ceiling),'HELPDESK_RECORD_PURPOSE_PREFIX':purpose_prefix,'HELPDESK_BROWSER_WORLD':str(world)}
    log=(world/'server.log').open('w',encoding='utf-8')
    server=subprocess.Popen([str(ROOT/'.venv/Scripts/python.exe'),'-m','uvicorn',factory,'--factory','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    sessions={}; journal=[];errors=[]; started=time.monotonic()
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base+'/health',timeout=1).close();break
            except OSError:
                if server.poll() is not None:raise RuntimeError('Recording server failed')
                time.sleep(.1)
        with sync_playwright() as pw:
            cached=sorted((Path(os.environ.get('LOCALAPPDATA',''))/'ms-playwright').glob('chromium-*/chrome-win64/chrome.exe'))
            browser=pw.chromium.launch(**({'executable_path':str(cached[-1])} if cached else {}))
            context=browser.new_context(viewport={'width':1440,'height':1000},record_video_dir=str(output/'capture'),record_video_size={'width':1440,'height':1000},locale='zh-CN',timezone_id='Asia/Shanghai')
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base);cid=None;actor=None;processed=set()
            save(output/'ready.json',{'base':base,'server_pid':server.pid,'mode':'scripted_offline' if args.offline else 'live_agent','command_directory':str(commands)})
            print(json.dumps({'ready':str(output)},ensure_ascii=True),flush=True)

            def inspect_cards():
                opened=[]
                # Read the actual message first, then expand every available
                # issue ID, receipt and source group one at a time.
                page.locator('#messages').scroll_into_view_if_needed()
                page.wait_for_timeout(4500 if not args.offline else 100)
                for card in page.locator('#issues .card').all():
                    for detail in card.locator('details').all():
                        summary=detail.locator('summary').first
                        summary.scroll_into_view_if_needed()
                        if detail.get_attribute('open') is None:summary.click()
                        expect(detail).to_have_attribute('open','')
                        label=summary.inner_text();opened.append(label)
                        page.wait_for_timeout(2200 if not args.offline else 100)
                        summary.click()
                page.locator('#message').scroll_into_view_if_needed()
                return opened

            def settled(previous_events):
                for _ in range(180):
                    state=page.request.get(base+'/conversations/'+cid).json()
                    if len(state.get('events',[]))>previous_events and not any(e['status'] in ('queued','running') for e in state['events']):
                        expect(page.locator('#messages')).to_contain_text(state['messages'][-1]['content'])
                        sessions[cid]=state
                        return state
                    page.wait_for_timeout(750)
                raise RuntimeError('Event did not finish; do not resend the message')

            while True:
                pending=[p for p in sorted(commands.glob('*.json')) if p.name not in processed]
                if not pending:
                    page.wait_for_timeout(300);continue
                file=pending[0]
                try:command=json.loads(file.read_text(encoding='utf-8'))
                except (OSError,json.JSONDecodeError):
                    page.wait_for_timeout(300);continue
                processed.add(file.name);op=command['op'];entry={'command':command,'start_seconds':round(time.monotonic()-started,3)}
                try:
                    if op=='login':
                        if page.locator('#logout').is_visible():page.locator('#logout').click()
                        expect(page.locator('#login-panel')).to_be_visible()
                        page.select_option('#username',command['actor']);page.get_by_role('button',name='登录',exact=True).click()
                        expect(page.locator('#identity')).to_contain_text(command['actor']);actor=command['actor'];cid=None
                    elif op=='new_chat':
                        page.locator('#new-chat').click();expect(page.locator('#history')).to_have_value(__import__('re').compile('^CHAT-'))
                        cid=page.locator('#history').input_value()
                    elif op=='open_chat':
                        cid=command['conversation_id'];page.select_option('#history',cid)
                        page.wait_for_timeout(1800)
                        before_state=page.request.get(base+'/conversations/'+cid).json()
                        if any(e['status'] in ('queued','running') for e in before_state['events']):state=settled(-1)
                        else:state=before_state
                        sessions[cid]=state;entry['state']=state;entry['expanded']=inspect_cards()
                    elif op=='send':
                        if not cid:raise ValueError('Select a chat first')
                        previous=page.request.get(base+'/conversations/'+cid).json()
                        page.locator('#message').fill(command['text']);page.wait_for_timeout(1500)
                        page.get_by_role('button',name='发送',exact=True).click();expect(page.locator('#message')).to_have_value('')
                        entry['state']=settled(len(previous['events']));entry['expanded']=inspect_cards()
                    elif op=='approve':
                        page.get_by_role('link',name='资源审批',exact=True).click()
                        card=page.locator('#approvals .card').filter(has=page.get_by_role('heading',name=command['resource'],exact=True))
                        expect(card).to_have_count(1);card.scroll_into_view_if_needed();page.wait_for_timeout(3000)
                        card.get_by_role('button',name='批准只读申请',exact=True).click();expect(card).to_contain_text('已批准')
                    elif op=='finish':
                        ensure_finished(world/('browser.sqlite' if args.offline else 'business.sqlite'),lambda: [] if args.offline else recording_rows(ledger_rows()))
                        entry['completed']=True
                    else:raise ValueError('Unknown recorder command')
                    entry.update(actor=actor,conversation_id=cid,end_seconds=round(time.monotonic()-started,3))
                except Exception as exc:
                    entry.update(error=type(exc).__name__+': '+str(exc),end_seconds=round(time.monotonic()-started,3))
                journal.append(entry);save(results/file.name,entry);save(output/'journal.json',journal)
                page.screenshot(path=str(output/'latest.png'))
                print(json.dumps({'command':file.name,'op':op,'conversation_id':cid,'error':entry.get('error'),'result_file':str(results/file.name)},ensure_ascii=True),flush=True)
                if entry.get('completed'):break
            video=page.video;context.close();shutil.copyfile(video.path(),output/'walkthrough.webm');browser.close()
        db_path=world/('browser.sqlite' if args.offline else 'business.sqlite')
        with sqlite3.connect(db_path) as db:
            db.row_factory=sqlite3.Row
            runs=[unpack(r) for r in db.execute('SELECT * FROM runs ORDER BY created_at')]
        after=[] if args.offline else ledger_rows();new=recording_rows(after)
        evidence={'mode':'scripted_model_offline_check' if args.offline else 'live_agent_real_http_browser','formal_benchmark':False,'inputs':'operator-authored synthetic employee messages, adapted to actual replies','model':'deepseek-flash' if not args.offline else 'fixture','timestamp_utc':datetime.now(timezone.utc).isoformat(),'runtime_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'runtime_hashes':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'app').rglob('*') if p.is_file() and '__pycache__' not in p.parts},'sessions':sessions,'journal':journal,'runs':[{k:r.get(k) for k in ('id','conversation_id','status','mode','prompt_version','model_calls','tool_calls','elapsed_seconds','result')} for r in runs],'tool_trace':[t for r in runs for t in r.get('trace',[]) if t.get('kind')=='tool'],'new_ledger_rows':new,'request_delta':len(new),'cost_usd':sum(r['spent'] or 0 for r in new)/1e9,'cumulative_spent_usd':sum(r['spent'] or 0 for r in after)/1e9,'limit_usd':limit,'recording_ceiling_usd':ceiling,'browser_errors':errors,'recording':'continuous original-speed screen capture; synthetic business and feedback; no model-answer replay'}
        evidence['reserved_usd']=sum(r['reserved'] for r in new if r['spent'] is None)/1e9
        evidence['ledger_purpose_prefix']=purpose_prefix
        save(output/'walkthrough.json',evidence)
        print(json.dumps({'finished':str(output),'cost_usd':evidence['cost_usd'],'requests':len(new),'browser_errors':errors}),flush=True)
    finally:
        server.terminate()
        try:server.wait(timeout=5)
        except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
        log.close()


if __name__=='__main__':main()
