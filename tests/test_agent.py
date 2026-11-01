import copy
import json
import pytest
from app.domain import Domain, DomainError
from app.storage import Store
from app.tools import ToolExecutor


@pytest.fixture
def env(tmp_path):
    d=Domain(Store(tmp_path/'world.sqlite').initialize())
    chat=d.create_conversation('alice.sales')['id']
    return d,chat


def candidates(**changes):
    value={k:[] for k in ('message_intents','new_issue_candidates','observations','corrections','relation_candidates','unresolved_references')}
    value.update(changes); return value


def tool(name,args,id='call1'):
    return {'id':id,'type':'function','function':{'name':name,'arguments':args if isinstance(args,str) else json.dumps(args)}}


def response(content=None,calls=None):
    m={'role':'assistant','content':content,'reasoning_content':'native-only-reason'}
    if calls: m['tool_calls']=calls
    return {'model':'deepseek-flash','choices':[{'message':m}],'usage':{'prompt_tokens':1,'completion_tokens':1}}


def final(text='请补充具体问题。',**changes):
    value={'reply':text,'issue_updates':[],'questions':[],'question_updates':[],'hypotheses':[],'guidance':[],'refinements':[]}
    value.update(changes); return response(calls=[tool('submit_turn',value,'finish')])


class Scripted:
    def __init__(self,*items): self.items=list(items);self.inputs=[]
    def complete(self,messages,**kwargs):
        self.inputs.append((copy.deepcopy(messages),kwargs))
        item=self.items.pop(0)
        return item(messages) if callable(item) else item


def event(d,chat,text):
    d.submit_user_message('alice.sales',chat,text,'message-'+str(len(d.snapshot('alice.sales',chat)['messages'])))
    return d.claim_event('test-worker')


def run(env,model,text='现在好了'):
    from app.agent import Agent
    d,chat=env
    return Agent(d,model,ToolExecutor(d)).handle(event(d,chat,text))


def test_two_stage_ambiguous_feedback_keeps_two_goals(env):
    d,chat=env
    a=d.create_issue('alice.sales',chat,'Grafana access',['grafana'])
    b=d.create_issue('alice.sales',chat,'Production read',['snowflake'])
    interpretation=candidates(message_intents=['unclear'],unresolved_references=[{'candidate_key':'u','quote':'现在好了','candidate_issue_ids':[a['id'],b['id']],'clarification_question':'哪一个恢复了？'}])
    model=Scripted(response(json.dumps(interpretation)),final('哪一个恢复了？',questions=[{'text':'哪一个恢复了？','issue_refs':[a['id'],b['id']],'question_intent':'reference_clarification'}]))
    result=run(env,model)
    assert result['status']=='completed'
    state=d.snapshot('alice.sales',chat)
    assert not state['observations'] and len(state['issues'])==2 and not state['all_resolved']
    assert len(state['questions'])==1 and len(model.inputs)==2
    assert '现在好了' in json.dumps(model.inputs[1][0],ensure_ascii=False)


def test_invalid_interpretation_atomic_one_repair_and_honest_stop(env):
    bad=candidates(new_issue_candidates=[{'candidate_key':'n','goal':'Bad','quote':'现在好了','service_refs':['okta']}],approved=True)
    result=run(env,Scripted(response(json.dumps(bad)),response(json.dumps(bad))))
    assert result['status']=='failed' and result['error_code']=='MODEL_PROTOCOL'
    state=env[0].snapshot('alice.sales',env[1]);assert not state['issues'] and len(state['messages'])==2


def test_native_pairing_malformed_args_and_repair_limit(env):
    def inspect(messages):
        assert messages[-3]['reasoning_content']=='native-only-reason'
        assert [m['tool_call_id'] for m in messages[-2:]]==['bad','good']
        assert json.loads(messages[-2]['content'])['error']['code']=='INVALID_ARGUMENT'
        return final()
    model=Scripted(response(json.dumps(candidates())),response(calls=[tool('inspect_account','{oops','bad'),tool('get_employee_context',{},'good')]),inspect)
    result=run(env,model)
    assert result['status']=='completed' and result['tool_calls']==2
    debug=env[0].get_run('alice.sales',result['run_id'])
    assert 'native-only-reason' not in json.dumps(debug)


def test_model_budget_stops_without_explanation_call(env):
    class Loop:
        def __init__(self):self.n=0
        def complete(self,messages,**kwargs):
            self.n+=1
            return response(json.dumps(candidates())) if self.n==1 else response(calls=[tool('get_employee_context',{},str(self.n))])
    model=Loop();result=run(env,model)
    assert result['status']=='budget_exhausted' and model.n==10
    assert result['model_calls']==10


def test_last_model_allowance_is_reserved_for_finalization(env):
    class LastTurn:
        def __init__(self):self.n=0
        def complete(self,messages,**kwargs):
            self.n+=1
            if self.n==1:return response(json.dumps(candidates()))
            if self.n==10:
                assert kwargs['tool_choice']=={'type':'function','function':{'name':'submit_turn'}}
                assert [s['function']['name'] for s in kwargs['tools']]==['submit_turn']
                return final('Progress is saved; I still need your feedback.')
            return response(calls=[tool('get_employee_context',{},str(self.n))])
    result=run(env,LastTurn(),'help')
    assert result['status']=='completed' and result['model_calls']==10


