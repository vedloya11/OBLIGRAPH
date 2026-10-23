import re
import time
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional
from flask import current_app
from app.database import get_db

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

def execute_sandboxed_query(sql: str, db_path: str) -> Dict[str, Any]:
    """
    Executes validated SQL query using a strictly read-only SQLite connection.
    Guarantees no modifications occur even if complex SQL injection is attempted.
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

    try:
        # Use SQLite URI mode=ro for kernel-level read-only enforcement
        resolved_path = Path(db_path).resolve().as_posix()
        uri = f"file:{resolved_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        
        cur = conn.cursor()
        cur.execute(clean_sql)
        
        # Limit to 100 rows to prevent browser freeze
        raw_rows = cur.fetchmany(100)
        
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in raw_rows]
        
        conn.close()
        
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
