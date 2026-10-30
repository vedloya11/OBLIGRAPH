import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional, Sequence
from flask import g, current_app, abort
from flask_login import current_user

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    raw_text TEXT
);

CREATE TABLE IF NOT EXISTS contract_stagings (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    raw_text TEXT,
    records_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS obligations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL,
    clause_text TEXT NOT NULL,
    actor TEXT NOT NULL,
    trigger_deadline TEXT,
    required_evidence TEXT,
    expected_state TEXT,
    violation_condition TEXT,
    remediation_action TEXT,
    target_value REAL,
    unit TEXT,
    deadline_date TEXT,
    status TEXT DEFAULT 'PENDING',  -- PENDING, COMPLIANT, AT_RISK, VIOLATED
    confidence REAL DEFAULT 0.0,    -- 0 - 100%
    risk_score REAL DEFAULT 0.0,    -- 0 - 100
    risk_label TEXT DEFAULT 'LOW',  -- LOW, MEDIUM, HIGH
    dispute_deadline TEXT,          -- ISO timestamp when dispute window expires
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (contract_id) REFERENCES contracts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upstream_id INTEGER NOT NULL,
    downstream_id INTEGER NOT NULL,
    note TEXT,
    FOREIGN KEY (upstream_id) REFERENCES obligations (id) ON DELETE CASCADE,
    FOREIGN KEY (downstream_id) REFERENCES obligations (id) ON DELETE CASCADE,
    UNIQUE (upstream_id, downstream_id)
);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obligation_id INTEGER NOT NULL,
    source TEXT NOT NULL,           -- email, sheet, manual
    trust_level TEXT NOT NULL,      -- self_reported, counterparty_confirmed, independently_verifiable
    trust_weight REAL NOT NULL,     -- 0.0 to 1.0
    observed_value REAL,
    description TEXT NOT NULL,
    raw_ref TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (obligation_id) REFERENCES obligations (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS disputes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obligation_id INTEGER NOT NULL,
    submitted_by TEXT NOT NULL,
    side TEXT NOT NULL,             -- obligated_party, counterparty
    counter_evidence TEXT NOT NULL,
    attachment TEXT,
    status TEXT DEFAULT 'open',     -- open, resolved, expired
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (obligation_id) REFERENCES obligations (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    obligation_id INTEGER NOT NULL,
    risk_score REAL NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    FOREIGN KEY (obligation_id) REFERENCES obligations (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    user_id INTEGER NOT NULL DEFAULT 0,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
"""

POSTGRES_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS contracts (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL DEFAULT 1,
        title TEXT NOT NULL,
        filename TEXT NOT NULL,
        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        raw_text TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS contract_stagings (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        filename TEXT NOT NULL,
        raw_text TEXT,
        records_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS obligations (
        id SERIAL PRIMARY KEY,
        contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
        clause_text TEXT NOT NULL,
        actor TEXT NOT NULL,
        trigger_deadline TEXT,
        required_evidence TEXT,
        expected_state TEXT,
        violation_condition TEXT,
        remediation_action TEXT,
        target_value DOUBLE PRECISION,
        unit TEXT,
        deadline_date TEXT,
        status TEXT DEFAULT 'PENDING',
        confidence DOUBLE PRECISION DEFAULT 0.0,
        risk_score DOUBLE PRECISION DEFAULT 0.0,
        risk_label TEXT DEFAULT 'LOW',
        dispute_deadline TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dependencies (
        id SERIAL PRIMARY KEY,
        upstream_id INTEGER NOT NULL REFERENCES obligations(id) ON DELETE CASCADE,
        downstream_id INTEGER NOT NULL REFERENCES obligations(id) ON DELETE CASCADE,
        note TEXT,
        UNIQUE (upstream_id, downstream_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence (
        id SERIAL PRIMARY KEY,
        obligation_id INTEGER NOT NULL REFERENCES obligations(id) ON DELETE CASCADE,
        source TEXT NOT NULL,
        trust_level TEXT NOT NULL,
        trust_weight DOUBLE PRECISION NOT NULL,
        observed_value DOUBLE PRECISION,
        description TEXT NOT NULL,
        raw_ref TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS disputes (
        id SERIAL PRIMARY KEY,
        obligation_id INTEGER NOT NULL REFERENCES obligations(id) ON DELETE CASCADE,
        submitted_by TEXT NOT NULL,
        side TEXT NOT NULL,
        counter_evidence TEXT NOT NULL,
        attachment TEXT,
        status TEXT DEFAULT 'open',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS escalations (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL DEFAULT 1,
        obligation_id INTEGER NOT NULL REFERENCES obligations(id) ON DELETE CASCADE,
        risk_score DOUBLE PRECISION NOT NULL,
        reason TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        prev_hash TEXT NOT NULL,
        entry_hash TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        user_id INTEGER NOT NULL DEFAULT 0,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (user_id, key)
    )
    """
]

DEFAULT_SETTINGS = {
    "risk_threshold_high": "70.0",
    "risk_threshold_medium": "35.0",
    "dispute_window_hours": "48",
    "weight_self_reported": "0.4",
    "weight_counterparty_confirmed": "0.7",
    "weight_independently_verifiable": "1.0",
    "risk_weight_deviation": "0.45",
    "risk_weight_untrusted": "0.25",
    "risk_weight_deadline": "0.30",
    "active_contract_id": "1",
    "email_sync_last": "Never",
    "sheet_sync_last": "Never"
}


class DictRow:
    """Row wrapper supporting both dict-key access (row['id']) and index access (row[0])."""
    def __init__(self, columns: Sequence[str], values: Sequence[Any]):
        self._columns = list(columns)
        self._values = list(values)
        self._map = dict(zip(self._columns, self._values))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._map[key]

    def get(self, key, default=None):
        return self._map.get(key, default)

    def keys(self):
        return self._columns

    def values(self):
        return self._values

    def items(self):
        return self._map.items()

    def __iter__(self):
        return iter(self._columns)

    def __len__(self):
        return len(self._values)


class PostgresCursorWrapper:
    """Cursor wrapper for SQLAlchemy/PostgreSQL connections matching sqlite3.Cursor API."""
    def __init__(self, result, lastrowid=None):
        self._result = result
        self.lastrowid = lastrowid
        self.description = [(col,) for col in result.keys()] if result.returns_rows else None
        self._rows = [DictRow(list(result.keys()), list(r)) for r in result.fetchall()] if result.returns_rows else []
        self._idx = 0

    def fetchone(self):
        if self._idx < len(self._rows):
            row = self._rows[self._idx]
            self._idx += 1
            return row
        return None

    def fetchall(self):
        if self._idx == 0:
            self._idx = len(self._rows)
            return list(self._rows)
        rem = self._rows[self._idx:]
        self._idx = len(self._rows)
        return rem

    def fetchmany(self, size=100):
        rem = self._rows[self._idx:self._idx + size]
        self._idx += len(rem)
        return rem


_pg_engines = {}


def _get_pg_engine(database_url: str):
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    if database_url.startswith("postgresql://"):
        try:
            import psycopg2  # noqa: F401
        except ImportError:
            database_url = database_url.replace("postgresql://", "postgresql+pg8000://", 1)
    if database_url not in _pg_engines:
        from sqlalchemy import create_engine
        _pg_engines[database_url] = create_engine(database_url, pool_pre_ping=True)
    return _pg_engines[database_url]


class PostgresConnectionWrapper:
    """SQLAlchemy-backed PostgreSQL connection wrapper mimicking sqlite3.Connection."""
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._engine = _get_pg_engine(database_url)
        self._conn = self._engine.connect()
        self._trans = self._conn.begin()

    def _translate_sql(self, sql: str) -> tuple[str, bool]:
        s = sql.strip()
        # Handle INSERT OR IGNORE
        if re.match(r"^INSERT\s+OR\s+IGNORE\s+INTO\b", s, re.IGNORECASE):
            s = re.sub(r"^INSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", s, flags=re.IGNORECASE)
            if "ON CONFLICT" not in s.upper():
                s = s.rstrip(";") + " ON CONFLICT DO NOTHING"
        # Handle INSERT OR REPLACE INTO settings
        if re.match(r"^INSERT\s+OR\s+REPLACE\s+INTO\s+settings\b", s, re.IGNORECASE):
            s = re.sub(r"^INSERT\s+OR\s+REPLACE\s+INTO\s+settings\b", "INSERT INTO settings", s, flags=re.IGNORECASE)
            if "ON CONFLICT" not in s.upper():
                s = s.rstrip(";") + " ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value"

        is_insert_with_id = bool(
            re.match(r"^INSERT\s+INTO\s+(users|contracts|obligations|dependencies|evidence|disputes|escalations)\b", s, re.IGNORECASE)
            and "RETURNING" not in s.upper()
            and "ON CONFLICT DO NOTHING" not in s.upper()
        )
        if is_insert_with_id:
            s = s.rstrip(";") + " RETURNING id"
        return s, is_insert_with_id

    def execute(self, sql: str, params: Sequence[Any] = ()) -> PostgresCursorWrapper:
        from sqlalchemy import text
        if sql.strip().upper().startswith("PRAGMA"):
            class _DummyResult:
                returns_rows = False
            return PostgresCursorWrapper(_DummyResult(), None)

        pg_sql, has_returning = self._translate_sql(sql)
        bind_dict = {}
        param_idx = 0

        def _repl(_match):
            nonlocal param_idx
            key = f"p{param_idx}"
            bind_dict[key] = params[param_idx]
            param_idx += 1
            return f":{key}"

        bound_sql = re.sub(r"\?", _repl, pg_sql)
        result = self._conn.execute(text(bound_sql), bind_dict)
        lastrowid = None
        if has_returning and result.returns_rows:
            rows = result.fetchall()
            if rows:
                lastrowid = rows[0][0]
            class _EmptyResult:
                returns_rows = False
            return PostgresCursorWrapper(_EmptyResult(), lastrowid)
        return PostgresCursorWrapper(result, lastrowid)

    def executescript(self, script: str):
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt:
                self.execute(stmt)

    def commit(self):
        if self._trans and self._trans.is_active:
            self._trans.commit()
            self._trans = self._conn.begin()

    def rollback(self):
        if self._trans and self._trans.is_active:
            self._trans.rollback()
            self._trans = self._conn.begin()

    def close(self):
        try:
            if self._trans and self._trans.is_active:
                self._trans.commit()
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()


def _is_postgres_url(url_or_path: Optional[str]) -> bool:
    if not url_or_path:
        return False
    return url_or_path.startswith("postgresql://") or url_or_path.startswith("postgres://")


def _resolve_db_target(db_path: Optional[str] = None) -> str:
    if db_path:
        return db_path
    try:
        if current_app:
            db_url = current_app.config.get("DATABASE_URL")
            if db_url:
                return db_url
            return current_app.config.get("DATABASE_PATH")
    except Exception:
        pass
    from config import Config
    return Config.DATABASE_URL or Config.DATABASE_PATH


def get_db():
    """Get the active database connection for the current Flask application context."""
    if 'db' not in g:
        target = _resolve_db_target()
        g.db = get_db_connection(target)
    return g.db


def close_db(e=None):
    """Close the active database connection if it exists."""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def get_db_connection(db_path: str = None):
    """Get a standalone connection to the database (SQLite or PostgreSQL)."""
    target = _resolve_db_target(db_path)
    if _is_postgres_url(target):
        return PostgresConnectionWrapper(target)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_readonly_db_connection(db_path: str):
    """Get a strictly read-only connection to the database for NL-to-SQL execution."""
    uri = f"file:{Path(db_path).resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_sqlite_schema(conn: sqlite3.Connection):
    """Idempotently add multi-tenant columns to existing SQLite tables if missing."""
    # Check contracts.user_id
    cols_contracts = {r[1] for r in conn.execute("PRAGMA table_info(contracts)").fetchall()}
    if "user_id" not in cols_contracts:
        conn.execute("ALTER TABLE contracts ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")

    # Check escalations.user_id
    cols_escalations = {r[1] for r in conn.execute("PRAGMA table_info(escalations)").fetchall()}
    if "user_id" not in cols_escalations:
        conn.execute("ALTER TABLE escalations ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")

    # Check settings.user_id
    cols_settings = {r[1] for r in conn.execute("PRAGMA table_info(settings)").fetchall()}
    if cols_settings and "user_id" not in cols_settings:
        old_rows = conn.execute("SELECT key, value FROM settings").fetchall()
        conn.execute("DROP TABLE settings")
        conn.execute("""
            CREATE TABLE settings (
                user_id INTEGER NOT NULL DEFAULT 0,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (user_id, key)
            )
        """)
        for r in old_rows:
            conn.execute(
                "INSERT OR IGNORE INTO settings (user_id, key, value) VALUES (0, ?, ?)",
                (r[0], r[1])
            )


def init_db(db_path: str = None):
    """Initialize database tables, run migrations, and seed default settings."""
    target = _resolve_db_target(db_path)

    if _is_postgres_url(target):
        conn = PostgresConnectionWrapper(target)
        with conn:
            for stmt in POSTGRES_SCHEMA_STATEMENTS:
                conn.execute(stmt)
            for key, val in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT INTO settings (user_id, key, value) VALUES (0, ?, ?) ON CONFLICT (user_id, key) DO NOTHING",
                    (key, val)
                )
        conn.close()
        return

    # SQLite initialization
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = get_db_connection(target)
    with conn:
        conn.executescript(SCHEMA_SQL)
        _migrate_sqlite_schema(conn)
        for key, val in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (user_id, key, value) VALUES (0, ?, ?)",
                (key, val)
            )
    conn.close()