def test_context_preserves_mandatory_constraints_or_fails(env):
    from app.context import ContextBuilder, ContextCapacityError
    d,chat=env
    for i in range(8):d.create_issue('alice.sales',chat,'Goal '+str(i),['okta'])
    for i in range(12):d.append_message('alice.sales',chat,'user','old-'+str(i)+'x'*1000,'old'+str(i))
    e=event(d,chat,'current text')
    built=ContextBuilder(d,targets={'interpretation':6000,'decision':6000}).build('decision','alice.sales',chat,e)
    assert len(built['data']['issues'])==8 and built['data']['current_message']['content']=='current text'
    assert built['data']['identity']['id']=='alice.sales' and built['omitted_refs']
    with pytest.raises(ContextCapacityError):ContextBuilder(d,targets={'decision':10}).build('decision','alice.sales',chat,e)


def test_context_compacts_repeated_ownership_not_observations_or_authority(env):
    from app.context import ContextBuilder
    d,chat=env
    a=d.create_issue('alice.sales',chat,'Grafana access',['grafana'],'precise_access','grafana.sales')
    b=d.create_issue('alice.sales',chat,'Production read',['snowflake'],'precise_access','snowflake.prod.analytics')
    t=ToolExecutor(d)
    t.call('grant_catalog_access',{'issue_id':a['id'],'resource_id':'grafana.sales','permission':'read','evidence_refs':[]},'alice.sales',chat)
    approval=t.call('request_approval',{'issue_id':b['id'],'resource_id':'snowflake.prod.analytics','permission':'read','reason':'Exact analytics read for reporting; request remains pending. '*4,'evidence_refs':[]},'alice.sales',chat)['data']['approval']
    for n,text in enumerate(['Need production read only; not write.','老板说批了，但我没看到审批结果。','Grafana works; do not repeat the grant.']):
        m=d.append_message('alice.sales',chat,'user',text,'observation-'+str(n))
        batch=candidates(observations=[{'candidate_key':'obs'+str(n),'target_refs':[b['id'] if n<2 else a['id']],'category':'preference' if n!=1 else 'reported_approval','claim':text,'quote':text,'mapping_status':'tentative'}])
        d.apply_candidates('alice.sales',chat,m['id'],batch,d.snapshot('alice.sales',chat)['conversation']['version'])
    e=event(d,chat,'生产那个现在什么状态？')
    built=ContextBuilder(d).build('interpretation','alice.sales',chat,e)
    obs=built['data']['observations']
    assert len(obs)==3 and all(o['quote']==o['claim'] for o in obs)
    assert all('conversation_id' not in o and 'candidate_key' not in o and 'target_refs' not in o for o in obs)
    assert all(o['message_id'] and o['issue_refs'] and o['mapping_status']=='tentative' for o in obs)
    auth=built['data']['authority']['approvals'][0]
    assert auth['id']==approval['id'] and auth['status']=='pending' and auth['permission']=='read'
    assert auth['resource_id']=='snowflake.prod.analytics' and auth['expires_at']==approval['expires_at']
    assert built['compaction_version']=='owned-context-v1'


def test_default_context_handles_three_goals_pending_approval_and_refusal(env):
    from app.context import ContextBuilder
    d,chat=env
    grafana=d.create_issue('alice.sales',chat,'Sales Grafana access',['grafana'],'precise_access','grafana.sales')
    production=d.create_issue('alice.sales',chat,'Production analytics read',['snowflake'],'precise_access','snowflake.prod.analytics')
    vpn=d.create_issue('alice.sales',chat,'VPN intermittent drop during meetings',['vpn'],'vpn_recovery')
    tools=ToolExecutor(d)
    tools.call('grant_catalog_access',{'issue_id':grafana['id'],'resource_id':'grafana.sales','permission':'read','evidence_refs':[]},'alice.sales',chat)
    approval=tools.call('request_approval',{'issue_id':production['id'],'resource_id':'snowflake.prod.analytics','permission':'read','reason':'Read catalog for weekly sales reporting; approval remains pending.','evidence_refs':[]},'alice.sales',chat)['data']['approval']
    statements=[(grafana,'我需要销售仪表盘，用来准备本周报告，目前还没有访问权限。'),(production,'生产库只需要 analytics 查询权限，不要写权限，也不需要其他目录。'),(production,'经理口头说同意了，不过我还没有看到资源负责人的正式审批结果。'),(grafana,'Grafana 仪表盘现在能打开了，请保留这个结果，不要重复开通。'),(vpn,'还有 VPN 每十分钟掉一次，其他网页可以打开，是家里的有线网络。'),(vpn,'我正在参加一个重要会议，现在先不要重置 VPN，会影响视频连接。')]
    for n,(issue,text) in enumerate(statements):
        m=d.append_message('alice.sales',chat,'user',text,'robust-'+str(n))
        d.apply_candidates('alice.sales',chat,m['id'],candidates(observations=[{'candidate_key':'o'+str(n),'target_refs':[issue['id']],'category':'preference','claim':text,'quote':text,'mapping_status':'tentative'}]),d.snapshot('alice.sales',chat)['conversation']['version'])
    e=event(d,chat,'Keep the approval request and investigate VPN without interruption, please.')
    built=ContextBuilder(d).build('interpretation','alice.sales',chat,e)
    assert len(built['data']['issues'])==3 and len(built['data']['observations'])==6
    assert built['data']['authority']['approvals'][0]['id']==approval['id']
    assert built['data']['observations'][-1]['claim']==statements[-1][1]
    assert built['target_tokens']==10000


def test_oversized_mandatory_history_still_fails_explicitly(env):
    from app.context import ContextBuilder,ContextCapacityError
    d,chat=env
    for n in range(20):d.create_issue('alice.sales',chat,'Unresolved detailed goal '+str(n)+'x'*900,['okta'])
    e=event(d,chat,'Keep every unresolved goal.')
    with pytest.raises(ContextCapacityError):ContextBuilder(d).build('interpretation','alice.sales',chat,e)


