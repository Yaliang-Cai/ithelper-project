"""Generic known-ambiguity boundary, tested with real persisted interpretations."""
import pytest
from app.domain import Domain
from app.storage import Store
from app.tools import ToolExecutor


def interpret(d,chat,text,observations=None,unresolved=None,intents=None):
    m=d.append_message('alice.sales',chat,'user',text,'message-'+str(len(d.snapshot('alice.sales',chat)['messages'])))
    proposal={k:[] for k in ('message_intents','new_issue_candidates','observations','corrections','relation_candidates','unresolved_references')}
    proposal['observations']=observations or [];proposal['unresolved_references']=unresolved or []
    proposal['message_intents']=intents or (['new_request'] if any(o.get('category')=='request' for o in observations or []) else ['additional_info'])
    d.apply_candidates('alice.sales',chat,m['id'],proposal,d.snapshot('alice.sales',chat)['conversation']['version'])
    return m


@pytest.fixture
def ambiguity(tmp_path):
    d=Domain(Store(tmp_path/'world.sqlite').initialize());chat=d.create_conversation('alice.sales')['id']
    okta=d.create_issue('alice.sales',chat,'Okta sign-in',['okta'],'login_recovery')
    prod=d.create_issue('alice.sales',chat,'Production read',['snowflake'],'precise_access','snowflake.prod.analytics')
    grafana=d.create_issue('alice.sales',chat,'Sales dashboard read',['grafana'],'precise_access','grafana.sales')
    interpret(d,chat,'That one still fails.',[{'candidate_key':'o','target_refs':[okta['id'],prod['id']],'category':'reported_failure','claim':'Unclear which target failed','quote':'That one still fails.','mapping_status':'needs_clarification'}])
    return d,chat,okta,prod,grafana,ToolExecutor(d)


def test_implicated_new_unlock_and_approval_block_but_reads_and_other_write_work(ambiguity):
    d,chat,okta,prod,grafana,t=ambiguity
    a=t.call('unlock_account',{'issue_id':okta['id'],'service_id':'okta','evidence_refs':[]},'alice.sales',chat)
    assert not a['ok'] and a['error']['code']=='AMBIGUOUS_SCOPE'
    a=t.call('request_approval',{'issue_id':prod['id'],'resource_id':'snowflake.prod.analytics','permission':'read','reason':'report','evidence_refs':[]},'alice.sales',chat)
    assert not a['ok'] and a['error']['code']=='AMBIGUOUS_SCOPE'
    assert t.call('inspect_account',{'service_id':'okta'},'alice.sales',chat)['ok']
    assert t.call('grant_catalog_access',{'issue_id':grafana['id'],'resource_id':'grafana.sales','permission':'read','evidence_refs':[]},'alice.sales',chat)['ok']
    assert len(d.snapshot('alice.sales',chat)['actions'])==1 and not d.snapshot('alice.sales',chat)['approvals']


def test_only_later_single_target_interpretation_clears_that_scope(ambiguity):
    d,chat,okta,prod,grafana,t=ambiguity
    interpret(d,chat,'Thanks.')
    assert not t.call('unlock_account',{'issue_id':okta['id'],'service_id':'okta','evidence_refs':[]},'alice.sales',chat)['ok']
    interpret(d,chat,'I mean Okta; unlock my account.',[{'candidate_key':'clarify','target_refs':[okta['id']],'category':'request','claim':'Explicit Okta unlock request','quote':'I mean Okta; unlock my account.','mapping_status':'tentative'}])
    assert t.call('unlock_account',{'issue_id':okta['id'],'service_id':'okta','evidence_refs':[]},'alice.sales',chat)['ok']
    blocked=d.ambiguity_blocks('alice.sales',chat)
    assert {b['issue_id'] for b in blocked}=={prod['id']}


def test_same_event_finalization_cannot_erase_latched_ambiguity(ambiguity):
    d,chat,okta,prod,grafana,t=ambiguity
    proposal={'reply':'Please specify which one.','issue_updates':[],'questions':[],'question_updates':[],'hypotheses':[],'guidance':[],'refinements':[],'relation_updates':[]}
    d.submit_turn('alice.sales',chat,proposal,'finish-current')
    result=t.call('unlock_account',{'issue_id':okta['id'],'service_id':'okta','evidence_refs':[]},'alice.sales',chat)
    assert not result['ok'] and result['error']['code']=='AMBIGUOUS_SCOPE'


