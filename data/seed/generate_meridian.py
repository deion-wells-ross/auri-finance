#!/usr/bin/env python3
"""
Synthetic data generator for Meridian Analytics (AURI Finance M0).

Produces db/meridian.db against db/schema.sql: 11 clean historical months
(Sep 2025 - Jul 2026, period_status = closed) plus one closing month
(Aug 2026, period_status = open) carrying six deliberately seeded anomalies.

Deterministic: fixed random seed, re-running produces the same database.
"""

import random
import sqlite3
import uuid
from datetime import date, timedelta
from pathlib import Path

random.seed(42)

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "db" / "meridian.db"
SCHEMA_PATH = ROOT / "db" / "schema.sql"

PERIODS = [
    "2025-09", "2025-10", "2025-11", "2025-12",
    "2026-01", "2026-02", "2026-03", "2026-04",
    "2026-05", "2026-06", "2026-07", "2026-08",
]
CLOSING_PERIOD = "2026-08"
PERIOD_LAST_DAY = {
    "2025-09": "2025-09-30", "2025-10": "2025-10-31", "2025-11": "2025-11-30",
    "2025-12": "2025-12-31", "2026-01": "2026-01-31", "2026-02": "2026-02-28",
    "2026-03": "2026-03-31", "2026-04": "2026-04-30", "2026-05": "2026-05-31",
    "2026-06": "2026-06-30", "2026-07": "2026-07-31", "2026-08": "2026-08-31",
}

