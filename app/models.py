from typing import Optional
from flask_login import UserMixin, LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from app.database import get_db, DEFAULT_SETTINGS

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to access your compliance workspace."
login_manager.login_message_category = "info"

csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address)


class User(UserMixin):
    def __init__(self, id: int, name: str, email: str, password_hash: str, created_at: Optional[str] = None):
        self.id = int(id)
        self.name = name
        self.email = email.lower().strip()
        self.password_hash = password_hash
        self.created_at = created_at

    @staticmethod
    def from_row(row) -> Optional["User"]:
        if not row:
            return None
        return User(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            password_hash=row["password_hash"],
            created_at=row["created_at"] if "created_at" in row.keys() else None
        )

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id: str) -> Optional[User]:
    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        return None
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return User.from_row(row)


def create_user(name: str, email: str, password: str, db=None) -> User:
    """Create a new user with hashed password and seed per-user default settings."""
    if db is None:
        db = get_db()
    clean_name = name.strip()
    clean_email = email.strip().lower()
    pw_hash = generate_password_hash(password)

    cur = db.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        (clean_name, clean_email, pw_hash)
    )
    user_id = cur.lastrowid

    for key, val in DEFAULT_SETTINGS.items():
        if key == "active_contract_id":
            val = ""
        db.execute(
            "INSERT OR IGNORE INTO settings (user_id, key, value) VALUES (?, ?, ?)",
            (user_id, key, val)
        )
    db.commit()

    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return User.from_row(row)


def get_user_by_email(email: str, db=None) -> Optional[User]:
    if db is None:
        db = get_db()
    clean_email = (email or "").strip().lower()
    row = db.execute("SELECT * FROM users WHERE email = ?", (clean_email,)).fetchone()
    return User.from_row(row)

