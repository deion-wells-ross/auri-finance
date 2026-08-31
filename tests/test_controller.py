"""Layer 1 tests for the Controller Agent's tool layer — deterministic,
no LLM involved. These exist specifically to satisfy M3's milestone bar:
"catches at least one deliberately broken trial balance in a test fixture;
correctly escalates."

Uses a throwaway COPY of the seeded database so these tests can deliberately
corrupt a period without touching db/meridian.db, which every other test
(and the live agents) depend on staying correct.
"""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest

from tools import controller_tools as ct

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DB = ROOT / "db" / "meridian.db"


@pytest.fixture
def broken_conn(tmp_path):
    """A scratch copy of the seeded DB with one deliberately unbalanced
    journal entry inserted into the August period — a single $500 debit
    with no offsetting credit anywhere. Nothing else about the dataset is
    touched."""
    # Resets period_status.approved_by regardless of what db/meridian.db's
    # live agent history happens to hold at test time (a prior live
    # Controller run may have legitimately marked 2026-08 ready) — this
    # fixture's precondition is "an unapproved period," not "whatever state
    # the shared db happens to be in," so it establishes that explicitly
    # rather than assuming it.
    scratch_path = tmp_path / "broken_meridian.db"
    shutil.copy(SOURCE_DB, scratch_path)
    conn = sqlite3.connect(scratch_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("UPDATE period_status SET approved_by = NULL WHERE period = '2026-08'")
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
    yield conn
    conn.close()


@pytest.fixture
def clean_conn(tmp_path):
    scratch_path = tmp_path / "clean_meridian.db"
    shutil.copy(SOURCE_DB, scratch_path)
    conn = sqlite3.connect(scratch_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    yield conn
    conn.close()


def test_check_trial_balance_catches_the_break(broken_conn):
    result = ct.check_trial_balance(broken_conn, "2026-08")
    assert result["balanced"] is False
    assert result["diff"] == pytest.approx(500.00, abs=0.01)


def test_check_trial_balance_passes_on_clean_data(clean_conn):
    result = ct.check_trial_balance(clean_conn, "2026-08")
    assert result["balanced"] is True
    assert result["diff"] == pytest.approx(0.0, abs=0.01)


def test_mark_period_ready_refuses_on_broken_trial_balance(broken_conn):
    result = ct.mark_period_ready_for_reporting(broken_conn, "2026-08")
    assert result["status"] == "refused"
    assert "trial balance" in result["reason"].lower()

    # and it must not have touched period_status
    row = broken_conn.execute("SELECT approved_by FROM period_status WHERE period='2026-08'").fetchone()
    assert row["approved_by"] is None


def test_broken_trial_balance_refusal_is_audited(broken_conn):
    ct.mark_period_ready_for_reporting(broken_conn, "2026-08")
    row = broken_conn.execute(
        "SELECT * FROM audit_log WHERE tool_name='mark_period_ready_for_reporting' AND action='write_refused' ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["agent"] == "controller_agent"


def test_mark_period_ready_refuses_above_materiality_even_when_balanced(clean_conn):
    # Deliberately inject one open correction request into an otherwise-clean
    # period (never rely on whatever an earlier live agent run happened to
    # leave in approvals/correction_requests — that made this test pass or
    # fail depending on session history rather than what it actually checks).
    # A materiality threshold of $0 means ANY exposure blocks readiness —
    # proves the threshold is a real code-enforced gate, not decoration.
    clean_conn.execute(
        """INSERT INTO correction_requests
           (request_id, period, raised_by_agent, target_agent, finding, evidence,
            requested_action, materiality_amount, status, round, created_at, resolved_at)
           VALUES ('cr_test_fixture', '2026-08', 'controller_agent', 'ap_agent',
                   'test finding', 'test evidence', 'test action', 500.0, 'open', 1, ?, NULL)""",
        (ct._now(),),
    )
    clean_conn.commit()

    result = ct.mark_period_ready_for_reporting(clean_conn, "2026-08", materiality_threshold=0.0)
    assert result["status"] == "refused"
    assert "materiality" in result["reason"].lower()


def test_raise_correction_request_never_touches_other_agents_data(clean_conn):
    """The Controller's only lever on someone else's finding is a row in
    correction_requests — confirm the tool literally has no path that writes
    to gl_transactions or ap_invoices."""
    before_gl_count = clean_conn.execute("SELECT COUNT(*) FROM gl_transactions").fetchone()[0]
    before_ap_count = clean_conn.execute("SELECT COUNT(*) FROM ap_invoices").fetchone()[0]

    result = ct.raise_correction_request(
        clean_conn, period="2026-08", target_agent="ap_agent",
        finding="Duplicate CloudScale hosting invoice", evidence="ap_... pair, $14,200, 3 days apart",
        requested_action="Reverse the duplicate and mark is_duplicate_of", materiality_amount=14200.0,
    )
    assert result["status"] == "raised"

    after_gl_count = clean_conn.execute("SELECT COUNT(*) FROM gl_transactions").fetchone()[0]
    after_ap_count = clean_conn.execute("SELECT COUNT(*) FROM ap_invoices").fetchone()[0]
    assert after_gl_count == before_gl_count
    assert after_ap_count == before_ap_count

    cr = clean_conn.execute("SELECT * FROM correction_requests WHERE request_id=?", (result["request_id"],)).fetchone()
    assert cr["status"] == "open"
    assert cr["raised_by_agent"] == "controller_agent"