# ---------------------------------------------------------------- helpers --

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def post_je(cur, period, txn_date, lines, source_type, source_id, memo):
    """lines: list of (account_id, department_id_or_None, amount, debit/credit).
    Must sum to zero (debits == credits) or this is a bug in the generator."""
    debit_total = sum(a for _, _, a, dc in lines if dc == "debit")
    credit_total = sum(a for _, _, a, dc in lines if dc == "credit")
    assert abs(debit_total - credit_total) < 0.01, (
        f"Unbalanced JE {memo}: debit={debit_total} credit={credit_total}"
    )
    je_id = new_id("je")
    for account_id, department_id, amount, dc in lines:
        cur.execute(
            """INSERT INTO gl_transactions
               (txn_id, je_id, txn_date, account_id, department_id, amount,
                debit_credit, memo, source_type, source_id, period, entered_by_agent, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (new_id("txn"), je_id, txn_date, account_id, department_id,
             round(amount, 2), dc, memo, source_type, source_id, period, "seed", "posted"),
        )
    return je_id


# ---------------------------------------------------------------- schema --

def build_schema(conn):
    conn.executescript(SCHEMA_PATH.read_text())


# ------------------------------------------------------------- reference --

DEPARTMENTS = [
    ("ENG", "Engineering", "CC-100"),
    ("SALES", "Sales", "CC-200"),
    ("MKT", "Marketing", "CC-300"),
    ("CS", "Customer Success", "CC-400"),
    ("GA", "General & Administrative", "CC-500"),
]

ACCOUNTS = [
    # id, code, name, type, normal_balance, is_control
    ("cash", "1000", "Cash", "asset", "debit", 0),
    ("ar", "1100", "Accounts Receivable", "asset", "debit", 1),
    ("prepaid", "1200", "Prepaid Expenses", "asset", "debit", 0),
    ("ap", "2000", "Accounts Payable", "liability", "credit", 1),
    ("accrued_payroll", "2100", "Accrued Payroll", "liability", "credit", 0),
    ("deferred_rev", "2200", "Deferred Revenue", "liability", "credit", 0),
    ("retained_earnings", "3000", "Retained Earnings", "equity", "credit", 0),
    ("common_stock", "3100", "Common Stock", "equity", "credit", 0),
    ("sub_rev", "4000", "Subscription Revenue", "revenue", "credit", 0),
    ("svc_rev", "4100", "Services Revenue", "revenue", "credit", 0),
    ("cogs_hosting", "5000", "Hosting & Infrastructure", "cogs", "debit", 0),
    ("cogs_support", "5100", "Customer Support Payroll (COGS)", "cogs", "debit", 0),
    ("cogs_data", "5200", "Third-Party Data Costs", "cogs", "debit", 0),
    ("sal_eng", "6000", "Salaries & Wages - Engineering", "opex", "debit", 0),
    ("sal_sales", "6010", "Salaries & Wages - Sales", "opex", "debit", 0),
    ("sal_mkt", "6020", "Salaries & Wages - Marketing", "opex", "debit", 0),
    ("sal_cs", "6030", "Salaries & Wages - Customer Success", "opex", "debit", 0),
    ("sal_ga", "6040", "Salaries & Wages - G&A", "opex", "debit", 0),
    ("payroll_tax", "6100", "Payroll Taxes & Benefits", "opex", "debit", 0),
    ("commissions", "6200", "Sales Commissions", "opex", "debit", 0),
    ("marketing", "6300", "Marketing & Advertising", "opex", "debit", 0),
    ("software", "6400", "Software & Tools", "opex", "debit", 0),
    ("travel", "6500", "Travel & Entertainment", "opex", "debit", 0),
    ("facilities", "6600", "Facilities & Rent", "opex", "debit", 0),
    ("prof_fees", "6700", "Professional Fees", "opex", "debit", 0),
    ("depreciation", "6800", "Depreciation & Amortization", "opex", "debit", 0),
]

SAL_ACCOUNT_BY_DEPT = {
    "ENG": "sal_eng", "SALES": "sal_sales", "MKT": "sal_mkt",
    "CS": "sal_cs", "GA": "sal_ga",
}

VENDORS = [
    ("cloudscale", "CloudScale Hosting", "hosting", "cogs_hosting", 30),
    ("datastream", "DataStream Analytics Infra", "hosting", "cogs_data", 30),
    ("toolsuite", "ToolSuite Software", "software", "software", 30),
    ("adworks", "AdWorks Media", "marketing", "marketing", 30),
    ("hartwell", "Hartwell & Cross LLP", "professional_services", "prof_fees", 30),
    ("meridian_consulting", "Meridian Consulting Partners LLC", "professional_services", "prof_fees", 30),
    ("primeoffice", "PrimeOffice Realty", "facilities", "facilities", 15),
]

FIRST_NAMES = ["Alex","Jordan","Taylor","Morgan","Casey","Riley","Jamie","Avery","Quinn","Cameron",
               "Drew","Skyler","Reese","Dakota","Peyton","Rowan","Emerson","Finley","Harper","Kendall",
               "Logan","Micah","Parker","Sage","Tatum","Blair","Charlie","Elliot","Frankie","Hayden"]
LAST_NAMES = ["Chen","Patel","Garcia","Nguyen","Kim","Brooks","Rivera","Cohen","Osei","Novak",
              "Ahmed","Fischer","Delgado","Watanabe","Larsson","Okafor","Petrov","Silva","Haddad","Moreau"]

def random_name(used):
    while True:
        n = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
        if n not in used:
            used.add(n)
            return n

CUSTOMER_NAMES = [
    "Vantage Retail Group","Northbridge Capital","Solano Foods","Kestrel Logistics","Bramwell Health",
    "Aurora Financial","Delta Robotics","Fenwick & Sons","Granite Peak Insurance","Harbor Freight Analytics",
    "Ironclad Manufacturing","Juniper Media Group","Kingsley Retail","Lumen Biosciences","Meadowlark Foods",
    "Nimbus Cloud Systems","Orchard Grove Retail","Pinnacle Freight","Quarry Point Energy","Redwood Legal",
    "Sequoia Telecom","Thornfield Bank","Union Square Media","Vertex Manufacturing","Westgate Retail",
    "Yellowstone Analytics","Zenith Logistics","Ashcroft Partners","Brightline Insurance","Cascade Foods",
    "Driftwood Media","Everline Health","Fairmont Capital","Glenwood Retail","Highpoint Robotics",
    "Ivywood Legal","Jasper Financial","Keystone Foods","Lakeside Manufacturing","Marrow Biosciences",
    "Norland Telecom","Oakhurst Insurance","Prairie Analytics","Ridgeline Freight","Stonebridge Retail",
]
INDUSTRIES = ["Retail","Financial Services","Food & Beverage","Logistics","Healthcare","Insurance",
              "Manufacturing","Media","Energy","Legal","Telecom","Biotech"]

# ------------------------------------------------------------------ main --

def seed_opening_balance(conn):
    """Without this, cumulative cash goes deeply negative — the model has 11+ months of
    real opex/payroll burn against revenue that doesn't cover it (realistic for a growth-stage
    SaaS company), but nothing was ever seeded as paid-in capital to fund that burn. One
    opening entry, dated to the first day of history, fixes it and makes the balance sheet
    tie out as: assets = liabilities + (paid-in capital + cumulative net income)."""
    cur = conn.cursor()
    post_je(
        cur, "2025-09", "2025-09-01",
        [("cash", None, 6_000_000.0, "debit"), ("common_stock", None, 6_000_000.0, "credit")],
        "opening_balance", None, "Opening paid-in capital (Series B, prior to Sep 2025)",
    )
    conn.commit()


def seed_reference_data(conn):
    cur = conn.cursor()
    for dept_id, name, cc in DEPARTMENTS:
        cur.execute("INSERT INTO departments VALUES (?,?,?,?)", (dept_id, name, cc, f"{name} Lead"))
    for account_id, code, name, typ, bal, ctrl in ACCOUNTS:
        cur.execute("INSERT INTO chart_of_accounts VALUES (?,?,?,?,?,?)",
                    (account_id, code, name, typ, bal, ctrl))
    for vid, name, category, acct, terms in VENDORS:
        cur.execute("INSERT INTO vendors VALUES (?,?,?,?,?)", (vid, name, category, acct, terms))
    conn.commit()


def seed_employees(conn):
    """Headcount ramps monthly; Engineering specifically grows 42 (Feb 2026) -> 50 (Aug 2026),
    a ~19% six-month increase, to drive anomaly #2. Other departments grow gently."""
    cur = conn.cursor()
    used_names = set()
    # target headcount by department at each period-end (approximate straight-line ramps)
    target_headcount = {
        "ENG":  {"2025-09": 36, "2026-02": 42, "2026-08": 50},
        "SALES":{"2025-09": 20, "2026-02": 23, "2026-08": 25},
        "MKT":  {"2025-09": 11, "2026-02": 13, "2026-08": 15},
        "CS":   {"2025-09": 16, "2026-02": 18, "2026-08": 20},
        "GA":   {"2025-09": 8,  "2026-02": 9,  "2026-08": 10},
    }
    salary_bands = {"ENG": 145000, "SALES": 110000, "MKT": 100000, "CS": 90000, "GA": 105000}

    employees = []  # (employee_id, dept, start_date)
    for dept, points in target_headcount.items():
        # build a simple month->count schedule by interpolating between the three anchor points
        anchors = sorted(points.items())
        schedule = {}
        for period in PERIODS:
            # find surrounding anchors
            prev = max((p for p in anchors if p[0] <= period), default=anchors[0])
            nxt = min((p for p in anchors if p[0] >= period), default=anchors[-1])
            if prev[0] == nxt[0]:
                schedule[period] = prev[1]
            else:
                p_idx, n_idx = PERIODS.index(prev[0]), PERIODS.index(nxt[0])
                frac = (PERIODS.index(period) - p_idx) / max(1, (n_idx - p_idx))
                schedule[period] = round(prev[1] + frac * (nxt[1] - prev[1]))
        prev_count = 0
        for period in PERIODS:
            count = schedule[period]
            new_hires = max(0, count - prev_count)
            for _ in range(new_hires):
                emp_id = new_id("emp")
                salary = round(salary_bands[dept] * random.uniform(0.85, 1.2), -2)
                start_date = f"{period}-{random.randint(1,25):02d}"
                cur.execute(
                    "INSERT INTO employees VALUES (?,?,?,?,?,?,?,?,?)",
                    (emp_id, random_name(used_names), dept, f"{dept} Team Member", "FT",
                     start_date, None, salary, None),
                )
                employees.append((emp_id, dept, start_date, salary))
            prev_count = count
    conn.commit()
    return employees


