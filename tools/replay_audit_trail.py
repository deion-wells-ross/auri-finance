"""Audit-trail replay (M8) — turns `audit_log` into a readable, ordered
narrative. Deterministic, read-only, no LLM involved.

Section 1's V1 bar #4 says the audit trail must be "replayable after the
fact" — this is that claim turned into runnable code instead of an
implicit property of a table nobody had actually queried end-to-end
before this milestone. Building it is also what surfaced a real M8 gap
(see tools/audit.py's docstring): every agent's terminal submit_report /
submit_briefing call was never persisted to audit_log at all, only its
intermediate tool calls were — so the first attempt at a real replay came
up missing the one thing an audit trail most needs, each agent's actual
conclusion. Fixed there; this module is what actually exercises the fix.
"""

from __future__ import annotations

import json
import sqlite3


def replay(conn: sqlite3.Connection, period: str | None = None, agents: list[str] | None = None) -> list[dict]:
    """Every audit_log row, ordered chronologically. `agents` restricts to
    a list of agent names. `period` is a best-effort text filter — audit_log
    deliberately has no period column of its own (Section 11: it's a
    cross-cutting log, not scoped to one entity type), so this matches the
    period string against inputs/outputs/notes; some period-relevant rows
    (e.g. a read call with no period argument) may not mention it and will
    be excluded, which is an honest limitation of a free-text log, not a
    bug in this function."""
    query = "SELECT * FROM audit_log"
    conditions = []
    params: list = []
    if agents:
        conditions.append(f"agent IN ({','.join('?' * len(agents))})")
        params.extend(agents)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY timestamp, log_id"

    rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    if period is None:
        return rows

    out = []
    for r in rows:
        blob = " ".join(str(r.get(c) or "") for c in ("inputs", "outputs", "notes"))
        if period in blob:
            out.append(r)
    return out


def render_text(entries: list[dict]) -> str:
    """A human-readable timeline: one line per action, with the reasoning
    ('notes', when present) indented underneath — literally "who did what
    and why," read straight off the log."""
    lines = []
    for e in entries:
        tool = e.get("tool_name") or "-"
        lines.append(f"[{e['timestamp']}] {e['agent']:<20} {e['action']:<18} {tool}")
        if e.get("notes"):
            lines.append(f"    why: {e['notes']}")
    return "\n".join(lines)


def summarize_by_agent(entries: list[dict]) -> dict:
    """Action counts per agent — a quick sanity check that every agent
    that should have participated actually left a trace."""
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["agent"]] = counts.get(e["agent"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT))

    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "db" / "meridian.db"))
    parser.add_argument("--period", default=None)
    parser.add_argument("--agent", action="append", default=None, help="repeatable; restrict to these agents")
    parser.add_argument("--out", default=None, help="write the rendered replay to this file instead of stdout")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    entries = replay(conn, period=args.period, agents=args.agent)
    conn.close()

    text = render_text(entries)
    summary = summarize_by_agent(entries)
    footer = "\n\n--- by agent ---\n" + "\n".join(f"{k}: {v}" for k, v in summary.items())
    full = text + footer + f"\n\n{len(entries)} total entries replayed.\n"

    if args.out:
        Path(args.out).write_text(full)
        print(f"Wrote replay ({len(entries)} entries) to {args.out}")
    else:
        print(full)
