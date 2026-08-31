"""Month-end close orchestrator (M5, extended in M7 with reporting).

A hard-coded Python state machine — deliberately NOT a generic workflow
engine (Section 5: "you have exactly one workflow and premature generality
here is the appearance-of-sophistication trap"). It runs the actual
month-end close sequence from Section 4 end to end for one period, and
enforces both of the architecture's structural gates in code, not prompts:

  GATE 1 — Controller's "ready to report" check. Controller (M3) already
  enforces this INSIDE mark_period_ready_for_reporting; the orchestrator
  independently re-checks the resulting period_status row before advancing
  a single step further, per Section 10's own language: "structural
  gates... literal booleans the orchestrator checks before advancing the
  workflow." Two independent checks of the same fact, not one agent's
  self-report trusted at face value.

  GATE 2 — human final approval. New in M5. The orchestrator will not
  close the period itself — full stop — until a human has approved a
  'finalize_period_close' row in the `approvals` table. There is no LLM
  anywhere in this check: it's one SQL query. If no approval exists yet,
  the orchestrator files the request and halts, printing exactly what a
  human needs to run to unblock it (orchestrator/approve.py — the "five-line
  CLI" approval interface Section 5 scoped for V1).

M7 addition: once the period is actually closed (both gates cleared), the
orchestrator runs FP&A and CFO (M6's live agents) and then hands their
output — plus the deterministic statements/metrics — to
services/dashboard.py's `publish()`, the "Dashboard Publisher" from
Section 2/4: a deterministic workflow step, not an agent. This satisfies
Section 13's M7 bar literally: "full package generated only after both
gates clear" is true both by construction (this code is unreachable unless
the finalize block above already ran) and by an independent code-level
check inside `publish()` itself, which re-reads period_status rather than
trusting that guarantee.

Design note — one deliberate deviation from Section 4's diagram: the
diagram sequences FP&A → CFO → human approval → Dashboard Publisher (the
human approves the close *and* the CFO briefing together). This build
keeps M5's gates exactly as shipped and tested — approval finalizes the
close alone — then runs FP&A/CFO/publish immediately afterward. Reasoning:
CFO output is explicitly advisory and executes nothing (Section 3), so it
doesn't need its own approval gate; re-sequencing a working, tested control
gate to match a diagram exactly would be exactly the kind of scope-creep
risk Section "Where I'd Push Back" warns against this late in the build.
Documented here rather than silently diverging from the charter.

Design note on Bookkeeping/AP/AR/Payroll running "in parallel" per Section
4's diagram: they're independent domains with no cross-dependencies, but
this orchestrator runs them sequentially rather than concurrently. Real
concurrency against one shared SQLite file, each step making its own live
API calls, buys nothing at this scale and adds real risk (SQLite's
write-locking, harder-to-read logs) for a demo that runs in well under a
minute either way — the kind of complexity the charter's own closing
section asks to be skeptical of.

Usage:
    export ANTHROPIC_API_KEY=$(cat /home/claude/.anthropic_key)
    python3 orchestrator/run_close.py --period 2026-08
    # if gate 2 blocks:
    python3 orchestrator/approve.py --period 2026-08 --approved-by "Alex Rivera, Controller"
    python3 orchestrator/run_close.py --period 2026-08
    # on the run that actually closes the period, FP&A, CFO, and the
    # Dashboard Publisher all run automatically as the last three steps;
    # the package lands in reports/<period>/ (override with --reports-dir)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.bookkeeping.run import run as run_bookkeeping  # noqa: E402
from agents.ap.run import run as run_ap  # noqa: E402
from agents.ar.run import run as run_ar  # noqa: E402
from agents.payroll.run import run as run_payroll  # noqa: E402
from agents.controller.run import run as run_controller  # noqa: E402
from agents.fpa.run import run as run_fpa  # noqa: E402
from agents.cfo.run import run as run_cfo  # noqa: E402
from services import dashboard  # noqa: E402

AGENT_NAME = "orchestrator"
DEFAULT_DB = str(ROOT / "db" / "meridian.db")
DEFAULT_REPORTS_DIR = str(ROOT / "reports")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(conn: sqlite3.Connection, action: str, step: str, inputs: dict, outputs: dict, notes: str | None = None) -> None:
    conn.execute(
        """INSERT INTO audit_log
           (log_id, timestamp, agent, action, tool_name, inputs, outputs,
            related_entity_type, related_entity_id, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (f"log_{uuid.uuid4().hex[:12]}", _now(), AGENT_NAME, action, step,
         json.dumps(inputs), json.dumps(outputs), "period_status", None, notes),
    )
    conn.commit()


