from concurrent.futures import ThreadPoolExecutor
import json
import httpx
import pytest


def settings(**kwargs):
    from app.config import Settings
    return Settings(deepseek_api_key='test-secret', **kwargs)


def test_forbidden_fallback():
    with pytest.raises(ValueError):
        settings(deepseek_model='deepseek-chat')
    with pytest.raises(ValueError):
        settings(embedding_model='Pro/BAAI/bge-m3')


@pytest.mark.parametrize('status,category',[(402,'insufficient_balance'),(401,'authentication'),(429,'rate_limit'),(503,'upstream')])
def test_safe_http_categories_and_bounded_timeout(tmp_path,status,category):
    from app.providers import ProviderError
    def handler(request):
        assert request.extensions['timeout']['read']<=1.5
        return httpx.Response(status,json={'error':'test-secret'})
    c,ledger=client(tmp_path,handler)
    with pytest.raises(ProviderError) as error:
        c.complete([{'role':'user','content':'hi'}],purpose='agent',timeout_seconds=1.5)
    assert error.value.category==category and error.value.http_status==status
    assert 'test-secret' not in str(error.value) and ledger.report()['reserved_usd']>0


def test_total_timeout_cancels_delayed_transport_and_retains_charge(tmp_path):
    import asyncio
    import time
    from app.providers import ProviderError
    async def delayed(request):
        await asyncio.sleep(1)
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'late'}}]})
    c,ledger=client(tmp_path,delayed)
    start=time.monotonic()
    with pytest.raises(ProviderError) as error:
        c.complete([{'role':'user','content':'hello'}],purpose='agent',timeout_seconds=.02)
    assert error.value.category=='timeout' and time.monotonic()-start<.5
    assert ledger.report()['reserved_usd']>0 and ledger.report()['spent_usd']==0


def test_shared_request_deadline_bounds_multiple_embedding_requests():
    import asyncio
    import time
    from app.providers import EmbeddingClient, ProviderError,request_deadline
    async def delayed(request):
        payload=json.loads(request.content)
        await asyncio.sleep(.01 if payload['input']==['first'] else 1)
        return httpx.Response(200,json={'data':[{'index':0,'embedding':[1.0]*1024}]})
    embedding=EmbeddingClient(settings(siliconflow_api_key='test-secret'),transport=httpx.MockTransport(delayed))
    started=time.monotonic()
    with request_deadline(.1):
        assert len(embedding.embed(['first'])[0])==1024
        with pytest.raises(ProviderError) as error:embedding.embed(['second'])
    assert error.value.category=='timeout' and time.monotonic()-started<.5


def test_restart_and_concurrency(tmp_path):
    from app.budget import BudgetLedger, BudgetExceeded
    path = tmp_path / 'usage.sqlite'
    ledger = BudgetLedger(path, .01)
    r = ledger.reserve(.009, purpose='agent')
    ledger.settle(r, .008)
    reopened = BudgetLedger(path, .01)
    with pytest.raises(BudgetExceeded):
        reopened.reserve(.003, purpose='grader')
    def reserve(_):
        try:
            BudgetLedger(path, .01).reserve(.0015, purpose='agent')
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(8))) == 1


def client(tmp_path, handler):
    from app.providers import DeepSeekClient
    from app.budget import BudgetLedger
    ledger = BudgetLedger(tmp_path / 'usage.sqlite', 1)
    return DeepSeekClient(settings(), ledger, transport=httpx.MockTransport(handler)), ledger


def test_protocol_and_usage(tmp_path):
    messages = [{'role':'assistant','content':None,'reasoning_content':'reason','tool_calls':[{'id':'x','type':'function','function':{'name':'ping','arguments':'{}'}}]}, {'role':'tool','tool_call_id':'x','content':'ok'}]
    message = {'role':'assistant','content':'ok','reasoning_content':'done'}
    def handler(request):
        body = json.loads(request.content)
        assert body['messages'] == messages
        assert body['model'] == 'deepseek-flash'
        assert body['thinking'] == {'type':'enabled'}
        return httpx.Response(200,json={'model':'deepseek-flash','choices':[{'message':message}], 'usage':{'prompt_tokens':100,'completion_tokens':10}})
    c, ledger = client(tmp_path, handler)
    assert c.complete(messages,purpose='agent',thinking=True)['choices'][0]['message'] == message
    assert ledger.report()['spent_usd'] == pytest.approx(.000042)
    assert ledger.report()['reserved_usd'] == 0


