"""Detect how controlflow-sdk was installed, to pick the right upgrade command.

The decision is split into a pure ``classify_install`` (fully unit-testable) and
a thin ``detect_install`` that gathers the real environment facts.
"""

from __future__ import annotations

import enum
import json
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname


class InstallMethod(enum.Enum):
    GIT_EDITABLE = "git-editable"
    PIPX = "pipx"
    PIP = "pip"
    UNKNOWN = "unknown"


def _direct_url() -> dict | None:
    try:
        dist = distribution("controlflow-sdk")
    except PackageNotFoundError:
        return None
    try:
        text = dist.read_text("direct_url.json")
    except Exception:
        return None
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _url_to_path(url: str) -> Path | None:
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    return Path(url2pathname(parsed.path))


def source_dir() -> Path | None:
    """The editable source tree, if installed editable from a local path."""
    direct_url = _direct_url()
    if not direct_url:
        return None
    return _url_to_path(str(direct_url.get("url", "")))


def classify_install(
    direct_url: dict | None, sys_prefix: str, source_has_git: bool
) -> InstallMethod:
    """Pure decision: map the gathered facts to an InstallMethod."""
    editable = bool((direct_url or {}).get("dir_info", {}).get("editable"))
    if editable:
        return InstallMethod.GIT_EDITABLE if source_has_git else InstallMethod.UNKNOWN
    prefix = sys_prefix.replace("\\", "/")
    if "/pipx/venvs/" in prefix:
        return InstallMethod.PIPX
    return InstallMethod.PIP


def detect_install() -> InstallMethod:
    direct_url = _direct_url()
    src = source_dir()
    has_git = bool(src and (src / ".git").exists())
    return classify_install(direct_url, sys.prefix, has_git)
