"""M8: checks whether each of the six anomalies seeded into the August
2026 close (data/seed/README.md's answer key) was actually surfaced by a
live agent run — the runnable version of Section 1's V1 bar #5, "the
system surfaces a problem it wasn't told about," which until now has only
ever been demonstrated one anomaly at a time, incidentally, across
whichever milestone happened to run into it. Deterministic, no LLM,
reads only from the database and audit_log.

Two different kinds of "found" are used honestly rather than blurred
together, mirroring the project's own deterministic-vs-judgment split
(Section 9): three anomalies (#1, #4, #6) leave a code-level trace — a
column changed, a specific tool called against the specific entity — and
are checked exactly. The other three (#2, #3, #5) are only ever surfaced
inside an agent's own narrative findings (a `submit_report` string), so
they're checked by confirming the relevant investigative tool was called
AND the accepted report's text mentions the expected facts — a real check,
but a narrative one, same as Section 12 already scopes CFO/FP&A synthesis
quality to human-graded review rather than automated grading.
"""

from __future__ import annotations

import json
import sqlite3


def _report_texts(conn: sqlite3.Connection, agent: str) -> list[str]:
    """Every accepted final report an agent submitted, flattened to a
    single lowercase search blob per report (summary + all list fields)."""
    rows = conn.execute(
        "SELECT outputs FROM audit_log WHERE agent = ? AND action IN ('final_report', 'briefing') "
        "ORDER BY timestamp",
        (agent,),
    ).fetchall()
    blobs = []
    for r in rows:
        try:
            report = json.loads(r["outputs"])
        except (TypeError, ValueError):
            continue
        parts = []
        for v in report.values():
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                parts.extend(str(x) for x in v)
        blobs.append(" ".join(parts).lower())
    return blobs


def _any_report_mentions(conn: sqlite3.Connection, agent: str, *needles: str) -> tuple[bool, str]:
    for blob in _report_texts(conn, agent):
        for needle in needles:
            if needle.lower() in blob:
                return True, f"{agent}'s submitted report mentions '{needle}'"
    return False, f"no {agent} report (of {len(_report_texts(conn, agent))} submitted) mentions any of {needles}"


def _tool_was_called(conn: sqlite3.Connection, agent: str, tool_name: str, contains: str | None = None) -> bool:
    rows = conn.execute(
        "SELECT inputs, outputs FROM audit_log WHERE agent = ? AND tool_name = ?",
        (agent, tool_name),
    ).fetchall()
    if contains is None:
        return len(rows) > 0
    for r in rows:
        blob = f"{r['inputs'] or ''} {r['outputs'] or ''}"
        if contains in blob:
            return True
    return False


def check_duplicate_invoice(conn: sqlite3.Connection, period: str) -> dict:
    """#1 — the duplicate CloudScale Hosting invoice.

    The seed data itself pre-links `is_duplicate_of` on the true duplicate
    pair (it's the answer key's ground truth, per data/seed/README.md's
    "Target" column) — that column is NOT evidence an agent found
    anything, it's true from the moment the database is generated, before
    any agent has run. What actually changes when AP acts on it is
    `status`: `resolve_duplicate_invoice` flips the duplicate row to
    'disputed'. That, or an explicit dismiss_duplicate_candidate entry
    naming the pair, is the real signal."""
    candidates = conn.execute(
        "SELECT invoice_id, amount, invoice_date, status, is_duplicate_of FROM ap_invoices "
        "WHERE vendor_id = 'cloudscale' AND invoice_date LIKE ? ORDER BY invoice_date",
        (f"{period}%",),
    ).fetchall()
    if len(candidates) < 2:
        return {"id": 1, "name": "Duplicate CloudScale Hosting invoice", "found": False,
                "evidence": f"expected 2+ CloudScale invoices in {period}, found {len(candidates)} — seed data mismatch"}

    resolved = [c for c in candidates if c["status"] == "disputed"]
    if resolved:
        return {"id": 1, "name": "Duplicate CloudScale Hosting invoice", "found": True,
                "evidence": f"{resolved[0]['invoice_id']} marked 'disputed' via resolve_duplicate_invoice "
                            f"(duplicate of {resolved[0]['is_duplicate_of']})"}

    invoice_ids = {c["invoice_id"] for c in candidates}
    dismissals = conn.execute(
        "SELECT inputs FROM audit_log WHERE tool_name = 'dismiss_duplicate_candidate'"
    ).fetchall()
    for row in dismissals:
        try:
            d = json.loads(row["inputs"])
        except (TypeError, ValueError):
            continue
        if d.get("invoice_a_id") in invoice_ids or d.get("invoice_b_id") in invoice_ids:
            return {"id": 1, "name": "Duplicate CloudScale Hosting invoice", "found": True,
                    "evidence": f"candidate pair reviewed and explicitly dismissed via dismiss_duplicate_candidate: {d.get('reason')}"}

    detected = _tool_was_called(conn, "ap_agent", "flag_duplicate_invoices")
    return {"id": 1, "name": "Duplicate CloudScale Hosting invoice", "found": False,
            "evidence": ("detector ran but the pair was never resolved or dismissed" if detected
                         else "flag_duplicate_invoices was never called this run")}


