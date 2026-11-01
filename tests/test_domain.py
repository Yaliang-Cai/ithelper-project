import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.storage import Store
from app.domain import Domain, DomainError
from app.tools import ToolExecutor, TOOL_SCHEMAS


@pytest.fixture
def env(tmp_path):
    now = [1804300000.0]
    store = Store(tmp_path / 'business.db', clock=lambda: now[0])
    store.initialize()
    domain = Domain(store)
    chat = domain.create_conversation('alice.sales')
    issue = domain.create_issue('alice.sales', chat['id'], 'Grafana access', ['grafana'],
                                criterion_id='access_and_user_verification', resource_id='grafana.sales')
    return store, domain, ToolExecutor(domain), chat['id'], issue['id'], now


def call(env, name, **args):
    return env[2].call(name, args, 'alice.sales', env[3])


def test_handoff_lifecycle_identity_and_legacy_uncertainty(env):
    store,domain,_,chat,issue,_=env
    args=dict(issue_refs=[issue],summary='Needs expert',facts=[],hypotheses=[],attempt_refs=[],evidence_refs=[],open_questions=['Cause?'],reason='Unsupported operation',team_hint='general_it')
    first=call(env,'create_handoff',**args)['data']['handoff']
    # Seed a later owned lifecycle to exercise the persistence boundary. Expert
    # resumption is outside this product; this fixture does not add that API.
    with store.write() as c:
        payload=json.loads(c.execute('SELECT payload FROM issues WHERE id=?',(issue,)).fetchone()[0]);payload['lifecycle']=2
        c.execute("UPDATE issues SET payload=?,status='investigating' WHERE id=?",(json.dumps(payload),issue))
    second=call(env,'create_handoff',**args)
    assert second['ok'] and second['data']['handoff']['id']!=first['id']
    assert second['data']['handoff']['issue_lifecycles']=={issue:2}
    # A legacy row without lifecycle metadata cannot be guessed to belong to1
    # when the issue has already advanced. Historical rows are never rewritten
    # by production replay; removal here is solely a legacy-record fixture.
    with store.write() as c:
        payload=json.loads(c.execute('SELECT payload FROM handoffs WHERE id=?',(first['id'],)).fetchone()[0]);payload.pop('issue_lifecycles')
        c.execute('UPDATE handoffs SET payload=? WHERE id=?',(json.dumps(payload),first['id']))
    unknown=call(env,'create_handoff',**args)
    assert not unknown['ok'] and unknown['error']['code']=='HANDOFF_LIFECYCLE_UNKNOWN'
    assert len(domain.snapshot('alice.sales',chat)['handoffs'])==2


def test_ownership_and_catalog_have_no_actor_override(env):
    store, domain, tools, chat, issue, _ = env
    with pytest.raises(DomainError, match='FORBIDDEN'):
        domain.snapshot('bo.remote', chat)
    result = tools.call('inspect_account', {'service_id':'okta', 'actor_id':'bo.remote'}, 'alice.sales', chat)
    assert result['error']['code'] == 'INVALID_ARGUMENT'
    assert len(TOOL_SCHEMAS) == 16
    assert all(s['function']['parameters']['additionalProperties'] is False for s in TOOL_SCHEMAS)


def test_real_grant_receipt_replay_and_read_provenance(env):
    first = call(env, 'grant_catalog_access', issue_id=env[4], resource_id='grafana.sales', permission='read')
    assert first['ok'] and first['action_id']
    replay = call(env, 'grant_catalog_access', issue_id=env[4], resource_id='grafana.sales', permission='read')
    assert replay['action_id'] == first['action_id']
    read = call(env, 'inspect_access', resource_id='grafana.sales')
    assert read['data']['permissions'] == ['read']
    evidence = read['evidence'][0]
    assert {'id','source','record_id','revision','content_hash','fetched_at','scope'} <= evidence.keys()
    assert env[1].snapshot('alice.sales',env[3])['evidence']


def test_unsupported_permission_and_wrong_department_never_mutate(env):
    assert call(env,'grant_catalog_access',issue_id=env[4],resource_id='grafana.sales',permission='admin')['error']['code'] == 'FORBIDDEN'
    assert call(env,'grant_catalog_access',issue_id=env[4],resource_id='grafana.platform',permission='read')['error']['code'] == 'FORBIDDEN'
    assert call(env,'restart_production',issue_id=env[4])['error']['code'] == 'INVALID_ARGUMENT'


