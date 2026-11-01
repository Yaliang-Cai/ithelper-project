"""Business persistence. Every connection is closed, including failed transactions."""
from contextlib import contextmanager, closing
from pathlib import Path
import json
import sqlite3
import time


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def unpack(row):
    if row is None:
        return None
    value = dict(row)
    for key in ('payload', 'receipt', 'scope', 'parameters', 'result'):
        if key in value and value[key] is not None:
            value[key] = json.loads(value[key])
    if 'payload' in value:
        payload = value.pop('payload')
        value = {**payload, **value}
    return value


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, active INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), csrf_hash TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, actor_id TEXT NOT NULL REFERENCES users(id), version INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), actor_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL, idempotency_key TEXT NOT NULL, UNIQUE(conversation_id,idempotency_key));
CREATE TABLE IF NOT EXISTS turn_submissions(actor_id TEXT NOT NULL REFERENCES users(id), conversation_id TEXT NOT NULL REFERENCES conversations(id), idempotency_key TEXT NOT NULL, proposal_hash TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(actor_id,conversation_id,idempotency_key));
CREATE TABLE IF NOT EXISTS issues(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), actor_id TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidate_batches(message_id TEXT PRIMARY KEY REFERENCES messages(id), content_hash TEXT NOT NULL, result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, message_id TEXT NOT NULL REFERENCES messages(id), actor_id TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS relations(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hypotheses(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, issue_id TEXT NOT NULL REFERENCES issues(id), version INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, actor_id TEXT NOT NULL, source TEXT NOT NULL, record_id TEXT NOT NULL, revision TEXT NOT NULL, content_hash TEXT NOT NULL, fetched_at TEXT NOT NULL, scope TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, issue_id TEXT NOT NULL REFERENCES issues(id), remedy_id TEXT NOT NULL, created_at REAL NOT NULL, payload TEXT NOT NULL, UNIQUE(issue_id,remedy_id));
CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, actor_id TEXT NOT NULL, issue_id TEXT NOT NULL REFERENCES issues(id), fingerprint TEXT UNIQUE NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, parameters TEXT NOT NULL, receipt TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS confirmations(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, actor_id TEXT NOT NULL, issue_id TEXT NOT NULL, fingerprint TEXT NOT NULL, decision TEXT NOT NULL, issued_at REAL NOT NULL, expires_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, actor_id TEXT NOT NULL, issue_id TEXT NOT NULL, resource_id TEXT NOT NULL, permission TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, expires_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS handoffs(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, actor_id TEXT NOT NULL, fingerprint TEXT UNIQUE NOT NULL, created_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, actor_id TEXT NOT NULL, issue_id TEXT, kind TEXT NOT NULL, dedupe_key TEXT UNIQUE NOT NULL, status TEXT NOT NULL, worker_id TEXT, lease_until REAL, created_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, actor_id TEXT NOT NULL, event_id TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL, created_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS questions(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, actor_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tool_calls(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, actor_id TEXT NOT NULL, created_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS world_records(kind TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(kind,id));
CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, service_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(actor_id,service_id));
CREATE TABLE IF NOT EXISTS vpn_sessions(id TEXT PRIMARY KEY, actor_id TEXT UNIQUE NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resources(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS access_grants(actor_id TEXT NOT NULL, resource_id TEXT NOT NULL REFERENCES resources(id), permission TEXT NOT NULL, revision INTEGER NOT NULL, action_id TEXT, PRIMARY KEY(actor_id,resource_id,permission));
CREATE INDEX IF NOT EXISTS event_queue ON events(status,lease_until,created_at);
CREATE INDEX IF NOT EXISTS issue_chat ON issues(conversation_id);
CREATE INDEX IF NOT EXISTS confirmation_fingerprint ON confirmations(fingerprint,issued_at);
"""


class Store:
    def __init__(self, db_path, clock=time.time):
        self.db_path = Path(db_path).resolve()
        self.clock = clock

    @contextmanager
    def read(self):
        with closing(sqlite3.connect(self.db_path, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            yield connection

    @contextmanager
    def write(self):
        with self.read() as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def initialize(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.read() as connection:
            connection.executescript(SCHEMA)
        from app.seed import seed_into
        with self.write() as connection:
            seed_into(connection)
        return self

    def reset_demo(self):
        """Reset only this explicitly selected business DB; never traverse runtime."""
        with self.write() as connection:
            tables = ['turn_submissions','tool_calls','questions','runs','events','handoffs','approvals','confirmations',
                      'actions','attempts','evidence','hypotheses','relations','observations','candidate_batches',
                      'issues','messages','conversations','sessions','access_grants','resources','accounts',
                      'vpn_sessions','world_records','users']
            for table in tables:
                connection.execute(f'DELETE FROM {table}')
            from app.seed import seed_into
            seed_into(connection)


def initialize(db_path):
    return Store(db_path).initialize()


def reset_demo(db_path):
    store = Store(db_path).initialize()
    store.reset_demo()
    return store
