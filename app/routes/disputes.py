from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify
from app.database import get_db
from engine.calculator import compute_obligation_metrics, get_risk_thresholds
from engine.connectors import get_trust_weight_for_level

disputes_bp = Blueprint("disputes", __name__, url_prefix="/disputes")

def calculate_remaining_dispute_hours(deadline_str: str) -> float:
    """Calculate remaining hours in dispute window."""
    if not deadline_str or deadline_str in ["EXPIRED", "RESOLVED"]:
        return 0.0
    try:
        clean_str = deadline_str.replace("Z", "+00:00")
        dl = datetime.fromisoformat(clean_str)
        now = datetime.now(timezone.utc)
        diff = (dl - now).total_seconds() / 3600.0
        return round(max(0.0, diff), 1)
    except Exception:
        return 0.0

@disputes_bp.route("/")
def index():
    db = get_db()
    
    # All disputes
    disputes = db.execute("""
        SELECT d.*, o.clause_text, o.actor, o.risk_score, o.risk_label, o.status as obligation_status, o.dispute_deadline
        FROM disputes d 
        LEFT JOIN obligations o ON d.obligation_id = o.id 
        ORDER BY d.created_at DESC
    """).fetchall()

    # Flagged obligations eligible for dispute (AT_RISK, VIOLATED, or active dispute window)
    flagged_obligations = db.execute("""
        SELECT o.*, c.title as contract_title 
        FROM obligations o 
        LEFT JOIN contracts c ON o.contract_id = c.id 
        WHERE o.status IN ('AT_RISK', 'VIOLATED') OR o.risk_score >= 35.0 OR o.dispute_deadline IS NOT NULL
        ORDER BY o.risk_score DESC
    """).fetchall()

    enriched_disputes = []
    for d in disputes:
        hrs_left = calculate_remaining_dispute_hours(d["dispute_deadline"])
        d_dict = dict(d)
        d_dict["hours_left"] = hrs_left
        enriched_disputes.append(d_dict)

    enriched_flagged = []
    for f in flagged_obligations:
        hrs_left = calculate_remaining_dispute_hours(f["dispute_deadline"])
        f_dict = dict(f)
        f_dict["hours_left"] = hrs_left
        enriched_flagged.append(f_dict)

    return render_template(
        "disputes/index.html",
        disputes=enriched_disputes,
        flagged_obligations=enriched_flagged
    )

@disputes_bp.route("/create/<int:obligation_id>", methods=["GET"])
def create(obligation_id):
    db = get_db()
    obligation = db.execute("""
        SELECT o.*, c.title as contract_title 
        FROM obligations o 
        LEFT JOIN contracts c ON o.contract_id = c.id 
        WHERE o.id = ?
    """, (obligation_id,)).fetchone()

    if not obligation:
        flash("Obligation record not found.", "warning")
        return redirect(url_for("disputes.index"))

    hours_left = calculate_remaining_dispute_hours(obligation["dispute_deadline"])

    # Recent evidence items for this obligation
    evidence_rows = db.execute("""
        SELECT * FROM evidence 
        WHERE obligation_id = ? 
        ORDER BY created_at DESC
    """, (obligation_id,)).fetchall()

    return render_template(
        "disputes/create.html",
        obligation=obligation,
        hours_left=hours_left,
        evidence=evidence_rows
    )