def test_approval_scope_expiry_refusal_and_duplicate_decisions(env):
    store, domain, tools, chat, _, now = env
    issue = domain.create_issue('alice.sales',chat,'Production read access',['snowflake'],
                                criterion_id='precise_access',resource_id='snowflake.prod.analytics')['id']
    req = call(env,'request_approval',issue_id=issue,resource_id='snowflake.prod.analytics',permission='read',reason='Quarterly report',evidence_refs=[])
    approval = req['data']['approval']
    assert approval['status'] == 'pending'
    assert call(env,'inspect_access',resource_id='snowflake.prod.analytics')['data']['permissions'] == []
    with pytest.raises(DomainError,match='FORBIDDEN'):
        domain.decide_approval('alice.sales',approval['id'],'approved')
    decided = domain.decide_approval('pat.approver',approval['id'],'approved')
    assert domain.decide_approval('pat.approver',approval['id'],'approved') == decided
    assert len(domain.list_events('alice.sales',chat)) == 1
    wrong = call(env,'grant_catalog_access',issue_id=issue,resource_id='snowflake.prod.finance',permission='read',approval_id=approval['id'])
    assert wrong['error']['code'] == 'FORBIDDEN'
    now[0] += 90000
    expired = call(env,'grant_catalog_access',issue_id=issue,resource_id='snowflake.prod.analytics',permission='read',approval_id=approval['id'])
    assert expired['error']['code'] == 'APPROVAL_REQUIRED'
    req2 = call(env,'request_approval',issue_id=issue,resource_id='snowflake.prod.analytics',permission='read',reason='Renewed',evidence_refs=[])
    domain.decide_approval('pat.approver',req2['data']['approval']['id'],'rejected')
    with pytest.raises(DomainError,match='CONFLICT'):
        domain.decide_approval('pat.approver',req2['data']['approval']['id'],'approved')


def test_approved_precise_grant_is_recovered_after_restart(env):
    store, domain, _, chat, _, _ = env
    issue = domain.create_issue('alice.sales',chat,'Production',['snowflake'],criterion_id='precise_access',resource_id='snowflake.prod.analytics')['id']
    req = call(env,'request_approval',issue_id=issue,resource_id='snowflake.prod.analytics',permission='read',reason='Business need',evidence_refs=[])
    aid = req['data']['approval']['id']
    domain.decide_approval('pat.approver',aid,'approved')
    restarted = Domain(Store(store.db_path,clock=store.clock))
    event = restarted.claim_event('worker-1')
    assert event['actor_id']=='alice.sales' and event['issue_id']==issue
    tools = ToolExecutor(restarted)
    result = tools.call('grant_catalog_access',{'issue_id':issue,'resource_id':'snowflake.prod.analytics','permission':'read','approval_id':aid},'alice.sales',chat)
    assert result['ok']
    restarted.finish_event(event['id'],'worker-1')
    assert restarted.claim_event('worker-2') is None


def test_vpn_confirmation_exact_owner_refusal_expiry_and_replay(env):
    store, domain, tools, chat, _, now = env
    issue = domain.create_issue('alice.sales',chat,'VPN',['vpn'],criterion_id='vpn_recovery')['id']
    pending = call(env,'reset_vpn_session',issue_id=issue,session_id='vpn.alice.sales')
    cid = pending['data']['confirmation']['id']
    assert pending['error']['code']=='CONFIRMATION_REQUIRED'
    assert call(env,'reset_vpn_session',issue_id=issue,session_id='vpn.alice.sales')['data']['confirmation']['id']==cid
    with pytest.raises(DomainError,match='FORBIDDEN'):
        domain.decide_confirmation('bo.remote',cid,'confirmed')
    domain.decide_confirmation('alice.sales',cid,'denied')
    assert not call(env,'reset_vpn_session',issue_id=issue,session_id='vpn.alice.sales',confirmation_id=cid)['ok']
    assert not call(env,'reset_vpn_session',issue_id=issue,session_id='vpn.bo.remote')['ok']


def test_confirmed_vpn_mutates_once_and_needs_observation_window(env):
    domain=env[1]
    issue=domain.create_issue('alice.sales',env[3],'VPN',['vpn'],criterion_id='vpn_recovery')['id']
    result=call(env,'reset_vpn_session',issue_id=issue,session_id='vpn.alice.sales')
    cid=result['data']['confirmation']['id']
    domain.decide_confirmation('alice.sales',cid,'confirmed')
    result=call(env,'reset_vpn_session',issue_id=issue,session_id='vpn.alice.sales',confirmation_id=cid)
    assert result['ok']
    read=call(env,'inspect_vpn')
    assert read['data']['reset_count']==1
    again=call(env,'reset_vpn_session',issue_id=issue,session_id='vpn.alice.sales',confirmation_id=cid)
    assert again['action_id']==result['action_id']
    assert call(env,'inspect_vpn')['data']['reset_count']==1


def test_unknown_response_after_commit_is_queryable_without_second_effect(env):
    class LostResponse:
        def after_commit(self, action):
            raise TimeoutError('response lost')
    issue=env[1].create_issue('alice.sales',env[3],'Login recovery',['okta'],criterion_id='login_recovery')['id']
    tools=ToolExecutor(env[1],adapter=LostResponse())
    result=tools.call('unlock_account',{'issue_id':issue,'service_id':'okta'},'alice.sales',env[3])
    assert result['error']['code']=='EXECUTION_UNCERTAIN'
    assert call(env,'get_action_result',action_id=result['action_id'])['data']['status']=='succeeded'
    assert call(env,'inspect_account',service_id='okta')['data']['locked'] is False
    assert call(env,'unlock_account',issue_id=issue,service_id='okta')['action_id']==result['action_id']


