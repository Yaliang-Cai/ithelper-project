"""Additional source-authored unknown-version/region probes; original 40 untouched."""
import time
from app.config import Settings
from app.providers import EmbeddingClient
from app.retrieval import Retrieval
from evals.runner import ROOT,save

QUERIES=[
 {'id':'Q41','query':'VPN periodic disconnect session','filters':{'service_id':'vpn'},'expected':['kb.vpn-session'],'source_type':'kb','condition':'version unknown'},
 {'id':'Q42','query':'Salesforce slow pages regional incident','filters':{'service_id':'salesforce','region':'apac'},'expected':[],'source_type':'history','condition':'wrong region excludes history'},
 {'id':'Q43','query':'Snowflake read owner approval','filters':{'service_id':'snowflake'},'expected':['kb.snowflake-read'],'source_type':'kb','condition':'version unknown'},
 {'id':'Q44','query':'Salesforce old plugin workaround','filters':{'service_id':'salesforce','version':'2025.1'},'expected':[],'source_type':'kb','condition':'retired wrong-version document excluded'}]

def main():
    settings=Settings.load(ROOT/'.env');rows=[]
    for mode in ('lexical','hybrid'):
        retrieval=Retrieval(None if mode=='lexical' else EmbeddingClient(settings),ROOT/'runtime/retrieval.sqlite')
        for q in QUERIES:
            start=time.perf_counter();result=retrieval.search(q['query'],q['filters'],source_type=q['source_type']);ids=[h['id'] for h in result['hits']]
            rows.append({**q,'requested_mode':mode,'actual_mode':result['mode'],'degraded':result['degraded'],'returned':ids,'passed':all(i in ids for i in q['expected']) if q['expected'] else not ids,'latency_seconds':time.perf_counter()-start})
    save(ROOT/'evals/results/retrieval-supplement/rows.json',rows);save(ROOT/'evals/public/retrieval-supplement.json',rows)
    print([(r['id'],r['requested_mode'],r['passed']) for r in rows])

if __name__=='__main__':main()
