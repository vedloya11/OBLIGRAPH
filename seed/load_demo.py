import os
import sys
import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config
from app.database import get_db_connection, init_db
from engine.calculator import compute_obligation_metrics, recompute_all_obligations
from engine.hash_chain import add_escalation_entry, verify_chain
from engine.config_store import set_setting

def load_demo_data(db_path: str = None) -> dict:
    """
    Reset and seed the complete canonical demo scenario for OBLIGRAPH.
    Seeds:
      1. Master Commercial Supply Agreement contract record.
      2. 10 canonical obligations with 6-field specifications.
      3. Upstream/downstream dependency links.
      4. Multi-channel evidence spanning Email, Google Sheet, and Manual Upload.
      5. One deliberately violated obligation (Chemical Purity: 98.1% vs 98.5%).
      6. One open counter-evidence dispute with 48h review window.
      7. One sealed escalation block in the SHA-256 cryptographic ledger.
    """
    if db_path is None:
        try:
            from flask import current_app
            if current_app:
                db_path = current_app.config.get("DATABASE_PATH", Config.DATABASE_PATH)
            else:
                db_path = Config.DATABASE_PATH
        except Exception:
            db_path = Config.DATABASE_PATH

    # Ensure schema exists
    init_db(db_path)

    conn = get_db_connection(db_path)
    now = datetime.datetime.now(datetime.timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.date.today()

    with conn:
        # 1. Reset tables cleanly
        conn.execute("DELETE FROM escalations")
        conn.execute("DELETE FROM disputes")
        conn.execute("DELETE FROM evidence")
        conn.execute("DELETE FROM dependencies")
        conn.execute("DELETE FROM obligations")
        conn.execute("DELETE FROM contracts")
        try:
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('contracts', 'obligations', 'dependencies', 'evidence', 'disputes', 'escalations')")
        except Exception:
            pass

        # 2. Ingest Canonical Contract
        contract_title = "Global Lithium & Chemical Materials Master Supply Agreement"
        filename = "demo_contract.pdf"
        
        # Read text from seed file if available
        pdf_path = Path(__file__).parent / "demo_contract.pdf"
        raw_text = "Master Commercial Supply Agreement between Apex Materials Ltd (Supplier) and Nexus Energy Corp (Buyer)."
        if pdf_path.exists():
            try:
                import pypdf
                reader = pypdf.PdfReader(str(pdf_path))
                raw_text = "\n".join(page.extract_text() or "" for page in reader.pages)
            except Exception:
                pass

        cur = conn.execute(
            "INSERT INTO contracts (title, filename, raw_text) VALUES (?, ?, ?)",
            (contract_title, filename, raw_text)
        )
        contract_id = cur.lastrowid

        # Reference dates for upcoming / overdue schedules
        d_due_14 = (today + datetime.timedelta(days=14)).strftime("%Y-%m-%d")
        d_due_21 = (today + datetime.timedelta(days=21)).strftime("%Y-%m-%d")
        d_due_30 = (today + datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        d_due_45 = (today + datetime.timedelta(days=45)).strftime("%Y-%m-%d")

        # 3. Insert 10 Canonical Obligations
        obligations_data = [
            (
                contract_id,
                "Clause 1.1: Minimum Monthly Delivery Volumes",
                "Supplier",
                "Monthly by 5th",
                "Bill of Lading / Inbound Freight Manifest",
                "Deliver minimum 5,000 MT battery-grade lithium carbonate per month",
                "Delivered volume below 5,000 MT or arrival past 5th calendar day",
                "Expedite replacement tonnage via air/priority freight; credit 2% per day of delay",
                5000.0,
                "MT",
                d_due_14
            ),
            (
                contract_id,
                "Clause 1.2: Hazardous Materials Packaging & Labeling",
                "Supplier",
                "Prior to port dispatch",
                "Port Hazmat Authority Container Certificate",
                "Hermetically sealed UN-approved steel drums adhering to GHS hazard labeling",
                "Ruptured container seals, leaking drums, or missing GHS hazard placards",
                "Immediate port quarantine; repackaging surcharges billed to Supplier",
                100.0,
                "%",
                d_due_14
            ),
            (
                contract_id,
                "Clause 2.1: Material Quality and Chemical Purity Standard",
                "Supplier",
                "Concurrent with dispatch",
                "Certified Laboratory Purity Assay Certificate (COA)",
                "Chemical purity not less than 98.5% Li2CO3 with iron content strictly below 0.001%",
                "Laboratory assay reveals chemical purity below 98.5% Li2CO3",
                "Immediate batch rejection; replacement conforming batch dispatched within 72 hours",
                98.5,
                "%",
                d_due_14
            ),
            (
                contract_id,
                "Clause 2.2: Certificate of Chemical Analysis (COA) Submission",
                "Supplier",
                "Concurrent with dispatch lot",
                "Independent Certified Laboratory Purity Assay signed by accredited agency",
                "Valid COA transmitted prior to port vessel arrival",
                "Failure to transmit accredited COA prior to vessel docking",
                "Suspension of payment timelines and customs clearance hold without interest penalty",
                1.0,
                "certificate",
                d_due_14
            ),
            (
                contract_id,
                "Clause 3.1: 30-Day Advance Written Notice for Price Adjustments",
                "Supplier",
                "At least 30 days prior to quarter end",
                "Registered mail confirmation letter & formal transmission receipt",
                "Provide at least thirty (30) calendar days prior written notice before price adjustments",
                "Unit price adjustment implemented without 30-day registered advance notice",
                "Price increase deemed void and non-binding; baseline contract pricing persists",
                30.0,
                "days",
                d_due_21
            ),
            (
                contract_id,
                "Clause 3.2: Quarterly Price Index Adjustment Cap",
                "Supplier",
                "Quarterly pricing cycle",
                "Executive review committee joint sign-off protocol",
                "Proposed price adjustment does not exceed 5.0% of preceding quarter index",
                "Price adjustment request exceeds 5.0% without mutual written sign-off",
                "Escalated executive review within 10 business days before invoice generation",
                5.0,
                "%",
                d_due_21
            ),
            (
                contract_id,
                "Clause 4.1: Inspection and Dispute Notification Window",
                "Buyer",
                "Within 7 business days of discharge",
                "Terminal Receiving Inspection Notice",
                "Inspect incoming consignment and submit formal notice of defect within 7 days",
                "Notice of defect served past 7 business days after terminal delivery",
                "Consignment deemed irrevocably accepted as delivered",
                7.0,
                "days",
                d_due_14
            ),
            (
                contract_id,
                "Clause 5.1: Payment Settlement and Invoicing Terms",
                "Buyer",
                "Net 30 days from electronic invoice",
                "Bank SWIFT wire confirmation / ERP payment remittance",
                "Remit full payment of undisputed invoice amounts Net 30 days from matching BOL",
                "Undisputed invoice balance remains unpaid past 30 days",
                "Interest accrues at SOFR plus 3.5% per annum until settled",
                30.0,
                "days",
                d_due_30
            ),
            (
                contract_id,
                "Clause 6.1: Annual Carbon Intensity Audit and Environmental Standards",
                "Supplier",
                "Annually on or before Dec 15th",
                "Third-party ISO 14064 greenhouse gas audit report",
                "Supply chain carbon intensity does not exceed 2.4 kg CO2e per kg product",
                "Audited carbon intensity exceeds 2.4 kg CO2e/kg product",
                "Purchase verified Gold Standard carbon offsets to balance excess within 30 days",
                2.4,
                "kg CO2e/kg",
                d_due_45
            ),
            (
                contract_id,
                "Clause 7.1: Comprehensive Commercial General Liability Insurance",
                "Supplier",
                "15 days prior to policy renewal",
                "ACORD Certificate of Insurance naming Buyer as additional insured",
                "Maintain active Commercial General Liability insurance with minimum $10M aggregate limit",
                "Lapse of liability policy or aggregate coverage under $10,000,000",
                "Immediate suspension of purchase orders; Buyer may procure backstop coverage at Supplier expense",
                10000000.0,
                "USD",
                d_due_45
            )
        ]

        for ob in obligations_data:
            conn.execute("""
                INSERT INTO obligations (
                    contract_id, clause_text, actor, trigger_deadline, required_evidence,
                    expected_state, violation_condition, remediation_action, target_value,
                    unit, deadline_date, status, confidence, risk_score, risk_label
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0.0, 0.0, 'LOW')
            """, ob)

        # 4. Insert Dependency Graph Links
        # Link 1: Clause 2.2 (COA) is upstream for Clause 1.1 (Delivery Acceptance)
        conn.execute("INSERT INTO dependencies (upstream_id, downstream_id, note) VALUES (4, 1, 'Accredited COA required before customs release and delivery acceptance')")
        # Link 2: Clause 3.1 (30-day notice) is upstream for Clause 3.2 (Quarterly Cap)
        conn.execute("INSERT INTO dependencies (upstream_id, downstream_id, note) VALUES (5, 6, '30-day formal notice must precede index adjustment cap calculation')")
        # Link 3: Clause 1.2 (Packaging) is upstream for Clause 4.1 (Inspection)
        conn.execute("INSERT INTO dependencies (upstream_id, downstream_id, note) VALUES (2, 7, 'Hazmat container integrity verifies receipt condition prior to final acceptance')")
        # Link 4: Clause 1.1 (Delivery) is upstream for Clause 5.1 (Payment)
        conn.execute("INSERT INTO dependencies (upstream_id, downstream_id, note) VALUES (1, 8, 'Satisfactory delivery BOL triggers Net 30 payment clock')")

        # 5. Insert Multi-Channel Evidence spanning all 3 sources
        evidence_records = [
            # Channel 1: Email IMAP parser (Counterparty Confirmed, 0.70 weight)
            (
                1,
                "email",
                "counterparty_confirmed",
                0.70,
                4850.0,
                "Inbound freight manifest BOL-88492: 4,850 MT arrived Rotterdam Berth 3 (150 MT short of 5,000 MT target)",
                "EMAIL: logistics@apexmaterials.com - BOL #88492-APEX"
            ),
            # Channel 2: Google Sheet / ERP feed (Independently Verifiable, 1.00 weight) -> DELIBERATE VIOLATION
            (
                3,
                "sheet",
                "independently_verifiable",
                1.00,
                96.8,
                "Accredited Lab Assay Report #COA-992: Purity measured 96.8% Li2CO3 — Batch rejected for chemical purity breach below 98.5% contract standard",
                "SHEET: Lab Assay Feed - Row #142 (Port Berth Chemical Lab Assay #LB-992)"
            ),
            # Channel 3: Manual Upload (Self-Reported, 0.40 weight) -> COMPLIANT
            (
                5,
                "manual",
                "self_reported",
                0.40,
                30.0,
                "Registered mail confirmation letter: 30-day notice served to Nexus procurement desk",
                "UPLOAD: Notice_Letter_Q4_Price.pdf (Tracking #NL-4492-REG)"
            ),
            # Channel 2: Google Sheet feed (Independently Verifiable, 1.00 weight) -> COMPLIANT
            (
                2,
                "sheet",
                "independently_verifiable",
                1.00,
                100.0,
                "Port Hazmat Authority Container Certificate #HZ-402: UN-approved drums inspected, seals intact",
                "SHEET: Hazmat Clearance Log - Berth 3 Container #CT-889"
            ),
            # Channel 2: Google Sheet feed (Independently Verifiable, 1.00 weight) -> COMPLIANT
            (
                9,
                "sheet",
                "independently_verifiable",
                1.00,
                2.10,
                "ISO 14064 Greenhouse Gas Emissions Verification Audit: 2.10 kg CO2e/kg recorded (Compliant under 2.4 limit)",
                "SHEET: ESG & Carbon Audit 2026 - Third-Party Certifier Bureau Veritas"
            ),
            # Channel 3: Manual Upload (Independently Verifiable, 1.00 weight) -> COMPLIANT
            (
                10,
                "manual",
                "independently_verifiable",
                1.00,
                10000000.0,
                "ACORD Certificate of Insurance #COI-2026-992: $10,000,000 Commercial General Liability active with Nexus Energy as additional insured",
                "UPLOAD: ACORD_COI_ApexMaterials_2026.pdf (Policy #GL-9948271-00)"
            )
        ]

        for ev in evidence_records:
            conn.execute("""
                INSERT INTO evidence (obligation_id, source, trust_level, trust_weight, observed_value, description, raw_ref, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (*ev, now_str))

        # 6. Insert Active Dispute on Obligation #3 (Chemical Purity)
        conn.execute("""
            INSERT INTO disputes (obligation_id, submitted_by, side, counter_evidence, attachment, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            3,
            "Apex Materials QA Director",
            "obligated_party",
            "Secondary split-sample assay conducted by SGS Minerals International on Retain Sample #LOT-2026-092 confirmed 98.62% Li2CO3 purity. Primary titration was calibrated on an unpurged argon baseline. Formal request for mutual umpire laboratory arbitration under Section 4.3 submitted.",
            "SGS_Assay_Rebuttal_COA_98.62.pdf",
            "open",
            now_str
        ))

        # 7. Recompute all obligations deterministically
        recompute_all_obligations(db=conn)

        # 8. Add sealed escalation entry to SHA-256 hash chain
        add_escalation_entry(
            obligation_id=1,
            reason="Uncured monthly delivery volume shortfall of 150 MT under Clause 1.1 beyond 5-day grace window; priority freight not dispatched.",
            risk_score=84.5,
            db=conn
        )
        # Update settings to point to this active contract
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('active_contract_id', ?)", (str(contract_id),))

    # 9. Verify cryptographic ledger
    audit = verify_chain(conn)
    conn.close()

    summary = {
        "status": "success",
        "contract_id": contract_id,
        "contract_title": contract_title,
        "obligations_count": len(obligations_data),
        "dependencies_count": 4,
        "evidence_count": len(evidence_records),
        "open_disputes": 1,
        "escalation_blocks": audit.get("total_blocks", 1),
        "chain_valid": audit.get("is_valid", True),
        "message": "Demo contract, obligations, dependency graph, multi-channel evidence, active dispute, and cryptographic escalation sealed successfully."
    }
    return summary

def main():
    print("=" * 60)
    print("OBLIGRAPH - Seed & Load Demo Environment")
    print("=" * 60)
    print(f"Target Database: {Config.DATABASE_PATH}")
    
    result = load_demo_data()
    
    print("\nDemo Data Seeding Summary:")
    print(f"  * Contract: '{result['contract_title']}' (ID: #{result['contract_id']})")
    print(f"  * Obligations Ingested: {result['obligations_count']}")
    print(f"  * Dependency Graph Edges: {result['dependencies_count']}")
    print(f"  * Multi-Channel Evidence Records: {result['evidence_count']}")
    print(f"  * Open Disputes: {result['open_disputes']} (48h countdown active)")
    print(f"  * Cryptographic Ledger Height: #{result['escalation_blocks']} (Chain Valid: {result['chain_valid']})")
    print("\n[OK] Demo environment successfully initialized. Run 'flask run' to explore.")

if __name__ == "__main__":
    main()
