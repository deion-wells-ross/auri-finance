"""KPI calculations. All pure formulas over statements.py / payroll_calc.py
output — no metric here is estimated or inferred, only computed. Section 9:
interpreting *why* a metric moved is FP&A/CFO's job, not this module's.
"""

from __future__ import annotations
import sqlite3
from services import statements as stmt


def gross_margin_pct(conn: sqlite3.Connection, period: str) -> float | None:
    return stmt.income_statement(conn, period)["gross_margin_pct"]


def payroll_pct_of_revenue(conn: sqlite3.Connection, period: str) -> dict:
    inc = stmt.income_statement(conn, period)
    revenue = inc["revenue"]["total"]
    salary_accounts = {"6000", "6010", "6020", "6030", "6040", "6100"}  # salaries + payroll tax
    payroll_cost = sum(l["amount"] for l in inc["opex"]["lines"] if l["code"] in salary_accounts)
    pct = round(payroll_cost / revenue * 100, 2) if revenue else None
    return {"period": period, "revenue": revenue, "payroll_cost": round(payroll_cost, 2), "pct_of_revenue": pct}


def budget_vs_actual(conn: sqlite3.Connection, department_id: str, account_id: str, period: str) -> dict:
    budget_row = conn.execute(
        "SELECT budgeted_amount FROM budgets WHERE department_id=? AND account_id=? AND period=?",
        (department_id, account_id, period),
    ).fetchone()
    budgeted = budget_row["budgeted_amount"] if budget_row else None

    actual_row = conn.execute(
        """SELECT SUM(CASE WHEN debit_credit='debit' THEN amount ELSE -amount END) AS amount
           FROM gl_transactions WHERE department_id=? AND account_id=? AND period=?""",
        (department_id, account_id, period),
    ).fetchone()
    actual = round(actual_row["amount"], 2) if actual_row and actual_row["amount"] is not None else 0.0

    if budgeted is None:
        return {"department_id": department_id, "account_id": account_id, "period": period,
                "budgeted": None, "actual": actual, "variance_pct": None, "variance_amount": None}

    variance_amount = round(actual - budgeted, 2)
    variance_pct = round(variance_amount / budgeted * 100, 2) if budgeted else None
    return {
        "department_id": department_id, "account_id": account_id, "period": period,
        "budgeted": round(budgeted, 2), "actual": actual,
        "variance_amount": variance_amount, "variance_pct": variance_pct,
    }


def burn_rate(conn: sqlite3.Connection, period: str) -> dict:
    cf = stmt.cash_flow_statement(conn, period)
    return {"period": period, "net_change_in_cash": cf["net_change_in_cash"],
            "is_burning": cf["net_change_in_cash"] < 0}


def cash_runway_months(conn: sqlite3.Connection, as_of_period: str, trailing_months: int = 3) -> dict:
    periods = [r["period"] for r in conn.execute(
        "SELECT DISTINCT period FROM gl_transactions WHERE period <= ? ORDER BY period DESC LIMIT ?",
        (as_of_period, trailing_months),
    ).fetchall()]
    burns = [burn_rate(conn, p)["net_change_in_cash"] for p in periods]
    avg_burn = sum(burns) / len(burns) if burns else 0.0
    cash_row = conn.execute(
        """SELECT COALESCE(SUM(CASE WHEN debit_credit='debit' THEN amount ELSE -amount END),0) AS cash
           FROM gl_transactions WHERE account_id='cash' AND period <= ?""",
        (as_of_period,),
    ).fetchone()
    ending_cash = round(cash_row["cash"], 2)
    if avg_burn >= 0:
        return {"as_of_period": as_of_period, "ending_cash": ending_cash, "avg_monthly_burn": round(avg_burn, 2),
                "runway_months": None, "note": "not burning cash over the trailing window"}
    runway = round(ending_cash / abs(avg_burn), 1)
    return {"as_of_period": as_of_period, "ending_cash": ending_cash,
            "avg_monthly_burn": round(avg_burn, 2), "runway_months": runway}
