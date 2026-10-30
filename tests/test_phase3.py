import pytest
import sqlite3
from pathlib import Path
from config import TestConfig
from app import create_app
from app.database import get_db_connection
from engine.calculator import compute_obligation_metrics, calculate_deadline_proximity
from engine.connectors import parse_email_text, add_manual_evidence, seed_demo_evidence_set

@pytest.fixture
def app():
    test_db_path = Path("test_obligraph.db").resolve()
    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except Exception:
            pass

    app = create_app(TestConfig)
    
    # Pre-seed a contract with volume and purity obligations
    conn = get_db_connection(TestConfig.DATABASE_PATH)
    with conn:
        cur = conn.execute(
            "INSERT INTO contracts (title, filename, raw_text) VALUES (?, ?, ?)",
            ("Test Procurement Agreement", "test.docx", "Raw text agreement")
        )
        cid = cur.lastrowid
        
        conn.execute("""
            INSERT INTO obligations (
                contract_id, clause_text, actor, trigger_deadline, required_evidence,
                expected_state, violation_condition, remediation_action, target_value,
                unit, deadline_date, status, confidence, risk_score, risk_label
            ) VALUES 
            (?, 'Clause 1.1: Minimum Monthly Delivery Volumes 5000 MT', 'Supplier', 'Monthly by 5th', 'Bill of Lading', 'Deliver 5000 MT', 'Below 5000 MT', 'Credit 2% per day', 5000.0, 'MT', '2026-11-05', 'PENDING', 0.0, 0.0, 'LOW'),
            (?, 'Clause 2.1: Material Quality and Chemical Purity Standard 98.5%', 'Supplier', 'Concurrent with dispatch', 'COA Lab Assay', 'Purity >= 98.5%', 'Purity < 98.5%', 'Batch rejection within 72h', 98.5, '%', '2026-11-06', 'PENDING', 0.0, 0.0, 'LOW'),
            (?, 'Clause 3.1: 30-Day Advance Written Notice for Price Adjustments', 'Supplier', '30 days prior', 'Written Notice', 'Provide 30 days notice', 'No notice served', 'Price increase void', 30.0, 'days', '2026-11-15', 'PENDING', 0.0, 0.0, 'LOW')
        """, (cid, cid, cid))
    conn.close()

    yield app

    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except Exception:
            pass

@pytest.fixture
def client(app):
    from app.models import create_user
    with app.app_context():
        create_user("Test User", "test@obligraph.io", "password123")
    c = app.test_client()
    c.post("/login", data={"email": "test@obligraph.io", "password": "password123"})
    return c

def test_email_regex_parser():
    """Verify email regex extraction of quantity, purity, invoice, and BOL."""
    sample_email = """
    Shipment dispatched under Bill of Lading: BOL-99420.
    Delivered quantity: 4,850 metric tons battery-grade lithium carbonate.
    Pre-shipment assay confirms purity of 98.2% Li2CO3.
    Invoice # INV-2026-881 attached for reference.
    """
    extracted = parse_email_text(sample_email)
    assert extracted.get("quantity") == 4850.0
    assert extracted.get("purity") == 98.2
    assert extracted.get("bol") == "BOL-99420"
    assert extracted.get("invoice_number") == "INV-2026-881"

def test_deadline_proximity_calculation():
    """Verify deadline proximity decay curve."""
    # Past deadline -> 100
    past_score = calculate_deadline_proximity("2020-01-01")
    assert past_score == 100.0

    # Far future deadline -> low proximity
    far_score = calculate_deadline_proximity("2035-12-31")
    assert far_score <= 10.0

def test_evidence_addition_triggers_recomputation(app):
    """Verify that adding evidence changes confidence, risk score, and status."""
    with app.app_context():
        conn = get_db_connection(TestConfig.DATABASE_PATH)
        
        # 1. Initially obligation 1 is PENDING with confidence 0.0
        ob_before = conn.execute("SELECT * FROM obligations WHERE id = 1").fetchone()
        assert ob_before["status"] == "PENDING"
        assert ob_before["confidence"] == 0.0

        # 2. Add compliant evidence (5000 MT, independently verifiable)
        ev_id = add_manual_evidence(
            db=conn,
            obligation_id=1,
            observed_value=5000.0,
            description="Terminal Port Authority Weighbridge certified 5,000 MT",
            trust_level="independently_verifiable",
            source="sheet"
        )
        assert ev_id > 0

        # 3. Check updated obligation metrics
        ob_after = conn.execute("SELECT * FROM obligations WHERE id = 1").fetchone()
        assert ob_after["confidence"] == 100.0
        assert ob_after["status"] in ["COMPLIANT", "AT_RISK"]
        assert ob_after["risk_score"] < 40.0
        conn.close()

def test_violated_evidence_triggers_high_risk_and_dispute_window(app):
    """Verify that severe shortfall or chemical breach triggers VIOLATED and dispute window."""
    with app.app_context():
        conn = get_db_connection(TestConfig.DATABASE_PATH)
        
        # Add chemical purity failure (96.5% vs 98.5% required)
        add_manual_evidence(
            db=conn,
            obligation_id=2,
            observed_value=96.5,
            description="Lab test failed: measured 96.5% purity with unpermitted iron contaminants",
            trust_level="independently_verifiable",
            source="sheet"
        )

        ob = conn.execute("SELECT * FROM obligations WHERE id = 2").fetchone()
        assert ob["status"] == "VIOLATED"
        assert ob["confidence"] < 60.0
        assert ob["risk_score"] >= 40.0
        conn.close()

def test_demo_evidence_seed_flow(client):
    """Test one-click demo evidence seeding endpoint."""
    res = client.post("/evidence/demo", follow_redirects=True)
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Successfully loaded" in html

    conn = sqlite3.connect(TestConfig.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    evidence_rows = conn.execute("SELECT * FROM evidence").fetchall()
    conn.close()

    assert len(evidence_rows) >= 3
    sources = {r["source"] for r in evidence_rows}
    assert "email" in sources or "sheet" in sources or "manual" in sources

def test_evidence_connectors_sync_endpoints(client):
    """Test sync endpoints for email, sheet, and all."""
    # Sync email
    res_email = client.post("/evidence/sync/email", follow_redirects=True)
    assert res_email.status_code == 200

    # Sync sheet
    res_sheet = client.post("/evidence/sync/sheet", follow_redirects=True)
    assert res_sheet.status_code == 200

    # Sync all
    res_all = client.post("/evidence/sync/all", follow_redirects=True)
    assert res_all.status_code == 200
    html_all = res_all.get_data(as_text=True)
    assert "All connectors synchronized successfully" in html_all
