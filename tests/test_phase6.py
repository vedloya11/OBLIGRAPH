import pytest
import sqlite3
import datetime
from pathlib import Path
from config import TestConfig
from app import create_app
from app.database import get_db_connection
from engine.hash_chain import add_escalation_entry, verify_chain

@pytest.fixture
def app():
    test_db_path = Path("test_obligraph.db").resolve()
    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except Exception:
            pass

    app = create_app(TestConfig)
    
    conn = get_db_connection(TestConfig.DATABASE_PATH)
    with conn:
        # Create Contract A
        cur = conn.execute(
            "INSERT INTO contracts (title, filename, raw_text) VALUES (?, ?, ?)",
            ("Master Cloud Services Agreement", "mcsa_2026.pdf", "Contract text for MCSA")
        )
        c_a = cur.lastrowid

        # Create Contract B
        cur = conn.execute(
            "INSERT INTO contracts (title, filename, raw_text) VALUES (?, ?, ?)",
            ("Logistics & Transport Framework", "logistics_2026.docx", "Contract text for logistics")
        )
        c_b = cur.lastrowid
        
        # Today's reference date
        today = datetime.date.today()
        d_upcoming_5 = (today + datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        d_upcoming_20 = (today + datetime.timedelta(days=20)).strftime("%Y-%m-%d")
        d_overdue_3 = (today - datetime.timedelta(days=3)).strftime("%Y-%m-%d")

        # Obligations for Contract A
        conn.execute("""
            INSERT INTO obligations (
                contract_id, clause_text, actor, trigger_deadline, required_evidence,
                expected_state, violation_condition, remediation_action, target_value,
                unit, deadline_date, status, confidence, risk_score, risk_label
            ) VALUES 
            (?, 'Clause 4.1: ISO 27001 Annual Recertification Audit', 'Provider Security', 'Annual by Q4', 'Audit Certificate', 'Audit completed', 'Expired cert', 'Grace 14d', 1.0, 'audit', ?, 'COMPLIANT', 90.0, 10.0, 'LOW'),
            (?, 'Clause 5.2: Uptime Guarantee >= 99.95% Monthly', 'Cloud Ops', 'Monthly 1st', 'Datadog SLA Export', '99.95% availability', '< 99.95% uptime', 'Penalty credit', 99.95, '%', ?, 'VIOLATED', 45.0, 85.0, 'HIGH'),
            (?, 'Clause 6.3: Incident Post-Mortem within 48 Hours', 'SRE Team', '48h after P1', 'Incident Root Cause Doc', 'Submit within 48h', '> 48h elapsed', 'Review call', 48.0, 'hours', ?, 'AT_RISK', 65.0, 48.0, 'MEDIUM')
        """, (c_a, d_upcoming_20, c_a, d_overdue_3, c_a, d_upcoming_5))

        # Obligations for Contract B
        conn.execute("""
            INSERT INTO obligations (
                contract_id, clause_text, actor, trigger_deadline, required_evidence,
                expected_state, violation_condition, remediation_action, target_value,
                unit, deadline_date, status, confidence, risk_score, risk_label
            ) VALUES 
            (?, 'Clause 1.2: Minimum Fleet Availability 50 Vehicles', 'Freight Co', 'Daily 06:00', 'GPS Telematics', '50 active trucks', '< 50 trucks', 'Contract default', 50.0, 'trucks', ?, 'COMPLIANT', 92.0, 12.0, 'LOW')
        """, (c_b, d_upcoming_5))

        # Add an open dispute on obligation #2
        conn.execute("""
            INSERT INTO disputes (obligation_id, submitted_by, side, counter_evidence, status)
            VALUES (2, 'Cloud Ops Director', 'obligated_party', 'Scheduled downtime excluded under Clause 5.3', 'open')
        """)

        # Add evidence item
        conn.execute("""
            INSERT INTO evidence (obligation_id, source, trust_level, trust_weight, description)
            VALUES (1, 'manual', 'independently_verifiable', 0.95, 'BSI ISO 27001 Certificate 2026')
        """)

        # Add a dependency
        conn.execute("""
            INSERT INTO dependencies (upstream_id, downstream_id, note)
            VALUES (1, 3, 'Audit certification required before incident review signing')
        """)
    conn.close()

    # Append an escalation entry to hash chain
    with app.app_context():
        add_escalation_entry(
            obligation_id=2,
            reason="Repeated monthly SLA breach under 99.80% uptime",
            risk_score=85.0
        )

    yield app

@pytest.fixture
def client(app):
    from app.models import create_user
    with app.app_context():
        create_user("Test User", "test@obligraph.io", "password123")
    c = app.test_client()
    c.post("/login", data={"email": "test@obligraph.io", "password": "password123"})
    return c

def test_dashboard_kpis_and_metrics(client):
    """Test dashboard route computes portfolio KPIs, risk counts, and chart data."""
    res = client.get("/dashboard")
    assert res.status_code == 200
    html = res.data.decode("utf-8")

    # Check key text elements
    assert "Obligation Intelligence Dashboard" in html
    assert "Compliance Rate" in html
    assert "Risk Distribution Profile" in html
    assert "Compliance Velocity" in html
    assert "30-Day Lookahead Calendar" in html

    # We inserted 4 obligations across 2 contracts: 2 COMPLIANT, 1 AT_RISK, 1 VIOLATED
    # Compliance rate = (2 / 4) * 100 = 50.0%
    assert "50.0%" in html
    assert "LOW RISK" in html
    assert "HIGH RISK" in html

def test_contract_scoping_and_switcher(client):
    """Test switching active contract filters the dashboard metrics dynamically."""
    # 1. Scope to Contract 1 (Cloud Services)
    res_switch = client.get("/switch-contract/1", follow_redirects=True)
    assert res_switch.status_code == 200

    # 3 obligations in Contract 1: 1 COMPLIANT, 1 AT_RISK, 1 VIOLATED -> 33.3%
    html_c1 = res_switch.data.decode("utf-8")
    assert "33.3%" in html_c1

    # 2. Scope to "all" contracts
    res_all = client.get("/switch-contract/all", follow_redirects=True)
    assert res_all.status_code == 200
    html_all = res_all.data.decode("utf-8")
    assert "50.0%" in html_all

def test_upcoming_deadlines_lookahead(client):
    """Test 30-day lookahead calendar aggregates upcoming deadlines with countdowns."""
    res = client.get("/dashboard")
    assert res.status_code == 200
    html = res.data.decode("utf-8")

    # Check overdue badge and countdown badge presence
    assert "Overdue by 3d" in html or "Overdue" in html
    assert "Due in 5d" in html or "Due" in html
    assert "Incident Post-Mortem within 48 Hours" in html

def test_global_search_api(client):
    """Test /api/search fuzzy search returns structured categorized results."""
    # Search for "Cloud"
    res = client.get("/api/search?q=Cloud")
    assert res.status_code == 200
    data = res.get_json()
    assert "results" in data
    assert len(data["results"]) >= 1

    # Search for "ISO"
    res_iso = client.get("/api/search?q=ISO")
    assert res_iso.status_code == 200
    data_iso = res_iso.get_json()
    assert any("27001" in item["title"] for item in data_iso["results"])

    # Short query under 2 chars returns empty list
    res_empty = client.get("/api/search?q=a")
    assert res_empty.status_code == 200
    assert res_empty.get_json()["results"] == []

def test_compliance_report_export(client):
    """Test executive compliance report generation with cryptographic proof."""
    res = client.get("/reports/export")
    assert res.status_code == 200
    html = res.data.decode("utf-8")

    assert "Compliance Audit Report" in html
    assert "OBL-AUDIT-" in html
    assert "Cryptographic Hash-Chain Verification Certificate" in html
    assert "SHA-256 LEDGER" in html
    assert "Chief Legal & Compliance Officer" in html
    assert "window.print()" in html

def test_keyboard_shortcuts_and_search_modals(client):
    """Test that base layout includes keyboard shortcuts and search modal markup."""
    res = client.get("/dashboard")
    assert res.status_code == 200
    html = res.data.decode("utf-8")

    assert 'id="globalSearchModal"' in html
    assert 'id="globalSearchInput"' in html
    assert 'id="shortcutsModal"' in html
    assert "Keyboard Shortcuts" in html

def test_error_handlers(client):
    """Test 404 error handler returns branded template."""
    res_404 = client.get("/non-existent-route-for-testing")
    assert res_404.status_code == 404
    assert "Obligation Not Found" in res_404.data.decode("utf-8")
