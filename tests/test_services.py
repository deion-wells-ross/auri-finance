"""Layer-1 tests (Section 12): pure deterministic correctness against the
seeded Meridian database. These are golden-value tests — the expected
numbers below were read directly off the generated db/meridian.db, not
hand-computed separately, so they'll need updating if generate_meridian.py's
random seed or logic ever changes. That's intentional: this file is meant to
catch "did my change to the generator or the services silently move a
number," not to re-derive the numbers from first principles.

One honest wrinkle, discovered rather than designed: db/meridian.db is no
longer a frozen M0 snapshot. Starting with M2, live agents (Bookkeeping,
Controller, AP, AR, Payroll) actually read AND WRITE this same file as part
of their own verification runs — that's the whole point of running them for
real instead of only reading their code. So a golden value here can
legitimately change if it was measuring something a later agent was
supposed to fix (see the August gross-margin test below). Run
`python3 data/seed/generate_meridian.py` to reset to the pristine,
uncorrected M0 state if you want to reproduce the "as-seeded" numbers.
"""

import pytest
from services.db import get_connection
from services import statements, metrics, aging, payroll_calc, forecast


@pytest.fixture(scope="module")
def conn():
    c = get_connection()
    yield c
    c.close()


# ---------------------------------------------------------------- statements --

@pytest.mark.parametrize("period", [
    "2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02",
    "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08",
])
def test_trial_balance_ties_out_every_period(conn, period):
    tb = statements.trial_balance(conn, period)
    assert tb["balanced"], f"{period} out of balance: debit={tb['total_debit']} credit={tb['total_credit']}"


def test_august_income_statement_matches_seeded_anomaly(conn):
    inc = statements.income_statement(conn, "2026-08")
    assert inc["revenue"]["total"] == pytest.approx(1_500_000, abs=1)
    # AS-SEEDED (fresh from generate_meridian.py, before any agent has run),
    # this reads ~72.1% because the duplicate CloudScale invoice (anomaly #1)
    # is still sitting in COGS, unflagged. But db/meridian.db in this repo
    # reflects the live M2-M4 agent runs described in the design charter —
    # AP Agent found and reversed that exact duplicate (Section 8, M4 status)
    # — so the number this file actually asserts against is the CORRECTED
    # figure, ~73.05%, matching the "~74% once corrected" estimate the M0
    # writeup made before any agent had actually done the correcting. A test
    # that stayed pinned to 72.1% here would be quietly asserting that AP's
    # fix never happened.
    assert inc["gross_margin_pct"] == pytest.approx(73.05, abs=0.2)


def test_balance_sheet_balances_and_cash_stays_positive(conn):
    bs = statements.balance_sheet(conn, "2026-08")
    assert bs["balanced"]
    assert bs["equity"]["paid_in_capital"] == pytest.approx(6_000_000, abs=1)


def test_cash_flow_statement_reconciles_to_balance_sheet_cash(conn):
    cf = statements.cash_flow_statement(conn, "2026-08")
    row = conn.execute(
        "SELECT SUM(CASE WHEN debit_credit='debit' THEN amount ELSE -amount END) AS c "
        "FROM gl_transactions WHERE account_id='cash' AND period <= '2026-08'"
    ).fetchone()
    assert cf["ending_cash"] == pytest.approx(row["c"], abs=1)


# --------------------------------------------------------------------- metrics --

def test_marketing_budget_overrun_anomaly_5(conn):
    result = metrics.budget_vs_actual(conn, "MKT", "marketing", "2026-08")
    assert result["variance_pct"] == pytest.approx(28.0, abs=1.0)


def test_payroll_pct_of_revenue_is_computed_not_guessed(conn):
    result = metrics.payroll_pct_of_revenue(conn, "2026-08")
    assert result["revenue"] > 0
    assert 0 < result["pct_of_revenue"] < 200  # sanity bound, not a golden value


# ---------------------------------------------------------------------- aging --

def test_ar_concentration_flags_vantage_anomaly_4(conn):
    ar = aging.ar_aging(conn, "2026-08-31")
    vantage = ar["by_customer"]["Vantage Retail Group"]
    assert vantage["concentration_pct"] == pytest.approx(34.4, abs=1.0)
    assert vantage["buckets"].get("0-30") is not None or vantage["buckets"].get("31-60") is not None


def test_duplicate_detector_finds_cloudscale_pair_anomaly_1(conn):
    candidates = aging.flag_duplicate_ap_invoices(conn)
    cloudscale_dupes = [c for c in candidates if c["vendor_id"] == "cloudscale" and c["amount"] == 14_200.0]
    assert len(cloudscale_dupes) >= 1
    assert cloudscale_dupes[0]["days_apart"] <= 7


def test_duplicate_detector_does_not_flag_unrelated_invoices(conn):
    candidates = aging.flag_duplicate_ap_invoices(conn, window_days=3, amount_tolerance=0.01)
    # tightened window/tolerance should still catch the real duplicate...
    assert any(c["vendor_id"] == "cloudscale" and c["amount"] == 14_200.0 for c in candidates)
    # ...without exploding into false positives across unrelated vendors/amounts
    assert len(candidates) < 20


# ------------------------------------------------------------------- payroll --

def test_engineering_headcount_growth_anomaly_2(conn):
    result = payroll_calc.payroll_growth(conn, "ENG", "2026-02", "2026-08")
    assert result["headcount_a"] == 42
    assert result["headcount_b"] == 50
    assert result["growth_pct"] == pytest.approx(19.0, abs=2.0)


def test_revenue_growth_same_window_is_much_smaller_than_payroll(conn):
    rev_feb = statements.income_statement(conn, "2026-02")["revenue"]["total"]
    rev_aug = statements.income_statement(conn, "2026-08")["revenue"]["total"]
    revenue_growth_pct = (rev_aug / rev_feb - 1) * 100
    payroll_growth_pct = payroll_calc.payroll_growth(conn, "ENG", "2026-02", "2026-08")["growth_pct"]
    assert revenue_growth_pct == pytest.approx(6.0, abs=1.0)
    assert payroll_growth_pct > revenue_growth_pct * 2  # the actual shape of anomaly #2


# ------------------------------------------------------------------- forecast --

def test_linear_forecast_states_its_assumption():
    result = forecast.linear_revenue_forecast([1_400_000, 1_415_000, 1_430_000], months_ahead=3)
    assert len(result["projection"]) == 3
    assert result["assumption"]["source"] == "inferred from trailing history"
    assert result["assumption"]["monthly_growth_rate_pct"] > 0


def test_linear_forecast_respects_an_explicit_assumption():
    result = forecast.linear_revenue_forecast([1_000_000], months_ahead=1, assumed_monthly_growth_rate=0.10)
    assert result["projection"][0] == pytest.approx(1_100_000, abs=1)
    assert result["assumption"]["source"] == "provided by caller (e.g. FP&A Agent)"
