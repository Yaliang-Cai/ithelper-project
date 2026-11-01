"""Two-stage model-driven Agent. Worker owns event claim/renew/finish lifecycle."""
import copy
import json
import sqlite3
import time
import re

from pydantic import ValidationError
from app.budget import BudgetExceeded
from app.context import ContextBuilder, ContextCapacityError, readable_timestamps, token_bound
from app.contracts import Interpretation, Refinement, Turn, refinement_schema, submit_schema
from app.domain import DomainError, digest, redact, require
from app.prompts import DECISION, INTERPRETATION, PROMPT_VERSION
from app.providers import ProviderError,request_deadline

WRITE_TOOLS={'unlock_account','grant_catalog_access','reset_vpn_session','request_approval','create_handoff'}
MAX_MODEL_CALLS=10


def parse_json(text):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    def invalid(value):raise ValueError('Nonfinite JSON')
    value=json.loads(text,object_pairs_hook=pairs,parse_constant=invalid)
    if not isinstance(value,dict):raise ValueError('Object required')
    return value


class RunStop(Exception):
    def __init__(self,code,status='failed'):
        self.code,self.status=code,status


def _turn_result(run_id,event_id,status,error_code,submitted,state,model_calls,tool_calls):
    cards={k:state[k] for k in ('issues','confirmations','approvals','handoffs','all_resolved')}
    return {'run_id':run_id,'event_id':event_id,'status':status,'error_code':error_code,
            'message':submitted['message'] if submitted else None,
            'guidance_refs':submitted.get('guidance_refs',[]) if submitted else [],
            'cards':cards,'model_calls':model_calls,'tool_calls':tool_calls}


class Agent:
    def __init__(self,domain,client,tools=None,context_builder=None,*,monotonic=time.monotonic,thinking=False):
        from app.tools import ToolExecutor
        self.domain=domain;self.client=client;self.tools=tools or ToolExecutor(domain)
        self.context=context_builder or ContextBuilder(domain)
        self.monotonic=monotonic;self.thinking=thinking

    def handle(self,claimed_event):
        """Accept a persisted *running* event dictionary, including worker_id.

        No implicit claim or finish. Caller keeps the lease alive while this
        synchronous method runs and finishes the event after durable result.
        Return contains run_id/status/error_code/message/cards/counts.
        """
        require(isinstance(claimed_event,dict),'INVALID_ARGUMENT','A claimed event dictionary is required')
        actor=claimed_event['actor_id'];chat=claimed_event['conversation_id']
        state=self.domain.snapshot(actor,chat)
        event=next((e for e in state['events'] if e['id']==claimed_event.get('id')),None)
        require(event is not None and event['status']=='running' and event.get('worker_id')==claimed_event.get('worker_id') and event['lease_until']>self.domain.store.clock(),'CONFLICT','Active claimed event required')
        # A duplicate dispatch of a finished run returns the durable result.
        prior=[r for r in state['runs'] if r['event_id']==event['id'] and r.get('result')]
        if prior:return prior[-1]['result']
        submission_key='agent:'+event['id']
        submitted=self.domain.get_submitted_turn(actor,chat,submission_key)
        if submitted is not None:
            # A submission receipt commits with its state/reply. Recover it
            # before requesting another stochastic interpretation/decision.
            current=self.domain.snapshot(actor,chat)
            owned_event=next(e for e in current['events'] if e['id']==event['id'])
            require(owned_event['status']=='running' and owned_event['worker_id']==event['worker_id'] and owned_event['lease_until']>self.domain.store.clock(),'CONFLICT','Active claimed event required')
            incomplete=[r for r in current['runs'] if r['event_id']==event['id']]
            run=incomplete[-1] if incomplete else self.domain.create_run(actor,chat,event['id'],{'mode':'recovered_submission'})
            result=_turn_result(run['id'],event['id'],'completed',None,submitted,current,run.get('model_calls',0),run.get('tool_calls',0))
            self.domain.update_run(actor,run['id'],'completed',{'result':result,'recovered_submission':True,'submission_key':submission_key,'error_code':None})
            return result
        return _Run(self,event).execute()