@pytest.mark.parametrize('kind',['timeout','missing_usage','invalid'])
def test_uncertain_retains_reservation_and_safe_errors(tmp_path,kind):
    from app.providers import ProviderError
    def handler(request):
        if kind == 'timeout':
            raise httpx.ReadTimeout('test-secret',request=request)
        return httpx.Response(200,json={} if kind == 'invalid' else {'choices':[{'message':{'role':'assistant','content':'ok'}}]})
    c, ledger = client(tmp_path,handler)
    if kind == 'missing_usage':
        c.complete([{'role':'user','content':'hi'}],purpose='agent')
    else:
        with pytest.raises(ProviderError) as e:
            c.complete([{'role':'user','content':'hi'}],purpose='agent')
        assert 'test-secret' not in str(e.value)
        assert e.value.__cause__ is None
    assert ledger.report()['reserved_usd'] > 0


def test_embedding_disabled_and_invalid():
    from app.providers import EmbeddingClient, EmbeddingDisabled, ProviderError
    with pytest.raises(EmbeddingDisabled):
        EmbeddingClient(settings()).embed(['hello'])
    for data in [[], [{'index':0,'embedding':[]}], [{'index':0,'embedding':[float('nan')]}]]:
        transport = httpx.MockTransport(lambda request: httpx.Response(200,json={'data':data}) if data != [{'index':0,'embedding':[float('nan')]}] else httpx.Response(200,text='{"data":[{"index":0,"embedding":[NaN]}]}'))
        with pytest.raises(ProviderError):
            EmbeddingClient(settings(siliconflow_api_key='test'),transport=transport).embed(['hello'])

def test_embedding_order_and_dimensions():
    from app.providers import EmbeddingClient, ProviderError
    s = settings(siliconflow_api_key='test')
    good = httpx.MockTransport(lambda r: httpx.Response(200,json={'data':[{'index':1,'embedding':[3,4]},{'index':0,'embedding':[1,2]}]}))
    assert EmbeddingClient(s,transport=good).embed(['a','b']) == [[1.,2.],[3.,4.]]
    bad = httpx.MockTransport(lambda r: httpx.Response(200,json={'data':[{'index':0,'embedding':[1,2]},{'index':1,'embedding':[3]}]}))
    with pytest.raises(ProviderError):
        EmbeddingClient(s,transport=bad).embed(['a','b'])


def test_load_env_and_secret_repr(tmp_path,monkeypatch):
    from app.config import Settings
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    path = tmp_path / '.env'
    path.write_text('DEEPSEEK_API_KEY=hidden-example\nLLM_BUDGET_USD=0.5\n')
    s = Settings.load(path)
    assert s.deepseek_api_key == 'hidden-example'
    assert s.llm_budget_usd == .5
    assert 'hidden-example' not in repr(s)

def test_default_authorized_budget():
    from app.config import Settings
    assert Settings().llm_budget_usd == 3.0

def test_http_failure_never_retries(tmp_path):
    from app.providers import ProviderError
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(401,text='test-secret')
    c,ledger = client(tmp_path,handler)
    with pytest.raises(ProviderError) as error:
        c.complete([{'role':'user','content':'hello'}],purpose='agent')
    assert len(requests)==1
    assert 'test-secret' not in str(error.value)
    assert ledger.report()['reserved_usd'] > 0


def test_malformed_tool_call_is_rejected(tmp_path):
    from app.providers import ProviderError
    c,_ = client(tmp_path,lambda r:httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'x','type':'function','function':{'name':'ping','arguments':{}}}]}}]}))
    with pytest.raises(ProviderError):
        c.complete([{'role':'user','content':'hello'}],purpose='agent')

def test_settle_is_idempotent_and_keeps_actual_cost(tmp_path):
    from app.budget import BudgetLedger
    ledger = BudgetLedger(tmp_path/'usage.sqlite',2)
    r = ledger.reserve(.01,purpose='agent')
    ledger.settle(r,.005)
    ledger.settle(r,.005)
    with pytest.raises(ValueError):
        ledger.settle(r,.004)
    assert ledger.report()['spent_usd'] == .005


def test_connectivity_json_bounded_retry(monkeypatch):
    from scripts.check_connections import probe_json
    class Probe:
        def __init__(self):
            self.calls=0
        def complete(self,*args,**kwargs):
            self.calls+=1
            return {'choices':[{'message':{'content':'broken' if self.calls==1 else '{"ok":true}'}}]}
    probe=Probe()
    assert probe_json(probe) == ['failed','ok']
    assert probe.calls == 2