def test_unresolved_reference_alone_blocks_new_confirmation(tmp_path):
    d=Domain(Store(tmp_path/'world.sqlite').initialize());chat=d.create_conversation('alice.sales')['id']
    a=d.create_issue('alice.sales',chat,'VPN drops',['vpn'],'vpn_recovery');b=d.create_issue('alice.sales',chat,'Okta login',['okta'],'login_recovery')
    interpret(d,chat,'Still broken.',unresolved=[{'candidate_key':'u','quote':'Still broken.','candidate_issue_ids':[a['id'],b['id']],'clarification_question':'Which one?'}])
    result=ToolExecutor(d).call('reset_vpn_session',{'issue_id':a['id'],'session_id':'vpn.alice.sales','evidence_refs':[]},'alice.sales',chat)
    assert not result['ok'] and result['error']['code']=='AMBIGUOUS_SCOPE'
    assert not d.snapshot('alice.sales',chat)['confirmations']


@pytest.mark.parametrize('category,intents,text',[('context',['additional_info'],'Okta is in my Chrome browser.'),('preference',['additional_info'],'Please leave Okta alone for now.'),('request',['cancel_request'],'Cancel handling Okta for now.')])
def test_context_preference_or_cancel_cannot_clear(ambiguity,category,intents,text):
    d,chat,okta,prod,grafana,t=ambiguity
    interpret(d,chat,text,[{'candidate_key':'later','target_refs':[okta['id']],'category':category,'claim':text,'quote':text,'mapping_status':'tentative'}],intents=intents)
    result=t.call('unlock_account',{'issue_id':okta['id'],'service_id':'okta','evidence_refs':[]},'alice.sales',chat)
    assert not result['ok'] and result['error']['code']=='AMBIGUOUS_SCOPE'


def resolved_pair(tmp_path):
    d=Domain(Store(tmp_path/'pair.sqlite').initialize());chat=d.create_conversation('alice.sales')['id'];t=ToolExecutor(d)
    issues=[]
    for resource,service in [('grafana.sales','grafana'),('snowflake.prod.analytics','snowflake')]:
        i=d.create_issue('alice.sales',chat,'Read '+resource,[service],'precise_access',resource);args={'issue_id':i['id'],'resource_id':resource,'permission':'read','evidence_refs':[]}
        if service=='snowflake':
            approval=t.call('request_approval',{**args,'reason':'report'},'alice.sales',chat)['data']['approval'];d.decide_approval('pat.approver',approval['id'],'approved');args['approval_id']=approval['id']
        t.call('grant_catalog_access',args,'alice.sales',chat)
        evidence=t.call('inspect_access',{'resource_id':resource},'alice.sales',chat)['evidence'][0]
        current=next(x for x in d.snapshot('alice.sales',chat)['issues'] if x['id']==i['id'])
        d.apply_issue_updates('alice.sales',chat,[{'issue_id':i['id'],'expected_version':current['version'],'requested_status':'resolved','criterion_id':'precise_access','evidence_refs':[evidence['id']]}]);issues.append(i)
    return d,chat,issues


def test_ambiguous_negative_feedback_preserves_verified_goals_and_flags_uncertainty(tmp_path):
    d,chat,issues=resolved_pair(tmp_path)
    assert d.snapshot('alice.sales',chat)['all_resolved']
    interpret(d,chat,'That one failed again.',[{'candidate_key':'o','target_refs':[i['id'] for i in issues],'category':'reported_failure','claim':'Unassigned regression report','quote':'That one failed again.','mapping_status':'needs_clarification'}])
    s=d.snapshot('alice.sales',chat)
    assert all(i['status']=='resolved' and i['lifecycle']==1 for i in s['issues'])
    assert not s['all_resolved'] and s['unassigned_feedback']
    old=s['observations'][0]
    m=d.append_message('alice.sales',chat,'user','Actually I meant the other one.','ambiguous-correction')
    proposal={k:[] for k in ('message_intents','new_issue_candidates','observations','corrections','relation_candidates','unresolved_references')};proposal['message_intents']=['correction']
    proposal['corrections']=[{'candidate_key':'c','observation_id':old['id'],'expected_version':old['version'],'claim':'Still ambiguous target','quote':'Actually I meant the other one.'}]
    d.apply_candidates('alice.sales',chat,m['id'],proposal,d.snapshot('alice.sales',chat)['conversation']['version'])
    s=d.snapshot('alice.sales',chat)
    assert all(i['status']=='resolved' and i['lifecycle']==1 for i in s['issues'])
    assert s['observations'][-1]['mapping_status']=='needs_clarification' and not s['all_resolved']
    interpret(d,chat,'Grafana failed again.',[{'candidate_key':'specific','target_refs':[issues[0]['id']],'category':'reported_failure','claim':'Grafana regression','quote':'Grafana failed again.','mapping_status':'tentative'}],intents=['progress_report'])
    s=d.snapshot('alice.sales',chat);byid={i['id']:i for i in s['issues']}
    assert byid[issues[0]['id']]['status']=='investigating' and byid[issues[0]['id']]['lifecycle']==2
    assert byid[issues[1]['id']]['status']=='resolved' and byid[issues[1]['id']]['lifecycle']==1
    assert s['unassigned_feedback'][0]['candidate_issue_ids']==[issues[1]['id']] and not s['all_resolved']


