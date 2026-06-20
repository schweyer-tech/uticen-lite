"""``cflow run`` subcommand — execute controls via the store and write workpaper outputs.

Usage
-----
    cflow run [dir] [--control <id>] [--at <iso8601>]

For each control (or the single control named by ``--control``):

1. Loads the project from ``controlplane.db`` (via ``load_project_from_store``).
2. Executes and persists the run via ``run_control_in_store`` (writes to DB,
   renders workpaper .md/.html, and evidence violations.json).
3. Prints a per-control summary line.

Exit codes
----------
0  All controls completed (author errors are reported per-control but do not abort others).
1  Any control errored, or the ``--control`` filter matched no controls.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

from controlflow_sdk.cli._store_guard import check_store
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.loader import load_project_from_store
from controlflow_sdk.store.run_service import run_control_in_store


def run_cmd(args: argparse.Namespace) -> int:
    """Handle ``cflow run [dir] [--control <id>] [--at <iso8601>]``."""
    root = Path(args.dir).resolve()
    executed_at: str = args.at

    # ── Load project from store ────────────────────────────────────────────────
    try:
        conn = connect(root)
        # Detect a missing/empty store before a read raises "no such table".
        if not check_store(conn, root):
            return 1
        project = load_project_from_store(conn)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR loading project at {root}: {exc}", file=sys.stderr)
        return 1

    # ── Apply --control filter ─────────────────────────────────────────────────
    controls = project.controls
    if args.control is not None:
        controls = [c for c in controls if c.id == args.control]
        if not controls:
            print(
                f"ERROR: no control with id '{args.control}' found in project.",
                file=sys.stderr,
            )
            return 1

    if not controls:
        print("No controls found — nothing to run.")
        return 0

    # ── Execute each control ───────────────────────────────────────────────────
    any_errored = False

    for control in controls:
        try:
            run = run_control_in_store(conn, root, control.id, executed_at)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR  {control.id}: {exc}", file=sys.stderr)
            any_errored = True
            continue

        print(
            f"  RUN  {control.id}  "
            f"{run.failed} violation(s) / {run.population_size} records  "
            f"{run.pass_rate}%"
        )

    return 1 if any_errored else 0