def test_security_disabled_account_cannot_be_unlocked(env):
    chat=env[1].create_conversation('dana.platform')['id']
    issue=env[1].create_issue('dana.platform',chat,'Login',['okta'])['id']
    result=env[2].call('unlock_account',{'issue_id':issue,'service_id':'okta'},'dana.platform',chat)
    assert result['error']['code']=='FORBIDDEN'


def test_candidates_atomic_immutable_provenance_and_stale_versions(env):
    domain=env[1]
    message=domain.append_message('alice.sales',env[3],'user','Grafana好了，老板说批了','request-1')
    assert domain.append_message('alice.sales',env[3],'user','Grafana好了，老板说批了','request-1')['id']==message['id']
    batch={'message_intents':['progress_report'],'new_issue_candidates':[], 'observations':[
        {'candidate_key':'o1','target_refs':[env[4]],'category':'reported_recovery','claim':'Grafana recovered','quote':'Grafana好了','mapping_status':'tentative'}],
        'corrections':[], 'relation_candidates':[], 'unresolved_references':[]}
    version=domain.snapshot('alice.sales',env[3])['conversation']['version']
    bad=json.loads(json.dumps(batch)); bad['observations'][0]['approved']=True
    with pytest.raises(DomainError,match='INVALID_ARGUMENT'):
        domain.apply_candidates('alice.sales',env[3],message['id'],bad,version)
    assert domain.snapshot('alice.sales',env[3])['observations']==[]
    saved=domain.apply_candidates('alice.sales',env[3],message['id'],batch,version)
    assert saved['observations'][0]['source_kind']=='user_report'
    assert domain.apply_candidates('alice.sales',env[3],message['id'],batch,version)==saved
    newer=domain.append_message('alice.sales',env[3],'user','still broken','request-2')
    with pytest.raises(DomainError,match='STALE_STATE'):
        domain.apply_candidates('alice.sales',env[3],newer['id'],batch,version)


def test_independent_resolution_requires_actual_access_and_user_feedback(env):
    domain=env[1]
    other=domain.create_issue('alice.sales',env[3],'Snowflake',['snowflake'],criterion_id='precise_access',resource_id='snowflake.prod.analytics')['id']
    call(env,'grant_catalog_access',issue_id=env[4],resource_id='grafana.sales',permission='read')
    read=call(env,'inspect_access',resource_id='grafana.sales')
    version=next(i['version'] for i in domain.snapshot('alice.sales',env[3])['issues'] if i['id']==env[4])
    proposal={'issue_id':env[4],'expected_version':version,'requested_status':'resolved','criterion_id':'access_and_user_verification','evidence_refs':[read['evidence'][0]['id']]}
    with pytest.raises(DomainError,match='PRECONDITION_FAILED'):
        domain.apply_issue_updates('alice.sales',env[3],[proposal])
    message=domain.append_message('alice.sales',env[3],'user','Grafana恢复了','feedback')
    batch={'message_intents':['progress_report'],'new_issue_candidates':[], 'observations':[{'candidate_key':'r','target_refs':[env[4]],'category':'reported_recovery','claim':'recovered','quote':'恢复了','mapping_status':'tentative'}], 'corrections':[], 'relation_candidates':[], 'unresolved_references':[]}
    result=domain.apply_candidates('alice.sales',env[3],message['id'],batch,domain.snapshot('alice.sales',env[3])['conversation']['version'])
    proposal['evidence_refs'].append(result['observations'][0]['id'])
    proposal['expected_version']=next(i['version'] for i in domain.snapshot('alice.sales',env[3])['issues'] if i['id']==env[4])
    domain.apply_issue_updates('alice.sales',env[3],[proposal])
    issues={i['id']:i['status'] for i in domain.snapshot('alice.sales',env[3])['issues']}
    assert issues[env[4]]=='resolved' and issues[other]=='investigating'


def test_handoff_failure_rolls_back_and_saved_handoff_stops_repairs(env):
    store,domain,_,chat,issue,_=env
    with store.write() as c:
        c.execute("CREATE TRIGGER fail_handoff BEFORE INSERT ON handoffs BEGIN SELECT RAISE(ABORT,'disk simulation'); END")
    args=dict(issue_refs=[issue],summary='Needs expert',facts=[],hypotheses=[],attempt_refs=[],evidence_refs=[],open_questions=['Cause?'],reason='Unsupported operation',team_hint='invented-team')
    failed=call(env,'create_handoff',**args)
    assert not failed['ok']
    assert domain.snapshot('alice.sales',chat)['issues'][0]['status']=='investigating'
    with store.write() as c: c.execute('DROP TRIGGER fail_handoff')
    result=call(env,'create_handoff',**args)
    assert result['data']['handoff']['team']=='general_it'
    assert result['data']['handoff']['simulated'] is True
    assert not call(env,'unlock_account',issue_id=issue,service_id='okta')['ok']


def test_concurrent_same_write_one_receipt_and_connections_close(env):
    def grant(_): return call(env,'grant_catalog_access',issue_id=env[4],resource_id='grafana.sales',permission='read')
    with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(grant,range(8)))
    assert all(r['ok'] for r in results)
    assert len({r['action_id'] for r in results})==1
    with env[0].read() as c: assert c.execute('SELECT COUNT(*) FROM actions').fetchone()[0]==1
    env[0].db_path.unlink()
    assert not env[0].db_path.exists()


