"""Live runner for the CFO Agent (M6).

Usage:
    export ANTHROPIC_API_KEY=$(cat /home/claude/.anthropic_key)
    cd /home/claude/auri-finance
    python3 agents/cfo/run.py [--db path/to/db.sqlite] [--period 2026-08]
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

from tools import cfo_tools as cfot  # noqa: E402
from tools import schema_validate  # noqa: E402

MODEL = "claude-sonnet-5"
MAX_TURNS = 40

SYSTEM_PROMPT = (ROOT / "agents" / "cfo" / "system_prompt.md").read_text()

TOOLS = [
    {
        "name": "get_statements",
        "description": "income_statement / balance_sheet / cash_flow for a period.",
        "input_schema": {
            "type": "object",
            "properties": {"statement_type": {"type": "string"}, "period": {"type": "string"}},
            "required": ["statement_type", "period"],
        },
    },
    {
        "name": "get_metrics",
        "description": "Gross margin, payroll % of revenue, burn rate, and cash runway, bundled.",
        "input_schema": {"type": "object", "properties": {"period": {"type": "string"}}, "required": ["period"]},
    },
    {
        "name": "delegate_to_agent",
        "description": "Ask one specialist (bookkeeping/ap/ar/payroll/controller/fpa) a specific question. Live, read-only, no side effects.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {"type": "string", "enum": ["bookkeeping", "ap", "ar", "payroll", "controller", "fpa"]},
                "question": {"type": "string"},
            },
            "required": ["agent_name", "question"],
        },
    },
    {
        "name": "submit_briefing",
        "description": "Call exactly once, last. Ends your run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "facts": {"type": "array", "items": {"type": "string"}},
                "hypotheses": {"type": "array", "items": {"type": "string"}},
                "recommendations": {"type": "array", "items": {"type": "string"}},
                "open_questions": {"type": "array", "items": {"type": "string"}},
                "agents_delegated_to": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "facts", "hypotheses", "recommendations", "open_questions", "agents_delegated_to"],
        },
    },
]

SUBMIT_BRIEFING_SCHEMA = next(t for t in TOOLS if t["name"] == "submit_briefing")["input_schema"]


def execute_tool(conn, name: str, tool_input: dict) -> dict:
    try:
        if name == "get_statements":
            return cfot.get_statements(conn, statement_type=tool_input["statement_type"], period=tool_input["period"])
        if name == "get_metrics":
            return cfot.get_metrics(conn, period=tool_input["period"])
        if name == "delegate_to_agent":
            return cfot.delegate_to_agent(conn, agent_name=tool_input["agent_name"], question=tool_input["question"])
        if name == "submit_briefing":
            # M8 finding: this used to return a bare receipt without ever
            # calling cfo_tools.submit_briefing — which already existed and
            # already logs to audit_log, but was simply never invoked. The
            # CFO's actual final briefing was never persisted anywhere but
            # stdout and the in-memory final_report dict. Now it really is
            # the terminal write it was always meant to be.
            return cfot.submit_briefing(
                conn, summary=tool_input["summary"], facts=tool_input["facts"],
                hypotheses=tool_input["hypotheses"], recommendations=tool_input["recommendations"],
                open_questions=tool_input["open_questions"], agents_delegated_to=tool_input["agents_delegated_to"],
            )
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
            f"Build the CFO briefing for {period}. Pull the statements and metrics first, then "
            "delegate specific questions to at least two specialists before you write the briefing."
        ),
    }]

    final_report = None
    total_input_tokens = 0
    total_output_tokens = 0
    delegated_input_tokens = 0
    delegated_output_tokens = 0

    print(f"=== CFO Agent run starting (model={MODEL}, db={db_path}, period={period}) ===\n")

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
                print(f"[turn {turn}] tool_call: {block.name}({json.dumps(block.input)[:300]})")
                if block.name == "submit_briefing":
                    violation = schema_validate.first_schema_violation(block.input, SUBMIT_BRIEFING_SCHEMA)
                    if violation:
                        result = {"status": "error", "reason": f"submit_briefing rejected: {violation}"}
                    else:
                        result = execute_tool(conn, block.name, block.input)
                        report_this_turn = block.input
                else:
                    result = execute_tool(conn, block.name, block.input)
                if block.name == "delegate_to_agent" and isinstance(result, dict):
                    delegated_input_tokens += result.get("input_tokens", 0)
                    delegated_output_tokens += result.get("output_tokens", 0)
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
            messages.append({"role": "user", "content": "Please continue, then call submit_briefing when done."})

    conn.close()
    print("=== Run complete ===")
    print(f"CFO's own tokens: {total_input_tokens} in / {total_output_tokens} out")
    print(f"Delegated specialist tokens (all delegate_to_agent calls combined): "
          f"{delegated_input_tokens} in / {delegated_output_tokens} out")
    print(f"Grand total: {total_input_tokens + delegated_input_tokens} in / "
          f"{total_output_tokens + delegated_output_tokens} out")
    if final_report:
        print("\nFinal briefing from agent:")
        print(json.dumps(final_report, indent=2))
    else:
        print("\nWARNING: agent never called submit_briefing within MAX_TURNS.")
    return {"final_report": final_report, "input_tokens": total_input_tokens, "output_tokens": total_output_tokens,
            "delegated_input_tokens": delegated_input_tokens, "delegated_output_tokens": delegated_output_tokens}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "db" / "meridian.db"))
    parser.add_argument("--period", default="2026-08")
    args = parser.parse_args()
    run(args.db, args.period)
