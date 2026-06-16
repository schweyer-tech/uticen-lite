"""``cflow run`` subcommand — execute controls and write workpaper outputs.

Usage
-----
    cflow run [dir] [--control <id>] [--at <iso8601>]

For each control (or the single control named by ``--control``):

1. Executes ``run_control(control, sources, root, executed_at)``.
2. Assembles a :class:`~controlflow_sdk.model.workpaper.Workpaper` via
   ``Workpaper.assemble(control, run, generated_at=executed_at)``.
3. Writes ``target/workpapers/<id>.md`` and ``target/workpapers/<id>.html``.
4. Writes ``target/evidence/<id>-violations.json`` (JSON list of violation dicts).
5. Appends the run to ``target/run-log.json`` via ``append_run``.
6. Prints a per-control summary line.

Exit codes
----------
0  All controls completed (author errors are reported per-control but do not abort others).
1  Any control errored, or the ``--control`` filter matched no controls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

from controlflow_sdk.model.workpaper import Workpaper
from controlflow_sdk.project.discovery import Project
from controlflow_sdk.render.html import render_html
from controlflow_sdk.render.markdown import render_markdown
from controlflow_sdk.runner.execute import RunnerError, run_control
from controlflow_sdk.runner.runlog import append_run


def run_cmd(args: argparse.Namespace) -> int:
    """Handle ``cflow run [dir] [--control <id>] [--at <iso8601>]``."""
    root = Path(args.dir).resolve()
    executed_at: str = args.at

    # ── Load project ──────────────────────────────────────────────────────────
    try:
        project = Project.load(root)
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

    # ── Output directories ─────────────────────────────────────────────────────
    target_dir = root / "target"
    workpapers_dir = target_dir / "workpapers"
    evidence_dir = target_dir / "evidence"
    workpapers_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # ── Execute each control ───────────────────────────────────────────────────
    any_errored = False

    for control in controls:
        try:
            run = run_control(control, project.sources, root, executed_at)
        except RunnerError as exc:
            print(f"  ERROR  {control.id}: {exc}", file=sys.stderr)
            any_errored = True
            continue

        # Assemble workpaper
        wp = Workpaper.assemble(control, run, generated_at=executed_at)

        # Write markdown
        md_path = workpapers_dir / f"{control.id}.md"
        md_path.write_text(render_markdown(wp), encoding="utf-8")

        # Write HTML
        html_path = workpapers_dir / f"{control.id}.html"
        html_path.write_text(render_html(wp), encoding="utf-8")

        # Write violations evidence
        violations_path = evidence_dir / f"{control.id}-violations.json"
        violations_path.write_text(
            json.dumps([v.to_dict() for v in run.violations], indent=2),
            encoding="utf-8",
        )

        # Append to run log
        append_run(target_dir, run)

        # Summary line
        print(
            f"  RUN  {control.id}  "
            f"{run.failed} violation(s) / {run.population_size} records  "
            f"{run.pass_rate}%"
        )

    return 1 if any_errored else 0
