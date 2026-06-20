"""``cflow build`` subcommand — assemble a versioned import-bundle zip.

Usage
-----
    cflow build [dir] [--out import-bundle.zip] [--at <iso8601>]

Steps
-----
1. Load the project from ``controlplane.db`` (via ``build_bundle``).
2. Read runs from the store (``repo.list_runs_for`` per control).
3. Exit 1 with a "run before build" message when no runs are found.
4. Reconstruct ``RunRecord.to_dict()`` shapes for bundle parity.
5. Call ``assemble_bundle(project, runs_by_control, generated_at)`` to build a
   validated manifest dict.
6. Call ``write_bundle(manifest, target_dir, out_path)`` to write the zip.
7. Print the bundle path plus control/run counts on success.

Exit codes
----------
0  Bundle written successfully.
1  No runs found, bundle validation failed, or project failed to load.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

from controlflow_sdk.bundle import BundleError
from controlflow_sdk.cli._store_guard import check_store
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.export_service import build_bundle
from controlflow_sdk.store.loader import load_project_from_store


def build_cmd(args: argparse.Namespace) -> int:
    """Handle ``cflow build [dir] [--out <path>] [--at <iso8601>]``."""
    root = Path(args.dir).resolve()
    generated_at: str = args.at
    out_path = Path(args.out) if args.out else root / "import-bundle.zip"

    # ── Load project to get control/run counts for summary ────────────────────
    try:
        conn = connect(root)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR connecting to store at {root}: {exc}", file=sys.stderr)
        return 1

    # Detect a missing/empty store before a read raises "no such table".
    if not check_store(conn, root):
        conn.close()
        return 1

    try:
        project = load_project_from_store(conn)
    except Exception as exc:  # noqa: BLE001
        conn.close()
        print(f"ERROR loading project at {root}: {exc}", file=sys.stderr)
        return 1

    # ── Pre-check: gather run counts for the summary line ─────────────────────
    runs_by_control = {c.id: repo.list_runs_for(conn, c.id) for c in project.controls}
    runs_by_control = {cid: runs for cid, runs in runs_by_control.items() if runs}

    if not runs_by_control:
        print(
            "ERROR: No runs found in store — run `cflow run` first, "
            "and make sure it completed without errors.",
            file=sys.stderr,
        )
        conn.close()
        return 1

    total_runs = sum(len(v) for v in runs_by_control.values())
    control_count = len(runs_by_control)

    # ── Assemble and write bundle ──────────────────────────────────────────────
    try:
        build_bundle(conn, root, out_path, generated_at)
    except (BundleError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        conn.close()
        return 1
    finally:
        conn.close()

    # ── Summary ────────────────────────────────────────────────────────────────
    ctrl_word = "control" if control_count == 1 else "controls"
    run_word = "run" if total_runs == 1 else "runs"
    print(f"  BUNDLE  {out_path}  {control_count} {ctrl_word} / {total_runs} {run_word}")

    return 0
