"""Bookkeeping Agent's tool surface.

These are the ONLY functions the Bookkeeping Agent can call. Two are
read-only (safe, no gate needed). One is a write with a code-enforced
control: post_categorization refuses to execute below CONFIDENCE_THRESHOLD,
regardless of what the agent's own reasoning concluded — the gate lives in
code, not in the system prompt, per Section 10 of the design charter
("a control the model could talk itself out of is not a control").

Every call — success, refusal, or escalation — is written to audit_log.
This is what "the agent must work, not merely demonstrate intelligence"
looks like in practice: a reviewer can open audit_log after the fact and
see exactly what was inspected, decided, and why, with no LLM in the loop
at review time.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

CONFIDENCE_THRESHOLD = 0.75
AGENT_NAME = "bookkeeping_agent"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(conn: sqlite3.Connection, action: str, tool_name: str, inputs: dict,
          outputs: dict, related_entity_type: str | None = None,
          related_entity_id: str | None = None, notes: str | None = None) -> None:
    conn.execute(
        """INSERT INTO audit_log
           (log_id, timestamp, agent, action, tool_name, inputs, outputs,
            related_entity_type, related_entity_id, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            f"log_{uuid.uuid4().hex[:12]}", _now(), AGENT_NAME, action, tool_name,
            json.dumps(inputs), json.dumps(outputs), related_entity_type,
            related_entity_id, notes,
        ),
    )
    conn.commit()


def get_uncategorized_txns(conn: sqlite3.Connection) -> list[dict]:
    """Read-only. Every bank_feed row not yet posted to the GL."""
    rows = conn.execute(
        """SELECT feed_id, posted_date, description, amount,
                  suggested_account_id, confidence_score
           FROM bank_feed WHERE categorized = 0 ORDER BY posted_date"""
    ).fetchall()
    result = [dict(r) for r in rows]
    _log(conn, "read", "get_uncategorized_txns", {}, {"count": len(result)})
    return result


def get_chart_of_accounts(conn: sqlite3.Connection) -> list[dict]:
    """Read-only. The accounts the agent is allowed to post against."""
    rows = conn.execute(
        """SELECT account_id, code, name, type, normal_balance
           FROM chart_of_accounts WHERE type != 'equity' ORDER BY code"""
    ).fetchall()
    result = [dict(r) for r in rows]
    _log(conn, "read", "get_chart_of_accounts", {}, {"count": len(result)})
    return result


