import pytest
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from config import TestConfig
from app import create_app
from app.database import get_db_connection
from engine.calculator import explain_score, compute_obligation_metrics, recompute_all_obligations, get_risk_thresholds
from engine.scheduler import check_deadlines_and_dispute_windows

@pytest.fixture
def app():
    test_db_path = Path("test_obligraph.db").resolve()
    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except Exception:
            pass

    app = create_app(TestConfig)
    
    # Pre-seed a contract with volume, purity, and notice obligations
    conn = get_db_connection(TestConfig.DATABASE_PATH)
    with conn:
        cur = conn.execute(
            "INSERT INTO contracts (title, filename, raw_text) VALUES (?, ?, ?)",
            ("Master Supply Agreement 2026", "supply_contract.docx", "Contract text")
        )
        cid = cur.lastrowid
        
        # Obligation 1: Volume delivery (5000 MT target)
        cur1 = conn.execute("""
            INSERT INTO obligations (
                contract_id, clause_text, actor, trigger_deadline, required_evidence,
                expected_state, violation_condition, remediation_action, target_value,
                unit, deadline_date, status, confidence, risk_score, risk_label
            ) VALUES (?, 'Clause 1.1: Minimum Monthly Delivery 5000 MT', 'Supplier', 'Monthly by 5th', 'Bill of Lading', 'Deliver 5000 MT', 'Below 5000 MT', 'Credit 2% per day', 5000.0, 'MT', '2026-11-05', 'PENDING', 0.0, 0.0, 'LOW')
        """, (cid,))
        ob1_id = cur1.lastrowid

        # Obligation 2: Chemical purity (98.5% target)
        cur2 = conn.execute("""
            INSERT INTO obligations (
                contract_id, clause_text, actor, trigger_deadline, required_evidence,
                expected_state, violation_condition, remediation_action, target_value,
                unit, deadline_date, status, confidence, risk_score, risk_label
            ) VALUES (?, 'Clause 2.1: Battery-Grade Li2CO3 Purity >= 98.5%', 'Supplier', 'Concurrent with dispatch', 'COA Lab Assay', 'Purity >= 98.5%', 'Purity < 98.5%', 'Batch rejection within 72h', 98.5, '%', '2026-11-06', 'PENDING', 0.0, 0.0, 'LOW')
        """, (cid,))
        ob2_id = cur2.lastrowid

        # Insert flawed evidence for Obligation 2 to push it to HIGH risk and open dispute window
        past_dispute_dl = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
        conn.execute("""
            INSERT INTO evidence (
                obligation_id, source, trust_level, trust_weight,
                observed_value, description, raw_ref, created_at
            ) VALUES (?, 'email', 'independently_verifiable', 1.0, 96.2, 'Independent Lab Assay: Purity breached at 96.2%', 'COA-991', '2026-10-22 10:00:00')
        """, (ob2_id,))

        conn.execute("""
            UPDATE obligations 
            SET status = 'VIOLATED', risk_score = 85.0, risk_label = 'HIGH', dispute_deadline = ?
            WHERE id = ?
        """, (past_dispute_dl, ob2_id))

    conn.close()

    yield app

    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except Exception:
            pass

@pytest.fixture
def client(app):
    return app.test_client()

def test_explain_score_breakdown(app):
    """Verify deterministic explain_score returns all three formula components and weights."""
    with app.app_context():
        explanation = explain_score(2)
        assert explanation["obligation_id"] == 2
        assert "breakdown" in explanation
        bd = explanation["breakdown"]
        
        # 3 components: w1 (deviation), w2 (trust deficit), w3 (deadline proximity)
        assert "w1_contribution" in bd
        assert "w2_contribution" in bd
        assert "w3_contribution" in bd
        assert "formula_string" in bd
        assert bd["w1_weight_pct"] == 45
        assert bd["w2_weight_pct"] == 25
        assert bd["w3_weight_pct"] == 30
        assert explanation["risk_label"] == "HIGH"
        assert explanation["dispute_hours_remaining"] is not None
        assert explanation["dispute_hours_remaining"] > 40.0

def test_explain_score_api_endpoint(client):
    """Verify /obligations/api/explain/<id> returns JSON breakdown."""
    resp = client.get("/obligations/api/explain/2")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["obligation_id"] == 2
    assert "breakdown" in data
    assert "total_risk_score" in data
    assert data["breakdown"]["w1_deviation_score"] > 0

    # Test non-existent obligation returns 404
    resp_404 = client.get("/obligations/api/explain/9999")
    assert resp_404.status_code == 404

