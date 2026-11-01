"""Owned state and validated candidate/decision services; no model gets SQL access."""
from datetime import datetime, timezone
import hashlib
import json
import uuid

from app.storage import dumps, unpack


def new_id(prefix):
    return f'{prefix}-{uuid.uuid4().hex}'


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def utc(timestamp):
    return datetime.fromtimestamp(timestamp,timezone.utc).isoformat()


class DomainError(Exception):
    def __init__(self, code, message='', data=None):
        super().__init__(f'{code}: {message}')
        self.code, self.message, self.data = code, message, data or {}


def require(condition, code='INVALID_ARGUMENT', message='Invalid request'):
    if not condition:
        raise DomainError(code,message)


def fields(value, allowed, required=()):
    require(isinstance(value,dict) and set(value)<=set(allowed) and set(required)<=set(value))


def text_value(value, maximum=8000):
    require(isinstance(value,str) and 0<len(value.strip())<=maximum)
    return value


STATUSES={'investigating','waiting_user','waiting_approval','waiting_external','verifying','resolved','handed_off','denied','cancelled'}
CRITERIA={'precise_access','access_and_user_verification','account_unlock','login_recovery','vpn_recovery','application_recovery'}
CATEGORIES={'symptom','context','prior_attempt','reported_recovery','reported_failure','reported_approval','preference','request','correction'}
INTENTS={'new_request','additional_info','correction','progress_report','question','cancel_request','handoff_request','unrelated','unclear'}
RELATIONS={'possible_shared_cause','depends_on','same_incident','duplicate_of'}
TEAM_DIRECTORY={'general_it':'General IT','security':'Security','platform':'Platform','data_platform':'Data Platform','network':'Network'}