def test_expired_confirmation_renews_without_reusing_old_authority(env):
    issue=env[1].create_issue('alice.sales',env[3],'VPN',['vpn'],criterion_id='vpn_recovery')['id']
    first=call(env,'reset_vpn_session',issue_id=issue,session_id='vpn.alice.sales')['data']['confirmation']
    env[5][0]+=601
    with pytest.raises(DomainError,match='EXPIRED'):
        env[1].decide_confirmation('alice.sales',first['id'],'confirmed')
    second=call(env,'reset_vpn_session',issue_id=issue,session_id='vpn.alice.sales')['data']['confirmation']
    assert second['id']!=first['id']
    assert not call(env,'reset_vpn_session',issue_id=issue,session_id='vpn.alice.sales',confirmation_id=first['id'])['ok']
    env[1].decide_confirmation('alice.sales',second['id'],'confirmed')
    assert call(env,'reset_vpn_session',issue_id=issue,session_id='vpn.alice.sales',confirmation_id=second['id'])['ok']


def test_cancelled_approval_and_inactive_requester_cannot_execute(env):
    domain=env[1];issue=domain.create_issue('alice.sales',env[3],'Production',['snowflake'],criterion_id='precise_access',resource_id='snowflake.prod.analytics')['id']
    approval=call(env,'request_approval',issue_id=issue,resource_id='snowflake.prod.analytics',permission='read',reason='Need it',evidence_refs=[])['data']['approval']
    domain.decide_approval('pat.approver',approval['id'],'approved')
    domain.decide_approval('alice.sales',approval['id'],'cancelled')
    assert call(env,'grant_catalog_access',issue_id=issue,resource_id='snowflake.prod.analytics',permission='read',approval_id=approval['id'])['error']['code']=='APPROVAL_REQUIRED'
    with env[0].write() as c:c.execute("UPDATE users SET active=0 WHERE id='alice.sales'")
    assert call(env,'inspect_account',service_id='okta')['error']['code']=='FORBIDDEN'


def test_guidance_requires_current_reviewed_remedy_and_limits_distinct_paths(env):
    domain=env[1];issue=domain.create_issue('alice.sales',env[3],'Login',['okta'],criterion_id='login_recovery')['id']
    evidence=domain.persist_evidence('alice.sales',env[3],'kb','kb.account-browser',1,{'metadata':{'id':'kb.account-browser'}},{'service_id':'okta'})
    attempt=domain.record_attempt('alice.sales',env[3],issue,'browser_private_window',{},[evidence['id']])
    assert attempt['remedy_id']=='browser_private_window'
    assert domain.record_attempt('alice.sales',env[3],issue,'browser_private_window',{},[evidence['id']])['id']==attempt['id']
    with pytest.raises(DomainError,match='NO_APPLICABLE_EVIDENCE'):
        domain.record_attempt('alice.sales',env[3],issue,'invented_shell_command',{},[evidence['id']])
    # Actual low-risk repair still requires a new piece of evidence after guidance.
    assert call(env,'unlock_account',issue_id=issue,service_id='okta')['error']['code']=='NO_APPLICABLE_EVIDENCE'
    read=call(env,'inspect_account',service_id='okta')
    assert call(env,'unlock_account',issue_id=issue,service_id='okta',evidence_refs=[read['evidence'][0]['id']])['ok']
    security=domain.persist_evidence('alice.sales',env[3],'kb','kb.account-security',1,{}, {'service_id':'okta'})
    with pytest.raises(DomainError,match='ATTEMPT_LIMIT'):
        domain.record_attempt('alice.sales',env[3],issue,'handoff_security',{},[security['id']])


def test_refinement_cannot_weaken_acceptance_or_change_authorized_scope(env):
    domain=env[1];issue=domain.snapshot('alice.sales',env[3])['issues'][0]
    with pytest.raises(DomainError,match='FORBIDDEN'):
        domain.refine_issue('alice.sales',env[3],issue['id'],issue['version'],criterion_id='precise_access')
    refined=domain.refine_issue('alice.sales',env[3],issue['id'],issue['version'],resource_id='grafana.sales')
    assert refined['resource_id']=='grafana.sales'
    call(env,'grant_catalog_access',issue_id=issue['id'],resource_id='grafana.sales',permission='read')
    version=domain.snapshot('alice.sales',env[3])['issues'][0]['version']
    with pytest.raises(DomainError,match='FORBIDDEN'):
        domain.refine_issue('alice.sales',env[3],issue['id'],version,resource_id='grafana.data')


