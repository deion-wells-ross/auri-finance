"""FP&A Agent's tool surface.

Scope, per Section 2/3: FP&A is forward-looking judgment over numbers the
deterministic layer already computes — explaining *why* a variance or a
margin trend happened, and setting forecast assumptions from evidence.
Every formula here is a thin wrapper around services/statements.py,
services/metrics.py, and services/forecast.py; nothing in this file does
its own arithmetic.

set_forecast_assumptions is FP&A's one real write, and it's the sharpest
edge of Section 9's "never silent" rule: forecast.py's own docstring says
an assumption always gets returned labeled, never buried — this table is
where that principle gets a permanent home instead of vanishing when the
run ends. Per Section 3 ("Review-required: forecast assumption changes
that materially shift outlook"), a change of more than 1.0 percentage
point in the monthly growth rate from the most recently active assumption
is refused as a direct write and filed as an approval instead — the same
threshold-plus-approval-fallback shape as Controller's materiality gate,
not a new pattern invented for this one tool.
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
from services import forecast  # noqa: E402

AGENT_NAME = "fpa_agent"
MATERIAL_ASSUMPTION_SHIFT_PP = 1.0  # percentage points of monthly growth rate


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


def get_income_statement(conn: sqlite3.Connection, period: str) -> dict:
    """Read-only. Full income statement for one period, straight from the
    deterministic statements service."""
    result = stmt.income_statement(conn, period)
    _log(conn, "read", "get_income_statement", {"period": period}, {"period": period})
    return result


def get_revenue_history(conn: sqlite3.Connection, period_start: str, period_end: str) -> list[dict]:
    """Read-only. Monthly revenue for every period in [period_start, period_end]
    inclusive — the raw series run_forecast_model needs as input, and the
    same series a trend claim about revenue should be checked against."""
    periods = [r["period"] for r in conn.execute(
        "SELECT DISTINCT period FROM gl_transactions WHERE period BETWEEN ? AND ? ORDER BY period",
        (period_start, period_end),
    ).fetchall()]
    result = [{"period": p, "revenue": stmt.income_statement(conn, p)["revenue"]["total"]} for p in periods]
    _log(conn, "read", "get_revenue_history", {"period_start": period_start, "period_end": period_end},
         {"count": len(result)})
    return result


def get_gross_margin_trend(conn: sqlite3.Connection, period_start: str, period_end: str) -> list[dict]:
    """Read-only. Gross margin % for every period in range — the trend
    line behind anomaly #3 (78% -> 76% -> ~73% as COGS outpaces pricing)."""
    periods = [r["period"] for r in conn.execute(
        "SELECT DISTINCT period FROM gl_transactions WHERE period BETWEEN ? AND ? ORDER BY period",
        (period_start, period_end),
    ).fetchall()]
    result = [{"period": p, "gross_margin_pct": metrics.gross_margin_pct(conn, p)} for p in periods]
    _log(conn, "read", "get_gross_margin_trend", {"period_start": period_start, "period_end": period_end},
         {"count": len(result)})
    return result


def get_all_budget_variances(conn: sqlite3.Connection, period: str) -> list[dict]:
    """Read-only. Every department/account line with a budget for this
    period, variance computed for each, sorted by |variance_pct| descending
    so the worst misses surface first — no code opinion on what's material,
    that's FP&A's job (Section 9)."""
    lines = conn.execute(
        "SELECT DISTINCT department_id, account_id FROM budgets WHERE period = ?", (period,)
    ).fetchall()
    results = [metrics.budget_vs_actual(conn, l["department_id"], l["account_id"], period) for l in lines]
    results.sort(key=lambda r: abs(r["variance_pct"]) if r["variance_pct"] is not None else -1, reverse=True)
    _log(conn, "read", "get_all_budget_variances", {"period": period}, {"count": len(results)})
    return results


def get_budget_vs_actual(conn: sqlite3.Connection, department_id: str, account_id: str, period: str) -> dict:
    """Read-only. Single-line drill-down, once get_all_budget_variances has
    pointed at which department/account is worth explaining."""
    result = metrics.budget_vs_actual(conn, department_id, account_id, period)
    _log(conn, "read", "get_budget_vs_actual", {"department_id": department_id, "account_id": account_id,
                                                   "period": period}, result)
    return result


