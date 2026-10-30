import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from app.database import get_db, get_current_user_id

GENESIS_PREV_HASH = "0" * 64


def _resolve_chain_user_id(db, user_id: Optional[int] = None, obligation_id: Optional[int] = None) -> int:
    if user_id is not None:
        return int(user_id)
    uid = get_current_user_id()
    if uid is not None:
        return int(uid)
    if obligation_id is not None and db is not None:
        try:
            row = db.execute("""
                SELECT c.user_id FROM obligations o
                JOIN contracts c ON o.contract_id = c.id
                WHERE o.id = ?
            """, (obligation_id,)).fetchone()
            if row and row["user_id"] is not None:
                return int(row["user_id"])
        except Exception:
            pass
    return 1


def compute_block_hash(block_id: int, timestamp: str, obligation_id: int, reason: str, prev_hash: str) -> str:
    """
    Compute cryptographic SHA-256 hash for an escalation block:
    hash = SHA-256(id + timestamp + obligation_id + reason + previous_hash)
    """
    raw = f"{block_id}{timestamp}{obligation_id}{reason}{prev_hash}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def get_last_escalation_block(db=None, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Get the most recent escalation block in the user's chain."""
    if db is None:
        db = get_db()
    uid = _resolve_chain_user_id(db, user_id=user_id)
    row = db.execute(
        "SELECT * FROM escalations WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (uid,)
    ).fetchone()
    if row:
        return dict(row)
    return None


def add_escalation_entry(
    obligation_id: int,
    reason: str,
    risk_score: float = 0.0,
    created_at: Optional[str] = None,
    db=None,
    user_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Append an immutable escalation block to the user's SHA-256 hash chain.
    Genesis block links to '0'*64; subsequent blocks link to prev_hash.
    """
    if db is None:
        db = get_db()

    uid = _resolve_chain_user_id(db, user_id=user_id, obligation_id=obligation_id)
    last_block = get_last_escalation_block(db=db, user_id=uid)
    prev_hash = last_block["entry_hash"] if last_block else GENESIS_PREV_HASH

    if not created_at:
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    cur = db.execute("""
        INSERT INTO escalations (user_id, obligation_id, risk_score, reason, created_at, prev_hash, entry_hash)
        VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
    """, (uid, obligation_id, float(risk_score), reason, created_at, prev_hash))

    block_id = cur.lastrowid
    entry_hash = compute_block_hash(block_id, created_at, obligation_id, reason, prev_hash)

    db.execute("UPDATE escalations SET entry_hash = ? WHERE id = ?", (entry_hash, block_id))
    db.commit()

    return {
        "id": block_id,
        "user_id": uid,
        "obligation_id": obligation_id,
        "risk_score": risk_score,
        "reason": reason,
        "created_at": created_at,
        "prev_hash": prev_hash,
        "entry_hash": entry_hash
    }


def verify_chain(db=None, user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Walk the user's hash chain from Genesis to latest block and verify cryptographic integrity.
    Flags any corrupted payload, broken linkage, or tampered block.
    """
    if db is None:
        db = get_db()

    uid = _resolve_chain_user_id(db, user_id=user_id)
    rows = db.execute(
        "SELECT * FROM escalations WHERE user_id = ? ORDER BY id ASC",
        (uid,)
    ).fetchall()
    blocks = [dict(r) for r in rows]

    if not blocks:
        return {
            "is_valid": True,
            "total_blocks": 0,
            "message": "Escalation hash chain is empty (no violations logged yet)."
        }

    for i, block in enumerate(blocks):
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


def simulate_tampering(block_id: Optional[int] = None, db=None, user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Simulate malicious tampering by altering block content directly in the database
    WITHOUT recalculating the cryptographic hash. Scoped to the user's chain.
    """
    if db is None:
        db = get_db()

    uid = _resolve_chain_user_id(db, user_id=user_id)

    if block_id is None:
        row = db.execute(
            "SELECT id FROM escalations WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (uid,)
        ).fetchone()
        if not row:
            return {"success": False, "message": "Cannot simulate tampering: no escalation blocks exist."}
        block_id = row["id"]
    else:
        owned = db.execute(
            "SELECT id FROM escalations WHERE id = ? AND user_id = ?",
            (block_id, uid)
        ).fetchone()
        if not owned:
            return {"success": False, "not_found": True, "message": "Escalation block not found."}

    tampered_text = f"[FORGED/TAMPERED ENTRY - AUDIT ALERT] Altered at {datetime.now(timezone.utc).strftime('%H:%M:%S')}"
    db.execute("UPDATE escalations SET reason = ? WHERE id = ? AND user_id = ?", (tampered_text, block_id, uid))
    db.commit()

    return {
        "success": True,
        "tampered_block_id": block_id,
        "message": f"Block #{block_id} payload altered in database without updating cryptographic hash. Verification should now fail."
    }


def repair_chain(db=None, user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Restore cryptographic integrity by recalculating valid hash chain from Genesis to latest
    for the current user's chain only.
    """
    if db is None:
        db = get_db()

    uid = _resolve_chain_user_id(db, user_id=user_id)
    rows = db.execute(
        "SELECT * FROM escalations WHERE user_id = ? ORDER BY id ASC",
        (uid,)
    ).fetchall()
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

        clean_reason = reason.replace("[FORGED/TAMPERED ENTRY - AUDIT ALERT]", "").strip()
        if not clean_reason:
            clean_reason = f"Restored Escalation Violation for Obligation #{obligation_id}"

        recalculated_hash = compute_block_hash(block_id, created_at, obligation_id, clean_reason, curr_prev_hash)

        db.execute("""
            UPDATE escalations 
            SET reason = ?, prev_hash = ?, entry_hash = ?
            WHERE id = ? AND user_id = ?
        """, (clean_reason, curr_prev_hash, recalculated_hash, block_id, uid))

        curr_prev_hash = recalculated_hash
        repaired_count += 1

    db.commit()

    return {
        "repaired": True,
        "blocks_repaired": repaired_count,
        "message": f"Hash chain successfully repaired and re-anchored across {repaired_count} block(s)."
    }
