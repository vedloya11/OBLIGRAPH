from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify
from app.database import get_db
from engine.hash_chain import (
    verify_chain,
    simulate_tampering,
    repair_chain,
    add_escalation_entry
)

escalations_bp = Blueprint("escalations", __name__, url_prefix="/escalations")

@escalations_bp.route("/")
def index():
    db = get_db()
    escalations = db.execute("""
        SELECT e.*, o.clause_text, o.actor, o.target_value, o.unit, c.title as contract_title
        FROM escalations e 
        LEFT JOIN obligations o ON e.obligation_id = o.id 
        LEFT JOIN contracts c ON o.contract_id = c.id
        ORDER BY e.id ASC
    """).fetchall()

    verification = verify_chain(db=db)

    # Active obligations eligible for manual escalation testing
    violatable_obligations = db.execute("""
        SELECT id, clause_text, actor, risk_score, status 
        FROM obligations 
        WHERE status IN ('VIOLATED', 'AT_RISK') OR risk_score >= 40.0
        ORDER BY risk_score DESC
    """).fetchall()

    return render_template(
        "escalations/index.html",
        escalations=escalations,
        verification=verification,
        violatable_obligations=violatable_obligations
    )

@escalations_bp.route("/verify", methods=["GET", "POST"])
def verify():
    """Cryptographic audit endpoint walking the full hash chain."""
    db = get_db()
    result = verify_chain(db=db)
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(result)
    
    if result["is_valid"]:
        flash(result["message"], "success")
    else:
        flash(result["reason"], "danger")
    return redirect(url_for("escalations.index"))

@escalations_bp.route("/tamper", methods=["POST"])
def tamper():
    """Simulate malicious ledger tampering to test audit tripwires."""
    db = get_db()
    block_id_str = request.form.get("block_id") or (request.json.get("block_id") if request.is_json else None)
    block_id = int(block_id_str) if block_id_str else None

    result = simulate_tampering(block_id=block_id, db=db)
    
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(result)

    if result["success"]:
        flash(result["message"], "warning")
    else:
        flash(result["message"], "danger")
    return redirect(url_for("escalations.index"))

@escalations_bp.route("/repair", methods=["POST"])
def repair():
    """Recalculate hashes from Genesis block and restore cryptographic validity."""
    db = get_db()
    result = repair_chain(db=db)
    
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(result)

    flash(result["message"], "success")
    return redirect(url_for("escalations.index"))

@escalations_bp.route("/create", methods=["POST"])
def create():
    """Manually escalate an obligation to append a new block to the chain."""
    obligation_id = request.form.get("obligation_id")
    reason = request.form.get("reason", "").strip() or "Unmitigated contract breach escalated to immutable ledger."
    
    if not obligation_id:
        flash("Obligation ID is required for escalation.", "warning")
        return redirect(url_for("escalations.index"))

    db = get_db()
    ob = db.execute("SELECT risk_score FROM obligations WHERE id = ?", (obligation_id,)).fetchone()
    risk_score = ob["risk_score"] if ob else 85.0

    entry = add_escalation_entry(
        obligation_id=int(obligation_id),
        reason=reason,
        risk_score=risk_score,
        db=db
    )

    flash(f"Block #{entry['id']} appended to cryptographic escalation chain.", "success")
    return redirect(url_for("escalations.index"))