def test_questions_bind_real_answer_and_hypothesis_revisions(env):
    d,chat=env;issue=d.create_issue('alice.sales',chat,'Login',['okta'])
    q=d.save_questions('alice.sales',chat,[{'text':'Which error?','issue_refs':[issue['id']],'question_intent':'error_detail'}])[0]
    msg=d.append_message('alice.sales',chat,'user','Locked','answer')
    updated=d.update_questions('alice.sales',chat,[{'question_id':q['id'],'status':'answered','answer_message_id':msg['id']}])[0]
    assert updated['status']=='answered' and updated['history'][0]['status']=='pending'
    with pytest.raises(DomainError):d.update_questions('alice.sales',chat,[{'question_id':q['id'],'status':'answered','answer_message_id':'fake'}])
    h=d.save_hypotheses('alice.sales',chat,[{'issue_id':issue['id'],'claim':'Maybe locked','status':'tentative','support_refs':[msg['id']],'contradict_refs':[]}])[0]
    revised=d.save_hypotheses('alice.sales',chat,[{'hypothesis_id':h['id'],'expected_version':1,'issue_id':issue['id'],'claim':'Rejected','status':'rejected','support_refs':[],'contradict_refs':[msg['id']]}])[0]
    assert revised['version']==2 and revised['history'][0]['status']=='tentative'
    assert d.snapshot('alice.sales',chat)['issues'][0]['status']=='investigating'


def test_partial_actual_resolution_does_not_accept_claimed_approval(env):
    d,chat=env
    grafana=d.create_issue('alice.sales',chat,'Grafana read',['grafana'],'precise_access','grafana.sales')
    prod=d.create_issue('alice.sales',chat,'Production read',['snowflake'],'precise_access','snowflake.prod.analytics')
    ToolExecutor(d).call('grant_catalog_access',{'issue_id':grafana['id'],'resource_id':'grafana.sales','permission':'read'},'alice.sales',chat)
    pending=ToolExecutor(d).call('request_approval',{'issue_id':prod['id'],'resource_id':'snowflake.prod.analytics','permission':'read','reason':'report','evidence_refs':[]},'alice.sales',chat)['data']['approval']
    proposal=candidates(message_intents=['progress_report'],observations=[{'candidate_key':'o','target_refs':[prod['id']],'category':'reported_approval','claim':'Claims approved','quote':'老板说批了','mapping_status':'tentative'}])
    def finish(messages):
        ctx=json.loads(messages[1]['content']);g=next(i for i in ctx['issues'] if i['id']==grafana['id'])
        read=json.loads(messages[-1]['content']);ref=read['evidence'][0]['id']
        return final('Grafana 权限已核实，生产权限仍等待实际审批。',issue_updates=[{'issue_id':g['id'],'expected_version':g['version'],'requested_status':'resolved','criterion_id':'precise_access','evidence_refs':[ref]}])
    model=Scripted(response(json.dumps(proposal)),response(calls=[tool('inspect_access',{'resource_id':'grafana.sales'})]),finish)
    result=run(env,model,'Grafana好了，老板说批了')
    state=d.snapshot('alice.sales',chat)
    assert result['status']=='completed'
    assert {i['id']:i['status'] for i in state['issues']}=={grafana['id']:'resolved',prod['id']:'waiting_approval'}
    assert state['approvals'][0]['id']==pending['id'] and state['approvals'][0]['status']=='pending'
    assert not state['all_resolved'] and result['cards']['approvals'][0]['status']=='pending'


def test_missing_evidence_finalization_is_atomic_and_repeated_rejection_stops(env):
    d,chat=env;i=d.create_issue('alice.sales',chat,'Login',['okta'],'login_recovery')
    def rejected(messages):
        issue=json.loads(messages[1]['content'])['issues'][0]
        return final('已恢复',questions=[{'text':'Should not persist','issue_refs':[i['id']],'question_intent':'general'}],
            issue_updates=[{'issue_id':i['id'],'expected_version':issue['version'],'requested_status':'resolved','criterion_id':'login_recovery','evidence_refs':['kb.fake']}])
    model=Scripted(response(json.dumps(candidates())),rejected,rejected)
    result=run(env,model)
    state=d.snapshot('alice.sales',chat)
    assert result['error_code']=='NO_PROGRESS' and not state['questions']
    assert state['issues'][0]['status']=='investigating'
    assert all(m['content']!='已恢复' for m in state['messages'])


def test_unknown_write_must_be_queried_before_any_write(env):
    from app.agent import Agent
    d,chat=env;i=d.create_issue('alice.sales',chat,'Unlock',['okta'],'account_unlock')
    class Lost:
        def after_commit(self,data):raise TimeoutError()
    def retry(messages):
        result=json.loads(messages[-1]['content'])
        assert result['error']['code']=='EXECUTION_UNCERTAIN'
        return response(calls=[tool('unlock_account',{'issue_id':i['id'],'service_id':'okta'},'blocked'),tool('get_action_result',{'action_id':result['action_id']},'query')])
    def finish(messages):
        assert json.loads(messages[-2]['content'])['error']['code']=='QUERY_BEFORE_WRITE_RETRY'
        assert json.loads(messages[-1]['content'])['data']['status']=='succeeded'
        return final('解锁操作已核实。')
    model=Scripted(response(json.dumps(candidates())),response(calls=[tool('unlock_account',{'issue_id':i['id'],'service_id':'okta'})]),retry,finish)
    result=Agent(d,model,ToolExecutor(d,adapter=Lost())).handle(event(d,chat,'请解锁Okta'))
    state=d.snapshot('alice.sales',chat)
    assert result['status']=='completed' and len(state['actions'])==1 and len(state['attempts'])==1


