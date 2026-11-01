"""Policy-controlled tool execution with atomic mock effects and durable receipts."""
import json
import sqlite3
from app.catalog import TOOL_SCHEMAS, validate_arguments
from app.domain import CRITERIA, TEAM_DIRECTORY, DomainError, digest, new_id, require, utc
from app.storage import dumps, unpack


POLICY_VERSION='2026.11.1'


class ToolExecutor:
    def __init__(self,domain,search=None,adapter=None):
        self.domain=domain
        self.store=domain.store
        self.search=search
        self.adapter=adapter

    @property
    def schemas(self):
        return TOOL_SCHEMAS

    def _envelope(self,ok,data=None,evidence=None,action_id=None,error=None):
        return {'ok':ok,'data':data or {},'evidence':evidence or [],'observed_at':utc(self.store.clock()),'action_id':action_id,'error':error}

    def _failure(self,code,message,data=None,action_id=None):
        uncertain=code=='EXECUTION_UNCERTAIN'
        return self._envelope(False,data,action_id=action_id,error={'code':code,'message':message,'retryable':code in ('TEMPORARY_UNAVAILABLE','TIMEOUT'),
                              'execution_status':'uncertain' if uncertain else 'not_executed'})

    def call(self,name,arguments,actor_id,conversation_id,*,before_effect=None):
        call_id=new_id('TOOL');new_action=False
        try:
            validate_arguments(name,arguments)
            with self.store.read() as c:self.domain._conversation(c,actor_id,conversation_id)
            search_result=None
            if name in ('search_kb','search_history'):
                require(self.search is not None,'TEMPORARY_UNAVAILABLE','Retrieval service is not configured')
                try:
                    search_result=self.search(arguments['query'],source_type='kb' if name=='search_kb' else 'history',filters=arguments.get('filters'),top_k=arguments.get('top_k',5))
                except TimeoutError:raise DomainError('TIMEOUT','Retrieval service timed out') from None
                except DomainError:raise
                except Exception:raise DomainError('TEMPORARY_UNAVAILABLE','Retrieval service unavailable') from None
            with self.store.write() as c:
                self.domain._conversation(c,actor_id,conversation_id)
                if before_effect is not None:before_effect()
                result,new_action=self._dispatch(c,name,arguments,actor_id,conversation_id,search_result)
                c.execute('INSERT INTO tool_calls VALUES(?,?,?,?,?)',(call_id,conversation_id,actor_id,self.store.clock(),dumps({'name':name,'arguments':arguments,'result':result})))
            if new_action and self.adapter is not None:
                try:self.adapter.after_commit(result['data'])
                except Exception:
                    result=self._failure('EXECUTION_UNCERTAIN','Action response lost; query the same action_id before any new write',{'action_id':result['action_id']},result['action_id'])
                    with self.store.write() as c:
                        c.execute('UPDATE tool_calls SET payload=? WHERE id=?',(dumps({'name':name,'arguments':arguments,'result':result}),call_id))
            return result
        except DomainError as exc:
            result=self._failure(exc.code,exc.message,exc.data)
        except (sqlite3.Error,OSError):
            result=self._failure('TEMPORARY_UNAVAILABLE','Business storage could not complete this request')
        # Failed authorized calls are auditable, but guesses of someone else's chat create no records.
        try:
            with self.store.write() as c:
                self.domain._conversation(c,actor_id,conversation_id)
                c.execute('INSERT OR IGNORE INTO tool_calls VALUES(?,?,?,?,?)',(call_id,conversation_id,actor_id,self.store.clock(),dumps({'name':name,'arguments':arguments,'result':result})))
        except (DomainError,sqlite3.Error,OSError):pass
        return result

    def _read(self,c,actor,chat,source,record,revision,data,scope=None):
        evidence=self.domain._evidence(c,actor,chat,source,record,revision,data,scope or {'actor_id':actor})
        return self._envelope(True,data,[evidence]),False

    def _world(self,c,kind):
        return [unpack(r) for r in c.execute('SELECT * FROM world_records WHERE kind=? ORDER BY id',(kind,))]

    @staticmethod
    def _approval_scope(actor,approvals):
        return {'actor_id':actor,'approval_ids':[r['id'] for r in approvals],
                'approval_revisions':{r['id']:str(r['revision']) for r in approvals},
                'approval_scopes':{r['id']:{k:r[k] for k in ('issue_id','resource_id','permission')} for r in approvals}}

    def _resource(self,c,rid):
        resource=unpack(c.execute('SELECT * FROM resources WHERE id=?',(rid,)).fetchone())
        require(resource is not None,'NOT_FOUND','Unknown catalog resource')
        return resource

    def _scope(self,c,actor,chat,issue_id,service_id,resource_id=None):
        issue=self.domain._issue(c,actor,chat,issue_id,True)
        require(service_id in issue['service_refs'],'FORBIDDEN','Action service differs from issue scope')
        if resource_id:require(issue.get('resource_id') in (None,resource_id),'FORBIDDEN','Action resource differs from issue scope')
        return issue

    def _eligible(self,c,actor,resource,permission):
        user=self.domain._actor(c,actor)
        require(resource['active'] and permission=='read' and permission in resource['permissions'],'FORBIDDEN','Only active catalog read permissions are supported')
        require(user['department'] in resource['eligible_departments'],'FORBIDDEN','Department is not eligible for this resource')
        return user

    def _dispatch(self,c,name,a,actor,chat,search_result=None):
        if name=='get_employee_context':
            user=self.domain._actor(c,actor)
            data={'employee':user,'resources':[unpack(r) for r in c.execute('SELECT * FROM resources ORDER BY id')],
                  'permissions':[dict(r) for r in c.execute('SELECT resource_id,permission,revision FROM access_grants WHERE actor_id=?',(actor,))]}
            return self._read(c,actor,chat,'directory',actor,1,data)
        if name=='inspect_account':
            account=unpack(c.execute('SELECT * FROM accounts WHERE actor_id=? AND service_id=?',(actor,a['service_id'])).fetchone())
            require(account is not None,'NOT_FOUND','No linked account for this service')
            return self._read(c,actor,chat,'directory',account['id'],account['revision'],account,{'actor_id':actor,'service_id':a['service_id']})
        if name=='inspect_access':
            resource=self._resource(c,a['resource_id'])
            grants=[dict(r) for r in c.execute('SELECT permission,revision,action_id FROM access_grants WHERE actor_id=? AND resource_id=?',(actor,resource['id']))]
            data={'resource_id':resource['id'],'service_id':resource['service_id'],'permissions':sorted(g['permission'] for g in grants),'grants':grants}
            revision=f"{resource['revision']}:"+','.join(str(g['revision']) for g in grants)
            return self._read(c,actor,chat,'directory',resource['id'],revision,data,{'actor_id':actor,'resource_id':resource['id']})
        if name=='inspect_vpn':
            vpn=unpack(c.execute('SELECT * FROM vpn_sessions WHERE actor_id=?',(actor,)).fetchone());require(vpn is not None,'NOT_FOUND','No own VPN session')
            vpn['observation_seconds']=max(0,int(self.store.clock()-vpn['connected_since'])) if vpn['connected_since'] is not None else 0
            return self._read(c,actor,chat,'service_state',vpn['id'],vpn['revision'],vpn,{'actor_id':actor,'service_id':'vpn','session_id':vpn['id']})
        if name=='get_service_context':
            service=unpack(c.execute("SELECT * FROM world_records WHERE kind='services' AND id=?",(a['service_id'],)).fetchone());require(service is not None,'NOT_FOUND','Unknown service')
            sid=service['id'];data={'service':service,'incidents':[r for r in self._world(c,'incidents') if r['service_id']==sid],
                'changes':[r for r in self._world(c,'changes') if r['service_id']==sid or sid in r.get('related_service_ids',[])],
                'dependencies':[r for r in self._world(c,'dependencies') if r['service_id']==sid or r['depends_on']==sid],
                'as_of':self._world(c,'metadata')[0]['as_of'],'simulated':True}
            return self._read(c,actor,chat,'service_state',sid,service['revision'],data,{'service_id':sid,'regions':sorted({r['region'] for r in data['incidents']})})
        if name in ('search_kb','search_history'):
            require(isinstance(search_result,dict) and isinstance(search_result.get('hits'),list),'TEMPORARY_UNAVAILABLE','Malformed retrieval result')
            source='kb' if name=='search_kb' else 'history';evidence=[];hits=search_result['hits'][:a.get('top_k',5)]
            if not hits:raise DomainError('NO_MATCH','No applicable retrieval evidence',{'hits':[],'degraded':search_result.get('degraded',True),'mode':search_result.get('mode','lexical')})
            for hit in hits:
                require(isinstance(hit,dict) and ('record_id' in hit or 'id' in hit),'TEMPORARY_UNAVAILABLE','Retrieval hit lacks source identity')
                record=hit.get('record_id',hit.get('id'));revision=hit.get('revision',hit.get('metadata',{}).get('revision',1))
                scope={'service_id':hit.get('service_id',hit.get('metadata',{}).get('service_id')),'source_type':source}
                evidence.append(self.domain._evidence(c,actor,chat,source,record,revision,hit,scope))
            return self._envelope(True,{**search_result,'hits':hits},evidence),False
        if name=='get_action_requirements':
            policies=self._world(c,'policies')
            if 'action' in a:policies=[p for p in policies if p['action'] in (a['action'],'all','repair','resolve')]
            data={'policy_version':POLICY_VERSION,'policies':policies,'resources':[unpack(r) for r in c.execute('SELECT * FROM resources ORDER BY id')],
                  'acceptance_criteria':sorted(CRITERIA),'teams':TEAM_DIRECTORY,'max_distinct_remedies':2,
                  'actions':{'unlock_account':{'remedy_id':'unlock_account','confirmation':False},'grant_catalog_access':{'remedy_id':'grant_grafana_read or grant_production_read','permission':'read'},'reset_vpn_session':{'remedy_id':'reset_vpn_session','confirmation':True,'impact':'Disconnects this VPN session and reconnects; active VPN traffic is interrupted.'}}}
            return self._read(c,actor,chat,'policy','policy.catalog',POLICY_VERSION,data,{'actor_id':actor,'action':a.get('action','all')})
        if name=='get_approval':
            query='SELECT * FROM approvals WHERE actor_id=? AND conversation_id=?';params=[actor,chat]
            if 'approval_id' in a:query+=' AND id=?';params.append(a['approval_id'])
            if 'issue_id' in a:self.domain._issue(c,actor,chat,a['issue_id']);query+=' AND issue_id=?';params.append(a['issue_id'])
            approvals=[unpack(r) for r in c.execute(query,params)]
            require(bool(approvals),'NOT_FOUND','No own approval matching this request')
            for item in approvals:
                if item['status'] in ('pending','approved') and item['expires_at']<=self.store.clock():
                    c.execute("UPDATE approvals SET status='expired',revision=revision+1 WHERE id=?",(item['id'],));item['status']='expired';item['revision']+=1
            return self._read(c,actor,chat,'approval_event',a.get('approval_id','approvals:'+chat),max(r['revision'] for r in approvals),{'approvals':approvals},self._approval_scope(actor,approvals))
        if name=='get_action_result':
            action=unpack(c.execute('SELECT * FROM actions WHERE id=? AND actor_id=? AND conversation_id=?',(a['action_id'],actor,chat)).fetchone());require(action is not None,'NOT_FOUND','No own action matching this ID')
            result,_=self._read(c,actor,chat,'tool_receipt',action['id'],1,action,{'actor_id':actor,'issue_id':action['issue_id']});result['action_id']=action['id'];return result,False
        if name=='read_conversation_context':
            ids=a.get('message_ids',[])
            if ids:
                messages=[]
                for mid in ids:
                    row=c.execute('SELECT * FROM messages WHERE id=? AND conversation_id=?',(mid,chat)).fetchone();require(row is not None,'FORBIDDEN','Message unavailable');messages.append(dict(row))
            else:messages=[dict(r) for r in c.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?',(chat,a.get('limit',20)))][::-1]
            return self._read(c,actor,chat,'conversation',chat,len(messages),{'messages':messages},{'actor_id':actor,'conversation_id':chat})
        if name=='request_approval':return self._request_approval(c,a,actor,chat)
        if name=='create_handoff':return self._handoff(c,a,actor,chat)
        return self._action(c,name,a,actor,chat)

    def _request_approval(self,c,a,actor,chat):
        resource=self._resource(c,a['resource_id']);self._eligible(c,actor,resource,a['permission'])
        issue=self._scope(c,actor,chat,a['issue_id'],resource['service_id'],resource['id'])
        require(resource['approval_required'],'INVALID_ARGUMENT','This catalog resource does not require approval')
        self.domain._validate_refs(c,actor,chat,a['evidence_refs'],issue['id'])
        # A tool retry cannot erase a refusal, even after the old expiry time.
        # Expiry permits renewal only for an otherwise live logical request.
        old=c.execute("""SELECT * FROM approvals WHERE actor_id=? AND issue_id=? AND resource_id=? AND permission=?
            AND COALESCE(json_extract(payload,'$.lifecycle'),1)=? ORDER BY created_at DESC,rowid DESC LIMIT 1""",
            (actor,issue['id'],resource['id'],a['permission'],issue['lifecycle'])).fetchone()
        if old and (old['status'] in ('rejected','cancelled') or (old['status'] in ('pending','approved') and old['expires_at']>self.store.clock())):
            item=unpack(old)
        else:
            self.domain._require_clear_scope(c,actor,chat,issue['id'])
            aid=new_id('APPROVAL');payload={'reason':a['reason'],'evidence_refs':a['evidence_refs'],'policy_version':POLICY_VERSION,'resource_revision':resource['revision'],'approver_role':'resource_owner',
                'scope':{'requester':actor,'issue_id':issue['id'],'resource_id':resource['id'],'permission':a['permission']},'lifecycle':issue['lifecycle'],'simulated':True}
            c.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?,?)',(aid,chat,actor,issue['id'],resource['id'],a['permission'],'pending',1,self.store.clock(),self.store.clock()+86400,dumps(payload)))
            item=unpack(c.execute('SELECT * FROM approvals WHERE id=?',(aid,)).fetchone())
            self._bind_resource(c,issue,resource)
            c.execute("UPDATE issues SET status='waiting_approval',version=version+1 WHERE id=?",(issue['id'],))
        return self._read(c,actor,chat,'approval_event',item['id'],item['revision'],{'approval':item},self._approval_scope(actor,[item]))

    def _bind_resource(self,c,issue,resource):
        if not issue.get('resource_id'):
            payload=json.loads(c.execute('SELECT payload FROM issues WHERE id=?',(issue['id'],)).fetchone()[0]);payload['resource_id']=resource['id']
            c.execute('UPDATE issues SET payload=?,version=version+1 WHERE id=?',(dumps(payload),issue['id']))

    def _approval(self,c,approval_id,actor,issue,resource,permission):
        item=unpack(c.execute('SELECT * FROM approvals WHERE id=?',(approval_id,)).fetchone()) if approval_id else None
        require(item is not None and item['actor_id']==actor and item['conversation_id']==issue['conversation_id'] and item['issue_id']==issue['id'] and item['resource_id']==resource['id'] and item['permission']==permission and item['status']=='approved' and item['expires_at']>self.store.clock(), 'APPROVAL_REQUIRED','Current exact-scope resource-owner approval is required')
        require(item.get('lifecycle',1)==issue['lifecycle'],'APPROVAL_REQUIRED','Approval belongs to an earlier issue lifecycle')
        approver=self.domain._actor(c,item.get('decided_by'))
        require(item.get('policy_version')==POLICY_VERSION and item.get('resource_revision')==resource['revision'] and approver['id'] in resource['owner_ids'] and 'approver' in approver['roles'] and approver['id']!=actor,'APPROVAL_REQUIRED','Approval policy or ownership changed')
        return item

    def _action(self,c,name,a,actor,chat):
        # Normalize independently of model-provided approval and confirmation IDs.
        resource=None;account=None;vpn=None
        if name=='grant_catalog_access':
            resource=self._resource(c,a['resource_id']);self._eligible(c,actor,resource,a['permission'])
            issue=self._scope(c,actor,chat,a['issue_id'],resource['service_id'],resource['id'])
            params={'resource_id':resource['id'],'permission':a['permission']};remedy='grant_production_read' if resource['approval_required'] else 'grant_grafana_read'
        elif name=='unlock_account':
            issue=self._scope(c,actor,chat,a['issue_id'],a['service_id'])
            account=unpack(c.execute('SELECT * FROM accounts WHERE actor_id=? AND service_id=?',(actor,a['service_id'])).fetchone())
            require(account is not None,'NOT_FOUND','No own linked account')
            require(account['account_type']=='ordinary' and account['disabled_reason'] is None,'FORBIDDEN','Security-disabled accounts cannot be unlocked')
            params={'service_id':a['service_id'],'account_id':account['id']};remedy='unlock_account'
        elif name=='reset_vpn_session':
            issue=self._scope(c,actor,chat,a['issue_id'],'vpn')
            vpn=unpack(c.execute('SELECT * FROM vpn_sessions WHERE id=? AND actor_id=?',(a['session_id'],actor)).fetchone());require(vpn is not None,'FORBIDDEN','VPN session is not owned by employee')
            params={'session_id':vpn['id']};remedy='reset_vpn_session'
        else:raise DomainError('INVALID_ARGUMENT','Unknown action')
        refs=a.get('evidence_refs',[]);self.domain._validate_refs(c,actor,chat,refs,issue['id'])
        fingerprint=digest({'actor_id':actor,'conversation_id':chat,'issue_id':issue['id'],'lifecycle':issue['lifecycle'],'action':name,'parameters':params})
        old=unpack(c.execute('SELECT * FROM actions WHERE fingerprint=?',(fingerprint,)).fetchone())
        if old:
            result,_=self._read(c,actor,chat,'tool_receipt',old['id'],1,old,{'actor_id':actor,'issue_id':issue['id']});result['action_id']=old['id'];return result,False
        self.domain._require_clear_scope(c,actor,chat,issue['id'])
        approval=None;confirmation=None
        if resource and resource['approval_required']:approval=self._approval(c,a.get('approval_id'),actor,issue,resource,a['permission'])
        if vpn:
            confirmation=unpack(c.execute('SELECT * FROM confirmations WHERE fingerprint=? ORDER BY issued_at DESC,rowid DESC LIMIT 1',(fingerprint,)).fetchone())
            if confirmation is not None and confirmation['expires_at']<=self.store.clock() and confirmation['decision'] in ('pending','confirmed','expired'):
                c.execute("UPDATE confirmations SET decision='expired' WHERE id=?",(confirmation['id'],))
                confirmation=None
            if confirmation is None:
                cid=new_id('CONFIRM');payload={'target':vpn['id'],'impact':'Disconnects this VPN session and reconnects; active VPN traffic is interrupted.','parameters':params,'policy_version':POLICY_VERSION,'target_revision':vpn['revision'],'action':name}
                c.execute('INSERT INTO confirmations VALUES(?,?,?,?,?,?,?,?,?)',(cid,chat,actor,issue['id'],fingerprint,'pending',self.store.clock(),self.store.clock()+600,dumps(payload)))
                confirmation=unpack(c.execute('SELECT * FROM confirmations WHERE id=?',(cid,)).fetchone())
            if a.get('confirmation_id')!=confirmation['id'] or confirmation['decision']!='confirmed':
                return self._failure('CONFIRMATION_REQUIRED','Explicit confirmation is required for this session interruption',{'confirmation':confirmation}),False
            require(confirmation['target_revision']==vpn['revision'] and confirmation['policy_version']==POLICY_VERSION,'STALE_STATE','VPN session or policy changed after confirmation')
        prior=[unpack(r) for r in c.execute('SELECT * FROM attempts WHERE issue_id=?',(issue['id'],))]
        require(any(r['remedy_id']==remedy for r in prior) or len(prior)<2,'ATTEMPT_LIMIT','Two distinct repair paths already attempted')
        if prior and not any(r['remedy_id']==remedy for r in prior):
            previous_refs=set(ref for attempt in prior for ref in attempt.get('evidence_refs',[]))
            require(bool(set(refs)-previous_refs),'NO_APPLICABLE_EVIDENCE','A different repair path requires new evidence')
        aid=new_id('ACTION');receipt={'action_id':aid,'action':name,'actor_id':actor,'issue_id':issue['id'],'parameters':params,'policy_version':POLICY_VERSION,
            'approval_id':approval['id'] if approval else None,'confirmation_id':confirmation['id'] if confirmation else None,'committed_at':utc(self.store.clock()),'simulated':True}
        receipt['after_message_sequence']=c.execute('SELECT COALESCE(MAX(rowid),0) FROM messages WHERE conversation_id=?',(chat,)).fetchone()[0]
        if resource:
            self._bind_resource(c,issue,resource)
            c.execute('INSERT INTO access_grants VALUES(?,?,?,?,?) ON CONFLICT(actor_id,resource_id,permission) DO NOTHING',(actor,resource['id'],a['permission'],1,aid))
            receipt['effect']={'resource_id':resource['id'],'permission':a['permission'],'present':True}
        elif account:
            payload=json.loads(c.execute('SELECT payload FROM accounts WHERE id=?',(account['id'],)).fetchone()[0]);payload['locked']=False;payload['unlock_count']+=1
            c.execute('UPDATE accounts SET revision=revision+1,payload=? WHERE id=?',(dumps(payload),account['id']))
            receipt['effect']={'account_id':account['id'],'locked':False}
        elif vpn:
            payload=json.loads(c.execute('SELECT payload FROM vpn_sessions WHERE id=?',(vpn['id'],)).fetchone()[0]);payload.update(connected=True,connected_since=self.store.clock(),reset_count=vpn['reset_count']+1,observation_seconds=0)
            c.execute('UPDATE vpn_sessions SET revision=revision+1,payload=? WHERE id=?',(dumps(payload),vpn['id']))
            receipt['effect']={'session_id':vpn['id'],'reset_count':payload['reset_count'],'connected':True,'verified_recovery':False}
        c.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)',(aid,chat,actor,issue['id'],fingerprint,'succeeded',self.store.clock(),dumps(params),dumps(receipt)))
        attempt_id=new_id('ATTEMPT')
        c.execute('INSERT OR IGNORE INTO attempts VALUES(?,?,?,?,?,?)',(attempt_id,chat,issue['id'],remedy,self.store.clock(),dumps({'normalized_parameters':params,'evidence_refs':refs,'outcome':'succeeded','action_id':aid})))
        c.execute("UPDATE issues SET status='verifying',version=version+1 WHERE id=?",(issue['id'],))
        action=unpack(c.execute('SELECT * FROM actions WHERE id=?',(aid,)).fetchone())
        result,_=self._read(c,actor,chat,'tool_receipt',aid,1,action,{'actor_id':actor,'issue_id':issue['id']});result['action_id']=aid
        return result,True

    def _handoff(self,c,a,actor,chat):
        require(0<len(a['issue_refs'])<=8,'INVALID_ARGUMENT','Handoff needs owned issues')
        for iid in a['issue_refs']:self.domain._issue(c,actor,chat,iid)
        refs=self.domain._validate_refs(c,actor,chat,a['evidence_refs']+a['attempt_refs'])
        require(all(t=='attempts' for t,r in refs[len(a['evidence_refs']):]),'INVALID_ARGUMENT','Attempt references must be actual attempts')
        for item in a['facts']+a['hypotheses']:
            if isinstance(item,dict):
                require(set(item)<= {'claim','text','evidence_refs','support_refs','contradict_refs','status'},'INVALID_ARGUMENT','Unsupported handoff fact fields')
                for key in ('evidence_refs','support_refs','contradict_refs'):
                    if key in item:self.domain._validate_refs(c,actor,chat,item[key])
        # Facts and assumptions are retained separately; the tool does not certify their semantics.
        team=a['team_hint'] if a['team_hint'] in TEAM_DIRECTORY else 'general_it'
        payload={**a,'team':team,'simulated':True,'status':'simulated_handoff_saved'}
        fingerprint=digest({'actor':actor,'chat':chat,'issues':sorted(a['issue_refs']),'summary':a['summary'],'reason':a['reason']})
        old=unpack(c.execute('SELECT * FROM handoffs WHERE fingerprint=?',(fingerprint,)).fetchone())
        if old:item=old
        else:
            hid=new_id('HANDOFF');c.execute('INSERT INTO handoffs VALUES(?,?,?,?,?,?)',(hid,chat,actor,fingerprint,self.store.clock(),dumps(payload)))
            for iid in a['issue_refs']:c.execute("UPDATE issues SET status='handed_off',version=version+1 WHERE id=?",(iid,))
            item=unpack(c.execute('SELECT * FROM handoffs WHERE id=?',(hid,)).fetchone())
        return self._read(c,actor,chat,'tool_receipt',item['id'],1,{'handoff':item},{'actor_id':actor,'issue_refs':a['issue_refs']})
