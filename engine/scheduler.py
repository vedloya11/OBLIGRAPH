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
    1. Recalculates deadline proximity and risk scores for active obligations.
    2. Checks open dispute windows; expires windows if deadline has passed.
    """
    try:
        conn = get_db_connection(db_path)
        thresh_high, _, _ = get_risk_thresholds()
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        obligations = conn.execute("SELECT id, dispute_deadline, risk_score, status FROM obligations").fetchall()
        
        for ob in obligations:
            ob_id = ob["id"]
            dispute_dl = ob["dispute_deadline"]

            # Check if dispute window has expired
            if dispute_dl:
                clean_dl = dispute_dl.replace("Z", "+00:00")
                try:
                    dl_dt = datetime.fromisoformat(clean_dl)
                    if dl_dt.tzinfo is None:
                        dl_dt = dl_dt.replace(tzinfo=timezone.utc)

                    if now > dl_dt:
                        # Window has expired!
                        # Check open disputes for this obligation
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
                            # Append to SHA-256 escalation blockchain ledger
                            try:
                                from engine.hash_chain import add_escalation_entry
                                reason_msg = f"Dispute window expired without mitigation (Risk Score: {ob['risk_score']})"
                                add_escalation_entry(
                                    obligation_id=ob_id,
                                    reason=reason_msg,
                                    risk_score=ob["risk_score"],
                                    db=conn
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

            # Periodically refresh metrics calculation
            compute_obligation_metrics(ob_id, db=conn)

        conn.close()
    except Exception as e:
        # Background worker should not crash the main thread
        pass

def start_scheduler(app):
    """Initialize and start the background scheduler."""
    global scheduler

    # In development mode, Werkzeug runs two processes: parent and reload worker.
    # Only start scheduler in the reload worker or non-debug mode.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
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