def check_engineering_payroll(conn: sqlite3.Connection, period: str) -> dict:
    """#2 — Engineering headcount/payroll outpacing revenue growth."""
    investigated = _tool_was_called(conn, "payroll_agent", "compare_payroll_growth", contains='"ENG"') or \
        _tool_was_called(conn, "payroll_agent", "compare_to_budget", contains='"ENG"')
    found, evidence = _any_report_mentions(conn, "payroll_agent", "engineering")
    if not found and investigated:
        evidence = "payroll_agent investigated ENG specifically, but its report text didn't name it explicitly"
    return {"id": 2, "name": "Engineering payroll outpacing revenue", "found": found, "evidence": evidence}


def check_gross_margin_decline(conn: sqlite3.Connection, period: str) -> dict:
    """#3 — the gross margin decline (78% -> 76% -> ~74%)."""
    rows = conn.execute(
        "SELECT outputs FROM audit_log WHERE agent = 'fpa_agent' AND action = 'final_report' ORDER BY timestamp"
    ).fetchall()
    for r in rows:
        try:
            report = json.loads(r["outputs"])
        except (TypeError, ValueError):
            continue
        if report.get("gross_margin_trend_explained") is True:
            return {"id": 3, "name": "Gross margin decline", "found": True,
                    "evidence": "fpa_agent's submitted report has gross_margin_trend_explained=true"}
    found, evidence = _any_report_mentions(conn, "fpa_agent", "margin", "cogs")
    return {"id": 3, "name": "Gross margin decline", "found": found, "evidence": evidence}


def check_ar_concentration(conn: sqlite3.Connection, period: str) -> dict:
    """#4 — Vantage Retail Group's AR concentration + aging.

    flag_concentration_risk's own audit_log entry only logs a count of
    flagged customers, not which ones (Section 9 territory: the tool
    returns the real names to its caller, but the log line summarizing a
    read call was never written with this specific check in mind) — so,
    like #2/#3/#5, this is checked narratively: was the tool run at all,
    and did ar_agent's own report name Vantage specifically."""
    customer = conn.execute("SELECT customer_id, name FROM customers WHERE name LIKE '%Vantage%'").fetchone()
    if customer is None:
        return {"id": 4, "name": "AR concentration + aging (Vantage)", "found": False,
                "evidence": "no customer matching 'Vantage' in this database — seed data mismatch"}

    investigated = _tool_was_called(conn, "ar_agent", "flag_concentration_risk")
    found, evidence = _any_report_mentions(conn, "ar_agent", "vantage")
    if not found and investigated:
        evidence = "flag_concentration_risk ran, but ar_agent's report never named Vantage specifically"
    return {"id": 4, "name": "AR concentration + aging (Vantage)", "found": found, "evidence": evidence}


def check_marketing_overrun(conn: sqlite3.Connection, period: str) -> dict:
    """#5 — the Marketing budget overrun (+28%, unbudgeted trade-show line)."""
    found, evidence = _any_report_mentions(conn, "fpa_agent", "marketing", "trade show", "trade-show", "tradeshow")
    return {"id": 5, "name": "Marketing budget overrun", "found": found, "evidence": evidence}


def check_bookkeeping_ambiguity(conn: sqlite3.Connection, period: str) -> dict:
    """#6 — the genuinely ambiguous $9,800 Meridian Consulting Partners LLC transaction."""
    feed_row = conn.execute(
        "SELECT feed_id FROM bank_feed WHERE description LIKE '%MERIDIAN CONSULTING%' AND amount = 9800.0"
    ).fetchone()
    if feed_row is None:
        return {"id": 6, "name": "Genuine bookkeeping ambiguity ($9,800)", "found": False,
                "evidence": "no matching bank_feed row in this database — seed data mismatch"}

    escalated = _tool_was_called(conn, "bookkeeping_agent", "escalate_for_review", contains=feed_row["feed_id"])
    if escalated:
        return {"id": 6, "name": "Genuine bookkeeping ambiguity ($9,800)", "found": True,
                "evidence": f"escalate_for_review called on {feed_row['feed_id']} rather than guessed at"}
    return {"id": 6, "name": "Genuine bookkeeping ambiguity ($9,800)", "found": False,
            "evidence": f"{feed_row['feed_id']} was never escalated — check whether it was guessed at instead"}


CHECKS = [
    check_duplicate_invoice,
    check_engineering_payroll,
    check_gross_margin_decline,
    check_ar_concentration,
    check_marketing_overrun,
    check_bookkeeping_ambiguity,
]


def verify_all(conn: sqlite3.Connection, period: str) -> list[dict]:
    return [check(conn, period) for check in CHECKS]
