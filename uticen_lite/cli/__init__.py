"""Uticen SDK CLI — ``uticen-lite`` entry point.

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

from uticen_lite.cli.build_cmd import build_cmd
from uticen_lite.cli.import_cmd import import_cmd
from uticen_lite.cli.run_cmd import run_cmd


def _version() -> str:
    """Return the installed ``uticen-lite`` version, or ``"unknown"``.

    Reads the distribution metadata so the reported version always matches what
    pip installed. Falls back gracefully when the package isn't installed as a
    distribution (e.g. run straight from a source checkout).
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("uticen-lite")
    except PackageNotFoundError:
        return "unknown"


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_validate(args: argparse.Namespace) -> int:
    """Handle ``uticen-lite validate [dir]`` — deprecated stub, returns 0."""
    print(
        "NOTE: `uticen-lite validate` is deprecated. "
        "Use `uticen-lite import` to load a project into the store, then `uticen-lite run`.",
        file=sys.stderr,
    )
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uticen-lite",
        description="Uticen SDK command-line interface.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_version()}",
        help="Show the installed uticen-lite version and exit.",
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

    # -- upgrade -------------------------------------------------------------
    upgrade_p = sub.add_parser(
        "upgrade",
        help="Check for and install uticen-lite updates (install-aware).",
    )
    upgrade_p.add_argument(
        "--check",
        action="store_true",
        help="Report installed vs latest and exit without installing.",
    )
    upgrade_p.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt and install.",
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

    if args.command == "upgrade":
        from uticen_lite.cli.upgrade_cmd import upgrade_cmd

        return upgrade_cmd(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