def get_current_user_id(default: Optional[int] = None) -> Optional[int]:
    """Return the authenticated user's ID, or `default` if outside auth context."""
    try:
        if current_user and current_user.is_authenticated:
            return int(current_user.id)
    except Exception:
        pass
    return default


def get_owned_or_404(resource_type: str, resource_id: int, user_id: Optional[int] = None, db=None):
    """
    Fetch a resource owned by `user_id` (defaults to current_user.id) or abort with HTTP 404.
    Supported resource_type values:
      - 'contract'
      - 'obligation'
      - 'dependency'
      - 'evidence'
      - 'dispute'
      - 'escalation'
    """
    if db is None:
        db = get_db()
    if user_id is None:
        user_id = get_current_user_id()
    if user_id is None:
        abort(404)

    rtype = resource_type.lower().strip()
    row = None

    if rtype == "contract":
        row = db.execute(
            "SELECT * FROM contracts WHERE id = ? AND user_id = ?",
            (resource_id, user_id)
        ).fetchone()
    elif rtype == "obligation":
        row = db.execute("""
            SELECT o.*, c.title as contract_title, c.user_id as owner_id
            FROM obligations o
            JOIN contracts c ON o.contract_id = c.id
            WHERE o.id = ? AND c.user_id = ?
        """, (resource_id, user_id)).fetchone()
    elif rtype == "dependency":
        row = db.execute("""
            SELECT d.*, c.user_id as owner_id
            FROM dependencies d
            JOIN obligations o ON d.upstream_id = o.id
            JOIN contracts c ON o.contract_id = c.id
            WHERE d.id = ? AND c.user_id = ?
        """, (resource_id, user_id)).fetchone()
    elif rtype == "evidence":
        row = db.execute("""
            SELECT e.*, o.clause_text, o.actor, c.user_id as owner_id
            FROM evidence e
            JOIN obligations o ON e.obligation_id = o.id
            JOIN contracts c ON o.contract_id = c.id
            WHERE e.id = ? AND c.user_id = ?
        """, (resource_id, user_id)).fetchone()
    elif rtype == "dispute":
        row = db.execute("""
            SELECT d.*, o.clause_text, o.actor, o.risk_score, o.risk_label,
                   o.status as obligation_status, o.target_value, o.unit,
                   o.dispute_deadline, o.confidence, c.title as contract_title,
                   c.user_id as owner_id
            FROM disputes d
            JOIN obligations o ON d.obligation_id = o.id
            JOIN contracts c ON o.contract_id = c.id
            WHERE d.id = ? AND c.user_id = ?
        """, (resource_id, user_id)).fetchone()
    elif rtype == "escalation":
        row = db.execute("""
            SELECT e.*
            FROM escalations e
            WHERE e.id = ? AND e.user_id = ?
        """, (resource_id, user_id)).fetchone()
    else:
        raise ValueError(f"Unknown resource_type: {resource_type}")

    if not row:
        abort(404)
    return row
