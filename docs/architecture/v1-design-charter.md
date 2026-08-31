# AURI Finance — V1 Architecture &amp; Design Decisions

*Prepared as the first working session on the AURI Finance charter. Covers the 14 items requested before any application code gets written.*

---

## 1. V1 Definition — What Has to Be True to Call This "Agentic"

A demo can fake agentic behavior with a clever prompt. To avoid that trap, V1 should have to clear five concrete bars, all falsifiable by looking at logs rather than reading prose:

1. **Real data, real math.** Every number in a financial statement or metric traces to synthetic Meridian Analytics data through deterministic code — never through an LLM "computing" a total.
2. **At least one real delegation chain.** One agent invokes another and consumes its structured output to change its own behavior — not just two agents that happen to run in sequence inside a script.
3. **At least one enforced approval gate.** A review/approval-required action is *actually blocked in code* until a recorded approval exists. If a prompt merely asks the model to "wait for approval," that is not a control, it's a suggestion.
4. **An audit trail that answers "who did what and why."** Every agent action (tool call, decision, escalation) is logged with inputs, outputs, and reasoning, replayable after the fact.
5. **The system surfaces a problem it wasn't told about.** Because the synthetic data contains injected anomalies (Section 8), V1 succeeds only if agents *find* at least one of them without being pointed at it — otherwise you've built a calculator with commentary.

**V1, concretely:** an agentic month-end close for one month of Meridian Analytics, executed by a coordinated team of finance agents against a real (synthetic) database, producing financial statements computed by code, a CFO synthesis produced by delegation across at least three specialist agents, one human-approval gate that structurally blocks finalization, and a full audit log — all runnable end-to-end and demoable.

Everything else in the charter (Sales/Marketing/Customer AURI, cross-departmental intelligence, dashboards, plugins) is explicitly out of scope for "is this legitimately agentic" and belongs in Section 17's V1.1/V2/long-term buckets.

---

## 2. Agent Architecture — Challenging the Proposed List

Your own closing principle is the right filter, and I'm going to apply it literally instead of decoratively:

> Agents reason and coordinate. Tools execute. Code calculates. Workflows orchestrate. Humans govern.

Run each of your ten proposed agents through that filter:

**Financial Reporting Agent → not an agent, a deterministic service.** Generating an income statement, balance sheet, or cash-flow statement from a trial balance is arithmetic and formatting, not judgment. There is no decision to make. This becomes a `statements` service (plain code) that any agent can call as a tool. A thin agent *wrapper* still has a job — decide when to run it, investigate a discrepancy it reveals, decide who needs to see it — but that job belongs to the Controller and CFO agents below, not a standalone "Reporting Agent."

**Financial Metrics Agent → mostly a deterministic service, with genuine agent work at the edges.** Gross margin, burn rate, CAC, LTV:CAC — these are formulas over the same trial balance and CRM-adjacent data. Calculating them is code. *Interpreting* them ("gross margin fell 4 points — is that mix shift, pricing, or COGS creep?") is real reasoning. I'd fold that interpretive layer into FP&A and CFO rather than staffing a separate agent whose only job is running formulas it doesn't need to reason about.

**Dashboard & Reporting Agent → a deterministic publish job, not an agent, in V1.** "Refresh dashboard, publish KPIs, generate the weekly email" is a scheduled data-pull-and-render task. It becomes the last step of the month-end **workflow**, not a tenth agent. Promote it to an agent later only if you want it making judgment calls about *what's worth surfacing as an alert* — that's a legitimate future upgrade, not a V1 requirement.

**AP Agent, AR Agent, Payroll & Workforce Agent → real agents, but scoped down.** Aging schedules, due-date tracking, and payroll totals are deterministic calculations (services). What's left for these three to *actually reason about* is narrower than the charter's list: is this vendor charge unusual, is this a duplicate, which overdue customer is worth chasing first, did this hire change the burn trajectory materially. That's legitimate agent work — pattern-spotting and prioritization judgment over structured data — so they stay as agents, but their job description shrinks to "investigate and flag," with the arithmetic delegated to shared calculation tools.

**Bookkeeping Agent, Controller Agent, CFO Agent, FP&A Agent → keep as designed, they're the real thing.** Categorizing an ambiguous transaction, validating another agent's work, deciding which specialist to delegate an investigation to, and setting forecast assumptions from evidence are all judgment calls that don't reduce to a formula. These four are the spine of the system.

**V1 agent roster (7, not 10):**

| Keep as full agent | Demote to deterministic service (called by tools) |
|---|---|
| Bookkeeping Agent | Statement Generator (IS/BS/CF) |
| AP Agent (narrowed) | Metrics Calculator |
| AR Agent (narrowed) | AP/AR Aging Calculator |
| Payroll & Workforce Agent (narrowed) | Payroll Calculator |
| Controller Agent | Dashboard Publisher (V1: a workflow step, not an agent) |
| FP&A Agent | Forecast math engine (linear/growth-rate projections) |
| CFO Agent | |

This isn't fewer capabilities than the charter — it's the same capabilities, correctly assigned to code vs. judgment. It also means when a number is wrong, you'll know instantly whether it's a bug (fix the code) or a bad judgment call (fix the prompt/context) — those are very different failure modes and conflating them is where a lot of "AI system" projects rot.

---

## 3. Agent Responsibility Matrix (V1)

| Agent | Role | Inputs | Tools | Key Actions | Outputs | Permissions | Triggers | Handoffs | Validation | Escalation |
|---|---|---|---|---|---|---|---|---|---|---|
| **Bookkeeping** | Maintain accurate, categorized transaction records | Raw bank feed, existing GL, chart of accounts | `get_uncategorized_txns`, `propose_category`, `post_journal_entry` (review-gated), `flag_duplicate` | Categorize high-confidence txns; propose journal entries for the rest; flag dupes | Categorized txn batch + structured exceptions list | Autonomous: categorize + auto-post above confidence threshold. Review-required: ambiguous categorizations, all journal entries | Month-end trigger; new txn batch import | → Controller (for validation); → AP/AR (bill/invoice-linked txns) | Self-check against chart-of-accounts schema; confidence score per categorization | Confidence below threshold; conflicting prior categorization; no matching account |
| **AP Agent** | Investigate payables risk and flag exceptions | Vendor bills, payment terms, AP aging (from service) | `get_ap_aging`, `get_vendor_history`, `flag_duplicate_invoice`, `flag_unusual_charge`, `recommend_payment` (approval-gated) | Detect duplicate/unusual vendor charges; recommend payment batch; forecast near-term cash need | Exception list + payment recommendation (not executed) | Autonomous: read/flag. Approval-required: any action that would move money | Month-end trigger; new bill ingested | → Controller (exceptions); → CFO (cash requirement) | Cross-check against 90-day vendor charge history | Charge >2x vendor's trailing average; new vendor with no history |
| **AR Agent** | Investigate receivables risk and prioritize collection | Customer invoices, AR aging (from service), payment history | `get_ar_aging`, `get_customer_payment_history`, `flag_overdue`, `prioritize_collections` | Identify overdue accounts; rank collection priority; forecast expected receipts | Prioritized collections list + cash-receipt forecast | Autonomous: read/flag/prioritize. Review-required: any customer-facing collection action | Month-end trigger; invoice past due date | → Controller (aging exceptions); → FP&A (cash forecast input) | Cross-check aging buckets against service output | Customer >120 days overdue; concentration risk (one customer >X% of AR) |
| **Payroll & Workforce** | Investigate labor-cost risk relative to plan | Payroll runs, headcount, department budgets | `get_payroll_summary`, `get_headcount_by_dept`, `compare_to_budget` | Flag payroll growing faster than revenue; flag OT spikes; assess hiring-plan impact | Variance flags + narrative | Autonomous: read/flag. Review-required: none (no write actions in V1) | Month-end trigger; new payroll run posted | → Controller (completeness check); → FP&A (labor cost trend for forecast) | Cross-check totals against payroll service output | Payroll growth outpaces revenue growth by threshold; department >X% over budget |
| **Controller** | Validate the period before it can close | Outputs of Bookkeeping, AP, AR, Payroll; trial balance | `check_trial_balance`, `check_subledger_tie_out`, `review_exceptions`, `approve_period_for_reporting` (gated) | Verify GL balances, subledgers tie to control accounts, all exceptions resolved or explicitly accepted | Period validation report (pass/fail + exceptions) | Autonomous: run checks. **Approval-required: mark period ready for statement generation** — this is the structural gate | All upstream agents report complete | ← receives from Bookkeeping/AP/AR/Payroll; → unblocks Reporting service, CFO | Deterministic reconciliation (trial balance must literally balance) | Any unresolved exception; trial balance out of balance |
| **FP&A** | Forward-looking analysis: variance and forecasting | Actuals (post-close), budget, prior forecasts | `get_budget_vs_actual`, `run_forecast_model` (deterministic engine), `set_forecast_assumptions` | Explain material variances; set/update forecast assumptions from evidence; run scenarios | Variance analysis + updated rolling forecast + labeled assumptions | Autonomous: run analysis, generate scenarios. Review-required: forecast assumption changes that materially shift outlook | Controller marks period closed | ← Payroll/AR trend data; → CFO | Forecast outputs traceable to stated assumptions (never silent) | Assumption change >X% from prior period without clear evidentiary basis |
| **CFO** | Orchestrate investigation, synthesize, recommend | Financial statements, KPIs, all specialist outputs | `delegate_to(agent, question)`, `get_statements`, `get_metrics` | Delegate investigations to specialists; synthesize evidence; produce management recommendation | CFO briefing (facts vs. hypotheses vs. recommendations, explicitly labeled) | Autonomous: analysis, delegation, drafting. Approval-required: nothing executes — CFO output is always advisory pending human sign-off | Controller closes period; statements + metrics generated | ← all agents; → human (final briefing); → Dashboard publish | Every claim traceable to a specialist's structured output, no unsourced numbers | Any finding it cannot resolve with existing specialists — surfaces as an open question to the human, not a guess |

