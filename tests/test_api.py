import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain import Domain
from app.storage import Store
from app.tools import ToolExecutor


class Scripted:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        if kwargs.get('response_format'):
            content = json.dumps({k: [] for k in ('message_intents', 'new_issue_candidates', 'observations', 'corrections', 'relation_candidates', 'unresolved_references')})
            message = {'role': 'assistant', 'content': content}
        else:
            turn = dict(reply='已记录，请补充问题。', issue_updates=[], questions=[], question_updates=[], hypotheses=[], guidance=[], refinements=[])
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'done', 'type': 'function', 'function': {'name': 'submit_turn', 'arguments': json.dumps(turn)}}]}
        return {'choices': [{'message': message}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}


@pytest.fixture
def env(tmp_path):
    from app.main import create_app
    d = Domain(Store(tmp_path / 'world.sqlite').initialize())
    provider = Scripted()
    app = create_app(Settings(runtime_dir=tmp_path, deepseek_api_key='never-expose-key'), domain=d, provider=provider, provider_mode='scripted_agent', start_worker=False)
    with TestClient(app) as client:
        yield client, d, app, provider


def login(c, username='alice.sales'):
    result = c.post('/auth/login', json={'username': username, 'password': 'DemoPass123!'})
    assert result.status_code == 200, result.text
    return {'X-CSRF-Token': result.json()['csrf_token']}


def chat(c, headers):
    result = c.post('/conversations', json={'title': 'Help'}, headers=headers)
    assert result.status_code == 201
    return result.json()['id']


def test_session_rotation_csrf_logout_and_secret_static_scope(env):
    c, d, app, provider = env
    assert c.get('/me').status_code == 401
    assert c.post('/auth/login', json={'username': 'alice.sales', 'password': 'wrong'}).status_code == 401
    h = login(c)
    old = c.cookies.get('helpdesk_session')
    assert 'HttpOnly' in c.post('/auth/login', json={'username': 'alice.sales', 'password': 'DemoPass123!'}).headers['set-cookie']
    assert old != c.cookies.get('helpdesk_session')
    with TestClient(app) as other:
        other.cookies.set('helpdesk_session', old)
        assert other.get('/me').status_code == 401
    h = {'X-CSRF-Token': c.get('/me').json()['csrf_token']}
    assert c.post('/conversations', json={}).status_code == 403
    assert c.post('/conversations', json={}, headers={'X-CSRF-Token': 'bad'}).status_code == 403
    cid = chat(c, h)
    body = c.get('/conversations/' + cid).text
    assert 'never-expose-key' not in body and 'session_token' not in body
    for path in ('/.env', '/runtime/usage.sqlite', '/ui/../.env', '/ui/%2e%2e/.env', '/ui/app/main.py'):
        r = c.get(path)
        assert r.status_code in (404, 400)
        assert 'never-expose-key' not in r.text
    assert c.post('/auth/logout', headers=h).status_code == 200
    assert c.get('/me').status_code == 401


def test_atomic_submission_retry_polling_owner_and_role_isolation(env):
    c, d, app, provider = env
    h = login(c); cid = chat(c, h)
    payload = {'content': '<img src=x onerror=alert(1)> VPN 问题', 'idempotency_key': 'stable-1'}
    first = c.post(f'/conversations/{cid}/messages', json=payload, headers=h)
    assert first.status_code == 202
    eid = first.json()['event_id']
    assert provider.calls == 0
    assert c.post(f'/conversations/{cid}/messages', json=payload, headers=h).json()['event_id'] == eid
    assert c.post(f'/conversations/{cid}/messages', json={**payload, 'content': 'different'}, headers=h).status_code == 409
    assert c.get(f'/runs/{eid}/events').json()['event']['status'] == 'queued'
    assert app.state.worker.process_once()
    poll = c.get(f'/runs/{eid}/events').json()
    assert poll['event']['status'] == 'completed' and poll['run']['status'] == 'completed'
    assert poll['run']['mode'] == 'scripted_agent'
    assert len(c.get(f'/conversations/{cid}').json()['messages']) == 2
    assert not app.state.worker.process_once() and provider.calls == 2
    for route in ('/approvals', '/approver', '/debug', '/debug/runs/' + poll['run']['id']):
        assert c.get(route).status_code == 403
    login(c, 'bo.remote')
    assert c.get('/conversations/' + cid).status_code == 403
    assert c.get(f'/runs/{eid}/events').status_code == 403
    assert c.post(f'/conversations/{cid}/messages', json=payload, headers=login(c, 'bo.remote')).status_code == 403
    login(c, 'dev.debug')
    debug = c.get('/debug/runs/' + poll['run']['id'])
    assert debug.status_code == 200
    assert 'never-expose-key' not in debug.text and 'reasoning_content' not in debug.text


def test_scope_bound_approval_duplicate_original_context_cancel_and_logout(env):
    c, d, app, provider = env
    h = login(c); cid = chat(c, h)
    issue = d.create_issue('alice.sales', cid, 'Production read', ['snowflake'], 'precise_access', 'snowflake.prod.analytics')
    tools = ToolExecutor(d)
    a = tools.call('request_approval', dict(issue_id=issue['id'], resource_id='snowflake.prod.analytics', permission='read', reason='report', evidence_refs=[]), 'alice.sales', cid)['data']['approval']
    assert c.post(f"/approvals/{a['id']}/decision", json={'decision': 'approved'}, headers=h).status_code == 403
    pat = login(c, 'pat.approver')
    assert c.get('/approvals').json()[0]['resource_id'] == 'snowflake.prod.analytics'
    r = c.post(f"/approvals/{a['id']}/decision", json={'decision': 'approved'}, headers=pat)
    assert r.status_code == 200
    assert c.post(f"/approvals/{a['id']}/decision", json={'decision': 'approved'}, headers=pat).status_code == 200
    events = d.list_events('alice.sales', cid)
    assert len(events) == 1 and events[0]['actor_id'] == 'alice.sales' and events[0]['issue_id'] == issue['id']
    h = login(c)
    assert c.post('/auth/logout', headers=h).status_code == 200
    assert d.list_approvals('alice.sales')[0]['status'] == 'approved'
    h = login(c)
    assert c.post(f"/approvals/{a['id']}/decision", json={'decision': 'cancelled'}, headers=h).status_code == 200
    result = tools.call('grant_catalog_access', dict(issue_id=issue['id'], resource_id='snowflake.prod.analytics', permission='read', approval_id=a['id']), 'alice.sales', cid)
    assert not result['ok']
    assert app.state.worker.process_once()
    assert app.state.worker.process_once()
    assert all(e['status'] == 'completed' for e in d.list_events('alice.sales', cid))
    assert not d.snapshot('alice.sales', cid)['actions']


def test_confirmation_denial_exact_owner_and_no_reset(env):
    c, d, app, provider = env
    h = login(c); cid = chat(c, h)
    issue = d.create_issue('alice.sales', cid, 'VPN', ['vpn'], 'vpn_recovery')
    tools = ToolExecutor(d)
    conf = tools.call('reset_vpn_session', {'issue_id': issue['id'], 'session_id': 'vpn.alice.sales'}, 'alice.sales', cid)['data']['confirmation']
    other = login(c, 'bo.remote')
    assert c.post(f"/confirmations/{conf['id']}/decision", json={'decision': 'denied'}, headers=other).status_code == 403
    h = login(c)
    for _ in range(2):
        assert c.post(f"/confirmations/{conf['id']}/decision", json={'decision': 'denied'}, headers=h).status_code == 200
    assert len(d.list_events('alice.sales', cid)) == 1
    assert not tools.call('reset_vpn_session', {'issue_id': issue['id'], 'session_id': 'vpn.alice.sales', 'confirmation_id': conf['id']}, 'alice.sales', cid)['ok']
    assert tools.call('inspect_vpn', {}, 'alice.sales', cid)['data']['reset_count'] == 0


def test_worker_heartbeat_survives_blocking_call_and_restart_recovers(env):
    from app.worker import Worker
    c, d, app, provider = env
    cid = d.create_conversation('alice.sales')['id']
    d.submit_user_message('alice.sales', cid, 'help', 'heartbeat')
    entered = threading.Event(); release = threading.Event()
    original = provider.complete
    def blocked(*args, **kwargs):
        entered.set(); release.wait(4)
        return original(*args, **kwargs)
    provider.complete = blocked
    w = Worker(d, app.state.agent, lease_seconds=1, heartbeat_seconds=.1)
    thread = threading.Thread(target=w.process_once); thread.start()
    try:
        assert entered.wait(2)
        time.sleep(1.2)
        assert d.claim_event('competing-worker', lease_seconds=1) is None
    finally:
        release.set(); thread.join(5)
    assert not thread.is_alive()
    assert d.list_events('alice.sales', cid)[0]['status'] == 'completed'
    # Simulate process death: durable claim expires, a new Worker uses Agent recovery.
    d.submit_user_message('alice.sales', cid, 'again', 'restart')
    abandoned = d.claim_event('dead-process', lease_seconds=1)
    assert abandoned
    time.sleep(1.05)
    assert Worker(d, app.state.agent).process_once()
    assert d.list_events('alice.sales', cid)[-1]['status'] == 'completed'


def test_http_queue_failure_returns_safe_unavailable_and_rolls_back(env):
    c, d, app, provider = env
    h = login(c); cid = chat(c, h)
    with d.store.write() as connection:
        connection.execute("CREATE TRIGGER queue_down BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'never-expose-key storage detail'); END")
    with TestClient(app, raise_server_exceptions=False) as http:
        http.cookies.update(c.cookies)
        result = http.post(f'/conversations/{cid}/messages', json={'content': 'help', 'idempotency_key': 'failure'}, headers=h)
    assert result.status_code == 503
    assert result.json()['error']['code'] == 'STORAGE_UNAVAILABLE'
    assert 'never-expose-key' not in result.text
    assert not d.snapshot('alice.sales', cid)['messages']
    assert not d.list_events('alice.sales', cid)


def test_secure_cookie_origin_validation_strict_body_and_safe_snapshot(env):
    c, d, app, provider = env
    with TestClient(app, base_url='https://testserver') as https:
        r = https.post('/auth/login', json={'username': 'alice.sales', 'password': 'DemoPass123!'})
        assert 'Secure' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
    assert c.post('/auth/login', json={'username': 'alice.sales', 'password': 'DemoPass123!'}, headers={'Origin': 'https://attacker.example'}).status_code == 403
    h = login(c); cid = chat(c, h)
    assert c.post(f'/conversations/{cid}/messages', json={'content': 'hello', 'idempotency_key': 'a', 'actor_id': 'bo.remote'}, headers=h).status_code == 422
    assert c.post('/conversations', json={}, headers={**h, 'Origin': 'https://attacker.example'}).status_code == 403
    d.submit_user_message('alice.sales', cid, 'safe', 'a')
    assert app.state.worker.process_once()
    snapshot = c.get('/conversations/' + cid)
    assert 'trace' not in snapshot.text and 'contexts' not in snapshot.text and 'usage' not in snapshot.text


def test_failed_run_retains_committed_action_and_worker_recovery_never_replays(env):
    from app.providers import ProviderError
    from app.worker import Worker
    c, d, app, provider = env
    h = login(c); cid = chat(c, h)
    issue = d.create_issue('alice.sales', cid, 'Grafana', ['grafana'], 'precise_access', 'grafana.sales')
    original = provider.complete
    def partial(messages, **kwargs):
        if kwargs.get('response_format'):
            return original(messages, **kwargs)
        if not d.snapshot('alice.sales', cid)['actions']:
            call = {'id': 'grant', 'type': 'function', 'function': {'name': 'grant_catalog_access', 'arguments': json.dumps(dict(issue_id=issue['id'], resource_id='grafana.sales', permission='read'))}}
            return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [call]}}]}
        raise ProviderError(category='unavailable')
    provider.complete = partial
    eid = c.post(f'/conversations/{cid}/messages', json={'content': 'please grant', 'idempotency_key': 'partial'}, headers=h).json()['event_id']
    original_finish = d.finish_event
    def finish_down(*args, **kwargs):
        raise sqlite3.OperationalError('injected terminal persistence outage')
    d.finish_event = finish_down
    worker = app.state.worker
    worker.lease_seconds = 1
    worker.heartbeat_seconds = .1
    assert not worker.process_once()
    state = c.get('/conversations/' + cid).json()
    assert state['runs'][-1]['status'] == 'failed' and len(state['actions']) == 1
    assert state['issues'][0]['status'] == 'verifying'
    assert c.get('/health').json()['status'] == 'recovering'
    assert state['processing']['recovery_pending'] is True
    d.finish_event = original_finish
    calls_before = provider.calls
    time.sleep(1.05)
    assert Worker(d, app.state.agent).process_once()
    assert provider.calls == calls_before
    assert c.get(f'/runs/{eid}/events').json()['event']['status'] == 'failed'
    assert len(d.snapshot('alice.sales', cid)['actions']) == 1


