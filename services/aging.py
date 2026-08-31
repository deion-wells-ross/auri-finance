"""AP/AR aging and rule-based duplicate detection — all deterministic.

Duplicate detection is a rule-based match (Section 9's table is explicit
that this is code, not judgment): same vendor, same amount within tolerance,
invoice dates within a window. AP Agent's actual job (M2+) is deciding what
to DO about a flagged candidate — dispute it, confirm it's legitimate — not
finding it in the first place.
"""

from __future__ import annotations
import sqlite3
from datetime import date, timedelta

AGING_BUCKETS = [(0, 30), (31, 60), (61, 90), (91, 10_000)]


def _bucket_label(days: int) -> str:
    for lo, hi in AGING_BUCKETS:
        if lo <= days <= hi:
            return f"{lo}-{hi}" if hi < 10_000 else f"{lo}+"
    return "unknown"


def ap_aging(conn: sqlite3.Connection, as_of_date: str) -> dict:
    rows = conn.execute(
        """SELECT ap.invoice_id, ap.vendor_id, v.name AS vendor_name, ap.amount, ap.due_date
           FROM ap_invoices ap JOIN vendors v ON ap.vendor_id = v.vendor_id
           WHERE ap.status = 'unpaid'"""
    ).fetchall()
    as_of = date.fromisoformat(as_of_date)
    by_vendor: dict[str, dict] = {}
    for r in rows:
        days_overdue = (as_of - date.fromisoformat(r["due_date"])).days
        bucket = _bucket_label(max(days_overdue, 0))
        v = by_vendor.setdefault(r["vendor_name"], {"vendor_id": r["vendor_id"], "buckets": {}, "total": 0.0})
        v["buckets"][bucket] = round(v["buckets"].get(bucket, 0.0) + r["amount"], 2)
        v["total"] = round(v["total"] + r["amount"], 2)
    total_unpaid = round(sum(v["total"] for v in by_vendor.values()), 2)
    return {"as_of_date": as_of_date, "by_vendor": by_vendor, "total_unpaid": total_unpaid}


def ar_aging(conn: sqlite3.Connection, as_of_date: str) -> dict:
    rows = conn.execute(
        """SELECT ar.invoice_id, ar.customer_id, c.name AS customer_name, ar.amount, ar.due_date
           FROM ar_invoices ar JOIN customers c ON ar.customer_id = c.customer_id
           WHERE ar.status = 'open'"""
    ).fetchall()
    as_of = date.fromisoformat(as_of_date)
    by_customer: dict[str, dict] = {}
    for r in rows:
        days_overdue = (as_of - date.fromisoformat(r["due_date"])).days
        bucket = _bucket_label(max(days_overdue, 0))
        c = by_customer.setdefault(
            r["customer_name"], {"customer_id": r["customer_id"], "buckets": {}, "total": 0.0}
        )
        c["buckets"][bucket] = round(c["buckets"].get(bucket, 0.0) + r["amount"], 2)
        c["total"] = round(c["total"] + r["amount"], 2)
    total_open = round(sum(c["total"] for c in by_customer.values()), 2)
    for c in by_customer.values():
        c["concentration_pct"] = round(c["total"] / total_open * 100, 2) if total_open else 0.0
    return {"as_of_date": as_of_date, "by_customer": by_customer, "total_open": total_open}


def flag_duplicate_ap_invoices(conn: sqlite3.Connection, window_days: int = 7,
                                amount_tolerance: float = 0.01) -> list[dict]:
    """Returns candidate duplicate pairs: same vendor, amounts within
    tolerance, invoice dates within window_days of each other. Does not
    look at ap_invoices.is_duplicate_of — that column records a HUMAN/agent
    conclusion after review; this function is the detector that feeds it."""
    rows = conn.execute(
        "SELECT invoice_id, vendor_id, invoice_date, amount FROM ap_invoices ORDER BY vendor_id, invoice_date"
    ).fetchall()
    candidates = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if b["vendor_id"] != a["vendor_id"]:
                continue
            gap = abs((date.fromisoformat(b["invoice_date"]) - date.fromisoformat(a["invoice_date"])).days)
            if gap > window_days:
                continue
            if abs(a["amount"] - b["amount"]) <= amount_tolerance:
                candidates.append({
                    "vendor_id": a["vendor_id"],
                    "invoice_a": a["invoice_id"], "invoice_b": b["invoice_id"],
                    "amount": a["amount"], "days_apart": gap,
                })
    return candidates