def test_event_dedupe_and_lease_recovery_do_not_cross_chats(env):
    domain=env[1]
    event=domain.enqueue_event('alice.sales',env[3],'resume',{},'unique-resume')
    other=domain.create_conversation('bo.remote')['id']
    with pytest.raises(DomainError,match='CONFLICT'):
        domain.enqueue_event('bo.remote',other,'resume',{},'unique-resume')
    claimed=domain.claim_event('crashed',lease_seconds=10)
    assert claimed['id']==event['id']
    assert domain.claim_event('other') is None
    env[5][0]+=11
    assert domain.claim_event('recovery')['id']==event['id']
    with pytest.raises(DomainError,match='CONFLICT'):domain.finish_event(event['id'],'crashed')
    domain.finish_event(event['id'],'recovery')


def test_all_read_tools_provenance_retrieval_unavailable_and_no_match(env):
    for name,args in [('get_employee_context',{}),('inspect_account',{'service_id':'okta'}),('inspect_access',{'resource_id':'grafana.sales'}),('inspect_vpn',{}),('get_service_context',{'service_id':'salesforce'}),('get_action_requirements',{}),('read_conversation_context',{})]:
        result=call(env,name,**args)
        assert result['ok'] and result['evidence'],(name,result)
    assert call(env,'search_kb',query='VPN')['error']['code']=='TEMPORARY_UNAVAILABLE'
    seen=[]
    def search(query,source_type='kb',filters=None,top_k=5):
        seen.append((query,source_type,filters,top_k))
        return {'hits':[] if query=='nothing' else [{'id':'kb.vpn-session','revision':1,'service_id':'vpn','text':'Confirm interruption before resetting.'}],'degraded':True,'mode':'lexical'}
    tools=ToolExecutor(env[1],search=search)
    result=tools.call('search_kb',{'query':'VPN','filters':{'service_id':'vpn'}},'alice.sales',env[3])
    assert result['evidence'][0]['source']=='kb' and result['data']['degraded'] is True
    assert seen[0]==('VPN','kb',{'service_id':'vpn'},5)
    assert tools.call('search_history',{'query':'nothing'},'alice.sales',env[3])['error']['code']=='NO_MATCH'


def test_cross_chat_candidates_and_invented_quote_roll_back_all_new_issues(env):
    domain=env[1];other=domain.create_conversation('bo.remote')['id'];foreign=domain.create_issue('bo.remote',other,'VPN',['vpn'])['id']
    message=domain.append_message('alice.sales',env[3],'user','New VPN problem','new-message')
    batch={'message_intents':['new_request'],'new_issue_candidates':[{'candidate_key':'n','goal':'VPN','quote':'VPN','service_refs':['vpn']}],
           'observations':[{'candidate_key':'o','target_refs':[foreign],'category':'symptom','claim':'failure','quote':'problem','mapping_status':'tentative'}],
           'corrections':[],'relation_candidates':[],'unresolved_references':[]}
    version=domain.snapshot('alice.sales',env[3])['conversation']['version']
    with pytest.raises(DomainError,match='FORBIDDEN'):domain.apply_candidates('alice.sales',env[3],message['id'],batch,version)
    assert len(domain.snapshot('alice.sales',env[3])['issues'])==1
    batch['observations'][0]['target_refs']=['n'];batch['observations'][0]['quote']='invented text'
    with pytest.raises(DomainError,match='Quote'):domain.apply_candidates('alice.sales',env[3],message['id'],batch,version)
    assert len(domain.snapshot('alice.sales',env[3])['issues'])==1


def test_receipt_persistence_failure_rolls_back_account_mutation(env):
    issue=env[1].create_issue('alice.sales',env[3],'Unlock',['okta'],criterion_id='account_unlock')['id']
    with env[0].write() as c:c.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON actions BEGIN SELECT RAISE(ABORT,'receipt unavailable'); END")
    assert not call(env,'unlock_account',issue_id=issue,service_id='okta')['ok']
    account=call(env,'inspect_account',service_id='okta')['data']
    assert account['locked'] is True and account['unlock_count']==0
    with env[0].write() as c:c.execute('DROP TRIGGER fail_receipt')
    assert call(env,'unlock_account',issue_id=issue,service_id='okta')['ok']


def test_recovery_feedback_versions_reopen_only_its_issue(env):
    domain=env[1]
    message=domain.append_message('alice.sales',env[3],'user','Grafana still broken','negative')
    with env[0].write() as c:c.execute("UPDATE issues SET status='resolved' WHERE id=?",(env[4],))
    prior=domain.snapshot('alice.sales',env[3])['issues'][0]['version']
    batch={'message_intents':['progress_report'],'new_issue_candidates':[], 'observations':[{'candidate_key':'failure','target_refs':[env[4]],'category':'reported_failure','claim':'Still broken','quote':'still broken','mapping_status':'tentative'}],'corrections':[],'relation_candidates':[],'unresolved_references':[]}
    domain.apply_candidates('alice.sales',env[3],message['id'],batch,domain.snapshot('alice.sales',env[3])['conversation']['version'])
    issue=domain.snapshot('alice.sales',env[3])['issues'][0]
    assert issue['status']=='investigating' and issue['lifecycle']==2 and issue['version']>prior
    newer=domain.append_message('alice.sales',env[3],'user','Broken again','negative-2')
    batch['observations'][0].update(quote='Broken',candidate_key='failure2')
    domain.apply_candidates('alice.sales',env[3],newer['id'],batch,domain.snapshot('alice.sales',env[3])['conversation']['version'])
    assert domain.snapshot('alice.sales',env[3])['issues'][0]['version']>issue['version']