def test_concurrent_http_cancel_and_approve_cannot_leave_reusable_authority(env):
    c, d, app, provider = env
    h = login(c); cid = chat(c, h)
    issue = d.create_issue('alice.sales', cid, 'Production', ['snowflake'], 'precise_access', 'snowflake.prod.analytics')
    tools = ToolExecutor(d)
    approval = tools.call('request_approval', dict(issue_id=issue['id'], resource_id='snowflake.prod.analytics', permission='read', reason='race', evidence_refs=[]), 'alice.sales', cid)['data']['approval']
    with TestClient(app) as pat:
        ph = login(pat, 'pat.approver')
        barrier = threading.Barrier(2)
        def decide(client, headers, decision):
            barrier.wait()
            return client.post(f"/approvals/{approval['id']}/decision", json={'decision': decision}, headers=headers)
        with ThreadPoolExecutor(max_workers=2) as pool:
            approved = pool.submit(decide, pat, ph, 'approved')
            cancelled = pool.submit(decide, c, h, 'cancelled')
            assert cancelled.result().status_code == 200
            assert approved.result().status_code in (200, 409)
    assert d.list_approvals('alice.sales')[0]['status'] == 'cancelled'
    result = tools.call('grant_catalog_access', dict(issue_id=issue['id'], resource_id='snowflake.prod.analytics', permission='read', approval_id=approval['id']), 'alice.sales', cid)
    assert not result['ok']


