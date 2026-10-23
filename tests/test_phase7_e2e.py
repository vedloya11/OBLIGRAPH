import pytest
import sqlite3
import datetime
from pathlib import Path
from config import TestConfig
from app import create_app
from app.database import get_db_connection
from seed.load_demo import load_demo_data
from engine.hash_chain import verify_chain, simulate_tampering, repair_chain
from engine.nl_query import translate_nl_to_sql, execute_sandboxed_query
from engine.calculator import compute_obligation_metrics, explain_score

@pytest.fixture
def app():
    test_db_path = Path("test_obligraph.db").resolve()
    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except Exception:
            pass

    app = create_app(TestConfig)
    yield app

@pytest.fixture
def client(app):
    return app.test_client()

def test_load_demo_data_function():
    """Test seed/load_demo.py resets tables and seeds the canonical scenario."""
    test_db = "test_seed_load.db"
    p = Path(test_db).resolve()
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass

    summary = load_demo_data(db_path=test_db)
    assert summary["status"] == "success"
    assert summary["contract_title"] == "Global Lithium & Chemical Materials Master Supply Agreement"
    assert summary["obligations_count"] == 10
    assert summary["dependencies_count"] == 4
    assert summary["evidence_count"] == 6
    assert summary["open_disputes"] == 1
    assert summary["escalation_blocks"] >= 1
    assert summary["chain_valid"] is True

    # Verify directly in SQLite
    conn = get_db_connection(test_db)
    obs = conn.execute("SELECT COUNT(*) FROM obligations").fetchone()[0]
    assert obs == 10

    # Verify deliberate violation exists (Chemical Purity standard)
    violated = conn.execute("SELECT * FROM obligations WHERE status = 'VIOLATED'").fetchall()
    assert len(violated) >= 1

    # Verify open dispute
    disp = conn.execute("SELECT * FROM disputes WHERE status = 'open'").fetchall()
    assert len(disp) == 1
    assert disp[0]["obligation_id"] == 3

    # Clean up test file
    conn.close()
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass

def test_settings_reset_demo_endpoint(client):
    """Test one-click demo reset endpoint in Settings."""
    res = client.post("/settings/reset-demo", follow_redirects=True)
    assert res.status_code == 200
    html = res.data.decode("utf-8")
    assert "Demo environment reset successfully!" in html
    assert "Obligation Intelligence Dashboard" in html
    assert "Global Lithium" in html

def test_complete_end_to_end_pipeline(client):
    """
    Test the full 6-stage lifecycle flow end-to-end:
    1. Contract ingestion & 6-field obligations
    2. Topological dependency relationships
    3. Multi-channel evidence ingestion & trust weights
    4. Deterministic predictive compliance scoring & 4-factor explainability
    5. Counter-evidence dispute lodging & 48h resolution window
    6. SHA-256 cryptographic escalation ledger & audit verification
    7. Sandboxed Natural Language to SQL execution
    8. Executive compliance audit certificate export
    """
    # 1. Reset and load demo baseline
    res_seed = client.post("/settings/reset-demo", follow_redirects=True)
    assert res_seed.status_code == 200

    # 2. Verify obligations list and detail view
    res_obs = client.get("/obligations/")
    assert res_obs.status_code == 200
    assert "Clause 2.1: Material Quality" in res_obs.data.decode("utf-8")

    # Detail view for Obligation #3 (the violated clause)
    res_detail = client.get("/obligations/3")
    assert res_detail.status_code == 200
    assert "VIOLATED" in res_detail.data.decode("utf-8")

    # 3. Test 4-Factor Explain Score API
    res_explain = client.get("/obligations/api/explain/3")
    assert res_explain.status_code == 200
    explain_data = res_explain.get_json()
    assert "breakdown" in explain_data
    b = explain_data["breakdown"]
    assert "w1_contribution" in b
    assert "w2_contribution" in b
    assert "w3_contribution" in b
    assert "formula_string" in b

    # 4. Verify Interactive Dependency Graph API
    res_graph = client.get("/obligations/api/graph-data")
    assert res_graph.status_code == 200
    graph_data = res_graph.get_json()
    assert len(graph_data["nodes"]) == 10
    assert len(graph_data["edges"]) == 4

    # 5. Verify Multi-Channel Evidence Feeds
    res_ev = client.get("/evidence/")
    assert res_ev.status_code == 200
    html_ev = res_ev.data.decode("utf-8")
    assert "BOL-88492" in html_ev
    assert "COA-992" in html_ev

    # Add fresh manual evidence item
    res_add_ev = client.post("/evidence/add", data={
        "obligation_id": 4,
        "source": "manual",
        "trust_level": "independently_verifiable",
        "description": "Port Customs Clearance Inspection Stamp verified lot purity",
        "observed_value": 1.0,
        "raw_ref": "CUSTOMS-STAMP-BERTH-3"
    }, follow_redirects=True)
    assert res_add_ev.status_code == 200

    # 6. Verify Active Disputes and Resolution
    res_disp = client.get("/disputes/")
    assert res_disp.status_code == 200
    assert "SGS Minerals International" in res_disp.data.decode("utf-8")

    # Lodge a new dispute on Obligation #1
    res_new_disp = client.post("/disputes/submit", data={
        "obligation_id": 1,
        "submitted_by": "Apex Freight Logistics Director",
        "side": "obligated_party",
        "counter_evidence": "Port Berth 3 crane breakdown caused temporary delay; supplemental barge dispatched with 200 MT."
    }, follow_redirects=True)
    assert res_new_disp.status_code == 200

    # Resolve dispute #1
    res_resolve = client.post("/disputes/resolve/1", follow_redirects=True)
    assert res_resolve.status_code == 200

    # 7. Verify SHA-256 Cryptographic Hash Chain Ledger
    res_esc = client.get("/escalations/")
    assert res_esc.status_code == 200
    html_esc = res_esc.data.decode("utf-8")
    assert "BLOCK #1" in html_esc
    assert "Genesis Anchor" in html_esc
    assert "CHAIN VALID" in html_esc

    # Verify tamper simulation and repair flow
    res_tamper = client.post("/escalations/tamper", follow_redirects=True)
    assert res_tamper.status_code == 200
    assert "INTEGRITY COMPROMISED" in res_tamper.data.decode("utf-8") or "Corrupted" in res_tamper.data.decode("utf-8")

    res_repair = client.post("/escalations/repair", follow_redirects=True)
    assert res_repair.status_code == 200
    assert "CHAIN VALID" in res_repair.data.decode("utf-8")

    # 8. Verify Natural Language Query Studio API
    res_nl = client.post("/query/api/execute", json={
        "query": "Which obligations are at risk?"
    })
    assert res_nl.status_code == 200
    nl_data = res_nl.get_json()
    assert nl_data["success"] is True
    assert "SELECT" in nl_data["sql"]
    assert "rows" in nl_data

    # 9. Verify Executive Compliance Audit Report Export
    res_report = client.get("/reports/export")
    assert res_report.status_code == 200
    html_report = res_report.data.decode("utf-8")
    assert "OBL-AUDIT-" in html_report
    assert "Cryptographic Hash-Chain Verification Certificate" in html_report
    assert "Chief Legal & Compliance Officer" in html_report
    assert "window.print()" in html_report
