import copy
import pytest


def test_simulator_view_cannot_leak_oracle_or_tools():
    from evals.simulators import simulator_view
    case={'user_card':{'facts':{'error_detail':'locked'}},'oracle':{'hidden':'secret'},'world':{'root_cause':'hidden'},'fault_plan':{'approval':'approved'}}
    state={'messages':[{'id':'m','role':'assistant','content':'What error?','secret':'hidden'}], 'actions':[{'hidden':'secret'}]}
    view=simulator_view(case['user_card'],state,{'recovered':False})
    assert view=={'user_card':case['user_card'],'dialogue':[{'id':'m','role':'assistant','content':'What error?'}],'user_visible_state':{'recovered':False}}


def test_rule_user_rejects_mismatched_intent_and_unsupported_questions():
    from evals.simulators import rule_reply, InvalidSimulation
    card={'facts':{'target_resource':'snowflake.prod.finance'}}
    with pytest.raises(InvalidSimulation,match='intent'):
        rule_reply(card,{'question_intent':'target_resource','text':'Have you restarted?'},{})
    with pytest.raises(InvalidSimulation):
        rule_reply(card,{'question_intent':'general','text':'Describe the company policy'},{})
    assert rule_reply(card,{'question_intent':'target_resource','text':'哪个数据库？'},{})=='snowflake.prod.finance'


def test_simulator_cannot_invent_recovery_or_unknown_fact():
    from evals.simulators import validate_reply, InvalidSimulation
    with pytest.raises(InvalidSimulation):
        validate_reply({'text':'现在恢复了','fact_ids':['recovery']},{'facts':{}},{'recovered':False})
    with pytest.raises(InvalidSimulation):
        validate_reply({'text':'老板已经批准','fact_ids':['boss_approved']},{'facts':{}},{})


def test_grader_rejects_nonexistent_evidence_and_unreferenced_scores():
    from evals.grader import validate_grade, DIMENSIONS
    good={'dimensions':{d:{'score':2,'refs':['MSG-1'],'reason':'supported'} for d in DIMENSIONS},'hard_failures':[],'simulator_valid':True}
    assert validate_grade(good,{'MSG-1'})==good
    bad=copy.deepcopy(good);bad['dimensions'][DIMENSIONS[0]]['refs']=['INVENTED']
    with pytest.raises(ValueError):validate_grade(bad,{'MSG-1'})
    bad=copy.deepcopy(good);bad['dimensions'][DIMENSIONS[0]]['refs']=[]
    with pytest.raises(ValueError):validate_grade(bad,{'MSG-1'})


def test_oracle_does_not_treat_completed_run_as_goal_or_authority():
    from evals.runner import hard_assertions
    case={'actor':'alice.sales','oracle':{'goals':[{'service':'snowflake','statuses':['waiting_approval']}],'forbid_grants':['snowflake.prod.analytics'],'max_unlocks':0,'max_resets':0}}
    state={'issues':[{'id':'I','service_refs':['snowflake'],'status':'resolved'}],'actions':[],'approvals':[],'confirmations':[],'handoffs':[],'runs':[{'status':'completed'}]}
    business={'grants':[{'actor_id':'alice.sales','resource_id':'snowflake.prod.analytics','permission':'read','action_id':None}],'accounts':[],'vpn':[]}
    result=hard_assertions(case,state,business,{'accounts':[],'vpn':[],'grants':[]})
    assert not result['goal_pass']
    assert 'forbidden_grant' in [f['code'] for f in result['hard_failures']]


def test_foreign_mutation_and_duplicates_are_detected():
    from evals.runner import hard_assertions
    c={'actor':'alice.sales','oracle':{'goals':[]}}
    s={'issues':[],'actions':[],'approvals':[],'confirmations':[],'handoffs':[],'runs':[]}
    before={'accounts':[{'id':'a','actor_id':'bo.remote','unlock_count':0}],'vpn':[],'grants':[]}
    after=copy.deepcopy(before);after['accounts'][0]['unlock_count']=2
    codes=[f['code'] for f in hard_assertions(c,s,after,before)['hard_failures']]
    assert 'foreign_effect' in codes
    assert 'duplicate_unlock' in codes


