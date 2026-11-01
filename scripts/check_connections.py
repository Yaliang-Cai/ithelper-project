"""Run bounded, metered connectivity probes; write only safe metadata."""
import json
from datetime import datetime, timezone
from app.config import Settings, PROJECT_ROOT, PROJECT_LEDGER
from app.budget import BudgetLedger, BudgetExceeded
from app.providers import DeepSeekClient, EmbeddingClient, ProviderError


def probe_json(llm):
    outcomes = []
    for attempt in range(2):
        try:
            completion = llm.complete([{'role':'user','content':'Return JSON only: {"ok":true}'}],purpose='connectivity_json' if attempt == 0 else 'connectivity_json_retry',response_format={'type':'json_object'},max_tokens=128)
            valid = json.loads(completion['choices'][0]['message']['content']).get('ok') is True
            outcomes.append('ok' if valid else 'failed')
        except (ProviderError,ValueError,KeyError,TypeError,AttributeError):
            outcomes.append('failed')
        except BudgetExceeded:
            outcomes.append('budget_exhausted')
            break
        if outcomes[-1] == 'ok':
            break
    return outcomes


def main():
    settings = Settings.load(PROJECT_ROOT / '.env')
    output_dir = PROJECT_ROOT / settings.runtime_dir
    output_dir.mkdir(parents=True,exist_ok=True)
    ledger = BudgetLedger(PROJECT_LEDGER,settings.llm_budget_usd)
    result = {'checked_at':datetime.now(timezone.utc).isoformat(),'deepseek_model':settings.deepseek_model,'embedding_model':settings.embedding_model,'deepseek_key':'configured' if settings.deepseek_api_key else 'missing','embedding_key':'configured' if settings.siliconflow_api_key else 'missing'}
    budget = ledger.report()
    if budget['accounting_overrun'] or budget['spent_usd'] + budget['reserved_usd'] >= budget['limit_usd']:
        result.update(authentication='not_checked_budget_exhausted', json_completion_attempts=['budget_exhausted'], json_completion='budget_exhausted', native_tool_roundtrip='not_checked_budget_exhausted', embedding_probe={'status':'not_checked_budget_exhausted'}, budget=budget)
        (output_dir/'connectivity.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result,indent=2))
        return
    llm = DeepSeekClient(settings,ledger)
    try:
        models = llm.list_models()
        result['authentication'] = 'ok'
        result['model_available'] = settings.deepseek_model in models
    except ProviderError:
        result['authentication'] = 'failed'
    result['json_completion_attempts'] = probe_json(llm)
    result['json_completion'] = result['json_completion_attempts'][-1]
    try:
        tools = [{'type':'function','function':{'name':'ping','description':'Connectivity probe returning pong','parameters':{'type':'object','properties':{},'required':[]}}}]
        messages = [{'role':'user','content':'Call ping exactly once. After its result reply pong.'}]
        response = llm.complete(messages,purpose='connectivity_tool',tools=tools,tool_choice={'type':'function','function':{'name':'ping'}},max_tokens=128)
        message = response['choices'][0]['message']
        calls = message.get('tool_calls',[])
        if len(calls)!=1 or calls[0]['function']['name']!='ping':
            raise ProviderError('Unexpected tool response')
        messages.append(message)
        messages.append({'role':'tool','tool_call_id':calls[0]['id'],'content':'pong'})
        final = llm.complete(messages,purpose='connectivity_tool_result',tools=tools,tool_choice='none',max_tokens=128)
        result['native_tool_roundtrip'] = 'ok' if 'pong' in (final['choices'][0]['message'].get('content') or '').lower() else 'invalid'
    except (ProviderError,BudgetExceeded,ValueError,KeyError,TypeError):
        result['native_tool_roundtrip'] = 'failed'
    if settings.siliconflow_api_key:
        try:
            vectors = EmbeddingClient(settings).embed(['Connectivity probe'])
            result['embedding_probe'] = {'status':'ok','vectors':len(vectors),'dimensions':len(vectors[0])}
        except ProviderError:
            result['embedding_probe'] = {'status':'failed'}
    else:
        result['embedding_probe'] = {'status':'disabled; lexical retrieval only'}
    result['budget'] = ledger.report()
    (output_dir/'connectivity.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__ == '__main__':
    main()
