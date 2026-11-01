import pytest

from app.auth import Auth
from app.domain import Domain, DomainError
from app.storage import Store


def test_argon_sessions_csrf_expiry_logout_and_safe_debug(tmp_path):
    now=[1804300000.0]
    store=Store(tmp_path/'business.db',clock=lambda:now[0]); store.initialize()
    auth=Auth(store)
    with pytest.raises(DomainError,match='UNAUTHENTICATED'):
        auth.login('alice.sales','wrong')
    first=auth.login('alice.sales','DemoPass123!')
    second=auth.login('alice.sales','DemoPass123!')
    assert first['session_token']!=second['session_token']
    assert auth.authenticate(first['session_token'])['id']=='alice.sales'
    with pytest.raises(DomainError,match='CSRF'):
        auth.authenticate(first['session_token'],'wrong',require_csrf=True)
    assert auth.authenticate(first['session_token'],first['csrf_token'],require_csrf=True)['id']=='alice.sales'
    with store.read() as c:
        user=c.execute('SELECT password_hash FROM users WHERE id=?',('alice.sales',)).fetchone()
        assert user[0].startswith('$argon2id$')
        assert first['session_token'] not in str([tuple(r) for r in c.execute('SELECT * FROM sessions')])
    auth.logout(first['session_token'])
    with pytest.raises(DomainError,match='UNAUTHENTICATED'): auth.authenticate(first['session_token'])
    now[0]+=86401
    with pytest.raises(DomainError,match='UNAUTHENTICATED'): auth.authenticate(second['session_token'])
    domain=Domain(store)
    with pytest.raises(DomainError,match='FORBIDDEN'): domain.debug_snapshot('alice.sales')
    safe=str(domain.debug_snapshot('dev.debug'))
    assert '$argon2' not in safe and 'token_hash' not in safe and 'csrf' not in safe


def test_reset_preserves_other_database_and_seed_is_idempotent(tmp_path):
    ledger=tmp_path/'spending.sqlite3'; ledger.write_bytes(b'keep accounting')
    store=Store(tmp_path/'business.db'); store.initialize(); store.initialize()
    assert len(Domain(store).demo_users())==6
    store.reset_demo()
    assert ledger.read_bytes()==b'keep accounting'
