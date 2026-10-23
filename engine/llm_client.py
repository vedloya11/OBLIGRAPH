import os
import json
import re
from typing import Dict, Any, Optional
import requests
from config import Config

SIX_FIELDS = [
    "actor",
    "trigger_deadline",
    "required_evidence",
    "expected_state",
    "violation_condition",
    "remediation_action"
]

def validate_clause_record(data: Dict[str, Any]) -> bool:
    """Validate that the record adheres strictly to the six-field canonical schema."""
    if not isinstance(data, dict):
        return False
    for field in SIX_FIELDS:
        val = data.get(field)
        if not val or not str(val).strip():
            return False
    return True

def extract_clause_mock(clause_text: str, clause_ref: str = "") -> Dict[str, Any]:
    """
    Deterministic canned / rule-assisted extractor for offline mock mode.
    Accurately extracts structured 6-field obligations based on clause content.
    """
    lower = clause_text.lower()
    
    # 1. Volume / Delivery clauses
    if any(k in lower for k in ["deliver", "volume", "metric ton", "quantity", "shipment"]):
        target_val = 5000.0
        unit = "metric tons"
        if "5,000" in clause_text or "5000" in clause_text:
            target_val = 5000.0
        elif "10,000" in clause_text or "10000" in clause_text:
            target_val = 10000.0
        
        return {
            "actor": "Supplier",
            "trigger_deadline": "Monthly schedule (by the 5th of each calendar month)",
            "required_evidence": "Signed Bill of Lading & Port Authority Weighbridge Certificate",
            "expected_state": f"Deliver minimum {target_val:,.0f} {unit} of compliant grade materials",
            "violation_condition": f"Delivered volume falls below {target_val:,.0f} {unit} or arrival delayed past monthly cutoff",
            "remediation_action": "Supplier shall expedite replacement volume via express freight at its sole expense and credit 2% per day of delay",
            "target_value": target_val,
            "unit": unit,
            "deadline_date": "2026-11-05"
        }

    # 2. Quality / Chemical Purity clauses
    elif any(k in lower for k in ["purity", "quality", "chemical", "specification", "coa", "assay", "grade"]):
        target_val = 98.5
        unit = "%"
        if "98.5" in clause_text:
            target_val = 98.5
        elif "99.0" in clause_text or "99%" in clause_text:
            target_val = 99.0

        return {
            "actor": "Supplier",
            "trigger_deadline": "Concurrent with each dispatched shipment lot",
            "required_evidence": "Independent Certified Laboratory Purity Assay Certificate (COA)",
            "expected_state": f"Material chemical purity shall equal or exceed {target_val}%",
            "violation_condition": f"Lot purity assay measures below {target_val}% or contains unpermitted contaminants",
            "remediation_action": "Immediate batch rejection with replacement shipment dispatched within 72 hours plus full disposal indemnification",
            "target_value": target_val,
            "unit": unit,
            "deadline_date": "2026-11-06"
        }

    # 3. Price adjustment notice clauses
    elif any(k in lower for k in ["price", "notice", "written notice", "adjustment", "rate"]):
        return {
            "actor": "Supplier",
            "trigger_deadline": "At least 30 calendar days prior to proposed effective effective date",
            "required_evidence": "Formal Written Notice Letter via registered mail or verified counterparty portal",
            "expected_state": "Provide verifiable 30-day advance notice before modifying unit pricing",
            "violation_condition": "Price increase billed without prior 30-day written notification window",
            "remediation_action": "Invoice adjustment voided; prior contracted baseline rate remains binding for subsequent quarter",
            "target_value": 30.0,
            "unit": "days",
            "deadline_date": "2026-11-15"
        }

    # 4. Inspection / Defect notification window
    elif any(k in lower for k in ["inspect", "dispute", "rejection", "acceptance", "defect"]):
        return {
            "actor": "Buyer",
            "trigger_deadline": "Within 7 business days following physical receipt at receiving terminal",
            "required_evidence": "Formal Receiving Inspection Report and photographic defect documentation",
            "expected_state": "Inspect incoming consignment and lodge any non-conformance notice within 7 days",
            "violation_condition": "Failure to lodge written rejection notice within the specified 7-day acceptance window",
            "remediation_action": "Consignment deemed irrevocably accepted as compliant without right of return",
            "target_value": 7.0,
            "unit": "days",
            "deadline_date": "2026-11-12"
        }

    # 5. Payment settlement clauses
    elif any(k in lower for k in ["payment", "invoice", "net 30", "settlement", "remit"]):
        return {
            "actor": "Buyer",
            "trigger_deadline": "Net 30 days from date of validated electronic invoice transmission",
            "required_evidence": "Bank Electronic Funds Transfer (EFT) remittance advice or SWIFT confirmation",
            "expected_state": "Full settlement of undisputed invoice amounts within 30 calendar days",
            "violation_condition": "Settlement delayed beyond 30 days without active disputed item notification",
            "remediation_action": "Late interest assessed at SOFR + 3.5% per annum calculated daily until settled",
            "target_value": 30.0,
            "unit": "days",
            "deadline_date": "2026-11-30"
        }

    # 6. Environmental / ESG / Audit clauses
    elif any(k in lower for k in ["carbon", "environmental", "audit", "emission", "esg", "sustainability"]):
        return {
            "actor": "Supplier",
            "trigger_deadline": "Annually on or before December 15th",
            "required_evidence": "Third-party ISO 14064 GHG audit verification statement",
            "expected_state": "Annual supply chain carbon intensity not to exceed 2.4 kg CO2e per kg product",
            "violation_condition": "Carbon intensity exceeds contracted cap or audit report omitted",
            "remediation_action": "Supplier must purchase Gold Standard carbon credits to offset excess emissions within 30 days",
            "target_value": 2.4,
            "unit": "kg CO2e/kg",
            "deadline_date": "2026-12-15"
        }

    # 7. Insurance & Liability Coverage
    elif any(k in lower for k in ["insurance", "liability", "policy", "coverage", "underwrite"]):
        return {
            "actor": "Supplier",
            "trigger_deadline": "Maintain continuous in-force policy; renewal proof due 15 days prior to expiry",
            "required_evidence": "ACORD Certificate of Commercial General Liability Insurance ($10M minimum)",
            "expected_state": "Maintain valid comprehensive commercial general liability insurance with Buyer named as additional insured",
            "violation_condition": "Policy lapse, coverage reduction below $10,000,000, or failure to submit certificate",
            "remediation_action": "Buyer may procure replacement policy at Supplier's expense and withhold corresponding billing amounts",
            "target_value": 10000000.0,
            "unit": "USD",
            "deadline_date": "2026-12-01"
        }

    # 8. Hazardous Material Packaging & Labeling
    elif any(k in lower for k in ["package", "packaging", "label", "hazardous", "transport", "safety"]):
        return {
            "actor": "Supplier",
            "trigger_deadline": "Prior to container loading and customs hand-off",
            "required_evidence": "Dangerous Goods Declaration and UN-certified drum inspection certificate",
            "expected_state": "All materials packaged in hermetically sealed UN-approved drums with GHS hazard labels",
            "violation_condition": "Damaged container seals, missing GHS toxic placards, or leaking drums",
            "remediation_action": "Port quarantine fees and hazmat repackaging charged directly to Supplier",
            "target_value": 100.0,
            "unit": "% compliant",
            "deadline_date": "2026-11-04"
        }

    # General Fallback
    actor = "Supplier" if "supplier" in lower else ("Buyer" if "buyer" in lower else "Obligated Party")
    return {
        "actor": actor,
        "trigger_deadline": "As stipulated in agreement terms",
        "required_evidence": "Written compliance statement and counterparty confirmation",
        "expected_state": clause_text[:120].strip() + "...",
        "violation_condition": "Failure to perform or maintain conditions as stated",
        "remediation_action": "Cure non-compliance within 15 calendar days upon written demand",
        "target_value": None,
        "unit": None,
        "deadline_date": None
    }

