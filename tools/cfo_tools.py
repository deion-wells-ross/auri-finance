"""CFO Agent's tool surface.

Scope, per Section 3: the CFO holds no write tools into any specialist's
domain data at all, and nothing it does executes — its only output is
advisory, pending human sign-off. Its two real capabilities are reading
already-computed statements/metrics, and delegate_to_agent, which is a live
nested call to a real specialist agent (agents/cfo/delegate.py) — the thing
that makes "the CFO delegates to specialists" literally true rather than a
description of reading a stored report.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import statements as stmt  # noqa: E402
from services import metrics  # noqa: E402
from agents.cfo import delegate  # noqa: E402

AGENT_NAME = "cfo_agent"


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


def get_statements(conn: sqlite3.Connection, statement_type: str, period: str) -> dict:
    """Read-only. statement_type: 'income_statement', 'balance_sheet', or
    'cash_flow'. balance_sheet is as-of the given period (it's a snapshot,
    not a period total, per M1's own design note)."""
    if statement_type == "income_statement":
        result = stmt.income_statement(conn, period)
    elif statement_type == "balance_sheet":
        result = stmt.balance_sheet(conn, period)
    elif statement_type == "cash_flow":
        result = stmt.cash_flow_statement(conn, period)
    else:
        result = {"status": "error", "reason": f"unknown statement_type '{statement_type}' — "
                                                  "use income_statement, balance_sheet, or cash_flow"}
    _log(conn, "read", "get_statements", {"statement_type": statement_type, "period": period},
         {"ok": "status" not in result})
    return result


def get_metrics(conn: sqlite3.Connection, period: str) -> dict:
    """Read-only. A convenience bundle of every headline KPI for a period in
    one call: gross margin, payroll % of revenue, burn rate, and cash
    runway (trailing 3 months)."""
    result = {
        "period": period,
        "gross_margin_pct": metrics.gross_margin_pct(conn, period),
        "payroll_pct_of_revenue": metrics.payroll_pct_of_revenue(conn, period),
        "burn_rate": metrics.burn_rate(conn, period),
        "cash_runway": metrics.cash_runway_months(conn, period),
    }
    _log(conn, "read", "get_metrics", {"period": period}, {"period": period})
    return result


def delegate_to_agent(conn: sqlite3.Connection, agent_name: str, question: str) -> dict:
    """The real delegation call. Runs a short, live, read-only Q&A loop
    against the named specialist (agents/cfo/delegate.py) and returns its
    answer. Every specialist keeps its own model tier and its own read-only
    tools — nothing this triggers can write anywhere."""
    inputs = {"agent_name": agent_name, "question": question}
    result = delegate.ask_specialist(conn, agent_name, question)
    _log(conn, "delegate", "delegate_to_agent", inputs,
         {"status": result.get("status"), "tool_calls": result.get("tool_calls")},
         related_entity_type="agent", related_entity_id=agent_name,
         notes=result.get("answer") if result.get("status") == "answered" else result.get("reason"))
    return result


def submit_briefing(conn: sqlite3.Connection, summary: str, facts: list[str], hypotheses: list[str],
                      recommendations: list[str], open_questions: list[str],
                      agents_delegated_to: list[str]) -> dict:
    """Terminal tool — not a write to any table, just the CFO's own
    structured output, logged to audit_log like everything else. Section 3:
    every claim in the briefing should trace to a specialist's structured
    output; that's why facts/hypotheses/recommendations/open_questions are
    separate, explicitly labeled fields rather than one prose blob."""
    outputs = {"facts": facts, "hypotheses": hypotheses, "recommendations": recommendations,
               "open_questions": open_questions, "agents_delegated_to": agents_delegated_to}
    _log(conn, "briefing", "submit_briefing", {"summary": summary}, outputs)
    return {"status": "briefing_received"}
