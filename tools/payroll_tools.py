"""Payroll & Workforce Agent's tool surface.

Entirely read-only, per Section 3's matrix: "Review-required: none (no
write actions in V1)." This agent investigates labor-cost risk and reports
what it finds — it never touches payroll_runs, employees, or the GL. Not
every agent in this system needs a write tool, and this one is the clearest
proof of that.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import payroll_calc, statements  # noqa: E402

AGENT_NAME = "payroll_agent"
DEPARTMENTS = ["ENG", "SALES", "MKT", "CS", "GA"]
SALARY_ACCOUNT_BY_DEPT = {"ENG": "sal_eng", "SALES": "sal_sales", "MKT": "sal_mkt", "CS": "sal_cs", "GA": "sal_ga"}


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


def get_payroll_summary(conn: sqlite3.Connection, period: str) -> dict:
    result = payroll_calc.department_payroll_summary(conn, period)
    _log(conn, "read", "get_payroll_summary", {"period": period}, {"total_cost": result["total_cost"]})
    return result


def get_headcount_by_dept(conn: sqlite3.Connection, as_of_date: str) -> dict:
    """Read-only. Headcount for every department as of a date, in one call
    — saves the agent five round trips to ask about each department."""
    result = {d: payroll_calc.headcount_as_of(conn, d, as_of_date) for d in DEPARTMENTS}
    _log(conn, "read", "get_headcount_by_dept", {"as_of_date": as_of_date}, result)
    return result


def compare_payroll_growth(conn: sqlite3.Connection, department_id: str, period_a: str, period_b: str) -> dict:
    result = payroll_calc.payroll_growth(conn, department_id, period_a, period_b)
    _log(conn, "read", "compare_payroll_growth", {"department_id": department_id, "period_a": period_a, "period_b": period_b}, result)
    return result


def get_revenue_growth(conn: sqlite3.Connection, period_a: str, period_b: str) -> dict:
    """Read-only. The other half of anomaly #2's actual test: payroll
    growth in a department only means something set against revenue growth
    over the SAME window. This wraps the income statement rather than
    payroll_calc since revenue isn't a payroll concept — but the agent
    needs it to reason about the ratio, not just the raw payroll number."""
    rev_a = statements.income_statement(conn, period_a)["revenue"]["total"]
    rev_b = statements.income_statement(conn, period_b)["revenue"]["total"]
    growth_pct = round((rev_b / rev_a - 1) * 100, 2) if rev_a else None
    result = {"period_a": period_a, "period_b": period_b, "revenue_a": rev_a, "revenue_b": rev_b, "growth_pct": growth_pct}
    _log(conn, "read", "get_revenue_growth", {"period_a": period_a, "period_b": period_b}, result)
    return result


def get_salary_account_map(conn: sqlite3.Connection) -> dict:
    """Read-only. Which chart-of-accounts account_id holds each department's
    salary line — needed before calling compare_to_budget, since budgets are
    keyed by the real account_id (e.g. 'sal_eng'), not a generic 'payroll'
    label that doesn't exist in the chart of accounts."""
    _log(conn, "read", "get_salary_account_map", {}, SALARY_ACCOUNT_BY_DEPT)
    return dict(SALARY_ACCOUNT_BY_DEPT)


def compare_to_budget(conn: sqlite3.Connection, department_id: str, account_id: str, period: str) -> dict:
    from services import metrics
    result = metrics.budget_vs_actual(conn, department_id, account_id, period)
    _log(conn, "read", "compare_to_budget", {"department_id": department_id, "account_id": account_id, "period": period}, result)
    return result
