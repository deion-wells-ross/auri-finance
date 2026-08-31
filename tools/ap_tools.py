"""AP Agent's tool surface.

Scope, per Section 3/10: AP owns the vendor-invoice lifecycle, so unlike
Controller it DOES get write tools — but only into its own domain
(ap_invoices, and the compensating GL entries that correcting an AP error
requires). resolve_duplicate_invoice is the one meaningful write here: it's
also how the correction-request protocol actually closes a loop, not just
proves it can open one — Controller (M3) files a correction_request against
'ap_agent'; this is the tool that lets AP accept it and act.

flag_unusual_charges is deliberately a DIFFERENT signal from the duplicate
detector: a same-vendor/same-amount/near-date match is what catches a
duplicate; a charge that's some multiple of a vendor's trailing average is a
different kind of risk (price change, scope creep, fraud) and is expected to
come back empty here, since CloudScale's rising usage fee is a real trend,
not a spike — that's a feature of the check, not a bug.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import aging  # noqa: E402

AGENT_NAME = "ap_agent"


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


def get_ap_aging(conn: sqlite3.Connection, as_of_date: str) -> dict:
    result = aging.ap_aging(conn, as_of_date)
    _log(conn, "read", "get_ap_aging", {"as_of_date": as_of_date}, {"total_unpaid": result["total_unpaid"]})
    return result


def get_vendor_history(conn: sqlite3.Connection, vendor_id: str, before_period: str | None = None) -> dict:
    """Read-only. All invoices for this vendor, plus the trailing average
    amount (excluding before_period and anything on/after it, when given —
    that's what makes 'is this charge unusual' a fair trailing comparison
    rather than data leakage from the period being reviewed)."""
    rows = conn.execute(
        "SELECT invoice_id, invoice_date, amount, status FROM ap_invoices WHERE vendor_id = ? ORDER BY invoice_date",
        (vendor_id,),
    ).fetchall()
    invoices = [dict(r) for r in rows]
    prior = [r for r in invoices if before_period is None or r["invoice_date"] < before_period]
    trailing_avg = round(sum(r["amount"] for r in prior) / len(prior), 2) if prior else None

    outputs = {"vendor_id": vendor_id, "invoice_count": len(invoices),
               "trailing_invoice_count": len(prior), "trailing_avg_amount": trailing_avg,
               "invoices": invoices}
    _log(conn, "read", "get_vendor_history", {"vendor_id": vendor_id, "before_period": before_period},
         {"trailing_avg_amount": trailing_avg, "invoice_count": len(invoices)})
    return outputs


def flag_unusual_charges(conn: sqlite3.Connection, period: str, multiple: float = 2.0) -> list[dict]:
    """Read-only. Per-invoice-in-period comparison against that vendor's
    trailing average (prior periods only). Flags a charge above `multiple`x
    trailing average, or an invoice from a vendor with zero prior history."""
    invoices = conn.execute(
        "SELECT invoice_id, vendor_id, amount, invoice_date FROM ap_invoices WHERE invoice_date LIKE ?",
        (f"{period}%",),
    ).fetchall()
    flags = []
    for inv in invoices:
        hist = get_vendor_history(conn, inv["vendor_id"], before_period=period)
        if hist["trailing_avg_amount"] is None:
            flags.append({"invoice_id": inv["invoice_id"], "vendor_id": inv["vendor_id"],
                           "amount": inv["amount"], "reason": "new vendor — no prior invoice history"})
        elif inv["amount"] > hist["trailing_avg_amount"] * multiple:
            flags.append({"invoice_id": inv["invoice_id"], "vendor_id": inv["vendor_id"],
                           "amount": inv["amount"], "trailing_avg": hist["trailing_avg_amount"],
                           "reason": f"charge is {round(inv['amount'] / hist['trailing_avg_amount'], 2)}x trailing average"})
    _log(conn, "read", "flag_unusual_charges", {"period": period, "multiple": multiple}, {"flag_count": len(flags)})
    return flags


def flag_duplicate_invoices(conn: sqlite3.Connection) -> list[dict]:
    """Read-only. AP's own independent run of the rule-based detector — it
    shouldn't only find a duplicate because Controller already told it to
    look. Excludes pairs already adjudicated: either invoice 'disputed' via
    a prior resolve_duplicate_invoice call, or the pair explicitly reviewed
    and dismissed as NOT a duplicate via dismiss_duplicate_candidate. The
    raw detector doesn't know either of those, by design (Section 9), so
    this tool is where both filters belong.

    Found live in M5: without the dismissal filter, a legitimate pair (two
    genuinely separate monthly invoices that happen to fall inside the
    detector's tolerance window) resurfaces as a fresh "candidate" on every
    single agent run with no memory of having been correctly cleared before
    — and since the agent's judgment on a borderline case isn't perfectly
    stable run to run, repeatedly re-litigating a settled question is real
    risk, not just wasted tokens. Recording a dismissal turns "the agent
    decided this wasn't a duplicate" into a durable, auditable fact instead
    of a one-off opinion that has to be independently re-reached correctly
    every time the close runs."""
    candidates = aging.flag_duplicate_ap_invoices(conn)

    dismissed_pairs = set()
    for row in conn.execute(
        "SELECT inputs FROM audit_log WHERE agent = ? AND tool_name = 'dismiss_duplicate_candidate'",
        (AGENT_NAME,),
    ).fetchall():
        try:
            d = json.loads(row["inputs"])
            dismissed_pairs.add(frozenset((d["invoice_a_id"], d["invoice_b_id"])))
        except (KeyError, ValueError, TypeError):
            continue

    open_candidates = []
    for cand in candidates:
        if frozenset((cand["invoice_a"], cand["invoice_b"])) in dismissed_pairs:
            continue
        status_a = conn.execute("SELECT status FROM ap_invoices WHERE invoice_id = ?", (cand["invoice_a"],)).fetchone()
        status_b = conn.execute("SELECT status FROM ap_invoices WHERE invoice_id = ?", (cand["invoice_b"],)).fetchone()
        if (status_a is None or status_a["status"] != "disputed") and (status_b is None or status_b["status"] != "disputed"):
            open_candidates.append(cand)
    _log(conn, "read", "flag_duplicate_invoices", {}, {"candidate_count": len(open_candidates)})
    return open_candidates


def dismiss_duplicate_candidate(conn: sqlite3.Connection, vendor_id: str, invoice_a_id: str,
                                  invoice_b_id: str, reason: str) -> dict:
    """Write (governance, not GL) — the mirror image of resolve_duplicate_invoice.
    Records that AP reviewed a flagged candidate pair and judged it NOT a
    duplicate (e.g. two genuinely separate recurring monthly charges that
    happen to fall inside the detector's tolerance window), so
    flag_duplicate_invoices stops re-surfacing it. Touches only audit_log —
    no ap_invoices or gl_transactions write, because nothing was wrong."""
    inputs = {"vendor_id": vendor_id, "invoice_a_id": invoice_a_id, "invoice_b_id": invoice_b_id, "reason": reason}
    outputs = {"status": "dismissed"}
    _log(conn, "dismiss", "dismiss_duplicate_candidate", inputs, outputs, "ap_invoices", invoice_a_id, notes=reason)
    return outputs


def review_correction_requests(conn: sqlite3.Connection) -> list[dict]:
    """Read-only. Open/disputed correction_requests addressed to ap_agent —
    this is how AP discovers a finding Controller already raised."""
    rows = conn.execute(
        "SELECT * FROM correction_requests WHERE target_agent = ? AND status IN ('open','disputed') ORDER BY created_at",
        (AGENT_NAME,),
    ).fetchall()
    result = [dict(r) for r in rows]
    _log(conn, "read", "review_correction_requests", {}, {"count": len(result)})
    return result


def resolve_duplicate_invoice(conn: sqlite3.Connection, duplicate_invoice_id: str, original_invoice_id: str,
                                correction_request_id: str | None, response: str) -> dict:
    """Write — but scoped entirely to AP's own domain: ap_invoices (marking
    the duplicate disputed) and a compensating gl_transactions entry that
    reverses the erroneous accrual AP itself is responsible for having
    posted. If a correction_request_id is given, this also closes the loop
    on Controller's finding — the owning agent responding on the record,
    per Section 10's protocol, not a silent fix."""
    inputs = {"duplicate_invoice_id": duplicate_invoice_id, "original_invoice_id": original_invoice_id,
               "correction_request_id": correction_request_id, "response": response}

    dup = conn.execute("SELECT * FROM ap_invoices WHERE invoice_id = ?", (duplicate_invoice_id,)).fetchone()
    original = conn.execute("SELECT * FROM ap_invoices WHERE invoice_id = ?", (original_invoice_id,)).fetchone()
    if dup is None or original is None:
        outputs = {"status": "error", "reason": "duplicate_invoice_id or original_invoice_id not found"}
        _log(conn, "write_error", "resolve_duplicate_invoice", inputs, outputs)
        return outputs
    if dup["vendor_id"] != original["vendor_id"]:
        outputs = {"status": "refused", "reason": "vendor_id mismatch between the two invoices — refusing to link as duplicates"}
        _log(conn, "write_refused", "resolve_duplicate_invoice", inputs, outputs)
        return outputs

    # Found live in M5: this reversal was unconditionally booked as Dr ap / Cr expense,
    # which only correctly undoes an outstanding UNPAID accrual. If the "duplicate" was
    # already paid, cash already left the building — its AP liability was already
    # extinguished by the payment, so debiting ap here has no real balance to offset and
    # creates a stray, permanent break between the GL ap control account and the AP
    # subledger (which never counted a paid invoice as outstanding in the first place).
    # Getting money back from a vendor who was legitimately paid twice is a collections
    # problem, not a same-domain journal entry — refuse in code rather than let the model
    # paper over it.
    if dup["status"] == "paid":
        outputs = {"status": "refused", "reason": (
            f"{duplicate_invoice_id} has already been paid — its AP liability is already "
            "settled, so there is no outstanding ap balance to reverse. Booking Dr ap here "
            "would create a phantom AP subledger break instead of correcting anything. "
            "This needs vendor refund/collections follow-up, which is outside AP's write "
            "scope — file a correction_request or escalate for human handling instead of "
            "calling resolve_duplicate_invoice on a paid invoice.")}
        _log(conn, "write_refused", "resolve_duplicate_invoice", inputs, outputs, "ap_invoices", duplicate_invoice_id)
        return outputs

    conn.execute(
        "UPDATE ap_invoices SET status = 'disputed', is_duplicate_of = ? WHERE invoice_id = ?",
        (original_invoice_id, duplicate_invoice_id),
    )

    # Reverse the erroneous accrual: Dr gl_account_id / Cr ap. Only reachable now when the
    # duplicate was still unpaid (the paid case is refused above), so this really is the
    # mirror image of the original Dr expense / Cr AP accrual — dated today, clearly memo'd
    # as a correction rather than backdated.
    je_id = f"je_ap_correction_{uuid.uuid4().hex[:8]}"
    today = datetime.now(timezone.utc).date().isoformat()
    period = today[:7]
    conn.execute(
        """INSERT INTO gl_transactions
           (txn_id, je_id, txn_date, account_id, department_id, amount, debit_credit,
            memo, source_type, source_id, period, entered_by_agent, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"txn_{uuid.uuid4().hex[:12]}", je_id, today, "ap", None, dup["amount"], "debit",
         f"Reversal of duplicate invoice {duplicate_invoice_id} (dup of {original_invoice_id})",
         "correction", duplicate_invoice_id, period, AGENT_NAME, "posted"),
    )
    conn.execute(
        """INSERT INTO gl_transactions
           (txn_id, je_id, txn_date, account_id, department_id, amount, debit_credit,
            memo, source_type, source_id, period, entered_by_agent, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"txn_{uuid.uuid4().hex[:12]}", je_id, today, dup["gl_account_id"], dup["department_id"],
         dup["amount"], "credit", f"Reversal of duplicate invoice {duplicate_invoice_id} (dup of {original_invoice_id})",
         "correction", duplicate_invoice_id, period, AGENT_NAME, "posted"),
    )

    if correction_request_id:
        conn.execute(
            "UPDATE correction_requests SET status = 'resolved', resolved_at = ? WHERE request_id = ?",
            (_now(), correction_request_id),
        )

    conn.commit()
    outputs = {"status": "resolved", "je_id": je_id, "reversal_amount": dup["amount"],
               "correction_request_closed": bool(correction_request_id)}
    _log(conn, "write", "resolve_duplicate_invoice", inputs, outputs, "ap_invoices", duplicate_invoice_id, notes=response)
    return outputs


def recommend_payment_batch(conn: sqlite3.Connection, as_of_date: str, invoice_ids: list[str], notes: str) -> dict:
    """Write — but only a RECOMMENDATION, not a payment: files an approvals
    row a human must act on. AP has no tool that actually moves money."""
    inputs = {"as_of_date": as_of_date, "invoice_ids": invoice_ids, "notes": notes}

    rows = conn.execute(
        f"SELECT invoice_id, amount FROM ap_invoices WHERE invoice_id IN ({','.join('?' * len(invoice_ids))})",
        invoice_ids,
    ).fetchall() if invoice_ids else []
    total = round(sum(r["amount"] for r in rows), 2)

    approval_id = f"appr_{uuid.uuid4().hex[:10]}"
    conn.execute(
        """INSERT INTO approvals
           (approval_id, action_type, entity_type, entity_id, requested_by, status, notes)
           VALUES (?,?,?,?,?,?,?)""",
        (approval_id, "payment_batch_recommendation", "ap_invoice_batch", as_of_date, AGENT_NAME, "pending",
         f"{notes} | invoices: {json.dumps(invoice_ids)} | total: ${total:,.2f}"),
    )
    conn.commit()

    outputs = {"status": "recommended", "approval_id": approval_id, "invoice_count": len(invoice_ids), "total": total}
    _log(conn, "write", "recommend_payment_batch", inputs, outputs, "approvals", approval_id)
    return outputs
