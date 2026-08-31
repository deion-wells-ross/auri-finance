"""Live runner for the Bookkeeping Agent (M2).

Real Claude API calls, real tool execution against the seeded SQLite
database, real writes to gl_transactions / approvals / audit_log. No mocked
responses anywhere in this file — per the project's standing rule, the
agent must actually work, not merely produce plausible-looking code.

Usage:
    export ANTHROPIC_API_KEY=$(cat /home/claude/.anthropic_key)
    cd /home/claude/auri-finance
    python3 agents/bookkeeping/run.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.db import get_connection  # noqa: E402
from tools import bookkeeping_tools as bk  # noqa: E402
from tools import audit  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
MAX_TURNS = 40

SYSTEM_PROMPT = (ROOT / "agents" / "bookkeeping" / "system_prompt.md").read_text()

TOOLS = [
    {
        "name": "get_uncategorized_txns",
        "description": "List every bank_feed row not yet posted to the GL, with description, amount, and the bank's own (unverified) suggested account and confidence.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_chart_of_accounts",
        "description": "List the accounts you may post against, with type and normal balance.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "post_categorization",
        "description": "Post a balanced journal entry (debit the chosen account, credit Cash) for one bank_feed transaction and mark it categorized. Refuses if confidence is below 0.75.",
        "input_schema": {
            "type": "object",
            "properties": {
                "feed_id": {"type": "string"},
                "account_id": {"type": "string"},
                "confidence": {"type": "number", "description": "Your own assessed confidence, 0.0-1.0, that this account is correct."},
                "memo": {"type": "string"},
                "department_id": {"type": ["string", "null"], "description": "ENG, SALES, MKT, CS, GA, or null if this is a company-wide cost."},
            },
            "required": ["feed_id", "account_id", "confidence", "memo"],
        },
    },
    {
        "name": "escalate_for_review",
        "description": "File a bank_feed transaction for human review instead of posting it, because you cannot confidently determine the correct account.",
        "input_schema": {
            "type": "object",
            "properties": {
                "feed_id": {"type": "string"},
                "reason": {"type": "string", "description": "Why this transaction can't be confidently categorized."},
            },
            "required": ["feed_id", "reason"],
        },
    },
    {
        "name": "submit_report",
        "description": "Call exactly once, last, after every uncategorized transaction has been posted or escalated. Ends your run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "posted_count": {"type": "integer"},
                "escalated_count": {"type": "integer"},
                "escalated_feed_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "posted_count", "escalated_count", "escalated_feed_ids"],
        },
    },
]


def execute_tool(conn, name: str, tool_input: dict) -> dict:
    try:
        if name == "get_uncategorized_txns":
            return {"transactions": bk.get_uncategorized_txns(conn)}
        if name == "get_chart_of_accounts":
            return {"accounts": bk.get_chart_of_accounts(conn)}
        if name == "post_categorization":
            missing = [k for k in ("feed_id", "account_id", "confidence") if k not in tool_input]
            if missing:
                return {"status": "error", "reason": f"missing required field(s): {missing} — retry the call with all fields present"}
            return bk.post_categorization(
                conn,
                feed_id=tool_input["feed_id"],
                account_id=tool_input["account_id"],
                confidence=tool_input["confidence"],
                memo=tool_input.get("memo", ""),
                department_id=tool_input.get("department_id"),
            )
        if name == "escalate_for_review":
            missing = [k for k in ("feed_id", "reason") if k not in tool_input]
            if missing:
                return {"status": "error", "reason": f"missing required field(s): {missing} — retry the call with all fields present"}
            return bk.escalate_for_review(conn, feed_id=tool_input["feed_id"], reason=tool_input["reason"])
        if name == "submit_report":
            audit.log_report(conn, bk.AGENT_NAME, "submit_report", tool_input)
            return {"status": "report_received"}
        return {"error": f"unknown tool {name}"}
    except Exception as exc:  # noqa: BLE001 - surface as a tool_result, never crash the run
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}


def run(db_path: str | None = None, period: str = "2026-08") -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set in environment")

    client = anthropic.Anthropic(api_key=api_key)
    conn = get_connection(db_path) if db_path else get_connection()

    messages = [
        {
            "role": "user",
            "content": (
                f"Begin your work for the {period} closing period. Start by "
                "checking the chart of accounts and the uncategorized transaction "
                "list, then work through every transaction."
            ),
        }
    ]

    final_report = None
    total_input_tokens = 0
    total_output_tokens = 0

    print(f"=== Bookkeeping Agent run starting (model={MODEL}) ===\n")

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

        assistant_content = []
        tool_results = []
        report_this_turn = None

        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"[turn {turn}] agent: {block.text.strip()}\n")
            if block.type == "tool_use":
                assistant_content.append(block)
                print(f"[turn {turn}] tool_call: {block.name}({json.dumps(block.input)})")
                result = execute_tool(conn, block.name, block.input)
                print(f"[turn {turn}] tool_result: {json.dumps(result)[:400]}\n")
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    }
                )
                if block.name == "submit_report":
                    report_this_turn = block.input
            elif block.type == "text":
                assistant_content.append(block)

        messages.append({"role": "assistant", "content": [b if isinstance(b, dict) else b.model_dump() for b in response.content]})

        if report_this_turn is not None:
            final_report = report_this_turn
            break

        if tool_results:
            # Always send tool_results for any tool_use block in this turn,
            # regardless of stop_reason — see agents/ar/run.py's comment on
            # this fix (M7) for the live failure that surfaced the bug.
            messages.append({"role": "user", "content": tool_results})
        else:
            # Agent stopped talking without calling submit_report — nudge it.
            messages.append({
                "role": "user",
                "content": "Please continue until every uncategorized transaction is posted or escalated, then call submit_report.",
            })

    conn.close()

    print("=== Run complete ===")
    print(f"Total tokens: {total_input_tokens} in / {total_output_tokens} out")
    if final_report:
        print("\nFinal report from agent:")
        print(json.dumps(final_report, indent=2))
    else:
        print("\nWARNING: agent never called submit_report within MAX_TURNS.")

    return {
        "final_report": final_report,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
    }


if __name__ == "__main__":
    run()
