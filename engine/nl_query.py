import re
import time
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional
from flask import current_app
from app.database import get_db, get_db_connection, get_current_user_id

FORBIDDEN_KEYWORDS = [
    r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b", r"\bDROP\b", r"\bALTER\b",
    r"\bCREATE\b", r"\bREPLACE\b", r"\bEXEC\b", r"\bEXECUTE\b", r"\bPRAGMA\b",
    r"\bATTACH\b", r"\bDETACH\b", r"\bVACUUM\b", r"\bGRANT\b", r"\bREVOKE\b",
    r"\bTRUNCATE\b", r"\bINTO\b"
]

DB_SCHEMA_PROMPT = """
SQLite Database Schema:
- contracts (id INTEGER, title TEXT, filename TEXT, raw_text TEXT, created_at TIMESTAMP)
- obligations (id INTEGER, contract_id INTEGER, clause_text TEXT, actor TEXT, trigger_deadline TEXT, required_evidence TEXT, expected_state TEXT, violation_condition TEXT, remediation_action TEXT, target_value REAL, unit TEXT, deadline_date TEXT, status TEXT, confidence REAL, risk_score REAL, risk_label TEXT, dispute_deadline TEXT, updated_at TIMESTAMP)
- dependencies (id INTEGER, upstream_id INTEGER, downstream_id INTEGER, note TEXT)
- evidence (id INTEGER, obligation_id INTEGER, source TEXT, trust_level TEXT, trust_weight REAL, observed_value REAL, description TEXT, raw_ref TEXT, created_at TIMESTAMP)
- disputes (id INTEGER, obligation_id INTEGER, submitted_by TEXT, side TEXT, counter_evidence TEXT, attachment TEXT, status TEXT, created_at TIMESTAMP)
- escalations (id INTEGER, obligation_id INTEGER, risk_score REAL, reason TEXT, created_at TIMESTAMP, prev_hash TEXT, entry_hash TEXT)
"""

def validate_and_sanitize_sql(sql: str) -> Dict[str, Any]:
    """
    Strict security sandbox:
    1. Removes SQL comments to prevent injection hiding.
    2. Enforces single statement starting with SELECT or WITH.
    3. Rejects dangerous DDL/DML and write keywords.
    """
    clean_sql = re.sub(r"--.*?$|/\*.*?\*/", "", sql, flags=re.MULTILINE).strip()
    
    # Strip trailing semicolons
    clean_sql = re.sub(r";+\s*$", "", clean_sql).strip()

    if ";" in clean_sql:
        return {
            "valid": False,
            "error": "Query rejected: Multiple SQL statements are strictly forbidden.",
            "sql": clean_sql
        }

    # Must start with SELECT or WITH
    if not re.match(r"^(SELECT|WITH)\b", clean_sql, re.IGNORECASE):
        return {
            "valid": False,
            "error": "Query rejected: Only read-only SELECT queries are permitted.",
            "sql": clean_sql
        }

    # Scan for forbidden keywords
    for pattern in FORBIDDEN_KEYWORDS:
        if re.search(pattern, clean_sql, re.IGNORECASE):
            match_word = re.search(pattern, clean_sql, re.IGNORECASE).group(0)
            return {
                "valid": False,
                "error": f"Security violation: Query contains forbidden command keyword '{match_word.upper()}'.",
                "sql": clean_sql
            }

    return {"valid": True, "error": None, "sql": clean_sql}

