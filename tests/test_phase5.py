import pytest
import sqlite3
from pathlib import Path
from config import TestConfig
from app import create_app
from app.database import get_db_connection
from engine.hash_chain import (
    compute_block_hash,
    add_escalation_entry,
    verify_chain,
    simulate_tampering,
    repair_chain,
    GENESIS_PREV_HASH
)
from engine.nl_query import (
    validate_and_sanitize_sql,
    translate_nl_to_sql,
    execute_sandboxed_query
)

@pytest.fixture
def app():
    test_db_path = Path("test_obligraph.db").resolve()
    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except Exception:
            pass

    app = create_app(TestConfig)
    
    # Pre-seed contract and obligations
    conn = get_db_connection(TestConfig.DATABASE_PATH)
    with conn:
        cur = conn.execute(
            "INSERT INTO contracts (title, filename, raw_text) VALUES (?, ?, ?)",
            ("Lithium Supply Contract 2026", "supply_agreement.docx", "Contract text")
        )
        cid = cur.lastrowid
        
        conn.execute("""
            INSERT INTO obligations (
                contract_id, clause_text, actor, trigger_deadline, required_evidence,
                expected_state, violation_condition, remediation_action, target_value,
                unit, deadline_date, status, confidence, risk_score, risk_label
            ) VALUES 
            (?, 'Clause 1.1: Minimum Monthly Lithium Volume 5000 MT', 'Supplier', 'Monthly by 5th', 'Bill of Lading', 'Deliver 5000 MT', 'Below 5000 MT', 'Credit 2% per day', 5000.0, 'MT', '2026-11-05', 'PENDING', 0.0, 10.0, 'LOW'),
            (?, 'Clause 2.1: Lithium Carbonate Purity >= 98.5%', 'Supplier', 'Concurrent with dispatch', 'COA Lab Assay', 'Purity >= 98.5%', 'Purity < 98.5%', 'Reject batch', 98.5, '%', '2026-11-06', 'VIOLATED', 50.0, 88.0, 'HIGH'),
            (?, 'Clause 3.1: 30-Day Written Notice for Price Adjustments', 'Supplier', '30 days prior', 'Written Notice', 'Provide 30 days notice', 'No notice served', 'Increase void', 30.0, 'days', '2026-11-20', 'COMPLIANT', 95.0, 5.0, 'LOW')
        """, (cid, cid, cid))

        # Add a dispute
        conn.execute("""
            INSERT INTO disputes (obligation_id, submitted_by, side, counter_evidence, status)
            VALUES (2, 'Supplier QA', 'obligated_party', 'Recalibrated sample assay', 'resolved')
        """)
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

# ================= PART 1: HASH CHAIN TESTS =================

def test_compute_block_hash_deterministic():
    """Verify compute_block_hash generates valid 64-character hex SHA-256 string deterministically."""
    h1 = compute_block_hash(1, "2026-10-22 12:00:00", 2, "Purity Breach 96.2%", GENESIS_PREV_HASH)
    h2 = compute_block_hash(1, "2026-10-22 12:00:00", 2, "Purity Breach 96.2%", GENESIS_PREV_HASH)
    assert len(h1) == 64
    assert h1 == h2

    # Any single character modification must completely alter the hash (avalanche effect)
    h_tampered = compute_block_hash(1, "2026-10-22 12:00:00", 2, "Purity Breach 96.3%", GENESIS_PREV_HASH)
    assert h1 != h_tampered

def test_hash_chain_genesis_and_sequential_linking(app):
    """Verify genesis block has '0'*64 and block 2 links to block 1's entry_hash."""
    with app.app_context():
        conn = get_db_connection(TestConfig.DATABASE_PATH)
        
        # Block 1 (Genesis)
        b1 = add_escalation_entry(2, "Unmitigated Purity Breach", risk_score=88.0, db=conn)
        assert b1["id"] == 1
        assert b1["prev_hash"] == GENESIS_PREV_HASH
        assert len(b1["entry_hash"]) == 64
        
        # Block 2
        b2 = add_escalation_entry(2, "Dispute window lapsed without counter-evidence", risk_score=92.0, db=conn)
        assert b2["id"] == 2
        assert b2["prev_hash"] == b1["entry_hash"]

        # Block 3
        b3 = add_escalation_entry(1, "Late delivery notice", risk_score=75.0, db=conn)
        assert b3["id"] == 3
        assert b3["prev_hash"] == b2["entry_hash"]

        # Verification passes
        audit = verify_chain(db=conn)
        assert audit["is_valid"] is True
        assert audit["total_blocks"] == 3
        assert audit["last_hash"] == b3["entry_hash"]
        conn.close()

def test_hash_chain_tamper_detection_and_repair(app):
    """Verify tampering a block flags cryptographic mismatch and repair restores validity."""
    with app.app_context():
        conn = get_db_connection(TestConfig.DATABASE_PATH)
        
        add_escalation_entry(2, "Initial breach block 1", risk_score=80.0, db=conn)
        add_escalation_entry(2, "Follow-up breach block 2", risk_score=85.0, db=conn)
        add_escalation_entry(1, "Volume breach block 3", risk_score=90.0, db=conn)

        # Confirm initial validity
        initial_check = verify_chain(db=conn)
        assert initial_check["is_valid"] is True

        # Simulate tampering on block 2
        tamper_res = simulate_tampering(block_id=2, db=conn)
        assert tamper_res["success"] is True

        # Verification must now FAIL
        tampered_check = verify_chain(db=conn)
        assert tampered_check["is_valid"] is False
        assert tampered_check["corrupted_block_id"] == 2
        assert "tamper detected" in tampered_check["reason"].lower()

        # Repair chain
        repair_res = repair_chain(db=conn)
        assert repair_res["repaired"] is True
        assert repair_res["blocks_repaired"] == 3

        # Verification must now SUCCEED again
        restored_check = verify_chain(db=conn)
        assert restored_check["is_valid"] is True
        conn.close()