@pytest.mark.parametrize('replace',[
    {'message_intents':[{'approved':True}]},
    {'observations':[{'candidate_key':'bad','target_refs':[{}],'category':'symptom','claim':'bad','quote':'test','mapping_status':'tentative'}]},
    {'new_issue_candidates':[{'candidate_key':'bad','goal':'x','quote':'test','service_refs':[{}]}]},
])
def test_malformed_candidate_values_are_controlled_errors(env,replace):
    domain=env[1];message=domain.append_message('alice.sales',env[3],'user','test','malformed')
    batch={'message_intents':[],'new_issue_candidates':[],'observations':[],'corrections':[],'relation_candidates':[],'unresolved_references':[]};batch.update(replace)
    with pytest.raises(DomainError,match='INVALID_ARGUMENT'):
        domain.apply_candidates('alice.sales',env[3],message['id'],batch,domain.snapshot('alice.sales',env[3])['conversation']['version'])


def test_debug_run_scope_and_terminal_states_are_protected(env):
    domain=env[1]
    event=domain.enqueue_event('alice.sales',env[3],'resume',{},'run-event')
    run=domain.create_run('alice.sales',env[3],event['id'],{'trace':{'api_key':'hidden','reasoning_content':'hidden','safe':'visible'}})
    assert domain.update_run('alice.sales',run['id'],'running',expected_version=1)['version']==2
    with pytest.raises(DomainError,match='STALE_STATE'):domain.update_run('alice.sales',run['id'],'completed',expected_version=1)
    call(env,'grant_catalog_access',issue_id=env[4],resource_id='grafana.sales',permission='read')
    debug=domain.debug_snapshot('dev.debug',run['id'])
    assert debug['actions'] and debug['tool_calls'] and debug['runs']
    assert 'hidden' not in str(debug)
    with env[0].write() as c:c.execute("UPDATE issues SET status='cancelled' WHERE id=?",(env[4],))
    issue=domain.snapshot('alice.sales',env[3])['issues'][0]
    with pytest.raises(DomainError,match='FORBIDDEN'):
        domain.apply_issue_updates('alice.sales',env[3],[{'issue_id':issue['id'],'expected_version':issue['version'],'requested_status':'investigating','criterion_id':issue['acceptance_criteria_id'],'evidence_refs':[]}])


def feedback(env,issue,content,key,category='reported_recovery'):
    domain=env[1];message=domain.append_message('alice.sales',env[3],'user',content,key)
    batch={'message_intents':['progress_report'],'new_issue_candidates':[],'observations':[{'candidate_key':'feedback','target_refs':[issue],'category':category,'claim':content,'quote':content,'mapping_status':'tentative'}],'corrections':[],'relation_candidates':[],'unresolved_references':[]}
    return domain.apply_candidates('alice.sales',env[3],message['id'],batch,domain.snapshot('alice.sales',env[3])['conversation']['version'])['observations'][0]['id']


def proposal(env,issue,refs,status='resolved'):
    current=next(i for i in env[1].snapshot('alice.sales',env[3])['issues'] if i['id']==issue)
    return {'issue_id':issue,'expected_version':current['version'],'requested_status':status,'criterion_id':current['acceptance_criteria_id'],'evidence_refs':refs}


def test_feedback_before_action_cannot_verify_later_repair(env):
    early=feedback(env,env[4],'Grafana working','early')
    call(env,'grant_catalog_access',issue_id=env[4],resource_id='grafana.sales',permission='read')
    access=call(env,'inspect_access',resource_id='grafana.sales')['evidence'][0]['id']
    with pytest.raises(DomainError,match='PRECONDITION_FAILED'):
        env[1].apply_issue_updates('alice.sales',env[3],[proposal(env,env[4],[access,early])])
    later=feedback(env,env[4],'Grafana works now','later')
    env[1].apply_issue_updates('alice.sales',env[3],[proposal(env,env[4],[access,later])])


def test_vpn_acceptance_requires_full_window_fresh_query_and_later_feedback(env):
    domain=env[1];issue=domain.create_issue('alice.sales',env[3],'VPN',['vpn'],criterion_id='vpn_recovery')['id']
    cid=call(env,'reset_vpn_session',issue_id=issue,session_id='vpn.alice.sales')['data']['confirmation']['id']
    domain.decide_confirmation('alice.sales',cid,'confirmed')
    call(env,'reset_vpn_session',issue_id=issue,session_id='vpn.alice.sales',confirmation_id=cid)
    early=call(env,'inspect_vpn')['evidence'][0]['id']
    recovered=feedback(env,issue,'VPN stable','vpn-feedback')
    with pytest.raises(DomainError,match='PRECONDITION_FAILED'):
        domain.apply_issue_updates('alice.sales',env[3],[proposal(env,issue,[early,recovered])])
    env[5][0]+=901
    with pytest.raises(DomainError,match='PRECONDITION_FAILED'):
        domain.apply_issue_updates('alice.sales',env[3],[proposal(env,issue,[early,recovered])])
    fresh=call(env,'inspect_vpn')['evidence'][0]['id']
    domain.apply_issue_updates('alice.sales',env[3],[proposal(env,issue,[fresh,recovered])])