def generate_mock_sql(prompt: str) -> str:
    """Deterministic NL-to-SQL rule generator for common queries and offline mode."""
    p = prompt.lower().strip()

    if any(k in p for k in ["due this month", "overdue", "deadline", "upcoming"]):
        return "SELECT id, clause_text, actor, target_value, unit, deadline_date, status, risk_score, risk_label FROM obligations WHERE deadline_date IS NOT NULL ORDER BY deadline_date ASC"

    if any(k in p for k in ["high risk", "supplier risk", "critical", "risk score"]):
        return "SELECT id, clause_text, actor, risk_score, risk_label, status, deadline_date FROM obligations WHERE risk_label = 'HIGH' OR risk_score >= 50.0 ORDER BY risk_score DESC"

    if any(k in p for k in ["missing bol", "bol", "bill of lading", "missing evidence"]):
        return "SELECT o.id, o.clause_text, o.actor, o.required_evidence, e.raw_ref, e.source FROM obligations o LEFT JOIN evidence e ON o.id = e.obligation_id WHERE (o.required_evidence LIKE '%BOL%' OR o.required_evidence LIKE '%Bill of Lading%') AND (e.id IS NULL OR e.raw_ref NOT LIKE '%BOL%')"

    if any(k in p for k in ["resolved dispute", "all disputes", "disputes", "counter-evidence"]):
        return "SELECT d.id, d.obligation_id, o.clause_text, d.submitted_by, d.side, d.status, d.created_at FROM disputes d JOIN obligations o ON d.obligation_id = o.id ORDER BY d.id DESC"

    if any(k in p for k in ["escalation", "hash chain", "blockchain", "ledger"]):
        return "SELECT e.id, e.obligation_id, o.clause_text, e.risk_score, e.reason, e.prev_hash, e.entry_hash, e.created_at FROM escalations e JOIN obligations o ON e.obligation_id = o.id ORDER BY e.id ASC"

    if any(k in p for k in ["purity", "chemical", "quality", "lithium"]):
        return "SELECT id, clause_text, actor, target_value, unit, status, confidence, risk_score FROM obligations WHERE clause_text LIKE '%purity%' OR clause_text LIKE '%chemical%' OR expected_state LIKE '%purity%'"

    if any(k in p for k in ["evidence", "proof", "assays"]):
        return "SELECT e.id, e.obligation_id, o.clause_text, e.source, e.trust_level, e.trust_weight, e.observed_value, e.description, e.raw_ref FROM evidence e JOIN obligations o ON e.obligation_id = o.id ORDER BY e.id DESC"

    # Default fallback: search keyword in clause text or show all obligations
    clean_words = [w for w in re.findall(r"[a-zA-Z0-9]+", p) if len(w) > 3 and w not in ["what", "show", "list", "which", "about", "your", "contracts", "obligations"]]
    if clean_words:
        like_clauses = " OR ".join([f"clause_text LIKE '%{w}%' OR actor LIKE '%{w}%'" for w in clean_words[:3]])
        return f"SELECT id, clause_text, actor, expected_state, status, confidence, risk_score, risk_label FROM obligations WHERE {like_clauses} ORDER BY risk_score DESC"

    return "SELECT id, clause_text, actor, expected_state, target_value, unit, deadline_date, status, confidence, risk_score, risk_label FROM obligations ORDER BY id ASC"

def translate_nl_to_sql(prompt: str) -> str:
    """
    Translates natural-language user prompt to SQLite SELECT statement.
    Attempts LLM if online/available; gracefully falls back to deterministic rule engine.
    """
    from engine.llm_client import is_llm_configured, generate_sql_with_llm
    
    if is_llm_configured():
        try:
            sql = generate_sql_with_llm(prompt, DB_SCHEMA_PROMPT)
            if sql and (sql.upper().startswith("SELECT") or sql.upper().startswith("WITH")):
                return sql
        except Exception:
            pass

    return generate_mock_sql(prompt)

