from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, abort
from flask_login import current_user
from app.database import get_db, get_owned_or_404
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
    uid = current_user.id
    escalations = db.execute("""
        SELECT e.*, o.clause_text, o.actor, o.target_value, o.unit, c.title as contract_title
        FROM escalations e 
        LEFT JOIN obligations o ON e.obligation_id = o.id 
        LEFT JOIN contracts c ON o.contract_id = c.id
        WHERE e.user_id = ?
        ORDER BY e.id ASC
    """, (uid,)).fetchall()

    verification = verify_chain(db=db, user_id=uid)

    # Active obligations owned by current_user eligible for manual escalation
    violatable_obligations = db.execute("""
        SELECT o.id, o.clause_text, o.actor, o.risk_score, o.status 
        FROM obligations o
        JOIN contracts c ON o.contract_id = c.id
        WHERE c.user_id = ? AND (o.status IN ('VIOLATED', 'AT_RISK') OR o.risk_score >= 40.0)
        ORDER BY o.risk_score DESC
    """, (uid,)).fetchall()

    return render_template(
        "escalations/index.html",
        escalations=escalations,
        verification=verification,
        violatable_obligations=violatable_obligations
    )


@escalations_bp.route("/<int:block_id>")
def detail(block_id):
    """Retrieve a specific escalation ledger block if owned by current_user, otherwise 404."""
    db = get_db()
    uid = current_user.id
    esc = get_owned_or_404("escalation", block_id, user_id=uid, db=db)
    return jsonify(dict(esc))


@escalations_bp.route("/verify", methods=["GET", "POST"])
def verify():
    """Cryptographic audit endpoint walking the current user's hash chain."""
    db = get_db()
    uid = current_user.id
    result = verify_chain(db=db, user_id=uid)
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(result)

    if result["is_valid"]:
        flash(result["message"], "success")
    else:
        flash(result["reason"], "danger")
    return redirect(url_for("escalations.index"))


@escalations_bp.route("/tamper", methods=["POST"])
def tamper():
    """Simulate malicious ledger tampering on the current user's chain to test audit tripwires."""
    db = get_db()
    uid = current_user.id
    block_id_str = request.form.get("block_id") or (request.json.get("block_id") if request.is_json else None)
    block_id = None
    if block_id_str is not None and str(block_id_str).strip() != "":
        try:
            block_id = int(block_id_str)
        except ValueError:
            abort(404)
        # Verify the specified block belongs to current_user (returns 404 otherwise)
        get_owned_or_404("escalation", block_id, user_id=uid, db=db)

    result = simulate_tampering(block_id=block_id, db=db, user_id=uid)

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(result)

    if result["success"]:
        flash(result["message"], "warning")
    else:
        flash(result["message"], "danger")
    return redirect(url_for("escalations.index"))


@escalations_bp.route("/repair", methods=["POST"])
def repair():
    """Recalculate hashes from Genesis block for the current user's chain and restore cryptographic validity."""
    db = get_db()
    uid = current_user.id
    result = repair_chain(db=db, user_id=uid)

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(result)

    flash(result["message"], "success")
    return redirect(url_for("escalations.index"))


@escalations_bp.route("/create", methods=["POST"])
def create():
    """Manually escalate an obligation owned by current_user to append a new block to their chain."""
    obligation_id = request.form.get("obligation_id")
    reason = request.form.get("reason", "").strip() or "Unmitigated contract breach escalated to immutable ledger."

    if not obligation_id:
        flash("Obligation ID is required for escalation.", "warning")
        return redirect(url_for("escalations.index"))

    db = get_db()
    uid = current_user.id
    try:
        ob_id_int = int(obligation_id)
    except ValueError:
        abort(404)

    ob = get_owned_or_404("obligation", ob_id_int, user_id=uid, db=db)
    risk_score = ob["risk_score"] if ob["risk_score"] is not None else 85.0

    entry = add_escalation_entry(
        obligation_id=ob_id_int,
        reason=reason,
        risk_score=risk_score,
        db=db,
        user_id=uid
    )

    flash(f"Block #{entry['id']} appended to cryptographic escalation chain.", "success")
    return redirect(url_for("escalations.index"))
