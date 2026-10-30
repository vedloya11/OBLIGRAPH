import pytest
import sqlite3
from pathlib import Path
from config import TestConfig
from app import create_app
from app.database import get_db_connection

@pytest.fixture
def app():
    test_db_path = Path("test_obligraph.db").resolve()
    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except Exception:
            pass

    app = create_app(TestConfig)
    
    # Pre-seed a test contract and obligations
    conn = get_db_connection(TestConfig.DATABASE_PATH)
    with conn:
        cur = conn.execute(
            "INSERT INTO contracts (title, filename, raw_text) VALUES (?, ?, ?)",
            ("Test Materials Agreement", "test.pdf", "Sample contract text")
        )
        cid = cur.lastrowid
        
        # Insert test obligations
        conn.execute("""
            INSERT INTO obligations (
                contract_id, clause_text, actor, trigger_deadline, required_evidence,
                expected_state, violation_condition, remediation_action, target_value,
                unit, deadline_date, status, confidence, risk_score, risk_label
            ) VALUES 
            (?, 'Clause 1.1: Minimum Monthly Delivery 5000 MT', 'Supplier', 'Monthly by 5th', 'Bill of Lading', 'Deliver 5000 MT', 'Delivery below 5000 MT', 'Expedite freight', 5000, 'MT', '2026-11-05', 'COMPLIANT', 90.0, 15.0, 'LOW'),
            (?, 'Clause 2.1: Material Quality and Chemical Purity Standard 98.5%', 'Supplier', 'Concurrent with dispatch', 'COA Certificate', 'Purity >= 98.5%', 'Purity < 98.5%', 'Batch rejection', 98.5, '%', '2026-11-06', 'AT_RISK', 60.0, 55.0, 'MEDIUM'),
            (?, 'Clause 2.2: Certificate of Chemical Analysis (COA) Submission', 'Supplier', 'Prior to port arrival', 'Certified COA', 'Furnish valid COA', 'Missing COA', 'Hold customs', NULL, NULL, '2026-11-06', 'PENDING', 0.0, 40.0, 'MEDIUM'),
            (?, 'Clause 3.1: 30-Day Advance Written Notice for Price Adjustments', 'Supplier', '30 days prior', 'Written Notice', 'Provide 30 days notice', 'Price increase without notice', 'Price voided', 30, 'days', '2026-11-15', 'PENDING', 0.0, 20.0, 'LOW'),
            (?, 'Clause 3.2: Quarterly Price Index Adjustment Cap', 'Supplier', 'Quarterly', 'Index calculation sheet', 'Cap at 5%', 'Increase > 5%', 'Executive review', 5.0, '%', '2026-11-15', 'PENDING', 0.0, 25.0, 'LOW'),
            (?, 'Clause 5.1: Payment Settlement and Invoicing Terms', 'Buyer', 'Net 30 days', 'Bank EFT confirmation', 'Pay within 30 days', 'Late payment', 'SOFR + 3.5% interest', 30, 'days', '2026-11-30', 'PENDING', 0.0, 10.0, 'LOW')
        """, (cid, cid, cid, cid, cid, cid))
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

def test_obligations_list_renders_and_filters(client):
    """Test obligations index page with search query and filter parameters."""
    res = client.get("/obligations/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Obligations Registry & Status Matrix" in html
    assert "Clause 1.1" in html
    assert "Supplier" in html

    # Filter by Actor
    res_actor = client.get("/obligations/?actor=Supplier")
    assert res_actor.status_code == 200
    html_actor = res_actor.get_data(as_text=True)
    assert "Supplier" in html_actor

    # Filter by Status
    res_status = client.get("/obligations/?status=COMPLIANT")
    assert res_status.status_code == 200
    html_status = res_status.get_data(as_text=True)
    assert "COMPLIANT" in html_status

    # Filter by Risk
    res_risk = client.get("/obligations/?risk=MEDIUM")
    assert res_risk.status_code == 200
    html_risk = res_risk.get_data(as_text=True)
    assert "MEDIUM" in html_risk

def test_obligation_detail_page_and_confidence_gauge(client):
    """Test detail view renders the 6 canonical fields and circular confidence gauge."""
    res = client.get("/obligations/1")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Six-Field Canonical Legal Record" in html
    assert "1. Obligated Actor" in html
    assert "2. Trigger / Deadline" in html
    assert "3. Required Evidence" in html
    assert "4. Expected State / Target" in html
    assert "5. Violation Condition" in html
    assert "6. Remediation Protocol" in html
    assert "Compliance Confidence" in html
    assert "Risk Score Composition" in html

def test_default_dependencies_seeded_and_appear_on_detail_page(client):
    """Verify that 2-3 dependency links are seeded and visible on detail page."""
    # Trigger seeding via graph or obligations view
    client.get("/obligations/")

    conn = sqlite3.connect(TestConfig.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    deps = conn.execute("SELECT * FROM dependencies").fetchall()
    conn.close()

    assert len(deps) >= 2, f"Expected at least 2 seeded dependencies, got {len(deps)}"

    # Inspect one of the linked obligations (e.g. price clause or coa clause)
    # The detail page of downstream obligation must show the upstream link
    for d in deps:
        downstream_id = d["downstream_id"]
        res = client.get(f"/obligations/{downstream_id}")
        html = res.get_data(as_text=True)
        assert "UPSTREAM PREREQUISITES" in html
        assert str(d["upstream_id"]) in html

def test_graph_api_and_colors(client):
    """Verify that /obligations/api/graph-data returns nodes and edges colored by risk."""
    res = client.get("/obligations/api/graph-data")
    assert res.status_code == 200
    data = res.get_json()

    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) >= 5
    assert len(data["edges"]) >= 2

    # Check node structure
    for node in data["nodes"]:
        assert "id" in node
        assert "label" in node
        assert "color" in node
        assert "background" in node["color"]

    # Check edge structure
    for edge in data["edges"]:
        assert "from" in edge
        assert "to" in edge
        assert "label" in edge
        assert edge["arrows"] == "to"

def test_add_and_delete_dependency_link(client):
    """Test manual addition and removal of dependency links."""
    # Add dependency link between obligation 1 and 2
    post_data = {
        "upstream_id": "1",
        "downstream_id": "2",
        "note": "Custom Manual Constraint Test"
    }
    add_res = client.post("/obligations/dependencies/add", data=post_data, follow_redirects=True)
    assert add_res.status_code == 200

    conn = sqlite3.connect(TestConfig.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    dep = conn.execute("SELECT * FROM dependencies WHERE upstream_id = 1 AND downstream_id = 2").fetchone()
    conn.close()

    assert dep is not None
    assert dep["note"] == "Custom Manual Constraint Test"

    # Now delete the dependency
    del_res = client.post(f"/obligations/dependencies/delete/{dep['id']}", follow_redirects=True)
    assert del_res.status_code == 200

    conn2 = sqlite3.connect(TestConfig.DATABASE_PATH)
    dep_after = conn2.execute("SELECT * FROM dependencies WHERE id = ?", (dep["id"],)).fetchone()
    conn2.close()

    assert dep_after is None
