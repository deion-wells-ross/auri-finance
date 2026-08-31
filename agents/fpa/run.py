"""Live runner for the FP&A Agent (M6).

Usage:
    export ANTHROPIC_API_KEY=$(cat /home/claude/.anthropic_key)
    cd /home/claude/auri-finance
    python3 agents/fpa/run.py [--db path/to/db.sqlite] [--period 2026-08]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import fpa_tools as fpat  # noqa: E402
from tools import schema_validate  # noqa: E402
from tools import audit  # noqa: E402

MODEL = "claude-sonnet-5"
MAX_TURNS = 40

SYSTEM_PROMPT = (ROOT / "agents" / "fpa" / "system_prompt.md").read_text()

TOOLS = [
    {
        "name": "get_income_statement",
        "description": "Full income statement for one period.",
        "input_schema": {"type": "object", "properties": {"period": {"type": "string"}}, "required": ["period"]},
    },
    {
        "name": "get_revenue_history",
        "description": "Monthly revenue for every period in [period_start, period_end].",
        "input_schema": {
            "type": "object",
            "properties": {"period_start": {"type": "string"}, "period_end": {"type": "string"}},
            "required": ["period_start", "period_end"],
        },
    },
    {
        "name": "get_gross_margin_trend",
        "description": "Gross margin % for every period in range.",
        "input_schema": {
            "type": "object",
            "properties": {"period_start": {"type": "string"}, "period_end": {"type": "string"}},
            "required": ["period_start", "period_end"],
        },
    },
    {
        "name": "get_all_budget_variances",
        "description": "Every budgeted department/account line for a period, with variance, sorted by |variance %| descending.",
        "input_schema": {"type": "object", "properties": {"period": {"type": "string"}}, "required": ["period"]},
    },
    {
        "name": "get_budget_vs_actual",
        "description": "Single department/account/period budget-vs-actual drill-down.",
        "input_schema": {
            "type": "object",
            "properties": {"department_id": {"type": "string"}, "account_id": {"type": "string"}, "period": {"type": "string"}},
            "required": ["department_id", "account_id", "period"],
        },
    },
    {
        "name": "run_forecast_model",
        "description": "Linear projection over historical monthly values. Omit assumed_monthly_growth_rate to infer it from the history; the result always states which happened.",
        "input_schema": {
            "type": "object",
            "properties": {
                "historical_values": {"type": "array", "items": {"type": "number"}},
                "months_ahead": {"type": "integer"},
                "assumed_monthly_growth_rate": {"type": ["number", "null"]},
            },
            "required": ["historical_values", "months_ahead"],
        },
    },
    {
        "name": "get_latest_forecast_assumption",
        "description": "The currently active forecast assumption, if any has ever been set.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_forecast_assumptions",
        "description": "Record a forecast assumption with its evidentiary basis. Refuses an empty basis. Refuses to write directly (files a pending approval instead) if the new rate is more than 1.0 percentage point from the currently active one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string"},
                "monthly_growth_rate_pct": {"type": "number"},
                "basis": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["period", "monthly_growth_rate_pct", "basis"],
        },
    },
    {
        "name": "submit_report",
        "description": "Call exactly once, last. Ends your run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "gross_margin_trend_explained": {"type": "boolean"},
                "material_variances": {"type": "array", "items": {"type": "string"}},
                "forecast_status": {"type": "string", "description": "'set', 'review_required', or 'not_attempted'"},
                "findings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "gross_margin_trend_explained", "material_variances", "forecast_status", "findings"],
        },
    },
]

SUBMIT_REPORT_SCHEMA = next(t for t in TOOLS if t["name"] == "submit_report")["input_schema"]


def execute_tool(conn, name: str, tool_input: dict) -> dict:
    try:
        if name == "get_income_statement":
            return fpat.get_income_statement(conn, period=tool_input["period"])
        if name == "get_revenue_history":
            return {"history": fpat.get_revenue_history(conn, period_start=tool_input["period_start"], period_end=tool_input["period_end"])}
        if name == "get_gross_margin_trend":
            return {"trend": fpat.get_gross_margin_trend(conn, period_start=tool_input["period_start"], period_end=tool_input["period_end"])}
        if name == "get_all_budget_variances":
            return {"variances": fpat.get_all_budget_variances(conn, period=tool_input["period"])}
        if name == "get_budget_vs_actual":
            return fpat.get_budget_vs_actual(conn, department_id=tool_input["department_id"],
                                               account_id=tool_input["account_id"], period=tool_input["period"])
        if name == "run_forecast_model":
            return fpat.run_forecast_model(conn, historical_values=tool_input["historical_values"],
                                             months_ahead=tool_input["months_ahead"],
                                             assumed_monthly_growth_rate=tool_input.get("assumed_monthly_growth_rate"))
        if name == "get_latest_forecast_assumption":
            return {"assumption": fpat.get_latest_forecast_assumption(conn)}
        if name == "set_forecast_assumptions":
            return fpat.set_forecast_assumptions(conn, period=tool_input["period"],
                                                   monthly_growth_rate_pct=tool_input["monthly_growth_rate_pct"],
                                                   basis=tool_input["basis"], notes=tool_input.get("notes", ""))
        if name == "submit_report":
            return {"status": "report_received"}
        return {"error": f"unknown tool {name}"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}


def run(db_path: str, period: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set in environment")

    client = anthropic.Anthropic(api_key=api_key)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    messages = [{
        "role": "user",
        "content": (
            f"Review {period} for FP&A: explain the gross margin trend, scan for material budget "
            "variances and explain the drivers behind the worst ones, then build and record a "
            "revenue forecast with a stated, evidence-backed growth assumption."
        ),
    }]

    final_report = None
    total_input_tokens = 0
    total_output_tokens = 0

    print(f"=== FP&A Agent run starting (model={MODEL}, db={db_path}, period={period}) ===\n")

    for turn in range(1, MAX_TURNS + 1):
        response = client.messages.create(model=MODEL, max_tokens=4096, system=SYSTEM_PROMPT, tools=TOOLS, messages=messages)
        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        tool_results = []
        report_this_turn = None
        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"[turn {turn}] agent: {block.text.strip()}\n")
            if block.type == "tool_use":
                print(f"[turn {turn}] tool_call: {block.name}({json.dumps(block.input)})")
                if block.name == "submit_report":
                    violation = schema_validate.first_schema_violation(block.input, SUBMIT_REPORT_SCHEMA)
                    if violation:
                        result = {"status": "error", "reason": f"submit_report rejected: {violation}"}
                    else:
                        result = execute_tool(conn, block.name, block.input)
                        report_this_turn = block.input
                        audit.log_report(conn, fpat.AGENT_NAME, "submit_report", block.input)
                else:
                    result = execute_tool(conn, block.name, block.input)
                print(f"[turn {turn}] tool_result: {json.dumps(result)[:500]}\n")
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})

        messages.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})

        if report_this_turn is not None:
            final_report = report_this_turn
            break
        if tool_results:
            # Always send tool_results for any tool_use block in this turn,
            # regardless of stop_reason — see agents/ar/run.py's comment on
            # this fix (M7) for the live failure that surfaced the bug.
            messages.append({"role": "user", "content": tool_results})
        else:
            messages.append({"role": "user", "content": "Please continue, then call submit_report when done."})

    conn.close()
    print("=== Run complete ===")
    print(f"Total tokens: {total_input_tokens} in / {total_output_tokens} out")
    if final_report:
        print("\nFinal report from agent:")
        print(json.dumps(final_report, indent=2))
    else:
        print("\nWARNING: agent never called submit_report within MAX_TURNS.")
    return {"final_report": final_report, "input_tokens": total_input_tokens, "output_tokens": total_output_tokens}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "db" / "meridian.db"))
    parser.add_argument("--period", default="2026-08")
    args = parser.parse_args()
    run(args.db, args.period)
