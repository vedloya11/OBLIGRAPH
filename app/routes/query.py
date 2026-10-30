from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import current_user
from engine.nl_query import translate_nl_to_sql, execute_sandboxed_query

query_bp = Blueprint("query", __name__, url_prefix="/query")


@query_bp.route("/", methods=["GET", "POST"])
def index():
    initial_prompt = request.args.get("q", "").strip()
    result = None

    if request.method == "POST":
        prompt = request.form.get("prompt", "").strip()
        raw_sql = request.form.get("sql", "").strip()

        sql_to_run = raw_sql if raw_sql else translate_nl_to_sql(prompt)
        db_path = current_app.config.get("DATABASE_PATH")
        result = execute_sandboxed_query(sql_to_run, db_path, user_id=current_user.id)
        initial_prompt = prompt

    return render_template("query/index.html", result=result, initial_prompt=initial_prompt)


@query_bp.route("/api", methods=["POST"])
@query_bp.route("/api/execute", methods=["POST"])
def api():
    """
    JSON API for natural language to SQL query execution with per-user sandbox enforcement.
    Payload: {"prompt": "What obligations are high risk?"} or {"query": "..."} or {"sql": "SELECT ..."}
    """
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or data.get("query") or "").strip()
    raw_sql = (data.get("sql") or "").strip()

    if not prompt and not raw_sql:
        return jsonify({
            "success": False,
            "error": "A natural language prompt or SQL query string is required.",
            "sql": "",
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": 0.0
        }), 400

    sql_to_run = raw_sql if raw_sql else translate_nl_to_sql(prompt)
    db_path = current_app.config.get("DATABASE_PATH")

    result = execute_sandboxed_query(sql_to_run, db_path, user_id=current_user.id)
    result["prompt"] = prompt
    result["success"] = result.get("error") is None
    return jsonify(result)