def test_production_provider_always_uses_original_project_ledger(tmp_path, monkeypatch):
    import app.main as main
    paths = []
    monkeypatch.setattr(main, 'BudgetLedger', lambda path, limit: paths.append(Path(path)) or object())
    app = main.create_app(Settings(runtime_dir=tmp_path), start_worker=False)
    assert paths == [Path(__file__).resolve().parents[1] / 'runtime' / 'usage.sqlite']
    assert app.state.domain.store.db_path == (tmp_path / 'business.sqlite').resolve()


def test_worker_recovery_notice_survives_another_conversation_completing(env):
    c, d, app, provider = env
    h = login(c); first = chat(c, h); second = chat(c, h)
    failed = d.submit_user_message('alice.sales', first, 'first', 'first')['event']
    d.submit_user_message('alice.sales', second, 'second', 'second')
    original = d.finish_event
    def first_finish_fails(eid, *args, **kwargs):
        if eid == failed['id']:
            raise sqlite3.OperationalError('finish interrupted')
        return original(eid, *args, **kwargs)
    d.finish_event = first_finish_fails
    assert not app.state.worker.process_once()
    assert app.state.worker.process_once()
    assert c.get('/conversations/' + first).json()['processing']['recovery_pending']
    assert not c.get('/conversations/' + second).json()['processing']['recovery_pending']
    assert c.get('/health').json()['status'] == 'recovering'


