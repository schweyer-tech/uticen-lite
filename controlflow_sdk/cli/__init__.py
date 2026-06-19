"""ControlFlow SDK CLI — ``cflow`` entry point.

Subcommands
-----------
import <src> [--into <dir>]
    Import a YAML project into a controlplane.db engagement store.

run [dir] [--control <id>] [--at <iso8601>]
    Execute all controls (or one) via the store and write workpaper + evidence outputs.
    Exits 0 if all controls completed, 1 if any errored.

build [dir] [--out import-bundle.zip] [--at <iso8601>]
    Read runs from the store, assemble a validated manifest, and write a zip bundle.
    Exits 0 on success, 1 if no runs exist or the manifest is invalid.

validate [dir]
    (Deprecated) YAML-project validator. Returns 0.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from controlflow_sdk.cli.build_cmd import build_cmd
from controlflow_sdk.cli.import_cmd import import_cmd
from controlflow_sdk.cli.run_cmd import run_cmd

# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_validate(args: argparse.Namespace) -> int:
    """Handle ``cflow validate [dir]`` — deprecated stub, returns 0."""
    print(
        "NOTE: `cflow validate` is deprecated. "
        "Use `cflow import` to load a project into the store, then `cflow run`.",
        file=sys.stderr,
    )
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cflow",
        description="ControlFlow SDK command-line interface.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # -- validate (deprecated stub) ------------------------------------------
    val_p = sub.add_parser("validate", help="(Deprecated) YAML project validator.")
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

    # -- import --------------------------------------------------------------
    import_p = sub.add_parser(
        "import",
        help="Import a YAML project into a controlplane.db engagement store.",
    )
    import_p.add_argument(
        "src",
        help="Path to the YAML project directory (must contain cflow.yaml).",
    )
    import_p.add_argument(
        "--into",
        default=None,
        metavar="<dir>",
        help="Target engagement directory (default: same as src).",
    )

    # -- build ---------------------------------------------------------------
    build_p = sub.add_parser(
        "build",
        help="Assemble a versioned import-bundle zip from the run log.",
    )
    build_p.add_argument(
        "dir",
        nargs="?",
        default=".",
        help="Project root directory (default: current directory).",
    )
    build_p.add_argument(
        "--out",
        default=None,
        metavar="<path>",
        help="Output zip path (default: <project-dir>/import-bundle.zip).",
    )
    build_p.add_argument(
        "--at",
        default=None,
        metavar="<iso8601>",
        help="Bundle timestamp in ISO-8601 format (default: current UTC time).",
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

    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "run":
        # Clock boundary: inject current UTC time only when --at is not supplied.
        if args.at is None:
            args.at = datetime.now(UTC).isoformat()
        return run_cmd(args)

    if args.command == "import":
        return import_cmd(args)

    if args.command == "build":
        # Clock boundary: inject current UTC time only when --at is not supplied.
        if args.at is None:
            args.at = datetime.now(UTC).isoformat()
        return build_cmd(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
