import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory of the repository
BASE_DIR = Path(__file__).resolve().parent

# Load .env if present
load_dotenv(BASE_DIR / ".env")


def _normalize_db_url(url: str) -> str:
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


class Config:
    FLASK_ENV = os.getenv("FLASK_ENV", "development").lower()
    IS_PRODUCTION = FLASK_ENV == "production" or bool(os.getenv("RENDER"))

    SECRET_KEY = os.getenv("SECRET_KEY", "obligraph-default-dev-secret-key-2026")
    BASE_DIR = BASE_DIR
    DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "obligraph.db"))
    DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL", "").strip())

    UPLOAD_FOLDER = BASE_DIR / "uploads"
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB max upload

    # Session & Cookie Security
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = IS_PRODUCTION
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = IS_PRODUCTION

    # CSRF & Rate Limiting
    WTF_CSRF_ENABLED = True
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")

    # LLM Settings (default MOCK_LLM=true so production works without API keys)
    MOCK_LLM = os.getenv("MOCK_LLM", "true").lower() in ("true", "1", "yes")
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    # Default Engine Settings
    RISK_THRESHOLD_HIGH = float(os.getenv("RISK_THRESHOLD_HIGH", 70.0))
    RISK_THRESHOLD_MEDIUM = float(os.getenv("RISK_THRESHOLD_MEDIUM", 35.0))
    DISPUTE_WINDOW_HOURS = int(os.getenv("DISPUTE_WINDOW_HOURS", 48))

    # Trust Weights
    WEIGHT_SELF_REPORTED = float(os.getenv("WEIGHT_SELF_REPORTED", 0.4))
    WEIGHT_COUNTERPARTY_CONFIRMED = float(os.getenv("WEIGHT_COUNTERPARTY_CONFIRMED", 0.7))
    WEIGHT_INDEPENDENTLY_VERIFIABLE = float(os.getenv("WEIGHT_INDEPENDENTLY_VERIFIABLE", 1.0))

    # Risk Weights
    RISK_WEIGHT_DEVIATION = float(os.getenv("RISK_WEIGHT_DEVIATION", 0.45))
    RISK_WEIGHT_UNTRUSTED = float(os.getenv("RISK_WEIGHT_UNTRUSTED", 0.25))
    RISK_WEIGHT_DEADLINE = float(os.getenv("RISK_WEIGHT_DEADLINE", 0.30))

    # Connectors
    IMAP_HOST = os.getenv("IMAP_HOST", "")
    IMAP_USER = os.getenv("IMAP_USER", "")
    IMAP_PASS = os.getenv("IMAP_PASS", "")
    IMAP_PORT = int(os.getenv("IMAP_PORT", 993))
    IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")
    GOOGLE_SHEET_CSV_URL = os.getenv("GOOGLE_SHEET_CSV_URL", "")


class TestConfig(Config):
    TESTING = True
    IS_PRODUCTION = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    DATABASE_PATH = str(BASE_DIR / "test_obligraph.db")
    DATABASE_URL = ""
    MOCK_LLM = True
