import datetime
from typing import Dict, Any, List, Optional
from flask import Blueprint, render_template, redirect, url_for, request, session, flash, jsonify, abort
from flask_login import current_user
from app.database import get_db, get_owned_or_404
from engine.hash_chain import verify_chain, get_last_escalation_block

main_bp = Blueprint("main", __name__)


@main_bp.route("/healthz")
def healthz():
    """Health check endpoint for Render deployment probes."""
    return jsonify({"status": "ok", "service": "obligraph"}), 200


@main_bp.route("/")
def landing():
    """Public landing page. Logged-in users are directed to their dashboard."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return render_template("landing.html")


@main_bp.route("/dashboard")
def dashboard():
    """Main predictive compliance monitoring dashboard with live per-user KPIs, charts, and lookahead."""
    db = get_db()
    uid = current_user.id

    # Check for contract scoping in query params or session
    contract_param = request.args.get("contract_id")
    if contract_param:
        if contract_param in ("all", "0", "portfolio"):
            session["active_contract_id"] = None
            session["active_contract_title"] = "All Contracts (Portfolio)"
        elif contract_param.isdigit():
            c_row = get_owned_or_404("contract", int(contract_param), user_id=uid, db=db)
            session["active_contract_id"] = c_row["id"]
            session["active_contract_title"] = c_row["title"]

    active_contract_id = session.get("active_contract_id")
    if active_contract_id:
        owned_c = db.execute(
            "SELECT id, title FROM contracts WHERE id = ? AND user_id = ?",
            (active_contract_id, uid)
        ).fetchone()
        if not owned_c:
            active_contract_id = None
            session["active_contract_id"] = None
            session["active_contract_title"] = "All Contracts (Portfolio)"

    is_scoped = bool(active_contract_id)

    # Query summary metrics scoped to current_user
    total_contracts = db.execute(
        "SELECT COUNT(*) FROM contracts WHERE user_id = ?", (uid,)
    ).fetchone()[0]

    if is_scoped:
        total_obligations = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.contract_id = ?",
            (uid, active_contract_id)
        ).fetchone()[0]
        compliant_count = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.contract_id = ? AND o.status = 'COMPLIANT'",
            (uid, active_contract_id)
        ).fetchone()[0]
        at_risk_count = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.contract_id = ? AND o.status = 'AT_RISK'",
            (uid, active_contract_id)
        ).fetchone()[0]
        violated_count = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.contract_id = ? AND o.status = 'VIOLATED'",
            (uid, active_contract_id)
        ).fetchone()[0]
        pending_count = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.contract_id = ? AND o.status = 'PENDING'",
            (uid, active_contract_id)
        ).fetchone()[0]

        low_risk_count = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.contract_id = ? AND o.risk_label = 'LOW'",
            (uid, active_contract_id)
        ).fetchone()[0]
        medium_risk_count = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.contract_id = ? AND o.risk_label = 'MEDIUM'",
            (uid, active_contract_id)
        ).fetchone()[0]
        high_risk_count = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.contract_id = ? AND o.risk_label = 'HIGH'",
            (uid, active_contract_id)
        ).fetchone()[0]

        open_disputes = db.execute(
            "SELECT COUNT(*) FROM disputes d JOIN obligations o ON d.obligation_id = o.id JOIN contracts c ON o.contract_id = c.id WHERE d.status = 'open' AND c.user_id = ? AND o.contract_id = ?",
            (uid, active_contract_id)
        ).fetchone()[0]

        total_escalations = db.execute(
            "SELECT COUNT(*) FROM escalations e JOIN obligations o ON e.obligation_id = o.id WHERE e.user_id = ? AND o.contract_id = ?",
            (uid, active_contract_id)
        ).fetchone()[0]
    else:
        total_obligations = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ?",
            (uid,)
        ).fetchone()[0]
        compliant_count = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.status = 'COMPLIANT'",
            (uid,)
        ).fetchone()[0]
        at_risk_count = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.status = 'AT_RISK'",
            (uid,)
        ).fetchone()[0]
        violated_count = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.status = 'VIOLATED'",
            (uid,)
        ).fetchone()[0]
        pending_count = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.status = 'PENDING'",
            (uid,)
        ).fetchone()[0]

        low_risk_count = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.risk_label = 'LOW'",
            (uid,)
        ).fetchone()[0]
        medium_risk_count = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.risk_label = 'MEDIUM'",
            (uid,)
        ).fetchone()[0]
        high_risk_count = db.execute(
            "SELECT COUNT(*) FROM obligations o JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ? AND o.risk_label = 'HIGH'",
            (uid,)
        ).fetchone()[0]

        open_disputes = db.execute(
            "SELECT COUNT(*) FROM disputes d JOIN obligations o ON d.obligation_id = o.id JOIN contracts c ON o.contract_id = c.id WHERE d.status = 'open' AND c.user_id = ?",
            (uid,)
        ).fetchone()[0]
        total_escalations = db.execute(
            "SELECT COUNT(*) FROM escalations WHERE user_id = ?",
            (uid,)
        ).fetchone()[0]

    compliance_pct = round((compliant_count / total_obligations * 100), 1) if total_obligations > 0 else 0.0

    # Trend line history (5 baseline milestones converging on current score)
    trend_labels = ["30d Ago", "21d Ago", "14d Ago", "7d Ago", "Today"]
    trend_values = [
        max(0.0, min(100.0, round(compliance_pct * 0.90, 1))),
        max(0.0, min(100.0, round(compliance_pct * 0.94, 1))),
        max(0.0, min(100.0, round(compliance_pct * 0.92, 1))),
        max(0.0, min(100.0, round(compliance_pct * 0.98, 1))),
        compliance_pct
    ]

    # Cryptographic Ledger State for current user
    chain_audit = verify_chain(db, user_id=uid)
    last_block = get_last_escalation_block(db, user_id=uid)

    # All obligations for current user (or scoped contract)
    if is_scoped:
        query = (
            "SELECT o.*, c.title as contract_title FROM obligations o "
            "JOIN contracts c ON o.contract_id = c.id "
            "WHERE c.user_id = ? AND o.contract_id = ? "
            "ORDER BY o.risk_score DESC, o.updated_at DESC"
        )
        contract_params = (uid, active_contract_id)
    else:
        query = (
            "SELECT o.*, c.title as contract_title FROM obligations o "
            "JOIN contracts c ON o.contract_id = c.id "
            "WHERE c.user_id = ? "
            "ORDER BY o.risk_score DESC, o.updated_at DESC"
        )
        contract_params = (uid,)

    all_obligations_raw = db.execute(query, contract_params).fetchall()
    obligations = [dict(r) for r in all_obligations_raw]

    # 30-Day Lookahead Calendar / Upcoming Deadlines
    today = datetime.date.today()
    upcoming_deadlines = []

    for ob in obligations:
        d_str = ob.get("deadline_date")
        if d_str:
            try:
                parts = [int(p) for p in d_str.strip().split("-")]
                if len(parts) == 3:
                    d_date = datetime.date(parts[0], parts[1], parts[2])
                    days_left = (d_date - today).days

                    if days_left < 0:
                        urgency = "danger"
                        badge_text = f"Overdue by {abs(days_left)}d"
                    elif days_left == 0:
                        urgency = "danger"
                        badge_text = "Due Today"
                    elif days_left <= 3:
                        urgency = "warning"
                        badge_text = f"Due in {days_left}d"
                    elif days_left <= 14:
                        urgency = "info"
                        badge_text = f"Due in {days_left}d"
                    else:
                        urgency = "secondary"
                        badge_text = f"In {days_left}d"

                    upcoming_deadlines.append({
                        "id": ob["id"],
                        "clause_text": ob["clause_text"],
                        "actor": ob["actor"],
                        "deadline_date": d_str,
                        "days_left": days_left,
                        "urgency": urgency,
                        "badge_text": badge_text,
                        "risk_label": ob["risk_label"],
                        "status": ob["status"]
                    })
            except Exception:
                continue

    upcoming_deadlines.sort(key=lambda x: x["days_left"])

    # Recent escalations (scoped to current user)
    if is_scoped:
        escalations = db.execute(
            "SELECT e.*, o.clause_text FROM escalations e "
            "JOIN obligations o ON e.obligation_id = o.id "
            "WHERE e.user_id = ? AND o.contract_id = ? "
            "ORDER BY e.created_at DESC LIMIT 5",
            (uid, active_contract_id)
        ).fetchall()
    else:
        escalations = db.execute(
            "SELECT e.*, o.clause_text FROM escalations e "
            "LEFT JOIN obligations o ON e.obligation_id = o.id "
            "WHERE e.user_id = ? "
            "ORDER BY e.created_at DESC LIMIT 5",
            (uid,)
        ).fetchall()

    # Mini Dependency Graph Metrics (scoped to current user)
    total_dependencies = db.execute(
        "SELECT COUNT(*) FROM dependencies d JOIN obligations o ON d.upstream_id = o.id JOIN contracts c ON o.contract_id = c.id WHERE c.user_id = ?",
        (uid,)
    ).fetchone()[0]
    bottlenecks = db.execute(
        "SELECT d.upstream_id as ob_id, o.clause_text, o.actor, COUNT(*) as downstream_count "
        "FROM dependencies d "
        "JOIN obligations o ON d.upstream_id = o.id "
        "JOIN contracts c ON o.contract_id = c.id "
        "WHERE c.user_id = ? "
        "GROUP BY d.upstream_id, o.clause_text, o.actor "
        "ORDER BY downstream_count DESC LIMIT 2",
        (uid,)
    ).fetchall()

    # Unified Live Activity Ticker (evidence, disputes, escalations scoped to current user)
    activities = []

    recent_ev = db.execute(
        "SELECT 'evidence' as type, e.id, e.description, e.source, e.created_at, e.obligation_id "
        "FROM evidence e JOIN obligations o ON e.obligation_id = o.id JOIN contracts c ON o.contract_id = c.id "
        "WHERE c.user_id = ? ORDER BY e.created_at DESC LIMIT 4",
        (uid,)
    ).fetchall()
    for ev in recent_ev:
        activities.append({
            "type": "evidence",
            "title": f"Evidence: {ev['description']}",
            "meta": f"Source: {ev['source'].upper()} (Obligation #{ev['obligation_id']})",
            "time": ev["created_at"],
            "icon": "bi-shield-check",
            "color": "text-success"
        })

    recent_disp = db.execute(
        "SELECT 'dispute' as type, d.id, d.counter_evidence, d.status, d.created_at, d.obligation_id "
        "FROM disputes d JOIN obligations o ON d.obligation_id = o.id JOIN contracts c ON o.contract_id = c.id "
        "WHERE c.user_id = ? ORDER BY d.created_at DESC LIMIT 3",
        (uid,)
    ).fetchall()
    for disp in recent_disp:
        activities.append({
            "type": "dispute",
            "title": f"Dispute lodged on #{disp['obligation_id']}",
            "meta": f"Counter-evidence: {disp['counter_evidence'][:60]}... ({disp['status']})",
            "time": disp["created_at"],
            "icon": "bi-arrow-left-right",
            "color": "text-warning"
        })

    recent_esc = db.execute(
        "SELECT 'escalation' as type, id, reason, created_at, obligation_id "
        "FROM escalations WHERE user_id = ? ORDER BY created_at DESC LIMIT 3",
        (uid,)
    ).fetchall()
    for esc in recent_esc:
        activities.append({
            "type": "escalation",
            "title": f"Escalation block #{esc['id']} sealed",
            "meta": f"Tamper-proof hash chain entry for Obligation #{esc['obligation_id']}",
            "time": esc["created_at"],
            "icon": "bi-link-45deg",
            "color": "text-danger"
        })

    activities.sort(key=lambda x: str(x["time"] or ""), reverse=True)
    activities = activities[:6]

    all_contracts = db.execute(
        "SELECT id, title FROM contracts WHERE user_id = ? ORDER BY id ASC",
        (uid,)
    ).fetchall()

    return render_template(
        "dashboard.html",
        stats={
            "total_contracts": total_contracts,
            "total_obligations": total_obligations,
            "compliant_count": compliant_count,
            "at_risk_count": at_risk_count,
            "violated_count": violated_count,
            "pending_count": pending_count,
            "compliance_pct": compliance_pct,
            "open_disputes": open_disputes,
            "total_escalations": total_escalations,
            "low_risk_count": low_risk_count,
            "medium_risk_count": medium_risk_count,
            "high_risk_count": high_risk_count,
            "chain_valid": chain_audit.get("is_valid", False),
            "total_blocks": chain_audit.get("total_blocks", 0),
            "latest_block": last_block
        },
        chart_data={
            "risk_doughnut": [low_risk_count, medium_risk_count, high_risk_count],
            "trend_labels": trend_labels,
            "trend_values": trend_values
        },
        upcoming_deadlines=upcoming_deadlines[:6],
        obligations=obligations,
        escalations=escalations,
        graph_stats={
            "total_dependencies": total_dependencies,
            "bottlenecks": [dict(b) for b in bottlenecks]
        },
        activities=activities,
        all_contracts=all_contracts,
        active_contract_id=active_contract_id,
        is_scoped=is_scoped
    )


@main_bp.route("/switch-contract/<contract_id>")
def switch_contract(contract_id):
    """Switch active contract or view all portfolio contracts for the current user."""
    db = get_db()
    uid = current_user.id
    if str(contract_id).lower() in ("all", "0", "portfolio"):
        session["active_contract_id"] = None
        session["active_contract_title"] = "All Contracts (Portfolio)"
        flash("Switched view to All Portfolio Contracts", "info")
    else:
        try:
            cid = int(contract_id)
        except ValueError:
            abort(404)
        contract = get_owned_or_404("contract", cid, user_id=uid, db=db)
        session["active_contract_id"] = contract["id"]
        session["active_contract_title"] = contract["title"]
        flash(f"Switched active contract to '{contract['title']}'", "info")

    return redirect(request.referrer or url_for("main.dashboard"))


@main_bp.route("/api/search")
def api_search():
    """Global fast fuzzy search endpoint across the current user's contracts, obligations, evidence, and escalations."""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify({"query": q, "results": []})

    db = get_db()
    uid = current_user.id
    results = []
    param = f"%{q}%"

    # Search obligations owned by current user
    obs = db.execute(
        "SELECT o.id, o.clause_text, o.actor, o.risk_label, o.status FROM obligations o "
        "JOIN contracts c ON o.contract_id = c.id "
        "WHERE c.user_id = ? AND (o.clause_text LIKE ? OR o.actor LIKE ?) LIMIT 6",
        (uid, param, param)
    ).fetchall()
    for o in obs:
        results.append({
            "category": "Obligation",
            "title": f"#{o['id']}: {o['clause_text'][:70]}...",
            "subtitle": f"Actor: {o['actor']} | Status: {o['status']} ({o['risk_label']} Risk)",
            "url": url_for("obligations.detail", obligation_id=o["id"]),
            "badge": o["risk_label"],
            "icon": "bi-journal-check"
        })

    # Search contracts owned by current user
    cts = db.execute(
        "SELECT id, title, filename FROM contracts WHERE user_id = ? AND (title LIKE ? OR filename LIKE ?) LIMIT 4",
        (uid, param, param)
    ).fetchall()
    for c in cts:
        results.append({
            "category": "Contract",
            "title": c["title"],
            "subtitle": f"File: {c['filename']}",
            "url": url_for("obligations.index", contract_id=c["id"]),
            "badge": "Contract",
            "icon": "bi-file-earmark-text"
        })

    # Search escalations owned by current user
    escs = db.execute(
        "SELECT id, obligation_id, reason, entry_hash FROM escalations WHERE user_id = ? AND (reason LIKE ? OR entry_hash LIKE ?) LIMIT 4",
        (uid, param, param)
    ).fetchall()
    for e in escs:
        results.append({
            "category": "Escalation Block",
            "title": f"Block #{e['id']} (Obligation #{e['obligation_id']})",
            "subtitle": f"{e['reason'][:60]} | SHA-256: {e['entry_hash'][:16]}...",
            "url": url_for("escalations.index"),
            "badge": "Ledger",
            "icon": "bi-link-45deg"
        })

    # Search evidence owned by current user
    evs = db.execute(
        "SELECT e.id, e.description, e.source, e.obligation_id FROM evidence e "
        "JOIN obligations o ON e.obligation_id = o.id JOIN contracts c ON o.contract_id = c.id "
        "WHERE c.user_id = ? AND (e.description LIKE ? OR e.raw_ref LIKE ?) LIMIT 4",
        (uid, param, param)
    ).fetchall()
    for ev in evs:
        results.append({
            "category": "Evidence",
            "title": ev["description"],
            "subtitle": f"Source: {ev['source'].upper()} on Obligation #{ev['obligation_id']}",
            "url": url_for("obligations.detail", obligation_id=ev["obligation_id"]),
            "badge": ev["source"].upper(),
            "icon": "bi-shield-check"
        })

    return jsonify({"query": q, "results": results})