def run_payroll(conn, employees):
    cur = conn.cursor()
    for period in PERIODS:
        pay_date = PERIOD_LAST_DAY[period]
        run_id = new_id("pr")
        cur.execute("INSERT INTO payroll_runs VALUES (?,?,?,?)", (run_id, period, pay_date, "posted"))
        dept_totals = {d[0]: 0.0 for d in DEPARTMENTS}
        for emp_id, dept, start_date, salary in employees:
            if start_date > pay_date:
                continue  # not hired yet
            gross = round(salary / 12, 2)
            ot_hours = round(random.uniform(0, 6), 1) if random.random() < 0.15 else 0
            ot_pay = round(ot_hours * (salary / 2080) * 1.5, 2)
            bonus = 0
            tax_burden = round((gross + ot_pay) * 0.21, 2)
            cur.execute(
                "INSERT INTO payroll_run_lines VALUES (?,?,?,?,?,?,?,?,?)",
                (new_id("prl"), run_id, emp_id, dept, gross, ot_hours, ot_pay, bonus, tax_burden),
            )
            dept_totals[dept] += gross + ot_pay
        # post one balanced JE per department: Dr Salaries&Wages / Payroll Tax, Cr Cash
        for dept, amount in dept_totals.items():
            if amount <= 0:
                continue
            tax = round(amount * 0.21, 2)
            sal_account = SAL_ACCOUNT_BY_DEPT[dept]
            post_je(
                cur, period, pay_date,
                [
                    (sal_account, dept, amount, "debit"),
                    ("payroll_tax", dept, tax, "debit"),
                    ("cash", None, amount + tax, "credit"),
                ],
                "payroll", run_id, f"{period} payroll - {dept}",
            )
    conn.commit()