def test_disputes_index_and_flagged_list(client):
    """Verify disputes list shows flagged obligations eligible for counter-evidence."""
    resp = client.get("/disputes/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Dispute Resolution Center" in html
    assert "Battery-Grade Li2CO3 Purity" in html
    assert "Lodge Dispute" in html

def test_dispute_create_page(client):
    """Verify dispute initiation form displays target values, hours left, and input fields."""
    resp = client.get("/disputes/create/2")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Lodge Counter-Evidence &amp; Dispute" in html
    assert "98.5 %" in html
    assert "Dispute Window Active" in html
    assert "COA-991" in html

def test_dispute_submission_flow(client, app):
    """Verify counter-evidence submission creates a dispute record and updates obligation metrics."""
    resp = client.post("/disputes/submit", data={
        "obligation_id": "2",
        "submitted_by": "Apex Quality Assurance Team",
        "side": "obligated_party",
        "counter_value": "98.8",
        "counter_evidence": "Re-calibration testing at certified SGS facility shows true purity is 98.8%, initial sample had moisture contamination.",
        "attachment": "SGS-CERT-8842.pdf"
    }, follow_redirects=True)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Counter-evidence" in html
    assert "SGS-CERT-8842" in html
    assert "Apex Quality Assurance Team" in html

    # Verify database state
    with app.app_context():
        conn = get_db_connection(TestConfig.DATABASE_PATH)
        dispute = conn.execute("SELECT * FROM disputes WHERE obligation_id = 2").fetchone()
        assert dispute is not None
        assert dispute["status"] in ["open", "resolved"]
        assert dispute["attachment"] == "SGS-CERT-8842.pdf"

        # Evidence should have been added
        ev = conn.execute("SELECT * FROM evidence WHERE obligation_id = 2 AND raw_ref = 'SGS-CERT-8842.pdf'").fetchone()
        assert ev is not None
        assert ev["observed_value"] == 98.8
        assert ev["trust_level"] == "counterparty_confirmed"
        conn.close()

def test_dispute_mutual_resolution(client, app):
    """Verify manual resolve action updates dispute to resolved and clears dispute window."""
    # First create a dispute
    client.post("/disputes/submit", data={
        "obligation_id": "1",
        "submitted_by": "Operations Lead",
        "side": "obligated_party",
        "counter_value": "5000",
        "counter_evidence": "Bill of Lading signed by carrier.",
        "attachment": "BOL-5000.pdf"
    })

    with app.app_context():
        conn = get_db_connection(TestConfig.DATABASE_PATH)
        d = conn.execute("SELECT id FROM disputes WHERE obligation_id = 1").fetchone()
        dispute_id = d["id"]
        conn.close()

    # Call resolve endpoint
    resp = client.post(f"/disputes/resolve/{dispute_id}", follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "resolved by mutual consent" in html

    with app.app_context():
        conn = get_db_connection(TestConfig.DATABASE_PATH)
        updated_d = conn.execute("SELECT status FROM disputes WHERE id = ?", (dispute_id,)).fetchone()
        assert updated_d["status"] == "resolved"
        conn.close()

def test_scheduler_dispute_window_expiry(app):
    """Verify background scheduler detects expired dispute windows and finalizes status."""
    with app.app_context():
        conn = get_db_connection(TestConfig.DATABASE_PATH)
        
        # Set dispute deadline in the past
        past_dl = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        conn.execute("""
            UPDATE obligations 
            SET dispute_deadline = ?, risk_score = 85.0, status = 'VIOLATED' 
            WHERE id = 2
        """, (past_dl,))
        
        # Insert an open dispute
        conn.execute("""
            INSERT INTO disputes (obligation_id, submitted_by, side, counter_evidence, status, created_at)
            VALUES (2, 'Supplier rep', 'obligated_party', 'Pending review', 'open', '2026-10-20 00:00:00')
        """)
        conn.commit()
        conn.close()

        # Run scheduler check
        check_deadlines_and_dispute_windows(TestConfig.DATABASE_PATH)

        conn = get_db_connection(TestConfig.DATABASE_PATH)
        ob = conn.execute("SELECT dispute_deadline, status FROM obligations WHERE id = 2").fetchone()
        dispute = conn.execute("SELECT status FROM disputes WHERE obligation_id = 2").fetchone()
        
        # Dispute window should be expired and dispute marked expired
        assert ob["dispute_deadline"] == "EXPIRED"
        assert dispute["status"] == "expired"
        conn.close()

def test_settings_update_triggers_recomputation(client, app):
    """Verify updating settings recomputes obligations across database."""
    resp = client.post("/settings/", data={
        "risk_threshold_high": "60.0",
        "risk_threshold_medium": "30.0",
        "dispute_window_hours": "72",
        "weight_self_reported": "0.35",
        "weight_counterparty_confirmed": "0.75",
        "weight_independently_verifiable": "1.0",
        "risk_weight_deviation": "0.50",
        "risk_weight_untrusted": "0.20",
        "risk_weight_deadline": "0.30",
        "google_sheet_csv_url": "https://example.com/sheet.csv"
    }, follow_redirects=True)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Engine parameters updated successfully and obligations recomputed" in html