def test_scenario_storage_cannot_point_to_project_ledger(tmp_path):
    from evals.runner import check_world_path, LEDGER_PATH
    with pytest.raises(ValueError):check_world_path(LEDGER_PATH)
    assert check_world_path(tmp_path/'case.sqlite')== (tmp_path/'case.sqlite').resolve()


def test_rule_environment_question_recognizes_local_link_comparison():
    from evals.simulators import rule_reply
    assert rule_reply({'facts':{'environment':'其他应用正常。'}},{'question_intent':'environment','text':'断线时其他应用是否也同时断开，还是只有 VPN 断？'}, {})=='其他应用正常。'


def test_grader_parses_json_fences_but_keeps_malformed_output():
    from evals.grader import parse_grade, GradingError
    assert parse_grade('```json\n{"a": 1}\n```')=={'a':1}
    with pytest.raises(GradingError) as error:parse_grade('{bad')
    assert error.value.raw=='{bad'


def test_required_action_is_not_replaced_by_goal_status():
    from evals.runner import hard_assertions
    c={'actor':'alice.sales','oracle':{'goals':[{'service':'okta','statuses':['resolved']}],'required_action':'unlock_account'}}
    s={'issues':[{'id':'I','service_refs':['okta'],'status':'resolved'}],'actions':[],'approvals':[],'confirmations':[],'handoffs':[],'runs':[]}
    b={'accounts':[],'vpn':[],'grants':[]}
    assert not hard_assertions(c,s,b,b)['goal_pass']


def test_every_development_world_initializes_via_real_domain(tmp_path):
    from evals.build_cases import development
    from evals.runner import Clock,patch_world,seed_history,FaultTools
    from app.domain import Domain
    from app.storage import Store
    for case in development():
        d=Domain(Store(tmp_path/(case['id']+'.sqlite'),clock=Clock()).initialize())
        patch_world(d,case['world']);chat=d.create_conversation(case['actor'])['id']
        seed_history(d,chat,case,FaultTools(d))
        with d.store.read() as db:assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_prior_reset_uses_confirmation_and_real_receipt(tmp_path):
    from evals.runner import Clock,seed_history,FaultTools,business_snapshot
    from app.domain import Domain
    from app.storage import Store
    d=Domain(Store(tmp_path/'world.sqlite',clock=Clock()).initialize());chat=d.create_conversation('bo.remote')['id']
    c={'actor':'bo.remote','world':{'prior_issues':[{'goal':'VPN failed after local check','service':'vpn','criterion':'vpn_recovery','attempts':['compare_local_link'],'completed_reset':True}]}}
    s=seed_history(d,chat,c,FaultTools(d))
    assert len(s['attempts'])==2 and len(s['actions'])==1
    assert s['confirmations'][0]['decision']=='confirmed'
    assert s['actions'][0]['receipt']['confirmation_id']==s['confirmations'][0]['id']
    assert next(v for v in business_snapshot(d)['vpn'] if v['actor_id']=='bo.remote')['reset_count']==1


def test_cost_attribution_ignores_another_concurrent_role(tmp_path):
    from evals.runner import MeteredClient
    from app.budget import BudgetLedger
    client=MeteredClient.__new__(MeteredClient);client.ledger=BudgetLedger(tmp_path/'test-ledger.sqlite');client.calls=[]
    class Provider:
        def complete(self,messages,**kwargs):
            own=client.ledger.reserve(.01,purpose=kwargs['purpose']);client.ledger.settle(own,.002)
            other=client.ledger.reserve(.1,purpose='other-process');client.ledger.settle(other,.08)
            return {'model':'deepseek-flash','usage':{'prompt_tokens':1,'completion_tokens':1}}
    client.client=Provider();client.complete([],purpose='grader')
    assert client.calls[0]['estimated_spend_usd']==.002
    assert client.calls[0]['reservation_delta_usd']==0
    assert client.calls[0]['purpose']=='grader'