def test_factory_rejects_unbounded_or_http_controlled_provider_modes(tmp_path):
    from app.main import create_app
    with pytest.raises(ValueError, match='run mode'):
        create_app(Settings(runtime_dir=tmp_path), provider=Scripted(), provider_mode='invented-success', start_worker=False)


def completion(name, args):
    return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'scope-test', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}}]}


def empty_turn(**changes):
    return dict(reply='Actual status remains in the cards.', issue_updates=[], questions=[], question_updates=[], hypotheses=[], guidance=[], refinements=[], **changes)


@pytest.mark.parametrize('kind', ['approval_decision', 'confirmation_decision'])
@pytest.mark.parametrize('mutation', ['action', 'handoff', 'refine', 'status', 'hypothesis', 'question', 'question_update', 'guidance', 'relation'])
def test_issue_bound_resume_rejects_other_issue_mutations(env, kind, mutation):
    c, d, app, provider = env
    cid = d.create_conversation('alice.sales')['id']
    target = d.create_issue('alice.sales', cid, 'VPN', ['vpn'], 'vpn_recovery')
    other = d.create_issue('alice.sales', cid, 'Grafana', ['grafana'], 'precise_access', 'grafana.sales')
    question = d.save_questions('alice.sales', cid, [{'text': 'Other issue?', 'issue_refs': [other['id']], 'question_intent': 'general'}])[0]
    message = d.append_message('alice.sales', cid, 'user', 'a later answer', 'answer')
    evidence = d.persist_evidence('alice.sales', cid, 'directory', 'scope-test', '1', {'fact': 'fixture'}, {})
    proposal = {k: [] for k in ('message_intents', 'new_issue_candidates', 'observations', 'corrections', 'relation_candidates', 'unresolved_references')}
    proposal['relation_candidates'] = [{'candidate_key': 'r', 'from_ref': target['id'], 'to_ref': other['id'], 'type': 'possible_shared_cause', 'support_refs': [evidence['id']]}]
    d.apply_candidates('alice.sales', cid, message['id'], proposal, d.snapshot('alice.sales', cid)['conversation']['version'])
    state = d.snapshot('alice.sales', cid)
    relation = state['relations'][0]
    name, args = 'submit_turn', empty_turn()
    if mutation == 'action':
        name, args = 'grant_catalog_access', dict(issue_id=other['id'], resource_id='grafana.sales', permission='read')
    elif mutation == 'handoff':
        name, args = 'create_handoff', dict(issue_refs=[target['id'], other['id']], summary='scope leak', facts=[], hypotheses=[], evidence_refs=[], attempt_refs=[], open_questions=[], team_hint='general_it', reason='user_request')
    elif mutation == 'refine':
        name, args = 'refine_issue', dict(issue_id=other['id'], expected_version=other['version'], service_refs=['grafana'])
    elif mutation == 'status':
        args['issue_updates'] = [dict(issue_id=other['id'], expected_version=other['version'], requested_status='waiting_user', criterion_id='precise_access', evidence_refs=[])]
    elif mutation == 'hypothesis':
        args['hypotheses'] = [dict(issue_id=other['id'], claim='Unrelated cause', status='tentative', support_refs=[], contradict_refs=[])]
    elif mutation == 'question':
        args['questions'] = [dict(text='unrelated question', issue_refs=[other['id']], question_intent='general')]
    elif mutation == 'question_update':
        args['question_updates'] = [dict(question_id=question['id'], status='answered', answer_message_id=message['id'])]
    elif mutation == 'guidance':
        args['guidance'] = [dict(issue_id=other['id'], remedy_id='unrelated', normalized_parameters={}, evidence_refs=[])]
    elif mutation == 'relation':
        args['relation_updates'] = [dict(relation_id=relation['id'], expected_version=relation['version'], status='rejected', support_refs=[], contradict_refs=[evidence['id']])]
    event = d.enqueue_event('alice.sales', cid, kind, {'decision': 'approved'}, 'scoped:' + mutation, issue_id=target['id'])
    calls = []
    def scripted(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return completion(name, args)
        assert json.loads(messages[-1]['content'])['error']['code'] == 'EVENT_SCOPE'
        return completion('submit_turn', empty_turn())
    provider.complete = scripted
    assert app.state.worker.process_once()
    after = d.snapshot('alice.sales', cid)
    run = after['runs'][-1]
    assert run['status'] == 'completed'
    assert after['issues'] == state['issues'] and after['questions'] == state['questions']
    assert after['relations'] == state['relations'] and not after['actions'] and not after['hypotheses']


@pytest.mark.parametrize('issue_id', [None, 'ISSUE-missing'])
def test_issue_bound_resume_without_valid_persisted_scope_fails_closed(env, issue_id):
    c, d, app, provider = env
    cid = d.create_conversation('alice.sales')['id']
    e = d.enqueue_event('alice.sales', cid, 'approval_decision', {}, 'missing-scope')
    if issue_id:
        with d.store.write() as connection:
            connection.execute('UPDATE events SET issue_id=? WHERE id=?', (issue_id, e['id']))
    assert app.state.worker.process_once()
    state = d.snapshot('alice.sales', cid)
    assert state['runs'][-1]['error_code'] == 'EVENT_SCOPE'
    assert provider.calls == 0 and not state['actions']


@pytest.mark.parametrize('kind', ['approval_decision', 'confirmation_decision'])
def test_scoped_resume_executes_only_original_issue_with_actual_authority(env, kind):
    c, d, app, provider = env
    cid = d.create_conversation('alice.sales')['id']
    tools = ToolExecutor(d)
    if kind == 'approval_decision':
        issue = d.create_issue('alice.sales', cid, 'Production', ['snowflake'], 'precise_access', 'snowflake.prod.analytics')
        approval = tools.call('request_approval', dict(issue_id=issue['id'], resource_id='snowflake.prod.analytics', permission='read', reason='normal', evidence_refs=[]), 'alice.sales', cid)['data']['approval']
        d.decide_approval('pat.approver', approval['id'], 'approved')
        name, args = 'grant_catalog_access', dict(issue_id=issue['id'], resource_id='snowflake.prod.analytics', permission='read', approval_id=approval['id'])
    else:
        issue = d.create_issue('alice.sales', cid, 'VPN', ['vpn'], 'vpn_recovery')
        confirmation = tools.call('reset_vpn_session', dict(issue_id=issue['id'], session_id='vpn.alice.sales'), 'alice.sales', cid)['data']['confirmation']
        d.decide_confirmation('alice.sales', confirmation['id'], 'confirmed')
        name, args = 'reset_vpn_session', dict(issue_id=issue['id'], session_id='vpn.alice.sales', confirmation_id=confirmation['id'])
    unrelated = d.create_issue('alice.sales', cid, 'Other', ['grafana'])
    requests = []
    def scripted(messages, **kwargs):
        requests.append(messages)
        return completion(name, args) if len(requests) == 1 else completion('submit_turn', empty_turn())
    provider.complete = scripted
    assert app.state.worker.process_once()
    state = d.snapshot('alice.sales', cid)
    assert state['runs'][-1]['status'] == 'completed'
    assert len(state['actions']) == 1 and state['actions'][0]['issue_id'] == issue['id']
    assert next(i for i in state['issues'] if i['id'] == unrelated['id']) == unrelated