@pytest.mark.parametrize('category,intents,text',[('reported_recovery',['progress_report'],'Grafana still works. I have not checked the database.'),('request',['new_request'],'Show me Grafana access details. I have not checked the database.')])
def test_scoped_progress_or_new_request_keeps_other_candidate_uncertain(tmp_path,category,intents,text):
    d,chat,issues=resolved_pair(tmp_path)
    interpret(d,chat,'That one failed again.',[{'candidate_key':'o','target_refs':[i['id'] for i in issues],'category':'reported_failure','claim':'Unassigned failure','quote':'That one failed again.','mapping_status':'needs_clarification'}])
    interpret(d,chat,text,[{'candidate_key':'p','target_refs':[issues[0]['id']],'category':category,'claim':text,'quote':text,'mapping_status':'tentative'}],intents=intents)
    state=d.snapshot('alice.sales',chat)
    assert all(i['status']=='resolved' and i['lifecycle']==1 for i in state['issues'])
    assert not state['all_resolved']
    assert issues[1]['id'] in state['unassigned_feedback'][0]['candidate_issue_ids']


@pytest.mark.parametrize('new_ambiguity',[False,True])
def test_inherited_correction_cannot_relatch_valid_same_batch_clarification(ambiguity,new_ambiguity):
    d,chat,okta,prod,grafana,t=ambiguity;old=d.snapshot('alice.sales',chat)['observations'][0]
    text='I mean Okta, please unlock it. The other reference is still unclear.'
    m=d.append_message('alice.sales',chat,'user',text,'clarify-with-correction')
    p={k:[] for k in ('message_intents','new_issue_candidates','observations','corrections','relation_candidates','unresolved_references')};p['message_intents']=['correction','new_request']
    p['corrections']=[{'candidate_key':'c','observation_id':old['id'],'expected_version':old['version'],'claim':'Supersede previous ambiguous report','quote':'I mean Okta'}]
    p['observations']=[{'candidate_key':'specific','target_refs':[okta['id']],'category':'request','claim':'Explicit Okta unlock','quote':'I mean Okta, please unlock it.','mapping_status':'tentative'}]
    if new_ambiguity:p['unresolved_references']=[{'candidate_key':'new','quote':'The other reference is still unclear.','candidate_issue_ids':[okta['id'],prod['id']],'clarification_question':'Which reference?'}]
    d.apply_candidates('alice.sales',chat,m['id'],p,d.snapshot('alice.sales',chat)['conversation']['version'])
    result=t.call('unlock_account',{'issue_id':okta['id'],'service_id':'okta','evidence_refs':[]},'alice.sales',chat)
    assert result['ok'] is (not new_ambiguity)
    assert bool(d.snapshot('alice.sales',chat)['unassigned_feedback']) is new_ambiguity


def test_linked_correction_only_assigns_its_exact_ambiguous_group(ambiguity):
    d,chat,okta,prod,grafana,t=ambiguity
    interpret(d,chat,'Two unclear references.',[
        {'candidate_key':'a','target_refs':[okta['id'],prod['id']],'category':'reported_failure','claim':'First unclear report','quote':'Two unclear references.','mapping_status':'needs_clarification'},
        {'candidate_key':'b','target_refs':[okta['id'],grafana['id']],'category':'reported_failure','claim':'Second unclear report','quote':'Two unclear references.','mapping_status':'needs_clarification'}])
    s=d.snapshot('alice.sales',chat);old=next(o for o in s['observations'] if o['claim']=='First unclear report')
    m=d.append_message('alice.sales',chat,'user','The first report meant Okta; please unlock it.','linked-first-only')
    p={k:[] for k in ('message_intents','new_issue_candidates','observations','corrections','relation_candidates','unresolved_references')};p['message_intents']=['correction','new_request']
    p['corrections']=[{'candidate_key':'c','observation_id':old['id'],'expected_version':old['version'],'claim':'First report assigned','quote':'The first report meant Okta'}]
    p['observations']=[{'candidate_key':'specific','target_refs':[okta['id']],'category':'request','claim':'Okta unlock','quote':'please unlock it.','mapping_status':'tentative'}]
    d.apply_candidates('alice.sales',chat,m['id'],p,d.snapshot('alice.sales',chat)['conversation']['version'])
    assert any(set(u['candidate_issue_ids'])=={okta['id'],grafana['id']} for u in d.snapshot('alice.sales',chat)['unassigned_feedback'])