def test_tool_batch_budget_pairs_all_requests_and_never_exceeds_twelve(env):
    d,chat=env
    model=Scripted(response(json.dumps(candidates())),response(calls=[tool('get_employee_context',{},str(i)) for i in range(14)]))
    result=run(env,model)
    assert result['status']=='budget_exhausted' and result['tool_calls']==12
    trace=d.get_run('alice.sales',result['run_id'])['trace']
    results=[t for t in trace if t['kind']=='tool']
    assert len(results)==14 and all(t['result']['error']['code']=='TOOL_CALL_LIMIT' for t in results[-2:])


def test_deadline_late_model_response_never_executes_tools(env):
    from app.agent import Agent
    d,chat=env;clock=[0.0]
    def slow(messages):clock[0]=121;return response(calls=[tool('get_employee_context',{})])
    model=Scripted(response(json.dumps(candidates())),slow)
    result=Agent(d,model,monotonic=lambda:clock[0]).handle(event(d,chat,'help'))
    assert result['error_code']=='RUN_DEADLINE' and result['tool_calls']==0
    assert model.inputs[-1][1]['timeout_seconds']<=45


def test_lease_lost_during_model_cannot_persist_reply_or_apply_candidates(env):
    from app.agent import Agent
    d,chat=env;original=d.store.clock;clock=[original()];d.store.clock=lambda:clock[0]
    def slow(messages):clock[0]+=151;return response(json.dumps(candidates()))
    result=Agent(d,Scripted(slow)).handle(event(d,chat,'help'))
    assert result['error_code']=='EVENT_LEASE_LOST' and result['message'] is None
    assert len(d.snapshot('alice.sales',chat)['messages'])==1


def test_confirmation_event_bypasses_interpretation_and_returns_same_durable_result(env):
    from app.agent import Agent
    d,chat=env;i=d.create_issue('alice.sales',chat,'VPN',['vpn'],'vpn_recovery')
    pending=ToolExecutor(d).call('reset_vpn_session',{'issue_id':i['id'],'session_id':'vpn.alice.sales'},'alice.sales',chat)
    d.decide_confirmation('alice.sales',pending['data']['confirmation']['id'],'confirmed')
    e=d.claim_event('test-worker');model=Scripted(final('已收到确认。'))
    agent=Agent(d,model);first=agent.handle(e);second=agent.handle(e)
    assert first==second and len(model.inputs)==1
    assert model.inputs[0][1]['purpose']=='agent_decision'


def test_actual_canonical_guidance_and_third_path_limit(env):
    from app.seed import knowledge_documents
    d,chat=env;i=d.create_issue('alice.sales',chat,'Login',['okta'],'login_recovery')
    articles=[a for a in knowledge_documents() if a['service_id']=='okta' and a['reviewed'] and a['status']=='current']
    refs=[d.persist_evidence('alice.sales',chat,'kb',a['id'],a['revision'],a,{'service_id':'okta'})['id'] for a in articles]
    # Canonical reviewed bodies, not model commands, must appear in saved reply.
    article=articles[0]
    model=Scripted(response(json.dumps(candidates())),final('请按指南检查。',guidance=[{'issue_id':i['id'],'remedy_id':article['remedy_ids'][0],'normalized_parameters':{},'evidence_refs':[refs[0]]}]))
    result=run(env,model,'登录不了')
    assert result['status']=='completed' and article['text'] in result['message']['content']
    assert d.snapshot('alice.sales',chat)['attempts'][0]['outcome']=='guidance_offered'
    assert d.snapshot('alice.sales',chat)['issues'][0]['status']=='verifying'
    for index in (1,2):
        if index>=len(articles):break
        if index==1:
            msg=d.append_message('alice.sales',chat,'user','Still failing','guidance-failure')
            d.apply_candidates('alice.sales',chat,msg['id'],candidates(observations=[{'candidate_key':'f','target_refs':[i['id']],'category':'reported_failure','claim':'Still failing','quote':'Still failing','mapping_status':'tentative'}]),d.snapshot('alice.sales',chat)['conversation']['version'])
            d.record_attempt('alice.sales',chat,i['id'],articles[index]['remedy_ids'][0],{},[refs[index]])
        else:
            with pytest.raises(DomainError,match='ATTEMPT_LIMIT'):d.record_attempt('alice.sales',chat,i['id'],articles[index]['remedy_ids'][0],{},[refs[index]])


def test_duplicate_question_and_cross_chat_hypothesis_do_not_change_authority(env):
    d,chat=env;i=d.create_issue('alice.sales',chat,'Login',['okta'])
    q={'text':'Which error?','issue_refs':[i['id']],'question_intent':'error_detail'}
    assert d.save_questions('alice.sales',chat,[q])[0]['id']==d.save_questions('alice.sales',chat,[q])[0]['id']
    other=d.create_conversation('bo.remote')['id'];foreign=d.append_message('bo.remote',other,'user','counterevidence','x')
    with pytest.raises(DomainError):d.save_hypotheses('alice.sales',chat,[{'issue_id':i['id'],'claim':'Supported','status':'supported','support_refs':[foreign['id']],'contradict_refs':[]}])
    assert not d.snapshot('alice.sales',chat)['hypotheses']


