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
