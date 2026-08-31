"""Shared read-only connection helper for the deterministic services layer.

Every function in services/ takes a sqlite3.Connection as its first argument
rather than a path — callers (agents' tools, tests, the orchestrator) own
connection lifecycle. Nothing in this package writes to the database except
the two explicitly transactional helpers in statements.py used by AP/AR/
Bookkeeping tools elsewhere (M2+) — everything here is pure computation.
"""

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "db" / "meridian.db"


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
