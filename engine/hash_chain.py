import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from app.database import get_db

GENESIS_PREV_HASH = "0" * 64

def compute_block_hash(block_id: int, timestamp: str, obligation_id: int, reason: str, prev_hash: str) -> str:
    """
    Compute cryptographic SHA-256 hash for an escalation block:
    hash = SHA-256(id + timestamp + obligation_id + reason + previous_hash)
    """
    raw = f"{block_id}{timestamp}{obligation_id}{reason}{prev_hash}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def get_last_escalation_block(db=None) -> Optional[Dict[str, Any]]:
    """Get the most recent escalation block in the chain."""
    if db is None:
        db = get_db()
    row = db.execute("SELECT * FROM escalations ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        return dict(row)
    return None

def add_escalation_entry(
    obligation_id: int,
    reason: str,
    risk_score: float = 0.0,
    created_at: Optional[str] = None,
    db=None
) -> Dict[str, Any]:
    """
    Append an immutable escalation block to the SHA-256 hash chain.
    Genesis block links to '0'*64; subsequent blocks link to prev_hash.
    """
    if db is None:
        db = get_db()

    last_block = get_last_escalation_block(db=db)
    prev_hash = last_block["entry_hash"] if last_block else GENESIS_PREV_HASH

    if not created_at:
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Insert with temporary entry_hash to obtain autoincrement ID
    cur = db.execute("""
        INSERT INTO escalations (obligation_id, risk_score, reason, created_at, prev_hash, entry_hash)
        VALUES (?, ?, ?, ?, ?, 'PENDING')
    """, (obligation_id, float(risk_score), reason, created_at, prev_hash))
    
    block_id = cur.lastrowid

    # Compute deterministic SHA-256 hash
    entry_hash = compute_block_hash(block_id, created_at, obligation_id, reason, prev_hash)

    # Finalize block
    db.execute("UPDATE escalations SET entry_hash = ? WHERE id = ?", (entry_hash, block_id))
    db.commit()

    return {
        "id": block_id,
        "obligation_id": obligation_id,
        "risk_score": risk_score,
        "reason": reason,
        "created_at": created_at,
        "prev_hash": prev_hash,
        "entry_hash": entry_hash
    }

def verify_chain(db=None) -> Dict[str, Any]:
    """
    Walk the hash chain from Genesis to latest block and verify cryptographic integrity.
    Flags any corrupted payload, broken linkage, or tampered block.
    """
    if db is None:
        db = get_db()

    rows = db.execute("SELECT * FROM escalations ORDER BY id ASC").fetchall()
    blocks = [dict(r) for r in rows]

    if not blocks:
        return {
            "is_valid": True,
            "total_blocks": 0,
            "message": "Escalation hash chain is empty (no violations logged yet)."
        }

    for i, block in enumerate(blocks):
        # 1. Check previous hash linkage
        if i == 0:
            if block["prev_hash"] != GENESIS_PREV_HASH:
                return {
                    "is_valid": False,
                    "corrupted_block_id": block["id"],
                    "reason": f"Genesis block #{block['id']} invalid prev_hash: expected {'0'*16}..., found {block['prev_hash'][:16]}...",
                    "total_blocks": len(blocks),
                    "verified_blocks": 0
                }
        else:
            prev_block = blocks[i - 1]
            if block["prev_hash"] != prev_block["entry_hash"]:
                return {
                    "is_valid": False,
                    "corrupted_block_id": block["id"],
                    "reason": f"Chain linkage broken at Block #{block['id']}: prev_hash does not match Block #{prev_block['id']} entry_hash.",
                    "total_blocks": len(blocks),
                    "verified_blocks": i
                }

        # 2. Check current block hash recalculation
        expected_hash = compute_block_hash(
            block["id"],
            block["created_at"],
            block["obligation_id"],
            block["reason"],
            block["prev_hash"]
        )

        if block["entry_hash"] != expected_hash:
            return {
                "is_valid": False,
                "corrupted_block_id": block["id"],
                "reason": f"Payload tamper detected at Block #{block['id']}! Computed SHA-256 {expected_hash[:16]}... does not match recorded hash {block['entry_hash'][:16]}...",
                "total_blocks": len(blocks),
                "verified_blocks": i
            }

    return {
        "is_valid": True,
        "total_blocks": len(blocks),
        "last_hash": blocks[-1]["entry_hash"],
        "message": f"Cryptographic integrity verified intact across all {len(blocks)} blockchain block(s)."
    }

def simulate_tampering(block_id: Optional[int] = None, db=None) -> Dict[str, Any]:
    """
    Simulate malicious tampering by altering block content directly in the database
    WITHOUT recalculating the cryptographic hash.
    """
    if db is None:
        db = get_db()

    if block_id is None:
        row = db.execute("SELECT id FROM escalations ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return {"success": False, "message": "Cannot simulate tampering: no escalation blocks exist."}
        block_id = row["id"]

    # Tamper with the reason field
    tampered_text = f"[FORGED/TAMPERED ENTRY - AUDIT ALERT] Altered at {datetime.now(timezone.utc).strftime('%H:%M:%S')}"
    db.execute("UPDATE escalations SET reason = ? WHERE id = ?", (tampered_text, block_id))
    db.commit()

    return {
        "success": True,
        "tampered_block_id": block_id,
        "message": f"Block #{block_id} payload altered in database without updating cryptographic hash. Verification should now fail."
    }

def repair_chain(db=None) -> Dict[str, Any]:
    """
    Restore cryptographic integrity by recalculating valid hash chain from Genesis to latest.
    """
    if db is None:
        db = get_db()

    rows = db.execute("SELECT * FROM escalations ORDER BY id ASC").fetchall()
    blocks = [dict(r) for r in rows]

    if not blocks:
        return {"repaired": True, "blocks_repaired": 0, "message": "No blocks to repair."}

    curr_prev_hash = GENESIS_PREV_HASH
    repaired_count = 0

    for block in blocks:
        block_id = block["id"]
        created_at = block["created_at"]
        obligation_id = block["obligation_id"]
        reason = block["reason"]

        # Clean tampered notice if present
        clean_reason = reason.replace("[FORGED/TAMPERED ENTRY - AUDIT ALERT]", "").strip()
        if not clean_reason:
            clean_reason = f"Restored Escalation Violation for Obligation #{obligation_id}"

        recalculated_hash = compute_block_hash(block_id, created_at, obligation_id, clean_reason, curr_prev_hash)

        db.execute("""
            UPDATE escalations 
            SET reason = ?, prev_hash = ?, entry_hash = ?
            WHERE id = ?
        """, (clean_reason, curr_prev_hash, recalculated_hash, block_id))

        curr_prev_hash = recalculated_hash
        repaired_count += 1

    db.commit()

    return {
        "repaired": True,
        "blocks_repaired": repaired_count,
        "message": f"Hash chain successfully repaired and re-anchored across {repaired_count} block(s)."
    }
