"""Controller Agent's tool surface.

Structural point, enforced here rather than in the system prompt: the
Controller holds ZERO write tools into any other agent's domain data.
It cannot post a journal entry, edit an ap_invoice, or touch bank_feed —
only Bookkeeping/AP/AR/Payroll can, because only they own that data
(segregation of duties, Section 10). When the Controller finds a problem
that lives in someone else's domain, its only lever is
raise_correction_request — a row in correction_requests, not a fix.

mark_period_ready_for_reporting is the milestone's structural gate: the
code refuses to mark a period ready unless the trial balance actually
balances AND the dollar exposure of unresolved open items is below a
materiality threshold. That refusal happens whether or not the model
argues the period looks fine — the check is arithmetic, not persuasion.
"""

from __future__ import annotations

import calendar
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import statements, aging  # noqa: E402

AGENT_NAME = "controller_agent"
DEFAULT_MATERIALITY_THRESHOLD = 25_000.0


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


def check_trial_balance(conn: sqlite3.Connection, period: str) -> dict:
    """Read-only. The single deterministic reconciliation check everything
    else depends on: does debit == credit for this period, account by
    account. If this doesn't balance, nothing downstream can be trusted."""
    tb = statements.trial_balance(conn, period)
    outputs = {
        "period": period, "balanced": tb["balanced"],
        "total_debit": tb["total_debit"], "total_credit": tb["total_credit"],
        "diff": round(tb["total_debit"] - tb["total_credit"], 2),
    }
    _log(conn, "read", "check_trial_balance", {"period": period}, outputs)
    return outputs


def check_subledger_tie_out(conn: sqlite3.Connection, period: str) -> dict:
    """Read-only. Do the AR/AP control accounts in the GL match the sum of
    open items in their respective subledgers as of this period's end?"""
    year, month = int(period[:4]), int(period[5:7])
    last_day = calendar.monthrange(year, month)[1]
    as_of = f"{period}-{last_day:02d}"

    ar_gl = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN debit_credit='debit' THEN amount ELSE -amount END),0)
           FROM gl_transactions WHERE account_id='ar' AND period <= ?""", (period,)
    ).fetchone()[0]
    ar_subledger = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM ar_invoices WHERE status='open' AND invoice_date <= ?", (as_of,)
    ).fetchone()[0]

    ap_gl = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN debit_credit='credit' THEN amount ELSE -amount END),0)
           FROM gl_transactions WHERE account_id='ap' AND period <= ?""", (period,)
    ).fetchone()[0]
    ap_subledger = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM ap_invoices WHERE status='unpaid' AND invoice_date <= ?", (as_of,)
    ).fetchone()[0]

    outputs = {
        "period": period,
        "ar": {"gl_balance": round(ar_gl, 2), "subledger_total": round(ar_subledger, 2),
               "ties_out": abs(ar_gl - ar_subledger) < 0.01},
        "ap": {"gl_balance": round(ap_gl, 2), "subledger_total": round(ap_subledger, 2),
               "ties_out": abs(ap_gl - ap_subledger) < 0.01},
    }
    _log(conn, "read", "check_subledger_tie_out", {"period": period}, outputs)
    return outputs


def review_exceptions(conn: sqlite3.Connection, period: str) -> dict:
    """Read-only. Pulls together everything currently unresolved for this
    close: bookkeeping items still pending human review, and a fresh run of
    the rule-based duplicate-invoice detector (Controller doesn't wait to be
    told about a duplicate — it can run the same deterministic check AP
    would, which is exactly how it can surface a problem nobody flagged for
    it yet)."""
    pending_approvals = conn.execute(
        """SELECT a.approval_id, a.entity_id, a.action_type, a.notes, b.description, b.amount
           FROM approvals a LEFT JOIN bank_feed b ON b.feed_id = a.entity_id
           WHERE a.status = 'pending' AND (b.posted_date LIKE ? OR b.posted_date IS NULL)""",
        (f"{period}%",),
    ).fetchall()
    pending_list = [dict(r) for r in pending_approvals]
    pending_total = round(sum(r["amount"] or 0 for r in pending_list), 2)

    # The raw detector is deliberately naive (Section 9: it's the pattern
    # matcher, not the adjudicator) — it doesn't know a candidate pair was
    # already resolved. That's this tool's job: exclude any pair where
    # either invoice is already 'disputed' (resolve_duplicate_invoice's
    # outcome), so a fixed duplicate doesn't get re-raised as a new
    # correction request on every subsequent close review.
    duplicate_candidates = aging.flag_duplicate_ap_invoices(conn)
    period_duplicates = []
    for cand in duplicate_candidates:
        row_a = conn.execute(
            "SELECT invoice_date, status FROM ap_invoices WHERE invoice_id = ?", (cand["invoice_a"],)
        ).fetchone()
        row_b = conn.execute(
            "SELECT status FROM ap_invoices WHERE invoice_id = ?", (cand["invoice_b"],)
        ).fetchone()
        if row_a and row_a["invoice_date"].startswith(period) and row_a["status"] != "disputed" \
                and (row_b is None or row_b["status"] != "disputed"):
            period_duplicates.append(cand)
    duplicate_total = round(sum(c["amount"] for c in period_duplicates), 2)

    open_correction_requests = conn.execute(
        "SELECT * FROM correction_requests WHERE period = ? AND status IN ('open','disputed')", (period,)
    ).fetchall()

    outputs = {
        "period": period,
        "pending_approvals": pending_list,
        "pending_approvals_total": pending_total,
        "duplicate_invoice_candidates": period_duplicates,
        "duplicate_invoice_total": duplicate_total,
        "open_correction_requests": [dict(r) for r in open_correction_requests],
        "total_unresolved_exposure": round(pending_total + duplicate_total, 2),
    }
    _log(conn, "read", "review_exceptions", {"period": period}, outputs)
    return outputs