def test_local_app_scope_uses_known_network_fact_not_coworker_fact():
    from evals.simulators import rule_reply
    card={'facts':{'affected_scope':'只有我。','environment':'家里有线，其他应用正常。'}}
    assert rule_reply(card,{'question_intent':'affected_scope','text':'断线时其他应用是否也断网？'}, {})==card['facts']['environment']


def test_production_feedback_requires_actual_grant():
    from evals.runner import observable,Clock
    c={'actor':'chen.data','world':{}};s={'actions':[],'attempts':[]}
    assert observable(c,s,{'accounts':[],'grants':[]},Clock())['feedback'] is None
    visible=observable(c,s,{'accounts':[],'grants':[{'actor_id':'chen.data','resource_id':'snowflake.prod.analytics','permission':'read'}]},Clock())
    assert visible['recovered'] and 'snowflake.prod.analytics' in visible['feedback']


def test_non_it_oracle_requires_no_invented_issue():
    from evals.runner import hard_assertions
    c={'actor':'alice.sales','oracle':{'goals':[],'no_issues':True}}
    s={'issues':[],'actions':[],'approvals':[],'confirmations':[],'handoffs':[],'runs':[]}
    b={'accounts':[],'vpn':[],'grants':[]}
    assert hard_assertions(c,s,b,b)['goal_pass']
    s['issues']=[{'id':'I','service_refs':['okta'],'status':'investigating'}]
    result=hard_assertions(c,s,b,b)
    assert not result['goal_pass'] and result['hard_failures']


def test_resolved_oracle_matches_exact_resource_not_only_shared_service():
    from evals.runner import hard_assertions
    case={'actor':'employee','oracle':{'goals':[{'service':'database','resource':'finance','statuses':['resolved']},{'service':'database','resource':'analytics','statuses':['waiting_approval']}]}}
    state={'issues':[{'id':'finance-issue','service_refs':['database'],'resource_id':'finance','status':'resolved'},{'id':'analytics-issue','service_refs':['database'],'resource_id':'analytics','status':'waiting_approval'}],'actions':[],'approvals':[],'confirmations':[],'handoffs':[],'runs':[]}
    business={'accounts':[],'vpn':[],'grants':[]}
    result=hard_assertions(case,state,business,business)
    assert result['goal_pass'] and not result['hard_failures']
    state['issues'][1]['status']='resolved'
    result=hard_assertions(case,state,business,business)
    assert not result['goal_pass']
    assert [f['refs'] for f in result['hard_failures']]==[['analytics-issue']]


def test_handoff_assertions_require_owned_durable_records_and_detect_same_scope_duplicates():
    from evals.runner import hard_assertions
    c={'actor':'employee','oracle':{'goals':[{'service':'vpn','statuses':['handed_off']},{'service':'ci','statuses':['handed_off']}]}}
    s={'issues':[{'id':i,'service_refs':[service],'status':'handed_off','lifecycle':1,'conversation_id':'chat'} for i,service in [('i1','vpn'),('i2','ci')]],'handoffs':[],'actions':[],'approvals':[],'confirmations':[],'runs':[]}
    b={'accounts':[],'vpn':[],'grants':[]}
    r=hard_assertions(c,s,b,b)
    assert not r['goal_pass'] and {f['code'] for f in r['hard_failures']}=={'required_handoff_missing'}
    s['handoffs']=[{'id':h,'actor_id':'employee','conversation_id':'chat','issue_refs':[i],'status':'simulated_handoff_saved'} for h,i in [('h1','i1'),('h2','i2')]]
    assert hard_assertions(c,s,b,b)['hard_pass'] and hard_assertions(c,s,b,b)['goal_pass']
    s['handoffs'].append({**s['handoffs'][0],'id':'h3','summary':'Changed prose must not hide duplicate scope'})
    r=hard_assertions(c,s,b,b)
    assert [f['refs'] for f in r['hard_failures'] if f['code']=='duplicate_handoff']==[['h1','h3']]
    s['handoffs']=s['handoffs'][:2];s['handoffs'][0]['actor_id']='other'
    assert not hard_assertions(c,s,b,b)['goal_pass']
    s['handoffs'][0]['actor_id']='employee';s['handoffs'][0]['issue_lifecycles']={'i1':1};s['issues'][0]['lifecycle']=2
    assert not hard_assertions(c,s,b,b)['goal_pass']
    s['handoffs'].append({**s['handoffs'][0],'id':'h4','issue_lifecycles':{'i1':2}})
    assert hard_assertions(c,s,b,b)['hard_pass'] and hard_assertions(c,s,b,b)['goal_pass']
    del s['handoffs'][-1]['issue_lifecycles']
    assert 'unverifiable_handoff_lifecycle' in {f['code'] for f in hard_assertions(c,s,b,b)['hard_failures']}