class _Run:
    def __init__(self,agent,event):
        self.agent=agent;self.domain=agent.domain;self.event=event
        self.actor=event['actor_id'];self.chat=event['conversation_id']
        self.start=agent.monotonic();self.model_calls=0;self.tool_calls=0;self.repairs=0
        self.trace=[];self.contexts=[];self.read_failures={};self.failed_writes=set();self.uncertain=set()
        self.rejected_submissions=set()
        self.usage=[];self.messages=[];self.stage='interpretation'
        # Retain uncertain writes from any interrupted previous run.
        for run in self.domain.snapshot(self.actor,self.chat)['runs']:
            for item in run.get('trace',[]):
                result=item.get('result',{})
                if result.get('error',{}) and result['error'].get('execution_status')=='uncertain' and result.get('action_id'):self.uncertain.add(result['action_id'])
                if item.get('name')=='get_action_result' and result.get('ok'):self.uncertain.discard(item.get('arguments',{}).get('action_id'))
        from app.seed import WORLD_AS_OF
        from app.tools import POLICY_VERSION
        self.run=self.domain.create_run(self.actor,self.chat,event['id'],{'prompt_version':PROMPT_VERSION,'mode':'live_agent','model':'deepseek-flash','thinking':agent.thinking,
            'data_as_of':WORLD_AS_OF,'policy_version':POLICY_VERSION,'limits':{'model_calls':MAX_MODEL_CALLS,'tool_calls':12,'seconds':120,'format_corrections':1}})

    def guard(self):
        if self.agent.monotonic()-self.start>=120:raise RunStop('RUN_DEADLINE','budget_exhausted')
        state=self.domain.snapshot(self.actor,self.chat)
        e=next((e for e in state['events'] if e['id']==self.event['id']),None)
        if not e or e['status']!='running' or e['worker_id']!=self.event['worker_id'] or e['lease_until']<=self.domain.store.clock():raise RunStop('EVENT_LEASE_LOST')

    def persist(self,status='running',extra=None):
        self.domain.update_run(self.actor,self.run['id'],status,{
            'model_calls':self.model_calls,'tool_calls':self.tool_calls,'repairs':self.repairs,
            'elapsed_seconds':round(self.agent.monotonic()-self.start,3),'contexts':self.contexts,
            'trace':redact(self.trace),'usage':self.usage,**(extra or {})})

    def fresh_context(self):
        self.guard()
        built=self.agent.context.build(self.stage,self.actor,self.chat,self.event)
        data={**built['data'],'remaining':{'model_calls':MAX_MODEL_CALLS-self.model_calls,'tool_calls':12-self.tool_calls,'seconds':max(0,120-(self.agent.monotonic()-self.start)),'format_corrections':1-self.repairs}}
        self.contexts.append({k:v for k,v in built.items() if k!='data'})
        data['context_selection']={'selected_refs':built['selected_refs'],'omitted_refs':built['omitted_refs']}
        return {'role':'user','content':json.dumps(readable_timestamps(data),ensure_ascii=False)}

    def complete(self,tools=None):
        self.guard()
        if self.model_calls>=MAX_MODEL_CALLS:raise RunStop('MODEL_CALL_LIMIT','budget_exhausted')
        self.messages[1]=self.fresh_context()
        remaining=120-(self.agent.monotonic()-self.start)
        if remaining<=0:raise RunStop('RUN_DEADLINE','budget_exhausted')
        self.model_calls+=1
        final_only=self.stage=='decision' and self.model_calls==MAX_MODEL_CALLS
        attempt_tools=[submit_schema()] if final_only else tools
        entry={'kind':'model','stage':self.stage,'input':redact(copy.deepcopy(self.messages)),'input_estimated_tokens':token_bound(self.messages),'schema_estimated_tokens':token_bound(attempt_tools or []),'output_limit':2048,'finalization_only':final_only}
        self.trace.append(entry);self.persist()
        try:
            result=self.agent.client.complete(self.messages,purpose='agent_'+self.stage,tools=attempt_tools,
                response_format={'type':'json_object'} if self.stage=='interpretation' else None,
                thinking=self.agent.thinking,max_tokens=2048,timeout_seconds=min(45,remaining),
                tool_choice={'type':'function','function':{'name':'submit_turn'}} if final_only else None)
        except ProviderError as exc:
            entry['error_category']=exc.category
            if exc.category!='protocol':raise
            self.correction()
            self.messages.append({'role':'user','content':'The previous provider response was malformed. Return a valid response following the current stage schema/protocol.'})
            return self.complete(tools)
        self.usage.append(result.get('usage',{}))
        self.guard()  # Never execute late-returned tool requests.
        message=copy.deepcopy(result['choices'][0]['message'])
        entry['response']=redact(message)
        self.persist()
        return message

    def correction(self):
        if self.repairs>=1:raise RunStop('MODEL_PROTOCOL')
        self.repairs+=1

    def interpret(self):
        if self.event['kind']!='user_message':return
        if self.domain.get_interpretation(self.actor,self.chat,self.event['message_id']) is not None:return
        self.messages=[{'role':'system','content':INTERPRETATION+'\nJSON schema:\n'+json.dumps(Interpretation.model_json_schema())},self.fresh_context()]
        while True:
            try:
                message=self.complete()
                if message.get('tool_calls'):raise ValueError('Interpretation cannot call tools')
                proposal=Interpretation.model_validate(parse_json(message.get('content'))).model_dump(exclude_none=True)
                self.guard()
                state=self.domain.snapshot(self.actor,self.chat)
                applied=self.domain.apply_candidates(self.actor,self.chat,self.event['message_id'],proposal,state['conversation']['version'])
                self.trace.append({'kind':'interpretation','proposal':proposal,'result':applied});self.persist();return
            except (ValueError,TypeError,ValidationError,DomainError) as exc:
                if isinstance(exc,DomainError) and exc.code not in ('INVALID_ARGUMENT','LIMIT_EXCEEDED','FORBIDDEN'):raise
                self.correction()
                # Malformed output is untrusted; no generated text is promoted to instructions.
                detail=({'code':exc.code,'message':exc.message} if isinstance(exc,DomainError) else
                        {'errors':exc.errors(include_input=False,include_url=False)} if isinstance(exc,ValidationError) else {'code':'INVALID_JSON'})
                self.messages.append({'role':'user','content':'Your interpretation failed validation. Return corrected JSON with exact current-message quotes, allowed categories, visible service IDs and issue-only relationship endpoints. Unknown scope uses service_refs=[]. Validation feedback (DATA): '+json.dumps(detail,ensure_ascii=False)})

    def envelope(self,code,message=None,data=None):
        return {'ok':False,'data':data or {},'evidence':[],'action_id':None,'error':{'code':code,'message':message or 'Request was not executed. Use exact schema, current scope and remaining budget.','retryable':False,'execution_status':'not_executed'}}

    def business_call(self,name,args):
        self.guard()
        key=digest({'name':name,'arguments':args})
        if name in WRITE_TOOLS and (self.uncertain or key in self.failed_writes):return self.envelope('QUERY_BEFORE_WRITE_RETRY')
        if name not in WRITE_TOOLS and self.read_failures.get(key,0)>1:return self.envelope('READ_RETRY_LIMIT')
        with request_deadline(120-(self.agent.monotonic()-self.start)):
            result=self.agent.tools.call(name,args,self.actor,self.chat,before_effect=self.guard)
        if not result['ok']:
            if name in WRITE_TOOLS:self.failed_writes.add(key)
            elif result.get('error',{}).get('retryable'):self.read_failures[key]=self.read_failures.get(key,0)+1
        if result.get('error') and result['error']['execution_status']=='uncertain' and result.get('action_id'):self.uncertain.add(result['action_id'])
        if name=='get_action_result' and result['ok']:self.uncertain.discard(args.get('action_id'))
        return result

    def decide(self):
        self.stage='decision'
        self.messages=[{'role':'system','content':DECISION},self.fresh_context()]
        schemas=[*self.agent.tools.schemas,refinement_schema(),submit_schema()]
        while True:
            message=self.complete(schemas)
            # Completion can include a bounded internal protocol retry; enforce
            # dispatch against the request that actually produced this result.
            final_only=self.model_calls==MAX_MODEL_CALLS
            calls=message.get('tool_calls',[])
            if not calls:
                self.correction()
                self.messages.append(message)
                self.messages.append({'role':'user','content':'Finish with submit_turn or request registered tools. Free text alone does not finalize.'})
                continue
            ids=[c.get('id') for c in calls]
            if any(not isinstance(i,str) or not i for i in ids) or len(ids)!=len(set(ids)):raise RunStop('MODEL_PROTOCOL')
            self.messages.append(message)  # Preserve all native fields, including reasoning_content, in memory only.
            submitted=None;invalid=False;no_progress=False
            exclusive=any(c['function']['name']=='submit_turn' for c in calls) and len(calls)>1
            for call in calls:
                self.guard()
                name=call['function']['name'];args=None
                if name!='submit_turn':
                    if self.tool_calls>=12:
                        result=self.envelope('TOOL_CALL_LIMIT')
                        self.messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(result)})
                        self.trace.append({'kind':'tool','call_id':call['id'],'name':name,'arguments':None,'result':result})
                        continue
                    self.tool_calls+=1
                try:
                    args=parse_json(call['function']['arguments'])
                    if exclusive:raise ValueError('submit_turn must be alone')
                    if final_only and name!='submit_turn':
                        result=self.envelope('FINALIZATION_REQUIRED','The final model allowance is reserved for submit_turn; no further business query was executed.')
                        self.messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(result)})
                        self.trace.append({'kind':'tool','call_id':call['id'],'name':name,'arguments':args,'result':result})
                        continue
                    if name=='submit_turn':
                        proposal=Turn.model_validate(args).model_dump(exclude_none=True)
                        self.guard()
                        submitted=self.domain.submit_turn(self.actor,self.chat,proposal,'agent:'+self.event['id'],before_effect=self.guard)
                        result={'ok':True,'data':submitted}
                    elif name=='refine_issue':
                        proposal=Refinement.model_validate(args).model_dump(exclude_none=True)
                        issue=self.domain.refine_issue(self.actor,self.chat,**proposal,before_effect=self.guard)
                        result={'ok':True,'data':{'issue':issue},'evidence':[],'action_id':None,'error':None}
                    else:
                        result=self.business_call(name,args)
                        if result.get('error',{} ) and result['error']['code']=='INVALID_ARGUMENT':invalid=True
                except DomainError as exc:
                    result=self.envelope(exc.code,exc.message,exc.data)
                    # A structurally valid proposal rejected by business state
                    # is decision feedback, not another JSON-format failure.
                    fingerprint=digest({'code':exc.code,'proposals':{k:v for k,v in (args or {}).items() if k!='reply'}})
                    no_progress=fingerprint in self.rejected_submissions
                    self.rejected_submissions.add(fingerprint)
                except (ValueError,TypeError,ValidationError):
                    invalid=True
                    result=self.envelope('INVALID_ARGUMENT')
                self.messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(readable_timestamps(result),ensure_ascii=False)})
                self.trace.append({'kind':'tool','call_id':call['id'],'name':name,'arguments':args,'result':result})
                self.persist()
            if submitted:return submitted
            if no_progress:raise RunStop('NO_PROGRESS')
            if invalid:self.correction()
            if self.tool_calls>=12:raise RunStop('TOOL_CALL_LIMIT','budget_exhausted')

    def execute(self):
        error_code=None;status='completed';submitted=None
        try:
            self.persist();self.interpret();submitted=self.decide();self.guard()
        except RunStop as exc:status=exc.status;error_code=exc.code
        except BudgetExceeded:status='budget_exhausted';error_code='PROJECT_BUDGET_EXHAUSTED'
        except ProviderError as exc:error_code='PROVIDER_'+exc.category.upper();status='failed'
        except ContextCapacityError:error_code='CONTEXT_CAPACITY';status='failed'
        except DomainError as exc:error_code=exc.code;status='failed'
        except (sqlite3.Error,OSError):error_code='STORAGE_UNAVAILABLE';status='failed'
        lease=next((e for e in self.domain.snapshot(self.actor,self.chat)['events'] if e['id']==self.event['id']),None)
        if not lease or lease['status']!='running' or lease['worker_id']!=self.event['worker_id'] or lease['lease_until']<=self.domain.store.clock():
            error_code='EVENT_LEASE_LOST';status='failed'
        if submitted is None:
            # No additional model request to explain stopping. Do not imply a repair/handoff.
            replies={'PROJECT_BUDGET_EXHAUSTED':'项目调用预算已用尽，本轮已停止，已保存当前进度。',
                'PROVIDER_INSUFFICIENT_BALANCE':'模型服务账户余额不足，本轮未完成，已保存当前进度。',
                'MODEL_CALL_LIMIT':'本轮已达到调用上限，已保存进度，尚未确认的问题会继续保留。',
                'TOOL_CALL_LIMIT':'本轮已达到工具调用上限，已保存进度，尚未确认的问题会继续保留。',
                'RUN_DEADLINE':'本轮处理超时，已保存进度，尚未确认的问题会继续保留。'}
            reply=replies.get(error_code,'本轮未能完成可靠处理，已保存原始消息和现有进度。请稍后继续。')
            user_messages=[m for m in self.domain.snapshot(self.actor,self.chat)['messages'] if m['role']=='user']
            if user_messages and not re.search(r'[\u3400-\u9fff]',user_messages[-1]['content']):
                english={'PROJECT_BUDGET_EXHAUSTED':'The project model budget is exhausted. This turn has stopped; current progress is saved.',
                    'PROVIDER_INSUFFICIENT_BALANCE':'The model provider account has insufficient balance. This turn could not finish; current progress is saved.',
                    'MODEL_CALL_LIMIT':'This turn reached its model-call limit. Progress is saved and unresolved goals remain open.',
                    'TOOL_CALL_LIMIT':'This turn reached its tool-call limit. Progress is saved and unresolved goals remain open.',
                    'RUN_DEADLINE':'This turn timed out. Progress is saved and unresolved goals remain open.'}
                reply=english.get(error_code,'This turn could not complete reliably. Your original message and current progress are saved; please continue later.')
            try:
                # A lost lease may not append to a chat now owned by another worker.
                if error_code!='EVENT_LEASE_LOST':
                    submitted={'message':self.domain.append_message(self.actor,self.chat,'assistant',reply,'agent:'+self.event['id']),'guidance_refs':[]}
            except (DomainError,sqlite3.Error,OSError):pass
        state=self.domain.snapshot(self.actor,self.chat)
        result=_turn_result(self.run['id'],self.event['id'],status,error_code,submitted,state,self.model_calls,self.tool_calls)
        self.persist(status,{'error_code':error_code,'result':result})
        return result