def test_application_acceptance_rechecks_actual_current_service_state(env):
    domain=env[1];issue=domain.create_issue('alice.sales',env[3],'Jenkins timeouts',['jenkins'])['id']
    old=call(env,'get_service_context',service_id='jenkins')['evidence'][0]['id']
    obs=feedback(env,issue,'Jenkins working','jenkins-feedback')
    with env[0].write() as c:
        row=c.execute("SELECT payload FROM world_records WHERE kind='services' AND id='jenkins'").fetchone()
        payload=json.loads(row[0]);payload['status']='degraded';payload['revision']=2
        c.execute("UPDATE world_records SET revision=2,payload=? WHERE kind='services' AND id='jenkins'",(json.dumps(payload),))
    with pytest.raises(DomainError,match='PRECONDITION_FAILED'):
        domain.apply_issue_updates('alice.sales',env[3],[proposal(env,issue,[old,obs])])


def test_message_and_event_submit_are_atomic_and_idempotent(env):
    domain=env[1]
    with env[0].write() as c:c.execute("CREATE TRIGGER fail_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'queue unavailable'); END")
    with pytest.raises(sqlite3.Error):domain.submit_user_message('alice.sales',env[3],'Help me','client-1')
    assert domain.snapshot('alice.sales',env[3])['messages']==[]
    with env[0].write() as c:c.execute('DROP TRIGGER fail_event')
    first=domain.submit_user_message('alice.sales',env[3],'Help me','client-1')
    second=domain.submit_user_message('alice.sales',env[3],'Help me','client-1')
    assert first==second
    assert first['event']['message_id']==first['message']['id']
    assert len(domain.snapshot('alice.sales',env[3])['messages'])==1
    assert len(domain.list_events('alice.sales',env[3]))==1


def test_regional_incident_only_waits_when_actual_scope_matches(env):
    domain=env[1]
    own=domain.create_issue('alice.sales',env[3],'Salesforce slow',['salesforce'])['id']
    evidence=call(env,'get_service_context',service_id='salesforce')['evidence'][0]['id']
    assert domain.apply_issue_updates('alice.sales',env[3],[proposal(env,own,[evidence],'waiting_external')])[0]['status']=='waiting_external'
    chat=domain.create_conversation('bo.remote')['id'];issue=domain.create_issue('bo.remote',chat,'Slow Salesforce',['salesforce'])
    result=env[2].call('get_service_context',{'service_id':'salesforce'},'bo.remote',chat)
    with pytest.raises(DomainError,match='PRECONDITION_FAILED'):
        domain.apply_issue_updates('bo.remote',chat,[{'issue_id':issue['id'],'expected_version':issue['version'],'requested_status':'waiting_external','criterion_id':issue['acceptance_criteria_id'],'evidence_refs':[result['evidence'][0]['id']]}])


@pytest.mark.parametrize('decision,approve_first', [('rejected',False),('cancelled',False),('cancelled',True)])
def test_terminal_approval_request_replay_preserves_decision(env,decision,approve_first):
    domain=env[1]
    issue=domain.create_issue('alice.sales',env[3],'Production read',['snowflake'],criterion_id='precise_access',resource_id='snowflake.prod.analytics')['id']
    args={'issue_id':issue,'resource_id':'snowflake.prod.analytics','permission':'read','reason':'Business report','evidence_refs':[]}
    original=call(env,'request_approval',**args)['data']['approval']
    if approve_first:domain.decide_approval('pat.approver',original['id'],'approved')
    actor='pat.approver' if decision=='rejected' else 'alice.sales'
    decided=domain.decide_approval(actor,original['id'],decision)
    for elapsed in (0,90000):
        env[5][0]+=elapsed
        repeated=call(env,'request_approval',**args)
        assert repeated['ok']
        assert repeated['data']['approval']['id']==original['id']
        assert repeated['data']['approval']['status']==decision
        assert repeated['data']['approval']['revision']==decided['revision']
    snapshot=domain.snapshot('alice.sales',env[3])
    assert len(snapshot['approvals'])==1
    assert len(snapshot['events'])==1+int(approve_first)
    assert call(env,'inspect_access',resource_id='snowflake.prod.analytics')['data']['permissions']==[]
    # A deliberately distinct issue/request may request the same resource again.
    fresh=domain.create_issue('alice.sales',env[3],'New production request',['snowflake'],criterion_id='precise_access',resource_id='snowflake.prod.analytics')['id']
    renewed=call(env,'request_approval',**{**args,'issue_id':fresh})['data']['approval']
    assert renewed['id']!=original['id'] and renewed['status']=='pending'


def correction_batch(observation_id,claim,quote):
    return {'message_intents':['correction'],'new_issue_candidates':[],'observations':[],
            'corrections':[{'candidate_key':'retract','observation_id':observation_id,'expected_version':1,'claim':claim,'quote':quote}],
            'relation_candidates':[],'unresolved_references':[]}