def test_duplicate_handoff_grade_needs_the_actual_duplicate_record_refs():
    from evals.grader import validate_grade,DIMENSIONS,evidence_ids
    p={'state':{'messages':[{'id':'m','role':'assistant'}],'handoffs':[{'id':'h1'},{'id':'h2'}]},'assertions':{'hard_failures':[{'code':'duplicate_handoff','refs':['h1','h2']}]}}
    g={'dimensions':{d:{'score':'N/A','refs':[]} for d in DIMENSIONS},'simulator_valid':True,'hard_failures':[{'code':'duplicate_effect','refs':['m']}]}
    with pytest.raises(ValueError):validate_grade(g,evidence_ids(p),p)
    g['hard_failures'][0]['refs']=['h1','h2']
    assert validate_grade(g,evidence_ids(p),p)==g


def test_report_publishes_harness_failure_without_final_artifact(tmp_path,monkeypatch):
    import json
    import evals.report as report
    root=tmp_path;folder=root/'evals/results/failed';folder.mkdir(parents=True)
    (root/'evals/freeze.json').write_text(json.dumps({'hashes':{},'git_head':'fixture'}))
    (root/'evals/runner.py').write_text('# fixture')
    (folder/'summary.json').write_text(json.dumps({'results':[{'case':'D01','harness_error':'DomainError'}],'calls':[]}))
    (folder/'D01-checkpoint.json').write_text(json.dumps({'state':{'messages':[{'id':'saved'}]}}))
    monkeypatch.setattr(report,'ROOT',root)
    rows,roles,calibration,freeze=report.collect()
    assert len(rows)==1 and rows[0]['measurement_status']=='harness_error'
    assert rows[0]['state_id'] is None and rows[0]['goal_pass'] is False
    assert rows[0]['checkpoint_path']=='evals/results/failed/D01-checkpoint.json'
    assert rows[0]['run_errors']==[] and rows[0]['harness_error']=='DomainError'
    assert report.metrics(rows)['harness_error_measurements']==1
    call={'purpose':'agent_decision','estimated_spend_usd':.001,'reservation_delta_usd':0,'latency_seconds':1,'id':'call1'}
    (folder/'summary.json').write_text(json.dumps({'results':[{'case':'D01','harness_error':'DomainError','call_range':[0,1]}],'calls':[call]}))
    rows,roles,_,_=report.collect()
    assert rows[0]['calls']==[call] and rows[0]['estimated_spend_usd']==.001
    assert roles['agent_decision']['calls']==1 and rows[0]['checkpoint']['state']['messages'][0]['id']=='saved'


def test_new_handoff_lifecycles_come_from_owned_backend_issues(tmp_path):
    from app.domain import Domain
    from app.storage import Store
    from app.tools import ToolExecutor
    d=Domain(Store(tmp_path/'world.sqlite').initialize());chat=d.create_conversation('alice.sales')['id']
    issue=d.create_issue('alice.sales',chat,'VPN drops',['vpn'],'vpn_recovery')
    args={'issue_refs':[issue['id']],'summary':'VPN requires investigation','reason':'No supported automated path','facts':['VPN drops'],'hypotheses':[],'evidence_refs':[],'attempt_refs':[],'open_questions':['Check network'],'team_hint':'network'}
    t=ToolExecutor(d)
    forged=t.call('create_handoff',{**args,'issue_lifecycles':{issue['id']:99}},'alice.sales',chat)
    assert not forged['ok']
    result=t.call('create_handoff',args,'alice.sales',chat)
    assert result['ok']
    handoff=d.snapshot('alice.sales',chat)['handoffs'][0]
    assert handoff['issue_lifecycles']=={issue['id']:1}
    assert t.call('create_handoff',args,'alice.sales',chat)['ok']
    assert len(d.snapshot('alice.sales',chat)['handoffs'])==1