def seed_customers_and_revenue(conn):
    cur = conn.cursor()
    customers = []
    for name in CUSTOMER_NAMES:
        cid = new_id("cust")
        segment = "Enterprise" if name in ("Vantage Retail Group", "Northbridge Capital", "Aurora Financial",
                                            "Ironclad Manufacturing", "Thornfield Bank") else \
                  ("Mid-Market" if random.random() < 0.4 else "SMB")
        customers.append((cid, name, segment))
        cur.execute("INSERT INTO customers VALUES (?,?,?,?,?)",
                    (cid, name, segment, random.choice(INDUSTRIES), "2024-06-01"))

    # Target MRR by period (drives the revenue-vs-payroll anomaly window: Feb->Aug = +6.0%)
    mrr_by_period = {
        "2025-09": 1_300_000, "2025-10": 1_325_000, "2025-11": 1_350_000, "2025-12": 1_375_000,
        "2026-01": 1_400_000, "2026-02": 1_415_000, "2026-03": 1_430_000, "2026-04": 1_443_000,
        "2026-05": 1_456_000, "2026-06": 1_469_000, "2026-07": 1_482_000, "2026-08": 1_500_000,
    }

    # Vantage Retail Group gets an oversized subscription (drives AR concentration anomaly #4:
    # its single unpaid August invoice should land around a third of total open AR)
    vantage_id = next(c for c in customers if c[1] == "Vantage Retail Group")[0]
    weights = {}
    for cid, name, segment in customers:
        base = {"Enterprise": 5.0, "Mid-Market": 2.0, "SMB": 1.0}[segment]
        weights[cid] = base * random.uniform(0.7, 1.3)
    total_weight = sum(weights.values())
    natural_mrr = {cid: mrr_by_period["2026-08"] * w / total_weight for cid, w in weights.items()}

    VANTAGE_TARGET_MRR = 103_000.0
    remaining_target = mrr_by_period["2026-08"] - VANTAGE_TARGET_MRR
    remaining_natural = sum(v for cid, v in natural_mrr.items() if cid != vantage_id)
    scale_others = remaining_target / remaining_natural

    for cid, name, segment in customers:
        sub_id = new_id("sub")
        mrr = round(VANTAGE_TARGET_MRR if cid == vantage_id else natural_mrr[cid] * scale_others, 2)
        cur.execute("INSERT INTO subscriptions VALUES (?,?,?,?,?,?)",
                    (sub_id, cid, segment, mrr, "2024-06-01", None))

    conn.commit()

    subs = cur.execute("SELECT subscription_id, customer_id, mrr FROM subscriptions").fetchall()
    total_current_mrr = sum(s[2] for s in subs)

    for period in PERIODS:
        scale = mrr_by_period[period] / total_current_mrr
        period_revenue = 0.0
        last_day = PERIOD_LAST_DAY[period]
        for sub_id, cust_id, mrr in subs:
            amount = round(mrr * scale, 2)
            period_revenue += amount
            inv_id = new_id("ar")
            due_date = (date.fromisoformat(last_day) + timedelta(days=30)).isoformat()  # true net-30
            cur.execute(
                "INSERT INTO ar_invoices VALUES (?,?,?,?,?,?,?)",
                (inv_id, cust_id, sub_id, last_day, due_date, amount, "open"),
            )
            if cust_id == vantage_id and period == CLOSING_PERIOD:
                vantage_invoice_id = inv_id  # backdated below (anomaly #4: genuinely overdue, not just concentrated)
        post_je(
            cur, period, last_day,
            [("ar", None, period_revenue, "debit"), ("sub_rev", None, period_revenue, "credit")],
            "ar", None, f"{period} subscription revenue recognized",
        )
        # collections: ~92% of invoices from THIS period collected same period, rest ages
        collectable = cur.execute(
            "SELECT invoice_id, customer_id, amount FROM ar_invoices WHERE invoice_date = ?", (last_day,)
        ).fetchall()
        for inv_id, cust_id, amount in collectable:
            if cust_id == vantage_id and period == CLOSING_PERIOD:
                continue  # Vantage's August invoice stays open & overdue (anomaly #4)
            if random.random() < 0.92:
                cur.execute("UPDATE ar_invoices SET status='paid' WHERE invoice_id=?", (inv_id,))
                post_je(
                    cur, period, last_day,
                    [("cash", None, amount, "debit"), ("ar", None, amount, "credit")],
                    "ar_collection", inv_id, f"Collection of {inv_id}",
                )
        # sweep: collect most invoices left open from EARLIER periods (aging realistically
        # resolves within a month or two) so open AR doesn't accumulate forever — except
        # Vantage's deliberately-held August invoice, which isn't open yet at this point anyway.
        stale_open = cur.execute(
            "SELECT invoice_id, customer_id, amount FROM ar_invoices WHERE status='open' AND invoice_date < ?",
            (last_day,),
        ).fetchall()
        for inv_id, cust_id, amount in stale_open:
            if random.random() < 0.88:
                cur.execute("UPDATE ar_invoices SET status='paid' WHERE invoice_id=?", (inv_id,))
                post_je(
                    cur, period, last_day,
                    [("cash", None, amount, "debit"), ("ar", None, amount, "credit")],
                    "ar_collection", inv_id, f"Collection of aged {inv_id}",
                )

    # Anomaly #4, corrected: the answer key says Vantage is "45 days past net-30 terms"
    # as of the August close, not merely large. Concentration alone (34% of open AR) was
    # already landing correctly, but invoice_date/due_date both defaulted to the close date
    # itself, so aging.ar_aging() could never actually compute her as overdue — a real gap
    # between the documented anomaly and what the deterministic aging math would show, caught
    # the same way the M2 bank_feed bug was: by actually running the agent that depends on it
    # (AR Agent, M4) rather than only reading the code. Backdate just this one row so it's
    # genuinely ~45 days past due by 2026-08-31 — revenue recognition is untouched, since the
    # GL entry above is already posted and tagged to the August period independent of this
    # ar_invoices row's own dates.
    as_of = date.fromisoformat(PERIOD_LAST_DAY[CLOSING_PERIOD])
    vantage_due = (as_of - timedelta(days=45)).isoformat()
    vantage_invoiced = (date.fromisoformat(vantage_due) - timedelta(days=30)).isoformat()
    cur.execute(
        "UPDATE ar_invoices SET invoice_date = ?, due_date = ? WHERE invoice_id = ?",
        (vantage_invoiced, vantage_due, vantage_invoice_id),
    )

    conn.commit()
    return vantage_id, mrr_by_period


