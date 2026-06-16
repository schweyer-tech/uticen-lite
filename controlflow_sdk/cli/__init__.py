"""ControlFlow SDK CLI — ``cflow`` entry point.

Subcommands
-----------
init <dir>
    Scaffold a new cflow project.

new control <slug> [--dir <dir>]
    Scaffold a new control under the given project directory.

validate [dir]
    Load the project, validate all controls, and report results.
    Exits 0 if all controls are valid, 1 if any are invalid.

run [dir] [--control <id>] [--at <iso8601>]
    Execute all controls (or one) and write workpaper + evidence outputs.
    Exits 0 if all controls completed, 1 if any errored.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from controlflow_sdk.cli.run_cmd import run_cmd
from controlflow_sdk.cli.scaffold import scaffold_control, scaffold_project
from controlflow_sdk.project import ProjectError, load_sources
from controlflow_sdk.schema.validate import validate_control

# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> int:
    """Handle ``cflow init <dir>``."""
    root = Path(args.dir).resolve()
    scaffold_project(root)
    print(f"Initialized project at {root}")
    return 0


def _cmd_new(args: argparse.Namespace) -> int:
    """Handle ``cflow new control <slug> [--dir <dir>]``."""
    if args.resource != "control":
        print(f"Unknown resource type: '{args.resource}'", file=sys.stderr)
        print("Usage: cflow new control <slug>", file=sys.stderr)
        return 2

    root = Path(args.dir).resolve()
    scaffold_control(root, args.slug)
    print(f"Created control '{args.slug}' in {root / 'controls' / args.slug}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """Handle ``cflow validate [dir]``."""
    root = Path(args.dir).resolve()
    all_valid = True

    # Load sources map (needed by discover_controls for reference resolution).
    # If sources.yaml is missing, report clearly and bail.
    try:
        sources = load_sources(root)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ProjectError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Walk controls and validate each control.yaml directly (schema layer),
    # then also attempt full project load to catch reference errors.
    import yaml  # noqa: PLC0415 — local import to keep startup fast

    controls_root = root / "controls"
    if not controls_root.is_dir():
        print("No controls/ directory found — nothing to validate.")
        return 0

    control_yamls = sorted(controls_root.glob("*/control.yaml"))
    if not control_yamls:
        print("No controls found — nothing to validate.")
        return 0

    for control_yaml in control_yamls:
        slug = control_yaml.parent.name
        try:
            with control_yaml.open(encoding="utf-8") as fh:
                doc: dict = yaml.safe_load(fh) or {}
            errors = validate_control(doc)
            # Also check source references
            for src_ref in doc.get("sources", []):
                src_id = src_ref.get("id", "")
                if src_id and src_id not in sources:
                    errors.append(f"sources: references unknown source id '{src_id}'")
        except Exception as exc:  # noqa: BLE001
            errors = [str(exc)]

        if errors:
            all_valid = False
            print(f"  FAIL  {slug}")
            for err in errors:
                print(f"        - {err}")
        else:
            print(f"  OK    {slug}")

    return 0 if all_valid else 1


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cflow",
        description="ControlFlow SDK command-line interface.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # -- init ----------------------------------------------------------------
    init_p = sub.add_parser("init", help="Scaffold a new cflow project.")
    init_p.add_argument("dir", help="Directory to initialise (created if absent).")

    # -- new -----------------------------------------------------------------
    new_p = sub.add_parser("new", help="Scaffold a new resource.")
    new_p.add_argument(
        "resource",
        choices=["control"],
        help="Resource type to create.",
    )
    new_p.add_argument("slug", help="Slug identifier for the new resource.")
    new_p.add_argument(
        "--dir",
        default=".",
        help="Project root directory (default: current directory).",
    )

    # -- validate ------------------------------------------------------------
    val_p = sub.add_parser("validate", help="Validate all controls in a project.")
    val_p.add_argument(
        "dir",
        nargs="?",
        default=".",
        help="Project root directory (default: current directory).",
    )

    # -- run -----------------------------------------------------------------
    run_p = sub.add_parser(
        "run",
        help="Execute controls and write workpaper + evidence outputs.",
    )
    run_p.add_argument(
        "dir",
        nargs="?",
        default=".",
        help="Project root directory (default: current directory).",
    )
    run_p.add_argument(
        "--control",
        default=None,
        metavar="<id>",
        help="Run only the control with this id (default: run all).",
    )
    run_p.add_argument(
        "--at",
        default=None,
        metavar="<iso8601>",
        help="Execution timestamp in ISO-8601 format (default: current UTC time).",
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Parse *argv* and dispatch to the appropriate subcommand.

    Args:
        argv: Argument list (excluding the program name).  When ``None``,
              ``sys.argv[1:]`` is used — this is the normal console-script path.

    Returns:
        Integer exit code (0 = success, 1 = validation failure, 2 = usage error).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return _cmd_init(args)
    if args.command == "new":
        return _cmd_new(args)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "run":
        # Clock boundary: inject current UTC time only when --at is not supplied.
        if args.at is None:
            args.at = datetime.now(UTC).isoformat()
        return run_cmd(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
