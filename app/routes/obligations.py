from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify
from app.database import get_db

obligations_bp = Blueprint("obligations", __name__, url_prefix="/obligations")

def seed_default_dependencies_if_needed(contract_id: int):
    """Seed 2-4 realistic dependency links for a contract if none exist yet."""
    db = get_db()
    existing_count = db.execute("""
        SELECT COUNT(*) FROM dependencies d
        JOIN obligations o ON d.upstream_id = o.id
        WHERE o.contract_id = ?
    """, (contract_id,)).fetchone()[0]

    if existing_count == 0:
        obligations = db.execute(
            "SELECT id, clause_text FROM obligations WHERE contract_id = ? ORDER BY id ASC",
            (contract_id,)
        ).fetchall()

        if len(obligations) >= 5:
            # Map by keywords
            notice_id = None
            price_id = None
            purity_id = None
            coa_id = None
            delivery_id = None
            payment_id = None

            for o in obligations:
                txt = o["clause_text"].lower()
                if "30-day" in txt or ("price" in txt and "notice" in txt):
                    notice_id = o["id"]
                elif "price index" in txt or ("index" in txt and "cap" in txt):
                    price_id = o["id"]
                elif "purity" in txt or "quality" in txt:
                    purity_id = o["id"]
                elif "coa" in txt or "certificate" in txt or "assay" in txt:
                    coa_id = o["id"]
                elif "volume" in txt or "5,000" in txt or "delivery" in txt:
                    delivery_id = o["id"]
                elif "payment" in txt or "net 30" in txt:
                    payment_id = o["id"]

            links_to_add = []
            if notice_id and price_id:
                links_to_add.append((notice_id, price_id, "Prerequisite: 30-day notice required before price adjustment"))
            if purity_id and coa_id:
                links_to_add.append((purity_id, coa_id, "Condition: COA assay validates material purity compliance"))
            if delivery_id and payment_id:
                links_to_add.append((delivery_id, payment_id, "Trigger: Verified port delivery required to unlock payment invoice"))
            if coa_id and payment_id:
                links_to_add.append((coa_id, payment_id, "Approval: Quality release prerequisite for invoice settlement"))

            # Fallback if specific keywords were not all present: link first few sequentially
            if not links_to_add and len(obligations) >= 4:
                links_to_add.append((obligations[0]["id"], obligations[1]["id"], "Operational Prerequisite"))
                links_to_add.append((obligations[2]["id"], obligations[3]["id"], "Verification Condition"))

            for up_id, down_id, note in links_to_add:
                db.execute(
                    "INSERT OR IGNORE INTO dependencies (upstream_id, downstream_id, note) VALUES (?, ?, ?)",
                    (up_id, down_id, note)
                )
            db.commit()

@obligations_bp.route("/")
def index():
    db = get_db()
    
    # Query parameters for filtering and sorting
    search = request.args.get("search", "").strip()
    actor_filter = request.args.get("actor", "").strip()
    status_filter = request.args.get("status", "").strip()
    risk_filter = request.args.get("risk", "").strip()
    sort_by = request.args.get("sort", "id_asc").strip()
    contract_filter = request.args.get("contract_id", "").strip()

    query = """
        SELECT o.*, c.title as contract_title 
        FROM obligations o 
        LEFT JOIN contracts c ON o.contract_id = c.id 
        WHERE 1=1
    """
    params = []

    if search:
        query += " AND (o.clause_text LIKE ? OR o.actor LIKE ? OR o.expected_state LIKE ? OR o.required_evidence LIKE ?)"
        wildcard = f"%{search}%"
        params.extend([wildcard, wildcard, wildcard, wildcard])

    if actor_filter:
        query += " AND o.actor = ?"
        params.append(actor_filter)

    if status_filter:
        query += " AND o.status = ?"
        params.append(status_filter)

    if risk_filter:
        query += " AND o.risk_label = ?"
        params.append(risk_filter)

    if contract_filter:
        query += " AND o.contract_id = ?"
        params.append(contract_filter)

    # Sorting
    if sort_by == "risk_desc":
        query += " ORDER BY o.risk_score DESC, o.id ASC"
    elif sort_by == "risk_asc":
        query += " ORDER BY o.risk_score ASC, o.id ASC"
    elif sort_by == "confidence_desc":
        query += " ORDER BY o.confidence DESC"
    elif sort_by == "deadline_asc":
        query += " ORDER BY o.deadline_date ASC NULLS LAST, o.id ASC"
    elif sort_by == "id_desc":
        query += " ORDER BY o.id DESC"
    else:
        query += " ORDER BY o.id ASC"

    obligations = db.execute(query, params).fetchall()

    # Get distinct actors and contracts for filter dropdowns
    actors = [r[0] for r in db.execute("SELECT DISTINCT actor FROM obligations WHERE actor != '' ORDER BY actor ASC").fetchall()]
    contracts = db.execute("SELECT id, title FROM contracts ORDER BY id DESC").fetchall()

    # Seed dependencies for active contracts if needed
    for c in contracts:
        seed_default_dependencies_if_needed(c["id"])

    # Compute quick summary stats
    total = len(obligations)
    compliant = sum(1 for o in obligations if o["status"] == "COMPLIANT")
    at_risk = sum(1 for o in obligations if o["status"] == "AT_RISK")
    violated = sum(1 for o in obligations if o["status"] == "VIOLATED")

    return render_template(
        "obligations/index.html",
        obligations=obligations,
        actors=actors,
        contracts=contracts,
        selected_search=search,
        selected_actor=actor_filter,
        selected_status=status_filter,
        selected_risk=risk_filter,
        selected_sort=sort_by,
        selected_contract=contract_filter,
        stats={"total": total, "compliant": compliant, "at_risk": at_risk, "violated": violated}
    )