def seed_ap_and_cogs(conn, mrr_by_period):
    cur = conn.cursor()
    hosting_pct_of_rev = {p: 0.22 for p in PERIODS}
    hosting_pct_of_rev["2026-07"] = 0.24
    hosting_pct_of_rev["2026-08"] = 0.26  # margin compression anomaly (#3)

    CLOUDSCALE_BASE = 14_200.0  # fixed monthly base fee — this is what gets duplicated in August
    DATASTREAM_FLAT = 8_000.0

    for period in PERIODS:
        last_day = PERIOD_LAST_DAY[period]
        revenue = mrr_by_period[period]
        total_hosting = revenue * hosting_pct_of_rev[period]
        cloudscale_usage = max(0.0, total_hosting - CLOUDSCALE_BASE - DATASTREAM_FLAT)

        def post_ap_invoice(vendor_id, account_id, amount, invoice_date, department_id=None, dup_of=None):
            inv_id = new_id("ap")
            cur.execute(
                "INSERT INTO ap_invoices VALUES (?,?,?,?,?,?,?,?,?)",
                (inv_id, vendor_id, invoice_date, invoice_date, amount, account_id, department_id, "paid", dup_of),
            )
            post_je(
                cur, period, invoice_date,
                [(account_id, department_id, amount, "debit"), ("cash", None, amount, "credit")],
                "ap", inv_id, f"{vendor_id} invoice {invoice_date}",
            )
            return inv_id

        post_ap_invoice("cloudscale", "cogs_hosting", CLOUDSCALE_BASE, last_day)
        post_ap_invoice("cloudscale", "cogs_hosting", cloudscale_usage, last_day)
        post_ap_invoice("datastream", "cogs_data", DATASTREAM_FLAT, last_day)

        # recurring opex vendors — department-tagged so budget-vs-actual (Section 12) is meaningful
        post_ap_invoice("toolsuite", "software", 9_500 + random.uniform(-300, 300), last_day, department_id="GA")
        post_ap_invoice("adworks", "marketing", 45_000 + random.uniform(-2000, 2000), last_day, department_id="MKT")
        post_ap_invoice("hartwell", "prof_fees", 6_000 + random.uniform(-500, 500), last_day, department_id="GA")
        post_ap_invoice("primeoffice", "facilities", 22_000, last_day, department_id="GA")

        if period == CLOSING_PERIOD:
            # Anomaly #1: duplicate CloudScale base invoice, entered 3 days apart, NOT flagged yet
            original_id = new_id("ap")
            original_date = "2026-08-05"
            cur.execute(
                "INSERT INTO ap_invoices VALUES (?,?,?,?,?,?,?,?,?)",
                (original_id, "cloudscale", original_date, original_date, CLOUDSCALE_BASE,
                 "cogs_hosting", None, "paid", None),
            )
            post_je(
                cur, period, original_date,
                [("cogs_hosting", None, CLOUDSCALE_BASE, "debit"), ("cash", None, CLOUDSCALE_BASE, "credit")],
                "ap", original_id, "CloudScale base fee (original)",
            )
            dup_id = new_id("ap")
            dup_date = "2026-08-08"
            cur.execute(
                "INSERT INTO ap_invoices VALUES (?,?,?,?,?,?,?,?,?)",
                (dup_id, "cloudscale", dup_date, dup_date, CLOUDSCALE_BASE,
                 "cogs_hosting", None, "unpaid", original_id),
            )
            post_je(
                cur, period, dup_date,
                [("cogs_hosting", None, CLOUDSCALE_BASE, "debit"), ("ap", None, CLOUDSCALE_BASE, "credit")],
                "ap", dup_id, "CloudScale base fee (DUPLICATE — unflagged, AP Agent's job)",
            )

            # Anomaly #5: unbudgeted trade-show sponsorship, Marketing dept
            tradeshow_id = new_id("ap")
            tradeshow_date = "2026-08-14"
            cur.execute(
                "INSERT INTO ap_invoices VALUES (?,?,?,?,?,?,?,?,?)",
                (tradeshow_id, "adworks", tradeshow_date, tradeshow_date, 40_000,
                 "marketing", "MKT", "paid", None),
            )
            post_je(
                cur, period, tradeshow_date,
                [("marketing", "MKT", 40_000, "debit"), ("cash", None, 40_000, "credit")],
                "ap", tradeshow_id, "Unbudgeted trade-show sponsorship",
            )
    conn.commit()


