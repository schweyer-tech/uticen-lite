"""Build the upgrade command(s) for a detected install method (pure)."""

from __future__ import annotations

import sys

from controlflow_sdk.upgrade.detect import InstallMethod


def build_upgrade_command(
    method: InstallMethod,
    *,
    python: str | None = None,
    source_dir: str | None = None,
) -> list[list[str]]:
    """Return the command(s) to run, as a list of argv lists (run in order)."""
    py = python or sys.executable
    if method is InstallMethod.GIT_EDITABLE:
        if not source_dir:
            raise ValueError("source_dir is required for a git-editable upgrade")
        return [
            ["git", "-C", source_dir, "pull", "--ff-only"],
            [py, "-m", "pip", "install", "-e", source_dir],
        ]
    if method is InstallMethod.PIPX:
        return [["pipx", "upgrade", "controlflow-sdk"]]
    if method is InstallMethod.PIP:
        return [[py, "-m", "pip", "install", "-U", "controlflow-sdk"]]
    raise ValueError(f"no upgrade command for install method {method}")
