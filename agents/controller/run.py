"""Live runner for the Controller Agent (M3).

Real Claude API calls, real tool execution against a real SQLite database.
Supports pointing at an alternate database file so the same live agent can
be run both against the real Meridian ledger and against a deliberately
broken test fixture, per the M3 milestone bar: "catches at least one
deliberately broken trial balance in a test fixture; correctly escalates."

Usage:
    export ANTHROPIC_API_KEY=$(cat /home/claude/.anthropic_key)
    cd /home/claude/auri-finance
    python3 agents/controller/run.py [--db path/to/db.sqlite] [--period 2026-08]
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

from tools import controller_tools as ct  # noqa: E402
from tools import audit  # noqa: E402

MODEL = "claude-sonnet-5"
MAX_TURNS = 40

SYSTEM_PROMPT = (ROOT / "agents" / "controller" / "system_prompt.md").read_text()

TOOLS = [
    {
        "name": "check_trial_balance",
        "description": "Debits vs. credits for the period, by account. The hard gate — nothing else matters if this doesn't balance.",
        "input_schema": {
            "type": "object",
            "properties": {"period": {"type": "string", "description": "e.g. '2026-08'"}},
            "required": ["period"],
        },
    },
    {
        "name": "check_subledger_tie_out",
        "description": "Does the AR/AP GL control-account balance match the sum of open items in the AR/AP subledgers as of this period's end?",
        "input_schema": {
            "type": "object",
            "properties": {"period": {"type": "string"}},
            "required": ["period"],
        },
    },
    {
        "name": "review_exceptions",
        "description": "Pending Bookkeeping escalations for this period, plus a fresh run of the rule-based duplicate-invoice detector.",
        "input_schema": {
            "type": "object",
            "properties": {"period": {"type": "string"}},
            "required": ["period"],
        },
    },
    {
        "name": "raise_correction_request",
        "description": "File a correction request against another agent's data. This is the ONLY way to act on a finding that isn't yours to fix.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string"},
                "target_agent": {"type": "string", "description": "e.g. 'ap_agent', 'bookkeeping_agent'"},
                "finding": {"type": "string"},
                "evidence": {"type": "string"},
                "requested_action": {"type": "string"},
                "materiality_amount": {"type": "number"},
            },
            "required": ["period", "target_agent", "finding", "evidence", "requested_action", "materiality_amount"],
        },
    },
    {
        "name": "mark_period_ready_for_reporting",
        "description": "Attempt to mark the period ready for reporting. Code-enforced: refuses if the trial balance doesn't balance or unresolved exposure exceeds materiality.",
        "input_schema": {
            "type": "object",
            "properties": {"period": {"type": "string"}},
            "required": ["period"],
        },
    },
    {
        "name": "submit_report",
        "description": "Call exactly once, last, after you've completed your review of the period. Ends your run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "trial_balance_ok": {"type": "boolean"},
                "period_marked_ready": {"type": "boolean"},
                "correction_requests_raised": {"type": "integer"},
                "findings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "trial_balance_ok", "period_marked_ready", "correction_requests_raised", "findings"],
        },
    },
]


def execute_tool(conn, name: str, tool_input: dict) -> dict:
    try:
        if name == "check_trial_balance":
            return ct.check_trial_balance(conn, period=tool_input["period"])
        if name == "check_subledger_tie_out":
            return ct.check_subledger_tie_out(conn, period=tool_input["period"])
        if name == "review_exceptions":
            return ct.review_exceptions(conn, period=tool_input["period"])
        if name == "raise_correction_request":
            required = ["period", "target_agent", "finding", "evidence", "requested_action", "materiality_amount"]
            missing = [k for k in required if k not in tool_input]
            if missing:
                return {"status": "error", "reason": f"missing required field(s): {missing} — retry with all fields present"}
            return ct.raise_correction_request(
                conn,
                period=tool_input["period"], target_agent=tool_input["target_agent"],
                finding=tool_input["finding"], evidence=tool_input["evidence"],
                requested_action=tool_input["requested_action"],
                materiality_amount=tool_input["materiality_amount"],
            )
        if name == "mark_period_ready_for_reporting":
            return ct.mark_period_ready_for_reporting(conn, period=tool_input["period"])
        if name == "submit_report":
            audit.log_report(conn, ct.AGENT_NAME, "submit_report", tool_input)
            return {"status": "report_received"}
        return {"error": f"unknown tool {name}"}
    except Exception as exc:  # noqa: BLE001 - surface as a tool_result, never crash the run
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}


def run(db_path: str, period: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set in environment")

    client = anthropic.Anthropic(api_key=api_key)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    messages = [
        {
            "role": "user",
            "content": (
                f"Validate the {period} closing period. Work through your checks in order "
                "and decide, with evidence, whether this period is ready to report on."
            ),
        }
    ]

    final_report = None
    total_input_tokens = 0
    total_output_tokens = 0

    print(f"=== Controller Agent run starting (model={MODEL}, db={db_path}, period={period}) ===\n")

    for turn in range(1, MAX_TURNS + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

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
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)}
                )
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
            messages.append({
                "role": "user",
                "content": "Please continue your review, then call submit_report when done.",
            })

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
