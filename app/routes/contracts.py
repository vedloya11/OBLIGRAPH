import os
import json
import uuid
from pathlib import Path
from flask import Blueprint, render_template, request, flash, redirect, url_for, session, current_app
from flask_login import current_user
from werkzeug.utils import secure_filename
from app.database import get_db
from engine.parsers.doc_parser import extract_text, split_into_clauses
from engine.llm_client import extract_clause_with_llm

contracts_bp = Blueprint("contracts", __name__, url_prefix="/contracts")

ALLOWED_EXTENSIONS = {"pdf", "docx"}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@contracts_bp.route("/")
def index():
    db = get_db()
    uid = current_user.id
    contracts = db.execute("""
        SELECT c.*, COUNT(o.id) as obligation_count 
        FROM contracts c 
        LEFT JOIN obligations o ON c.id = o.contract_id 
        WHERE c.user_id = ?
        GROUP BY c.id 
        ORDER BY c.uploaded_at DESC
    """, (uid,)).fetchall()
    return render_template("contracts/index.html", contracts=contracts)


@contracts_bp.route("/load-sample", methods=["POST"])
def load_sample():
    """Load the sample battery-materials contract and demo scenario into the current user's account."""
    from seed.load_demo import load_demo_data
    try:
        res = load_demo_data(user_id=current_user.id)
        session["active_contract_id"] = res["contract_id"]
        session["active_contract_title"] = res["contract_title"]
        flash(
            f"Sample contract '{res['contract_title']}' loaded into your account with {res['obligations_count']} obligations!",
            "success"
        )
    except Exception as e:
        flash(f"Failed to load sample contract: {e}", "danger")
    return redirect(url_for("main.dashboard"))


@contracts_bp.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        uid = current_user.id
        use_demo = request.form.get("use_demo")
        demo_type = request.form.get("demo_type", "pdf")

        file_path = None
        doc_title = None
        filename = None

        if use_demo:
            seed_dir = Path(current_app.root_path).parent / "seed"
            if demo_type == "docx":
                file_path = seed_dir / "demo_contract.docx"
                filename = "demo_contract.docx"
            else:
                file_path = seed_dir / "demo_contract.pdf"
                filename = "demo_contract.pdf"
            doc_title = "Global Lithium & Chemical Materials Master Supply Agreement"
        else:
            doc_title = request.form.get("title", "").strip() or "Untitled Agreement"
            if "contract_file" not in request.files:
                flash("No file part provided.", "danger")
                return redirect(request.url)

            file = request.files["contract_file"]
            if file.filename == "":
                flash("No file selected for ingestion.", "warning")
                return redirect(request.url)

            if file and allowed_file(file.filename):
                safe_base = secure_filename(file.filename)
                unique_prefix = uuid.uuid4().hex[:12]
                filename = f"{unique_prefix}_{safe_base}"
                base_upload_dir = Path(current_app.config.get("UPLOAD_FOLDER", "uploads"))
                user_upload_dir = base_upload_dir / f"user_{uid}"
                user_upload_dir.mkdir(parents=True, exist_ok=True)
                file_path = user_upload_dir / filename
                file.save(str(file_path))
            else:
                flash("Invalid file type. Only .pdf and .docx files (up to 10 MB) are permitted.", "danger")
                return redirect(request.url)

        # Extract text and split into clauses
        try:
            raw_text = extract_text(str(file_path))
            clauses = split_into_clauses(raw_text)

            if not clauses:
                flash("Could not detect identifiable clauses in the document. Check file contents.", "warning")
                return redirect(request.url)

            # Run 6-field canonical extraction on each clause
            extracted_records = []
            for c in clauses:
                extracted = extract_clause_with_llm(c["text"], c.get("clause_ref", ""))
                extracted_records.append({
                    "clause_ref": c.get("clause_ref", "Clause"),
                    "clause_text": c["text"],
                    "actor": extracted.get("actor", "Obligated Party"),
                    "trigger_deadline": extracted.get("trigger_deadline", "As stipulated"),
                    "required_evidence": extracted.get("required_evidence", "Verification Document"),
                    "expected_state": extracted.get("expected_state", ""),
                    "violation_condition": extracted.get("violation_condition", ""),
                    "remediation_action": extracted.get("remediation_action", ""),
                    "target_value": extracted.get("target_value"),
                    "unit": extracted.get("unit") or "",
                    "deadline_date": extracted.get("deadline_date") or ""
                })

            staging_token = str(uuid.uuid4())
            staging_data = {
                "title": doc_title,
                "filename": filename,
                "raw_text": raw_text,
                "records": extracted_records
            }

            # Store staging data in the database (ephemeral-filesystem safe) scoped to current_user
            db = get_db()
            db.execute(
                "INSERT INTO contract_stagings (token, user_id, title, filename, raw_text, records_json) VALUES (?, ?, ?, ?, ?, ?)",
                (staging_token, uid, doc_title, filename, raw_text, json.dumps(extracted_records))
            )
            db.commit()

            session["staging_token"] = staging_token

            flash(f"Extracted {len(extracted_records)} obligations. Review and confirm below.", "info")
            return redirect(url_for("contracts.review"))

        except Exception as e:
            flash(f"Error parsing document: {str(e)}", "danger")
            return redirect(request.url)

    return render_template("contracts/upload.html")


