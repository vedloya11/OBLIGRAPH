import pytest
import os
import sqlite3
from pathlib import Path
from config import TestConfig
from app import create_app
from app.database import init_db

@pytest.fixture
def app():
    # Setup test DB path
    test_db_path = Path("test_obligraph.db").resolve()
    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except Exception:
            pass

    app = create_app(TestConfig)
    yield app

    # Teardown test DB
    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except Exception:
            pass

@pytest.fixture
def client(app):
    return app.test_client()

def test_database_tables_exist(app):
    """Test that all 7 required schema tables exist in the initialized SQLite DB."""
    with app.app_context():
        conn = sqlite3.connect(TestConfig.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        expected_tables = {
            "contracts", "obligations", "dependencies",
            "evidence", "disputes", "escalations", "settings"
        }
        for table in expected_tables:
            assert table in tables, f"Expected table '{table}' was not created in database."

def test_default_settings_seeded(app):
    """Test that default engine settings are seeded on initialization."""
    with app.app_context():
        conn = sqlite3.connect(TestConfig.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        settings = dict(cursor.fetchall())
        conn.close()

        assert "risk_threshold_high" in settings
        assert float(settings["risk_threshold_high"]) == 70.0
        assert "dispute_window_hours" in settings
        assert int(settings["dispute_window_hours"]) == 48
        assert "weight_self_reported" in settings

def test_landing_page_renders(client):
    """Test that the landing page renders with hero and 6-stage pipeline."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Autonomous Contract-to-Execution Obligation Graph Engine" in html
    assert "STAGE 01" in html
    assert "Contract Ingestion" in html
    assert "STAGE 06" in html
    assert "Hash-Chain Escalation" in html
    assert "Open Compliance Dashboard" in html

def test_dashboard_shell_renders(client):
    """Test that the dashboard shell renders with KPIs and empty states."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Obligation Intelligence Dashboard" in html
    assert "Total Obligations" in html
    assert "Compliance Rate" in html
    assert "No Obligations Ingested Yet" in html or "table-modern" in html

def test_navigation_routes_render(client):
    """Test that all blueprint endpoints resolve cleanly."""
    routes = [
        "/contracts/",
        "/contracts/upload",
        "/obligations/",
        "/obligations/graph",
        "/evidence/",
        "/disputes/",
        "/escalations/",
        "/query/",
        "/settings/"
    ]
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200, f"Route {route} failed with status {response.status_code}"

def test_settings_update(client):
    """Test that engine settings can be updated via the settings page."""
    post_data = {
        "risk_threshold_high": "75.0",
        "risk_threshold_medium": "40.0",
        "dispute_window_hours": "72",
        "weight_self_reported": "0.35",
        "weight_counterparty_confirmed": "0.75",
        "weight_independently_verifiable": "1.0",
        "risk_weight_deviation": "0.50",
        "risk_weight_untrusted": "0.20",
        "risk_weight_deadline": "0.30",
        "google_sheet_csv_url": "https://example.com/test.csv"
    }
    response = client.post("/settings/", data=post_data, follow_redirects=True)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Engine parameters updated successfully" in html
    assert "75.0" in html

def test_404_error_page(client):
    """Test custom 404 error handler."""
    response = client.get("/nonexistent-route-for-testing")
    assert response.status_code == 404
    html = response.get_data(as_text=True)
    assert "Obligation Not Found" in html
