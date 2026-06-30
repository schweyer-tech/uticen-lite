"""Read the current version and (optionally) the latest available version.

Network and git access are injectable (``fetch`` / ``git_run``) so tests run
fully offline. The default ``fetch`` shells out to ``pip index versions`` (which
honours the user's configured index); the default git runner uses ``git`` in the
editable source tree.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uticen_lite.upgrade.detect import InstallMethod, source_dir
from uticen_lite.upgrade.version import is_newer

Fetcher = Callable[[], "str | None"]
GitRunner = Callable[[list[str]], Any]


@dataclass(frozen=True)
class UpdateInfo:
    method: InstallMethod
    current: str
    latest: str | None
    available: bool
    message: str


def current_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("uticen-lite")
    except PackageNotFoundError:
        from uticen_lite import __version__

        return __version__


def _pip_index_latest() -> str | None:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "index", "versions", "uticen-lite"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("LATEST:"):
            return stripped.split(":", 1)[1].strip()
    return None


def latest_version(fetch: Fetcher | None = None) -> str | None:
    return (fetch or _pip_index_latest)()


def _default_git_run(source: Path) -> GitRunner:
    def run(args: list[str]) -> Any:
        return subprocess.run(
            args, cwd=str(source), capture_output=True, text=True, timeout=20, check=False
        )

    return run


def _git_behind(git_run: GitRunner) -> tuple[int, str | None]:
    git_run(["git", "fetch", "--quiet"])
    rev = git_run(["git", "rev-list", "--count", "HEAD..@{u}"])
    try:
        count = int(str(rev.stdout).strip())
    except (ValueError, AttributeError):
        return 0, None
    sha = str(git_run(["git", "rev-parse", "--short", "@{u}"]).stdout).strip() or None
    return count, sha


def check_for_update(
    method: InstallMethod,
    *,
    fetch: Fetcher | None = None,
    git_run: GitRunner | None = None,
) -> UpdateInfo:
    current = current_version()
    if method is InstallMethod.GIT_EDITABLE:
        src = source_dir()
        if src is None:
            return UpdateInfo(method, current, None, False, "Could not locate the source checkout.")
        count, sha = _git_behind(git_run or _default_git_run(src))
        if count > 0:
            return UpdateInfo(method, current, sha, True, f"{count} commit(s) behind origin.")
        return UpdateInfo(method, current, sha, False, "Up to date with origin.")
    if method is InstallMethod.UNKNOWN:
        return UpdateInfo(
            method, current, None, False, "Automatic upgrade isn't available for this install."
        )
    latest = latest_version(fetch)
    if latest is None:
        return UpdateInfo(method, current, None, False, "Couldn't check for updates.")
    if is_newer(latest, current):
        return UpdateInfo(method, current, latest, True, f"Version {latest} is available.")
    return UpdateInfo(method, current, latest, False, "You're on the latest version.")
