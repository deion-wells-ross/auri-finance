"""Layer 1 tests for the M8 anomaly verifier — deterministic, no LLM.

Uses a throwaway COPY of the freshly-generated demo database (db/demo_
meridian.db, produced by data/seed/generate_meridian.py against an
overridden DB_PATH — same generator, same random.seed(42), just not
db/meridian.db itself) — same reasoning as every other test file in this
project: real agent runs (and every other test) shouldn't depend on this
file's mutations, and this file shouldn't depend on what a live run left
behind in the shared database.

Covers both halves of verify_anomalies' honest split: the pristine seed
(before any agent has touched it) should show all six anomalies as NOT
found, and each check should flip to found=True once the specific,
minimal piece of evidence a real agent run would produce is present —
without needing a full live agent run inside a test.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest

from tools import ap_tools as apt
from tools import ar_tools as art
from tools import bookkeeping_tools as bk
from tools import verify_anomalies as va

ROOT = Path(__file__).resolve().parents[1]
PERIOD = "2026-08"


@pytest.fixture(scope="module")
def demo_source(tmp_path_factory):
    """Generate the demo database once per test module — it's a pure
    function of random.seed(42), identical every time, and generating it
    involves writing ~1400 gl_transactions rows; no need to redo that for
    every single test."""
    from data.seed import generate_meridian as gen
    path = tmp_path_factory.mktemp("demo_src") / "demo_meridian.db"
    gen.DB_PATH = path
    gen.main()
    return path


@pytest.fixture
def conn(demo_source, tmp_path):
    scratch = tmp_path / "verify_scratch.db"
    shutil.copy(demo_source, scratch)
    c = sqlite3.connect(scratch)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    yield c
    c.close()


def _log_final_report(conn, agent: str, report: dict) -> None:
    """Stand-in for tools/audit.py's log_report, without importing agents/
    run.py (which needs an API key just to import anthropic-backed
    modules is fine, but pulling in a live agent loop for a unit test
    isn't the point) — same insert shape."""
    conn.execute(
        """INSERT INTO audit_log
           (log_id, timestamp, agent, action, tool_name, inputs, outputs,
            related_entity_type, related_entity_id, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (f"log_{uuid.uuid4().hex[:12]}", "2026-08-31T12:00:00+00:00", agent, "final_report",
         "submit_report", "{}", json.dumps(report), None, None, report.get("summary")),
    )
    conn.commit()


def test_pristine_seed_shows_nothing_found(conn):
    """Before any agent has run, none of the six anomalies should read as
    found — a checker that finds things in an untouched database isn't
    checking anything."""
    results = va.verify_all(conn, PERIOD)
    assert len(results) == 6
    assert all(r["found"] is False for r in results), results


def _cloudscale_duplicate_pair(conn):
    """The seed pre-links is_duplicate_of on the true duplicate pair as its
    own ground-truth answer key — that's how a test (or a human) locates
    which of CloudScale's several August invoices are the anomaly, same as
    a real agent's rule-based detector would locate them independently by
    amount/date proximity instead."""
    dup = conn.execute(
        "SELECT invoice_id, is_duplicate_of FROM ap_invoices "
        "WHERE vendor_id='cloudscale' AND invoice_date LIKE '2026-08%' AND is_duplicate_of IS NOT NULL"
    ).fetchone()
    assert dup is not None, "expected the seed to pre-link a CloudScale duplicate pair in August"
    return dup["invoice_id"], dup["is_duplicate_of"]


def test_duplicate_invoice_found_after_resolve(conn):
    duplicate_id, original_id = _cloudscale_duplicate_pair(conn)
    apt.resolve_duplicate_invoice(conn, duplicate_invoice_id=duplicate_id, original_invoice_id=original_id,
                                    correction_request_id=None, response="Confirmed duplicate.")
    result = va.check_duplicate_invoice(conn, PERIOD)
    assert result["found"] is True


def test_duplicate_invoice_found_after_dismissal(conn):
    duplicate_id, original_id = _cloudscale_duplicate_pair(conn)
    apt.dismiss_duplicate_candidate(conn, vendor_id="cloudscale", invoice_a_id=duplicate_id,
                                      invoice_b_id=original_id, reason="Two separate monthly charges.")
    result = va.check_duplicate_invoice(conn, PERIOD)
    assert result["found"] is True


def test_bookkeeping_ambiguity_found_after_escalation(conn):
    feed = conn.execute(
        "SELECT feed_id FROM bank_feed WHERE description LIKE '%MERIDIAN CONSULTING%' AND amount=9800.0"
    ).fetchone()
    assert feed is not None
    bk.escalate_for_review(conn, feed_id=feed["feed_id"], reason="No confident account match.")
    result = va.check_bookkeeping_ambiguity(conn, PERIOD)
    assert result["found"] is True


def test_ar_concentration_found_after_report_mentions_vantage(conn):
    art.flag_concentration_risk(conn, as_of_date="2026-08-31")
    _log_final_report(conn, "ar_agent", {
        "summary": "Vantage Retail Group represents a concentration risk in open AR.",
        "findings": ["Vantage Retail Group is ~34% of open AR."],
    })
    result = va.check_ar_concentration(conn, PERIOD)
    assert result["found"] is True


def test_engineering_payroll_found_after_report_mentions_it(conn):
    _log_final_report(conn, "payroll_agent", {
        "summary": "Engineering headcount grew faster than revenue.",
        "findings": ["Engineering headcount grew 19.0% vs 6.0% revenue growth."],
    })
    result = va.check_engineering_payroll(conn, PERIOD)
    assert result["found"] is True


def test_gross_margin_found_via_explicit_flag(conn):
    _log_final_report(conn, "fpa_agent", {
        "summary": "Margin trend reviewed.", "gross_margin_trend_explained": True,
        "material_variances": [], "forecast_status": "not_attempted", "findings": [],
    })
    result = va.check_gross_margin_decline(conn, PERIOD)
    assert result["found"] is True


def test_marketing_overrun_found_after_report_mentions_it(conn):
    _log_final_report(conn, "fpa_agent", {
        "summary": "Marketing overspent.", "gross_margin_trend_explained": False,
        "material_variances": ["Marketing +28% vs budget, driven by an unbudgeted trade-show line."],
        "forecast_status": "not_attempted", "findings": [],
    })
    result = va.check_marketing_overrun(conn, PERIOD)
    assert result["found"] is True
