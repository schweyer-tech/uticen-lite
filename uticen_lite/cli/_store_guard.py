"""Shared pre-check for store-backed CLI subcommands (``run`` / ``build``).

``store.db.connect`` creates an empty SQLite file on demand, so pointing a
subcommand at a directory that was never imported yields a connection whose
schema was never built. Reading from it surfaces a cryptic
``sqlite3.OperationalError: no such table: project``. ``check_store`` turns that
into an actionable message telling the user to run ``uticen-lite import`` first.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from uticen_lite.store.db import DB_FILENAME


def check_store(conn: sqlite3.Connection, root: Path) -> bool:
    """Return True when *conn* points at a migrated engagement store.

    On a missing/empty store, print an actionable message to stderr and return
    False so the caller can exit non-zero. A migrated store always has the
    ``project`` table (created by the first migration step).
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project'"
    ).fetchone()
    if row is not None:
        return True

    print(
        f"ERROR: No engagement store found in {root} "
        f"(expected {DB_FILENAME}). Run `uticen-lite import <yaml-project>` first.",
        file=sys.stderr,
    )
    return False