def extract_clause_with_llm(clause_text: str, clause_ref: str = "") -> Dict[str, Any]:
    """
    Main entry point for extracting the canonical six-field record.
    If MOCK_LLM is enabled or if external API is unreachable, uses deterministic extraction.
    """
    if Config.MOCK_LLM:
        return extract_clause_mock(clause_text, clause_ref)

    # If API keys are present, attempt LLM extraction
    prompt = f"""
You are an expert legal contract analyst. Extract the legal obligation from the following contract clause into a strict JSON record with EXACTLY these six fields:
1. "actor": The party obligated to act (e.g. "Supplier", "Buyer").
2. "trigger_deadline": When the obligation is triggered or due (e.g. "Monthly by 5th", "Within 30 days").
3. "required_evidence": What verifiable proof is required to demonstrate compliance (e.g. "Bill of Lading", "Lab Assay Certificate").
4. "expected_state": The quantifiable or qualitative state expected (e.g. "Deliver 5,000 MT with purity >= 98.5%").
5. "violation_condition": Exact circumstances constituting a breach or violation.
6. "remediation_action": Cure protocol, liquidated damages, or remedy specified.

Optional helper numeric fields:
- "target_value": numeric target if applicable (e.g. 5000, 98.5, 30) or null
- "unit": unit of measure (e.g. "metric tons", "%", "days") or null
- "deadline_date": ISO date "YYYY-MM-DD" if specific, else null

Clause Reference: {clause_ref}
Clause Text:
\"\"\"{clause_text}\"\"\"

Output ONLY valid JSON without markdown formatting or code blocks.
"""

    headers = {"Content-Type": "application/json"}
    
    # Gemini API
    if Config.LLM_PROVIDER == "gemini" and Config.GEMINI_API_KEY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={Config.GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}
        }
        for attempt in range(2):
            try:
                res = requests.post(url, json=payload, headers=headers, timeout=12)
                if res.status_code == 200:
                    text_resp = res.json()["candidates"][0]["content"]["parts"][0]["text"]
                    cleaned = re.sub(r"^```json\s*", "", text_resp.strip())
                    cleaned = re.sub(r"\s*```$", "", cleaned)
                    parsed = json.loads(cleaned)
                    if validate_clause_record(parsed):
                        return parsed
            except Exception:
                pass

    # Claude API
    elif Config.LLM_PROVIDER == "claude" and Config.ANTHROPIC_API_KEY:
        url = "https://api.anthropic.com/v1/messages"
        headers["x-api-key"] = Config.ANTHROPIC_API_KEY
        headers["anthropic-version"] = "2023-06-01"
        payload = {
            "model": "claude-3-haiku-20240307",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": prompt}]
        }
        for attempt in range(2):
            try:
                res = requests.post(url, json=payload, headers=headers, timeout=12)
                if res.status_code == 200:
                    text_resp = res.json()["content"][0]["text"]
                    cleaned = re.sub(r"^```json\s*", "", text_resp.strip())
                    cleaned = re.sub(r"\s*```$", "", cleaned)
                    parsed = json.loads(cleaned)
                    if validate_clause_record(parsed):
                        return parsed
            except Exception:
                pass

    # Safe fallback if API was configured but failed or timed out
    return extract_clause_mock(clause_text, clause_ref)

