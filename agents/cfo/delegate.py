"""Delegation harness for the CFO Agent's delegate_to_agent tool (M6).

Section 3's CFO row lists `delegate_to(agent, question)` as a real tool, and
Section 13's M6 bar is "CFO demonstrably delegates to >=2 specialists." This
module is what makes that literally true rather than metaphorically true: a
live, nested Claude API call to a real specialist agent — not the CFO
reading a specialist's stored submit_report output.

Deliberately narrow, matching Section 3's own CFO row ("Approval-required:
nothing executes — CFO output is always advisory"): every specialist here
gets ONLY its read-only tools. No specialist invoked through this path can
post a journal entry, categorize a transaction, resolve a duplicate, raise
a correction request, or set a forecast assumption — the CFO can ask any
specialist a question, but nothing the CFO does ever has a side effect on
gl_transactions, ap_invoices, approvals, or correction_requests. That's not
a limitation bolted on; it's the same segregation-of-duties principle
(Section 10) applied to a new kind of call this system didn't have before
M6 — a live agent-to-agent question, not just agent-to-tool.

Each specialist keeps its own model tier from its normal agent (Haiku for
high-volume investigation, Sonnet for materiality/evidence judgment) —
delegation doesn't change what kind of thinking the question needs.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import schema_validate  # noqa: E402
from tools import ap_tools as apt  # noqa: E402
from tools import ar_tools as art  # noqa: E402
from tools import bookkeeping_tools as bk  # noqa: E402
from tools import controller_tools as ct  # noqa: E402
from tools import fpa_tools as fpat  # noqa: E402
from tools import payroll_tools as pt  # noqa: E402

MAX_DELEGATE_TURNS = 12

SUBMIT_ANSWER_TOOL = {
    "name": "submit_answer",
    "description": "Call exactly once, last, once you've investigated enough to answer the question directly.",
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "description": "A direct, concise answer to the question you were asked."},
            "evidence": {"type": "array", "items": {"type": "string"}, "description": "The specific figures/facts your answer relies on."},
        },
        "required": ["answer", "evidence"],
    },
}


def _bookkeeping_tools():
    tools = [
        {"name": "get_uncategorized_txns", "description": "Every bank_feed row not yet posted to the GL.",
         "input_schema": {"type": "object", "properties": {}, "required": []}},
        {"name": "get_chart_of_accounts", "description": "Accounts postable against.",
         "input_schema": {"type": "object", "properties": {}, "required": []}},
    ]

    def execute(conn, name, inp):
        if name == "get_uncategorized_txns":
            return {"transactions": bk.get_uncategorized_txns(conn)}
        if name == "get_chart_of_accounts":
            return {"accounts": bk.get_chart_of_accounts(conn)}
        return {"error": f"unknown tool {name}"}

    return tools, execute


def _ap_tools():
    tools = [
        {"name": "get_ap_aging", "description": "AP aging by vendor as of a date.",
         "input_schema": {"type": "object", "properties": {"as_of_date": {"type": "string"}}, "required": ["as_of_date"]}},
        {"name": "get_vendor_history", "description": "All invoices for a vendor plus trailing average.",
         "input_schema": {"type": "object", "properties": {"vendor_id": {"type": "string"}, "before_period": {"type": ["string", "null"]}}, "required": ["vendor_id"]}},
        {"name": "flag_unusual_charges", "description": "Charges that are a multiple of a vendor's trailing average.",
         "input_schema": {"type": "object", "properties": {"period": {"type": "string"}, "multiple": {"type": "number"}}, "required": ["period"]}},
        {"name": "flag_duplicate_invoices", "description": "Rule-based duplicate-invoice scan.",
         "input_schema": {"type": "object", "properties": {}, "required": []}},
        {"name": "review_correction_requests", "description": "Open/disputed correction requests addressed to AP.",
         "input_schema": {"type": "object", "properties": {}, "required": []}},
    ]

    def execute(conn, name, inp):
        if name == "get_ap_aging":
            return apt.get_ap_aging(conn, as_of_date=inp["as_of_date"])
        if name == "get_vendor_history":
            return apt.get_vendor_history(conn, vendor_id=inp["vendor_id"], before_period=inp.get("before_period"))
        if name == "flag_unusual_charges":
            return {"flags": apt.flag_unusual_charges(conn, period=inp["period"], multiple=inp.get("multiple", 2.0))}
        if name == "flag_duplicate_invoices":
            return {"candidates": apt.flag_duplicate_invoices(conn)}
        if name == "review_correction_requests":
            return {"requests": apt.review_correction_requests(conn)}
        return {"error": f"unknown tool {name}"}

    return tools, execute


def _ar_tools():
    tools = [
        {"name": "get_ar_aging", "description": "AR aging by customer as of a date.",
         "input_schema": {"type": "object", "properties": {"as_of_date": {"type": "string"}}, "required": ["as_of_date"]}},
        {"name": "get_customer_payment_history", "description": "A customer's invoice/payment history.",
         "input_schema": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]}},
        {"name": "flag_concentration_risk", "description": "Customers over a % of total AR.",
         "input_schema": {"type": "object", "properties": {"as_of_date": {"type": "string"}, "threshold_pct": {"type": "number"}}, "required": ["as_of_date"]}},
        {"name": "prioritize_collections", "description": "Overdue invoices ranked by days_overdue x amount.",
         "input_schema": {"type": "object", "properties": {"as_of_date": {"type": "string"}}, "required": ["as_of_date"]}},
    ]

    def execute(conn, name, inp):
        if name == "get_ar_aging":
            return art.get_ar_aging(conn, as_of_date=inp["as_of_date"])
        if name == "get_customer_payment_history":
            return art.get_customer_payment_history(conn, customer_id=inp["customer_id"])
        if name == "flag_concentration_risk":
            return {"flagged": art.flag_concentration_risk(conn, as_of_date=inp["as_of_date"], threshold_pct=inp.get("threshold_pct", 25.0))}
        if name == "prioritize_collections":
            return {"ranked": art.prioritize_collections(conn, as_of_date=inp["as_of_date"])}
        return {"error": f"unknown tool {name}"}

    return tools, execute


def _payroll_tools():
    tools = [
        {"name": "get_payroll_summary", "description": "Payroll totals by department for a period.",
         "input_schema": {"type": "object", "properties": {"period": {"type": "string"}}, "required": ["period"]}},
        {"name": "get_headcount_by_dept", "description": "Headcount by department as of a date.",
         "input_schema": {"type": "object", "properties": {"as_of_date": {"type": "string"}}, "required": ["as_of_date"]}},
        {"name": "compare_payroll_growth", "description": "Payroll cost/headcount growth between two periods for a department.",
         "input_schema": {"type": "object", "properties": {"department_id": {"type": "string"}, "period_a": {"type": "string"}, "period_b": {"type": "string"}}, "required": ["department_id", "period_a", "period_b"]}},
        {"name": "get_revenue_growth", "description": "Company revenue growth between two periods.",
         "input_schema": {"type": "object", "properties": {"period_a": {"type": "string"}, "period_b": {"type": "string"}}, "required": ["period_a", "period_b"]}},
        {"name": "get_salary_account_map", "description": "Department -> salary account_id mapping.",
         "input_schema": {"type": "object", "properties": {}, "required": []}},
        {"name": "compare_to_budget", "description": "Budget vs. actual for a department/account/period.",
         "input_schema": {"type": "object", "properties": {"department_id": {"type": "string"}, "account_id": {"type": "string"}, "period": {"type": "string"}}, "required": ["department_id", "account_id", "period"]}},
    ]

    def execute(conn, name, inp):
        if name == "get_payroll_summary":
            return pt.get_payroll_summary(conn, period=inp["period"])
        if name == "get_headcount_by_dept":
            return pt.get_headcount_by_dept(conn, as_of_date=inp["as_of_date"])
        if name == "compare_payroll_growth":
            return pt.compare_payroll_growth(conn, department_id=inp["department_id"], period_a=inp["period_a"], period_b=inp["period_b"])
        if name == "get_revenue_growth":
            return pt.get_revenue_growth(conn, period_a=inp["period_a"], period_b=inp["period_b"])
        if name == "get_salary_account_map":
            return pt.get_salary_account_map(conn)
        if name == "compare_to_budget":
            return pt.compare_to_budget(conn, department_id=inp["department_id"], account_id=inp["account_id"], period=inp["period"])
        return {"error": f"unknown tool {name}"}

    return tools, execute


def _controller_tools():
    tools = [
        {"name": "check_trial_balance", "description": "Debits vs. credits for a period.",
         "input_schema": {"type": "object", "properties": {"period": {"type": "string"}}, "required": ["period"]}},
        {"name": "check_subledger_tie_out", "description": "Does AR/AP in the GL match the subledgers?",
         "input_schema": {"type": "object", "properties": {"period": {"type": "string"}}, "required": ["period"]}},
        {"name": "review_exceptions", "description": "Pending Bookkeeping escalations plus a live duplicate-invoice scan.",
         "input_schema": {"type": "object", "properties": {"period": {"type": "string"}}, "required": ["period"]}},
    ]

    def execute(conn, name, inp):
        if name == "check_trial_balance":
            return ct.check_trial_balance(conn, period=inp["period"])
        if name == "check_subledger_tie_out":
            return ct.check_subledger_tie_out(conn, period=inp["period"])
        if name == "review_exceptions":
            return ct.review_exceptions(conn, period=inp["period"])
        return {"error": f"unknown tool {name}"}

    return tools, execute


def _fpa_tools():
    tools = [
        {"name": "get_income_statement", "description": "Full income statement for a period.",
         "input_schema": {"type": "object", "properties": {"period": {"type": "string"}}, "required": ["period"]}},
        {"name": "get_revenue_history", "description": "Monthly revenue for every period in a range.",
         "input_schema": {"type": "object", "properties": {"period_start": {"type": "string"}, "period_end": {"type": "string"}}, "required": ["period_start", "period_end"]}},
        {"name": "get_gross_margin_trend", "description": "Gross margin % for every period in a range.",
         "input_schema": {"type": "object", "properties": {"period_start": {"type": "string"}, "period_end": {"type": "string"}}, "required": ["period_start", "period_end"]}},
        {"name": "get_all_budget_variances", "description": "Every budgeted line for a period, with variance.",
         "input_schema": {"type": "object", "properties": {"period": {"type": "string"}}, "required": ["period"]}},
        {"name": "get_budget_vs_actual", "description": "Single department/account/period budget-vs-actual drill-down.",
         "input_schema": {"type": "object", "properties": {"department_id": {"type": "string"}, "account_id": {"type": "string"}, "period": {"type": "string"}}, "required": ["department_id", "account_id", "period"]}},
        {"name": "run_forecast_model", "description": "Linear projection over historical monthly values.",
         "input_schema": {"type": "object", "properties": {"historical_values": {"type": "array", "items": {"type": "number"}}, "months_ahead": {"type": "integer"}, "assumed_monthly_growth_rate": {"type": ["number", "null"]}}, "required": ["historical_values", "months_ahead"]}},
        {"name": "get_latest_forecast_assumption", "description": "The currently active forecast assumption, if any.",
         "input_schema": {"type": "object", "properties": {}, "required": []}},
    ]

    def execute(conn, name, inp):
        if name == "get_income_statement":
            return fpat.get_income_statement(conn, period=inp["period"])
        if name == "get_revenue_history":
            return {"history": fpat.get_revenue_history(conn, period_start=inp["period_start"], period_end=inp["period_end"])}
        if name == "get_gross_margin_trend":
            return {"trend": fpat.get_gross_margin_trend(conn, period_start=inp["period_start"], period_end=inp["period_end"])}
        if name == "get_all_budget_variances":
            return {"variances": fpat.get_all_budget_variances(conn, period=inp["period"])}
        if name == "get_budget_vs_actual":
            return fpat.get_budget_vs_actual(conn, department_id=inp["department_id"], account_id=inp["account_id"], period=inp["period"])
        if name == "run_forecast_model":
            return fpat.run_forecast_model(conn, historical_values=inp["historical_values"], months_ahead=inp["months_ahead"],
                                             assumed_monthly_growth_rate=inp.get("assumed_monthly_growth_rate"))
        if name == "get_latest_forecast_assumption":
            return {"assumption": fpat.get_latest_forecast_assumption(conn)}
        return {"error": f"unknown tool {name}"}

    return tools, execute


REGISTRY = {
    "bookkeeping": {"label": "Bookkeeping Agent", "model": "claude-haiku-4-5-20251001", "build": _bookkeeping_tools},
    "ap": {"label": "AP Agent", "model": "claude-haiku-4-5-20251001", "build": _ap_tools},
    "ar": {"label": "AR Agent", "model": "claude-haiku-4-5-20251001", "build": _ar_tools},
    "payroll": {"label": "Payroll & Workforce Agent", "model": "claude-haiku-4-5-20251001", "build": _payroll_tools},
    "controller": {"label": "Controller Agent", "model": "claude-sonnet-5", "build": _controller_tools},
    "fpa": {"label": "FP&A Agent", "model": "claude-sonnet-5", "build": _fpa_tools},
}


def ask_specialist(conn, agent_name: str, question: str) -> dict:
    """Runs a short, read-only, single-question live loop against one
    specialist and returns its answer. This is the actual delegation — a
    real nested API call, not a lookup against something the specialist
    said earlier."""
    if agent_name not in REGISTRY:
        return {"status": "error", "reason": f"unknown agent '{agent_name}' — choose one of {sorted(REGISTRY)}"}

    spec = REGISTRY[agent_name]
    tools, execute = spec["build"]()
    tools_with_submit = tools + [SUBMIT_ANSWER_TOOL]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set in environment")
    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = (
        f"You are the {spec['label']} for AURI Finance, currently answering ONE specific "
        f"question from the CFO in advisory mode. You have read-only access to your normal "
        f"investigative tools — nothing you call here can post, categorize, resolve, escalate, "
        f"or otherwise change any record. Investigate using your tools as needed to answer "
        f"accurately, then call submit_answer(answer, evidence) exactly once, with a direct "
        f"answer and the specific figures it relies on. Be concise."
    )

    messages = [{"role": "user", "content": question}]
    tool_call_count = 0
    answer = None
    input_tokens = 0
    output_tokens = 0

    for turn in range(1, MAX_DELEGATE_TURNS + 1):
        response = client.messages.create(model=spec["model"], max_tokens=3072, system=system_prompt,
                                            tools=tools_with_submit, messages=messages)
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        tool_results = []
        answer_this_turn = None
        for block in response.content:
            if block.type == "tool_use":
                tool_call_count += 1
                if block.name == "submit_answer":
                    violation = schema_validate.first_schema_violation(block.input, SUBMIT_ANSWER_TOOL["input_schema"])
                    if violation:
                        result = {"status": "error", "reason": f"submit_answer rejected: {violation}"}
                    else:
                        answer_this_turn = block.input
                        result = {"status": "received"}
                else:
                    try:
                        result = execute(conn, block.name, block.input)
                    except Exception as exc:  # noqa: BLE001
                        result = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})

        messages.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})

        if answer_this_turn is not None:
            answer = answer_this_turn
            break
        if tool_results:
            # Always send tool_results for any tool_use block in this turn,
            # regardless of stop_reason. This is the actual root cause of
            # the "tool_use ids were found without tool_result blocks" API
            # error documented (but not root-caused) in M6: branching on
            # stop_reason == "tool_use" silently drops queued tool_results
            # whenever a turn both called a tool AND ended for some other
            # reason (max_tokens is the common case) — the very next turn
            # then sends an assistant message with an unanswered tool_use
            # id, which the API rejects outright. It hit FP&A delegation
            # specifically because FP&A's tool outputs (income statements,
            # budget-variance lists) are large enough to occasionally brush
            # the 3072-token cap. Fixed by keying off whether there ARE
            # tool_results, not off why the model stopped generating.
            messages.append({"role": "user", "content": tool_results})
        else:
            messages.append({"role": "user", "content": "Please call submit_answer with your answer."})

    if answer is None:
        return {"status": "no_answer", "reason": f"{spec['label']} did not submit an answer within {MAX_DELEGATE_TURNS} turns",
                "tool_calls": tool_call_count, "input_tokens": input_tokens, "output_tokens": output_tokens}

    return {"status": "answered", "agent": agent_name, "agent_label": spec["label"], "question": question,
            "answer": answer.get("answer"), "evidence": answer.get("evidence", []), "tool_calls": tool_call_count,
            "input_tokens": input_tokens, "output_tokens": output_tokens}
