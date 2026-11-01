"""Opaque local helpdesk sessions, separate from mock enterprise accounts."""
import hashlib
import secrets
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from app.domain import Domain, DomainError, require


def token_hash(token):
    require(isinstance(token,str) and len(token)<=512,'UNAUTHENTICATED','Invalid session')
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self,store,session_seconds=28800):
        self.store=store
        self.session_seconds=session_seconds
        self.hasher=PasswordHasher()
        self._dummy_hash=self.hasher.hash(secrets.token_urlsafe(32))

    def login(self,username,password):
        require(isinstance(username,str) and isinstance(password,str) and len(password)<=1024,'UNAUTHENTICATED','Invalid credentials')
        with self.store.write() as c:
            user=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
            try: valid=self.hasher.verify(user['password_hash'] if user else self._dummy_hash,password)
            except (VerificationError,InvalidHashError):valid=False
            require(valid and user is not None and user['active'],'UNAUTHENTICATED','Invalid credentials')
            session=secrets.token_urlsafe(48);csrf=secrets.token_urlsafe(32);expires=self.store.clock()+self.session_seconds
            c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(token_hash(session),user['id'],token_hash(csrf),expires))
            safe=Domain(self.store)._actor(c,user['id'])
            return {'user':safe,'session_token':session,'csrf_token':csrf,'expires_at':expires}

    def authenticate(self,session_token,csrf_token=None,require_csrf=False):
        with self.store.read() as c:
            session=c.execute('SELECT * FROM sessions WHERE token_hash=?',(token_hash(session_token),)).fetchone()
            require(session is not None and session['expires_at']>self.store.clock(),'UNAUTHENTICATED','Session expired or invalid')
            if require_csrf:require(csrf_token is not None and secrets.compare_digest(token_hash(csrf_token),session['csrf_hash']),'CSRF','CSRF token mismatch')
            return Domain(self.store)._actor(c,session['user_id'])

    def logout(self,session_token):
        with self.store.write() as c:
            c.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(session_token),))
