import sqlite3
import os
from flask import g, current_app
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    raw_text TEXT
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
    obligation_id INTEGER NOT NULL,
    risk_score REAL NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    FOREIGN KEY (obligation_id) REFERENCES obligations (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

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

def get_db():
    """Get the active SQLite connection for the current Flask application context."""
    if 'db' not in g:
        db_path = current_app.config['DATABASE_PATH']
        g.db = sqlite3.connect(db_path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

def close_db(e=None):
    """Close the active SQLite connection if it exists."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

def get_db_connection(db_path: str):
    """Get a standalone connection to the database (for scripts, tests, background workers)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def get_readonly_db_connection(db_path: str):
    """Get a strictly read-only connection to the database for NL-to-SQL execution."""
    uri = f"file:{Path(db_path).resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path: str = None):
    """Initialize database tables and seed default settings if not already present."""
    if db_path is None:
        from config import Config
        db_path = Config.DATABASE_PATH
    
    # Ensure directory exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    conn = get_db_connection(db_path)
    with conn:
        conn.executescript(SCHEMA_SQL)
        for key, val in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, val)
            )
    conn.close()