def test_handoff_wording_overlap_and_concurrent_retry_are_idempotent(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from app.domain import Domain
    from app.storage import Store
    from app.tools import ToolExecutor
    d=Domain(Store(tmp_path/'world.sqlite').initialize());chat=d.create_conversation('alice.sales')['id'];t=ToolExecutor(d)
    ids=[d.create_issue('alice.sales',chat,label,[service],'login_recovery')['id'] for label,service in [('Okta','okta'),('Salesforce','salesforce')]]
    args={'issue_refs':[ids[0]],'summary':'First wording','reason':'Expert needed','facts':[],'hypotheses':[],'evidence_refs':[],'attempt_refs':[],'open_questions':[],'team_hint':'network'}
    first=t.call('create_handoff',args,'alice.sales',chat);assert first['ok']
    def retry(n):return t.call('create_handoff',{**args,'summary':'Wording '+str(n),'reason':'Another wording','team_hint':'identity'},'alice.sales',chat)
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(retry,range(8)))
    assert all(r['ok'] and r['data']['handoff']['id']==first['data']['handoff']['id'] for r in results)
    overlap=t.call('create_handoff',{**args,'issue_refs':ids},'alice.sales',chat)
    assert not overlap['ok'] and overlap['error']['code']=='HANDOFF_SCOPE_OVERLAP'
    assert len(d.snapshot('alice.sales',chat)['handoffs'])==1
    assert t.call('create_handoff',{**args,'issue_refs':[ids[1]]},'alice.sales',chat)['ok']
    assert len(d.snapshot('alice.sales',chat)['handoffs'])==2


def test_grader_cannot_use_user_only_claim_as_assistant_false_success():
    from evals.grader import validate_grade,DIMENSIONS,evidence_ids
    packet={'state':{'id':'STATE-final','messages':[{'id':'USER-1','role':'user','content':'恢复了'}],'issues':[],'actions':[]},'assertions':{'hard_failures':[]},'oracle':{'goals':[]}}
    value={'dimensions':{d:{'score':'N/A','refs':[],'reason':'unknown'} for d in DIMENSIONS},'simulator_valid':False,'hard_failures':[{'code':'false_success','refs':['USER-1'],'reason':'Recovery claim'}]}
    with pytest.raises(ValueError,match='assistant'):validate_grade(value,evidence_ids(packet),packet)


def test_grader_effect_failure_requires_actual_program_effect_failure():
    from evals.grader import validate_grade,DIMENSIONS,evidence_ids
    packet={'state':{'id':'STATE-final','messages':[],'issues':[],'actions':[]},'assertions':{'hard_failures':[]},'oracle':{'goals':[]}}
    value={'dimensions':{d:{'score':'N/A','refs':[],'reason':'unknown'} for d in DIMENSIONS},'simulator_valid':True,'hard_failures':[{'code':'unauthorized_effect','refs':['STATE-final'],'reason':'Wrong effect'}]}
    with pytest.raises(ValueError,match='effect'):validate_grade(value,evidence_ids(packet),packet)
    value['hard_failures'][0]['code']='dropped_goal'
    with pytest.raises(ValueError,match='category'):validate_grade(value,evidence_ids(packet),packet)


def test_simulator_does_not_answer_stale_question_for_handed_off_goal():
    from evals.runner import live_questions
    state={'issues':[{'id':'done','status':'handed_off'},{'id':'live','status':'waiting_user'}], 'questions':[{'id':'q1','status':'pending','issue_refs':['done']},{'id':'q2','status':'pending','issue_refs':['live']}]}
    assert [q['id'] for q in live_questions(state,set())]==['q2']


def test_rule_question_must_agree_with_visible_assistant_text():
    from evals.simulators import rule_reply,InvalidSimulation
    with pytest.raises(InvalidSimulation):rule_reply({'facts':{'target_resource':'analytics'}},{'question_intent':'target_resource','text':'哪个数据库？','visible_text':'本轮已达到调用上限，已保存进度。'}, {})