def test_escalation_routes_and_ui(client, app):
    """Verify /escalations/ renders visual blockchain, verify, tamper, and repair endpoints."""
    # Seed 1 block
    with app.app_context():
        conn = get_db_connection(TestConfig.DATABASE_PATH)
        add_escalation_entry(2, "Chemical Purity Breach Escalation", risk_score=88.0, db=conn)
        conn.close()

    # Index page
    resp = client.get("/escalations/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Cryptographic Escalation Ledger" in html
    assert "BLOCK #1 [GENESIS]" in html
    assert "Chemical Purity Breach Escalation" in html
    assert "CHAIN VALID" in html

    # Verify endpoint
    resp_v = client.post("/escalations/verify", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp_v.status_code == 200
    assert resp_v.get_json()["is_valid"] is True

    # Tamper endpoint
    resp_t = client.post("/escalations/tamper", data={"block_id": "1"}, follow_redirects=True)
    assert resp_t.status_code == 200
    html_t = resp_t.get_data(as_text=True)
    assert "INTEGRITY COMPROMISED" in html_t or "Tamper Detected" in html_t

    # Repair endpoint
    resp_r = client.post("/escalations/repair", follow_redirects=True)
    assert resp_r.status_code == 200
    html_r = resp_r.get_data(as_text=True)
    assert "CHAIN VALID" in html_r or "verified intact" in html_r.lower()

# ================= PART 2: NATURAL LANGUAGE TO SQL TESTS =================

def test_sql_sandbox_rejection_rules():
    """Verify regex sandbox permits ONLY SELECT and strictly rejects injection / write operations."""
    # Permitted queries
    assert validate_and_sanitize_sql("SELECT * FROM obligations")["valid"] is True
    assert validate_and_sanitize_sql("WITH high_risk AS (SELECT * FROM obligations WHERE risk_score > 70) SELECT * FROM high_risk")["valid"] is True

    # Forbidden DDL / DML operations
    assert validate_and_sanitize_sql("INSERT INTO obligations (actor) VALUES ('Malicious')")["valid"] is False
    assert validate_and_sanitize_sql("UPDATE obligations SET risk_score = 0")["valid"] is False
    assert validate_and_sanitize_sql("DELETE FROM obligations WHERE id = 1")["valid"] is False
    assert validate_and_sanitize_sql("DROP TABLE obligations")["valid"] is False
    assert validate_and_sanitize_sql("ALTER TABLE obligations ADD COLUMN hacked TEXT")["valid"] is False
    assert validate_and_sanitize_sql("PRAGMA table_info(obligations)")["valid"] is False
    assert validate_and_sanitize_sql("ATTACH DATABASE 'malicious.db' AS mal")["valid"] is False

    # Multi-statement injection forbidden
    assert validate_and_sanitize_sql("SELECT * FROM obligations; DROP TABLE contracts;")["valid"] is False

def test_nl_to_sql_translation_rules():
    """Verify plain English questions map to accurate, valid SELECT queries."""
    sql_due = translate_nl_to_sql("What obligations are due this month?")
    assert sql_due.upper().startswith("SELECT")
    assert "deadline_date" in sql_due.lower()

    sql_risk = translate_nl_to_sql("Show high risk obligations for supplier")
    assert sql_risk.upper().startswith("SELECT")
    assert "risk" in sql_risk.lower()

    sql_bol = translate_nl_to_sql("Which deliveries have missing BOL evidence?")
    assert sql_bol.upper().startswith("SELECT")
    assert "evidence" in sql_bol.lower()

    sql_disputes = translate_nl_to_sql("List all resolved disputes")
    assert sql_disputes.upper().startswith("SELECT")
    assert "disputes" in sql_disputes.lower()

def test_sandboxed_query_execution(app):
    """Verify execute_sandboxed_query returns columns, rows, timing, and read-only safety."""
    db_path = str(TestConfig.DATABASE_PATH)
    
    # Valid SELECT
    res = execute_sandboxed_query("SELECT id, actor, status, risk_score FROM obligations ORDER BY id ASC", db_path)
    assert res["error"] is None
    assert "id" in res["columns"]
    assert "actor" in res["columns"]
    assert len(res["rows"]) == 3
    assert res["execution_time_ms"] >= 0.0

    # Malicious injection rejected
    bad_res = execute_sandboxed_query("DROP TABLE obligations", db_path)
    assert bad_res["error"] is not None
    assert "rejected" in bad_res["error"].lower() or "violation" in bad_res["error"].lower()

def test_query_studio_api_and_ui(client):
    """Verify /query/ and /query/api endpoints process prompts and return clean tabular results."""
    # API endpoint test
    resp = client.post("/query/api", json={"prompt": "Show all high risk obligations"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["error"] is None
    assert data["sql"].upper().startswith("SELECT")
    assert len(data["rows"]) >= 1

    # Security test via API
    resp_bad = client.post("/query/api", json={"sql": "DELETE FROM contracts"})
    assert resp_bad.status_code == 200
    data_bad = resp_bad.get_json()
    assert data_bad["error"] is not None
    assert "forbidden" in data_bad["error"].lower() or "rejected" in data_bad["error"].lower()

    # Form UI test
    resp_ui = client.post("/query/", data={"prompt": "What obligations are due this month?"}, follow_redirects=True)
    assert resp_ui.status_code == 200
    html = resp_ui.get_data(as_text=True)
    assert "Query Results" in html
    assert "Generated SQL Query" in html
    assert "mode=ro" in html
