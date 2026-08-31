-- AURI Finance / Meridian Analytics — M0 schema
-- 13 tables per architecture/v1-design-charter.md, Section 8.
-- je_id groups the debit+credit lines of one journal entry (implementation
-- detail: the charter calls for balanced GL postings but didn't specify how
-- lines are grouped; this is the simplest workable choice).

PRAGMA foreign_keys = ON;

CREATE TABLE departments (
  department_id     TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  cost_center_code   TEXT NOT NULL,
  budget_owner       TEXT NOT NULL
);

CREATE TABLE chart_of_accounts (
  account_id         TEXT PRIMARY KEY,
  code               TEXT NOT NULL UNIQUE,
  name               TEXT NOT NULL,
  type               TEXT NOT NULL CHECK (type IN ('asset','liability','equity','revenue','cogs','opex')),
  normal_balance     TEXT NOT NULL CHECK (normal_balance IN ('debit','credit')),
  is_control_account INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE employees (
  employee_id        TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  department_id      TEXT NOT NULL REFERENCES departments(department_id),
  title              TEXT NOT NULL,
  employment_type    TEXT NOT NULL DEFAULT 'FT',
  start_date         TEXT NOT NULL,
  end_date           TEXT,
  annual_salary      REAL NOT NULL,
  manager_id         TEXT REFERENCES employees(employee_id)
);

CREATE TABLE payroll_runs (
  payroll_run_id     TEXT PRIMARY KEY,
  period             TEXT NOT NULL,
  pay_date           TEXT NOT NULL,
  status             TEXT NOT NULL DEFAULT 'posted'
);

CREATE TABLE payroll_run_lines (
  line_id            TEXT PRIMARY KEY,
  payroll_run_id     TEXT NOT NULL REFERENCES payroll_runs(payroll_run_id),
  employee_id        TEXT NOT NULL REFERENCES employees(employee_id),
  department_id      TEXT NOT NULL REFERENCES departments(department_id),
  gross_pay          REAL NOT NULL,
  overtime_hours     REAL NOT NULL DEFAULT 0,
  overtime_pay       REAL NOT NULL DEFAULT 0,
  bonus              REAL NOT NULL DEFAULT 0,
  employer_tax_burden REAL NOT NULL DEFAULT 0
);

CREATE TABLE vendors (
  vendor_id          TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  category           TEXT NOT NULL,
  default_account_id TEXT NOT NULL REFERENCES chart_of_accounts(account_id),
  payment_terms_days INTEGER NOT NULL DEFAULT 30
);

CREATE TABLE ap_invoices (
  invoice_id         TEXT PRIMARY KEY,
  vendor_id          TEXT NOT NULL REFERENCES vendors(vendor_id),
  invoice_date       TEXT NOT NULL,
  due_date           TEXT NOT NULL,
  amount             REAL NOT NULL,
  gl_account_id      TEXT NOT NULL REFERENCES chart_of_accounts(account_id),
  department_id      TEXT REFERENCES departments(department_id),
  status             TEXT NOT NULL DEFAULT 'unpaid' CHECK (status IN ('unpaid','paid','disputed')),
  is_duplicate_of    TEXT REFERENCES ap_invoices(invoice_id)
);

CREATE TABLE customers (
  customer_id        TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  segment            TEXT NOT NULL CHECK (segment IN ('SMB','Mid-Market','Enterprise')),
  industry           TEXT NOT NULL,
  start_date         TEXT NOT NULL
);

CREATE TABLE subscriptions (
  subscription_id    TEXT PRIMARY KEY,
  customer_id        TEXT NOT NULL REFERENCES customers(customer_id),
  plan_tier          TEXT NOT NULL,
  mrr                REAL NOT NULL,
  start_date         TEXT NOT NULL,
  churn_date         TEXT
);

CREATE TABLE ar_invoices (
  invoice_id         TEXT PRIMARY KEY,
  customer_id        TEXT NOT NULL REFERENCES customers(customer_id),
  subscription_id    TEXT REFERENCES subscriptions(subscription_id),
  invoice_date       TEXT NOT NULL,
  due_date           TEXT NOT NULL,
  amount             REAL NOT NULL,
  status             TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','paid','overdue'))
);

CREATE TABLE bank_feed (
  feed_id            TEXT PRIMARY KEY,
  posted_date        TEXT NOT NULL,
  description        TEXT NOT NULL,
  amount             REAL NOT NULL,
  categorized        INTEGER NOT NULL DEFAULT 0,
  suggested_account_id TEXT REFERENCES chart_of_accounts(account_id),
  confidence_score   REAL
);

CREATE TABLE gl_transactions (
  txn_id             TEXT PRIMARY KEY,
  je_id              TEXT NOT NULL,
  txn_date           TEXT NOT NULL,
  account_id         TEXT NOT NULL REFERENCES chart_of_accounts(account_id),
  department_id      TEXT REFERENCES departments(department_id),
  amount             REAL NOT NULL,
  debit_credit       TEXT NOT NULL CHECK (debit_credit IN ('debit','credit')),
  memo               TEXT,
  source_type        TEXT NOT NULL,
  source_id          TEXT,
  period             TEXT NOT NULL,
  entered_by_agent   TEXT NOT NULL DEFAULT 'seed',
  status             TEXT NOT NULL DEFAULT 'posted'
);

CREATE TABLE budgets (
  budget_id          TEXT PRIMARY KEY,
  department_id      TEXT NOT NULL REFERENCES departments(department_id),
  account_id         TEXT NOT NULL REFERENCES chart_of_accounts(account_id),
  period             TEXT NOT NULL,
  budgeted_amount    REAL NOT NULL
);

CREATE TABLE period_status (
  period             TEXT PRIMARY KEY,
  status             TEXT NOT NULL CHECK (status IN ('open','closed')),
  closed_at          TEXT,
  approved_by        TEXT
);

CREATE TABLE forecast_assumptions (
  assumption_id           TEXT PRIMARY KEY,
  period                  TEXT NOT NULL,
  monthly_growth_rate_pct REAL NOT NULL,
  basis                   TEXT NOT NULL,
  set_by_agent            TEXT NOT NULL,
  status                  TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded')),
  created_at              TEXT NOT NULL
);

CREATE TABLE audit_log (
  log_id             TEXT PRIMARY KEY,
  timestamp          TEXT NOT NULL,
  agent              TEXT NOT NULL,
  action             TEXT NOT NULL,
  tool_name          TEXT,
  inputs             TEXT,
  outputs            TEXT,
  related_entity_type TEXT,
  related_entity_id  TEXT,
  notes              TEXT
);

CREATE TABLE correction_requests (
  request_id         TEXT PRIMARY KEY,
  period             TEXT NOT NULL,
  raised_by_agent    TEXT NOT NULL,
  target_agent       TEXT NOT NULL,
  finding            TEXT NOT NULL,
  evidence           TEXT,
  requested_action   TEXT,
  materiality_amount REAL,
  status             TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','accepted','disputed','resolved','escalated')),
  round              INTEGER NOT NULL DEFAULT 1,
  created_at         TEXT NOT NULL,
  resolved_at        TEXT
);

CREATE TABLE approvals (
  approval_id        TEXT PRIMARY KEY,
  action_type        TEXT NOT NULL,
  entity_type        TEXT NOT NULL,
  entity_id          TEXT NOT NULL,
  requested_by       TEXT NOT NULL,
  approved_by        TEXT,
  approved_at        TEXT,
  status             TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  notes              TEXT
);

CREATE INDEX idx_gl_period ON gl_transactions(period);
CREATE INDEX idx_gl_account ON gl_transactions(account_id);
CREATE INDEX idx_ap_vendor ON ap_invoices(vendor_id);
CREATE INDEX idx_ar_customer ON ar_invoices(customer_id);