def test_embedding_lookup_failure_is_unknown_never_healthy(env):
    from app.agent import Agent
    d,chat=env
    def search(*args,**kwargs):return {'hits':[],'degraded':True,'mode':'lexical','no_match':True}
    def finish(messages):
        result=json.loads(messages[-1]['content']);assert not result['ok'] and result['error']['code']=='NO_MATCH'
        assert result['data']['degraded'] and not result['evidence']
        return final('暂无适用指南，需要补充症状。')
    model=Scripted(response(json.dumps(candidates())),response(calls=[tool('search_kb',{'query':'unknown'})]),finish)
    result=Agent(d,model,ToolExecutor(d,search=search)).handle(event(d,chat,'unknown'))
    assert result['status']=='completed' and not d.snapshot('alice.sales',chat)['evidence']


def test_relation_reversal_retains_counterevidence_without_status_propagation(env):
    d,chat=env
    a=d.create_issue('alice.sales',chat,'Jenkins',['jenkins']);b=d.create_issue('alice.sales',chat,'Tableau',['tableau'])
    message=d.append_message('alice.sales',chat,'user','Both failed after maintenance','m')
    batch=candidates(observations=[{'candidate_key':'o','target_refs':[a['id'],b['id']],'category':'context','claim':'Temporal correlation','quote':'after maintenance','mapping_status':'tentative'}],relation_candidates=[{'candidate_key':'r','from_ref':a['id'],'to_ref':b['id'],'type':'possible_shared_cause','support_refs':['o']}])
    applied=d.apply_candidates('alice.sales',chat,message['id'],batch,d.snapshot('alice.sales',chat)['conversation']['version'])
    contradiction=d.append_message('alice.sales',chat,'user','Jenkins recovered but Tableau is still stale','counter')
    relation=applied['relations'][0]
    revised=d.update_relations('alice.sales',chat,[{'relation_id':relation['id'],'expected_version':1,'status':'rejected','support_refs':relation['evidence_refs'],'contradict_refs':[contradiction['id']]}])[0]
    assert revised['version']==2 and revised['history'][0]['status']=='candidate'
    assert revised['contradict_refs']==[contradiction['id']]
    assert all(i['status']=='investigating' for i in d.snapshot('alice.sales',chat)['issues'])


def test_provider_protocol_failure_is_corrected_at_most_once(env):
    from app.providers import ProviderError
    def malformed(messages):raise ProviderError('Invalid completion response')
    model=Scripted(malformed,response(json.dumps(candidates())),final())
    result=run(env,model)
    assert result['status']=='completed' and result['model_calls']==3


def test_retrieval_returns_after_deadline_cannot_persist_evidence(env):
    from app.agent import Agent
    d,chat=env;clock=[0.0]
    def slow_search(*args,**kwargs):
        clock[0]=121
        return {'hits':[{'id':'kb.account-unlock','revision':1,'service_id':'okta'}],'degraded':False,'mode':'hybrid'}
    model=Scripted(response(json.dumps(candidates())),response(calls=[tool('search_kb',{'query':'Okta'})]))
    result=Agent(d,model,ToolExecutor(d,search=slow_search),monotonic=lambda:clock[0]).handle(event(d,chat,'help'))
    assert result['error_code']=='RUN_DEADLINE' and not d.snapshot('alice.sales',chat)['evidence']


def test_tool_timestamp_display_comes_from_backend_epoch(env):
    from app.context import readable_timestamps
    from datetime import datetime,timezone
    epoch=1793577600.0
    displayed=readable_timestamps({'data':{'approval':{'expires_at':epoch}}})
    assert displayed['data']['approval']['expires_at']==epoch
    assert displayed['data']['approval']['expires_at_iso']==datetime.fromtimestamp(epoch,timezone.utc).isoformat()


def test_successful_unlock_needs_verification_before_different_guidance(env):
    d,chat=env;i=d.create_issue('alice.sales',chat,'Login',['okta'],'login_recovery')
    read=ToolExecutor(d).call('inspect_account',{'service_id':'okta'},'alice.sales',chat)
    assert ToolExecutor(d).call('unlock_account',{'issue_id':i['id'],'service_id':'okta','evidence_refs':[read['evidence'][0]['id']]},'alice.sales',chat)['ok']
    from app.seed import knowledge_documents
    a=next(a for a in knowledge_documents() if a['id']=='kb.account-browser')
    ref=d.persist_evidence('alice.sales',chat,'kb',a['id'],a['revision'],a,{'service_id':'okta'})['id']
    with pytest.raises(DomainError,match='WAITING_FOR_ATTEMPT_FEEDBACK'):
        d.record_attempt('alice.sales',chat,i['id'],'browser_private_window',{},[ref])
    message=d.append_message('alice.sales',chat,'user','Still cannot log in','failure')
    failure=candidates(observations=[{'candidate_key':'failed','target_refs':[i['id']],'category':'reported_failure','claim':'Still cannot log in','quote':'Still cannot log in','mapping_status':'tentative'}])
    d.apply_candidates('alice.sales',chat,message['id'],failure,d.snapshot('alice.sales',chat)['conversation']['version'])
    assert d.record_attempt('alice.sales',chat,i['id'],'browser_private_window',{},[ref])['outcome']=='guidance_offered'


