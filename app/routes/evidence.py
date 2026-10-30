from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, abort
from flask_login import current_user
from app.database import get_db, get_owned_or_404
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
    uid = current_user.id

    # Query all evidence belonging to current_user with joined obligation details
    evidence_items = db.execute("""
        SELECT e.*, o.clause_text, o.actor, o.risk_label, o.target_value, o.unit 
        FROM evidence e 
        JOIN obligations o ON e.obligation_id = o.id 
        JOIN contracts c ON o.contract_id = c.id
        WHERE c.user_id = ?
        ORDER BY e.created_at DESC
    """, (uid,)).fetchall()

    # Query all obligations owned by current_user for the Add Evidence dropdown
    obligations = db.execute("""
        SELECT o.id, o.clause_text, o.actor, o.expected_state, o.target_value, o.unit 
        FROM obligations o
        JOIN contracts c ON o.contract_id = c.id
        WHERE c.user_id = ?
        ORDER BY o.id ASC
    """, (uid,)).fetchall()

    email_last_sync = get_setting("email_sync_last", "Never", user_id=uid)
    sheet_last_sync = get_setting("sheet_sync_last", "Never", user_id=uid)

    weights = {
        "self_reported": get_setting("weight_self_reported", "0.4", user_id=uid),
        "counterparty_confirmed": get_setting("weight_counterparty_confirmed", "0.7", user_id=uid),
        "independently_verifiable": get_setting("weight_independently_verifiable", "1.0", user_id=uid)
    }

    return render_template(
        "evidence/index.html",
        evidence=evidence_items,
        obligations=obligations,
        email_last_sync=email_last_sync,
        sheet_last_sync=sheet_last_sync,
        weights=weights
    )


@evidence_bp.route("/<int:evidence_id>")
def detail(evidence_id):
    """Retrieve a specific evidence record if owned by current_user, otherwise 404."""
    db = get_db()
    uid = current_user.id
    ev = get_owned_or_404("evidence", evidence_id, user_id=uid, db=db)
    return jsonify(dict(ev))


@evidence_bp.route("/add", methods=["POST"])
def add():
    obligation_id = request.form.get("obligation_id")
    if not obligation_id:
        flash("Please select an obligation to correlate evidence with.", "warning")
        return redirect(url_for("evidence.index"))

    db = get_db()
    uid = current_user.id
    try:
        ob_id_int = int(obligation_id)
    except ValueError:
        abort(404)

    # Verify obligation belongs to current_user (returns 404 if not owned)
    get_owned_or_404("obligation", ob_id_int, user_id=uid, db=db)

    source = request.form.get("source", "manual")
    trust_level = request.form.get("trust_level") or DEFAULT_SOURCE_TRUST.get(source, "self_reported")

    observed_val_str = request.form.get("observed_value", "").strip()
    observed_value = float(observed_val_str) if observed_val_str else None

    description = request.form.get("description", "").strip()
    if not description:
        flash("Please provide an evidence description or attestation statement.", "warning")
        return redirect(url_for("evidence.index"))

    raw_ref = request.form.get("raw_ref", "").strip()

    try:
        ev_id = add_manual_evidence(
            db=db,
            obligation_id=ob_id_int,
            observed_value=observed_value,
            description=description,
            trust_level=trust_level,
            source=source,
            raw_ref=raw_ref,
            user_id=uid
        )
        flash(f"Evidence record #{ev_id} registered successfully with trust level '{trust_level}'. Obligation metrics recomputed.", "success")
    except Exception as e:
        flash(f"Error registering evidence: {str(e)}", "danger")

    return redirect(request.referrer or url_for("evidence.index"))


@evidence_bp.route("/sync/<source>", methods=["POST"])
def sync(source):
    db = get_db()
    uid = current_user.id
    try:
        if source == "email":
            res = sync_imap_emails(db, user_id=uid)
            flash(f"IMAP Email connector synchronized. Processed {res['processed']} emails; ingested {res['inserted']} evidence records.", "info")
        elif source == "sheet":
            res = sync_google_sheets(db, user_id=uid)
            flash(f"Google Sheet / CSV connector synchronized. Ingested {res['inserted']} verifiable records.", "info")
        elif source == "all":
            res_email = sync_imap_emails(db, user_id=uid)
            res_sheet = sync_google_sheets(db, user_id=uid)
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
    uid = current_user.id
    try:
        inserted = seed_demo_evidence_set(db, user_id=uid)
        if inserted > 0:
            flash(f"Successfully loaded {inserted} demo evidence items across all 3 source types (email, sheet, manual) with calibrated trust weights!", "success")
        else:
            flash("No obligations found in your account to attach demo evidence to. Please ingest a contract first.", "warning")
    except Exception as e:
        flash(f"Error seeding demo evidence: {str(e)}", "danger")

    return redirect(url_for("evidence.index"))
