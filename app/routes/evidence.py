from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify
from app.database import get_db
from engine.connectors import (
    sync_imap_emails,
    sync_google_sheets,
    add_manual_evidence,
    seed_demo_evidence_set,
    DEFAULT_SOURCE_TRUST
)
from engine.config_store import get_setting

evidence_bp = Blueprint("evidence", __name__, url_prefix="/evidence")

@evidence_bp.route("/")
def index():
    db = get_db()

    # Query all evidence with joined obligation details
    evidence_items = db.execute("""
        SELECT e.*, o.clause_text, o.actor, o.risk_label, o.target_value, o.unit 
        FROM evidence e 
        LEFT JOIN obligations o ON e.obligation_id = o.id 
        ORDER BY e.created_at DESC
    """).fetchall()

    # Query all obligations for the Add Evidence dropdown
    obligations = db.execute("""
        SELECT id, clause_text, actor, expected_state, target_value, unit 
        FROM obligations 
        ORDER BY id ASC
    """).fetchall()

    # Connector metadata and last sync timestamps
    email_last_sync = get_setting("email_sync_last", "Never")
    sheet_last_sync = get_setting("sheet_sync_last", "Never")
    
    # Trust weights from settings
    weights = {
        "self_reported": get_setting("weight_self_reported", "0.4"),
        "counterparty_confirmed": get_setting("weight_counterparty_confirmed", "0.7"),
        "independently_verifiable": get_setting("weight_independently_verifiable", "1.0")
    }

    return render_template(
        "evidence/index.html",
        evidence=evidence_items,
        obligations=obligations,
        email_last_sync=email_last_sync,
        sheet_last_sync=sheet_last_sync,
        weights=weights
    )

@evidence_bp.route("/add", methods=["POST"])
def add():
    obligation_id = request.form.get("obligation_id")
    if not obligation_id:
        flash("Please select an obligation to correlate evidence with.", "warning")
        return redirect(url_for("evidence.index"))

    source = request.form.get("source", "manual")
    trust_level = request.form.get("trust_level") or DEFAULT_SOURCE_TRUST.get(source, "self_reported")
    
    observed_val_str = request.form.get("observed_value", "").strip()
    observed_value = float(observed_val_str) if observed_val_str else None
    
    description = request.form.get("description", "").strip()
    if not description:
        flash("Please provide an evidence description or attestation statement.", "warning")
        return redirect(url_for("evidence.index"))

    raw_ref = request.form.get("raw_ref", "").strip()

    db = get_db()
    try:
        ev_id = add_manual_evidence(
            db=db,
            obligation_id=int(obligation_id),
            observed_value=observed_value,
            description=description,
            trust_level=trust_level,
            source=source,
            raw_ref=raw_ref
        )
        flash(f"Evidence record #{ev_id} registered successfully with trust level '{trust_level}'. Obligation metrics recomputed.", "success")
    except Exception as e:
        flash(f"Error registering evidence: {str(e)}", "danger")

    return redirect(request.referrer or url_for("evidence.index"))

@evidence_bp.route("/sync/<source>", methods=["POST"])
def sync(source):
    db = get_db()
    try:
        if source == "email":
            res = sync_imap_emails(db)
            flash(f"IMAP Email connector synchronized. Processed {res['processed']} emails; ingested {res['inserted']} evidence records.", "info")
        elif source == "sheet":
            res = sync_google_sheets(db)
            flash(f"Google Sheet / CSV connector synchronized. Ingested {res['inserted']} verifiable records.", "info")
        elif source == "all":
            res_email = sync_imap_emails(db)
            res_sheet = sync_google_sheets(db)
            total_inserted = res_email["inserted"] + res_sheet["inserted"]
            flash(f"All connectors synchronized successfully! Ingested {total_inserted} new evidence records.", "success")
        else:
            flash(f"Unknown connector source '{source}'.", "warning")
    except Exception as e:
        flash(f"Connector sync encountered an error: {str(e)}", "danger")

    return redirect(request.referrer or url_for("evidence.index"))

@evidence_bp.route("/demo", methods=["POST"])
def demo_seed():
    db = get_db()
    try:
        inserted = seed_demo_evidence_set(db)
        if inserted > 0:
            flash(f"Successfully loaded {inserted} demo evidence items across all 3 source types (email, sheet, manual) with calibrated trust weights!", "success")
        else:
            flash("No obligations found in database to attach demo evidence to. Please ingest a contract first.", "warning")
    except Exception as e:
        flash(f"Error seeding demo evidence: {str(e)}", "danger")

    return redirect(url_for("evidence.index"))
