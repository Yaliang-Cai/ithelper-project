"""Atomic persistent project-wide cost reservations, in integer nano-USD."""
from contextlib import contextmanager
import math
from pathlib import Path
import sqlite3
import uuid

class BudgetExceeded(RuntimeError):
    pass

class BudgetOverrun(BudgetExceeded):
    """Incurred cost exceeded a reservation; ledger is now fail-closed."""

class BudgetLedger:
    def __init__(self, path, limit_usd=4.0):
        if not math.isfinite(limit_usd) or not 0 < limit_usd <= 4:
            raise ValueError('Invalid budget limit')
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.limit = self._units(limit_usd)
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS ledger_state (id INTEGER PRIMARY KEY CHECK(id=1), accounting_overrun INTEGER NOT NULL DEFAULT 0)')
            db.execute('INSERT OR IGNORE INTO ledger_state(id) VALUES(1)')
            db.execute('CREATE TABLE IF NOT EXISTS costs (id TEXT PRIMARY KEY, purpose TEXT NOT NULL, reserved INTEGER NOT NULL, spent INTEGER, created TEXT DEFAULT CURRENT_TIMESTAMP)')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level='IMMEDIATE')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _units(amount):
        if not math.isfinite(amount) or amount < 0:
            raise ValueError('Invalid cost')
        return math.ceil(amount * 1_000_000_000)

    def reserve(self, amount_usd, *, purpose):
        amount = self._units(amount_usd)
        reservation = uuid.uuid4().hex
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            used = db.execute('SELECT COALESCE(SUM(COALESCE(spent,reserved)),0) FROM costs').fetchone()[0]
            overrun = db.execute('SELECT accounting_overrun FROM ledger_state WHERE id=1').fetchone()[0]
            if overrun or used >= self.limit or used + amount > self.limit:
                raise BudgetExceeded('Cumulative project LLM budget exhausted')
            db.execute('INSERT INTO costs(id,purpose,reserved) VALUES(?,?,?)',(reservation,purpose,amount))
        return reservation

    def settle(self, reservation, actual_usd):
        amount = self._units(actual_usd)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT spent,reserved FROM costs WHERE id=?',(reservation,)).fetchone()
            if row is None:
                raise ValueError('Unknown reservation')
            if row[0] is not None and row[0] != amount:
                raise ValueError('Reservation already settled')
            db.execute('UPDATE costs SET spent=? WHERE id=?',(amount,reservation))
            overrun = amount > row[1]
            if overrun:
                db.execute('UPDATE ledger_state SET accounting_overrun=1 WHERE id=1')
        # Raise only after committing the truthful incurred cost and circuit breaker.
        if overrun:
            raise BudgetOverrun('Actual provider cost exceeded reservation; further requests disabled')

    def report(self):
        with self._connect() as db:
            spent,reserved,count = db.execute('SELECT COALESCE(SUM(spent),0), COALESCE(SUM(CASE WHEN spent IS NULL THEN reserved ELSE 0 END),0), COUNT(*) FROM costs').fetchone()
            overrun = bool(db.execute('SELECT accounting_overrun FROM ledger_state WHERE id=1').fetchone()[0])
        return {'accounting_overrun':overrun,'spent_usd':spent/1e9,'reserved_usd':reserved/1e9,'limit_usd':self.limit/1e9,'requests':count}


