"""Forecast projection MATH ONLY. Choosing/defending the growth-rate
assumption is FP&A's job (Section 9) — this module refuses to guess one
silently. Every result states the assumption it used, in the return value,
never buried in a docstring.
"""

from __future__ import annotations


def linear_revenue_forecast(historical_monthly_values: list[float], months_ahead: int,
                             assumed_monthly_growth_rate: float | None = None) -> dict:
    """historical_monthly_values: ordered oldest -> newest.
    If assumed_monthly_growth_rate is not given, it's inferred as the average
    month-over-month growth rate across the provided history — and that
    inferred value is returned alongside the projection, labeled as an
    assumption, not presented as fact."""
    if not historical_monthly_values:
        raise ValueError("need at least 1 historical point to project from")
    if assumed_monthly_growth_rate is None and len(historical_monthly_values) < 2:
        raise ValueError("need at least 2 historical points to INFER a growth rate "
                          "(pass assumed_monthly_growth_rate explicitly to project from a single point)")

    inferred = assumed_monthly_growth_rate is None
    if inferred:
        rates = [
            (b / a - 1) for a, b in zip(historical_monthly_values, historical_monthly_values[1:]) if a
        ]
        assumed_monthly_growth_rate = sum(rates) / len(rates) if rates else 0.0

    projection = []
    last = historical_monthly_values[-1]
    for _ in range(months_ahead):
        last = round(last * (1 + assumed_monthly_growth_rate), 2)
        projection.append(last)

    return {
        "projection": projection,
        "assumption": {
            "monthly_growth_rate_pct": round(assumed_monthly_growth_rate * 100, 3),
            "source": "inferred from trailing history" if inferred else "provided by caller (e.g. FP&A Agent)",
        },
    }


def budget_variance(budgeted: float, actual: float) -> dict:
    """Low-level shared helper — metrics.budget_vs_actual() wraps this with
    the department/account/period lookup; kept here too since FP&A's
    scenario modeling needs the same math without a DB round-trip."""
    variance_amount = round(actual - budgeted, 2)
    variance_pct = round(variance_amount / budgeted * 100, 2) if budgeted else None
    return {"budgeted": round(budgeted, 2), "actual": round(actual, 2),
            "variance_amount": variance_amount, "variance_pct": variance_pct}