def _prior_period(period: str) -> str:
    """6-month lookback for the payroll-vs-revenue window (Section 8's
    anomaly #2 test), matching what every M4 Payroll run has used so far."""
    year, month = int(period[:4]), int(period[5:7])
    month -= 6
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}"


def run_close(db_path: str, period: str, reports_dir: str = DEFAULT_REPORTS_DIR) -> dict:
    print(f"\n{'=' * 70}\nAURI FINANCE — MONTH-END CLOSE ORCHESTRATOR\nPeriod: {period}   Database: {db_path}\n{'=' * 70}\n")

    prior_period = _prior_period(period)
    as_of_date = f"{period}-31"

    print(f"--- STEP 1/5: Bookkeeping Agent ---")
    bk = run_bookkeeping(db_path=db_path, period=period)

    print(f"\n--- STEP 2/5: AP Agent ---")
    ap = run_ap(db_path, period, as_of_date)

    print(f"\n--- STEP 3/5: AR Agent ---")
    ar = run_ar(db_path, as_of_date)

    print(f"\n--- STEP 4/5: Payroll & Workforce Agent ---")
    payroll = run_payroll(db_path, prior_period, period)

    print(f"\n--- STEP 5/5: Controller Agent ---")
    controller = run_controller(db_path, period)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # ---- GATE 1: Controller's own validation, re-checked independently ----
    row = conn.execute("SELECT status, approved_by, closed_at FROM period_status WHERE period = ?", (period,)).fetchone()
    if row is None:
        outputs = {"gate": "gate1_controller_ready", "passed": False, "reason": f"no period_status row for {period}"}
        _log(conn, "gate_check", "gate1_controller_ready", {"period": period}, outputs)
        conn.close()
        print(f"\n{'!' * 70}\nGATE 1 BLOCKED — no period_status row exists for {period}.\n{'!' * 70}\n")
        return {"status": "blocked_at_gate1", "outputs": outputs}

    gate1_passed = row["approved_by"] is not None
    _log(conn, "gate_check", "gate1_controller_ready", {"period": period},
         {"passed": gate1_passed, "approved_by": row["approved_by"]})

    if not gate1_passed:
        print(f"\n{'!' * 70}\nGATE 1 BLOCKED — Controller has not marked {period} ready for reporting.\n"
              f"Check the Controller run above for why (unbalanced trial balance, or\n"
              f"unresolved exposure above the materiality threshold). Fix the underlying\n"
              f"issue and re-run this orchestrator.\n{'!' * 70}\n")
        conn.close()
        return {"status": "blocked_at_gate1", "bookkeeping": bk, "ap": ap, "ar": ar, "payroll": payroll, "controller": controller}

    print(f"\nGATE 1 PASSED — Controller validated {period} (approved_by='{row['approved_by']}').")

    # ---- GATE 2: human final approval — no LLM in this check at all ----
    approved = conn.execute(
        """SELECT * FROM approvals WHERE action_type = 'finalize_period_close'
           AND entity_id = ? AND status = 'approved' ORDER BY approved_at DESC LIMIT 1""",
        (period,),
    ).fetchone()

    if approved is None:
        pending = conn.execute(
            """SELECT * FROM approvals WHERE action_type = 'finalize_period_close'
               AND entity_id = ? AND status = 'pending'""",
            (period,),
        ).fetchone()
        if pending is None:
            approval_id = f"appr_{uuid.uuid4().hex[:10]}"
            conn.execute(
                """INSERT INTO approvals
                   (approval_id, action_type, entity_type, entity_id, requested_by, status, notes)
                   VALUES (?,?,?,?,?,?,?)""",
                (approval_id, "finalize_period_close", "period", period, AGENT_NAME, "pending",
                 f"Controller validated {period} as ready for reporting. Awaiting human sign-off to finalize the close."),
            )
            conn.commit()
        else:
            approval_id = pending["approval_id"]

        _log(conn, "gate_check", "gate2_human_approval", {"period": period}, {"passed": False, "approval_id": approval_id})
        print(f"\n{'!' * 70}\nGATE 2 BLOCKED — awaiting human approval to finalize {period}.\n"
              f"Approval request filed: {approval_id}\n\n"
              f"A human reviews this and, if satisfied, runs:\n"
              f'  python3 orchestrator/approve.py --period {period} --approved-by "Your Name"\n'
              f"Then re-run this orchestrator to complete the close.\n{'!' * 70}\n")
        conn.close()
        return {"status": "blocked_at_gate2", "approval_id": approval_id,
                "bookkeeping": bk, "ap": ap, "ar": ar, "payroll": payroll, "controller": controller}

    print(f"\nGATE 2 PASSED — human approval on record (approved by '{approved['approved_by']}' at {approved['approved_at']}).")

    # ---- Finalize: the first and only place period_status.status becomes 'closed' ----
    conn.execute(
        "UPDATE period_status SET status = 'closed', closed_at = ?, approved_by = ? WHERE period = ?",
        (_now(), approved["approved_by"], period),
    )
    conn.commit()
    _log(conn, "close_period", "finalize_close", {"period": period},
         {"status": "closed", "approved_by": approved["approved_by"]},
         notes=f"Closed via approval {approved['approval_id']}")

    print(f"\n{'=' * 70}\nPERIOD {period} CLOSED. approved_by='{approved['approved_by']}'\n{'=' * 70}\n")
    conn.close()

    # ---- M7: FP&A + CFO, then the deterministic Dashboard Publisher ----
    # Only reachable once both gates above have cleared. Each of these runs
    # independently opens its own connection to db_path (same pattern as
    # every agent above) and is wrapped so that a live-agent hiccup here
    # can never unwind or contradict a close that has already happened.
    print(f"\n--- STEP 6/8: FP&A Agent ---")
    try:
        fpa = run_fpa(db_path, period)
    except Exception as exc:  # noqa: BLE001
        fpa = {"final_report": None, "error": f"{type(exc).__name__}: {exc}"}
        print(f"FP&A run raised: {fpa['error']}")

    print(f"\n--- STEP 7/8: CFO Agent ---")
    try:
        cfo = run_cfo(db_path, period)
    except Exception as exc:  # noqa: BLE001
        cfo = {"final_report": None, "error": f"{type(exc).__name__}: {exc}"}
        print(f"CFO run raised: {cfo['error']}")

    print(f"\n--- STEP 8/8: Dashboard Publisher (deterministic, no LLM) ---")
    pub_conn = sqlite3.connect(db_path)
    pub_conn.row_factory = sqlite3.Row
    pub_conn.execute("PRAGMA foreign_keys = ON")
    package_result = dashboard.publish(
        pub_conn, period, reports_dir,
        fpa_report=fpa.get("final_report"), cfo_briefing=cfo.get("final_report"),
    )
    pub_conn.close()
    if package_result.get("status") == "published":
        print(f"Close package published: {package_result['html_path']}")
    else:
        print(f"Dashboard Publisher did not publish: {package_result.get('reason', package_result)}")

    return {"status": "closed", "approved_by": approved["approved_by"],
            "bookkeeping": bk, "ap": ap, "ar": ar, "payroll": payroll, "controller": controller,
            "fpa": fpa, "cfo": cfo, "package": package_result}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--period", default="2026-08")
    parser.add_argument("--reports-dir", default=DEFAULT_REPORTS_DIR)
    args = parser.parse_args()
    result = run_close(args.db, args.period, reports_dir=args.reports_dir)
    print(f"\nOrchestrator result: {result['status']}")
