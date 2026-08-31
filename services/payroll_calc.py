"""Payroll totals and headcount — deterministic. Judging whether a number
here is a *problem* is Payroll & Workforce Agent's job (M4), not this
module's; this only computes what happened.
"""

from __future__ import annotations
import sqlite3


def department_payroll_summary(conn: sqlite3.Connection, period: str) -> dict:
    rows = conn.execute(
        """SELECT prl.department_id, d.name,
                  SUM(prl.gross_pay) AS gross, SUM(prl.overtime_pay) AS ot,
                  SUM(prl.employer_tax_burden) AS tax, COUNT(DISTINCT prl.employee_id) AS headcount
           FROM payroll_run_lines prl
           JOIN payroll_runs pr ON prl.payroll_run_id = pr.payroll_run_id
           JOIN departments d ON prl.department_id = d.department_id
           WHERE pr.period = ? GROUP BY prl.department_id ORDER BY prl.department_id""",
        (period,),
    ).fetchall()
    by_dept = {
        r["department_id"]: {
            "name": r["name"], "gross_pay": round(r["gross"], 2), "overtime_pay": round(r["ot"], 2),
            "tax_burden": round(r["tax"], 2), "headcount": r["headcount"],
            "total_cost": round(r["gross"] + r["ot"] + r["tax"], 2),
        }
        for r in rows
    }
    return {"period": period, "by_department": by_dept,
            "total_cost": round(sum(d["total_cost"] for d in by_dept.values()), 2)}


def headcount_as_of(conn: sqlite3.Connection, department_id: str, as_of_date: str) -> int:
    row = conn.execute(
        """SELECT COUNT(*) AS n FROM employees
           WHERE department_id = ? AND start_date <= ? AND (end_date IS NULL OR end_date > ?)""",
        (department_id, as_of_date, as_of_date),
    ).fetchone()
    return row["n"]


def payroll_growth(conn: sqlite3.Connection, department_id: str, period_a: str, period_b: str) -> dict:
    """Growth in total payroll cost for one department between two periods.
    Pair this with an income_statement() revenue comparison over the same
    window to check payroll growth against revenue growth (anomaly #2's
    actual test) — that comparison is FP&A's job, not this function's."""
    a = department_payroll_summary(conn, period_a)["by_department"].get(department_id)
    b = department_payroll_summary(conn, period_b)["by_department"].get(department_id)
    if not a or not b:
        return {"department_id": department_id, "period_a": period_a, "period_b": period_b, "error": "no data"}
    growth_pct = round((b["total_cost"] / a["total_cost"] - 1) * 100, 2) if a["total_cost"] else None
    return {
        "department_id": department_id, "period_a": period_a, "period_b": period_b,
        "cost_a": a["total_cost"], "cost_b": b["total_cost"],
        "headcount_a": a["headcount"], "headcount_b": b["headcount"],
        "growth_pct": growth_pct,
    }
