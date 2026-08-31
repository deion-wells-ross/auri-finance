"""Layer 1 tests for tools/replay_audit_trail.py (M8) — deterministic, no
LLM. Uses a throwaway scratch database with a handful of hand-inserted
audit_log rows rather than the seeded dataset — this module only ever
reads audit_log, so what matters is exercising ordering, filtering, and
rendering, not realistic financial data.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tools import replay_audit_trail as replay_mod

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript((ROOT / "db" / "schema.sql").read_text())
    rows = [
        ("log_1", "2026-08-31T10:00:00+00:00", "bookkeeping_agent", "read", "get_uncategorized_txns", "{}", "{}", None, None, None),
        ("log_2", "2026-08-31T10:05:00+00:00", "bookkeeping_agent", "escalate", "escalate_for_review",
         json.dumps({"feed_id": "feed_abc"}), json.dumps({"status": "pending"}), "bank_feed", "feed_abc",
         "Genuinely ambiguous vendor description."),
        ("log_3", "2026-08-31T10:10:00+00:00", "ap_agent", "read", "get_ap_aging", "{}", "{}", None, None, None),
        ("log_4", "2026-08-31T10:15:00+00:00", "controller_agent", "final_report", "submit_report", "{}",
         json.dumps({"summary": "Period reviewed for 2026-08."}), None, None, "Period reviewed for 2026-08."),
    ]
    c.executemany(
        "INSERT INTO audit_log (log_id, timestamp, agent, action, tool_name, inputs, outputs, "
        "related_entity_type, related_entity_id, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    c.commit()
    yield c
    c.close()


def test_replay_returns_all_rows_in_timestamp_order(conn):
    entries = replay_mod.replay(conn)
    assert [e["log_id"] for e in entries] == ["log_1", "log_2", "log_3", "log_4"]


def test_replay_filters_by_agent(conn):
    entries = replay_mod.replay(conn, agents=["ap_agent"])
    assert len(entries) == 1
    assert entries[0]["log_id"] == "log_3"


def test_replay_filters_by_period_text_match(conn):
    entries = replay_mod.replay(conn, period="2026-08")
    assert [e["log_id"] for e in entries] == ["log_4"]  # only this row's outputs/notes mention the period


def test_render_text_includes_notes_as_why(conn):
    entries = replay_mod.replay(conn, agents=["bookkeeping_agent"])
    text = replay_mod.render_text(entries)
    assert "escalate_for_review" in text
    assert "why: Genuinely ambiguous vendor description." in text


def test_summarize_by_agent_counts_correctly(conn):
    entries = replay_mod.replay(conn)
    summary = replay_mod.summarize_by_agent(entries)
    assert summary["bookkeeping_agent"] == 2
    assert summary["ap_agent"] == 1
    assert summary["controller_agent"] == 1