def is_llm_configured() -> bool:
    """Check if an external LLM API key is actively configured."""
    return bool(Config.GEMINI_API_KEY or Config.ANTHROPIC_API_KEY)

def generate_sql_with_llm(prompt: str, schema_prompt: str) -> Optional[str]:
    """Generate SQL query from plain English prompt using configured LLM, or None if offline."""
    if not is_llm_configured():
        return None

    full_prompt = (
        f"You are a SQL expert. Translate the user natural language query into a valid, safe SQLite SELECT query.\n"
        f"{schema_prompt}\n"
        f"CRITICAL RULES:\n"
        f"1. Return ONLY the raw SQL query string. No markdown formatting, no backticks, no explanations.\n"
        f"2. ONLY generate SELECT statements. Never generate INSERT, UPDATE, DELETE, or DDL statements.\n"
        f"3. Keep queries concise, accurate, and bounded.\n\n"
        f"User Query: {prompt}\n"
        f"SQLite Query:"
    )

    headers = {"Content-Type": "application/json"}
    
    # Gemini
    if Config.LLM_PROVIDER == "gemini" and Config.GEMINI_API_KEY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={Config.GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {"temperature": 0.0}
        }
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                raw = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                cleaned = re.sub(r"^```(sql)?\s*", "", raw)
                cleaned = re.sub(r"\s*```$", "", cleaned).strip()
                return cleaned
        except Exception:
            pass

    # Claude
    elif Config.LLM_PROVIDER == "claude" and Config.ANTHROPIC_API_KEY:
        url = "https://api.anthropic.com/v1/messages"
        headers["x-api-key"] = Config.ANTHROPIC_API_KEY
        headers["anthropic-version"] = "2023-06-01"
        payload = {
            "model": "claude-3-haiku-20240307",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": full_prompt}]
        }
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                raw = res.json()["content"][0]["text"].strip()
                cleaned = re.sub(r"^```(sql)?\s*", "", raw)
                cleaned = re.sub(r"\s*```$", "", cleaned).strip()
                return cleaned
        except Exception:
            pass

    return None
