"""Fresh owned state, explicit context selection, and conservative byte budgets."""
import json
from datetime import datetime,timezone


class ContextCapacityError(RuntimeError):pass


def readable_timestamps(value):
    """Keep authoritative epochs and supply unambiguous UTC display companions."""
    if isinstance(value,list):return [readable_timestamps(v) for v in value]
    if not isinstance(value,dict):return value
    result={k:readable_timestamps(v) for k,v in value.items()}
    for key in ('created_at','expires_at','lease_until','issued_at','connected_since'):
        stamp=value.get(key)
        if type(stamp) in (int,float):
            try:result[key+'_iso']=datetime.fromtimestamp(stamp,timezone.utc).isoformat()
            except (ValueError,OSError,OverflowError):pass
    return result


def token_bound(value):
    # UTF-8 bytes upper-bound tokens conservatively; not a provider usage claim.
    return len(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8'))


def compact_owned(record,kind):
    """Remove storage duplication, never a semantic observation or authority term.

    Caller has already loaded an owned chat. Canonical message/issue references
    supersede interpretation-local candidate keys. Full receipts remain durable.
    """
    if record is None:return None
    result=dict(record)
    for key in ('conversation_id','candidate_key','target_refs','simulated'):
        result.pop(key,None)
    if kind not in ('approval','confirmation'):result.pop('actor_id',None)
    if kind in ('observation','attempt','action','question'):result.pop('created_at',None)
    if kind=='observation' and result.get('source_id')==result.get('message_id'):result.pop('source_id',None)
    if kind=='event':
        for key in ('worker_id','lease_until','dedupe_key','created_at'):result.pop(key,None)
    if kind=='action':
        result.pop('fingerprint',None)
        receipt=dict(result.get('receipt',{}))
        for key in ('actor_id','action_id','issue_id','parameters','simulated'):receipt.pop(key,None)
        result['receipt']=receipt
    if kind=='attempt' and 'reviewed_steps' in result:
        result.pop('reviewed_steps')
        result['reviewed_steps_from']='evidence_refs (full reviewed source remains retrievable)'
    if kind=='approval':
        scope=result.get('scope',{})
        if scope=={'requester':result.get('actor_id'),'issue_id':result.get('issue_id'),'resource_id':result.get('resource_id'),'permission':result.get('permission')}:
            result.pop('scope')
    return result


class ContextBuilder:
    def __init__(self,domain,targets=None):
        self.domain=domain
        # UTF-8 byte bounds, not measured provider tokens. The original 6k
        # interpretation bound failed ordinary three-goal/refusal flows (6560
        # mandatory bytes after structural compaction); 10k preserves headroom.
        self.targets=targets or {'interpretation':10000,'decision':12000}

    def build(self,stage,actor_id,conversation_id,event):
        state=self.domain.snapshot(actor_id,conversation_id)
        current=next((m for m in state['messages'] if m['id']==event.get('message_id')),None)
        selected=[];omitted=[]
        summary_keys=('id','goal','status','version','acceptance_criteria_id','service_refs','resource_id','lifecycle')
        data={'identity':self.domain.employee(actor_id),'catalog':self.domain.context_catalog(actor_id),'event':compact_owned(event,'event'),'state_version':state['conversation']['version'],
              'current_message':compact_owned(current,'message'),'issues':[{k:i[k] for k in summary_keys if k in i} for i in state['issues']],
              'authority':{'approvals':[compact_owned(r,'approval') for r in state['approvals']],'confirmations':[compact_owned(r,'confirmation') for r in state['confirmations']]},
              'constraints':['User reports and hypotheses are tentative; actual authorization is backend-owned.',
                'Each independent goal requires its own acceptance; relationships never propagate success.',
                'At most two distinct remedies, respect prior failures and user refusals.',
                'No arbitrary commands; procedural guidance must be rendered from reviewed KB.'],
              'pending_questions':[compact_owned(q,'question') for q in state['questions'] if q['status']=='pending'],
              'attempts':[compact_owned(r,'attempt') for r in state['attempts']],'actions':[compact_owned(r,'action') for r in state['actions']],
              'observations':[compact_owned(r,'observation') for r in state['observations']],'relations':[compact_owned(r,'relation') for r in state['relations']],'hypotheses':[compact_owned(r,'hypothesis') for r in state['hypotheses']],
              'interpretation':self.domain.get_interpretation(actor_id,conversation_id,current['id']) if current else None,
              'evidence':[],'recent_messages':[],'issue_details':[]}
        if data['interpretation']:
            data['interpretation']={k:[{'id':r['id']} if isinstance(r,dict) and r.get('id') else r for r in v] if isinstance(v,list) else v for k,v in data['interpretation'].items()}
        target=self.targets[stage]
        if token_bound(data)>target:raise ContextCapacityError('Mandatory identity, unresolved goals, authority and failure history exceed context capacity')
        for key in ('current_message','issues','authority','pending_questions','attempts','actions','observations','relations','hypotheses'):
            values=data[key] if isinstance(data[key],list) else [data[key]]
            for item in values:
                if isinstance(item,dict) and item.get('id'):selected.append(item['id'])
        # Relevant current issue details, recent turns, then source-labeled evidence.
        groups=[('issue_details',state['issues']),('recent_messages',list(reversed(state['messages'][-8:]))),('evidence',list(reversed(state['evidence'])))]
        for key,items in groups:
            for item in items:
                if current and key=='recent_messages' and item['id']==current['id']:continue
                data[key].append(compact_owned(item,'message') if key=='recent_messages' else item)
                if token_bound(data)>target:data[key].pop();omitted.append(item['id'])
                else:selected.append(item['id'])
        data['recent_messages'].reverse()
        included=set(selected)
        omitted.extend(m['id'] for m in state['messages'] if m['id'] not in included and m['id'] not in omitted)
        return {'data':data,'selected_refs':list(dict.fromkeys(selected)),'omitted_refs':list(dict.fromkeys(omitted)),
                'estimated_tokens':token_bound(data),'estimation':'conservative_utf8_bytes','target_tokens':target,'compaction_version':'owned-context-v1'}
