"""Live runner for the AR Agent (M4).

Usage:
    export ANTHROPIC_API_KEY=$(cat /home/claude/.anthropic_key)
    cd /home/claude/auri-finance
    python3 agents/ar/run.py [--db path/to/db.sqlite] [--as-of 2026-08-31]
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

from tools import ar_tools as art  # noqa: E402
from tools import audit  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
MAX_TURNS = 40

SYSTEM_PROMPT = (ROOT / "agents" / "ar" / "system_prompt.md").read_text()

TOOLS = [
    {
        "name": "get_ar_aging",
        "description": "AR aging by customer as of a date: buckets, totals, and each customer's concentration_pct of total open AR.",
        "input_schema": {"type": "object", "properties": {"as_of_date": {"type": "string"}}, "required": ["as_of_date"]},
    },
    {
        "name": "get_customer_payment_history",
        "description": "All invoices for a customer, with an overdue rate — approximates payment reliability since there's no stored paid_date.",
        "input_schema": {"type": "object", "properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]},
    },
    {
        "name": "flag_concentration_risk",
        "description": "Customers whose share of total open AR exceeds threshold_pct (default 25%).",
        "input_schema": {
            "type": "object",
            "properties": {"as_of_date": {"type": "string"}, "threshold_pct": {"type": "number"}},
            "required": ["as_of_date"],
        },
    },
    {
        "name": "prioritize_collections",
        "description": "Ranked list of overdue invoices by priority_score = days_overdue x amount.",
        "input_schema": {"type": "object", "properties": {"as_of_date": {"type": "string"}}, "required": ["as_of_date"]},
    },
    {
        "name": "submit_report",
        "description": "Call exactly once, last. Ends your run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "concentration_risk_flagged": {"type": "boolean"},
                "top_priority_accounts": {"type": "array", "items": {"type": "string"}},
                "findings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "concentration_risk_flagged", "top_priority_accounts", "findings"],
        },
    },
]


def execute_tool(conn, name: str, tool_input: dict) -> dict:
    try:
        if name == "get_ar_aging":
            return art.get_ar_aging(conn, as_of_date=tool_input["as_of_date"])
        if name == "get_customer_payment_history":
            return art.get_customer_payment_history(conn, customer_id=tool_input["customer_id"])
        if name == "flag_concentration_risk":
            return {"flagged": art.flag_concentration_risk(conn, as_of_date=tool_input["as_of_date"], threshold_pct=tool_input.get("threshold_pct", 25.0))}
        if name == "prioritize_collections":
            return {"ranked": art.prioritize_collections(conn, as_of_date=tool_input["as_of_date"])}
        if name == "submit_report":
            audit.log_report(conn, art.AGENT_NAME, "submit_report", tool_input)
            return {"status": "report_received"}
        return {"error": f"unknown tool {name}"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}


def run(db_path: str, as_of_date: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set in environment")

    client = anthropic.Anthropic(api_key=api_key)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    messages = [{
        "role": "user",
        "content": f"Review accounts receivable as of {as_of_date}. Identify concentration risk and prioritize collections.",
    }]

    final_report = None
    total_input_tokens = 0
    total_output_tokens = 0

    print(f"=== AR Agent run starting (model={MODEL}, db={db_path}, as_of={as_of_date}) ===\n")

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
            # Any tool_use block MUST get a tool_result in the very next
            # message, regardless of stop_reason (e.g. max_tokens can end a
            # turn that still contains a completed tool_use block) — a bug
            # discovered live in M7, root-caused after it broke a real CFO
            # delegation call four times in a row. Branching on stop_reason
            # here instead of on "were there tool_use blocks" is what caused
            # 'tool_use ids were found without tool_result blocks' errors.
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
    parser.add_argument("--as-of", default="2026-08-31")
    args = parser.parse_args()
    run(args.db, args.as_of)