def seed_budgets(conn):
    cur = conn.cursor()
    dept_accounts = {
        "ENG": ["sal_eng"], "SALES": ["sal_sales", "commissions"], "MKT": ["sal_mkt", "marketing"],
        "CS": ["sal_cs"], "GA": ["sal_ga", "prof_fees", "facilities", "software"],
    }
    for period in PERIODS:
        actuals = dict(cur.execute(
            """SELECT department_id || ':' || account_id, SUM(CASE WHEN debit_credit='debit' THEN amount ELSE -amount END)
               FROM gl_transactions WHERE period=? AND department_id IS NOT NULL GROUP BY 1""",
            (period,),
        ).fetchall())
        for dept, accounts in dept_accounts.items():
            for account in accounts:
                key = f"{dept}:{account}"
                actual = actuals.get(key, 0.0)
                if period == CLOSING_PERIOD and dept == "MKT" and account == "marketing":
                    # Anomaly #5: budget set normally, actual (incl. trade show) busts it by ~28%
                    budget = round((actual) / 1.28, 2)
                else:
                    budget = round(actual * random.uniform(0.95, 1.05), 2) if actual else round(random.uniform(5000, 20000), 2)
                cur.execute("INSERT INTO budgets VALUES (?,?,?,?,?)",
                            (new_id("bud"), dept, account, period, budget))
    conn.commit()


