"""Small metered live-Agent smoke cases; always use the shared project ledger.

These are ordinary employee utterances, not runtime scenario routing or oracles.
Trace artifacts are honest observations, not grading or success claims.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from app.agent import Agent
from app.budget import BudgetLedger
from app.config import Settings, PROJECT_LEDGER
from app.domain import Domain,redact
from app.providers import DeepSeekClient,EmbeddingClient
from app.retrieval import Retrieval
from app.storage import Store
from app.tools import ToolExecutor

CASES={
    'okta':('alice.sales',['我的 Okta 账号被锁了，请帮我解锁；我还需要确认能登录。']),
    'unclear_resource':('chen.data',['我需要生产数据库的只读权限，老板说已经批准了。']),
    'two_goals':('alice.sales',['请给我销售 Grafana 仪表盘只读权限，同时申请 snowflake.prod.analytics 的生产只读权限。']),
}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',choices=list(CASES),action='append')
    parser.add_argument('--followups',action='store_true',help='Also exercise current-chat feedback with ordinary scripted employee messages.')
    args=parser.parse_args()
    settings=Settings.load(ROOT/'.env')
    ledger=BudgetLedger(PROJECT_LEDGER,limit_usd=settings.llm_budget_usd)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output=ROOT/'runtime'/'live'/('task3-'+stamp)
    output.mkdir(parents=True)
    retrieval=Retrieval(EmbeddingClient(settings),cache_path=ROOT/'runtime'/'retrieval.sqlite')
    client=DeepSeekClient(settings,ledger)
    summary={'mode':'live_agent','model':'deepseek-flash','embedding_model':'BAAI/bge-m3','budget_before':ledger.report(),'cases':[]}
    for name in args.case or ['okta','unclear_resource','two_goals']:
        actor,utterances=CASES[name]
        if args.followups:
            followups={'okta':'我刚重新登录，Okta 登录成功了。','two_goals':'Grafana已经能打开了，生产那个老板说批了。','unclear_resource':'我要的是 snowflake.prod.analytics 的只读权限。'}
            utterances=[*utterances,followups[name]]
        domain=Domain(Store(output/(name+'.sqlite')).initialize())
        chat=domain.create_conversation(actor)['id']
        agent=Agent(domain,client,ToolExecutor(domain,search=retrieval.search))
        runs=[]
        for n,text in enumerate(utterances):
            domain.submit_user_message(actor,chat,text,'live-'+str(n))
            claimed=domain.claim_event('task3-live',lease_seconds=180)
            result=agent.handle(claimed)
            domain.finish_event(claimed['id'],'task3-live',status='completed' if result['status']=='completed' else 'failed')
            runs.append(domain.get_run(actor,result['run_id']))
        state=domain.snapshot(actor,chat)
        case={'name':name,'actor_id':actor,'mode':'live_agent','runs':runs,'state':state}
        (output/(name+'.json')).write_text(json.dumps(redact(case),ensure_ascii=False,indent=2),encoding='utf-8')
        result=runs[-1]['result']
        turns=[{'run_id':r['id'],'status':r['status'],'error_code':r['result']['error_code'],'model_calls':r['model_calls'],'tool_calls':r['tool_calls'],'repairs':r['repairs']} for r in runs]
        aggregate='completed' if all(r['status']=='completed' for r in runs) else 'mixed' if len(runs)>1 else result['status']
        entry={'case':name,'status':aggregate,'final_status':result['status'],'turns':turns,'error_code':result['error_code'],'model_calls':sum(r['model_calls'] for r in runs),'tool_calls':sum(r['tool_calls'] for r in runs),
               'reply':result['message']['content'] if result['message'] else None,
               'issues':[{k:i[k] for k in ('id','goal','status')} for i in state['issues']],
               'retrieval_modes':sorted({t['result']['data'].get('mode') for r in runs for t in r.get('trace',[]) if t.get('name') in ('search_kb','search_history') and t.get('result',{}).get('data',{}).get('mode')})}
        summary['cases'].append(entry)
        print(json.dumps(entry,ensure_ascii=True),flush=True)
    summary['budget_after']=ledger.report()
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),'budget_after':summary['budget_after']},ensure_ascii=True))


if __name__=='__main__':main()
