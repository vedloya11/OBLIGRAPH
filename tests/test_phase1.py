import pytest
import os
import sqlite3
from pathlib import Path
from config import TestConfig
from app import create_app
from engine.parsers.doc_parser import extract_text, split_into_clauses
from engine.llm_client import extract_clause_with_llm, validate_clause_record, SIX_FIELDS

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

    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except Exception:
            pass

@pytest.fixture
def client(app):
    return app.test_client()

def test_seed_files_exist():
    """Verify that both seed demo contract files exist."""
    docx_path = Path("seed/demo_contract.docx")
    pdf_path = Path("seed/demo_contract.pdf")
    assert docx_path.exists(), "seed/demo_contract.docx does not exist."
    assert pdf_path.exists(), "seed/demo_contract.pdf does not exist."

def test_pdf_extraction_and_clause_count():
    """Verify that PDF text extraction and clause splitting produce 8-12 obligations."""
    pdf_text = extract_text("seed/demo_contract.pdf")
    assert len(pdf_text) > 500
    assert "MASTER COMMERCIAL SUPPLY" in pdf_text
    
    clauses = split_into_clauses(pdf_text)
    assert 8 <= len(clauses) <= 12, f"Expected 8-12 clauses, got {len(clauses)}"
    for c in clauses:
        assert "clause_ref" in c
        assert "text" in c
        assert len(c["text"]) > 20

def test_docx_extraction_and_clause_count():
    """Verify that DOCX text extraction and clause splitting produce 8-12 obligations."""
    docx_text = extract_text("seed/demo_contract.docx")
    assert len(docx_text) > 500
    assert "MASTER COMMERCIAL SUPPLY" in docx_text
    
    clauses = split_into_clauses(docx_text)
    assert 8 <= len(clauses) <= 12, f"Expected 8-12 clauses, got {len(clauses)}"

def test_six_field_schema_validation():
    """Verify the six-field canonical validation function."""
    valid_record = {
        "actor": "Supplier",
        "trigger_deadline": "Monthly by 5th",
        "required_evidence": "Bill of Lading",
        "expected_state": "Deliver 5,000 MT",
        "violation_condition": "Volume < 5,000 MT",
        "remediation_action": "Credit 2% per day of delay"
    }
    assert validate_clause_record(valid_record) is True

    # Missing a required field
    invalid_record = valid_record.copy()
    invalid_record.pop("remediation_action")
    assert validate_clause_record(invalid_record) is False

    # Empty string in a required field
    invalid_record2 = valid_record.copy()
    invalid_record2["actor"] = "   "
    assert validate_clause_record(invalid_record2) is False

def test_llm_canonical_extraction_on_all_seed_clauses():
    """Verify that every extracted clause from the demo contract produces all six fields."""
    raw_text = extract_text("seed/demo_contract.pdf")
    clauses = split_into_clauses(raw_text)

    for c in clauses:
        record = extract_clause_with_llm(c["text"], c["clause_ref"])
        assert validate_clause_record(record) is True, f"Clause {c['clause_ref']} failed six-field validation: {record}"
        for f in SIX_FIELDS:
            assert f in record
            assert len(str(record[f]).strip()) > 0

def test_demo_contract_ingestion_flow(client):
    """Test full upload route -> review -> save pipeline using seed demo agreement."""
    # 1. Post to upload using seed demo contract
    upload_res = client.post("/contracts/upload", data={"use_demo": "1", "demo_type": "pdf"}, follow_redirects=True)
    assert upload_res.status_code == 200
    html = upload_res.get_data(as_text=True)
    assert "Review & Validate Extracted Obligations" in html
    assert "Clause 1.1" in html

    # 2. Simulate saving review form
    save_data = {
        "clause_index": ["0", "1"],
        "clause_text_0": "Clause 1.1: Deliver 5000 MT lithium",
        "actor_0": "Supplier",
        "trigger_deadline_0": "Monthly by 5th",
        "required_evidence_0": "Bill of Lading",
        "expected_state_0": "Deliver minimum 5,000 metric tons",
        "violation_condition_0": "Volume below 5,000 MT",
        "remediation_action_0": "2% credit per day",
        "target_value_0": "5000",
        "unit_0": "MT",
        "deadline_date_0": "2026-11-05",

        "clause_text_1": "Clause 2.1: Material purity 98.5%",
        "actor_1": "Supplier",
        "trigger_deadline_1": "Concurrent with shipment",
        "required_evidence_1": "Lab COA",
        "expected_state_1": "Purity >= 98.5%",
        "violation_condition_1": "Purity < 98.5%",
        "remediation_action_1": "Batch rejection within 72h",
        "target_value_1": "98.5",
        "unit_1": "%",
        "deadline_date_1": "2026-11-06"
    }

    save_res = client.post("/contracts/save", data=save_data, follow_redirects=True)
    assert save_res.status_code == 200
    save_html = save_res.get_data(as_text=True)
    assert "Successfully ingested" in save_html

    # Check database
    conn = sqlite3.connect(TestConfig.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    contracts = conn.execute("SELECT * FROM contracts").fetchall()
    obligations = conn.execute("SELECT * FROM obligations").fetchall()
    conn.close()

    assert len(contracts) >= 1
    assert len(obligations) == 2
    assert obligations[0]["actor"] == "Supplier"
    assert obligations[0]["target_value"] == 5000.0
    assert obligations[1]["target_value"] == 98.5
