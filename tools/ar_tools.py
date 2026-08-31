"""AR Agent's tool surface.

Deliberately read-only. Per Section 3, AR's autonomous scope is read/flag/
prioritize; anything customer-facing (actually contacting a customer about
a bill) is review-required and out of scope for V1 entirely — there's no
tool for it. That's not an oversight: an agent that can only analyze and
recommend, with zero ability to act on a customer relationship, is the
correct amount of autonomy for this domain at this stage.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import aging  # noqa: E402

AGENT_NAME = "ar_agent"


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
        (f"log_{uuid.uuid4().hex[:12]}", _now(), AGENT_NAME, action, tool_name,
         json.dumps(inputs), json.dumps(outputs), related_entity_type, related_entity_id, notes),
    )
    conn.commit()


def get_ar_aging(conn: sqlite3.Connection, as_of_date: str) -> dict:
    result = aging.ar_aging(conn, as_of_date)
    _log(conn, "read", "get_ar_aging", {"as_of_date": as_of_date}, {"total_open": result["total_open"]})
    return result


def get_customer_payment_history(conn: sqlite3.Connection, customer_id: str) -> dict:
    """Read-only. This dataset has no stored paid_date, so 'payment
    behavior' is approximated from invoice status: how many of this
    customer's invoices ended up 'overdue' vs cleanly 'paid'."""
    rows = conn.execute(
        "SELECT invoice_id, invoice_date, due_date, amount, status FROM ar_invoices WHERE customer_id = ? ORDER BY invoice_date",
        (customer_id,),
    ).fetchall()
    invoices = [dict(r) for r in rows]
    total = len(invoices)
    overdue_count = sum(1 for r in invoices if r["status"] == "overdue")
    outputs = {
        "customer_id": customer_id, "invoice_count": total, "overdue_count": overdue_count,
        "overdue_rate_pct": round(overdue_count / total * 100, 1) if total else None,
        "invoices": invoices,
    }
    _log(conn, "read", "get_customer_payment_history", {"customer_id": customer_id},
         {"invoice_count": total, "overdue_count": overdue_count})
    return outputs


def flag_concentration_risk(conn: sqlite3.Connection, as_of_date: str, threshold_pct: float = 25.0) -> list[dict]:
    """Read-only. Which customers make up more than threshold_pct of total
    open AR — a single customer that large is a real collection/business
    risk regardless of whether they're current or overdue."""
    ar = aging.ar_aging(conn, as_of_date)
    flagged = [
        {"customer_name": name, **info}
        for name, info in ar["by_customer"].items()
        if info["concentration_pct"] > threshold_pct
    ]
    flagged.sort(key=lambda c: c["concentration_pct"], reverse=True)
    _log(conn, "read", "flag_concentration_risk", {"as_of_date": as_of_date, "threshold_pct": threshold_pct},
         {"flagged_count": len(flagged)})
    return flagged


def prioritize_collections(conn: sqlite3.Connection, as_of_date: str) -> list[dict]:
    """Read-only. Ranks overdue customer balances by a simple, transparent
    priority score (days overdue x dollar amount) so the highest-dollar,
    most-overdue accounts surface first — the arithmetic is deterministic;
    deciding what to actually DO about #1 on this list is the agent's job."""
    ar = aging.ar_aging(conn, as_of_date)
    as_of = date.fromisoformat(as_of_date)
    rows = conn.execute(
        """SELECT ar.customer_id, c.name AS customer_name, ar.invoice_id, ar.amount, ar.due_date
           FROM ar_invoices ar JOIN customers c ON ar.customer_id = c.customer_id
           WHERE ar.status IN ('open','overdue')"""
    ).fetchall()
    scored = []
    for r in rows:
        days_overdue = max((as_of - date.fromisoformat(r["due_date"])).days, 0)
        if days_overdue == 0:
            continue
        concentration = ar["by_customer"].get(r["customer_name"], {}).get("concentration_pct", 0.0)
        scored.append({
            "customer_id": r["customer_id"], "customer_name": r["customer_name"],
            "invoice_id": r["invoice_id"], "amount": r["amount"], "days_overdue": days_overdue,
            "concentration_pct": concentration,
            "priority_score": round(days_overdue * r["amount"], 2),
        })
    scored.sort(key=lambda x: x["priority_score"], reverse=True)
    _log(conn, "read", "prioritize_collections", {"as_of_date": as_of_date}, {"ranked_count": len(scored)})
    return scored
