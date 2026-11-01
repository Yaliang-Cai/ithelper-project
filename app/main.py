"""Same-origin local web API. Import has no Settings/ledger/network side effects."""
from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.agent import Agent
from app.auth import Auth
from app.budget import BudgetLedger
from app.config import Settings
from app.domain import Domain, DomainError, redact, require
from app.providers import DeepSeekClient, EmbeddingClient
from app.retrieval import Retrieval
from app.storage import Store
from app.tools import ToolExecutor
from app.worker import Worker

UI = Path(__file__).parent / 'ui'
PROJECT_LEDGER = Path(__file__).resolve().parents[1] / 'runtime' / 'usage.sqlite'
SESSION = 'helpdesk_session'
CSRF = 'helpdesk_csrf'


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(Body):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


class Conversation(Body):
    title: str = Field(default='New conversation', min_length=1, max_length=200)


class Message(Body):
    content: str = Field(min_length=1, max_length=32000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class Decision(Body):
    decision: str = Field(min_length=1, max_length=20)


def create_app(settings=None, *, domain=None, provider=None, provider_mode='live_agent', start_worker=True, secure_cookies=False):
    """Inject business world/provider explicitly; production loads keys only here.

    The project spending ledger is fixed at repository/runtime/usage.sqlite,
    independently of Settings.runtime_dir and injected business databases.
    Injected providers never create/reset a ledger.
    Use secure_cookies=True when serving this factory behind HTTPS.
    """
    if provider_mode not in ('live_agent', 'scripted_agent') or (provider is None and provider_mode != 'live_agent'):
        raise ValueError('Invalid run mode')
    settings = settings if settings is not None else Settings.load()
    domain = domain if domain is not None else Domain(Store(settings.runtime_dir / 'business.sqlite').initialize())
    auth = Auth(domain.store)
    if provider is None:
        provider = DeepSeekClient(settings, BudgetLedger(PROJECT_LEDGER, settings.llm_budget_usd))
    retrieval = Retrieval(EmbeddingClient(settings), cache_path=settings.runtime_dir / 'retrieval.sqlite')
    agent = Agent(domain, provider, ToolExecutor(domain, search=retrieval.search), run_mode=provider_mode)
    worker = Worker(domain, agent)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title='Local IT Helpdesk (simulated)', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.domain, app.state.agent, app.state.worker = domain, agent, worker

    def safe(value):
        # Field redaction plus exact configured-secret scrubbing. Settings is
        # never serialized; public configuration is an explicit allowlist.
        def scrub(item):
            if isinstance(item, dict):
                return {k: scrub(v) for k, v in item.items()}
            if isinstance(item, list):
                return [scrub(v) for v in item]
            if isinstance(item, str):
                for secret in (settings.deepseek_api_key, settings.siliconflow_api_key):
                    if secret:
                        item = item.replace(secret, '[redacted]')
            return item
        return scrub(redact(value))

    @app.middleware('http')
    async def security(request, call_next):
        # Reject browser cross-origin mutations, including login CSRF. CLI
        # clients have no Origin; authenticated writes still require CSRF.
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and (
            request.headers.get('sec-fetch-site') == 'cross-site'
            or (origin is not None and origin.rstrip('/') != str(request.base_url).rstrip('/'))
        ):
            response = JSONResponse({'error': {'code': 'CSRF', 'message': 'Cross-origin write denied'}}, status_code=403)
        else:
            response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        status = {'UNAUTHENTICATED': 401, 'CSRF': 403, 'FORBIDDEN': 403, 'NOT_FOUND': 404, 'CONFLICT': 409, 'STALE_STATE': 409, 'EXPIRED': 409}.get(exc.code, 400)
        return JSONResponse(safe({'error': {'code': exc.code, 'message': exc.message}}), status_code=status)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, exc):
        return JSONResponse({'error': {'code': 'STORAGE_UNAVAILABLE', 'message': 'Business storage is temporarily unavailable. Retry with the same message key.'}}, status_code=503)

    def actor(request: Request):
        return auth.authenticate(request.cookies.get(SESSION, ''))

    def writer(request: Request):
        return auth.authenticate(request.cookies.get(SESSION, ''), request.headers.get('x-csrf-token'), require_csrf=True)

    def role(user, name):
        require(name in user['roles'], 'FORBIDDEN', f'{name} role required')

    def event_poll(user, identifier):
        # Read an event ID immediately after enqueue, or an owned run ID later.
        if identifier.startswith('RUN-'):
            run = domain.get_run(user['id'], identifier)
            state = domain.snapshot(user['id'], run['conversation_id'])
            event = next(e for e in state['events'] if e['id'] == run['event_id'])
        else:
            with domain.store.read() as c:
                row = c.execute('SELECT conversation_id FROM events WHERE id=? AND actor_id=?', (identifier, user['id'])).fetchone()
                require(row is not None, 'FORBIDDEN', 'Event unavailable')
                cid = row['conversation_id']
            state = domain.snapshot(user['id'], cid)
            event = next(e for e in state['events'] if e['id'] == identifier)
            runs = [r for r in state['runs'] if r['event_id'] == identifier]
            run = runs[-1] if runs else None
        return {'event': public_event(event), 'run': public_run(run) if run else None}

    def public_event(e):
        return {k: e.get(k) for k in ('id', 'conversation_id', 'issue_id', 'kind', 'status', 'created_at', 'lease_until')}

    def public_run(r):
        return {k: r.get(k) for k in ('id', 'event_id', 'status', 'error_code', 'created_at', 'elapsed_seconds', 'model_calls', 'tool_calls', 'mode')}

    @app.post('/auth/login')
    def login(body: Login, request: Request, response: Response):
        result = auth.login(body.username, body.password)
        # Successful login invalidates the previously presented session, even
        # when switching identities. A failed login does not destroy it.
        previous = request.cookies.get(SESSION)
        if previous:
            auth.logout(previous)
        for name, value, http_only in ((SESSION, result['session_token'], True), (CSRF, result['csrf_token'], False)):
            response.set_cookie(name, value, httponly=http_only, secure=secure_cookies or request.url.scheme == 'https', samesite='strict', max_age=auth.session_seconds, path='/')
        return {'user': safe(result['user']), 'csrf_token': result['csrf_token']}

    @app.post('/auth/logout')
    def logout(request: Request, response: Response, user=Depends(writer)):
        auth.logout(request.cookies[SESSION])
        response.delete_cookie(SESSION, path='/'); response.delete_cookie(CSRF, path='/')
        return {'ok': True}

    @app.get('/me')
    def me(request: Request, user=Depends(actor)):
        return {'user': safe(user), 'csrf_token': request.cookies.get(CSRF), 'configuration': {'model': settings.deepseek_model, 'embedding_model': settings.embedding_model, 'simulated_business_systems': True}}

    @app.get('/health')
    def health():
        return {'status': 'recovering' if worker.last_error else 'ready', 'worker_running': worker.alive, 'recovery': worker.last_error}

    @app.get('/conversations')
    def conversations(user=Depends(actor)):
        return safe(domain.list_conversations(user['id']))

    @app.post('/conversations', status_code=201)
    def create(body: Conversation, user=Depends(writer)):
        return safe(domain.create_conversation(user['id'], body.title))

    @app.get('/conversations/{cid}')
    def snapshot(cid: str, user=Depends(actor)):
        state = domain.snapshot(user['id'], cid)
        result = {k: state[k] for k in ('conversation', 'messages', 'issues', 'evidence', 'actions', 'confirmations', 'approvals', 'handoffs', 'questions', 'all_resolved')}
        result['unassigned_feedback'] = [
            {k: item[k] for k in ('source_message_id', 'candidate_issue_ids')}
            for item in state['unassigned_feedback']
        ]
        result['events'] = [public_event(e) for e in state['events']]
        result['runs'] = [public_run(r) for r in state['runs']]
        result['processing'] = {'recovery_pending': any(e['id'] in worker.recovery_events and e['status'] == 'running' for e in state['events'])}
        return safe(result)

    @app.post('/conversations/{cid}/messages', status_code=202)
    def message(cid: str, body: Message, user=Depends(writer)):
        submitted = domain.submit_user_message(user['id'], cid, body.content, body.idempotency_key)
        return safe({'message': submitted['message'], 'event_id': submitted['event']['id'], 'status': submitted['event']['status']})

    @app.get('/runs/{identifier}/events')
    def poll(identifier: str, user=Depends(actor)):
        return safe(event_poll(user, identifier))

    @app.post('/confirmations/{identifier}/decision')
    def confirm(identifier: str, body: Decision, user=Depends(writer)):
        return safe(domain.decide_confirmation(user['id'], identifier, body.decision))

    @app.get('/approvals')
    def approvals(user=Depends(actor)):
        role(user, 'approver')
        return safe([a for a in domain.list_approvals(user['id']) if a['actor_id'] != user['id']])

    @app.post('/approvals/{identifier}/decision')
    def approve(identifier: str, body: Decision, user=Depends(writer)):
        return safe(domain.decide_approval(user['id'], identifier, body.decision))

    @app.get('/handoffs/{identifier}')
    def handoff(identifier: str, user=Depends(actor)):
        return safe(domain.get_handoff(user['id'], identifier))

    @app.get('/debug/runs')
    def debug_runs(user=Depends(actor)):
        role(user, 'debug')
        return safe([public_run(r) for r in domain.debug_snapshot(user['id'])['runs']])

    @app.get('/debug/runs/{identifier}')
    def debug_run(identifier: str, user=Depends(actor)):
        role(user, 'debug')
        return safe(domain.debug_snapshot(user['id'], identifier))

    @app.get('/')
    def index():
        return FileResponse(UI / 'index.html')

    @app.get('/approver')
    def approver_page(user=Depends(actor)):
        role(user, 'approver')
        return FileResponse(UI / 'index.html')

    @app.get('/debug')
    def debug_page(user=Depends(actor)):
        role(user, 'debug')
        return FileResponse(UI / 'index.html')

    # Mount only the packaged UI, never repository/runtime/eval directories.
    app.mount('/ui', StaticFiles(directory=UI), name='ui')
    return app
