import os
from flask import Flask, render_template, session, request, redirect, url_for, flash
from flask_login import current_user
from config import Config
from app.database import close_db, init_db, get_db
from app.models import login_manager, csrf, limiter

PUBLIC_ENDPOINTS = {
    "main.landing",
    "main.healthz",
    "auth.login",
    "auth.signup",
    "auth.logout",
    "static",
}


def create_app(config_class=Config):
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object(config_class)

    # Ensure uploads and database folders exist
    os.makedirs(app.config.get("UPLOAD_FOLDER", "uploads"), exist_ok=True)
    db_target = app.config.get("DATABASE_URL") or app.config.get("DATABASE_PATH")
    init_db(db_target)

    # Initialize security & auth extensions
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    # Database teardown
    app.teardown_appcontext(close_db)

    # Register Blueprints
    from app.routes.auth import auth_bp
    from app.routes.main import main_bp
    from app.routes.contracts import contracts_bp
    from app.routes.obligations import obligations_bp
    from app.routes.evidence import evidence_bp
    from app.routes.disputes import disputes_bp
    from app.routes.escalations import escalations_bp
    from app.routes.query import query_bp
    from app.routes.settings import settings_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(contracts_bp)
    app.register_blueprint(obligations_bp)
    app.register_blueprint(evidence_bp)
    app.register_blueprint(disputes_bp)
    app.register_blueprint(escalations_bp)
    app.register_blueprint(query_bp)
    app.register_blueprint(settings_bp)

    @app.before_request
    def enforce_authentication():
        endpoint = request.endpoint
        if endpoint is None:
            return None
        if endpoint in PUBLIC_ENDPOINTS or endpoint.startswith("static"):
            return None
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.path))
        return None

    # Global template context processor (scoped strictly to current_user)
    @app.context_processor
    def inject_global_context():
        if not current_user.is_authenticated:
            return {
                "active_contract": None,
                "all_contracts": [],
                "notification_count": 0,
                "escalation_count": 0,
                "open_dispute_count": 0,
            }
        try:
            db = get_db()
            uid = int(current_user.id)
            contracts = db.execute(
                "SELECT id, title FROM contracts WHERE user_id = ? ORDER BY id DESC",
                (uid,)
            ).fetchall()
            active_id = session.get("active_contract_id")
            active_contract = None
            if active_id:
                active_contract = db.execute(
                    "SELECT id, title FROM contracts WHERE id = ? AND user_id = ?",
                    (active_id, uid)
                ).fetchone()
                if not active_contract:
                    session.pop("active_contract_id", None)
                    session.pop("active_contract_title", None)
            if not active_contract and contracts and "active_contract_id" not in session:
                active_contract = contracts[0]
                session["active_contract_id"] = active_contract["id"]
                session["active_contract_title"] = active_contract["title"]

            escalation_count = db.execute(
                "SELECT COUNT(*) FROM escalations WHERE user_id = ?",
                (uid,)
            ).fetchone()[0]
            open_dispute_count = db.execute("""
                SELECT COUNT(*) FROM disputes d
                JOIN obligations o ON d.obligation_id = o.id
                JOIN contracts c ON o.contract_id = c.id
                WHERE d.status = 'open' AND c.user_id = ?
            """, (uid,)).fetchone()[0]

            return {
                "active_contract": active_contract,
                "all_contracts": contracts,
                "notification_count": escalation_count + open_dispute_count,
                "escalation_count": escalation_count,
                "open_dispute_count": open_dispute_count,
            }
        except Exception:
            return {
                "active_contract": None,
                "all_contracts": [],
                "notification_count": 0,
                "escalation_count": 0,
                "open_dispute_count": 0,
            }

    # Template filters
    @app.template_filter("risk_badge")
    def risk_badge_filter(label):
        label = (label or "LOW").upper()
        if label == "HIGH":
            return "badge-risk-high"
        elif label == "MEDIUM":
            return "badge-risk-medium"
        return "badge-risk-low"

    @app.template_filter("status_badge")
    def status_badge_filter(status):
        status = (status or "PENDING").upper()
        if status == "COMPLIANT":
            return "badge-status-compliant"
        elif status == "AT_RISK":
            return "badge-status-atrisk"
        elif status == "VIOLATED":
            return "badge-status-violated"
        return "badge-status-pending"

    @app.template_filter("trust_badge")
    def trust_badge_filter(level):
        level = (level or "").lower()
        if "verifiable" in level or "independent" in level:
            return "bg-success-subtle text-success border border-success-subtle"
        elif "confirmed" in level or "counterparty" in level:
            return "bg-primary-subtle text-primary border border-primary-subtle"
        return "bg-secondary-subtle text-muted border border-secondary-subtle"

    # Error handlers
    @app.errorhandler(403)
    def forbidden_error(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def file_too_large(e):
        flash("File exceeds the 10 MB maximum upload size limit.", "danger")
        return redirect(url_for("contracts.upload"))

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/500.html"), 500

    # Initialize background scheduler for deadline proximity and dispute windows
    if not app.config.get("TESTING"):
        from engine.scheduler import start_scheduler
        start_scheduler(app)

    return app


_wsgi_app = None


def __getattr__(name):
    """Support both `gunicorn run:app` and Render's default `gunicorn app:app`."""
    global _wsgi_app
    if name == "app":
        if _wsgi_app is None:
            _wsgi_app = create_app()
        return _wsgi_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

