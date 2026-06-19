from __future__ import annotations

import sqlite3
from pathlib import Path

DB_FILENAME = "controlplane.db"


def connect(project_root: Path) -> sqlite3.Connection:
    """Open (creating if needed) the engagement DB under project_root."""
    project_root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(project_root / DB_FILENAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