Note what's *not* in this table: a permissions column that says "trust the prompt." Every approval-required action listed above is enforced by a real code path (Section 10), not by asking the model nicely. Also note Controller and CFO hold no write tools into any specialist's domain data at all — when either finds a problem elsewhere, it raises a `correction_request` back to the owning agent rather than fixing it directly (Section 10 has the full protocol, including why).

---

## 4. Month-End Close Workflow

```
[Month-end trigger]
   │
   ▼
Bookkeeping Agent ──(autonomous: categorize high-confidence)──┐
                    (review-required: ambiguous txns)         │
   │                                                            │
   ▼                                                            │
AP Agent ──(autonomous: aging, flag)── (approval-required: payment rec.)
AR Agent ──(autonomous: aging, flag, prioritize)
Payroll Agent ──(autonomous: variance flags)
   │  (these three run in parallel — independent domains)
   ▼
Controller Agent
   ├─ autonomous: trial balance check, subledger tie-out
   ├─ review-required: exceptions surfaced by specialists
   └─ APPROVAL-REQUIRED (structural gate): mark period "ready to report"
        → blocks below until approved
   │
   ▼  (gate open)
Statement Generator (deterministic service) ── autonomous
Metrics Calculator (deterministic service) ── autonomous
   │
   ▼
FP&A Agent
   ├─ autonomous: budget-vs-actual, scenario modeling
   └─ review-required: material forecast/assumption changes
   │
   ▼
CFO Agent
   ├─ autonomous: delegates to Metrics/Payroll/FP&A for investigation, synthesizes
   └─ output is always advisory — no autonomous execution
   │
   ▼
APPROVAL-REQUIRED (human): finalize close, accept CFO briefing
   │
   ▼  (approved)
Dashboard Publisher (deterministic workflow step) ── autonomous, runs only after approval
```

Two structural gates matter more than the rest: **Controller's "ready to report" gate** (nothing downstream trusts unvalidated numbers) and the **human final-approval gate** (nothing is presented as authoritative without sign-off). Everything else can fail safely into a review queue without blocking the whole pipeline.

---

## 5. System Architecture — Simplest Credible Version

Resist the pull toward n8n + MCP + a database + a queue + a frontend on day one. Here's the smallest stack that's still honest:

