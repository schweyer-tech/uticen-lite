"""``uticen-lite import`` — load a YAML project into a local controlplane.db store."""

from __future__ import annotations

import argparse
from pathlib import Path

from uticen_lite.store.db import connect
from uticen_lite.store.import_service import import_project
from uticen_lite.store.migrations import migrate


def import_cmd(args: argparse.Namespace) -> int:
    """Import a YAML project directory into a controlplane.db engagement store.

    Args:
        args: Parsed CLI namespace with:
            ``src``  — path to the YAML project directory.
            ``into`` — target engagement directory (defaults to ``src``).

    Returns:
        0 on success.
    """
    src = Path(args.src)
    into = Path(args.into) if getattr(args, "into", None) else src

    conn = connect(into)
    migrate(conn)
    n_controls, n_sources = import_project(conn, src)
    conn.close()

    print(f"IMPORT  {n_controls} controls / {n_sources} sources → {into}")
    return 0