def post_categorization(conn: sqlite3.Connection, feed_id: str, account_id: str,
                          confidence: float, memo: str,
                          department_id: str | None = None) -> dict:
    """Write. Posts a balanced JE (Dr expense/cogs account, Cr Cash) for one
    bank_feed row and marks it categorized. REFUSES below CONFIDENCE_THRESHOLD
    — this is the code-level control, not a suggestion to the model."""
    inputs = {"feed_id": feed_id, "account_id": account_id, "confidence": confidence,
               "memo": memo, "department_id": department_id}

    if confidence < CONFIDENCE_THRESHOLD:
        outputs = {"status": "refused", "reason": (
            f"confidence {confidence} is below the {CONFIDENCE_THRESHOLD} posting "
            "threshold — call escalate_for_review instead of posting this one")}
        _log(conn, "write_refused", "post_categorization", inputs, outputs, "bank_feed", feed_id)
        return outputs

    feed_row = conn.execute(
        "SELECT * FROM bank_feed WHERE feed_id = ?", (feed_id,)
    ).fetchone()
    if feed_row is None:
        outputs = {"status": "error", "reason": f"no bank_feed row with feed_id={feed_id}"}
        _log(conn, "write_error", "post_categorization", inputs, outputs, "bank_feed", feed_id)
        return outputs
    if feed_row["categorized"]:
        outputs = {"status": "error", "reason": "already categorized — refusing to double-post"}
        _log(conn, "write_error", "post_categorization", inputs, outputs, "bank_feed", feed_id)
        return outputs

    acct = conn.execute(
        "SELECT account_id, normal_balance, type FROM chart_of_accounts WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    if acct is None:
        outputs = {"status": "error", "reason": f"unknown account_id={account_id}"}
        _log(conn, "write_error", "post_categorization", inputs, outputs, "bank_feed", feed_id)
        return outputs

    amount = round(float(feed_row["amount"]), 2)
    je_id = f"je_bk_{uuid.uuid4().hex[:10]}"
    period = feed_row["posted_date"][:7]  # "YYYY-MM-DD" -> "YYYY-MM"

    conn.execute(
        """INSERT INTO gl_transactions
           (txn_id, je_id, txn_date, account_id, department_id, amount, debit_credit,
            memo, source_type, source_id, period, entered_by_agent, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"txn_{uuid.uuid4().hex[:12]}", je_id, feed_row["posted_date"], account_id,
         department_id, amount, "debit", memo, "bank_feed", feed_id, period, AGENT_NAME, "posted"),
    )
    conn.execute(
        """INSERT INTO gl_transactions
           (txn_id, je_id, txn_date, account_id, department_id, amount, debit_credit,
            memo, source_type, source_id, period, entered_by_agent, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"txn_{uuid.uuid4().hex[:12]}", je_id, feed_row["posted_date"], "cash", None,
         amount, "credit", memo, "bank_feed", feed_id, period, AGENT_NAME, "posted"),
    )
    conn.execute(
        "UPDATE bank_feed SET categorized = 1, suggested_account_id = ?, confidence_score = ? WHERE feed_id = ?",
        (account_id, confidence, feed_id),
    )
    conn.commit()

    outputs = {"status": "posted", "je_id": je_id, "amount": amount, "account_id": account_id}
    _log(conn, "write", "post_categorization", inputs, outputs, "gl_transactions", je_id)
    return outputs


def escalate_for_review(conn: sqlite3.Connection, feed_id: str, reason: str) -> dict:
    """Write (governance, not GL). Files an approvals-table entry for a human
    to resolve — used when the agent cannot responsibly reach the confidence
    threshold on its own, e.g. a genuinely ambiguous vendor description.

    Idempotent by design: get_uncategorized_txns() filters on
    bank_feed.categorized = 0, which escalation deliberately does NOT flip
    (the transaction is still unposted, awaiting a human decision) — so a
    re-run of this agent against the same period sees the same row as
    "uncategorized" and will naturally try to escalate it again. Found live
    during M5's orchestrator run: a second Bookkeeping pass filed a second,
    near-duplicate approval for each of the three already-escalated
    transactions, double-counting them in Controller's materiality math.
    Rather than trusting the model not to re-escalate, refuse the duplicate
    in code and hand back the existing approval."""
    inputs = {"feed_id": feed_id, "reason": reason}

    feed_row = conn.execute("SELECT * FROM bank_feed WHERE feed_id = ?", (feed_id,)).fetchone()
    if feed_row is None:
        outputs = {"status": "error", "reason": f"no bank_feed row with feed_id={feed_id}"}
        _log(conn, "write_error", "escalate_for_review", inputs, outputs, "bank_feed", feed_id)
        return outputs

    existing = conn.execute(
        """SELECT approval_id FROM approvals
           WHERE entity_type = 'bank_feed' AND entity_id = ? AND status = 'pending'""",
        (feed_id,),
    ).fetchone()
    if existing is not None:
        outputs = {"status": "already_escalated", "approval_id": existing["approval_id"]}
        _log(conn, "escalate_skipped_duplicate", "escalate_for_review", inputs, outputs, "bank_feed", feed_id,
             notes="a pending approval already exists for this transaction — not filing a duplicate")
        return outputs

    approval_id = f"appr_{uuid.uuid4().hex[:10]}"
    conn.execute(
        """INSERT INTO approvals
           (approval_id, action_type, entity_type, entity_id, requested_by, status, notes)
           VALUES (?,?,?,?,?,?,?)""",
        (approval_id, "categorize_ambiguous_txn", "bank_feed", feed_id, AGENT_NAME, "pending", reason),
    )
    conn.commit()

    outputs = {"status": "escalated", "approval_id": approval_id}
    _log(conn, "escalate", "escalate_for_review", inputs, outputs, "bank_feed", feed_id, notes=reason)
    return outputs
