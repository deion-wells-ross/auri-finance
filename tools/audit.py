"""Shared audit_log write helper — genuinely new in M8, not a refactor of
something that already worked.

Every tools/*.py module has always defined its own private `_log()` with
this exact insert shape, used for every read/write tool call an agent
makes. What none of the seven agent runners (`agents/*/run.py`) ever did
is call anything like it for the one call that matters most for an audit
trail: the terminal `submit_report` / `submit_briefing` call. Every
runner's `execute_tool` handled that call by returning a bare
`{"status": "report_received"}` receipt and nothing else — the agent's
actual synthesized conclusion (the "why" in Section 1's V1 bar #4, "an
audit trail that answers who did what and why") lived only in the
in-memory `final_report` dict, printed to stdout, and discarded the
moment the process exited. `audit_log` faithfully recorded every
intermediate `get_x` / `flag_y` / `check_z` call an agent made on the way
to its conclusion, but never the conclusion itself.

Found while building M8's audit-trail replay tool: trying to reconstruct
"what did the Payroll Agent decide" from `audit_log` alone came up empty,
because nothing had ever written it there. Fixed by giving every runner
one call to make when it accepts a final report — the same insert shape
every tools module already uses, exposed once instead of copied an eighth
time.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone


def log_report(conn: sqlite3.Connection, agent: str, tool_name: str, report: dict) -> None:
    """Persist an agent's accepted terminal report (submit_report /
    submit_briefing) to audit_log. Called only once a report has already
    passed schema validation where that applies (fpa, cfo) — this records
    the conclusion an agent actually stood behind, not a rejected attempt."""
    conn.execute(
        """INSERT INTO audit_log
           (log_id, timestamp, agent, action, tool_name, inputs, outputs,
            related_entity_type, related_entity_id, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (f"log_{uuid.uuid4().hex[:12]}", datetime.now(timezone.utc).isoformat(timespec="seconds"),
         agent, "final_report", tool_name, json.dumps({}), json.dumps(report),
         None, None, report.get("summary")),
    )
    conn.commit()