@pytest.mark.parametrize('limit',[3.000001,4])
def test_rejects_unauthorized_limit(tmp_path,limit):
    from app.config import Settings
    from app.budget import BudgetLedger
    with pytest.raises(ValueError):
        Settings(llm_budget_usd=limit)
    with pytest.raises(ValueError):
        BudgetLedger(tmp_path/'cap.sqlite',limit)


def test_overrun_is_recorded_and_blocks_cap(tmp_path):
    from app.budget import BudgetLedger, BudgetExceeded, BudgetOverrun
    path=tmp_path/'cap.sqlite'
    ledger=BudgetLedger(path,3)
    r=ledger.reserve(2.9,purpose='agent')
    with pytest.raises(BudgetOverrun):
        ledger.settle(r,3.1)
    reopened=BudgetLedger(path,3)
    assert reopened.report()['spent_usd']==3.1
    assert reopened.report()['accounting_overrun'] is True
    with pytest.raises(BudgetExceeded):
        reopened.reserve(.000001,purpose='agent')


def test_connections_closed_deterministically(tmp_path,monkeypatch):
    import sqlite3
    from app.budget import BudgetLedger
    original=sqlite3.connect
    opened=[]
    def connect(*args,**kwargs):
        connection=original(*args,**kwargs)
        opened.append(connection)
        return connection
    monkeypatch.setattr(sqlite3,'connect',connect)
    path=tmp_path/'closed.sqlite'
    ledger=BudgetLedger(path,3)
    r=ledger.reserve(.01,purpose='agent')
    ledger.settle(r,.005)
    ledger.report()
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')
    path.unlink()
    assert not path.exists()


@pytest.mark.parametrize('message',['invalid',None,0,[],{'role':'assistant','content':None,'tool_calls':'invalid'}])
def test_bad_message_shape_stays_inside_provider_boundary(tmp_path,message):
    from app.providers import ProviderError
    c,ledger=client(tmp_path,lambda r:httpx.Response(200,json={'choices':[{'message':message}]}))
    with pytest.raises(ProviderError):
        c.complete([{'role':'user','content':'hello'}],purpose='agent')
    assert ledger.report()['reserved_usd']>0


def test_reported_output_over_request_bound_is_charged_and_rejected(tmp_path):
    from app.providers import ProviderError
    c,ledger=client(tmp_path,lambda r:httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'ok'}}],'usage':{'prompt_tokens':1,'completion_tokens':100}}))
    with pytest.raises(ProviderError):
        c.complete([{'role':'user','content':'hello'}],purpose='agent',max_tokens=1)
    assert ledger.report()['spent_usd']==pytest.approx(.0001203)


def test_provider_excessive_usage_persists_overrun(tmp_path):
    from app.budget import BudgetOverrun
    c,ledger=client(tmp_path,lambda r:httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'ok'}}],'usage':{'prompt_tokens':20_000_000,'completion_tokens':1}}))
    with pytest.raises(BudgetOverrun):
        c.complete([{'role':'user','content':'hello'}],purpose='agent',max_tokens=1)
    assert ledger.report()['spent_usd']>6
    assert ledger.report()['accounting_overrun'] is True

def test_reservation_overrun_blocks_restart_even_below_cap(tmp_path):
    from app.budget import BudgetLedger, BudgetExceeded, BudgetOverrun
    path=tmp_path/'overrun.sqlite'
    ledger=BudgetLedger(path,3)
    r=ledger.reserve(.001,purpose='agent')
    with pytest.raises(BudgetOverrun):
        ledger.settle(r,.002)
    reopened=BudgetLedger(path,3)
    assert reopened.report()['spent_usd']==.002
    assert reopened.report()['accounting_overrun'] is True
    with pytest.raises(BudgetExceeded):
        reopened.reserve(.001,purpose='grader')


def test_smaller_authorized_limit_and_environment_ceiling(tmp_path):
    from app.config import Settings
    from app.budget import BudgetLedger
    assert Settings(llm_budget_usd=.5).llm_budget_usd==.5
    assert BudgetLedger(tmp_path/'small.sqlite',.5).report()['limit_usd']==.5
    env=tmp_path/'ceiling.env'
    env.write_text('LLM_BUDGET_USD=3.1')
    with pytest.raises(ValueError):
        Settings.load(env)


def test_ledger_boundary_rejects_above_authorized_limit(tmp_path):
    from app.budget import BudgetLedger
    with pytest.raises(ValueError):
        BudgetLedger(tmp_path/'cap.sqlite',3.01)