def test_business_precondition_feedback_does_not_spend_second_format_retry(env):
    d,chat=env;i=d.create_issue('alice.sales',chat,'Unlock',['okta'],'account_unlock')
    assert ToolExecutor(d).call('unlock_account',{'issue_id':i['id'],'service_id':'okta'},'alice.sales',chat)['ok']
    def premature(messages):
        issue=json.loads(messages[1]['content'])['issues'][0]
        return final('已经解锁',issue_updates=[{'issue_id':i['id'],'expected_version':issue['version'],'requested_status':'resolved','criterion_id':'account_unlock','evidence_refs':[]}])
    def query(messages):
        error=json.loads(messages[-1]['content'])['error']
        assert error['code']=='PRECONDITION_FAILED' and 'inspection' in error['message']
        return response(calls=[tool('inspect_account',{'service_id':'okta'},'verify')])
    def verified(messages):
        issue=json.loads(messages[1]['content'])['issues'][0];ref=json.loads(messages[-1]['content'])['evidence'][0]['id']
        return final('已核实解锁',issue_updates=[{'issue_id':i['id'],'expected_version':issue['version'],'requested_status':'resolved','criterion_id':'account_unlock','evidence_refs':[ref]}])
    malformed=response(calls=[tool('submit_turn','{oops','bad')])
    result=run(env,Scripted(response(json.dumps(candidates())),malformed,premature,query,verified),'请解锁')
    assert result['status']=='completed' and d.get_run('alice.sales',result['run_id'])['repairs']==1
    assert d.snapshot('alice.sales',chat)['issues'][0]['status']=='resolved'


def test_provider_balance_failure_is_distinct_and_english_fallback_needs_no_call(env):
    from app.providers import ProviderError
    def broke(messages):raise ProviderError(category='insufficient_balance',http_status=402)
    model=Scripted(broke)
    result=run(env,model,'Please unlock my account')
    assert result['status']=='failed' and result['error_code']=='PROVIDER_INSUFFICIENT_BALANCE'
    assert 'balance' in result['message']['content'].lower() and len(model.inputs)==1


def test_clarification_refines_scope_then_requests_approval_in_same_event(env):
    d,chat=env;i=d.create_issue('alice.sales',chat,'Production database read',[],'precise_access')
    proposal=candidates(message_intents=['additional_info'],observations=[{'candidate_key':'target','target_refs':[i['id']],'category':'request','claim':'Exact analytics target','quote':'snowflake.prod.analytics','mapping_status':'tentative'}])
    def refine(messages):
        issue=json.loads(messages[1]['content'])['issues'][0]
        return response(calls=[tool('refine_issue',{'issue_id':i['id'],'expected_version':issue['version'],'service_refs':['snowflake'],'resource_id':'snowflake.prod.analytics','criterion_id':'precise_access'},'refine')])
    def request(messages):
        result=json.loads(messages[-1]['content']);assert result['ok'] and result['data']['issue']['resource_id']=='snowflake.prod.analytics'
        return response(calls=[tool('request_approval',{'issue_id':i['id'],'resource_id':'snowflake.prod.analytics','permission':'read','reason':'Business report','evidence_refs':[]},'approve')])
    result=run(env,Scripted(response(json.dumps(proposal)),refine,request,final('已创建精确范围的待审批申请。')),'我要 snowflake.prod.analytics 的只读权限')
    state=d.snapshot('alice.sales',chat)
    assert result['status']=='completed' and result['tool_calls']==2
    assert len(state['issues'])==1 and state['issues'][0]['id']==i['id'] and state['issues'][0]['status']=='waiting_approval'
    assert state['approvals'][0]['resource_id']=='snowflake.prod.analytics' and state['approvals'][0]['status']=='pending'


def test_interpretation_context_supplies_actual_service_and_resource_catalog(env):
    from app.context import ContextBuilder
    d,chat=env;e=event(d,chat,'production database')
    data=ContextBuilder(d).build('interpretation','alice.sales',chat,e)['data']
    assert 'snowflake' in data['catalog']['service_ids'] and 'production_database' not in data['catalog']['service_ids']
    resource=next(r for r in data['catalog']['resources'] if r['id']=='snowflake.prod.analytics')
    assert resource['service_id']=='snowflake'


def test_interpretation_schema_distinguishes_intents_from_observation_categories():
    from app.contracts import Interpretation
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Interpretation.model_validate(candidates(observations=[{'candidate_key':'o','target_refs':['existing'],'category':'additional_info','claim':'x','quote':'x','mapping_status':'tentative'}]))


def test_cancelling_obsolete_issue_revokes_unexecuted_approval_with_audit(env):
    d,chat=env;i=d.create_issue('alice.sales',chat,'Production read',['snowflake'],'precise_access','snowflake.prod.analytics')
    a=ToolExecutor(d).call('request_approval',{'issue_id':i['id'],'resource_id':'snowflake.prod.analytics','permission':'read','reason':'report','evidence_refs':[]},'alice.sales',chat)['data']['approval']
    d.decide_approval('pat.approver',a['id'],'approved')
    issue=d.snapshot('alice.sales',chat)['issues'][0]
    value={'reply':'已取消旧申请，后续目标需单独授权。','issue_updates':[{'issue_id':issue['id'],'expected_version':issue['version'],'requested_status':'cancelled','criterion_id':'precise_access','evidence_refs':[]}],
        'questions':[],'question_updates':[],'hypotheses':[],'guidance':[],'refinements':[]}
    d.submit_turn('alice.sales',chat,value,'cancel-final')
    state=d.snapshot('alice.sales',chat)
    assert state['approvals'][0]['status']=='cancelled' and state['issues'][0]['status']=='cancelled'
    assert any(e.get('approval_id')==a['id'] and e.get('decision')=='cancelled' for e in state['events'])
    assert not ToolExecutor(d).call('grant_catalog_access',{'issue_id':i['id'],'resource_id':'snowflake.prod.analytics','permission':'read','approval_id':a['id']},'alice.sales',chat)['ok']


