from typing import Dict, Any
import sqlite3
from app.database import get_db, get_db_connection

def get_setting(key: str, default: Any = None) -> Any:
    """Retrieve a setting by key from the database."""
    try:
        db = get_db()
        row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row:
            return row["value"]
    except Exception:
        pass
    return default

def get_all_settings(db_path: str = None) -> Dict[str, str]:
    """Retrieve all settings as a dictionary."""
    if db_path:
        conn = get_db_connection(db_path)
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        conn.close()
    else:
        db = get_db()
        rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}

def set_setting(key: str, value: str, db_path: str = None):
    """Upsert a setting value."""
    if db_path:
        conn = get_db_connection(db_path)
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value))
            )
        conn.close()
    else:
        db = get_db()
        with db:
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value))
            )
