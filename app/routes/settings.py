from flask import Blueprint, render_template, request, flash, redirect, url_for, session
from flask_login import current_user
from engine.config_store import get_all_settings, set_setting
from engine.calculator import recompute_all_obligations

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


@settings_bp.route("/", methods=["GET", "POST"])
def index():
    uid = current_user.id
    if request.method == "POST":
        for key in [
            "risk_threshold_high", "risk_threshold_medium", "dispute_window_hours",
            "weight_self_reported", "weight_counterparty_confirmed", "weight_independently_verifiable",
            "risk_weight_deviation", "risk_weight_untrusted", "risk_weight_deadline",
            "google_sheet_csv_url"
        ]:
            if key in request.form:
                set_setting(key, request.form[key].strip(), user_id=uid)

        # Trigger recomputation across only this user's obligations with updated parameters
        recompute_all_obligations(user_id=uid)

        flash("Engine parameters updated successfully and obligations recomputed.", "success")
        return redirect(url_for("settings.index"))

    settings = get_all_settings(user_id=uid)
    return render_template("settings/index.html", settings=settings)


@settings_bp.route("/reset-demo", methods=["POST"])
def reset_demo():
    """One-click Reset & Load Demo environment for the current user's account only."""
    from seed.load_demo import load_demo_data
    try:
        res = load_demo_data(user_id=current_user.id)
        session["active_contract_id"] = res["contract_id"]
        session["active_contract_title"] = res["contract_title"]
        flash(
            f"Demo environment reset successfully! Loaded '{res['contract_title']}' with 10 obligations, evidence feeds, active dispute, and verified ledger.",
            "success"
        )
    except Exception as e:
        flash(f"Failed to reset demo: {e}", "danger")

    return redirect(url_for("main.dashboard"))