class Domain:
    def __init__(self, store):
        self.store=store

    def _actor(self,c,actor_id):
        row=c.execute('SELECT * FROM users WHERE id=? AND active=1',(actor_id,)).fetchone()
        require(row is not None,'FORBIDDEN','Inactive or unknown employee')
        user=unpack(row);user.pop('password_hash',None)
        return user

    def _conversation(self,c,actor_id,conversation_id):
        self._actor(c,actor_id)
        row=c.execute('SELECT * FROM conversations WHERE id=? AND actor_id=?',(conversation_id,actor_id)).fetchone()
        require(row is not None,'FORBIDDEN','Conversation unavailable')
        return unpack(row)

    def _issue(self,c,actor_id,conversation_id,issue_id,writable=False):
        self._conversation(c,actor_id,conversation_id)
        row=c.execute('SELECT * FROM issues WHERE id=? AND conversation_id=? AND actor_id=?',(issue_id,conversation_id,actor_id)).fetchone()
        require(row is not None,'FORBIDDEN','Issue unavailable')
        issue=unpack(row)
        if writable:
            require(issue['status'] not in ('handed_off','cancelled','denied'),'FORBIDDEN','Issue is no longer automated')
        return issue

    def demo_users(self):
        with self.store.read() as c:
            return [dict(id=r['id'],name=json.loads(r['payload'])['name']) for r in c.execute('SELECT id,payload FROM users ORDER BY id')]

    def employee(self,actor_id):
        with self.store.read() as c:
            return self._actor(c,actor_id)

    def create_conversation(self,actor_id,title='New conversation'):
        text_value(title,200)
        with self.store.write() as c:
            self._actor(c,actor_id)
            cid=new_id('CHAT')
            c.execute('INSERT INTO conversations VALUES(?,?,?,?,?)',(cid,actor_id,1,self.store.clock(),dumps({'title':title})))
            return unpack(c.execute('SELECT * FROM conversations WHERE id=?',(cid,)).fetchone())

    def list_conversations(self,actor_id):
        with self.store.read() as c:
            self._actor(c,actor_id)
            return [unpack(r) for r in c.execute('SELECT * FROM conversations WHERE actor_id=? ORDER BY created_at,id',(actor_id,))]

    def snapshot(self,actor_id,conversation_id):
        with self.store.read() as c:
            # A transaction gives a consistent multi-table view while another worker commits.
            c.execute('BEGIN')
            result={'conversation':self._conversation(c,actor_id,conversation_id)}
            for table in ('messages','issues','observations','relations','hypotheses','evidence','attempts','actions','confirmations','approvals','handoffs','questions','events','runs'):
                result[table]=[unpack(r) for r in c.execute(f'SELECT * FROM {table} WHERE conversation_id=? ORDER BY rowid',(conversation_id,))]
            result['all_resolved']=bool(result['issues']) and all(i['status']=='resolved' for i in result['issues'])
            return result

    def append_message(self,actor_id,conversation_id,role,content,idempotency_key):
        require(role in ('user','assistant','system'));text_value(content,32000);text_value(idempotency_key,200)
        with self.store.write() as c:
            return self._append_message(c,actor_id,conversation_id,role,content,idempotency_key)

    def _append_message(self,c,actor_id,conversation_id,role,content,idempotency_key):
        self._conversation(c,actor_id,conversation_id)
        old=c.execute('SELECT * FROM messages WHERE conversation_id=? AND idempotency_key=?',(conversation_id,idempotency_key)).fetchone()
        if old:
            require(old['content']==content and old['role']==role,'CONFLICT','Idempotency key reused with different message')
            return dict(old)
        mid=new_id('MSG')
        c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)',(mid,conversation_id,actor_id,role,content,self.store.clock(),idempotency_key))
        c.execute('UPDATE conversations SET version=version+1 WHERE id=?',(conversation_id,))
        return dict(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())

    def submit_user_message(self,actor_id,conversation_id,content,idempotency_key):
        """Atomic HTTP submission primitive: acknowledge only after message+queue commit."""
        text_value(content,32000);text_value(idempotency_key,200)
        with self.store.write() as c:
            message=self._append_message(c,actor_id,conversation_id,'user',content,idempotency_key)
            event=self._event(c,actor_id,conversation_id,None,'user_message',{'message_id':message['id']},'message:'+message['id'])
            return {'message':message,'event':event}

    def _new_issue(self,c,actor_id,conversation_id,goal,service_refs,criterion_id,resource_id=None):
        text_value(goal,2000)
        require(isinstance(criterion_id,str) and criterion_id in CRITERIA and isinstance(service_refs,list) and len(service_refs)<=8)
        for sid in service_refs:
            text_value(sid,200)
            require(c.execute("SELECT 1 FROM world_records WHERE kind='services' AND id=?",(sid,)).fetchone() is not None)
        if resource_id:
            r=unpack(c.execute('SELECT * FROM resources WHERE id=?',(resource_id,)).fetchone())
            require(r is not None and r['service_id'] in service_refs,'INVALID_ARGUMENT','Resource must match issue service')
        iid=new_id('ISSUE')
        payload={'goal':goal,'service_refs':service_refs,'acceptance_criteria_id':criterion_id,'resource_id':resource_id,'lifecycle':1}
        c.execute('INSERT INTO issues VALUES(?,?,?,?,?,?,?)',(iid,conversation_id,actor_id,'investigating',1,self.store.clock(),dumps(payload)))
        return unpack(c.execute('SELECT * FROM issues WHERE id=?',(iid,)).fetchone())

    def create_issue(self,actor_id,conversation_id,goal,service_refs,criterion_id='application_recovery',resource_id=None):
        with self.store.write() as c:
            self._conversation(c,actor_id,conversation_id)
            issue=self._new_issue(c,actor_id,conversation_id,goal,service_refs,criterion_id,resource_id)
            c.execute('UPDATE conversations SET version=version+1 WHERE id=?',(conversation_id,))
            return issue

    def refine_issue(self,actor_id,conversation_id,issue_id,expected_version,service_refs=None,resource_id=None,criterion_id=None):
        """Fill missing scope or strengthen criteria; never repurpose an authorized issue."""
        with self.store.write() as c:
            issue=self._issue(c,actor_id,conversation_id,issue_id,True)
            require(issue['version']==expected_version,'STALE_STATE','Issue changed')
            for table in ('actions','approvals','confirmations','attempts'):
                require(c.execute(f'SELECT 1 FROM {table} WHERE issue_id=?',(issue_id,)).fetchone() is None,'FORBIDDEN','Issue already has authorization or attempts')
            payload=json.loads(c.execute('SELECT payload FROM issues WHERE id=?',(issue_id,)).fetchone()[0])
            if service_refs is not None:
                require(isinstance(service_refs,list) and bool(service_refs))
                require(not payload['service_refs'] or service_refs==payload['service_refs'],'FORBIDDEN','Existing service scope cannot be replaced')
                for sid in service_refs:require(c.execute("SELECT 1 FROM world_records WHERE kind='services' AND id=?",(sid,)).fetchone() is not None)
                payload['service_refs']=service_refs
            if resource_id is not None:
                require(payload.get('resource_id') in (None,resource_id),'FORBIDDEN','Existing resource scope cannot be replaced')
                resource=unpack(c.execute('SELECT * FROM resources WHERE id=?',(resource_id,)).fetchone())
                require(resource is not None and resource['service_id'] in payload['service_refs'],'INVALID_ARGUMENT','Resource must match issue service')
                payload['resource_id']=resource_id
            if criterion_id is not None:
                require(criterion_id in CRITERIA)
                previous=payload['acceptance_criteria_id']
                allowed={previous}
                if previous=='precise_access':allowed.add('access_and_user_verification')
                if previous=='account_unlock':allowed.add('login_recovery')
                if previous=='application_recovery':allowed.update(('access_and_user_verification','login_recovery','vpn_recovery'))
                require(criterion_id in allowed,'FORBIDDEN','Acceptance criteria cannot be weakened')
                if criterion_id!=previous:
                    for row in c.execute('SELECT * FROM observations WHERE conversation_id=?',(conversation_id,)):
                        observation=unpack(row)
                        require(not (issue_id in observation['issue_refs'] and observation['category'] in ('reported_recovery','reported_failure')),'FORBIDDEN','Criteria cannot change after outcome feedback')
                payload['acceptance_criteria_id']=criterion_id
            c.execute('UPDATE issues SET payload=?,version=version+1 WHERE id=?',(dumps(payload),issue_id))
            c.execute('UPDATE conversations SET version=version+1 WHERE id=?',(conversation_id,))
            return unpack(c.execute('SELECT * FROM issues WHERE id=?',(issue_id,)).fetchone())

    def record_attempt(self,actor_id,conversation_id,issue_id,remedy_id,normalized_parameters,evidence_refs):
        """Record reviewed guidance offered, independently of execution or recovery."""
        text_value(remedy_id,200);require(isinstance(normalized_parameters,dict))
        from app.seed import knowledge_documents,WORLD_AS_OF
        with self.store.write() as c:
            issue=self._issue(c,actor_id,conversation_id,issue_id,True)
            refs=self._validate_refs(c,actor_id,conversation_id,evidence_refs,issue_id)
            valid=[]
            for table,evidence in refs:
                if table!='evidence' or evidence['source']!='kb':continue
                for article in knowledge_documents():
                    if article['id']!=evidence['record_id'] or remedy_id not in article['remedy_ids']:continue
                    service=unpack(c.execute("SELECT * FROM world_records WHERE kind='services' AND id=?",(article['service_id'],)).fetchone())
                    if service and article['service_id'] in issue['service_refs'] and article['reviewed'] and article['status']=='current' and service['version'] in article['applicable_versions'] and str(article['revision'])==evidence['revision'] and article['valid_from']<=WORLD_AS_OF[:10] and (not article['valid_until'] or article['valid_until']>=WORLD_AS_OF[:10]):valid.append(article)
            require(bool(valid),'NO_APPLICABLE_EVIDENCE','No current reviewed guide supports this remedy and service version')
            old=unpack(c.execute('SELECT * FROM attempts WHERE issue_id=? AND remedy_id=?',(issue_id,remedy_id)).fetchone())
            if old:
                require(old['normalized_parameters']==normalized_parameters,'CONFLICT','Remedy parameters changed')
                return old
            prior=[unpack(r) for r in c.execute('SELECT * FROM attempts WHERE issue_id=?',(issue_id,))]
            require(len(prior)<2,'ATTEMPT_LIMIT','Two distinct repair paths already attempted')
            if prior:require(bool(set(evidence_refs)-{ref for row in prior for ref in row.get('evidence_refs',[])}),'NO_APPLICABLE_EVIDENCE','Another remedy requires new evidence')
            aid=new_id('ATTEMPT');payload={'normalized_parameters':normalized_parameters,'evidence_refs':evidence_refs,'outcome':'guidance_offered','reviewed_steps':[article['text'] for article in valid],
                                         'after_message_sequence':c.execute('SELECT COALESCE(MAX(rowid),0) FROM messages WHERE conversation_id=?',(conversation_id,)).fetchone()[0]}
            c.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)',(aid,conversation_id,issue_id,remedy_id,self.store.clock(),dumps(payload)))
            c.execute("UPDATE issues SET status='verifying',version=version+1 WHERE id=?",(issue_id,))
            return unpack(c.execute('SELECT * FROM attempts WHERE id=?',(aid,)).fetchone())

    def apply_candidates(self,actor_id,conversation_id,message_id,candidates,expected_version):
        top={'message_intents','new_issue_candidates','observations','corrections','relation_candidates','unresolved_references'}
        fields(candidates,top,top)
        require(all(isinstance(v,list) for v in candidates.values()))
        require(all(isinstance(v,str) and v in INTENTS for v in candidates['message_intents']))
        require(len(candidates['new_issue_candidates'])<=8 and len(candidates['observations'])<=20,'LIMIT_EXCEEDED','Batch too large; split into messages')
        require(all(len(candidates[k])<=20 for k in ('corrections','relation_candidates','unresolved_references')),'LIMIT_EXCEEDED','Batch too large')
        with self.store.write() as c:
            chat=self._conversation(c,actor_id,conversation_id)
            message=c.execute("SELECT * FROM messages WHERE id=? AND conversation_id=? AND role='user'",(message_id,conversation_id)).fetchone()
            require(message is not None,'FORBIDDEN','Message unavailable')
            old=c.execute('SELECT * FROM candidate_batches WHERE message_id=?',(message_id,)).fetchone()
            if old:
                require(old['content_hash']==digest(candidates),'CONFLICT','Message candidates already applied')
                return json.loads(old['result'])
            require(chat['version']==expected_version,'STALE_STATE','Conversation changed')
            result={'issues':[],'observations':[],'corrections':[],'relations':[],'unresolved_references':[]}
            refs={};keys=set();touched=set()
            def candidate(item,allowed,required):
                fields(item,allowed,required)
                key=item['candidate_key'];text_value(key,100)
                require(key not in keys,'INVALID_ARGUMENT','Duplicate candidate key');keys.add(key)
                quote=item.get('quote')
                if quote is not None:
                    text_value(quote);require(quote in message['content'],'INVALID_ARGUMENT','Quote not present in bound message')
            def target(ref):
                text_value(ref,200)
                actual=refs.get(ref,ref)
                return self._issue(c,actor_id,conversation_id,actual)['id']
            for item in candidates['new_issue_candidates']:
                candidate(item,{'candidate_key','goal','quote','service_refs','criterion_id','resource_id'}, {'candidate_key','goal','quote','service_refs'})
                criterion=item.get('criterion_id','application_recovery')
                issue=self._new_issue(c,actor_id,conversation_id,item['goal'],item['service_refs'],criterion,item.get('resource_id'))
                refs[item['candidate_key']]=issue['id'];result['issues'].append(issue)
            for item in candidates['observations']:
                candidate(item,{'candidate_key','target_refs','category','claim','quote','mapping_status'}, {'candidate_key','target_refs','category','claim','quote','mapping_status'})
                require(isinstance(item['category'],str) and item['category'] in CATEGORIES and isinstance(item['mapping_status'],str) and item['mapping_status'] in ('tentative','needs_clarification','superseded'))
                text_value(item['claim']);require(isinstance(item['target_refs'],list) and 0<len(item['target_refs'])<=8)
                targets=[target(ref) for ref in item['target_refs']]
                touched.update(targets)
                oid=new_id('OBS')
                payload={**item,'issue_refs':targets,'source_kind':'user_report','source_id':message_id,'status':'active'}
                c.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',(oid,conversation_id,message_id,actor_id,1,self.store.clock(),dumps(payload)))
                observation=unpack(c.execute('SELECT * FROM observations WHERE id=?',(oid,)).fetchone())
                refs[item['candidate_key']]=oid;result['observations'].append(observation)
                if item['category'] in ('reported_failure','symptom','correction'):
                    self._reopen_resolved(c,actor_id,conversation_id,targets)
            for item in candidates['corrections']:
                candidate(item,{'candidate_key','observation_id','expected_version','claim','quote'}, {'candidate_key','observation_id','expected_version','claim','quote'})
                text_value(item['claim']);text_value(item['observation_id'],200);require(type(item['expected_version']) is int)
                old_obs=unpack(c.execute('SELECT * FROM observations WHERE id=? AND conversation_id=?',(item['observation_id'],conversation_id)).fetchone())
                require(old_obs is not None,'FORBIDDEN','Observation unavailable')
                require(old_obs['version']==item['expected_version'],'STALE_STATE','Observation changed')
                touched.update(old_obs['issue_refs'])
                old_payload=json.loads(c.execute('SELECT payload FROM observations WHERE id=?',(old_obs['id'],)).fetchone()[0]);old_payload['status']='superseded'
                c.execute('UPDATE observations SET version=version+1,payload=? WHERE id=?',(dumps(old_payload),old_obs['id']))
                oid=new_id('OBS');payload={**item,'category':'correction','issue_refs':old_obs['issue_refs'],'source_kind':'user_report','source_id':message_id,'status':'active','mapping_status':'tentative'}
                c.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',(oid,conversation_id,message_id,actor_id,1,self.store.clock(),dumps(payload)))
                result['corrections'].append(unpack(c.execute('SELECT * FROM observations WHERE id=?',(oid,)).fetchone()));refs[item['candidate_key']]=oid
                self._reopen_resolved(c,actor_id,conversation_id,old_obs['issue_refs'])
            for item in candidates['relation_candidates']:
                candidate(item,{'candidate_key','from_ref','to_ref','type','support_refs'}, {'candidate_key','from_ref','to_ref','type','support_refs'})
                require(isinstance(item['type'],str) and item['type'] in RELATIONS)
                a,b=target(item['from_ref']),target(item['to_ref']);require(a!=b)
                require(isinstance(item['support_refs'],list) and all(isinstance(r,str) for r in item['support_refs']))
                support=[refs.get(r,r) for r in item['support_refs']];require(bool(support),'INVALID_ARGUMENT','Relation requires evidence')
                self._validate_refs(c,actor_id,conversation_id,support)
                rid=new_id('REL');payload={**item,'from_issue':a,'to_issue':b,'evidence_refs':support,'status':'candidate'}
                c.execute('INSERT INTO relations VALUES(?,?,?,?)',(rid,conversation_id,1,dumps(payload)))
                result['relations'].append(unpack(c.execute('SELECT * FROM relations WHERE id=?',(rid,)).fetchone()))
            for item in candidates['unresolved_references']:
                candidate(item,{'candidate_key','quote','candidate_issue_ids','clarification_question'}, {'candidate_key','quote','candidate_issue_ids','clarification_question'})
                text_value(item['clarification_question'])
                require(isinstance(item['candidate_issue_ids'],list))
                result['unresolved_references'].append({**item,'candidate_issue_ids':[target(r) for r in item['candidate_issue_ids']]})
            for iid in touched:c.execute('UPDATE issues SET version=version+1 WHERE id=?',(iid,))
            result['issues']=[unpack(c.execute('SELECT * FROM issues WHERE id=?',(i['id'],)).fetchone()) for i in result['issues']]
            c.execute('INSERT INTO candidate_batches VALUES(?,?,?)',(message_id,digest(candidates),dumps(result)))
            c.execute('UPDATE conversations SET version=version+1 WHERE id=?',(conversation_id,))
            return result

    def _reopen_resolved(self,c,actor_id,conversation_id,issue_ids):
        """A recurrence or correction starts only the affected resolved lifecycles."""
        for iid in issue_ids:
            issue=self._issue(c,actor_id,conversation_id,iid)
            if issue['status']=='resolved':
                payload=json.loads(c.execute('SELECT payload FROM issues WHERE id=?',(iid,)).fetchone()[0])
                payload['lifecycle']+=1
                c.execute("UPDATE issues SET status='investigating',payload=? WHERE id=?",(dumps(payload),iid))

    def _validate_refs(self,c,actor_id,conversation_id,refs,issue_id=None):
        require(isinstance(refs,list) and len(refs)<=100)
        found=[]
        for ref in refs:
            text_value(ref,200)
            row=None
            for table in ('evidence','observations','actions','approvals','confirmations','handoffs','attempts','messages'):
                row=c.execute(f'SELECT * FROM {table} WHERE id=? AND conversation_id=?',(ref,conversation_id)).fetchone()
                if row:
                    value=unpack(row)
                    require(value.get('actor_id',actor_id)==actor_id,'FORBIDDEN','Reference unavailable')
                    if issue_id and table=='observations': require(issue_id in value['issue_refs'],'INVALID_ARGUMENT','Observation targets another issue')
                    if issue_id and table in ('actions','approvals','confirmations','attempts'): require(value['issue_id']==issue_id,'INVALID_ARGUMENT','Reference targets another issue')
                    found.append((table,value));break
            require(row is not None,'INVALID_ARGUMENT','Reference not found in current conversation')
        return found

    def persist_evidence(self,actor_id,conversation_id,source,record_id,revision,content,scope):
        with self.store.write() as c:
            self._conversation(c,actor_id,conversation_id)
            return self._evidence(c,actor_id,conversation_id,source,record_id,revision,content,scope)

    def _evidence(self,c,actor_id,conversation_id,source,record_id,revision,content,scope):
        require(source in ('directory','service_state','kb','history','policy','tool_receipt','approval_event','confirmation_event','conversation'))
        eid=new_id('EVID');fetched=utc(self.store.clock())
        c.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)',(eid,conversation_id,actor_id,source,record_id,str(revision),digest(content),fetched,dumps(scope),dumps({'content':content})))
        return unpack(c.execute('SELECT * FROM evidence WHERE id=?',(eid,)).fetchone())

    def apply_issue_updates(self,actor_id,conversation_id,proposals):
        require(isinstance(proposals,list) and len(proposals)<=20)
        with self.store.write() as c:
            self._conversation(c,actor_id,conversation_id)
            seen=set();result=[]
            for p in proposals:
                fields(p,{'issue_id','expected_version','requested_status','criterion_id','evidence_refs'}, {'issue_id','expected_version','requested_status','criterion_id','evidence_refs'})
                issue=self._issue(c,actor_id,conversation_id,p['issue_id'])
                require(issue['id'] not in seen);seen.add(issue['id'])
                require(issue['version']==p['expected_version'],'STALE_STATE','Issue changed')
                require(p['criterion_id']==issue['acceptance_criteria_id'] and p['requested_status'] in STATUSES)
                require(issue['status'] not in ('handed_off','cancelled','denied') or p['requested_status']==issue['status'],'FORBIDDEN','Terminal issue stops automation')
                refs=self._validate_refs(c,actor_id,conversation_id,p['evidence_refs'],issue['id'])
                status=p['requested_status']
                if status=='resolved': self._accept(c,actor_id,issue,refs)
                if status=='waiting_approval':
                    require(any(t=='approvals' and r['status']=='pending' and r['expires_at']>self.store.clock() for t,r in refs),'PRECONDITION_FAILED','No pending approval')
                if status=='handed_off': require(any(t=='handoffs' and issue['id'] in r['issue_refs'] for t,r in refs),'PRECONDITION_FAILED','No persisted handoff')
                if status=='denied': require(any(t=='approvals' and r['status']=='rejected' for t,r in refs) or any(t=='evidence' and r['source']=='policy' for t,r in refs),'PRECONDITION_FAILED','No denial evidence')
                if status=='waiting_external':
                    user=self._actor(c,actor_id)
                    require(any(t=='evidence' and r['source']=='service_state' and self._matching_incident(r['content'],issue,user) for t,r in refs),'PRECONDITION_FAILED','No matching active incident')
                c.execute('UPDATE issues SET status=?,version=version+1 WHERE id=?',(status,issue['id']))
                result.append(unpack(c.execute('SELECT * FROM issues WHERE id=?',(issue['id'],)).fetchone()))
            c.execute('UPDATE conversations SET version=version+1 WHERE id=?',(conversation_id,))
            return result

    @staticmethod
    def _matching_incident(content,issue,user):
        incidents=content.get('incidents',[]) if isinstance(content,dict) else []
        return any(i.get('status')=='active' and i.get('region')==user['region'] and i.get('service_id') in issue['service_refs'] for i in incidents)

    def _accept(self,c,actor_id,issue,refs):
        criterion=issue['acceptance_criteria_id']
        feedback=[r for t,r in refs if t=='observations' and r['category']=='reported_recovery' and r['status']=='active' and r['mapping_status']=='tentative']
        def sequence(observation):
            return c.execute('SELECT rowid FROM messages WHERE id=?',(observation['message_id'],)).fetchone()[0]
        prior_actions=[unpack(r) for r in c.execute('SELECT * FROM actions WHERE issue_id=?',(issue['id'],))]
        prior_attempts=[unpack(r) for r in c.execute('SELECT * FROM attempts WHERE issue_id=?',(issue['id'],))]
        after=max([a['receipt'].get('after_message_sequence',0) for a in prior_actions]+[a.get('after_message_sequence',0) for a in prior_attempts]+[0])
        feedback=[r for r in feedback if sequence(r)>after]
        # A later failure/correction invalidates an earlier recovery report.
        if feedback:
            latest=max(sequence(r) for r in feedback)
            failures=[unpack(r) for r in c.execute('SELECT * FROM observations WHERE conversation_id=?',(issue['conversation_id'],))]
            require(not any(issue['id'] in r['issue_refs'] and r['status']=='active' and r['category'] in ('reported_failure','correction') and sequence(r)>=latest for r in failures),'PRECONDITION_FAILED','Conflicting feedback')
        evidence=[r for t,r in refs if t=='evidence']
        if criterion in ('precise_access','access_and_user_verification'):
            resource=issue.get('resource_id')
            require(resource is not None,'PRECONDITION_FAILED','Exact resource is not bound')
            require(c.execute("SELECT 1 FROM access_grants WHERE actor_id=? AND resource_id=? AND permission='read'",(actor_id,resource)).fetchone() is not None,'PRECONDITION_FAILED','Precise permission missing')
            require(any(e['record_id']==resource and e['content'].get('permissions')==['read'] and e['scope'].get('actor_id')==actor_id for e in evidence),'PRECONDITION_FAILED','Access inspection missing')
            if criterion=='access_and_user_verification': require(bool(feedback),'PRECONDITION_FAILED','User access verification missing')
        elif criterion in ('account_unlock','login_recovery'):
            sid=issue['service_refs'][0] if len(issue['service_refs'])==1 else None
            account=unpack(c.execute('SELECT * FROM accounts WHERE actor_id=? AND service_id=?',(actor_id,sid)).fetchone())
            require(account is not None and not account['locked'] and not account['disabled_reason'],'PRECONDITION_FAILED','Account not usable')
            require(any(e['record_id']==account['id'] and e['revision']==str(account['revision']) for e in evidence),'PRECONDITION_FAILED','Fresh account inspection missing')
            if criterion=='login_recovery': require(bool(feedback),'PRECONDITION_FAILED','Login feedback missing')
        elif criterion=='vpn_recovery':
            vpn=unpack(c.execute('SELECT * FROM vpn_sessions WHERE actor_id=?',(actor_id,)).fetchone())
            require(vpn['connected'] and vpn['connected_since'] is not None and self.store.clock()-vpn['connected_since']>=vpn['required_observation_seconds'] and bool(feedback),'PRECONDITION_FAILED','VPN observation window or feedback incomplete')
            require(any(e['record_id']==vpn['id'] and e['revision']==str(vpn['revision']) and e['content'].get('connected') and e['content'].get('observation_seconds',0)>=vpn['required_observation_seconds'] for e in evidence),'PRECONDITION_FAILED','Completed VPN observation inspection missing')
        else:
            require(bool(feedback),'PRECONDITION_FAILED','Symptom recovery feedback missing')
            user=self._actor(c,actor_id)
            require(bool(issue['service_refs']),'PRECONDITION_FAILED','Service scope missing')
            incidents=[unpack(r) for r in c.execute("SELECT * FROM world_records WHERE kind='incidents'")]
            require(not self._matching_incident({'incidents':incidents},issue,user),'PRECONDITION_FAILED','Current matching incident is active')
            for sid in issue['service_refs']:
                service=unpack(c.execute("SELECT * FROM world_records WHERE kind='services' AND id=?",(sid,)).fetchone())
                require(service is not None and service['status']=='operational','PRECONDITION_FAILED','Current service condition is not healthy')
                require(any(e['source']=='service_state' and e['record_id']==sid and e['revision']==str(service['revision']) and e['content'].get('service',{}).get('status')=='operational' for e in evidence),'PRECONDITION_FAILED','Current objective application inspection missing')

    def _event(self,c,actor_id,conversation_id,issue_id,kind,payload,dedupe_key):
        old=c.execute('SELECT * FROM events WHERE dedupe_key=?',(dedupe_key,)).fetchone()
        if old:
            require(old['actor_id']==actor_id and old['conversation_id']==conversation_id and old['issue_id']==issue_id and old['kind']==kind and json.loads(old['payload'])==payload,'CONFLICT','Event idempotency key already used')
            return unpack(old)
        eid=new_id('EVENT')
        c.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?)',(eid,conversation_id,actor_id,issue_id,kind,dedupe_key,'queued',None,None,self.store.clock(),dumps(payload)))
        return unpack(c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone())

    def enqueue_event(self,actor_id,conversation_id,kind,payload,dedupe_key,issue_id=None):
        require(kind in ('user_message','approval_decision','confirmation_decision','tool_result','resume'));text_value(dedupe_key,300)
        with self.store.write() as c:
            self._conversation(c,actor_id,conversation_id)
            if issue_id:self._issue(c,actor_id,conversation_id,issue_id)
            return self._event(c,actor_id,conversation_id,issue_id,kind,payload,dedupe_key)

    def list_events(self,actor_id,conversation_id):
        return self.snapshot(actor_id,conversation_id)['events']

    def claim_event(self,worker_id,lease_seconds=150):
        text_value(worker_id,200);require(1<=lease_seconds<=3600)
        with self.store.write() as c:
            now=self.store.clock()
            row=c.execute("""SELECT e.* FROM events e JOIN users u ON u.id=e.actor_id WHERE u.active=1 AND
                (e.status='queued' OR (e.status='running' AND e.lease_until<=?)) AND NOT EXISTS
                (SELECT 1 FROM events busy WHERE busy.conversation_id=e.conversation_id AND busy.id!=e.id AND busy.status='running' AND busy.lease_until>?)
                ORDER BY e.created_at,e.rowid LIMIT 1""",(now,now)).fetchone()
            if row is None:return None
            c.execute("UPDATE events SET status='running',worker_id=?,lease_until=? WHERE id=?",(worker_id,now+lease_seconds,row['id']))
            return unpack(c.execute('SELECT * FROM events WHERE id=?',(row['id'],)).fetchone())

    def finish_event(self,event_id,worker_id,status='completed'):
        require(status in ('completed','failed','queued'))
        with self.store.write() as c:
            row=c.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
            require(row is not None and row['worker_id']==worker_id and row['status']=='running' and row['lease_until']>self.store.clock(),'CONFLICT','Event lease unavailable')
            c.execute('UPDATE events SET status=?,lease_until=NULL WHERE id=?',(status,event_id))
            return unpack(c.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone())

    def renew_event(self,event_id,worker_id,lease_seconds=150):
        require(1<=lease_seconds<=3600)
        with self.store.write() as c:
            changed=c.execute("UPDATE events SET lease_until=? WHERE id=? AND worker_id=? AND status='running' AND lease_until>?",(self.store.clock()+lease_seconds,event_id,worker_id,self.store.clock())).rowcount
            require(changed==1,'CONFLICT','Event lease unavailable')

    def decide_confirmation(self,actor_id,confirmation_id,decision):
        require(decision in ('confirmed','denied','cancelled'))
        with self.store.write() as c:
            self._actor(c,actor_id)
            item=unpack(c.execute('SELECT * FROM confirmations WHERE id=? AND actor_id=?',(confirmation_id,actor_id)).fetchone())
            require(item is not None,'FORBIDDEN','Confirmation unavailable')
            self._issue(c,actor_id,item['conversation_id'],item['issue_id'],True)
            require(item['expires_at']>self.store.clock(),'EXPIRED','Confirmation expired')
            if item['decision']==decision:return item
            # Cancellation revokes a previously confirmed action while it is still unexecuted.
            require(item['decision']=='pending' or (decision=='cancelled' and item['decision']=='confirmed'),'CONFLICT','Confirmation already decided')
            require(c.execute('SELECT 1 FROM actions WHERE fingerprint=?',(item['fingerprint'],)).fetchone() is None,'CONFLICT','Action already executed')
            c.execute('UPDATE confirmations SET decision=? WHERE id=?',(decision,confirmation_id))
            self._event(c,actor_id,item['conversation_id'],item['issue_id'],'confirmation_decision',{'confirmation_id':confirmation_id,'decision':decision},f'confirmation:{confirmation_id}:{decision}')
            return unpack(c.execute('SELECT * FROM confirmations WHERE id=?',(confirmation_id,)).fetchone())

    def list_approvals(self,actor_id):
        with self.store.read() as c:
            user=self._actor(c,actor_id);result=[]
            for row in c.execute('SELECT * FROM approvals ORDER BY created_at,id'):
                item=unpack(row)
                resource=unpack(c.execute('SELECT * FROM resources WHERE id=?',(item['resource_id'],)).fetchone())
                if item['actor_id']==actor_id or ('approver' in user['roles'] and actor_id in resource['owner_ids']):
                    if item['expires_at']<=self.store.clock() and item['status'] in ('pending','approved'):item['status']='expired'
                    result.append(item)
            return result

    def decide_approval(self,actor_id,approval_id,decision):
        require(decision in ('approved','rejected','cancelled'))
        with self.store.write() as c:
            user=self._actor(c,actor_id)
            item=unpack(c.execute('SELECT * FROM approvals WHERE id=?',(approval_id,)).fetchone())
            require(item is not None,'FORBIDDEN','Approval unavailable')
            resource=unpack(c.execute('SELECT * FROM resources WHERE id=?',(item['resource_id'],)).fetchone())
            if decision=='cancelled':require(item['actor_id']==actor_id,'FORBIDDEN','Only requester may cancel')
            else:require('approver' in user['roles'] and actor_id in resource['owner_ids'] and actor_id!=item['actor_id'],'FORBIDDEN','Authorized resource owner required')
            require(item['expires_at']>self.store.clock(),'EXPIRED','Approval expired')
            self._issue(c,item['actor_id'],item['conversation_id'],item['issue_id'],True)
            if item['status']==decision:return item
            require(item['status']=='pending' or (decision=='cancelled' and item['status']=='approved'),'CONFLICT','Approval already decided')
            require(c.execute("SELECT 1 FROM actions WHERE issue_id=? AND json_extract(receipt,'$.approval_id')=?",(item['issue_id'],approval_id)).fetchone() is None,'CONFLICT','Approved action already executed')
            payload=json.loads(c.execute('SELECT payload FROM approvals WHERE id=?',(approval_id,)).fetchone()[0]);payload['decided_by']=actor_id;payload['decided_at']=utc(self.store.clock())
            c.execute('UPDATE approvals SET status=?,revision=revision+1,payload=? WHERE id=?',(decision,dumps(payload),approval_id))
            self._event(c,item['actor_id'],item['conversation_id'],item['issue_id'],'approval_decision',{'approval_id':approval_id,'decision':decision},f'approval:{approval_id}:{decision}')
            return unpack(c.execute('SELECT * FROM approvals WHERE id=?',(approval_id,)).fetchone())

    def get_handoff(self,actor_id,handoff_id):
        with self.store.read() as c:
            self._actor(c,actor_id)
            row=c.execute('SELECT * FROM handoffs WHERE id=? AND actor_id=?',(handoff_id,actor_id)).fetchone()
            require(row is not None,'FORBIDDEN','Handoff unavailable')
            return unpack(row)

    def save_questions(self,actor_id,conversation_id,questions):
        require(isinstance(questions,list) and len(questions)<=8)
        with self.store.write() as c:
            self._conversation(c,actor_id,conversation_id);result=[]
            for item in questions:
                fields(item,{'text','issue_refs','question_intent'}, {'text','issue_refs','question_intent'});text_value(item['text'])
                require(item['question_intent'] in ('affected_scope','error_detail','attempt_result','target_resource','reference_clarification','environment','general'))
                for ref in item['issue_refs']:self._issue(c,actor_id,conversation_id,ref)
                qid=new_id('QUESTION');c.execute('INSERT INTO questions VALUES(?,?,?,?,?)',(qid,conversation_id,actor_id,'pending',dumps(item)))
                result.append(unpack(c.execute('SELECT * FROM questions WHERE id=?',(qid,)).fetchone()))
            return result

    def create_run(self,actor_id,conversation_id,event_id,metadata=None):
        with self.store.write() as c:
            self._conversation(c,actor_id,conversation_id)
            require(c.execute('SELECT 1 FROM events WHERE id=? AND actor_id=? AND conversation_id=?',(event_id,actor_id,conversation_id)).fetchone() is not None,'FORBIDDEN','Event unavailable')
            rid=new_id('RUN');c.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)',(rid,conversation_id,actor_id,event_id,'queued',1,self.store.clock(),dumps(metadata or {})))
            return unpack(c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone())

    def update_run(self,actor_id,run_id,status,metadata=None,expected_version=None):
        require(status in ('queued','running','waiting','completed','failed','budget_exhausted'))
        with self.store.write() as c:
            self._actor(c,actor_id)
            run=unpack(c.execute('SELECT * FROM runs WHERE id=? AND actor_id=?',(run_id,actor_id)).fetchone());require(run is not None,'FORBIDDEN','Run unavailable')
            if expected_version is not None:require(run['version']==expected_version,'STALE_STATE','Run changed')
            payload=json.loads(c.execute('SELECT payload FROM runs WHERE id=?',(run_id,)).fetchone()[0]);payload.update(metadata or {})
            c.execute('UPDATE runs SET status=?,version=version+1,payload=? WHERE id=?',(status,dumps(payload),run_id))
            return unpack(c.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone())

    def get_run(self,actor_id,run_id):
        with self.store.read() as c:
            self._actor(c,actor_id)
            run=unpack(c.execute('SELECT * FROM runs WHERE id=? AND actor_id=?',(run_id,actor_id)).fetchone());require(run is not None,'FORBIDDEN','Run unavailable');return run

    def debug_snapshot(self,actor_id,run_id=None):
        with self.store.read() as c:
            user=self._actor(c,actor_id);require('debug' in user['roles'],'FORBIDDEN','Debug role required')
            chat=None
            if run_id is not None:
                run=c.execute('SELECT conversation_id FROM runs WHERE id=?',(run_id,)).fetchone()
                require(run is not None,'NOT_FOUND','Run unavailable');chat=run['conversation_id']
            result={}
            for table in ('runs','tool_calls','actions','events'):
                query=f'SELECT * FROM {table}'
                params=()
                if run_id is not None:
                    query+=' WHERE id=?' if table=='runs' else ' WHERE conversation_id=?'
                    params=(run_id if table=='runs' else chat,)
                result[table]=[unpack(r) for r in c.execute(query,params)]
            return redact(result)


def redact(value):
    if isinstance(value,dict):return {k:redact(v) for k,v in value.items() if not any(s in k.lower() for s in ('password','secret','api_key','token_hash','csrf','reasoning_content','session_token'))}
    if isinstance(value,list):return [redact(v) for v in value]
    return value
