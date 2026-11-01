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


class ContextBuilder:
    def __init__(self,domain,targets=None):
        self.domain=domain
        self.targets=targets or {'interpretation':6000,'decision':12000}

    def build(self,stage,actor_id,conversation_id,event):
        state=self.domain.snapshot(actor_id,conversation_id)
        current=next((m for m in state['messages'] if m['id']==event.get('message_id')),None)
        selected=[];omitted=[]
        summary_keys=('id','goal','status','version','acceptance_criteria_id','service_refs','resource_id','lifecycle')
        data={'identity':self.domain.employee(actor_id),'catalog':self.domain.context_catalog(actor_id),'event':event,'state_version':state['conversation']['version'],
              'current_message':current,'issues':[{k:i[k] for k in summary_keys if k in i} for i in state['issues']],
              'authority':{'approvals':state['approvals'],'confirmations':state['confirmations']},
              'constraints':['User reports and hypotheses are tentative; actual authorization is backend-owned.',
                'Each independent goal requires its own acceptance; relationships never propagate success.',
                'At most two distinct remedies, respect prior failures and user refusals.',
                'No arbitrary commands; procedural guidance must be rendered from reviewed KB.'],
              'pending_questions':[q for q in state['questions'] if q['status']=='pending'],
              'attempts':state['attempts'],'actions':state['actions'],
              'observations':state['observations'],'relations':state['relations'],'hypotheses':state['hypotheses'],
              'interpretation':self.domain.get_interpretation(actor_id,conversation_id,current['id']) if current else None,
              'evidence':[],'recent_messages':[],'issue_details':[]}
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
                data[key].append(item)
                if token_bound(data)>target:data[key].pop();omitted.append(item['id'])
                else:selected.append(item['id'])
        data['recent_messages'].reverse()
        included=set(selected)
        omitted.extend(m['id'] for m in state['messages'] if m['id'] not in included and m['id'] not in omitted)
        return {'data':data,'selected_refs':list(dict.fromkeys(selected)),'omitted_refs':list(dict.fromkeys(omitted)),
                'estimated_tokens':token_bound(data),'estimation':'conservative_utf8_bytes','target_tokens':target}
