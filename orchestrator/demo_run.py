"""M8: the end-to-end demo run.

Section 13's M8 bar: "one full month, injected anomalies found, audit
trail replayable, human approval recorded." Every piece of that already
exists from M0-M7 — this milestone is what actually proves the claim
instead of asserting it, and it deliberately runs against a completely
fresh database rather than reusing db/meridian.db.

Why a separate database (db/demo_meridian.db, not db/meridian.db): the
project's working database already carries five milestones of real audit
history on top of the same August 2026 period — corrections applied,
disputes resolved, forecast assumptions set, the period closed and
re-closed while M6/M7 were being built and debugged. Re-running the
pipeline against it would mostly be re-confirming state that's already
settled, not proving the system finds a month's anomalies cold. A demo
that's supposed to show "the system surfaces problems it wasn't told
about" needs an agent that's actually seeing that data for the first
time. data/seed/generate_meridian.py is deterministic (random.seed(42)):
regenerating produces the identical six anomalies from data/seed/README.md
every time, so this demo is fully reproducible.

What this script does, end to end:
  1. Regenerate a fresh db/demo_meridian.db (skipped if it already exists
     and --fresh wasn't passed, so re-running after approving doesn't wipe
     the approval).
  2. Run orchestrator.run_close against it — same two structural gates as
     every other close, no shortcuts. First run blocks at Gate 2 exactly
     like it always does; approve with orchestrator/approve.py (pointed at
     --db db/demo_meridian.db) and re-run this script to finish.
  3. Once closed: verify all six seeded anomalies against the live agents'
     actual output (tools/verify_anomalies.py) and build a full audit-trail
     replay (tools/replay_audit_trail.py) — both written to
     reports/demo/2026-08/ alongside the regular close package.

Usage:
    export ANTHROPIC_API_KEY=$(cat /home/claude/.anthropic_key)
    python3 orchestrator/demo_run.py
    # if it blocks at Gate 2:
    python3 orchestrator/approve.py --db db/demo_meridian.db --period 2026-08 --approved-by "Your Name"
    python3 orchestrator/demo_run.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.seed import generate_meridian as gen  # noqa: E402
from orchestrator.run_close import run_close  # noqa: E402
from tools import replay_audit_trail  # noqa: E402
from tools import verify_anomalies  # noqa: E402

DEMO_DB = ROOT / "db" / "demo_meridian.db"
DEMO_REPORTS_DIR = ROOT / "reports" / "demo"
PERIOD = "2026-08"


def regenerate_demo_db() -> None:
    print(f"Regenerating a fresh demo database at {DEMO_DB} ...")
    gen.DB_PATH = DEMO_DB
    gen.main()


def demo_run(force_fresh: bool = False) -> dict:
    if force_fresh or not DEMO_DB.exists():
        regenerate_demo_db()
    else:
        print(f"Reusing existing {DEMO_DB} (pass --fresh to regenerate). "
              f"This is expected on the run after an approval.")

    result = run_close(str(DEMO_DB), PERIOD, reports_dir=str(DEMO_REPORTS_DIR))

    if result["status"] != "closed":
        print(f"\nDemo run did not close this pass (status={result['status']}). "
              f"Follow the gate instructions printed above, then re-run this script.")
        return result

    print(f"\n{'=' * 70}\nM8 VERIFICATION\n{'=' * 70}\n")
    conn = sqlite3.connect(DEMO_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    scorecard = verify_anomalies.verify_all(conn, PERIOD)
    found_count = sum(1 for r in scorecard if r["found"])
    print(f"Anomaly scorecard: {found_count}/{len(scorecard)} of the seeded anomalies found\n")
    for r in scorecard:
        mark = "FOUND" if r["found"] else "MISSED"
        print(f"  #{r['id']} [{mark}] {r['name']}")
        print(f"        {r['evidence']}")

    entries = replay_audit_trail.replay(conn)
    summary = replay_audit_trail.summarize_by_agent(entries)
    print(f"\nAudit trail: {len(entries)} entries across {len(summary)} agents — {summary}")

    out_dir = DEMO_REPORTS_DIR / PERIOD
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "anomaly_scorecard.json").write_text(json.dumps({
        "period": PERIOD, "found_count": found_count, "total": len(scorecard), "results": scorecard,
    }, indent=2))
    (out_dir / "audit_trail_replay.txt").write_text(
        replay_audit_trail.render_text(entries)
        + "\n\n--- by agent ---\n" + "\n".join(f"{k}: {v}" for k, v in summary.items())
        + f"\n\n{len(entries)} total entries replayed.\n"
    )
    print(f"\nWrote {out_dir / 'anomaly_scorecard.json'}")
    print(f"Wrote {out_dir / 'audit_trail_replay.txt'}")

    conn.close()
    return {"close_result": result, "scorecard": scorecard, "found_count": found_count,
            "total_anomalies": len(scorecard), "audit_entries": len(entries)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="force-regenerate the demo database even if it exists")
    args = parser.parse_args()
    demo_run(force_fresh=args.fresh)