@obligations_bp.route("/<int:obligation_id>")
def detail(obligation_id):
    db = get_db()
    obligation = db.execute("""
        SELECT o.*, c.title as contract_title 
        FROM obligations o 
        LEFT JOIN contracts c ON o.contract_id = c.id 
        WHERE o.id = ?
    """, (obligation_id,)).fetchone()

    if not obligation:
        flash("Obligation record not found.", "warning")
        return redirect(url_for("obligations.index"))

    # Seed dependencies if needed
    seed_default_dependencies_if_needed(obligation["contract_id"])

    # Fetch Upstream dependencies (obligations that this obligation depends ON)
    upstream = db.execute("""
        SELECT d.id as dep_id, d.note, o.* 
        FROM dependencies d 
        JOIN obligations o ON d.upstream_id = o.id 
        WHERE d.downstream_id = ?
        ORDER BY o.id ASC
    """, (obligation_id,)).fetchall()

    # Fetch Downstream dependencies (obligations that depend ON this obligation)
    downstream = db.execute("""
        SELECT d.id as dep_id, d.note, o.* 
        FROM dependencies d 
        JOIN obligations o ON d.downstream_id = o.id 
        WHERE d.upstream_id = ?
        ORDER BY o.id ASC
    """, (obligation_id,)).fetchall()

    # Fetch all other obligations in this contract for the "Add Dependency" dropdown
    sibling_obligations = db.execute("""
        SELECT id, clause_text, actor, risk_label 
        FROM obligations 
        WHERE contract_id = ? AND id != ?
        ORDER BY id ASC
    """, (obligation["contract_id"], obligation_id)).fetchall()

    # Fetch related evidence
    evidence = db.execute("""
        SELECT * FROM evidence 
        WHERE obligation_id = ? 
        ORDER BY created_at DESC
    """, (obligation_id,)).fetchall()

    # Fetch related disputes
    disputes = db.execute("""
        SELECT * FROM disputes 
        WHERE obligation_id = ? 
        ORDER BY created_at DESC
    """, (obligation_id,)).fetchall()

    return render_template(
        "obligations/detail.html",
        obligation=obligation,
        upstream=upstream,
        downstream=downstream,
        sibling_obligations=sibling_obligations,
        evidence=evidence,
        disputes=disputes
    )

@obligations_bp.route("/graph")
def graph():
    db = get_db()
    contracts = db.execute("SELECT id, title FROM contracts ORDER BY id DESC").fetchall()
    active_contract_id = request.args.get("contract_id")
    if not active_contract_id and contracts:
        active_contract_id = contracts[0]["id"]
    
    if active_contract_id:
        seed_default_dependencies_if_needed(int(active_contract_id))

    all_obligations = db.execute(
        "SELECT id, clause_text, actor FROM obligations ORDER BY id ASC"
    ).fetchall()

    return render_template(
        "obligations/graph.html",
        contracts=contracts,
        active_contract_id=int(active_contract_id) if active_contract_id else None,
        all_obligations=all_obligations
    )

@obligations_bp.route("/api/graph-data")
def graph_data():
    """API endpoint providing nodes and edges JSON formatted for vis-network."""
    db = get_db()
    contract_id = request.args.get("contract_id")

    query = "SELECT * FROM obligations"
    params = []

    if contract_id:
        query += " WHERE contract_id = ?"
        params.append(contract_id)
        seed_default_dependencies_if_needed(int(contract_id))
    else:
        contracts = db.execute("SELECT id FROM contracts").fetchall()
        for c in contracts:
            seed_default_dependencies_if_needed(c["id"])

    obligations = db.execute(query, params).fetchall()

    # Color scheme adhering to design direction
    color_map = {
        "LOW": {"background": "rgba(16, 185, 129, 0.9)", "border": "#059669", "highlight": "#34d399"},
        "MEDIUM": {"background": "rgba(245, 158, 11, 0.9)", "border": "#d97706", "highlight": "#fbbf24"},
        "HIGH": {"background": "rgba(239, 68, 68, 0.9)", "border": "#b91c1c", "highlight": "#f87171"}
    }

    nodes = []
    node_ids = set()
    for o in obligations:
        risk = (o["risk_label"] or "LOW").upper()
        colors = color_map.get(risk, color_map["LOW"])
        
        # Clean label with clause summary
        snippet = (o["clause_text"] or "")[:45].strip()
        label = f"#{o['id']}: {snippet}...\n[{o['actor']}] • {risk} ({round(o['risk_score'] or 0, 1)})\nConf: {round(o['confidence'] or 0)}%"

        nodes.append({
            "id": o["id"],
            "label": label,
            "title": f"<b>#{o['id']} {o['actor']}</b><br>{o['clause_text']}<br><b>Expected:</b> {o['expected_state']}<br><b>Risk:</b> {risk} ({o['risk_score']})",
            "color": colors,
            "font": {"color": "#ffffff", "face": "Inter", "size": 11, "bold": True},
            "shape": "box",
            "margin": 10,
            "shadow": {"enabled": True, "color": "rgba(0,0,0,0.4)", "size": 6}
        })
        node_ids.add(o["id"])

    # Fetch dependencies
    dep_query = "SELECT d.id, d.upstream_id, d.downstream_id, d.note FROM dependencies d"
    dep_params = []
    if contract_id:
        dep_query += """
            JOIN obligations o1 ON d.upstream_id = o1.id 
            WHERE o1.contract_id = ?
        """
        dep_params.append(contract_id)

    deps = db.execute(dep_query, dep_params).fetchall()

    edges = []
    for d in deps:
        if d["upstream_id"] in node_ids and d["downstream_id"] in node_ids:
            edges.append({
                "id": d["id"],
                "from": d["upstream_id"],
                "to": d["downstream_id"],
                "label": d["note"] or "Prerequisite",
                "arrows": "to",
                "color": {"color": "#60a5fa", "highlight": "#93c5fd"},
                "font": {"color": "#94a3b8", "size": 10, "align": "middle", "strokeWidth": 2, "strokeColor": "#0f172a"},
                "smooth": {"type": "cubicBezier", "roundness": 0.3}
            })

    return jsonify({"nodes": nodes, "edges": edges})

@obligations_bp.route("/dependencies/add", methods=["POST"])
def add_dependency():
    upstream_id = request.form.get("upstream_id")
    downstream_id = request.form.get("downstream_id")
    note = request.form.get("note", "").strip() or "Prerequisite Dependency"

    if not upstream_id or not downstream_id:
        flash("Both upstream and downstream obligations must be selected.", "warning")
        return redirect(request.referrer or url_for("obligations.graph"))

    if upstream_id == downstream_id:
        flash("An obligation cannot depend on itself.", "danger")
        return redirect(request.referrer or url_for("obligations.graph"))

    db = get_db()
    try:
        db.execute(
            "INSERT INTO dependencies (upstream_id, downstream_id, note) VALUES (?, ?, ?)",
            (upstream_id, downstream_id, note)
        )
        db.commit()
        flash("Dependency link created successfully.", "success")
    except Exception as e:
        flash(f"Could not create dependency link: {str(e)}", "danger")

    return redirect(request.referrer or url_for("obligations.graph"))

@obligations_bp.route("/dependencies/delete/<int:dep_id>", methods=["POST"])
def delete_dependency(dep_id):
    db = get_db()
    db.execute("DELETE FROM dependencies WHERE id = ?", (dep_id,))
    db.commit()
    flash("Dependency link removed.", "info")
    return redirect(request.referrer or url_for("obligations.graph"))

@obligations_bp.route("/api/explain/<int:obligation_id>")
def api_explain(obligation_id):
    from engine.calculator import explain_score
    db = get_db()
    data = explain_score(obligation_id, db=db)
    if not data:
        return jsonify({"error": "Obligation not found"}), 404
    return jsonify(data)
