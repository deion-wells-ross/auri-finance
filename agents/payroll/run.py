"""Live runner for the Payroll & Workforce Agent (M4).

Usage:
    export ANTHROPIC_API_KEY=$(cat /home/claude/.anthropic_key)
    cd /home/claude/auri-finance
    python3 agents/payroll/run.py [--db path/to/db.sqlite] [--period-a 2026-02] [--period-b 2026-08]
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

from tools import payroll_tools as pt  # noqa: E402
from tools import audit  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
MAX_TURNS = 40

SYSTEM_PROMPT = (ROOT / "agents" / "payroll" / "system_prompt.md").read_text()

TOOLS = [
    {
        "name": "get_payroll_summary",
        "description": "Payroll cost and headcount by department for a period.",
        "input_schema": {"type": "object", "properties": {"period": {"type": "string"}}, "required": ["period"]},
    },
    {
        "name": "get_headcount_by_dept",
        "description": "Headcount for every department as of a date, in one call.",
        "input_schema": {"type": "object", "properties": {"as_of_date": {"type": "string"}}, "required": ["as_of_date"]},
    },
    {
        "name": "compare_payroll_growth",
        "description": "Payroll cost growth for one department between two periods.",
        "input_schema": {
            "type": "object",
            "properties": {"department_id": {"type": "string"}, "period_a": {"type": "string"}, "period_b": {"type": "string"}},
            "required": ["department_id", "period_a", "period_b"],
        },
    },
    {
        "name": "get_revenue_growth",
        "description": "Company revenue growth between two periods — the comparison point for judging whether payroll growth is a problem.",
        "input_schema": {
            "type": "object",
            "properties": {"period_a": {"type": "string"}, "period_b": {"type": "string"}},
            "required": ["period_a", "period_b"],
        },
    },
    {
        "name": "get_salary_account_map",
        "description": "Maps each department_id to its real chart-of-accounts salary account_id (e.g. ENG -> 'sal_eng'). Call this before compare_to_budget — budgets are keyed by the real account_id, not a generic label.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "compare_to_budget",
        "description": "Budget vs. actual for a department/account/period.",
        "input_schema": {
            "type": "object",
            "properties": {"department_id": {"type": "string"}, "account_id": {"type": "string"}, "period": {"type": "string"}},
            "required": ["department_id", "account_id", "period"],
        },
    },
    {
        "name": "submit_report",
        "description": "Call exactly once, last. Ends your run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "payroll_outpacing_revenue": {"type": "boolean"},
                "findings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "payroll_outpacing_revenue", "findings"],
        },
    },
]


def execute_tool(conn, name: str, tool_input: dict) -> dict:
    try:
        if name == "get_payroll_summary":
            return pt.get_payroll_summary(conn, period=tool_input["period"])
        if name == "get_headcount_by_dept":
            return pt.get_headcount_by_dept(conn, as_of_date=tool_input["as_of_date"])
        if name == "get_salary_account_map":
            return pt.get_salary_account_map(conn)
        if name == "compare_payroll_growth":
            return pt.compare_payroll_growth(conn, department_id=tool_input["department_id"], period_a=tool_input["period_a"], period_b=tool_input["period_b"])
        if name == "get_revenue_growth":
            return pt.get_revenue_growth(conn, period_a=tool_input["period_a"], period_b=tool_input["period_b"])
        if name == "compare_to_budget":
            return pt.compare_to_budget(conn, department_id=tool_input["department_id"], account_id=tool_input["account_id"], period=tool_input["period"])
        if name == "submit_report":
            audit.log_report(conn, pt.AGENT_NAME, "submit_report", tool_input)
            return {"status": "report_received"}
        return {"error": f"unknown tool {name}"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}


def run(db_path: str, period_a: str, period_b: str) -> dict:
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
            f"Investigate labor-cost risk across departments, comparing {period_a} to {period_b}. "
            "Pay particular attention to Engineering, and check its growth against company revenue "
            "growth over the same window."
        ),
    }]

    final_report = None
    total_input_tokens = 0
    total_output_tokens = 0

    print(f"=== Payroll & Workforce Agent run starting (model={MODEL}, db={db_path}, {period_a}->{period_b}) ===\n")

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
                result = execute_tool(conn, block.name, block.input)
                print(f"[turn {turn}] tool_result: {json.dumps(result)[:500]}\n")
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})
                if block.name == "submit_report":
                    report_this_turn = block.input

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
    parser.add_argument("--period-a", default="2026-02")
    parser.add_argument("--period-b", default="2026-08")
    args = parser.parse_args()
    run(args.db, args.period_a, args.period_b)
