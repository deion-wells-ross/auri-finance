"""Layer 1 tests for the Dashboard Publisher (M7) — deterministic, no LLM
involved. These exist to cover the one thing that makes this module a real
structural gate rather than a formatting convenience: it refuses to build
or publish a package for a period that isn't closed, checked directly
against period_status rather than trusted from a caller.

Uses a throwaway COPY of the seeded database, same reasoning as
test_controller.py and test_fpa.py: the live agents (and every other test)
depend on db/meridian.db staying untouched.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from services import dashboard

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DB = ROOT / "db" / "meridian.db"


@pytest.fixture
def scratch_conn(tmp_path):
    scratch_path = tmp_path / "dashboard_meridian.db"
    shutil.copy(SOURCE_DB, scratch_path)
    conn = sqlite3.connect(scratch_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    yield conn
    conn.close()


def test_refuses_when_no_period_status_row(scratch_conn):
    scratch_conn.execute("DELETE FROM period_status WHERE period = '2099-01'")
    result = dashboard.build_close_package(scratch_conn, "2099-01")
    assert result["status"] == "refused"
    assert "not closed" in result["reason"]


def test_refuses_when_period_open(scratch_conn):
    scratch_conn.execute(
        "INSERT OR REPLACE INTO period_status (period, status, closed_at, approved_by) "
        "VALUES ('2026-09', 'open', NULL, NULL)"
    )
    scratch_conn.commit()
    result = dashboard.build_close_package(scratch_conn, "2026-09")
    assert result["status"] == "refused"


def test_builds_full_package_for_closed_period(scratch_conn):
    # db/meridian.db already has 2026-08 closed as of M5 — that's the fact
    # under test, not something this test needs to set up itself.
    row = scratch_conn.execute("SELECT status FROM period_status WHERE period = '2026-08'").fetchone()
    assert row is not None and row["status"] == "closed", "expected 2026-08 to already be closed from M5"

    result = dashboard.build_close_package(scratch_conn, "2026-08")
    assert result["status"] == "ok"
    for key in ("income_statement", "balance_sheet", "cash_flow", "kpis",
                "audit_log_summary", "close", "generated_at"):
        assert key in result
    assert result["balance_sheet"]["balanced"] is True
    assert result["income_statement"]["period"] == "2026-08"


def test_package_degrades_gracefully_with_no_agent_reports(scratch_conn):
    """The financial package must never be held hostage by FP&A/CFO not
    having produced a report on a given run (MAX_TURNS exhausted, API
    error, etc.) — fpa_report/cfo_briefing are optional inputs."""
    result = dashboard.build_close_package(scratch_conn, "2026-08", fpa_report=None, cfo_briefing=None)
    assert result["status"] == "ok"
    assert result["fpa_report"] is None
    assert result["cfo_briefing"] is None
    html = dashboard.render_html(result)
    assert "did not produce" in html


def test_publish_refused_writes_no_files(scratch_conn, tmp_path):
    out_dir = tmp_path / "reports"
    result = dashboard.publish(scratch_conn, "2099-01", str(out_dir))
    assert result["status"] == "refused"
    assert not out_dir.exists()


def test_publish_writes_json_and_html_for_closed_period(scratch_conn, tmp_path):
    out_dir = tmp_path / "reports"
    fake_fpa = {"summary": "test", "gross_margin_trend_explained": True,
                "material_variances": ["x"], "forecast_status": "set", "findings": ["y"]}
    fake_cfo = {"summary": "test", "facts": ["a"], "hypotheses": ["b"],
                "recommendations": ["c"], "open_questions": ["d"], "agents_delegated_to": ["fpa"]}

    result = dashboard.publish(scratch_conn, "2026-08", str(out_dir), fpa_report=fake_fpa, cfo_briefing=fake_cfo)
    assert result["status"] == "published"

    json_path = Path(result["json_path"])
    html_path = Path(result["html_path"])
    assert json_path.exists()
    assert html_path.exists()

    package = json.loads(json_path.read_text())
    assert package["fpa_report"]["summary"] == "test"
    assert package["cfo_briefing"]["agents_delegated_to"] == ["fpa"]

    html = html_path.read_text()
    assert "2026-08" in html
    assert "Income Statement" in html
    assert "CFO Briefing" in html


def test_publish_logs_to_audit_log(scratch_conn, tmp_path):
    before = scratch_conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE agent = 'dashboard_publisher'"
    ).fetchone()[0]
    dashboard.publish(scratch_conn, "2026-08", str(tmp_path / "reports"))
    after = scratch_conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE agent = 'dashboard_publisher'"
    ).fetchone()[0]
    assert after == before + 1
