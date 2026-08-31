"""Live runner for the AP Agent (M4).

Usage:
    export ANTHROPIC_API_KEY=$(cat /home/claude/.anthropic_key)
    cd /home/claude/auri-finance
    python3 agents/ap/run.py [--db path/to/db.sqlite] [--period 2026-08] [--as-of 2026-08-31]
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

from tools import ap_tools as apt  # noqa: E402
from tools import audit  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
MAX_TURNS = 40

SYSTEM_PROMPT = (ROOT / "agents" / "ap" / "system_prompt.md").read_text()

TOOLS = [
    {
        "name": "get_ap_aging",
        "description": "AP aging by vendor as of a date: buckets, totals.",
        "input_schema": {"type": "object", "properties": {"as_of_date": {"type": "string"}}, "required": ["as_of_date"]},
    },
    {
        "name": "get_vendor_history",
        "description": "All invoices for a vendor, plus trailing average amount (excluding before_period and later).",
        "input_schema": {
            "type": "object",
            "properties": {"vendor_id": {"type": "string"}, "before_period": {"type": ["string", "null"]}},
            "required": ["vendor_id"],
        },
    },
    {
        "name": "flag_unusual_charges",
        "description": "Per-invoice comparison against vendor trailing average for a period; flags >multiple-x charges or brand-new vendors.",
        "input_schema": {
            "type": "object",
            "properties": {"period": {"type": "string"}, "multiple": {"type": "number"}},
            "required": ["period"],
        },
    },
    {
        "name": "flag_duplicate_invoices",
        "description": "Rule-based duplicate-invoice scan: same vendor, amount within tolerance, dates within a window.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "review_correction_requests",
        "description": "Open/disputed correction_requests addressed to ap_agent.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "resolve_duplicate_invoice",
        "description": "Confirm a duplicate: marks it disputed, posts the reversing GL entry, optionally closes a correction request. Refuses in code if the duplicate has already been paid — that needs vendor refund/collections follow-up, not a journal entry; use dismiss_duplicate_candidate or a correction_request instead in that case.",
        "input_schema": {
            "type": "object",
            "properties": {
                "duplicate_invoice_id": {"type": "string"},
                "original_invoice_id": {"type": "string"},
                "correction_request_id": {"type": ["string", "null"]},
                "response": {"type": "string"},
            },
            "required": ["duplicate_invoice_id", "original_invoice_id", "response"],
        },
    },
    {
        "name": "dismiss_duplicate_candidate",
        "description": "Record that you reviewed a flagged candidate pair and judged it NOT a duplicate (e.g. two genuinely separate recurring monthly charges). Prevents flag_duplicate_invoices from re-surfacing the same pair on a future run. Use this instead of just staying silent whenever you decide a candidate is a false positive.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vendor_id": {"type": "string"},
                "invoice_a_id": {"type": "string"},
                "invoice_b_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["vendor_id", "invoice_a_id", "invoice_b_id", "reason"],
        },
    },
    {
        "name": "recommend_payment_batch",
        "description": "File a payment-batch recommendation for human approval. Does not move money.",
        "input_schema": {
            "type": "object",
            "properties": {
                "as_of_date": {"type": "string"},
                "invoice_ids": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
            },
            "required": ["as_of_date", "invoice_ids", "notes"],
        },
    },
    {
        "name": "submit_report",
        "description": "Call exactly once, last. Ends your run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "duplicate_resolved": {"type": "boolean"},
                "correction_requests_resolved": {"type": "integer"},
                "unusual_charges_flagged": {"type": "integer"},
                "findings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "duplicate_resolved", "correction_requests_resolved", "unusual_charges_flagged", "findings"],
        },
    },
]


def execute_tool(conn, name: str, tool_input: dict) -> dict:
    try:
        if name == "get_ap_aging":
            return apt.get_ap_aging(conn, as_of_date=tool_input["as_of_date"])
        if name == "get_vendor_history":
            return apt.get_vendor_history(conn, vendor_id=tool_input["vendor_id"], before_period=tool_input.get("before_period"))
        if name == "flag_unusual_charges":
            return {"flags": apt.flag_unusual_charges(conn, period=tool_input["period"], multiple=tool_input.get("multiple", 2.0))}
        if name == "flag_duplicate_invoices":
            return {"candidates": apt.flag_duplicate_invoices(conn)}
        if name == "review_correction_requests":
            return {"requests": apt.review_correction_requests(conn)}
        if name == "resolve_duplicate_invoice":
            return apt.resolve_duplicate_invoice(
                conn, duplicate_invoice_id=tool_input["duplicate_invoice_id"],
                original_invoice_id=tool_input["original_invoice_id"],
                correction_request_id=tool_input.get("correction_request_id"),
                response=tool_input["response"],
            )
        if name == "dismiss_duplicate_candidate":
            return apt.dismiss_duplicate_candidate(
                conn, vendor_id=tool_input["vendor_id"], invoice_a_id=tool_input["invoice_a_id"],
                invoice_b_id=tool_input["invoice_b_id"], reason=tool_input["reason"],
            )
        if name == "recommend_payment_batch":
            return apt.recommend_payment_batch(
                conn, as_of_date=tool_input["as_of_date"], invoice_ids=tool_input["invoice_ids"], notes=tool_input["notes"],
            )
        if name == "submit_report":
            audit.log_report(conn, apt.AGENT_NAME, "submit_report", tool_input)
            return {"status": "report_received"}
        return {"error": f"unknown tool {name}"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}


def run(db_path: str, period: str, as_of_date: str) -> dict:
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
            f"Review AP for the {period} closing period (as of {as_of_date}). Check for correction "
            "requests addressed to you, run your own duplicate and unusual-charge scans, and resolve "
            "anything you confirm."
        ),
    }]

    final_report = None
    total_input_tokens = 0
    total_output_tokens = 0

    print(f"=== AP Agent run starting (model={MODEL}, db={db_path}, period={period}) ===\n")

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
    parser.add_argument("--period", default="2026-08")
    parser.add_argument("--as-of", default="2026-08-31")
    args = parser.parse_args()
    run(args.db, args.period, args.as_of)
