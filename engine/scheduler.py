import os
import atexit
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from app.database import get_db_connection
from engine.calculator import compute_obligation_metrics, get_risk_thresholds

scheduler = None

def check_deadlines_and_dispute_windows(db_path: str):
    """
    Background periodic job:
    1. Recalculates deadline proximity and risk scores for active obligations per user.
    2. Checks open dispute windows; expires windows if deadline has passed and records
       to the owner user's isolated SHA-256 hash chain.
    """
    try:
        conn = get_db_connection(db_path)
        now = datetime.now(timezone.utc)

        obligations = conn.execute(
            """
            SELECT o.id, o.dispute_deadline, o.risk_score, o.status, COALESCE(c.user_id, 1) AS user_id
            FROM obligations o
            LEFT JOIN contracts c ON o.contract_id = c.id
            """
        ).fetchall()

        user_thresholds = {}
        for ob in obligations:
            ob_id = ob["id"]
            uid = int(ob["user_id"] or 1)
            if uid not in user_thresholds:
                user_thresholds[uid] = get_risk_thresholds(user_id=uid, db=conn)[0]
            thresh_high = user_thresholds[uid]
            dispute_dl = ob["dispute_deadline"]

            # Check if dispute window has expired
            if dispute_dl and dispute_dl != "EXPIRED":
                clean_dl = dispute_dl.replace("Z", "+00:00")
                try:
                    dl_dt = datetime.fromisoformat(clean_dl)
                    if dl_dt.tzinfo is None:
                        dl_dt = dl_dt.replace(tzinfo=timezone.utc)

                    if now > dl_dt:
                        # Window has expired!
                        open_dispute = conn.execute(
                            "SELECT id FROM disputes WHERE obligation_id = ? AND status = 'open'",
                            (ob_id,)
                        ).fetchone()

                        if open_dispute:
                            conn.execute(
                                "UPDATE disputes SET status = 'expired' WHERE id = ?",
                                (open_dispute["id"],)
                            )

                        # If still high risk or violated, confirm violation state and lock dispute deadline to EXPIRED
                        if ob["risk_score"] >= thresh_high or ob["status"] == "VIOLATED":
                            conn.execute(
                                "UPDATE obligations SET status = 'VIOLATED', dispute_deadline = 'EXPIRED' WHERE id = ?",
                                (ob_id,)
                            )
                            # Append to owner's SHA-256 escalation blockchain ledger
                            try:
                                from engine.hash_chain import add_escalation_entry
                                reason_msg = f"Dispute window expired without mitigation (Risk Score: {ob['risk_score']})"
                                add_escalation_entry(
                                    obligation_id=ob_id,
                                    reason=reason_msg,
                                    risk_score=ob["risk_score"],
                                    db=conn,
                                    user_id=uid
                                )
                            except Exception:
                                pass
                        else:
                            conn.execute(
                                "UPDATE obligations SET dispute_deadline = NULL WHERE id = ?",
                                (ob_id,)
                            )
                        conn.commit()
                except Exception:
                    pass

            # Periodically refresh metrics calculation for this user's obligation
            compute_obligation_metrics(ob_id, db=conn, user_id=uid)

        conn.close()
    except Exception:
        # Background worker should not crash the main thread
        pass

def start_scheduler(app):
    """Initialize and start the background scheduler (guarded against duplicate Gunicorn workers)."""
    global scheduler

    # In development mode, Werkzeug runs two processes: parent and reload worker.
    # Only start scheduler in the reload worker or non-debug mode.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    # Guard against duplicate scheduler threads if multiple workers are launched
    if os.environ.get("DISABLE_SCHEDULER", "false").lower() == "true":
        return

    if scheduler is None:
        db_path = app.config.get("DATABASE_PATH")
        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            func=check_deadlines_and_dispute_windows,
            args=[db_path],
            trigger="interval",
            seconds=45,
            id="obligraph_deadline_checker",
            replace_existing=True
        )
        scheduler.start()
        atexit.register(stop_scheduler)

def stop_scheduler():
    """Cleanly shut down background scheduler."""
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        scheduler = None