def test_recovery_retraction_reopens_only_corrected_resolved_issue(env):
    domain=env[1]
    assert call(env,'grant_catalog_access',issue_id=env[4],resource_id='grafana.sales',permission='read')['ok']
    access=call(env,'inspect_access',resource_id='grafana.sales')['evidence'][0]['id']
    recovered=feedback(env,env[4],'Grafana is working','resolved-feedback')
    domain.apply_issue_updates('alice.sales',env[3],[proposal(env,env[4],[access,recovered])])
    other=domain.create_issue('alice.sales',env[3],'Unlock account',['okta'],criterion_id='account_unlock')['id']
    assert call(env,'unlock_account',issue_id=other,service_id='okta')['ok']
    account=call(env,'inspect_account',service_id='okta')['evidence'][0]['id']
    domain.apply_issue_updates('alice.sales',env[3],[proposal(env,other,[account])])
    before=domain.snapshot('alice.sales',env[3])
    assert before['all_resolved'] is True
    original={i['id']:i for i in before['issues']}
    message=domain.append_message('alice.sales',env[3],'user','Correction: Grafana is still broken','retraction')
    batch=correction_batch(recovered,'The recovery report was incorrect','Grafana is still broken')
    result=domain.apply_candidates('alice.sales',env[3],message['id'],batch,domain.snapshot('alice.sales',env[3])['conversation']['version'])
    after=domain.snapshot('alice.sales',env[3]);issues={i['id']:i for i in after['issues']}
    assert issues[env[4]]['status']=='investigating'
    assert issues[env[4]]['lifecycle']==original[env[4]]['lifecycle']+1
    assert issues[env[4]]['version']>original[env[4]]['version']
    assert issues[other]==original[other]
    assert after['all_resolved'] is False
    observations={o['id']:o for o in after['observations']}
    assert observations[recovered]['status']=='superseded'
    assert result['corrections'][0]['source_kind']=='user_report'
    assert result['corrections'][0]['message_id']==message['id']
    assert domain.apply_candidates('alice.sales',env[3],message['id'],batch,before['conversation']['version'])==result
    assert domain.snapshot('alice.sales',env[3])['issues']==after['issues']


def test_recovery_retraction_preserves_saved_handoff_terminal_state(env):
    domain=env[1]
    reported=feedback(env,env[4],'Grafana recovered','handoff-feedback')
    handoff=call(env,'create_handoff',issue_refs=[env[4]],summary='Employee requested expert review',facts=[],hypotheses=[],attempt_refs=[],evidence_refs=[],open_questions=['Verify recurrence'],reason='Employee requested handoff',team_hint='general_it')
    assert handoff['ok']
    before=domain.snapshot('alice.sales',env[3])['issues'][0]
    message=domain.append_message('alice.sales',env[3],'user','Actually Grafana is still broken','handoff-retraction')
    batch=correction_batch(reported,'Recovery report retracted','Grafana is still broken')
    domain.apply_candidates('alice.sales',env[3],message['id'],batch,domain.snapshot('alice.sales',env[3])['conversation']['version'])
    after=domain.snapshot('alice.sales',env[3])['issues'][0]
    assert after['status']=='handed_off' and after['lifecycle']==before['lifecycle']


def test_reopened_lifecycle_requires_its_own_precise_approval(env):
    domain=env[1]
    issue=domain.create_issue('alice.sales',env[3],'Access production catalog',['snowflake'],criterion_id='access_and_user_verification',resource_id='snowflake.prod.analytics')['id']
    args={'issue_id':issue,'resource_id':'snowflake.prod.analytics','permission':'read','reason':'Catalog access','evidence_refs':[]}
    prior=call(env,'request_approval',**args)['data']['approval']
    domain.decide_approval('pat.approver',prior['id'],'approved')
    grant={k:v for k,v in args.items() if k not in ('reason','evidence_refs')}
    assert call(env,'grant_catalog_access',**grant,approval_id=prior['id'])['ok']
    access=call(env,'inspect_access',resource_id=args['resource_id'])['evidence'][0]['id']
    recovered=feedback(env,issue,'Production access works','prod-recovery')
    domain.apply_issue_updates('alice.sales',env[3],[proposal(env,issue,[access,recovered])])
    message=domain.append_message('alice.sales',env[3],'user','Actually production access is still broken','prod-correction')
    batch=correction_batch(recovered,'Recovery report retracted','production access is still broken')
    domain.apply_candidates('alice.sales',env[3],message['id'],batch,domain.snapshot('alice.sales',env[3])['conversation']['version'])
    fresh=call(env,'request_approval',**args)['data']['approval']
    assert fresh['id']!=prior['id'] and fresh['status']=='pending' and fresh['lifecycle']==2
    stale=call(env,'grant_catalog_access',**grant,approval_id=prior['id'])
    assert stale['ok'] is False
    assert stale['error']['code']=='APPROVAL_REQUIRED'
    domain.decide_approval('pat.approver',fresh['id'],'approved')
    assert call(env,'grant_catalog_access',**grant,approval_id=fresh['id'])['ok']