def execute_sandboxed_query(sql: str, db_path: str = None, user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Executes validated SQL query inside a strictly read-only, per-user isolated
    in-memory SQLite sandbox populated ONLY with the authenticated user's rows.
    Guarantees:
      1. No modifications can occur to the main database.
      2. Cross-user data access is impossible because other users' rows and
         sensitive tables (users, settings) do not exist in the sandbox.
    """
    validation = validate_and_sanitize_sql(sql)
    if not validation["valid"]:
        return {
            "sql": sql,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": 0.0,
            "error": validation["error"]
        }

    clean_sql = validation["sql"]
    start_time = time.perf_counter()

    uid = user_id if user_id is not None else get_current_user_id(default=1)
    uid = int(uid or 1)

    try:
        src_conn = get_db_connection(db_path)

        # Fetch only the current user's records from the source database
        contracts_rows = src_conn.execute(
            "SELECT id, title, filename, raw_text, uploaded_at FROM contracts WHERE user_id = ?",
            (uid,)
        ).fetchall()
        obligations_rows = src_conn.execute(
            """
            SELECT o.id, o.contract_id, o.clause_text, o.actor, o.trigger_deadline,
                   o.required_evidence, o.expected_state, o.violation_condition,
                   o.remediation_action, o.target_value, o.unit, o.deadline_date,
                   o.status, o.confidence, o.risk_score, o.risk_label,
                   o.dispute_deadline, o.updated_at
            FROM obligations o
            JOIN contracts c ON o.contract_id = c.id
            WHERE c.user_id = ?
            """,
            (uid,)
        ).fetchall()
        dependencies_rows = src_conn.execute(
            """
            SELECT d.id, d.upstream_id, d.downstream_id, d.note
            FROM dependencies d
            JOIN obligations o ON d.upstream_id = o.id
            JOIN contracts c ON o.contract_id = c.id
            WHERE c.user_id = ?
            """,
            (uid,)
        ).fetchall()
        evidence_rows = src_conn.execute(
            """
            SELECT e.id, e.obligation_id, e.source, e.trust_level, e.trust_weight,
                   e.observed_value, e.description, e.raw_ref, e.created_at
            FROM evidence e
            JOIN obligations o ON e.obligation_id = o.id
            JOIN contracts c ON o.contract_id = c.id
            WHERE c.user_id = ?
            """,
            (uid,)
        ).fetchall()
        disputes_rows = src_conn.execute(
            """
            SELECT d.id, d.obligation_id, d.submitted_by, d.side,
                   d.counter_evidence, d.attachment, d.status, d.created_at
            FROM disputes d
            JOIN obligations o ON d.obligation_id = o.id
            JOIN contracts c ON o.contract_id = c.id
            WHERE c.user_id = ?
            """,
            (uid,)
        ).fetchall()
        escalations_rows = src_conn.execute(
            """
            SELECT id, obligation_id, risk_score, reason, created_at, prev_hash, entry_hash
            FROM escalations
            WHERE user_id = ?
            """,
            (uid,)
        ).fetchall()
        src_conn.close()

        # Build isolated in-memory SQLite database containing ONLY this user's rows
        sandbox = sqlite3.connect(":memory:")
        sandbox.row_factory = sqlite3.Row
        sandbox.executescript("""
            CREATE TABLE contracts (id INTEGER PRIMARY KEY, title TEXT, filename TEXT, raw_text TEXT, created_at TIMESTAMP);
            CREATE TABLE obligations (id INTEGER PRIMARY KEY, contract_id INTEGER, clause_text TEXT, actor TEXT, trigger_deadline TEXT, required_evidence TEXT, expected_state TEXT, violation_condition TEXT, remediation_action TEXT, target_value REAL, unit TEXT, deadline_date TEXT, status TEXT, confidence REAL, risk_score REAL, risk_label TEXT, dispute_deadline TEXT, updated_at TIMESTAMP);
            CREATE TABLE dependencies (id INTEGER PRIMARY KEY, upstream_id INTEGER, downstream_id INTEGER, note TEXT);
            CREATE TABLE evidence (id INTEGER PRIMARY KEY, obligation_id INTEGER, source TEXT, trust_level TEXT, trust_weight REAL, observed_value REAL, description TEXT, raw_ref TEXT, created_at TIMESTAMP);
            CREATE TABLE disputes (id INTEGER PRIMARY KEY, obligation_id INTEGER, submitted_by TEXT, side TEXT, counter_evidence TEXT, attachment TEXT, status TEXT, created_at TIMESTAMP);
            CREATE TABLE escalations (id INTEGER PRIMARY KEY, obligation_id INTEGER, risk_score REAL, reason TEXT, created_at TIMESTAMP, prev_hash TEXT, entry_hash TEXT);
        """)

        if contracts_rows:
            sandbox.executemany(
                "INSERT INTO contracts VALUES (?, ?, ?, ?, ?)",
                [tuple(r) for r in contracts_rows]
            )
        if obligations_rows:
            sandbox.executemany(
                "INSERT INTO obligations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [tuple(r) for r in obligations_rows]
            )
        if dependencies_rows:
            sandbox.executemany(
                "INSERT INTO dependencies VALUES (?, ?, ?, ?)",
                [tuple(r) for r in dependencies_rows]
            )
        if evidence_rows:
            sandbox.executemany(
                "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [tuple(r) for r in evidence_rows]
            )
        if disputes_rows:
            sandbox.executemany(
                "INSERT INTO disputes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [tuple(r) for r in disputes_rows]
            )
        if escalations_rows:
            sandbox.executemany(
                "INSERT INTO escalations VALUES (?, ?, ?, ?, ?, ?, ?)",
                [tuple(r) for r in escalations_rows]
            )
        sandbox.commit()

        # Lock the in-memory database to read-only before executing user SQL
        sandbox.execute("PRAGMA query_only = ON;")

        cur = sandbox.cursor()
        cur.execute(clean_sql)

        # Limit to 100 rows to prevent browser freeze
        raw_rows = cur.fetchmany(100)

        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in raw_rows]

        sandbox.close()

        exec_ms = round((time.perf_counter() - start_time) * 1000.0, 2)

        return {
            "sql": clean_sql,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "execution_time_ms": exec_ms,
            "error": None
        }
    except Exception as e:
        exec_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
        return {
            "sql": clean_sql,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": exec_ms,
            "error": f"SQL Execution Error: {str(e)}"
        }
