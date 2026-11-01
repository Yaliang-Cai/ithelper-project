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