- **Data layer:** SQLite for V1 (Postgres is a one-line swap later if you need concurrency; you don't yet). Holds GL, subledgers, payroll, budgets — the single source of truth. Google Sheets can be a *read-only mirror* for a human-friendly "ERP cockpit" view (fits your stated interest in Sheets), generated from SQLite, never the system of record — two-way sync between Sheets and a ledger is a correctness hazard you don't need to take on for a portfolio project.
- **Deterministic services layer:** plain Python modules (`statements.py`, `metrics.py`, `aging.py`, `payroll_calc.py`, `forecast.py`) — unit-testable, no LLM involved.
- **Agent layer:** Claude API (Messages API + tool use), one system prompt + scoped tool set per agent, calling the deterministic services as tools. Structured (JSON-schema-validated) outputs for every agent so handoffs are mechanical, not vibes-based parsing of prose.
- **Orchestrator:** a Python script/state machine that runs the workflow in Section 4, tracks each agent's status, and enforces the approval gates in code. This is intentionally *not* a generic workflow engine yet — it's the sequence in the diagram above, hard-coded, because you have exactly one workflow and premature generality here is the "appearance of sophistication" trap you specifically asked me to flag.
- **Audit log:** an append-only table (`agent`, `action`, `inputs`, `outputs`, `timestamp`, `linked_evidence`) written by every tool call.
- **Approval interface:** for V1, a CLI or a five-line Flask/Streamlit page listing pending approvals and a button — not a real auth system yet (Section 13 makes RBAC a deliberate V1.1+ item once there's more than one human in the loop).
- **Source control:** GitHub, from day one.

**What's deliberately absent from V1, and why:**
- **n8n** — you don't have an external trigger or a scheduling need yet; "run the close" is a CLI command. Add n8n when you want a real monthly cron trigger, webhook-driven ingestion, or to demonstrate workflow-orchestration skills specifically — that's a legitimate V1.1 addition, not a V1 requirement.
- **MCP server** — justified once more than one *runtime* needs the same Meridian data (e.g., you also want to poke at the ledger from Claude Desktop or Claude Code directly, or a second product surface appears). One Python product with its own tool functions doesn't need the protocol overhead yet. Section 6 has the exact tripwire for when to add it.
- **AWS** — nothing here needs a cloud service yet; add S3 for artifact storage or Lambda for scheduled runs when you deploy beyond your own machine, not before.

---

## 6. Claude Architecture — Where Each Capability Actually Belongs

I checked the current state of these (mid-2026) rather than working from stale assumptions, since the ecosystem has moved:

**Claude API — Messages API + tool use (DIY orchestration): use this for V1.** You write the agent loop yourself, in the orchestrator described above. This is the right call *specifically for V1* because you said you want to understand what you build — a hand-written loop makes every decision point visible and debuggable, whereas a managed loop hides it. It also gives you full control over where permission checks live (Section 10 depends on this).

**Claude API — Managed Agents (beta): the deliberate V1.1/V2 upgrade, not V1.** Managed Agents give you a persisted `Agent` config, sandboxed per-session tool execution, native MCP/credential integration, persistent memory across sessions, multi-agent coordination primitives, and — notably for your flagship workflow — **scheduled/recurring deployment and webhook notifications on state change**. That last point is a genuinely good fit for "a month-end trigger initiates the workflow" once you're past the demo stage. The tradeoff is real: you gain infrastructure, you lose some visibility into the loop. Recommendation: build V1 by hand so you understand the mechanics cold, then port to Managed Agents as a V1.1 exercise specifically *because* re-implementing the same system on a managed substrate is one of the best ways to learn what the managed layer is actually doing for you.

**Agent Skills via the API: legitimate V1.1 use, not required for V1.** Skills are reusable, filesystem-based domain packages (`SKILL.md` + resources) that can be attached per-agent or mounted straight from your repo (`.claude/skills/<name>/SKILL.md`), and the API now supports up to 500 skills per session shared across agents. This is architecturally the right home for things like "Meridian's chart-of-accounts mapping rules" or "SaaS metrics formula definitions" — versioned, reusable across the Bookkeeping/Metrics/FP&A agents, instead of copy-pasted into three system prompts and drifting out of sync. For V1, plain context injection is simpler and your domain knowledge is small enough that a skill is overkill; revisit once you have two or more agents that need the *same* domain package and you're tired of keeping prompts in sync.

**MCP: a V1.1 decision, with a clear tripwire.** Build a "Meridian ERP" MCP server once a second consumer needs the same data through the same contract — e.g., you want to query the ledger from Claude Desktop while debugging, or a future AURI department needs read access to Finance data. The 2026 MCP roadmap is actively hardening exactly the things that would matter here (audit trails, SSO-integrated auth, gateway behavior) as enterprise-readiness extensions — so building an MCP server later, once those land, costs you less than building one now against a moving spec. Until then, plain typed Python tool functions are simpler and just as correct.

**Claude Projects: this project itself, not the product.** You're using it right now to carry the charter and architecture decisions across sessions — that's exactly its job. It is not part of AURI Finance's runtime architecture; don't confuse "where we plan AURI" with "what AURI runs on."

**Claude Code + CLAUDE.md + subagents/agent teams: your build tooling (Section 7).** This is where subagents earn their keep — not inside the finance product.

**Plugins: a portfolio/distribution move for later, not V1.** Packaging AURI Finance's dev tooling (or eventually the finance-agent bundle itself) as an installable plugin is a nice V2/portfolio flourish — "I built a distributable multi-agent system" is a stronger claim than "I built a script" — but it's presentation, not architecture, and shouldn't be a V1 dependency.

**Hooks and Commands: dev-workflow conveniences.** Use Claude Code hooks to enforce things like "tests run before a subagent's change is considered done," and slash commands for repeatable dev actions (`/run-close`, `/new-agent-scaffold`, `/seed-data`). These help you build faster; they have no runtime role in AURI Finance itself.

**Structured outputs / tool use: non-negotiable, used everywhere.** Every agent-to-agent handoff in Section 3 should be a JSON-schema-validated object, not prose the next agent has to parse. This is what makes the Controller's validation mechanical instead of "the LLM read the LLM's report and vibed a judgment about it."

**Agent memory/context patterns: keep it simple in V1.** A shared "business context" document (chart of accounts, company profile, prior-period figures) injected into every agent's context, plus the audit log as a queryable event store, is sufficient at "one company, one month" scale. Don't build retrieval/RAG infrastructure for a dataset this size — that's solving a problem you don't have yet, and it's the kind of complexity Section 16 asks me to challenge.

**Permission models: this is engineering, not a Claude feature, and that's the most important single point in this section.** Claude has no built-in RBAC. The permission model in Section 10 is enforced by *your* code: an approval-required tool function literally checks an approvals table before it will execute, and refuses otherwise — no prompt instruction, however carefully worded, is a substitute for that check existing in the tool layer. If you remember one thing from this whole document, make it this one, because it's also the difference between a real control and a demo that looks like it has one.

---

## 7. Development Agent Team — "Agents Building AURI"

Subagents/agent teams are a genuinely good fit here, but only for the well-scoped, repeatable, tool-heavy roles. The cross-cutting judgment roles are better done as direct back-and-forth with me in this conversation, because delegating architecture judgment to an isolated subagent just reproduces the "no memory of prior context" problem you're trying to avoid in the product itself.

| Role | Use a subagent? | Why |
|---|---|---|
| **Solution Architect** | No — this conversation | Needs full charter context and iterative judgment; this document is that role's output |
| **Agent Architect** (writes each finance agent's prompt/tool spec, tests handoffs) | No — this conversation, closely | Same reason; agent design is where most of the "is this really agentic" judgment lives |
| **Data Engineer** (builds synthetic Meridian dataset + schema + seed scripts) | **Yes** | Well-scoped, deterministic, testable in isolation against a spec you approve first |
| **Application Engineer** (orchestrator, tool implementations, approval interface) | **Yes** | Same — implementation against an approved design, reviewable diff by diff |
| **QA Engineer** (unit tests for services, agent evals per Section 12) | **Yes** | Benefits from an independent perspective — a subagent that didn't write the code is a better bug-finder than the one that did |
| **Security/Controls Reviewer** (audits permission gates, approval enforcement) | **Yes**, and deliberately run *after* Application Engineer, never by the same context | Same independence argument, applied to the control architecture specifically since that's the part most likely to have a subtle bypass |
| **Documentation Specialist** (README, architecture diagrams, case study draft) | **Yes** | Low-judgment, high-volume writing task, good subagent fit |

Practical setup: define these as named subagents in `.claude/agents/` with scoped tool access matching their role (Data Engineer doesn't need the approval-interface code, Security Reviewer needs read access to everything but shouldn't be the one who wrote it). Use Claude Code hooks to gate "done" on tests passing, and slash commands to invoke each role's standard task (`/seed-data`, `/implement-agent bookkeeping`, `/review-controls`).

---

## 8. Data Model — Meridian Analytics Dataset (Finalized for M0)

**Company profile:** Meridian Analytics — a B2B SaaS analytics platform, ~120 employees across five departments (Engineering, Sales, Marketing, Customer Success, G&A), ~$18M ARR. History runs September 2025 through July 2026 (clean, 11 months), with **August 2026 as the closing period** carrying every seeded anomaly below.

Thirteen tables — three more than the original ten, because Section 10's correction-request protocol (below) needs its own tables to be real rather than aspirational:

1. **`departments`** — department_id, name, cost_center_code, budget_owner_employee_id
2. **`chart_of_accounts`** — account_id, code, name, type (asset/liability/equity/revenue/cogs/opex), normal_balance, is_control_account
3. **`employees`** — employee_id, name, department_id, title, employment_type, start_date, end_date, annual_salary, manager_id
4. **`payroll_runs`** / **`payroll_run_lines`** — run-level (period, pay_date, status) and line-level (employee_id, gross_pay, overtime_hours, overtime_pay, bonus, employer_tax_burden, department_id)
5. **`vendors`** / **`ap_invoices`** — vendor_id, name, category, default_gl_account, payment_terms_days; invoice_id, vendor_id, invoice_date, due_date, amount, gl_account_id, department_id, status, is_duplicate_of (self-referencing FK — how the seeded duplicate gets marked once found)
6. **`customers`** / **`subscriptions`** / **`ar_invoices`** — customer_id, name, segment, industry; subscription_id, plan_tier, mrr, start_date, churn_date; invoice_id, customer_id, invoice_date, due_date, amount, status
7. **`bank_feed`** — raw pre-categorization transactions: feed_id, posted_date, description, amount, categorized (bool), suggested_account_id, confidence_score — what Bookkeeping actually works from
8. **`gl_transactions`** — the ledger: txn_id, txn_date, account_id, department_id, amount, debit_credit, memo, source_type, source_id, period, entered_by_agent, status
9. **`budgets`** — department_id, account_id, period, budgeted_amount
10. **`period_status`** — period, status (open/closed), closed_at, approved_by — the literal record the Controller's structural gate reads and writes; statements for any prior period are computed on demand from `gl_transactions` rather than stored separately, since 11 months of history is small enough not to need pre-materialized snapshots
11. **`audit_log`** — agent, action, tool_name, inputs, outputs, timestamp, related_entity, notes
12. **`correction_requests`** — request_id, period, raised_by_agent, target_agent, finding, evidence, requested_action, materiality_amount, status (open/accepted/disputed/resolved/escalated), round, created_at, resolved_at
13. **`approvals`** — approval_id, action_type, entity_type, entity_id, requested_by, approved_by (human), approved_at, status, notes

### The anomaly answer key (August 2026 close)

Six seeded problems, each mapped to the agent that should find it — this is the actual scoring rubric for Section 12's evals, so it's worth being this concrete before the generator gets built:

| # | Anomaly | Where it lives | Who should catch it |
|---|---|---|---|
| 1 | **Duplicate vendor charge.** Two AP invoices from "CloudScale Hosting" for the same $14,200 infrastructure charge, posted three days apart under different invoice numbers. | `ap_invoices` | AP Agent, via `flag_duplicate_invoice` |
| 2 | **Payroll outpacing revenue.** Engineering headcount grew 42→50 (Feb→Aug 2026); payroll costs up 19.0% against 6.0% revenue growth in the same six-month window. | `payroll_runs`, `employees` | Payroll & Workforce Agent, corroborated by FP&A's runway model |
| 3 | **Gross margin decline, traceable cause.** Hosting COGS rises from 22% to 26% of revenue in June→August as usage-tier costs outpace a matching price increase; gross margin runs 78%→76%→~74% once the duplicate in #1 is corrected (the *as-entered* August figure reads 72.1% until AP nets that duplicate out — a deliberate, realistic wrinkle: the raw books are briefly worse than reality until the agent team catches the error). | `gl_transactions` (COGS), `subscriptions` | Financial Metrics service surfaces the number; FP&A/CFO trace the cause |
| 4 | **AR concentration + aging risk.** "Vantage Retail Group" (Enterprise) is 34% of total AR and 45 days past net-30 terms. | `ar_invoices`, `customers` | AR Agent, via `prioritize_collections` |
| 5 | **Department budget overrun.** Marketing overspends August budget by 28%, driven by an unbudgeted trade-show sponsorship. | `budgets` vs `gl_transactions` | FP&A, budget-vs-actual; Controller flags as an exception pending explanation |
| 6 | **Genuine bookkeeping ambiguity.** A $9,800 bank transaction memo'd only "Meridian Consulting Partners LLC" — plausibly professional fees or a marketing agency payment, with no chart-of-accounts rule that resolves it confidently. | `bank_feed` | Bookkeeping Agent — correct behavior is to escalate, not guess |

Anomaly #6 matters as much as the other five: it's there specifically to test that Bookkeeping *doesn't* force a confident categorization onto a genuinely ambiguous transaction. An agent that never escalates anything is exactly as broken as one that escalates everything.

**Seed 11 months of clean history** ahead of the August close so period-over-period comparisons, budget baselines, and forecast assumptions all have real prior data to work from — none of the six anomalies above are visible without something normal to contrast them against.

### M0 status: built and verified

The schema and generator described above are implemented (`db/schema.sql`, `data/seed/generate_meridian.py`) and have been run: 120 employees, 45 customers, 87 AP invoices, 540 AR invoices, and balanced GL transaction lines across the 12 periods, funded by one opening paid-in-capital entry ($6,000,000, dated to the start of history) so cumulative cash stays positive rather than drifting negative with nothing ever seeded to fund 11+ months of real burn. Verification run against the live database: **trial balance ties out to $0.00 for every one of the 12 periods** (including August, despite the unflagged duplicate — a balanced-but-wrong entry is exactly what a real erroneous duplicate looks like), **zero foreign-key violations**, the balance sheet identity holds to the penny (assets = liabilities + [paid-in capital + cumulative net income]), and all six anomalies land within rounding of their targets (duplicate $14,200 CloudScale invoice; Engineering headcount 42→50, +19.0%; gross margin 78%→76%→72.1% as-entered (~74% once corrected); Vantage Retail Group at 34.4% of open AR; Marketing August variance +28.0%; the $9,800 ambiguous transaction present with no confident suggested account). M0 is complete.

### M1 status: built and verified

The deterministic services layer (`services/`) is implemented against this database: `statements.py` (trial balance, income statement, balance sheet, cash flow — direct method), `metrics.py` (gross margin, payroll % of revenue, budget-vs-actual, burn rate, cash runway), `aging.py` (AP/AR aging with AR concentration, plus a rule-based duplicate-invoice detector), `payroll_calc.py` (department payroll summaries, headcount, growth comparisons), and `forecast.py` (linear projection math that always states its growth-rate assumption in its return value, per Section 9 — it never infers one silently without saying so).

One real design decision surfaced while building this, worth recording: computing a balance sheet from a flat transaction ledger that's never formally closed means equity can't just be "the retained earnings account balance" — nothing ever posts to it. `balance_sheet()` instead constructs equity as paid-in capital (the equity-type accounts) plus cumulative net income (all-time revenue minus cogs/opex through the requested period), which is the standard way to derive a balance sheet under exactly this constraint. Worth knowing before M2's agents start calling these functions and expecting a `retained_earnings` ledger balance that will never move.

24 tests in `tests/test_services.py` pass against the live seeded database — including golden-value checks that each of the six seeded anomalies produces the exact number the answer key predicts (e.g., Vantage's AR concentration comes back at 34.4% from `aging.ar_aging()`, not just from the generator's own self-check). M1 is complete — next up is M2, the Bookkeeping Agent, the first piece of the system that actually reasons rather than only calculates.

### M2 status: built and verified — first live agent run

The Bookkeeping Agent is real, callable code, not a design sketch: `tools/bookkeeping_tools.py` (read-only `get_uncategorized_txns` / `get_chart_of_accounts`, a confidence-gated `post_categorization` that refuses to post below 0.75 regardless of what the model argues, and `escalate_for_review` which files an `approvals` row for a human), `agents/bookkeeping/system_prompt.md`, and `agents/bookkeeping/run.py` — a manual tool-use loop against the live Claude API (`claude-haiku-4-5-20251001`), with every tool call, refusal, and escalation written to `audit_log`.

**It found a real bug on its first live run, which is exactly the point of running it for real instead of just reading the code.** The bank feed the agent worked from originally reused the same vendor names/amounts as invoices `seed_ap_and_cogs()` and `run_payroll()` had already posted straight to the GL at cash-basis (this dataset has no formal accrual layer — see the M1 note above). The agent did its job correctly — it categorized and posted every bank line it was confident about — which meant it re-booked expenses (CloudScale hosting, AdWorks marketing, the EFTPS payroll tax deposit, and others) that were already in the ledger. Nothing in the agent was wrong; the synthetic bank feed was quietly double-counting real dollars, and the existing pytest suite caught it immediately: August gross margin came back 69.68% instead of the documented 72.1%, and the Marketing budget variance came back 159.35% instead of 28.0%. Fixed by trimming `bank_feed` in `data/seed/generate_meridian.py` down to cash activity that genuinely never went through the AP or payroll path — that's the Bookkeeping Agent's actual real-world job (bank-only transactions with no PO/invoice trail), not re-confirming AP's work. Re-seeded, re-verified: all 24 tests pass both before and after the agent's live postings, and the ledger balances to the penny all-time ($60,073,207.72 debits = credits) after its writes land.

The pass/fail bar was whether the agent would escalate the genuinely ambiguous $9,800 "Meridian Consulting Partners LLC" transaction (anomaly #6) rather than guess a category for it. **It did, on both live runs** — along with two other transactions it correctly judged it couldn't confidently categorize (a $612.40 Staples charge with no itemization, and a $3,420.55 AmEx card payment that only reflects a settlement, not the underlying purchases). On the corrected dataset: 4 transactions posted as balanced journal entries (Delta travel, Slack/Zoom/GitHub software), 3 escalated to the `approvals` table with the agent's stated reasoning, 9 audit_log rows, zero silent guesses. Real API cost for the two complete verification runs: roughly $0.05 total (Haiku 4.5, ~26,000 tokens combined) — see Section 14 for the cost model this is built on.

### M3 status: built and verified — the correction-request protocol works live

The Controller Agent is real, callable code: `tools/controller_tools.py` (read-only `check_trial_balance`, `check_subledger_tie_out`, `review_exceptions` — which re-runs M1's rule-based duplicate detector itself rather than waiting to be told about a problem — plus two writes: `raise_correction_request`, scoped to the `correction_requests` table only, and `mark_period_ready_for_reporting`, a code-enforced gate that refuses if the trial balance doesn't balance or unresolved exposure exceeds a $25,000 materiality threshold), `agents/controller/system_prompt.md`, and `agents/controller/run.py` on `claude-sonnet-5` (Sonnet, not Haiku, per the cost-tiering plan in Section 14 — Controller's job is judgment about materiality and evidence, not high-volume categorization). Structurally, the Controller holds no write tools into any other agent's domain data at all — confirmed by a dedicated test (`test_raise_correction_request_never_touches_other_agents_data`) that asserts `gl_transactions` and `ap_invoices` row counts are unchanged after a correction request is raised.

**Live run against the real August close, unprompted, rediscovered anomaly #1.** Nobody told this agent about the CloudScale duplicate. It ran its own exceptions review, which re-executes the M1 duplicate-invoice detector, found the same $14,200 pair the design charter seeded back in M0, and — because it holds no write access to `ap_invoices` — did the only thing a Controller structurally can do: filed `correction_requests` row `cr_1efbf8e71d` against `ap_agent` with the specific invoice IDs and dollar amount as evidence. It then tried `mark_period_ready_for_reporting` and the tool correctly refused: the $14,200 duplicate plus $13,832.95 in pending Bookkeeping escalations summed to $28,032.95 — over the $25,000 materiality bar — so `period_status.approved_by` is still null. This is Section 1's V1 bar #5 ("the system surfaces a problem it wasn't told about") happening for real, on a live API call, not a scripted demo.

**The M3 milestone's specific bar — catch a deliberately broken trial balance in a test fixture, and escalate correctly — passed live, twice over.** `tests/test_controller.py` covers the deterministic layer (Layer 1: a scratch copy of the database with one single-sided $500 debit inserted, no LLM involved, CI-repeatable) — `check_trial_balance` catches the $500 diff exactly, `mark_period_ready_for_reporting` refuses and never touches `period_status`, and the refusal is itself audited. Separately, `tests/fixtures/make_broken_trial_balance_fixture.py` builds the same broken database for a **live** run: pointed at it, the agent checked the trial balance first, found it $500 off, correctly treated everything downstream as moot ("no further validation... since it would be moot until the ledger balances"), filed a correction request against `bookkeeping_agent` with the exact figures, confirmed the gate's refusal, and stopped — it never attempted to guess at or paper over the missing $500, because it structurally cannot touch `gl_transactions` at all. Real API cost for both live Controller runs: roughly $0.11 (Sonnet, ~36,000 tokens combined).

### M4 status: built and verified — the correction closed the loop, live

AP, AR, and Payroll & Workforce are all real, callable agents: `tools/ap_tools.py`, `tools/ar_tools.py`, `tools/payroll_tools.py`, and `agents/ap/`, `agents/ar/`, `agents/payroll/`. AP runs on `claude-haiku-4-5-20251001` (its job is high-volume investigation, not the materiality judgment Controller does) and is the only one of the three with real write access — scoped entirely to its own domain (`ap_invoices`, plus the compensating GL entry a confirmed duplicate requires) — because Section 10's segregation-of-duties rule means only the owning agent gets to fix its own data. AR and Payroll are deliberately, entirely read-only: AR has no tool that touches a customer, and Payroll's matrix line in Section 3 ("Review-required: none — no write actions in V1") is implemented literally, not just documented. Both prove that not every agent in this system needs a write tool to be doing real work.

**Live, this session found and fixed three more real gaps — on top of M2's and M3's — the same way every prior one was found: by actually running the agent against real data instead of only reading the code.**

First, the AR aging bug. `services/aging.ar_aging()` computes real day-level buckets from `due_date`, but the M0 generator had been setting `due_date = invoice_date` for every AR invoice — "net-30 approximated as due at period end for simplicity," per the original comment. That meant nothing could ever compute as overdue: `prioritize_collections()` came back empty even for Vantage's $103,000 invoice, which the anomaly answer key explicitly documents as "45 days past net-30 terms." Fixed at the source: `generate_meridian.py` now sets true net-30 due dates for every invoice, and Vantage's specific held-open August invoice is deliberately backdated so it's genuinely ~45 days overdue by the close date — revenue recognition is untouched, since the GL entry that recognized her $103,000 was already tagged to the August period independent of this one row's own invoice/due dates.

Second, the duplicate-detector re-flagging bug. `flag_duplicate_ap_invoices()` is deliberately naive (Section 9: it's the pattern matcher, not the adjudicator) — it doesn't know a candidate pair was already resolved. The first time AP genuinely fixed the CloudScale duplicate, the very next Controller run re-flagged the *same already-fixed pair* as a brand-new finding, because nothing downstream of the raw detector checked whether either invoice had already been marked `disputed`. Fixed in the tool layer (`controller_tools.review_exceptions`, `ap_tools.flag_duplicate_invoices`) rather than the detector itself, preserving the detector's intentional "dumb pattern matcher" purity while making the agent-facing tools aware of what's already been adjudicated — the same principle Section 10 already establishes for `is_duplicate_of`, just not yet applied consistently before this run surfaced the gap.

Third, a genuine agent tool-usage miss, not a code bug: the Payroll Agent guessed a nonexistent account_id (`"payroll"`) when calling `compare_to_budget`, because it had no tool to discover that budgets are actually keyed by each department's real salary account (`sal_eng`, not `payroll`). The code behaved correctly — it returned `null`, as it should for a real query against a non-existent account — but the agent's conclusion ("no budget data available") was built on a wrong premise I'd handed it no way to avoid. Fixed by adding `get_salary_account_map()` so the agent looks up the real account_id instead of guessing one; re-run, it correctly pulled Engineering's actual budget ($636,102.42) and found a genuinely interesting nuance the first run couldn't see — Engineering is *under* budget per head (-2.16%) even while its 19% headcount growth outpaces revenue, meaning the real story is hiring *volume*, not overspending per hire.

**The capstone: a full month-end loop, closed live, with a real number changing because a real agent fixed a real error.** With every M2-M4 agent re-run in sequence against the corrected dataset — Bookkeeping escalates the $9,800 ambiguous transaction (again, correctly) and posts everything it's confident about; Controller rediscovers the CloudScale duplicate unprompted and refuses to mark the period ready; AP picks up Controller's correction request, independently corroborates it against CloudScale's 26-invoice history, correctly *dismisses* a second candidate pair the detector also flagged (a coincidental July/August billing-date collision, not a real duplicate — exactly the "is this near-duplicate actually a problem" judgment Section 9 reserves for the agent), and reverses the real one — Controller was run a third time to check the outcome. This time: **zero duplicate candidates, and `mark_period_ready_for_reporting` succeeded.** `period_status.approved_by` is now `controller_agent`. August's gross margin, which read 72.1% "as entered" with the duplicate still sitting in COGS, now computes to 72.11%→73.05% once AP's correction lands — matching the M0 writeup's own "~74% once corrected" estimate, made months before any agent existed to do the correcting. `tests/test_services.py` was updated to assert the corrected figure, with the pre-correction value documented in a comment rather than erased — the test failure that flagged this drift was itself the proof the fix worked, the same story as M2's bank_feed bug.

Final state: ledger balances to the penny all-time ($60,087,407.72 debits = credits), 48 audit_log rows across five agents, one correction request raised and resolved end-to-end, and 30/30 tests passing. Real API cost for this session's full M4 build — three new agents' first live runs, the three fixes above, and two full agent-team replays to reach a clean final state — was roughly $0.40 combined (Haiku for AP/AR/Payroll, Sonnet for Controller's three runs).

### M5 status: built and verified — the period closed live, and re-running the close for real found the two bugs a design review never would have

The orchestrator is a hard-coded Python state machine, deliberately not a workflow engine (Section 5): `orchestrator/run_close.py` calls the five agents' `run()` functions directly, in sequence (Bookkeeping → AP → AR → Payroll → Controller), then enforces two structural gates in code, not prompts. **Gate 1** independently re-checks `period_status.approved_by` after Controller runs — a second, code-level check of the same fact Controller just asserted, per Section 10's "literal booleans the orchestrator checks before advancing," not one agent's self-report trusted at face value. **Gate 2** is new: no LLM anywhere in it. The orchestrator will not flip `period_status.status` to `'closed'` — the one write in the entire system that does that — until a row in `approvals` with `action_type='finalize_period_close'` has been marked `'approved'` by a human, via the five-line CLI `orchestrator/approve.py` Section 5 scoped for V1. If no approval exists, the orchestrator files the request itself and halts, printing the exact command a human runs to clear it.

**The first live run blocked at Gate 2 as designed, but it also surfaced two real bugs — neither visible from reading the code, both only visible from actually re-running the same agents against a database that already had a full close's worth of history sitting in it.**

First: Bookkeeping's `escalate_for_review` wasn't idempotent. `get_uncategorized_txns` filters on `bank_feed.categorized = 0`, and escalating a transaction deliberately doesn't flip that flag — the transaction really is still awaiting a human decision. But that also means a second Bookkeeping run against the same period sees the same three already-escalated transactions as "new" and escalates them again, filing a second near-duplicate `approvals` row for each. Controller's materiality math summed all of them, so unresolved exposure read $27,665.90 — double-counting $13,832.95 of it — against the real $13,832.95. Fixed by making `escalate_for_review` check for an existing pending approval on that `feed_id` first and hand back the existing one instead of filing a duplicate.

Second, and more consequential: `mark_period_ready_for_reporting` only ever *set* `period_status.approved_by` on success — it never cleared it on refusal. So when Controller's second run correctly refused to re-approve the period (the duplicate-escalation noise above had pushed exposure over threshold), `approved_by` stayed at its stale `'controller_agent'` value from M4's last successful pass. The orchestrator's own Gate 1 — the independent re-check that exists specifically to not trust one agent's self-report — read that stale flag and passed anyway. This is the sharper of the two bugs: a control built to catch exactly this kind of staleness was itself relying on a column that could be stale. Fixed by clearing `approved_by` back to `NULL` on both of `mark_period_ready_for_reporting`'s refusal paths, so the flag now always reflects the *most recent* run's verdict, never any run's.

**Fixing those two surfaced a third, independent problem — a live agent's own judgment had actually gotten something wrong on an earlier run, and the code had let it act on that mistake.** With the duplicate-escalation noise cleared, Controller's next run found trial balance and AR tying out cleanly, but the AP subledger no longer tied to its GL control account — a genuine, previously-undetected $14,200 break. Tracing it: on the very first (buggy) orchestrator run, AP Agent had re-examined CloudScale's invoices, this time misjudged a legitimate pair of separate monthly charges (`ap_a6d8dc2eb3`, `ap_79c65303e3` — five days apart, the same false-positive pattern M4's agent had correctly dismissed on its first look) as a duplicate, and called `resolve_duplicate_invoice` on it. `ap_79c65303e3` had *already been paid* — its AP liability was already settled by real cash going out the door — but `resolve_duplicate_invoice` unconditionally booked a `Dr ap / Cr cogs_hosting` reversal regardless of the invoice's actual status, which only correctly undoes an *outstanding, unpaid* accrual. Against a paid invoice it just creates a stray, permanent break between the GL and a subledger that never counted a paid invoice as outstanding in the first place — and, as a side effect, silently pushed August's gross margin from 73.05% to 74.0% by crediting COGS a second time for a real expense. Two fixes: `resolve_duplicate_invoice` now refuses outright whenever the target invoice's status is `'paid'`, with a message explaining that recovering money from a vendor who was legitimately paid twice is a collections problem, not a same-domain journal entry. And because the *root cause* was really that AP's own correct "not a duplicate" verdicts had no durable memory — the exact same candidate pair resurfaces on every run with no record that anyone already looked at it and got it right — AP got a new tool, `dismiss_duplicate_candidate`, that records a reviewed-and-cleared false positive in `audit_log`; `flag_duplicate_invoices` now filters out any pair that's been explicitly dismissed, the same way it already filtered out pairs already marked `disputed`. The erroneous entry itself was corrected the way a real error gets corrected — with an explicit reversing journal entry restoring the invoice to `'paid'`, not a silent row deletion — preserving the mistake and its correction both in the audit trail.

**Re-run clean, the whole thing worked exactly as designed, twice.** With both bugs fixed, AP's next live pass hit the same CloudScale candidate pair, correctly judged it a false positive again — and this time called `dismiss_duplicate_candidate` on it, so it can never resurface. Controller found trial balance balanced, both subledgers tied out, zero duplicate candidates, and $13,832.95 of genuinely-still-open exposure (three real, unresolved ambiguous categorizations) safely under the $25,000 threshold — `mark_period_ready_for_reporting` succeeded. **Gate 1 passed.** **Gate 2 correctly blocked**, filing a `finalize_period_close` approval and printing the exact command to clear it. Running `python3 orchestrator/approve.py --period 2026-08 --approved-by "Jordan Ellis, VP Finance"` approved it as a human would. One more orchestrator run: **Gate 1 passed, Gate 2 passed, and `period_status` flipped to `status='closed'`, `closed_at` timestamped, `approved_by` overwritten with the human's name** — the first time any period has ever actually closed in this project's history, and the only write anywhere in the system capable of doing that.

Final verification: 30/30 tests passing, the ledger balances to the penny all-time ($60,125,607.72 debits = credits), all three correction requests raised during this milestone resolved, three legitimate pending categorization approvals still open (correctly *not* blocking the close, since they're within materiality — genuine judgment calls, not errors), and 223 `audit_log` rows across all six agents plus the orchestrator's own gate checks. Real API cost: four full orchestrator passes (twenty agent invocations) were needed to find and fix the three issues above, roughly $0.85 combined (Haiku for Bookkeeping/AP/AR/Payroll, Sonnet for Controller) — on top of the ~$0.55 spent through M0–M4, putting total live-API spend for the whole project so far at roughly **$1.40**.

The throughline across every milestone so far holds again, more sharply than ever: every real bug this project has found — the M2 double-counting, the M3 broken-trial-balance fixture, M4's due-date/re-flagging/wrong-account gaps, and now M5's stale-approval flag and paid-invoice reversal — came from actually running the agents against real, evolving state, never from reading the code. A design review would have called Gate 1 correct; only re-running Controller against a database that had already been through one close attempt showed it trusting a column that could go stale. That's the whole argument for "must actually work, not merely demonstrate intelligence" in one milestone.

### M6 status: built and verified live — CFO delegation is a real nested API call, and it found the gap in the system's own structured-output discipline

FP&A and CFO are real, callable agents: `tools/fpa_tools.py` + `agents/fpa/` (Sonnet — margin-trend and forecast-assumption judgment, not high-volume categorization), and `tools/cfo_tools.py` + `agents/cfo/` (Sonnet). FP&A's tools are thin wrappers around `services/statements.py`, `services/metrics.py`, and `services/forecast.py` — nothing here does its own arithmetic. Its one real write, `set_forecast_assumptions`, got a new table (`forecast_assumptions`, added to the live database without touching any existing data) and the same threshold-plus-approval-fallback shape as Controller's materiality gate: an empty basis is refused outright, and a shift of more than 1.0 percentage point from the currently active assumption doesn't get written directly at all — it's filed as a pending approval instead. `tests/test_fpa.py` covers that gate deterministically (5 tests: empty-basis refusal, first-write-through, small-shift-allowed, material-shift-files-approval-and-leaves-state-untouched, supersede-not-overwrite).

**CFO's `delegate_to_agent` is a real nested Claude API call, not a description of one.** `agents/cfo/delegate.py` runs a short, live, read-only Q&A loop against whichever specialist the CFO names — its own model tier (Haiku for Bookkeeping/AP/AR/Payroll, Sonnet for Controller/FP&A), its own filtered read-only tool subset, its own short system prompt, ending in its own `submit_answer` call — and hands the answer back as a tool result. Nothing this triggers can write anywhere: every write tool (`post_categorization`, `resolve_duplicate_invoice`, `set_forecast_assumptions`, and so on) is simply absent from the delegated tool list, the same segregation-of-duties principle from Section 10 applied to a new kind of call this system didn't have before M6.

**FP&A's first live run found something real that wasn't one of the six seeded anomalies.** Investigating August's budget variances, it flagged Sales commissions at $0.00 actual against a $6,903.96 budget — and traced it back further than asked: `gl_transactions` has *zero* rows on the `commissions` account across the entire 12-month history, despite a nonzero commissions budget being seeded every single month since September 2025. Verified directly against the database — real, not a hallucination. This isn't a bug in this session's code; it's a genuine, previously-invisible gap in the M0 dataset that four prior milestones of live agent runs and 30 passing tests never had a reason to surface, because nothing before FP&A's `get_all_budget_variances` scan had ever queried that specific account. It's the sharpest evidence yet for Section 1's V1 bar #5 ("the system surfaces a problem it wasn't told about") — a real, previously-undiscovered issue in the company's own books, not a bug in this project's build. Left as a finding for the write-up and a future milestone, not silently patched: fabricating commission postings to make the variance disappear would be exactly the wrong instinct for a finance system to have.

**The CFO's first live run exposed a real gap in this system's own structured-output discipline — Section 6 calls it "non-negotiable," and nothing before M6 had ever actually checked.** `submit_briefing` declares `facts`/`hypotheses`/`recommendations`/`open_questions` as arrays of strings, but the Messages API only *guides* generation toward a schema — it doesn't enforce it. The CFO's first run passed those fields as single CDATA-wrapped multi-line strings instead: content a human could still read, but a silent violation of the tool's own declared shape, accepted without complaint because nothing downstream ever checked. Fixed with `tools/schema_validate.py`, a small dependency-free checker of required fields and top-level types against a tool's own `input_schema`, wired into both `submit_briefing` and `submit_report`'s terminal handling: a violation is rejected with a specific, actionable `tool_result` instead of silently accepted, and the agent retries in the same run. **It immediately proved itself on the very next live run** — turn 3 called `submit_briefing` missing the required `hypotheses` field entirely, got rejected, and turn 4 retried with a clean, fully-conforming report. The same gap existed one layer down: `agents/cfo/delegate.py`'s own `submit_answer` mini-tool had no validation either, and a delegated FP&A call came back `"status": "answered", "answer": null` — the specialist had called `submit_answer` without a valid answer, and nothing had caught it. Same fix, same file, applied consistently rather than patched once and left inconsistent elsewhere.

**Fixing the shape problem surfaced a shape-shaped hole in the fix itself, and it's worth stating plainly rather than papering over: structural validation catches malformed shape, not empty content.** On a later run, a delegated specialist — cut off near its turn budget after real, expensive investigation (7-13 tool calls) — called `submit_answer` with `answer: "test"` and `evidence: ["a", "b"]`. That's a perfectly schema-valid array of strings and a perfectly schema-valid non-empty string. It is also completely worthless. No amount of type-checking catches that; catching it needs to evaluate what the content *means*, and Section 12's own evaluation framework already scopes that correctly — "human-graded sampling for CFO narrative quality... the one layer that stays qualitative because synthesis quality genuinely needs human judgment." Rather than reach for a fragile heuristic (reject short answers? reject "test"? that's a losing game), the legitimate fix was widening the delegated sub-agent's turn and token budget (8 turns/2048 tokens → 12 turns/3072 tokens) so a specialist has more room to actually finish before being forced to conclude — and documenting the rest honestly as the real, acknowledged boundary of what code alone can check. The CFO's own live reasoning is the better evidence this boundary is survivable in practice: on the run where Controller's delegated answer came back `"Test"` / `["a", "b"]`, the CFO noticed it wasn't a real answer and delegated to Controller again with a sharper follow-up question, unprompted, and got a substantive one the second time. That's not a designed retry loop — nothing tells the CFO to do this — it's the CFO's own judgment doing exactly what Section 3 asks of it.

**One more thing worth recording plainly: a live nested call genuinely failed with an Anthropic API 400 error mid-run** (`tool_use ids were found without tool_result blocks immediately after` — a malformed conversation state inside one `ask_specialist` call), **and the system's existing defensive architecture, built all the way back in M2, caught it without incident.** `execute_tool`'s blanket `try/except` (added in M2 after the first uncaught `KeyError` crash) turned the failure into a clean error `tool_result` instead of crashing the run; the CFO saw the failure, said so ("FPA call errored out — let me retry that one"), and retried the delegation itself. No corrupted state, no silent data loss, no manual intervention — the exact payoff of a defensive pattern adopted four milestones ago for an unrelated reason, now protecting a capability that didn't exist when it was written.

**Final live run: CFO delegated to three specialists (payroll, controller, fpa — exceeding the ≥2 bar), synthesized a fully schema-conforming, explicitly labeled briefing, and correctly flagged the same commissions gap and the unverifiable $0.00-liabilities balance sheet line as open questions for a human rather than guessing at either.** 35/35 tests passing (30 from M0-M5 plus 5 new `test_fpa.py` gate tests), the ledger still balances to the penny all-time, `forecast_assumptions` holds exactly one active row, and `audit_log` grew to 458 rows across all seven agents (FP&A's and CFO's own runs, plus every specialist's delegated sub-calls, correctly attributed to their own agent name by the same `_log()` pattern used since M2). Real API cost for this milestone — one FP&A run, four CFO runs (two spent finding and fixing the two structured-output bugs, one hitting the genuine API error, one clean final pass), all live: roughly **$2.00** (a conservative estimate priced entirely at Sonnet rates, since not every delegated call was cheaper Haiku), putting total live-API spend for the whole project at roughly **$3.40**.

### M7 status: built and verified live — the Dashboard Publisher, and a real bug from M6 got root-caused instead of just contained

`services/dashboard.py` is the "Dashboard Publisher" from Section 2/4, built exactly as scoped: a deterministic publish job, not an agent. `build_close_package` assembles the income statement, balance sheet, cash flow, the KPI bundle, the active forecast assumption, an audit-log summary by agent, and (optionally) the FP&A report and CFO briefing from the same run — every number either read straight from `services/statements.py`/`services/metrics.py` or passed through unchanged from an already-completed agent report. No LLM calls, no judgment calls. `publish()` writes it to `reports/<period>/` as both `close_package.json` (machine-readable) and `dashboard.html` (a single self-contained static page — no build step, no external assets). Wired into `orchestrator/run_close.py` as steps 6-8, running immediately after the period actually closes: FP&A, then CFO, then the publisher. Section 13's M7 bar — "full package generated only after both gates clear" — holds two ways at once: by construction (the code is unreachable unless Gate 1 and Gate 2 both already passed earlier in the same function) and independently in `build_close_package` itself, which re-reads `period_status` directly rather than trusting that guarantee, the same double-check pattern Gate 1 already uses on Controller's own verdict. `tests/test_dashboard.py` (7 tests) covers the refusal gate directly — no period_status row, and an explicitly `'open'` period — plus graceful degradation when FP&A/CFO didn't produce a report on a given run, and that `publish()` actually writes both files and logs to `audit_log`.

**One explicit, documented deviation from Section 4's diagram, decided deliberately rather than discovered by accident:** the diagram sequences FP&A → CFO → human approval → Dashboard Publisher, so the human approves the close *and* the CFO briefing in one gate. This build keeps M5's two gates exactly as shipped and tested — approval finalizes the close alone — then runs FP&A/CFO/publish immediately afterward. Reasoning, recorded in `orchestrator/run_close.py`'s own docstring: CFO output is explicitly advisory and executes nothing (Section 3), so it doesn't need its own approval gate, and re-sequencing a working, tested control gate to match a diagram exactly this late in the build is precisely the kind of scope-creep risk the charter's own "Where I'd Push Back" section warns against.

**The first live end-to-end run (all eight orchestrator steps, for real) reproduced the exact API error M6 had documented as "genuine but not root-caused" — four times in a row, inside the same FP&A delegation call, which finally exhausted that call's turn budget.** That reproducibility is what made root-causing it possible this time. The actual bug, present in every one of the eight agent runner files (`agents/*/run.py` and `agents/cfo/delegate.py`) since as far back as M2: each turn loop branched on `response.stop_reason != "tool_use"` to decide whether to send the accumulated `tool_results` back to the API or a plain-text nudge instead — but a turn can call a tool **and** still end with a `stop_reason` other than `"tool_use"` (most commonly `max_tokens`, which is exactly what FP&A's larger tool outputs — full income statements, budget-variance lists — were occasionally brushing against). When that happened, the code sent the nudge text instead of the tool results, leaving an assistant message with an unanswered `tool_use` id — which the Messages API correctly rejects on the very next call with `tool_use ids were found without tool_result blocks immediately after`. M2's defensive `try/except` had been catching the symptom cleanly for four milestones; it just never occurred to check whether the trigger was actually reproducible until it fired four times in one delegation call instead of once. **Fixed identically in all eight files**: branch on whether `tool_results` is non-empty, not on why the model stopped generating. Verified by an isolated live re-run of the exact FP&A delegation call that had failed (clean `"status": "answered"`, no error) and then a full second live orchestrator run, all eight steps, zero API errors anywhere. All 42 tests still pass post-fix.

**Fixing that unmasked — and reconfirmed, on the very same live run — two things already on record from M6, now doubly verified rather than resting on a single occurrence.** First, `schema_validate.py` caught two more distinct real violations in the very same CFO run: `submit_briefing`'s `facts` field arriving as a single CDATA-wrapped string again (the identical failure mode from M6, independently reproduced by the model), rejected and retried; then a second attempt missing the required `open_questions` field entirely, also rejected and retried; the third attempt was clean. Second, the semantic-emptiness limitation reproduced too — an isolated direct test of `ask_specialist(conn, "fpa", ...)` made 7 genuine tool calls (income statement, budget variances, forecast data) and then still called `submit_answer` with `answer: "Test"`, `evidence: ["a", "b"]`. Schema-valid, still worthless, still not something a type-checker can catch — exactly the boundary documented and deliberately left unpatched in M6, now confirmed as a real recurring behavior rather than a one-off. In the full orchestrator's own live run, the CFO once again noticed the FP&A delegation had come back placeholder text and flagged it explicitly in its briefing's recommendations ("Re-engage FP&A for a substantive... analysis — the initial delegation did not return a usable answer") rather than treating "Test" as a real finding.

**Final live run: all eight orchestrator steps end to end, zero errors, full package published.** Bookkeeping/AP/AR/Payroll/Controller ran clean against the already-closed August period (re-confirming M5's idempotency fixes still hold), Gate 1 and Gate 2 both passed against existing state, the period re-closed, FP&A and CFO both produced fully schema-conforming reports, and the Dashboard Publisher wrote `reports/2026-08/close_package.json` and `reports/2026-08/dashboard.html` — a real, openable static report with the income statement, balance sheet (balanced), cash flow, KPIs, the active forecast assumption, the full FP&A report, the full CFO briefing, and an audit-log summary by agent (690 rows total across all eight agents plus the orchestrator and the publisher itself). 42/42 tests passing (35 from M0-M6 plus 7 new `test_dashboard.py` gate tests), ledger still balanced to the penny all-time ($60,125,607.72 = $60,125,607.72). Real API cost for this milestone — two full orchestrator runs (one that surfaced the bug, one clean verification run) plus one isolated delegation re-test, all live: roughly **$1.80**, putting total live-API spend for the whole project at roughly **$5.20**.

### M8 status: built and verified live — a completely fresh month, a real live mistake caught by the system's own controls, and a genuine audit-trail gap fixed along the way

Section 13's M8 bar is "one full month, injected anomalies found, audit trail replayable, human approval recorded" — four claims this project has been making incrementally, one milestone at a time, against the *same* August 2026 database that's carried five milestones of real audit history and hand-applied corrections since M2. M8 is what actually proves all four at once, against a database no agent has ever seen before. `orchestrator/demo_run.py` regenerates a completely fresh `db/demo_meridian.db` via `data/seed/generate_meridian.py` with its output path overridden (deterministic under `random.seed(42)` — the identical six seeded anomalies every time, per `data/seed/README.md`'s answer key), then runs the exact same live pipeline as every other milestone against it: five domain agents, Gate 1, Gate 2, a human approval, FP&A, CFO, and the Dashboard Publisher — nothing about the pipeline itself is special-cased for a demo.

**Building the verification step surfaced a real, previously-unnoticed gap in the audit trail itself — the very thing this milestone was supposed to prove was already solid.** Trying to write `tools/replay_audit_trail.py` and reconstruct "what did each agent actually conclude" from `audit_log` alone came up empty: every one of the seven agents' terminal `submit_report` / `submit_briefing` calls (`agents/*/run.py`'s `execute_tool`) returned a bare `{"status": "report_received"}` receipt and never wrote the report's actual content anywhere — not even CFO's, despite `cfo_tools.submit_briefing` already existing and already knowing how to log itself correctly; `cfo/run.py` simply never called it. Every intermediate tool call (`get_x`, `flag_y`, `check_z`) was faithfully logged, but the one thing Section 1's V1 bar #4 actually asks for — an audit trail that answers "who did what and **why**," replayable after the fact — was missing exactly the "why" for six milestones running, silently, because nothing had ever tried to replay it end to end before. Fixed with `tools/audit.py` (a single shared `log_report()`, the same insert shape every `tools/*.py` module already had privately, exposed once instead of copied an eighth time) wired into all seven runners; CFO's fix was narrower — actually calling the `cfo_tools.submit_briefing` that already existed instead of bypassing it.

**The first live pass blocked at Gate 1, and it wasn't a code bug — it was a specialist agent getting a real judgment call wrong, live, and the system's own controls catching it.** AP's independent run of the duplicate detector found the exact seeded CloudScale pair (`ap_2457e406df` / `ap_a52c5ddb25`, $14,200 each, 3 days apart) and, on its own live reasoning, dismissed it as a false positive ("two separate legitimate billing items"). Controller — which reruns its own independent duplicate scan rather than trusting AP's, precisely so a specialist's dismissal can't quietly become the last word — flagged the identical pair on its own pass, raised a correction request, and correctly refused to mark the period ready once combined unresolved exposure ($28,032.95) crossed the $25k materiality threshold. This is Section 10's segregation-of-duties control working exactly as designed, unprompted, against a mistake this specific live run actually made — not a hypothetical the architecture was built to withstand, an hallucinated failure it stopped: **re-running the same pipeline against the same database, AP saw Controller's correction request, correctly reversed the unpaid duplicate, correctly refused to reverse the already-paid one (the exact M5-discovered paid-invoice guard, firing live, unprompted, for the right reason), and was explicit that the paid duplicate represents a genuine double-payment needing vendor collections follow-up outside AP's scope.** Gate 1 passed on the next run. This is arguably the strongest evidence in the whole project that segregation-of-duties matters in practice, not just on paper: a specialist got it wrong, and the close still couldn't complete on a wrong answer.

**Final live run: Gate 2 blocked as designed, a human approved via `orchestrator/approve.py`, and the period closed for a database that had never been touched before this milestone.** `tools/verify_anomalies.py`'s scorecard, checked against real database state and real submitted reports rather than trusted from a summary: **6 of 6 seeded anomalies found** — the duplicate invoice (code-level: the duplicate row actually marked `disputed`), the $9,800 ambiguous transaction (code-level: actually escalated, not guessed at), and the gross margin decline, Engineering payroll growth, AR concentration, and Marketing overrun all present by name and figure in the relevant specialist's own submitted findings. `tools/replay_audit_trail.py` reconstructed a full, readable, chronological narrative from 202 `audit_log` rows across nine agents (including, now, every agent's actual final conclusion, not just its intermediate tool calls) — written to `reports/demo/2026-08/audit_trail_replay.txt` alongside the scorecard and the usual close package. 55/55 tests passing (42 from M0-M7 plus 13 new: `test_verify_anomalies.py`, `test_replay.py`). Real API cost for this milestone — three full live passes against the fresh database (one that hit the AP mistake, one that resolved it and blocked at Gate 2, one clean final close) plus FP&A and CFO: roughly **$1.90**, putting total live-API spend for the whole project at roughly **$7.10**.

### M9 status: built

The repository is public at `github.com/deion-wells-ross/auri-finance`. Per an explicit scoping decision (a live "browse the data" or "trigger a real run" web app was ruled out on cost — real Anthropic API spend per visitor — and concurrency grounds — a single SQLite file with no per-visitor isolation), M9 is a static showcase: `docs/index.html`, a self-contained page with no build step, deployed via GitHub Pages. It carries the agent roster, the two-gate control architecture, and — as its centerpiece — the real M8 story told with direct quotes pulled from that milestone's actual live run: AP dismissing the seeded CloudScale duplicate as "two separate legitimate billing items," Controller's independent re-check catching it anyway and blocking Gate 1, and AP's own corrected conclusion on the very next pass. The 6/6 anomaly scorecard and real August-close KPIs (73.05% gross margin, 4.4-month runway, 202 audit rows) are pulled from the actual `reports/demo/2026-08/` output, not invented for the page. `docs/sample-dashboard.html` is an unedited copy of a real generated `dashboard.html`, linked directly from the showcase, so a visitor can see one actual artifact the system produced rather than only a description of it. The same honesty standard the rest of this project has held to applies here too: the page's "built by finding real bugs" section names four of the real defects this document already describes (the M7 `tool_result`/`stop_reason` bug, the M8 audit-log gap, and the two M5 control bugs), rather than presenting only a success narrative.

---

## 9. Deterministic vs. AI Boundary

**Rule of thumb:** if it's a formula, it's code; if it's a judgment call synthesizing multiple facts into a decision or narrative, it's an agent — and the agent calls the code as a tool rather than trying to reproduce the arithmetic itself.

| Deterministic (code) | Agent/AI (judgment) |
|---|---|
| GL posting, trial balance | Categorizing an ambiguous transaction |
| Income statement, balance sheet, cash-flow statement | Deciding *when* to regenerate statements, or that a discrepancy needs investigating |
| All metric formulas (margin, CAC, LTV, runway, ratios) | Interpreting *why* a metric moved |
| AP/AR aging buckets | Deciding which overdue account to prioritize, or that a charge looks wrong |
| Payroll totals, OT calculations | Judging whether payroll growth relative to revenue is a problem |
| Budget-vs-actual variance math | Explaining the variance and setting next period's assumptions |
| Forecast projection math (given assumptions) | Choosing/defending the assumptions themselves, from evidence |
| Duplicate-detection algorithm (rule-based match) | Deciding whether a flagged near-duplicate is actually a problem |

This table is also your best defense against silent hallucination: any time a number in a report didn't come from the deterministic layer, that's a bug, full stop — not a matter of prompt tuning.

---

## 10. Control Architecture

- **Risk-tiered tools, tagged and enforced in code.** Every tool function carries a tier (`autonomous` / `review_required` / `approval_required`). Approval-required tools check an `approvals` table for a matching, unexpired, human-issued approval record before executing anything; absent that record, the call returns a `pending_approval` result rather than executing. This is a code path, not a prompt instruction — see Section 6's closing point.
- **Structural gates, not soft ones.** The Controller's "ready to report" flag and the human final-approval flag are literal booleans the orchestrator checks before advancing the workflow — not something an agent can talk itself past.
- **Audit log on every tool call.** `agent, action, inputs, outputs, timestamp, linked_evidence` — append-only, queryable, and what makes the "watch it work" portfolio demo possible (Section 14) since you can replay exactly what happened and why.
- **Validation before trust propagates.** Deterministic reconciliation checks (trial balance must balance; AP/AR subledgers must tie to GL control accounts) gate whether the Controller can approve the period — these are hard checks, not agent opinions.
- **Idempotency on every state-mutating action.** Posting a journal entry, recording an approval, etc. use idempotency keys so a retried call can't double-post — important once you add retries.
- **Failure handling:** retries with backoff on tool/API errors; anything the Controller can't resolve after retry goes to an exception queue surfaced to the human rather than silently skipped or silently guessed through.

### Correction requests — how a finding actually gets fixed

This is the piece the charter didn't specify: when the Controller (or CFO, or FP&A) finds a problem that lives in *another* agent's domain, what happens next? Real finance teams have a well-worn answer, and it's worth importing directly rather than improvising.

**How it works in a real close.** A controller who spots a missing accrual doesn't reach into the AP subledger and fix it themselves. They send it back to whoever owns that data — a query, a ticket, a line in the close checklist — with the specific finding, the evidence, and (usually) a deadline, because the close has one. The owning team either corrects it and says so, or pushes back with a reason, and either way it's logged. Two things are actually operating here, both worth keeping: **segregation of duties** (the person who validates a number is structurally not the person who is allowed to change it — that's a real internal-controls principle, not bureaucracy for its own sake) and a **materiality threshold** (nobody escalates a $4 rounding difference; there's a dollar or percentage bar below which a variance is auto-accepted rather than investigated).

**The agentic equivalent, concretely:**

- **A finding becomes a `correction_requests` row, not a silent fix.** When Controller (or CFO/FP&A) identifies an issue whose data belongs to another agent, it writes `{from_agent, to_agent, finding, evidence, requested_action, materiality_amount}` rather than calling a write tool on that agent's data.
- **Only the owning agent can write to its own domain.** Controller and CFO hold no `post_journal_entry`, `update_invoice`, or equivalent write tools at all — by tool scoping, not by instruction. If Controller finds a miscategorized transaction, it can only ask Bookkeeping to reconsider it; Bookkeeping is the one that actually calls `post_journal_entry` again, with the new information in context. This is the segregation-of-duties principle enforced the same way everything else in Section 10 is enforced: in the tool layer, not the prompt.
- **The owning agent responds, on the record.** It either accepts the finding and re-processes (and the new output supersedes the old, both kept in the audit log), or disputes it with a stated reason — "not a duplicate, these are two separate hosting tiers billed separately" is a legitimate answer, and the system needs to allow it rather than assuming the first agent to speak is always right.
- **A materiality threshold decides what's even worth a correction request.** Each domain gets a configured threshold (e.g., an AP amount discrepancy under $50, or a metric variance under 2%) below which Controller auto-accepts rather than round-tripping — otherwise every rounding difference becomes a negotiation and the close never finishes. This threshold is a number you and I set deliberately per account, not something an agent infers.
- **Rounds are capped, then it escalates to a human — never loops forever.** Two rounds of back-and-forth between Controller and an owning agent is the ceiling; if it's still unresolved, `correction_requests.status` becomes `escalated` and it surfaces as an open item in the CFO briefing and the human approval queue, exactly like a real close that hits its deadline with an item still open. This mirrors reality more than an "agents negotiate until they agree" design would — real closes ship with documented open items rather than infinite meetings.

This isn't cosmetic: it's also the best moment in the whole demo. A reviewer watching the audit log sees Controller flag something, watch AP push back with a real reason, Controller accept the correction, and the number change — visible, attributable, and disagreement-tolerant. That's a stronger proof of "these are actual coordinating agents" than any amount of them agreeing with each other on the first try.

---

## 11. Repository Structure

```
auri-finance/
  CLAUDE.md                    # repo-level guidance for Claude Code / dev subagents
  docs/
    architecture/              # this document, diagrams, decision log
    case-study/                # portfolio write-up drafts
  data/
    seed/                      # synthetic Meridian generator scripts + seed data
  db/
    schema.sql
    migrations/
  services/                    # deterministic, unit-tested, no LLM calls
    statements.py
    metrics.py
    aging.py
    payroll_calc.py
    forecast.py
  agents/                      # one folder per finance agent
    bookkeeping/  (system_prompt.md, tools.py, tests/)
    ap/  ar/  payroll/  controller/  fpna/  cfo/
  orchestrator/                # month-end workflow state machine, approval gates
  tools/                       # tool implementations exposed to agents, permission-tagged
  mcp/                         # added in V1.1, once justified (Section 6)
  evals/                       # golden datasets, eval harness (Section 12)
  ui/                          # minimal approval interface / dashboard
  .claude/
    agents/                    # dev-team subagent definitions (Section 7)
    commands/                  # /seed-data, /run-close, /implement-agent, /review-controls
    hooks/
```

---

## 12. Evaluation Framework

Two distinct layers — don't conflate "is the code right" with "is the agent doing its job":

**Layer 1 — deterministic correctness (ordinary software testing).** Unit tests for every service: given fixed inputs, statements/metrics/aging must equal expected outputs exactly. No LLM involved, no tolerance for "close enough."

**Layer 2 — agent evaluation, built on the Section 8 answer key:**
- **Golden transaction sets** for Bookkeeping: known-correct categorizations; measure precision/recall, and confirm low-confidence cases actually get flagged rather than guessed.
- **Injected-anomaly recall** for Controller/AP/AR/Payroll/FP&A: does the team catch the specific problems you seeded (duplicate vendor charge, AR concentration, payroll/revenue divergence, margin decline)? This is your single best "is this actually agentic" metric, since it's finding something, not restating something.
- **Structured-output schema validation** on every agent output — a handoff that fails schema validation is a bug regardless of whether the content looks reasonable.
- **Groundedness checks**: every number an agent states in prose must match the deterministic layer's output for that number — mechanically diffable, catches hallucination directly rather than relying on human read-through.
- **Human-graded sampling** for CFO narrative quality (clarity, correct fact/hypothesis/recommendation labeling per Section 9's intelligence standard) — the one layer that stays qualitative because synthesis quality genuinely needs a human judgment call.

Build this as a repeatable harness (Claude Code's eval tooling or a lightweight custom runner) that runs the full pipeline against the fixed synthetic month and diffs outputs — this is also what makes "did my last change break something" answerable in seconds instead of a manual re-read.

---

## 13. Build Sequence — Testable Milestones

| # | Milestone | What "done" looks like | Status |
|---|---|---|---|
| M0 | Data model + synthetic Meridian generator | DB seeds cleanly; referential integrity holds; anomaly answer key documented | ✅ Done |
| M1 | Deterministic services (statements, metrics, aging basics) | Unit tests pass against known hand-computed figures | ✅ Done (24/24 passing) |
| M2 | Bookkeeping Agent | Run against a labeled transaction batch; categorization accuracy measured against answer key | ✅ Done (escalated the ambiguous $9,800 txn correctly, live) |
| M3 | Controller Agent | Catches at least one deliberately broken trial balance in a test fixture; correctly escalates | ✅ Done (also rediscovered the CloudScale duplicate live, unprompted) |
| M4 | AP + AR + Payroll Agents | Each correctly flags its seeded anomaly (duplicate charge / AR concentration / payroll divergence) | ✅ Done (AP resolved the duplicate live; Controller then closed the period) |
| M5 | Orchestrator + audit log + approval-gate stub | Full pipeline runs end to end for one synthetic month; gate actually blocks without approval | ✅ Done (blocked live without approval, then closed live once approved — and found 2 real control bugs doing it) |
| M6 | FP&A + CFO Agents | CFO demonstrably delegates to ≥2 specialists and synthesizes a labeled (fact/hypothesis/recommendation) briefing | ✅ Done (delegated live to 3 specialists via real nested API calls; found and fixed 2 structured-output validation gaps along the way) |
| M7 | Reporting/metrics wiring + Dashboard publish step | Full package generated only after both gates clear | ✅ Done (live end to end; also root-caused a real API-error bug present in every agent since M2, not just contained it) |
| M8 | End-to-end demo run | One full month, injected anomalies found, audit trail replayable, human approval recorded | ✅ Done (fresh database, live; 6/6 anomalies found; caught and fixed a real audit-trail gap; a real AP misjudgment was caught and corrected by the system's own controls) |
| M9 | Portfolio artifacts | Case study, architecture diagram, demo walkthrough | ✅ Done (static showcase page + real generated dashboard sample, deployed via GitHub Pages) |

---

## 14. First Milestone — What to Build Next

**M0: the data model and synthetic dataset generator.** Nothing else in this document can be truthfully tested until real data exists — even "does the Bookkeeping Agent categorize well" is meaningless without a labeled answer key to score it against. Concretely, next session: we finalize the schema in Section 8 together (I'd want your sign-off on the anomaly list specifically, since those are the "correct answers" for every later eval), then hand the generator script to a Data Engineer subagent to implement against that approved spec.

---

## Where I'd Push Back If You Push Further

Two things worth flagging now, since Section 16 asked me to: first, the temptation once M0–M5 work will be to immediately add Sales/Marketing/Customer AURI or start wiring n8n and MCP "since they're planned anyway" — resist that until the Finance V1 demo is fully working end to end; half-built breadth reads worse in a portfolio than a complete narrow system. Second, watch for FP&A and CFO scope creep into doing arithmetic themselves "just this once" for convenience — every time that happens, it should be treated as a bug in the deterministic layer (a formula you haven't built yet), not a shortcut worth keeping.