def seed_period_status(conn):
    cur = conn.cursor()
    for period in PERIODS:
        if period == CLOSING_PERIOD:
            cur.execute("INSERT INTO period_status VALUES (?,?,?,?)", (period, "open", None, None))
        else:
            cur.execute("INSERT INTO period_status VALUES (?,?,?,?)",
                        (period, "closed", f"{period}-28T00:00:00Z", "seed-script"))
    conn.commit()


def seed_bank_feed(conn):
    """August-only: the raw, not-yet-categorized feed Bookkeeping works from (M2).

    IMPORTANT: this table must contain ONLY cash activity that is NOT already
    posted to the GL by seed_ap_and_cogs() or run_payroll() — those two
    functions post the full cash-basis entry (Dr expense / Cr cash) at the
    moment they record a vendor invoice or a payroll run. Earlier drafts of
    this dataset reused the same vendor names/amounts (CloudScale, AdWorks,
    ToolSuite, PrimeOffice, Hartwell, DataStream, the EFTPS payroll tax
    deposit) in BOTH ap_invoices/payroll AND bank_feed, which meant the
    Bookkeeping Agent — correctly doing its job of posting every bank line —
    silently double-booked those expenses. That surfaced as a live pytest
    failure (August gross margin and Marketing budget variance drifting off
    their documented anomaly targets) the first time the agent actually ran
    against real data. Fixed by keeping bank_feed limited to genuinely
    unrecorded cash activity: the Bookkeeping Agent's real job is items that
    never went through a PO/invoice process, not re-confirming AP's work."""
    cur = conn.cursor()
    rows = [
        ("2026-08-11", "STAPLES BUSINESS ADVANTAGE", 612.40, "software", 0.62),
        ("2026-08-12", "DELTA AIR LINES TRAVEL", 1840.00, "travel", 0.88),
        ("2026-08-15", "SLACK TECHNOLOGIES ANNUAL", 3600.00, "software", 0.93),
        # Anomaly #6: genuinely ambiguous — no confident account match
        ("2026-08-18", "MERIDIAN CONSULTING PARTNERS LLC", 9800.00, None, 0.41),
        ("2026-08-19", "ZOOM VIDEO COMMUNICATIONS", 2400.00, "software", 0.95),
        ("2026-08-21", "AMEX BUSINESS PLATINUM PAYMENT", 3420.55, "travel", 0.55),
        ("2026-08-22", "GITHUB ENTERPRISE ANNUAL", 4800.00, "software", 0.89),
    ]
    for date, desc, amount, acct, conf in rows:
        cur.execute(
            "INSERT INTO bank_feed VALUES (?,?,?,?,?,?,?)",
            (new_id("feed"), date, desc, amount, 0, acct, conf),
        )
    conn.commit()


