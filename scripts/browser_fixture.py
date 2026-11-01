"""SCRIPTED external-model fixture for verify_browser.py; never a runtime mode.

Uses real Agent, policies, auth, queue, worker and temporary SQLite. No keys,
paid providers, shared ledger, or production business state are accessed.
"""
import json
import os
from pathlib import Path

from app.config import Settings
from app.domain import Domain
from app.main import create_app
from app.providers import ProviderError
from app.storage import Store


def response(*, content=None, name=None, args=None):
    message = {'role': 'assistant', 'content': content, 'reasoning_content': 'fixture-private-reasoning'}
    if name:
        message['tool_calls'] = [{'id': 'fixture-call', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]
    return {'choices': [{'message': message}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}


class BrowserModel:
    def complete(self, messages, **kwargs):
        ctx = json.loads(messages[1]['content'])
        if ctx.get('current_message') and 'fixture failure' in ctx['current_message']['content']:
            raise ProviderError(category='unavailable')
        if kwargs.get('response_format'):
            proposal = {k: [] for k in ('message_intents', 'new_issue_candidates', 'observations', 'corrections', 'relation_candidates', 'unresolved_references')}
            if not ctx['issues']:
                quote = ctx['current_message']['content']
                proposal['new_issue_candidates'] = [
                    dict(candidate_key='vpn', goal='VPN connection', quote=quote, service_refs=['vpn'], criterion_id='vpn_recovery'),
                    dict(candidate_key='prod', goal='Production read', quote=quote, service_refs=['snowflake'], criterion_id='precise_access', resource_id='snowflake.prod.analytics'),
                ]
            return response(content=json.dumps(proposal))
        issue = next(i for i in ctx['issues'] if i['service_refs'] == ['vpn'])
        if not ctx['authority']['confirmations']:
            return response(name='reset_vpn_session', args={'issue_id': issue['id'], 'session_id': 'vpn.alice.sales'})
        production = next(i for i in ctx['issues'] if i['service_refs'] == ['snowflake'])
        if not ctx['authority']['approvals']:
            return response(name='request_approval', args=dict(issue_id=production['id'], resource_id='snowflake.prod.analytics', permission='read', reason='Scripted browser test request', evidence_refs=[]))
        approved = next((a for a in ctx['authority']['approvals'] if a['status'] == 'approved'), None)
        if approved and not ctx['actions']:
            return response(name='grant_catalog_access', args=dict(issue_id=production['id'], resource_id='snowflake.prod.analytics', permission='read', approval_id=approved['id']))
        if ctx['event']['kind'] == 'approval_decision' and approved and ctx['actions']:
            raise ProviderError(category='unavailable')
        return response(name='submit_turn', args=dict(reply='这是脚本浏览器验证。请查看各问题的实际状态卡。<img src=x onerror=alert(1)>', issue_updates=[], questions=[], question_updates=[], hypotheses=[], guidance=[], refinements=[]))


def factory():
    root = Path(os.environ['HELPDESK_BROWSER_WORLD'])
    d = Domain(Store(root / 'browser.sqlite').initialize())
    return create_app(Settings(runtime_dir=root), domain=d, provider=BrowserModel(), provider_mode='scripted_agent')
