import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import init_db
from config import Config

def main():
    print(f"Initializing Obligraph database at: {Config.DATABASE_PATH}")
    init_db(Config.DATABASE_PATH)
    print("Database schema and default settings initialized successfully.")

if __name__ == "__main__":
    main()
