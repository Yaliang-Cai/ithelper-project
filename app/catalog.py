"""Single model-visible business tool catalog and execution argument validator."""
from app.domain import fields, require, text_value


def string(description='', enum=None):
    value={'type':'string','description':description,'minLength':1,'maxLength':8000}
    if enum:value['enum']=enum
    return value


def array(items=None):
    return {'type':'array','items':items or string(),'maxItems':100}


TOOL_DEFINITIONS={
    'get_employee_context':('Read authenticated employee, device, region and visible resource catalog.',{},[]),
    'inspect_account':('Read only your own linked account.',{'service_id':string()},['service_id']),
    'inspect_access':('Read your exact permission on a catalog resource.',{'resource_id':string()},['resource_id']),
    'inspect_vpn':('Read your VPN session and observation window.',{},[]),
    'get_service_context':('Read service health, regional incidents, changes and dependency counterevidence.',{'service_id':string()},['service_id']),
    'search_kb':('Search reviewed knowledge; check applicability and version before using remedies.',{'query':string(),'filters':{'type':'object','properties':{'service_id':string(),'version':string(),'region':string()},'additionalProperties':False},'top_k':{'type':'integer','minimum':1,'maximum':10}},['query']),
    'search_history':('Search curated general cases, never other employee chats.',{'query':string(),'filters':{'type':'object','properties':{'service_id':string(),'version':string(),'region':string()},'additionalProperties':False},'top_k':{'type':'integer','minimum':1,'maximum':10}},['query']),
    'get_action_requirements':('Read authoritative policy, acceptance criteria, allowed remedies and exact resource IDs.',{'action':string(enum=['unlock_account','grant_catalog_access','reset_vpn_session','request_approval','create_handoff'])},[]),
    'get_approval':('Read only your own issue approval; user claims are not approval events.',{'approval_id':string(),'issue_id':string()},[]),
    'get_action_result':('Recover backend-issued action receipt; never blindly repeat an uncertain write.',{'action_id':string()},['action_id']),
    'read_conversation_context':('Read immutable messages from this chat by ID, never another chat.',{'message_ids':array(),'limit':{'type':'integer','minimum':1,'maximum':50}},[]),
    'unlock_account':('Unlock own ordinary lock only. Never security-disabled accounts or MFA.',{'issue_id':string(),'service_id':string(),'evidence_refs':array()},['issue_id','service_id']),
    'grant_catalog_access':('Grant exact eligible catalog read permission after required approval.',{'issue_id':string(),'resource_id':string(),'permission':string(),'approval_id':string(),'evidence_refs':array()},['issue_id','resource_id','permission']),
    'reset_vpn_session':('Disruptively reset your exact VPN session after backend confirmation.',{'issue_id':string(),'session_id':string(),'confirmation_id':string(),'evidence_refs':array()},['issue_id','session_id']),
    'request_approval':('Create a pending exact-scope request; this creates no permission.',{'issue_id':string(),'resource_id':string(),'permission':string(),'reason':string(),'evidence_refs':array()},['issue_id','resource_id','permission','reason','evidence_refs']),
    'create_handoff':('Persist a simulated handoff with facts, assumptions, evidence, attempts and questions.',{'issue_refs':array(),'summary':string(),'facts':array({'anyOf':[string(),{'type':'object'}]}),'hypotheses':array({'anyOf':[string(),{'type':'object'}]}),'attempt_refs':array(),'evidence_refs':array(),'open_questions':array(),'reason':string(),'team_hint':string()},['issue_refs','summary','facts','hypotheses','attempt_refs','evidence_refs','open_questions','reason','team_hint']),
}
TOOL_SCHEMAS=[{'type':'function','function':{'name':name,'description':description,'parameters':{'type':'object','properties':properties,'required':required,'additionalProperties':False}}} for name,(description,properties,required) in TOOL_DEFINITIONS.items()]


def validate_value(value,schema):
    if 'anyOf' in schema:
        require(isinstance(value,(str,dict)));return
    kind=schema['type']
    if kind=='string':
        text_value(value,schema.get('maxLength',8000))
        if 'enum' in schema:require(value in schema['enum'])
    elif kind=='array':
        require(isinstance(value,list) and len(value)<=schema.get('maxItems',100))
        for item in value:validate_value(item,schema['items'])
    elif kind=='integer':require(type(value) is int and schema.get('minimum',value)<=value<=schema.get('maximum',value))
    elif kind=='object':
        require(isinstance(value,dict))
        if 'properties' in schema:
            fields(value,schema['properties'],schema.get('required',[]))
            for key,item in value.items():validate_value(item,schema['properties'][key])


def validate_arguments(name,arguments):
    require(name in TOOL_DEFINITIONS,'INVALID_ARGUMENT','Unknown business tool')
    _,properties,required=TOOL_DEFINITIONS[name]
    fields(arguments,properties,required)
    for key,value in arguments.items():validate_value(value,properties[key])