@disputes_bp.route("/submit", methods=["POST"])
def submit():
    obligation_id = request.form.get("obligation_id")
    submitted_by = request.form.get("submitted_by", "").strip() or "Obligated Party Representative"
    side = request.form.get("side", "obligated_party")
    counter_evidence = request.form.get("counter_evidence", "").strip()
    attachment = request.form.get("attachment", "").strip()
    
    counter_val_str = request.form.get("counter_value", "").strip()
    counter_val = float(counter_val_str) if counter_val_str else None

    if not obligation_id or not counter_evidence:
        flash("Obligation and counter-evidence statement are required.", "warning")
        return redirect(url_for("disputes.index"))

    db = get_db()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # 1. Insert dispute record
    cur = db.execute("""
        INSERT INTO disputes (
            obligation_id, submitted_by, side, counter_evidence, attachment, status, created_at
        ) VALUES (?, ?, ?, ?, ?, 'open', ?)
    """, (obligation_id, submitted_by, side, counter_evidence, attachment, now_str))
    dispute_id = cur.lastrowid

    # 2. Correlate counter-evidence into evidence stream
    trust_level = "counterparty_confirmed" if side == "obligated_party" else "self_reported"
    trust_weight = get_trust_weight_for_level(trust_level)
    desc = f"Counter-Evidence from {submitted_by} ({side.replace('_', ' ').title()}): {counter_evidence}"
    
    db.execute("""
        INSERT INTO evidence (
            obligation_id, source, trust_level, trust_weight,
            observed_value, description, raw_ref, created_at
        ) VALUES (?, 'manual', ?, ?, ?, ?, ?, ?)
    """, (obligation_id, trust_level, trust_weight, counter_val, desc, attachment or "DISPUTE-FILING", now_str))

    db.commit()

    # 3. Recompute obligation metrics
    metrics = compute_obligation_metrics(int(obligation_id), db=db)

    # 4. If new counter-evidence lowered risk below high cutoff, mark dispute as resolved
    thresh_high, _, _ = get_risk_thresholds()
    if metrics.get("risk_score", 100.0) < thresh_high and metrics.get("status") != "VIOLATED":
        db.execute("UPDATE disputes SET status = 'resolved' WHERE id = ?", (dispute_id,))
        db.commit()
        flash(f"Counter-evidence successfully registered! Risk score reduced to {metrics.get('risk_score')}. Dispute marked as resolved.", "success")
    else:
        flash(f"Counter-evidence registered. Obligation recomputed: Risk Score {metrics.get('risk_score')}, Status: {metrics.get('status')}.", "info")

    return redirect(url_for("disputes.detail", dispute_id=dispute_id))

@disputes_bp.route("/<int:dispute_id>")
def detail(dispute_id):
    db = get_db()
    dispute = db.execute("""
        SELECT d.*, o.clause_text, o.actor, o.risk_score, o.risk_label, o.status as obligation_status,
               o.target_value, o.unit, o.dispute_deadline, o.confidence, c.title as contract_title
        FROM disputes d 
        JOIN obligations o ON d.obligation_id = o.id 
        LEFT JOIN contracts c ON o.contract_id = c.id 
        WHERE d.id = ?
    """, (dispute_id,)).fetchone()

    if not dispute:
        flash("Dispute record not found.", "warning")
        return redirect(url_for("disputes.index"))

    hours_left = calculate_remaining_dispute_hours(dispute["dispute_deadline"])

    # Fetch all evidence for side-by-side timeline
    evidence_rows = db.execute("""
        SELECT * FROM evidence 
        WHERE obligation_id = ? 
        ORDER BY created_at ASC
    """, (dispute["obligation_id"],)).fetchall()

    # Separate into Obligated Party (Counter-Evidence) vs Counterparty / Automated Claims
    obligated_party_submissions = []
    counterparty_submissions = []

    for ev in evidence_rows:
        desc_lower = (ev["description"] or "").lower()
        if "counter-evidence" in desc_lower or ev["trust_level"] == "self_reported" or "dispute" in desc_lower:
            obligated_party_submissions.append(ev)
        else:
            counterparty_submissions.append(ev)

    # If all submissions are on one side, ensure side-by-side has the dispute itself
    return render_template(
        "disputes/detail.html",
        dispute=dispute,
        hours_left=hours_left,
        obligated_party_submissions=obligated_party_submissions,
        counterparty_submissions=counterparty_submissions,
        all_evidence=evidence_rows
    )

@disputes_bp.route("/resolve/<int:dispute_id>", methods=["POST"])
def resolve(dispute_id):
    db = get_db()
    db.execute("UPDATE disputes SET status = 'resolved' WHERE id = ?", (dispute_id,))
    
    # Clear dispute deadline on the obligation
    dispute = db.execute("SELECT obligation_id FROM disputes WHERE id = ?", (dispute_id,)).fetchone()
    if dispute:
        db.execute("UPDATE obligations SET dispute_deadline = NULL WHERE id = ?", (dispute["obligation_id"],))
        compute_obligation_metrics(dispute["obligation_id"], db=db)

    db.commit()
    flash(f"Dispute #{dispute_id} resolved by mutual consent.", "success")
    return redirect(url_for("disputes.detail", dispute_id=dispute_id))