def get_staging_contract():
    staging_token = session.get("staging_token")
    if not staging_token:
        return None
    db = get_db()
    uid = current_user.id
    row = db.execute(
        "SELECT title, filename, raw_text, records_json FROM contract_stagings WHERE token = ? AND user_id = ?",
        (staging_token, uid)
    ).fetchone()
    if row:
        try:
            return {
                "title": row["title"],
                "filename": row["filename"],
                "raw_text": row["raw_text"],
                "records": json.loads(row["records_json"])
            }
        except Exception:
            return None
    # Fallback for legacy file staging if present in tests
    upload_dir = Path(current_app.config.get("UPLOAD_FOLDER", "uploads"))
    staging_file = upload_dir / f"staging_{staging_token}.json"
    if staging_file.exists():
        with open(staging_file, "r", encoding="utf-8") as sf:
            return json.load(sf)
    return None


def clear_staging_contract():
    staging_token = session.pop("staging_token", None)
    if staging_token:
        db = get_db()
        db.execute(
            "DELETE FROM contract_stagings WHERE token = ? AND user_id = ?",
            (staging_token, current_user.id)
        )
        db.commit()
        upload_dir = Path(current_app.config.get("UPLOAD_FOLDER", "uploads"))
        staging_file = upload_dir / f"staging_{staging_token}.json"
        if staging_file.exists():
            try:
                staging_file.unlink()
            except Exception:
                pass


@contracts_bp.route("/review", methods=["GET"])
def review():
    staging = get_staging_contract()
    if not staging:
        flash("No active document extraction in review. Please upload an agreement first.", "warning")
        return redirect(url_for("contracts.upload"))

    return render_template("contracts/review.html", staging=staging)


@contracts_bp.route("/save", methods=["POST"])
def save():
    staging = get_staging_contract()
    if not staging:
        flash("No active extraction found in session.", "warning")
        return redirect(url_for("contracts.upload"))

    db = get_db()
    uid = current_user.id

    # 1. Insert contract document owned by current_user
    cur = db.execute(
        "INSERT INTO contracts (user_id, title, filename, raw_text) VALUES (?, ?, ?, ?)",
        (uid, staging["title"], staging["filename"], staging["raw_text"])
    )
    contract_id = cur.lastrowid

    # 2. Extract reviewed fields from form submission
    indices = request.form.getlist("clause_index")
    saved_count = 0

    for idx in indices:
        clause_text = request.form.get(f"clause_text_{idx}", "").strip()
        actor = request.form.get(f"actor_{idx}", "Obligated Party").strip()
        trigger_deadline = request.form.get(f"trigger_deadline_{idx}", "").strip()
        required_evidence = request.form.get(f"required_evidence_{idx}", "").strip()
        expected_state = request.form.get(f"expected_state_{idx}", "").strip()
        violation_condition = request.form.get(f"violation_condition_{idx}", "").strip()
        remediation_action = request.form.get(f"remediation_action_{idx}", "").strip()

        target_val_str = request.form.get(f"target_value_{idx}", "").strip()
        target_val = float(target_val_str) if target_val_str else None
        unit = request.form.get(f"unit_{idx}", "").strip()
        deadline_date = request.form.get(f"deadline_date_{idx}", "").strip() or None

        if clause_text and expected_state:
            db.execute("""
                INSERT INTO obligations (
                    contract_id, clause_text, actor, trigger_deadline, required_evidence,
                    expected_state, violation_condition, remediation_action, target_value,
                    unit, deadline_date, status, confidence, risk_score, risk_label
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0.0, 0.0, 'LOW')
            """, (
                contract_id, clause_text, actor, trigger_deadline, required_evidence,
                expected_state, violation_condition, remediation_action, target_val,
                unit, deadline_date
            ))
            saved_count += 1

    db.commit()

    # Clear staging and set active contract
    clear_staging_contract()
    session["active_contract_id"] = contract_id
    session["active_contract_title"] = staging["title"]

    flash(f"Successfully ingested '{staging['title']}' with {saved_count} obligations!", "success")
    return redirect(url_for("obligations.index"))