def raise_correction_request(conn: sqlite3.Connection, period: str, target_agent: str,
                               finding: str, evidence: str, requested_action: str,
                               materiality_amount: float) -> dict:
    """Write — but ONLY to correction_requests. The Controller cannot fix
    another agent's data itself; this is the one lever it has (Section 10)."""
    inputs = {"period": period, "target_agent": target_agent, "finding": finding,
               "evidence": evidence, "requested_action": requested_action,
               "materiality_amount": materiality_amount}

    request_id = f"cr_{uuid.uuid4().hex[:10]}"
    conn.execute(
        """INSERT INTO correction_requests
           (request_id, period, raised_by_agent, target_agent, finding, evidence,
            requested_action, materiality_amount, status, round, created_at, resolved_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (request_id, period, AGENT_NAME, target_agent, finding, evidence,
         requested_action, materiality_amount, "open", 1, _now(), None),
    )
    conn.commit()

    outputs = {"status": "raised", "request_id": request_id}
    _log(conn, "write", "raise_correction_request", inputs, outputs, "correction_requests", request_id)
    return outputs


def mark_period_ready_for_reporting(conn: sqlite3.Connection, period: str,
                                      materiality_threshold: float = DEFAULT_MATERIALITY_THRESHOLD) -> dict:
    """Write (approval-required tier) — but the approval is a code-enforced
    precondition, not the model's opinion. Refuses if the trial balance
    doesn't balance, or if unresolved exceptions exceed materiality. Only
    touches period_status.approved_by — never gl_transactions or any other
    agent's domain. Does NOT close the period; final close is a human gate
    (Section 4), built in M5.

    Both refusal paths also clear approved_by back to NULL. Found live
    during M5: a period that had been approved on an earlier pass stayed
    marked approved_by='controller_agent' even after a later pass refused
    to re-approve it, because the original code only ever *set* the flag,
    never cleared it. The orchestrator's Gate 1 trusts this column as the
    independent re-check of Controller's verdict (Section 10), so a stale
    'approved' from a prior run is exactly the false-positive that check
    exists to prevent — approval must reflect the most recent run's
    verdict, not any run's verdict."""
    inputs = {"period": period, "materiality_threshold": materiality_threshold}

    tb = statements.trial_balance(conn, period)
    if not tb["balanced"]:
        conn.execute("UPDATE period_status SET approved_by = NULL WHERE period = ?", (period,))
        conn.commit()
        outputs = {"status": "refused", "reason": (
            f"trial balance does not balance for {period}: debit {tb['total_debit']} != "
            f"credit {tb['total_credit']} (diff {round(tb['total_debit'] - tb['total_credit'], 2)}). "
            "Cannot mark ready for reporting.")}
        _log(conn, "write_refused", "mark_period_ready_for_reporting", inputs, outputs, "period_status", period)
        return outputs

    open_crs = conn.execute(
        "SELECT COALESCE(SUM(materiality_amount),0) FROM correction_requests WHERE period=? AND status IN ('open','disputed')",
        (period,),
    ).fetchone()[0]
    pending_appr = conn.execute(
        """SELECT COALESCE(SUM(b.amount),0) FROM approvals a JOIN bank_feed b ON b.feed_id = a.entity_id
           WHERE a.status='pending' AND b.posted_date LIKE ?""", (f"{period}%",)
    ).fetchone()[0]
    exposure = round((open_crs or 0) + (pending_appr or 0), 2)

    if exposure > materiality_threshold:
        conn.execute("UPDATE period_status SET approved_by = NULL WHERE period = ?", (period,))
        conn.commit()
        outputs = {"status": "refused", "reason": (
            f"unresolved exposure ${exposure:,.2f} exceeds the ${materiality_threshold:,.2f} "
            "materiality threshold — open correction requests and/or pending approvals must be "
            "resolved (or the threshold explicitly overridden by a human) before this period can "
            "be marked ready for reporting."), "exposure": exposure}
        _log(conn, "write_refused", "mark_period_ready_for_reporting", inputs, outputs, "period_status", period)
        return outputs

    conn.execute("UPDATE period_status SET approved_by = ? WHERE period = ?", (AGENT_NAME, period))
    conn.commit()

    outputs = {"status": "marked_ready", "period": period, "exposure_within_threshold": exposure}
    _log(conn, "write", "mark_period_ready_for_reporting", inputs, outputs, "period_status", period)
    return outputs
