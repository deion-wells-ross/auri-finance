"""The human approval interface — deliberately this small.

Section 5 scoped V1's approval interface as "a CLI or a five-line Flask/
Streamlit page listing pending approvals and a button — not a real auth
system yet." This is that CLI. It does exactly one thing: mark a pending
'finalize_period_close' approval as approved, by a named human, so
orchestrator/run_close.py's gate 2 can pass on its next run. It has no
authentication and isn't meant to — Section 13's RBAC item is explicitly a
V1.1+ concern, once there's more than one human in the loop.

Usage:
    python3 orchestrator/approve.py --period 2026-08 --approved-by "Your Name"
    python3 orchestrator/approve.py --list                 # see what's pending
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_DB = str(ROOT / "db" / "meridian.db")


def list_pending(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM approvals WHERE status = 'pending' ORDER BY action_type").fetchall()
    conn.close()
    if not rows:
        print("No pending approvals.")
        return
    for r in rows:
        print(f"[{r['approval_id']}] {r['action_type']} — entity: {r['entity_type']}/{r['entity_id']} "
              f"— requested by {r['requested_by']}\n    {r['notes']}\n")


def approve(db_path: str, period: str, approved_by: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    row = conn.execute(
        """SELECT * FROM approvals WHERE action_type = 'finalize_period_close'
           AND entity_id = ? AND status = 'pending'""",
        (period,),
    ).fetchone()
    if row is None:
        print(f"No pending 'finalize_period_close' approval found for period {period}. "
              f"Run the orchestrator first — it files this request once Controller has validated the period.")
        conn.close()
        return

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE approvals SET status = 'approved', approved_by = ?, approved_at = ? WHERE approval_id = ?",
        (approved_by, now, row["approval_id"]),
    )
    conn.commit()
    conn.close()
    print(f"Approved: {row['approval_id']} ({period}) by '{approved_by}' at {now}.\n"
          f"Re-run orchestrator/run_close.py --period {period} to complete the close.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--period", default="2026-08")
    parser.add_argument("--approved-by", default=None)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        list_pending(args.db)
    elif args.approved_by:
        approve(args.db, args.period, args.approved_by)
    else:
        parser.error("pass --approved-by \"Name\" to approve, or --list to see pending approvals")
