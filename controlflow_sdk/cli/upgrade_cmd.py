"""Handle ``cflow upgrade [--check] [--yes]`` — the install-aware upgrade routine.

Runs inline (there is no server to outlive). For a git checkout this is the
maintainer's ``git pull --ff-only && pip install -e .`` made first-class.
"""

from __future__ import annotations

import argparse
import subprocess

from controlflow_sdk.upgrade.check import check_for_update
from controlflow_sdk.upgrade.command import build_upgrade_command
from controlflow_sdk.upgrade.detect import InstallMethod, detect_install, source_dir


def upgrade_cmd(args: argparse.Namespace) -> int:
    method = detect_install()
    info = check_for_update(method)
    print(f"Installed: {info.current}   ({method.value})")
    print(info.message)

    if args.check:
        return 0
    if method is InstallMethod.UNKNOWN:
        print("Automatic upgrade isn't available for this install — see docs/INSTALL.md.")
        return 1
    if not info.available:
        return 0
    if not args.yes:
        reply = input("Proceed with upgrade? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 1

    src = source_dir() if method is InstallMethod.GIT_EDITABLE else None
    commands = build_upgrade_command(method, source_dir=str(src) if src else None)
    for cmd in commands:
        print("$", " ".join(cmd))
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print("Upgrade command failed.")
            return result.returncode
    print("Upgrade complete. Restart controlplane to use the new version.")
    return 0
