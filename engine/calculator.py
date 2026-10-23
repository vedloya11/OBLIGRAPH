from datetime import datetime, timezone, timedelta, date
from typing import Dict, Any, Optional, Tuple
from config import Config
from engine.config_store import get_setting

def get_trust_weights() -> Dict[str, float]:
    """Retrieve current configured trust weights."""
    return {
        "self_reported": float(get_setting("weight_self_reported", Config.WEIGHT_SELF_REPORTED)),
        "counterparty_confirmed": float(get_setting("weight_counterparty_confirmed", Config.WEIGHT_COUNTERPARTY_CONFIRMED)),
        "independently_verifiable": float(get_setting("weight_independently_verifiable", Config.WEIGHT_INDEPENDENTLY_VERIFIABLE))
    }

def get_risk_weights() -> Tuple[float, float, float]:
    """Retrieve current configured risk equation weights (w1, w2, w3)."""
    w1 = float(get_setting("risk_weight_deviation", Config.RISK_WEIGHT_DEVIATION))
    w2 = float(get_setting("risk_weight_untrusted", Config.RISK_WEIGHT_UNTRUSTED))
    w3 = float(get_setting("risk_weight_deadline", Config.RISK_WEIGHT_DEADLINE))
    total = w1 + w2 + w3
    if total > 0:
        return (w1 / total, w2 / total, w3 / total)
    return (0.45, 0.25, 0.30)

def get_risk_thresholds() -> Tuple[float, float, int]:
    """Retrieve HIGH risk cutoff, MEDIUM risk cutoff, and dispute window hours."""
    high = float(get_setting("risk_threshold_high", Config.RISK_THRESHOLD_HIGH))
    medium = float(get_setting("risk_threshold_medium", Config.RISK_THRESHOLD_MEDIUM))
    dispute_hours = int(get_setting("dispute_window_hours", Config.DISPUTE_WINDOW_HOURS))
    return high, medium, dispute_hours

def calculate_deadline_proximity(deadline_str: Optional[str]) -> float:
    """
    Calculate deadline proximity score from 0.0 to 100.0.
    Rises as remaining days decrease; reaches 100.0 when overdue.
    """
    if not deadline_str:
        return 20.0  # Default nominal proximity for conditional/on-demand obligations

    try:
        # Accept YYYY-MM-DD or ISO formats
        clean_date_str = deadline_str.split("T")[0].strip()
        deadline = datetime.strptime(clean_date_str, "%Y-%m-%d").date()
        today = date.today()
        days_left = (deadline - today).days

        if days_left <= 0:
            return 100.0  # Overdue
        elif days_left >= 45:
            return 5.0    # Far in the future
        else:
            # Exponential decay: proximity increases sharply within 14 days
            # Formula maps [1..45] days to [100..5]
            normalized = max(0.0, min(1.0, (45 - days_left) / 44))
            return round(5.0 + (95.0 * (normalized ** 1.35)), 1)
    except Exception:
        return 25.0

def compute_obligation_metrics(obligation_id: int, db=None) -> Dict[str, Any]:
    """
    Compute trust-weighted confidence, deterministic risk score, label,
    and compliance status for a specific obligation record.
    Persists updated metrics back to the obligations table.
    """
    should_close = False
    if db is None:
        from app.database import get_db
        db = get_db()

    # 1. Fetch obligation details
    ob = db.execute("SELECT * FROM obligations WHERE id = ?", (obligation_id,)).fetchone()
    if not ob:
        return {}

    # 2. Fetch associated evidence
    evidence_rows = db.execute(
        "SELECT * FROM evidence WHERE obligation_id = ? ORDER BY created_at ASC",
        (obligation_id,)
    ).fetchall()

    trust_weights_cfg = get_trust_weights()
    w1, w2, w3 = get_risk_weights()
    thresh_high, thresh_medium, dispute_hours = get_risk_thresholds()

    deadline_proximity = calculate_deadline_proximity(ob["deadline_date"])

    # 3. Case A: No evidence yet
    if not evidence_rows:
        confidence = 0.0
        deviation_score = 30.0  # Default unverified deviation penalty
        trust_deficit_score = 100.0  # No evidence = 100% untrusted

        risk_score = round(
            (w1 * deviation_score) + (w2 * trust_deficit_score) + (w3 * deadline_proximity),
            1
        )
        risk_score = max(0.0, min(100.0, risk_score))

        if risk_score >= thresh_high:
            risk_label = "HIGH"
        elif risk_score >= thresh_medium:
            risk_label = "MEDIUM"
        else:
            risk_label = "LOW"

        status = "PENDING"
        dispute_deadline = ob["dispute_deadline"]

    # 4. Case B: Evidence is present
    else:
        weighted_agreements = []
        total_trust_weight = 0.0
        target_val = ob["target_value"]

        for ev in evidence_rows:
            tw = ev["trust_weight"]
            if tw is None or tw <= 0:
                tw = trust_weights_cfg.get(ev["trust_level"], 0.4)

            obs_val = ev["observed_value"]

            # Check for explicit failure keywords in description
            desc_lower = (ev["description"] or "").lower()
            is_explicit_failure = any(bad in desc_lower for bad in ["failed", "breach", "reject", "violation", "unpermitted"])

            # Compute agreement ratio (0.0 to 1.0)
            if is_explicit_failure:
                agreement = 0.0
            elif target_val is not None and obs_val is not None:
                if obs_val >= target_val:
                    agreement = 1.0
                else:
                    # If unit is percentage (e.g. chemical purity 98.5%)
                    is_pct = (ob["unit"] or "").strip() == "%" or target_val > 90.0
                    if is_pct:
                        # For chemical purity, shortfall of 0.5% or more is severe
                        gap = target_val - obs_val
                        agreement = max(0.0, 1.0 - (gap / 1.5))
                    else:
                        agreement = max(0.0, min(1.0, obs_val / target_val))
            else:
                if any(warn in desc_lower for warn in ["delay", "partial", "marginal", "dispute", "pending"]):
                    agreement = 0.5
                else:
                    agreement = 1.0

            weighted_agreements.append(agreement * tw)
            total_trust_weight += tw

        # Confidence: Trust-weighted aggregate agreement percentage (0 to 100%)
        if total_trust_weight > 0:
            confidence = round((sum(weighted_agreements) / total_trust_weight) * 100.0, 1)
            avg_trust_weight = total_trust_weight / len(evidence_rows)
        else:
            confidence = 0.0
            avg_trust_weight = 0.4

        # Deviation component (0 to 100)
        deviation_score = round(100.0 - confidence, 1)

        # Trust deficit component (0 to 100) = (1 - trust) * 100
        trust_deficit_score = round(max(0.0, (1.0 - avg_trust_weight) * 100.0), 1)

        # Total Risk Score (0 to 100)
        risk_score = round(
            (w1 * deviation_score) + (w2 * trust_deficit_score) + (w3 * deadline_proximity),
            1
        )
        risk_score = max(0.0, min(100.0, risk_score))

        # Check for any shortfall in evidence
        has_any_shortfall = any(
            (ev["observed_value"] is not None and ob["target_value"] is not None and ev["observed_value"] < ob["target_value"]) or
            any(bad in (ev["description"] or "").lower() for bad in ["failed", "breach", "reject", "violation"])
            for ev in evidence_rows
        )

        has_severe_breach = any(
            any(bad in (ev["description"] or "").lower() for bad in ["failed", "breach", "reject", "violation"]) or
            (ev["observed_value"] is not None and ob["target_value"] is not None and (ob["target_value"] - ev["observed_value"] > 1.0 if (ob["unit"] or "") == "%" else ev["observed_value"] / ob["target_value"] < 0.90))
            for ev in evidence_rows
        )

        # Risk Label
        if risk_score >= thresh_high or has_severe_breach:
            risk_label = "HIGH"
            if risk_score < thresh_high:
                risk_score = max(risk_score, 72.0)
        elif risk_score >= thresh_medium or has_any_shortfall:
            risk_label = "MEDIUM"
        else:
            risk_label = "LOW"

        # Compliance Status
        if has_severe_breach or confidence < 60.0 or risk_score >= thresh_high:
            status = "VIOLATED"
        elif has_any_shortfall or risk_score >= thresh_medium or confidence < 85.0:
            status = "AT_RISK"
        else:
            status = "COMPLIANT"

        # Check dispute window trigger:
        # If obligation crosses risk threshold high or is violated, open dispute window if not already active or expired
        dispute_deadline = ob["dispute_deadline"]
        if (risk_score >= thresh_high or status == "VIOLATED"):
            if not dispute_deadline:
                expires_at = datetime.now(timezone.utc) + timedelta(hours=dispute_hours)
                dispute_deadline = expires_at.isoformat()
            # If already EXPIRED or has active timestamp, preserve it
        else:
            if dispute_deadline and dispute_deadline != "EXPIRED":
                # Risk dropped below threshold and not violated, clear dispute window
                dispute_deadline = None

    # Update database record
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("""
        UPDATE obligations 
        SET status = ?, confidence = ?, risk_score = ?, risk_label = ?, 
            dispute_deadline = ?, updated_at = ?
        WHERE id = ?
    """, (status, confidence, risk_score, risk_label, dispute_deadline, now_str, obligation_id))
    db.commit()

    return {
        "obligation_id": obligation_id,
        "status": status,
        "confidence": confidence,
        "risk_score": risk_score,
        "risk_label": risk_label,
        "deviation_score": deviation_score,
        "trust_deficit_score": trust_deficit_score,
        "deadline_proximity": deadline_proximity,
        "dispute_deadline": dispute_deadline
    }

def recompute_all_obligations(contract_id: Optional[int] = None, db=None) -> int:
    """Recompute compliance metrics and risk for all obligations."""
    if db is None:
        from app.database import get_db
        db = get_db()

    query = "SELECT id FROM obligations"
    params = []
    if contract_id:
        query += " WHERE contract_id = ?"
        params.append(contract_id)

    rows = db.execute(query, params).fetchall()
    for r in rows:
        compute_obligation_metrics(r["id"], db=db)

    return len(rows)

def explain_score(obligation_id: int, db=None) -> Dict[str, Any]:
    """
    Generate an explainability breakdown of the three risk score components:
    1. Deviation from Target (w1 = 45%)
    2. Evidence Trust Deficit (w2 = 25%)
    3. Deadline Proximity (w3 = 30%)
    """
    if db is None:
        from app.database import get_db
        db = get_db()

    ob = db.execute("""
        SELECT o.*, c.title as contract_title 
        FROM obligations o 
        LEFT JOIN contracts c ON o.contract_id = c.id 
        WHERE o.id = ?
    """, (obligation_id,)).fetchone()

    if not ob:
        return {}

    metrics = compute_obligation_metrics(obligation_id, db=db)
    w1, w2, w3 = get_risk_weights()
    thresh_high, thresh_medium, dispute_hours = get_risk_thresholds()

    evidence_rows = db.execute(
        "SELECT * FROM evidence WHERE obligation_id = ? ORDER BY created_at ASC",
        (obligation_id,)
    ).fetchall()

    dev_score = metrics.get("deviation_score", 0.0)
    trust_deficit_score = metrics.get("trust_deficit_score", 0.0)
    proximity_score = metrics.get("deadline_proximity", 0.0)

    # Compute days remaining
    days_left = None
    if ob["deadline_date"]:
        try:
            d = datetime.strptime(ob["deadline_date"].split("T")[0], "%Y-%m-%d").date()
            days_left = (d - date.today()).days
        except Exception:
            pass

    # Dispute window remaining hours
    dispute_hours_remaining = None
    if ob["dispute_deadline"] and ob["dispute_deadline"] not in ["EXPIRED", "RESOLVED"]:
        try:
            clean_str = ob["dispute_deadline"].replace("Z", "+00:00")
            dl = datetime.fromisoformat(clean_str)
            now = datetime.now(timezone.utc)
            diff = (dl - now).total_seconds() / 3600.0
            dispute_hours_remaining = round(max(0.0, diff), 1)
        except Exception:
            pass

    # Contributions
    c1 = round(w1 * dev_score, 1)
    c2 = round(w2 * trust_deficit_score, 1)
    c3 = round(w3 * proximity_score, 1)

    return {
        "obligation_id": ob["id"],
        "clause_text": ob["clause_text"],
        "actor": ob["actor"],
        "expected_state": ob["expected_state"],
        "target_value": ob["target_value"],
        "unit": ob["unit"],
        "deadline_date": ob["deadline_date"],
        "days_left": days_left,
        "status": ob["status"],
        "confidence": ob["confidence"],
        "total_risk_score": ob["risk_score"],
        "risk_label": ob["risk_label"],
        "dispute_deadline": ob["dispute_deadline"],
        "dispute_hours_remaining": dispute_hours_remaining,
        "thresholds": {
            "high": thresh_high,
            "medium": thresh_medium,
            "dispute_window_hours": dispute_hours
        },
        "breakdown": {
            "w1_weight_pct": round(w1 * 100),
            "w1_deviation_score": dev_score,
            "w1_contribution": c1,
            
            "w2_weight_pct": round(w2 * 100),
            "w2_trust_deficit_score": trust_deficit_score,
            "w2_contribution": c2,
            
            "w3_weight_pct": round(w3 * 100),
            "w3_proximity_score": proximity_score,
            "w3_contribution": c3,
            
            "formula_string": f"({round(w1,2)} × {dev_score}) + ({round(w2,2)} × {trust_deficit_score}) + ({round(w3,2)} × {proximity_score}) = {ob['risk_score']}"
        },
        "evidence_count": len(evidence_rows)
    }
