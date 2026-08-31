"""Deterministic financial statements — no LLM involved anywhere in this file.

Per Section 9 of the architecture charter: if it's a formula, it's code.
Every function here returns a plain, JSON-serializable dict so an agent tool
can hand the result straight to another agent as a structured output.
"""

from __future__ import annotations
import sqlite3
from typing import Optional


def trial_balance(conn: sqlite3.Connection, period: str) -> dict:
    """Debits and credits by account for a single period. Balanced iff
    total_debit == total_credit — that equality IS the Controller's
    structural validation check (Section 10), not a separate algorithm."""
    rows = conn.execute(
        """SELECT coa.account_id, coa.code, coa.name, coa.type,
                  COALESCE(SUM(CASE WHEN gl.debit_credit='debit' THEN gl.amount ELSE 0 END),0) AS debit,
                  COALESCE(SUM(CASE WHEN gl.debit_credit='credit' THEN gl.amount ELSE 0 END),0) AS credit
           FROM chart_of_accounts coa
           LEFT JOIN gl_transactions gl ON gl.account_id = coa.account_id AND gl.period = ?
           GROUP BY coa.account_id ORDER BY coa.code""",
        (period,),
    ).fetchall()
    lines = [
        {
            "account_id": r["account_id"], "code": r["code"], "name": r["name"], "type": r["type"],
            "debit": round(r["debit"], 2), "credit": round(r["credit"], 2),
            "net": round(r["debit"] - r["credit"], 2),
        }
        for r in rows
    ]
    total_debit = round(sum(l["debit"] for l in lines), 2)
    total_credit = round(sum(l["credit"] for l in lines), 2)
    return {
        "period": period,
        "lines": lines,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": abs(total_debit - total_credit) < 0.01,
    }


def _sum_by_account(conn, period, account_type, normal_side) -> list[dict]:
    """normal_side: 'debit' or 'credit' — the side that INCREASES this account
    type, per its normal_balance. Revenue is credit-normal; cogs/opex are
    debit-normal. Returned amounts are always positive when the account moved
    in its normal direction."""
    increase, decrease = ("debit", "credit") if normal_side == "debit" else ("credit", "debit")
    rows = conn.execute(
        f"""SELECT coa.account_id, coa.code, coa.name,
                   SUM(CASE WHEN gl.debit_credit='{increase}' THEN gl.amount ELSE -gl.amount END) AS amount
            FROM gl_transactions gl JOIN chart_of_accounts coa ON gl.account_id = coa.account_id
            WHERE gl.period = ? AND coa.type = ?
            GROUP BY coa.account_id ORDER BY coa.code""",
        (period, account_type),
    ).fetchall()
    return [{"account_id": r["account_id"], "code": r["code"], "name": r["name"], "amount": round(r["amount"], 2)}
            for r in rows]


def income_statement(conn: sqlite3.Connection, period: str) -> dict:
    revenue_lines = _sum_by_account(conn, period, "revenue", "credit")
    cogs_lines = _sum_by_account(conn, period, "cogs", "debit")
    opex_lines = _sum_by_account(conn, period, "opex", "debit")

    revenue_total = round(sum(l["amount"] for l in revenue_lines), 2)
    cogs_total = round(sum(l["amount"] for l in cogs_lines), 2)
    opex_total = round(sum(l["amount"] for l in opex_lines), 2)
    gross_profit = round(revenue_total - cogs_total, 2)
    operating_income = round(gross_profit - opex_total, 2)
    gross_margin_pct = round(gross_profit / revenue_total * 100, 2) if revenue_total else None

    return {
        "period": period,
        "revenue": {"lines": revenue_lines, "total": revenue_total},
        "cogs": {"lines": cogs_lines, "total": cogs_total},
        "gross_profit": gross_profit,
        "gross_margin_pct": gross_margin_pct,
        "opex": {"lines": opex_lines, "total": opex_total},
        "operating_income": operating_income,
    }


def balance_sheet(conn: sqlite3.Connection, as_of_period: str) -> dict:
    """Cumulative, all periods through and including as_of_period.

    Equity is NOT read off a ledger balance for retained earnings — this
    model never posts formal period-close entries. Equity is constructed as
    paid-in capital (the equity-type accounts, e.g. common stock) plus
    cumulative net income (revenue - cogs - opex, all-time through this
    period). That construction rule is itself a deterministic decision worth
    documenting: it's the standard way to derive a balance sheet from a flat
    transaction ledger that was never formally closed.
    """
    def cumulative(types, side):
        placeholders = ",".join("?" * len(types))
        row = conn.execute(
            f"""SELECT COALESCE(SUM(CASE WHEN gl.debit_credit=? THEN gl.amount ELSE -gl.amount END),0)
                FROM gl_transactions gl JOIN chart_of_accounts coa ON gl.account_id = coa.account_id
                WHERE coa.type IN ({placeholders}) AND gl.period <= ?""",
            (side, *types, as_of_period),
        ).fetchone()
        return round(row[0], 2)

    assets_total = cumulative(["asset"], "debit")
    liabilities_total = cumulative(["liability"], "credit")
    paid_in_capital = cumulative(["equity"], "credit")

    rev = cumulative(["revenue"], "credit")
    exp = cumulative(["cogs", "opex"], "debit")
    cumulative_net_income = round(rev - exp, 2)
    equity_total = round(paid_in_capital + cumulative_net_income, 2)

    return {
        "as_of_period": as_of_period,
        "assets": {"total": assets_total},
        "liabilities": {"total": liabilities_total},
        "equity": {
            "paid_in_capital": paid_in_capital,
            "cumulative_net_income": cumulative_net_income,
            "total": equity_total,
        },
        "balanced": abs(assets_total - (liabilities_total + equity_total)) < 0.01,
    }


def cash_flow_statement(conn: sqlite3.Connection, period: str) -> dict:
    """Direct method: every gl_transactions line touching the cash account,
    grouped by source_type. Simplified — no indirect reconciliation to net
    income, since every cash movement in this model is already a direct
    posting rather than inferred from accrual accounts."""
    rows = conn.execute(
        """SELECT source_type,
                  SUM(CASE WHEN debit_credit='debit' THEN amount ELSE -amount END) AS net
           FROM gl_transactions WHERE period = ? AND account_id = 'cash'
           GROUP BY source_type ORDER BY source_type""",
        (period,),
    ).fetchall()
    by_source = [{"source_type": r["source_type"], "net": round(r["net"], 2)} for r in rows]
    net_change = round(sum(r["net"] for r in by_source), 2)

    prior_periods = conn.execute(
        "SELECT DISTINCT period FROM gl_transactions WHERE period < ? ORDER BY period", (period,)
    ).fetchall()
    beginning_cash = 0.0
    if prior_periods:
        row = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN debit_credit='debit' THEN amount ELSE -amount END),0)
               FROM gl_transactions WHERE account_id='cash' AND period < ?""",
            (period,),
        ).fetchone()
        beginning_cash = round(row[0], 2)
    ending_cash = round(beginning_cash + net_change, 2)

    return {
        "period": period,
        "by_source": by_source,
        "net_change_in_cash": net_change,
        "beginning_cash": beginning_cash,
        "ending_cash": ending_cash,
    }
