from typing import Dict, Any, Optional
from app.database import get_db, get_db_connection, get_current_user_id, DEFAULT_SETTINGS


def _resolve_uid(user_id: Optional[int] = None) -> int:
    if user_id is not None:
        return int(user_id)
    uid = get_current_user_id(default=0)
    return int(uid) if uid is not None else 0


def get_setting(key: str, default: Any = None, user_id: Optional[int] = None, db=None) -> Any:
    """Retrieve a per-user setting by key from the database, falling back to global default."""
    uid = _resolve_uid(user_id)
    try:
        conn = db if db is not None else get_db()
        row = conn.execute(
            "SELECT value FROM settings WHERE user_id = ? AND key = ?",
            (uid, key)
        ).fetchone()
        if row:
            return row["value"]
        if uid != 0:
            row0 = conn.execute(
                "SELECT value FROM settings WHERE user_id = 0 AND key = ?",
                (key,)
            ).fetchone()
            if row0:
                return row0["value"]
    except Exception:
        pass
    return default


def get_all_settings(db_path: str = None, user_id: Optional[int] = None, db=None) -> Dict[str, str]:
    """Retrieve all settings for a user as a dictionary."""
    uid = _resolve_uid(user_id)
    settings = dict(DEFAULT_SETTINGS)
    should_close = False
    if db is not None:
        conn = db
    elif db_path:
        conn = get_db_connection(db_path)
        should_close = True
    else:
        conn = get_db()

    try:
        rows0 = conn.execute("SELECT key, value FROM settings WHERE user_id = 0").fetchall()
        for r in rows0:
            settings[r["key"]] = r["value"]
        if uid != 0:
            rows_u = conn.execute("SELECT key, value FROM settings WHERE user_id = ?", (uid,)).fetchall()
            for r in rows_u:
                settings[r["key"]] = r["value"]
    finally:
        if should_close:
            conn.close()
    return settings


def set_setting(key: str, value: str, db_path: str = None, user_id: Optional[int] = None, db=None):
    """Upsert a per-user setting value."""
    uid = _resolve_uid(user_id)
    if db is not None:
        db.execute(
            "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?) ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
            (uid, key, str(value))
        )
        db.commit()
    elif db_path:
        conn = get_db_connection(db_path)
        with conn:
            conn.execute(
                "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?) ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
                (uid, key, str(value))
            )
        conn.close()
    else:
        conn = get_db()
        with conn:
            conn.execute(
                "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?) ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
                (uid, key, str(value))
            )
