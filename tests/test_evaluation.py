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


def test_sealed_case_counts_and_retrieval_labels_have_real_source_ids(tmp_path):
    import json,hashlib
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
    for cid,expected in seal['cases'].items():assert hashlib.sha256((ROOT/'evals/cases'/(cid+'.json')).read_bytes()).hexdigest()==expected
    for c in (c for c in cases if c['split']=='holdout'):
        d=Domain(Store(tmp_path/(c['id']+'.sqlite'),clock=Clock()).initialize());patch_world(d,c['world']);chat=d.create_conversation(c['actor'])['id']
        seed_history(d,chat,c,FaultTools(d))
        with d.store.read() as db:assert not db.execute('PRAGMA foreign_key_check').fetchall()