def sanity_checks(conn):
    cur = conn.cursor()
    print("\n=== Row counts ===")
    for table in ["departments","chart_of_accounts","employees","payroll_runs","payroll_run_lines",
                  "vendors","ap_invoices","customers","subscriptions","ar_invoices","bank_feed",
                  "gl_transactions","budgets","period_status","audit_log","correction_requests","approvals"]:
        n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:22s} {n}")

    print("\n=== Trial balance by period (debits - credits, should be 0.00) ===")
    for period in PERIODS:
        d = cur.execute(
            "SELECT COALESCE(SUM(amount),0) FROM gl_transactions WHERE period=? AND debit_credit='debit'",
            (period,),
        ).fetchone()[0]
        c = cur.execute(
            "SELECT COALESCE(SUM(amount),0) FROM gl_transactions WHERE period=? AND debit_credit='credit'",
            (period,),
        ).fetchone()[0]
        flag = "OK" if abs(d - c) < 0.01 else "*** OUT OF BALANCE ***"
        print(f"  {period}: debit={d:,.2f} credit={c:,.2f}  {flag}")

    print("\n=== Anomaly answer key — spot checks ===")

    dup = cur.execute(
        "SELECT invoice_id, amount, invoice_date, is_duplicate_of FROM ap_invoices "
        "WHERE vendor_id='cloudscale' AND is_duplicate_of IS NOT NULL"
    ).fetchall()
    print(f"  #1 Duplicate CloudScale invoice: {dup}")

    eng_feb = cur.execute(
        "SELECT COUNT(*) FROM employees WHERE department_id='ENG' AND start_date <= '2026-02-28' "
        "AND (end_date IS NULL OR end_date > '2026-02-28')"
    ).fetchone()[0]
    eng_aug = cur.execute(
        "SELECT COUNT(*) FROM employees WHERE department_id='ENG' AND start_date <= '2026-08-31'"
    ).fetchone()[0]
    print(f"  #2 Engineering headcount: Feb26={eng_feb} -> Aug26={eng_aug} "
          f"({(eng_aug/eng_feb-1)*100:.1f}% growth)")

    for period in ("2026-06", "2026-07", "2026-08"):
        rev = cur.execute(
            "SELECT SUM(amount) FROM gl_transactions WHERE period=? AND account_id='sub_rev' AND debit_credit='credit'",
            (period,),
        ).fetchone()[0]
        cogs_hosting = cur.execute(
            "SELECT SUM(amount) FROM gl_transactions WHERE period=? AND account_id IN ('cogs_hosting','cogs_data','cogs_support') AND debit_credit='debit'",
            (period,),
        ).fetchone()[0]
        margin = (rev - cogs_hosting) / rev * 100
        print(f"  #3 {period}: revenue={rev:,.0f} COGS={cogs_hosting:,.0f} gross_margin={margin:.1f}%")

    total_ar = cur.execute("SELECT SUM(amount) FROM ar_invoices WHERE status='open'").fetchone()[0]
    vantage_ar = cur.execute(
        "SELECT SUM(amount) FROM ar_invoices ar JOIN customers c ON ar.customer_id=c.customer_id "
        "WHERE c.name='Vantage Retail Group' AND ar.status='open'"
    ).fetchone()[0]
    print(f"  #4 Vantage AR: {vantage_ar:,.0f} of {total_ar:,.0f} open AR = {vantage_ar/total_ar*100:.1f}%")

    mkt_actual = cur.execute(
        "SELECT SUM(amount) FROM gl_transactions WHERE period='2026-08' AND account_id='marketing' AND debit_credit='debit'"
    ).fetchone()[0]
    mkt_budget = cur.execute(
        "SELECT budgeted_amount FROM budgets WHERE period='2026-08' AND department_id='MKT' AND account_id='marketing'"
    ).fetchone()[0]
    print(f"  #5 Marketing Aug: actual={mkt_actual:,.0f} budget={mkt_budget:,.0f} "
          f"variance={(mkt_actual/mkt_budget-1)*100:.1f}%")

    ambiguous = cur.execute(
        "SELECT description, amount, confidence_score FROM bank_feed WHERE suggested_account_id IS NULL"
    ).fetchall()
    print(f"  #6 Ambiguous bank_feed row(s): {ambiguous}")


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    build_schema(conn)
    seed_reference_data(conn)
    seed_opening_balance(conn)
    employees = seed_employees(conn)
    run_payroll(conn, employees)
    vantage_id, mrr_by_period = seed_customers_and_revenue(conn)
    seed_ap_and_cogs(conn, mrr_by_period)
    seed_budgets(conn)
    seed_period_status(conn)
    seed_bank_feed(conn)
    sanity_checks(conn)
    conn.close()
    print(f"\nWrote {DB_PATH}")


if __name__ == "__main__":
    main()
