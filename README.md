# AURI Finance

An agentic finance department for Meridian Analytics (a fictional B2B SaaS
company) — Project 01 of AURI. See `docs/architecture/` for the full design
charter: V1 scope, agent roster, control architecture, and build sequence.

## Status

- **M0 — data model & synthetic dataset: done.** `db/schema.sql` (13 tables),
  `data/seed/generate_meridian.py` (deterministic generator), seeded into
  `db/meridian.db`. Six anomalies deliberately seeded into the August 2026
  closing period — see `data/seed/README.md` for the answer key.
- **M1 — deterministic services: done.** `services/` — statements, metrics,
  aging, payroll, forecast — pure Python, no LLM calls, computing every
  number an agent will later reason about. 24 tests in `tests/`, all
  passing against the seeded database.
- **M2 — Bookkeeping Agent: done, verified live.** `tools/bookkeeping_tools.py`
  + `agents/bookkeeping/` — a real Claude API tool-use loop (Haiku 4.5), run
  live against the seeded August bank feed. It correctly escalated the
  genuinely ambiguous $9,800 transaction instead of guessing, posted 4
  balanced journal entries for the transactions it was actually confident
  about, and its first live run caught a real double-counting bug in the M0
  seed data (fixed — see the design charter's M2 section). Every tool call
  is in `audit_log`.
- **M3 — Controller Agent: done, verified live.** `tools/controller_tools.py`
  + `agents/controller/` — a live Claude API loop (Sonnet 5) that validates a
  closing period: trial balance, AR/AP subledger tie-out, and its own rerun
  of the M1 duplicate-invoice detector. It holds zero write access into any
  other agent's data — its only lever on a finding is a row in
  `correction_requests`. Live against the real August close it rediscovered
  the seeded CloudScale duplicate *unprompted*, filed a correction request
  against the (not-yet-built) AP Agent, and correctly refused to mark the
  period ready once unresolved exposure crossed the $25k materiality
  threshold. Against a deliberately broken trial balance fixture
  (`tests/fixtures/make_broken_trial_balance_fixture.py`), it caught the
  imbalance immediately, treated everything downstream as moot, and
  escalated rather than guessing. `tests/test_controller.py` covers the
  deterministic layer.
- **M4 — AP, AR, Payroll & Workforce Agents: done, verified live.**
  `tools/ap_tools.py`, `tools/ar_tools.py`, `tools/payroll_tools.py` +
  `agents/{ap,ar,payroll}/`. AP is the only one with write access (scoped
  to its own domain); AR and Payroll are entirely read-only by design. Live
  runs found and fixed three more real gaps: a due-date bug that made
  overdue AR invisible to aging math, a duplicate detector that kept
  re-flagging an already-resolved invoice, and a Payroll agent guessing at
  a nonexistent budget account name. Capstone: ran the full M2→M4 agent
  chain back to back — Bookkeeping escalates, Controller finds the seeded
  duplicate unprompted and blocks the close, AP resolves it live, and
  Controller's next pass actually marks the period ready. A real number
  (August gross margin) changed because a real agent fixed a real error.
- **M5 — Orchestrator + human approval gate: done, verified live — period
  closed for the first time.** `orchestrator/run_close.py` runs all five
  agents in sequence, re-checks Controller's "ready" verdict independently
  (Gate 1), then refuses to close the period at all — the one write anywhere
  that flips `period_status.status` — until a human approves a
  `finalize_period_close` request via the five-line `orchestrator/approve.py`
  CLI (Gate 2). The first live run blocked at Gate 2 as designed, but along
  the way found two real control bugs by re-running the same agents against
  a database that already had a full close in it: Bookkeeping's escalation
  wasn't idempotent (re-filed duplicate approvals on a second run), and
  Controller's own "ready" flag never got cleared on refusal, so the
  orchestrator's independent Gate 1 check could pass on a stale approval.
  Fixing those surfaced a third, subtler one — a live AP run had genuinely
  misjudged a legitimate recurring charge as a duplicate and reversed an
  already-*paid* invoice, which the AP tools let happen because nothing
  checked invoice status before booking the reversal. All three fixed
  (idempotent escalation, a cleared-on-refusal approval flag, and a
  paid-invoice refusal plus a new `dismiss_duplicate_candidate` tool so a
  correct "not a duplicate" call is remembered instead of re-litigated every
  run). Re-run clean: Gate 1 passed, Gate 2 blocked and printed the approval
  command, a human approved via the CLI, and the next run closed the period
  — `period_status.status='closed'`, `approved_by` holding the human's name,
  for the first time in this project's history. 30/30 tests passing, ledger
  balances to the penny all-time.
- **M6 — FP&A + CFO Agents: done, verified live.** `tools/fpa_tools.py` +
  `agents/fpa/` (Sonnet), `tools/cfo_tools.py` + `agents/cfo/` (Sonnet). The
  CFO's `delegate_to_agent` is a real nested Claude API call — a live,
  read-only Q&A loop against whichever specialist it names, not a
  description of reading a stored report. FP&A's own first live run found
  something real: Sales commissions have been budgeted every month since
  September 2025 but never once posted to the GL — a genuine, previously-
  invisible gap in the company's own books, verified against the database,
  left as a finding rather than silently patched. The CFO's first live run
  exposed a real gap in this system's own structured-output discipline:
  `submit_briefing` silently accepted malformed output (arrays passed as
  single strings) because nothing checked the model's output against its
  own declared schema. Fixed with `tools/schema_validate.py`, wired into
  every terminal report across both new agents — it caught a real
  violation on the very next live run and forced a clean retry. A genuine
  Anthropic API error inside a nested delegation call was caught cleanly by
  the same defensive `try/except` pattern added back in M2, with the CFO
  agent itself noticing and retrying, unprompted. Final live run: CFO
  delegated to three specialists (payroll, controller, fpa) and produced a
  fully schema-conforming, explicitly labeled briefing. 35/35 tests
  passing, ledger balances to the penny all-time.
- **M7 — Dashboard Publisher + reporting wiring: done, verified live.**
  `services/dashboard.py` — a deterministic publish job, not an agent, per
  the design charter's own scoping — assembles the closed period's
  statements, KPIs, active forecast assumption, FP&A report, and CFO
  briefing into a real close package (`reports/<period>/close_package.json`
  + `dashboard.html`), and refuses outright unless `period_status` is
  actually `'closed'`. Wired into the orchestrator as its last three steps
  (FP&A, then CFO, then publish), running only once both gates have
  cleared. Running it live end to end reproduced — four times in a row —
  the "genuine but not root-caused" API error M6 had documented: a bug
  present in *every* agent runner since M2, where a turn that called a
  tool but ended for a reason other than `stop_reason == "tool_use"` (most
  often `max_tokens`) silently dropped its `tool_result`, corrupting the
  next API call. Root-caused and fixed identically across all eight agent
  runner files. A second full live run afterward: zero API errors, a real
  close package published, 42/42 tests passing, ledger still balanced to
  the penny all-time.
- **M8 — end-to-end demo run: done, verified live, against a database no
  agent had ever seen.** `orchestrator/demo_run.py` regenerates a
  completely fresh `db/demo_meridian.db` (the same deterministic M0
  generator, output path overridden — identical seeded anomalies every
  time) and runs the full live pipeline against it, separately from the
  project's working `db/meridian.db`. Building the verification step
  found a real, six-milestone-old gap: every agent's terminal report was
  logged nowhere — only its intermediate tool calls were, so the audit
  trail was missing the one thing it most needed, each agent's own
  conclusion. Fixed (`tools/audit.py`, wired into all seven runners). The
  first live pass then genuinely tripped over its own control architecture
  working as designed: AP misjudged the seeded duplicate invoice as a
  false positive, Controller's independent re-check caught it anyway and
  blocked the close, and AP correctly resolved it on the very next pass.
  Final run: **6 of 6 seeded anomalies found** (`tools/verify_anomalies.py`),
  a full 202-row audit trail replayed end to end
  (`tools/replay_audit_trail.py`, `reports/demo/2026-08/`), a human
  approval on record, and the period closed. 55/55 tests passing.
- **M9 onward** (portfolio artifacts): not started yet.

## Setup

```
pip install -r requirements.txt
python3 data/seed/generate_meridian.py   # (re)builds db/meridian.db
python3 -m pytest tests/ -v
```

## Layout

```
db/                  schema.sql, meridian.db
data/seed/           synthetic dataset generator + its own README
services/            deterministic calculation layer (statements, metrics, aging, payroll_calc, forecast)
tests/               Layer-1 correctness tests against the seeded database
docs/architecture/   the design charter this was built from
agents/              (M2+) one folder per finance agent
orchestrator/        (M5+) month-end workflow state machine
reports/             (M7+) published close packages, one folder per period
                      (M8+) reports/demo/ — fresh-database end-to-end demo output
```

Full repository layout and rationale: `docs/architecture/v1-design-charter.md`, Section 11.