@pytest.mark.parametrize('read_kind',['request','single','aggregate'])
def test_approval_wrapper_dereferences_current_owned_record(env,read_kind):
    d,chat=env;i=d.create_issue('alice.sales',chat,'Production read',['snowflake'],'precise_access','snowflake.prod.analytics')
    result=ToolExecutor(d).call('request_approval',{'issue_id':i['id'],'resource_id':'snowflake.prod.analytics','permission':'read','reason':'report','evidence_refs':[]},'alice.sales',chat)
    approval_id=result['data']['approval']['id']
    if read_kind!='request':
        args={'approval_id':approval_id} if read_kind=='single' else {'issue_id':i['id']}
        result=ToolExecutor(d).call('get_approval',args,'alice.sales',chat)
    current=d.snapshot('alice.sales',chat)['issues'][0]
    update={'issue_id':i['id'],'expected_version':current['version'],'requested_status':'waiting_approval','criterion_id':'precise_access','evidence_refs':[result['evidence'][0]['id']]}
    assert d.apply_issue_updates('alice.sales',chat,[update])[0]['status']=='waiting_approval'
    assert d.snapshot('alice.sales',chat)['approvals'][0]['status']=='pending'
    update['expected_version']+=1
    update['evidence_refs']=[approval_id]
    assert d.apply_issue_updates('alice.sales',chat,[update])[0]['status']=='waiting_approval'


@pytest.mark.parametrize('bad_kind',['stale','wrong_scope','wrong_revision','wrong_permission','rejected','fabricated','user_report','expired'])
def test_approval_wrappers_cannot_manufacture_or_reuse_authority(env,bad_kind):
    d,chat=env;i=d.create_issue('alice.sales',chat,'Production read',['snowflake'],'precise_access','snowflake.prod.analytics')
    tools=ToolExecutor(d)
    result=tools.call('request_approval',{'issue_id':i['id'],'resource_id':'snowflake.prod.analytics','permission':'read','reason':'report','evidence_refs':[]},'alice.sales',chat)
    approval=result['data']['approval'];ref=result['evidence'][0]['id']
    if bad_kind=='stale':d.decide_approval('pat.approver',approval['id'],'approved')
    elif bad_kind=='rejected':
        d.decide_approval('pat.approver',approval['id'],'rejected')
        ref=tools.call('get_approval',{'approval_id':approval['id']},'alice.sales',chat)['evidence'][0]['id']
    elif bad_kind=='wrong_scope':
        i=d.create_issue('alice.sales',chat,'Other production request',['snowflake'],'precise_access','snowflake.prod.analytics')
    elif bad_kind=='fabricated':
        ref=d.persist_evidence('alice.sales',chat,'approval_event','APPROVAL-invented',1,{'approval':{'status':'pending','issue_id':i['id']}},{'actor_id':'alice.sales','issue_id':i['id'],'resource_id':'snowflake.prod.analytics','permission':'read'})['id']
    elif bad_kind in ('wrong_revision','wrong_permission'):
        scope=copy.deepcopy(result['evidence'][0]['scope']);revision=approval['revision']
        if bad_kind=='wrong_revision':
            revision=0;scope['approval_revisions'][approval['id']]='0'
        else:scope['approval_scopes'][approval['id']]['permission']='admin'
        ref=d.persist_evidence('alice.sales',chat,'approval_event',approval['id'],revision,result['data'],scope)['id']
    elif bad_kind=='user_report':
        msg=d.append_message('alice.sales',chat,'user','Boss approved','claimed')
        ref=d.apply_candidates('alice.sales',chat,msg['id'],candidates(observations=[{'candidate_key':'o','target_refs':[i['id']],'category':'reported_approval','claim':'Boss approved','quote':'Boss approved','mapping_status':'tentative'}]),d.snapshot('alice.sales',chat)['conversation']['version'])['observations'][0]['id']
    elif bad_kind=='expired':
        future=d.store.clock()+90000;d.store.clock=lambda:future
    current=next(row for row in d.snapshot('alice.sales',chat)['issues'] if row['id']==i['id'])
    update={'issue_id':i['id'],'expected_version':current['version'],'requested_status':'waiting_approval','criterion_id':'precise_access','evidence_refs':[ref]}
    with pytest.raises(DomainError) as error:d.apply_issue_updates('alice.sales',chat,[update])
    assert error.value.code=='PRECONDITION_FAILED' and 'current_approvals' in error.value.data
    assert 'proposed' in error.value.message.lower() or 'no current pending' in error.value.message.lower()


