"""Layer 1 tests for the FP&A Agent's tool layer — deterministic, no LLM
involved. These exist to cover set_forecast_assumptions' code-enforced
gate: an empty basis is refused outright, and a shift from the currently
active assumption larger than the review threshold gets filed as a pending
approval instead of written directly — the same threshold-plus-approval
shape as Controller's materiality gate (test_controller.py), applied to
FP&A's one real write.

Uses a throwaway COPY of the seeded database, same reasoning as
test_controller.py: the live agents (and every other test) depend on
db/meridian.db staying untouched.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from tools import fpa_tools as fpat

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DB = ROOT / "db" / "meridian.db"


@pytest.fixture
def clean_conn(tmp_path):
    """A scratch copy with any pre-existing forecast_assumptions rows
    cleared, so these tests don't depend on whatever a prior live FP&A run
    happened to leave active in the shared database."""
    scratch_path = tmp_path / "fpa_meridian.db"
    shutil.copy(SOURCE_DB, scratch_path)
    conn = sqlite3.connect(scratch_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM forecast_assumptions")
    conn.commit()
    yield conn
    conn.close()


def test_set_forecast_assumptions_refuses_empty_basis(clean_conn):
    result = fpat.set_forecast_assumptions(clean_conn, period="2026-09", monthly_growth_rate_pct=1.0, basis="")
    assert result["status"] == "refused"
    row = clean_conn.execute("SELECT COUNT(*) FROM forecast_assumptions").fetchone()[0]
    assert row == 0


def test_set_forecast_assumptions_writes_directly_with_no_prior(clean_conn):
    result = fpat.set_forecast_assumptions(clean_conn, period="2026-09", monthly_growth_rate_pct=1.0,
                                             basis="Trailing 8-month trend.")
    assert result["status"] == "set"
    row = clean_conn.execute("SELECT status FROM forecast_assumptions WHERE assumption_id = ?",
                               (result["assumption_id"],)).fetchone()
    assert row["status"] == "active"


def test_set_forecast_assumptions_allows_small_shift_directly(clean_conn):
    fpat.set_forecast_assumptions(clean_conn, period="2026-09", monthly_growth_rate_pct=1.0,
                                    basis="Trailing 8-month trend.")
    result = fpat.set_forecast_assumptions(clean_conn, period="2026-10", monthly_growth_rate_pct=1.5,
                                             basis="Slight uptick given September actuals.")
    assert result["status"] == "set"
    active = clean_conn.execute("SELECT COUNT(*) FROM forecast_assumptions WHERE status='active'").fetchone()[0]
    assert active == 1


def test_set_forecast_assumptions_refuses_material_shift_and_files_approval(clean_conn):
    fpat.set_forecast_assumptions(clean_conn, period="2026-09", monthly_growth_rate_pct=1.0,
                                    basis="Trailing 8-month trend.")
    result = fpat.set_forecast_assumptions(clean_conn, period="2026-10", monthly_growth_rate_pct=5.0,
                                             basis="Speculative acceleration.")
    assert result["status"] == "review_required"
    assert "approval_id" in result

    approval = clean_conn.execute(
        "SELECT * FROM approvals WHERE approval_id = ?", (result["approval_id"],)
    ).fetchone()
    assert approval["status"] == "pending"
    assert approval["action_type"] == "revise_forecast_assumption"

    # the prior assumption must still be the one and only active row —
    # a refused write must never touch existing state
    active_rows = clean_conn.execute(
        "SELECT * FROM forecast_assumptions WHERE status='active'"
    ).fetchall()
    assert len(active_rows) == 1
    assert active_rows[0]["monthly_growth_rate_pct"] == 1.0


def test_set_forecast_assumptions_supersedes_not_overwrites(clean_conn):
    first = fpat.set_forecast_assumptions(clean_conn, period="2026-09", monthly_growth_rate_pct=1.0,
                                            basis="Trailing 8-month trend.")
    fpat.set_forecast_assumptions(clean_conn, period="2026-10", monthly_growth_rate_pct=1.3,
                                    basis="Small uptick.")

    prior_row = clean_conn.execute(
        "SELECT status FROM forecast_assumptions WHERE assumption_id = ?", (first["assumption_id"],)
    ).fetchone()
    assert prior_row["status"] == "superseded"

    all_rows = clean_conn.execute("SELECT COUNT(*) FROM forecast_assumptions").fetchone()[0]
    assert all_rows == 2  # history preserved, nothing deleted