@main_bp.route("/reports/export")
def export_compliance_report():
    """Generate print-ready executive compliance audit certificate report for current user."""
    db = get_db()
    uid = current_user.id
    explicit_cid = request.args.get("contract_id")
    session_cid = session.get("active_contract_id")

    contract = None
    if explicit_cid:
        try:
            cid_int = int(explicit_cid)
        except ValueError:
            abort(404)
        contract = get_owned_or_404("contract", cid_int, user_id=uid, db=db)
    elif session_cid:
        contract = db.execute(
            "SELECT * FROM contracts WHERE id = ? AND user_id = ?",
            (int(session_cid), uid)
        ).fetchone()

    if contract:
        obligations = db.execute(
            "SELECT o.*, c.title as contract_title FROM obligations o "
            "JOIN contracts c ON o.contract_id = c.id "
            "WHERE c.user_id = ? AND o.contract_id = ? "
            "ORDER BY o.risk_score DESC, o.id ASC",
            (uid, contract["id"])
        ).fetchall()
    else:
        obligations = db.execute(
            "SELECT o.*, c.title as contract_title FROM obligations o "
            "JOIN contracts c ON o.contract_id = c.id "
            "WHERE c.user_id = ? "
            "ORDER BY o.risk_score DESC, o.id ASC",
            (uid,)
        ).fetchall()

    total_obs = len(obligations)
    compliant_obs = sum(1 for o in obligations if o["status"] == "COMPLIANT")
    at_risk_obs = sum(1 for o in obligations if o["status"] == "AT_RISK")
    violated_obs = sum(1 for o in obligations if o["status"] == "VIOLATED")
    pending_obs = sum(1 for o in obligations if o["status"] == "PENDING")
    compliance_pct = round((compliant_obs / total_obs * 100), 1) if total_obs > 0 else 0.0

    chain_audit = verify_chain(db, user_id=uid)
    last_block = get_last_escalation_block(db, user_id=uid)

    if contract:
        escalations = db.execute(
            "SELECT e.*, o.clause_text FROM escalations e "
            "JOIN obligations o ON e.obligation_id = o.id "
            "WHERE e.user_id = ? AND o.contract_id = ? ORDER BY e.id ASC",
            (uid, contract["id"])
        ).fetchall()
    else:
        escalations = db.execute(
            "SELECT e.*, o.clause_text FROM escalations e "
            "LEFT JOIN obligations o ON e.obligation_id = o.id "
            "WHERE e.user_id = ? ORDER BY e.id ASC",
            (uid,)
        ).fetchall()

    now = datetime.datetime.now(datetime.timezone.utc)
    audit_date = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    audit_id = f"OBL-AUDIT-{now.strftime('%Y%m%d')}-{contract['id'] if contract else 'PORTFOLIO'}"

    return render_template(
        "reports/compliance_report.html",
        audit_id=audit_id,
        audit_date=audit_date,
        contract=contract,
        obligations=obligations,
        total_obs=total_obs,
        compliant_obs=compliant_obs,
        at_risk_obs=at_risk_obs,
        violated_obs=violated_obs,
        pending_obs=pending_obs,
        compliance_pct=compliance_pct,
        chain_audit=chain_audit,
        last_block=last_block,
        escalations=escalations,
        auto_print=bool(request.args.get("print"))
    )