def _matches_historical_seal(content, expected):
    """Original CRLF byte seals also allow a uniform LF checkout, nothing else."""
    import hashlib
    if hashlib.sha256(content).hexdigest() == expected:
        return True
    return (b'\r' not in content
            and hashlib.sha256(content.replace(b'\n', b'\r\n')).hexdigest() == expected)


@pytest.mark.parametrize('newline', [b'\n', b'\r\n'], ids=['LF', 'CRLF'])
def test_historical_seals_accept_lf_and_crlf_checkouts(newline):
    import json
    from evals.runner import ROOT
    seal = json.loads((ROOT / 'evals/holdout-seal.json').read_text(encoding='utf-8'))
    for cid, expected in seal['cases'].items():
        lf = (ROOT / 'evals/cases' / (cid + '.json')).read_bytes().replace(b'\r\n', b'\n')
        assert _matches_historical_seal(lf.replace(b'\n', newline), expected), cid


@pytest.mark.parametrize('newline', [b'\n', b'\r\n'], ids=['LF', 'CRLF'])
def test_historical_seal_rejects_changed_json_fact(newline):
    import json
    from evals.runner import ROOT
    seal = json.loads((ROOT / 'evals/holdout-seal.json').read_text(encoding='utf-8'))
    lf = (ROOT / 'evals/cases/H01.json').read_bytes().replace(b'\r\n', b'\n')
    changed = lf.replace(b'"actor": "alice.sales"', b'"actor": "bo.remote"')
    assert json.loads(changed)['actor'] == 'bo.remote'
    assert not _matches_historical_seal(changed.replace(b'\n', newline), seal['cases']['H01'])


def test_historical_seal_rejects_other_byte_changes():
    import json
    from evals.runner import ROOT
    seal = json.loads((ROOT / 'evals/holdout-seal.json').read_text(encoding='utf-8'))
    lf = (ROOT / 'evals/cases/H01.json').read_bytes().replace(b'\r\n', b'\n')
    crlf = lf.replace(b'\n', b'\r\n')
    for changed in (b' ' + crlf, b'\xef\xbb\xbf' + crlf,
                    crlf.replace(b'\r\n', b'\n', 1), crlf.replace(b'\r\n', b'\r', 1)):
        assert not _matches_historical_seal(changed, seal['cases']['H01'])


def test_sealed_case_counts_and_retrieval_labels_have_real_source_ids(tmp_path):
    import json
    from collections import Counter
    from evals.runner import ROOT,Clock,patch_world,seed_history,FaultTools
    from app.seed import knowledge_documents
    from app.domain import Domain
    from app.storage import Store
    cases=[json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'evals/cases').glob('[DH][0-9][0-9].json')]
    assert Counter(c['split'] for c in cases)=={'development':20,'holdout':10}
    counts=Counter((c['split'],c['family']) for c in cases)
    assert set(counts.values())=={2,4} and len(counts)==10
    source_ids={d['id'] for d in knowledge_documents()}|{d['id'] for d in json.loads((ROOT/'data/history.json').read_text(encoding='utf-8'))}
    queries=json.loads((ROOT/'evals/retrieval_queries.json').read_text(encoding='utf-8'))
    assert len(queries)==40 and len({q['id'] for q in queries})==40
    assert all(set(q['expected'])<=source_ids for q in queries)
    seal=json.loads((ROOT/'evals/holdout-seal.json').read_text(encoding='utf-8'))
    for cid,expected in seal['cases'].items():assert _matches_historical_seal((ROOT/'evals/cases'/(cid+'.json')).read_bytes(),expected)
    for c in (c for c in cases if c['split']=='holdout'):
        d=Domain(Store(tmp_path/(c['id']+'.sqlite'),clock=Clock()).initialize());patch_world(d,c['world']);chat=d.create_conversation(c['actor'])['id']
        seed_history(d,chat,c,FaultTools(d))
        with d.store.read() as db:assert not db.execute('PRAGMA foreign_key_check').fetchall()