def run_forecast_model(conn: sqlite3.Connection, historical_values: list[float], months_ahead: int,
                         assumed_monthly_growth_rate: float | None = None) -> dict:
    """Pure math, no DB read — wraps services/forecast.py directly. Always
    returns its assumption labeled (inferred vs. provided), per that
    module's own rule against silent assumptions."""
    result = forecast.linear_revenue_forecast(historical_values, months_ahead, assumed_monthly_growth_rate)
    _log(conn, "read", "run_forecast_model",
         {"months_ahead": months_ahead, "assumed_monthly_growth_rate": assumed_monthly_growth_rate},
         {"assumption": result["assumption"]})
    return result


def get_latest_forecast_assumption(conn: sqlite3.Connection) -> dict | None:
    """Read-only. The currently active assumption, if one has ever been set
    — what set_forecast_assumptions compares a new one against to decide
    whether the change is material enough to need a human."""
    row = conn.execute(
        "SELECT * FROM forecast_assumptions WHERE status = 'active' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    result = dict(row) if row else None
    _log(conn, "read", "get_latest_forecast_assumption", {}, {"found": result is not None})
    return result


def set_forecast_assumptions(conn: sqlite3.Connection, period: str, monthly_growth_rate_pct: float,
                               basis: str, notes: str = "") -> dict:
    """Write — FP&A's one real write tool. Refuses a silent or unsupported
    assumption (empty basis), and refuses to just overwrite a prior
    assumption in place: superseded rows stay in the table, not deleted.
    A shift of more than MATERIAL_ASSUMPTION_SHIFT_PP percentage points from
    the currently active assumption doesn't get written directly at all —
    it's filed as a pending approval instead, per Section 3's "material
    shift needs review" line, the same threshold-plus-approval shape as
    every other gated write in this system."""
    inputs = {"period": period, "monthly_growth_rate_pct": monthly_growth_rate_pct, "basis": basis, "notes": notes}

    if not basis or not basis.strip():
        outputs = {"status": "refused", "reason": "basis is required — an assumption with no stated evidence "
                                                    "is exactly what forecast.py's docstring warns against"}
        _log(conn, "write_refused", "set_forecast_assumptions", inputs, outputs)
        return outputs

    prior = conn.execute(
        "SELECT * FROM forecast_assumptions WHERE status = 'active' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    if prior is not None:
        shift = abs(monthly_growth_rate_pct - prior["monthly_growth_rate_pct"])
        if shift > MATERIAL_ASSUMPTION_SHIFT_PP:
            approval_id = f"appr_{uuid.uuid4().hex[:10]}"
            conn.execute(
                """INSERT INTO approvals
                   (approval_id, action_type, entity_type, entity_id, requested_by, status, notes)
                   VALUES (?,?,?,?,?,?,?)""",
                (approval_id, "revise_forecast_assumption", "forecast_assumptions", period, AGENT_NAME, "pending",
                 f"Proposed monthly growth rate {monthly_growth_rate_pct}% vs. active {prior['monthly_growth_rate_pct']}% "
                 f"— a {round(shift, 2)}pp shift exceeds the {MATERIAL_ASSUMPTION_SHIFT_PP}pp review threshold. "
                 f"Basis: {basis}"),
            )
            conn.commit()
            outputs = {"status": "review_required", "approval_id": approval_id,
                       "shift_pp": round(shift, 2), "threshold_pp": MATERIAL_ASSUMPTION_SHIFT_PP,
                       "prior_rate_pct": prior["monthly_growth_rate_pct"]}
            _log(conn, "write_refused", "set_forecast_assumptions", inputs, outputs,
                 "forecast_assumptions", None, notes="material shift filed for human review instead of written directly")
            return outputs

    if prior is not None:
        conn.execute("UPDATE forecast_assumptions SET status = 'superseded' WHERE assumption_id = ?",
                     (prior["assumption_id"],))

    assumption_id = f"fca_{uuid.uuid4().hex[:10]}"
    conn.execute(
        """INSERT INTO forecast_assumptions
           (assumption_id, period, monthly_growth_rate_pct, basis, set_by_agent, status, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (assumption_id, period, monthly_growth_rate_pct, basis, AGENT_NAME, "active", _now()),
    )
    conn.commit()
    outputs = {"status": "set", "assumption_id": assumption_id}
    _log(conn, "write", "set_forecast_assumptions", inputs, outputs, "forecast_assumptions", assumption_id, notes=notes)
    return outputs
