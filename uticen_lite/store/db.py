from __future__ import annotations

import sqlite3
from pathlib import Path

DB_FILENAME = "controlplane.db"


def connect(project_root: Path) -> sqlite3.Connection:
    """Open (creating if needed) the engagement DB under project_root.

    ``check_same_thread=False``: the control plane is FastAPI + sync handlers, where
    a request's connection is created in the dependency-setup threadpool task but the
    GET handler (and its ``finally: conn.close()``) run on a *different* threadpool
    thread. With the sqlite3 default this raised ``ProgrammingError`` and 500'd every
    GET under concurrency (each page load fires the header update-indicator fetch
    alongside it). It is safe here because every request owns its OWN connection and
    uses it sequentially — never two threads touching one connection at once. See
    learning 0002. ``busy_timeout`` lets a write wait for a concurrent reader's lock
    instead of erroring (localhost, low concurrency).
    """
    project_root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(project_root / DB_FILENAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn
