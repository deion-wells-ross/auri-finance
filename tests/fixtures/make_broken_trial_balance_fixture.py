"""Regenerates the deliberately-broken-trial-balance fixture used to
live-verify the Controller Agent (M3) — a scratch copy of db/meridian.db
with one single-sided $500 debit inserted into the August period and no
matching credit anywhere. db/meridian.db itself is never touched.

Usage:
    python3 tests/fixtures/make_broken_trial_balance_fixture.py
    # writes db/broken_fixture.db

    export ANTHROPIC_API_KEY=$(cat /home/claude/.anthropic_key)
    python3 agents/controller/run.py --db db/broken_fixture.db --period 2026-08

The pytest suite (tests/test_controller.py) does the same thing in-memory
via a pytest fixture for the deterministic, no-LLM checks; this script
exists so the same broken database can be handed to the LIVE agent on
demand, for the live-run half of the M3 verification.
"""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DB = ROOT / "db" / "meridian.db"
OUTPUT_DB = ROOT / "db" / "broken_fixture.db"


def main() -> None:
    shutil.copy(SOURCE_DB, OUTPUT_DB)
    conn = sqlite3.connect(OUTPUT_DB)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """INSERT INTO gl_transactions
           (txn_id, je_id, txn_date, account_id, department_id, amount, debit_credit,
            memo, source_type, source_id, period, entered_by_agent, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"txn_{uuid.uuid4().hex[:12]}", f"je_broken_{uuid.uuid4().hex[:8]}", "2026-08-20",
         "software", None, 500.00, "debit", "TEST FIXTURE: deliberately unbalanced, no matching credit",
         "test_fixture", None, "2026-08", "test_fixture", "posted"),
    )
    conn.commit()

    diff = conn.execute(
        """SELECT SUM(CASE WHEN debit_credit='debit' THEN amount ELSE 0 END)
                 - SUM(CASE WHEN debit_credit='credit' THEN amount ELSE 0 END)
           FROM gl_transactions WHERE period='2026-08'"""
    ).fetchone()[0]
    conn.close()

    print(f"Wrote {OUTPUT_DB} — August 2026 trial balance now off by ${diff:,.2f}")


if __name__ == "__main__":
    main()
