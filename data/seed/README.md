# Meridian Analytics — synthetic dataset (M0)

Generates `db/meridian.db` from `db/schema.sql`: 11 clean historical months
(Sep 2025 – Jul 2026) plus one closing month (Aug 2026, `period_status = open`)
carrying six deliberately seeded anomalies. Deterministic — re-running
produces an identical database (`random.seed(42)`).

## Run it

```
cd auri-finance
python3 data/seed/generate_meridian.py
```

Prints row counts, a per-period trial balance check (should read `OK` for
all 12 periods), a foreign-key check, and a spot-check of all six anomalies
against their targets.

## The anomaly answer key (August 2026 close)

| # | Anomaly | Target | Where |
|---|---|---|---|
| 1 | Duplicate CloudScale Hosting invoice | Two $14,200 invoices, 3 days apart, `is_duplicate_of` linked | `ap_invoices` |
| 2 | Engineering payroll outpacing revenue | Headcount 42→50 (Feb→Aug), +19.0% vs. +6.0% revenue growth | `employees`, `payroll_runs` |
| 3 | Gross margin decline | 78%→76%→~74% (72.1% as-entered until #1 is corrected) | `gl_transactions` (COGS accounts) |
| 4 | AR concentration + aging | Vantage Retail Group ≈34% of open AR, unpaid past due | `ar_invoices`, `customers` |
| 5 | Marketing budget overrun | +28.0% vs. budget, driven by an unbudgeted trade-show line | `budgets` vs `gl_transactions` |
| 6 | Genuine bookkeeping ambiguity | $9,800 "Meridian Consulting Partners LLC," no confident account | `bank_feed` (`suggested_account_id IS NULL`) |

This is the scoring rubric for Section 12's agent evaluations later — an
agent run against this database should surface these six things without
being told where to look, and should *not* invent problems that aren't here.

## Notes for M1+ (deterministic services)

- All 11 historical periods are fully posted and closed (`period_status`) —
  safe to compute statements/metrics against directly.
- August is deliberately incomplete: the duplicate invoice is unflagged, the
  Vantage invoice is open, and `bank_feed` holds transactions Bookkeeping
  hasn't processed into `gl_transactions` yet. That's intentional — it's
  the raw material for the agent pipeline (M2 onward), not a bug.
- `correction_requests`, `approvals`, and `audit_log` are seeded empty —
  they get populated once the agents and orchestrator (M2–M5) exist.