def test_submit_turn_replays_whole_transaction_before_versioned_mutations(env):
    d,chat=env;i=d.create_issue('alice.sales',chat,'Login',['okta'])
    q=d.save_questions('alice.sales',chat,[{'text':'Which error?','issue_refs':[i['id']],'question_intent':'error_detail'}])[0]
    msg=d.append_message('alice.sales',chat,'user','Locked','answer')
    h=d.save_hypotheses('alice.sales',chat,[{'issue_id':i['id'],'claim':'Possibly locked','status':'tentative','support_refs':[msg['id']],'contradict_refs':[]}])[0]
    proposal=json.loads(final('Need confirmation.',
        hypotheses=[{'hypothesis_id':h['id'],'expected_version':1,'issue_id':i['id'],'claim':'Lock supported','status':'supported','support_refs':[msg['id']],'contradict_refs':[]},
                    {'issue_id':i['id'],'claim':'Still tentative','status':'tentative','support_refs':[msg['id']],'contradict_refs':[]}],
        question_updates=[{'question_id':q['id'],'status':'answered','answer_message_id':msg['id']}],
        issue_updates=[{'issue_id':i['id'],'expected_version':i['version'],'requested_status':'waiting_user','criterion_id':'application_recovery','evidence_refs':[]}]
    )['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
    first=d.submit_turn('alice.sales',chat,proposal,'whole-turn')
    before=d.snapshot('alice.sales',chat)
    replay=Domain(Store(d.store.db_path).initialize()).submit_turn('alice.sales',chat,proposal,'whole-turn')
    after=d.snapshot('alice.sales',chat)
    assert replay==first and after==before
    assert len(after['hypotheses'])==2 and next(x for x in after['hypotheses'] if x['id']==h['id'])['version']==2
    assert after['issues'][0]['version']==i['version']+1 and len(after['questions'][0]['history'])==1
    assert len([m for m in after['messages'] if m['role']=='assistant'])==1
    conflicting=copy.deepcopy(proposal);conflicting['hypotheses'][1]['claim']='Changed under same key'
    with pytest.raises(DomainError,match='CONFLICT'):d.submit_turn('alice.sales',chat,conflicting,'whole-turn')
    assert d.snapshot('alice.sales',chat)==before


def test_submit_turn_key_is_owned_scoped_and_reset_with_business_world(env):
    d,chat=env
    proposal=json.loads(final('Saved.')['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
    first=d.submit_turn('alice.sales',chat,proposal,'scoped')
    assert d.get_submitted_turn('alice.sales',chat,'scoped')==first
    other=d.create_conversation('alice.sales')['id']
    assert d.get_submitted_turn('alice.sales',other,'scoped') is None
    second=d.submit_turn('alice.sales',other,proposal,'scoped')
    assert second['message']['id']!=first['message']['id']
    with pytest.raises(DomainError,match='FORBIDDEN'):d.get_submitted_turn('bo.remote',chat,'scoped')
    d.store.reset_demo()
    with d.store.read() as c:assert c.execute('SELECT COUNT(*) FROM turn_submissions').fetchone()[0]==0


def test_agent_recovers_committed_submission_after_run_save_crash_without_model(env):
    import sqlite3
    from app.agent import Agent
    d,chat=env;i=d.create_issue('alice.sales',chat,'Login',['okta'])
    e=event(d,chat,'Locked')
    msg=e['message_id']
    model=Scripted(response(json.dumps(candidates())),final('Saved before worker crash.',
        hypotheses=[{'issue_id':i['id'],'claim':'Possibly locked','status':'tentative','support_refs':[msg],'contradict_refs':[]}],
        issue_updates=[{'issue_id':i['id'],'expected_version':i['version'],'requested_status':'waiting_user','criterion_id':'application_recovery','evidence_refs':[]}]))
    with d.store.write() as c:
        c.execute("""CREATE TRIGGER crash_run_save AFTER UPDATE ON runs
            WHEN EXISTS(SELECT 1 FROM messages m WHERE m.conversation_id=NEW.conversation_id
                        AND m.idempotency_key='agent:' || NEW.event_id AND m.role='assistant')
            BEGIN SELECT RAISE(ABORT,'simulated crash after committed submission'); END""")
    with pytest.raises(sqlite3.IntegrityError,match='simulated crash'):
        Agent(d,model).handle(e)
    before=d.snapshot('alice.sales',chat)
    assert len(before['hypotheses'])==1 and not before['runs'][0].get('result')
    with d.store.write() as c:c.execute('DROP TRIGGER crash_run_save')
    extra=d.create_issue('alice.sales',chat,'Separate current goal',['grafana'])
    current=d.snapshot('alice.sales',chat)
    # New process facade and an empty provider: recovery must not need a fresh
    # stochastic proposal to rediscover the already committed turn fingerprint.
    restarted=Domain(Store(d.store.db_path).initialize())
    recovered=Agent(restarted,Scripted()).handle(e)
    after=restarted.snapshot('alice.sales',chat)
    assert recovered['status']=='completed' and recovered['run_id']==before['runs'][0]['id']
    assert recovered['message']['content']=='Saved before worker crash.'
    assert after['hypotheses']==current['hypotheses'] and after['issues']==current['issues']
    assert after['messages']==before['messages']
    assert recovered['cards']['issues']==current['issues'] and any(row['id']==extra['id'] for row in recovered['cards']['issues'])
    assert after['runs'][0]['result']==recovered and after['runs'][0]['recovered_submission'] is True


@pytest.mark.parametrize('ignore_final_choice',[False,True])
def test_internal_protocol_retry_recomputes_final_schema_and_dispatch(env,ignore_final_choice):
    from app.providers import ProviderError
    d,chat=env
    class NinthFailure:
        def __init__(self):self.n=0;self.final_request=None
        def complete(self,messages,**kwargs):
            self.n+=1
            if self.n==1:return response(json.dumps(candidates()))
            if self.n==9:raise ProviderError(category='protocol')
            if self.n==10:
                self.final_request=kwargs
                return response(calls=[tool('get_employee_context',{},'ignored-final')]) if ignore_final_choice else final('Saved at final allowance.')
            return response(calls=[tool('get_employee_context',{},str(self.n))])
    model=NinthFailure();result=run(env,model,'help')
    assert model.n==10 and result['model_calls']==10
    assert [s['function']['name'] for s in model.final_request['tools']]==['submit_turn']
    assert model.final_request['tool_choice']=={'type':'function','function':{'name':'submit_turn'}}
    with d.store.read() as c:
        actual_calls=[json.loads(row['payload']) for row in c.execute('SELECT payload FROM tool_calls WHERE conversation_id=?',(chat,))]
    assert len(actual_calls)==7 and all(row['name']=='get_employee_context' for row in actual_calls)
    assert result['status']==('budget_exhausted' if ignore_final_choice else 'completed')
    if ignore_final_choice:
        trace=d.get_run('alice.sales',result['run_id'])['trace']
        denied=next(t for t in trace if t.get('call_id')=='ignored-final')
        assert denied['result']['error']['code']=='FINALIZATION_REQUIRED'
