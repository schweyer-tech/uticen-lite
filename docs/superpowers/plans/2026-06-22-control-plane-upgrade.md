# Control-plane Upgrade & Update-awareness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the control plane an install-aware upgrade path — a dashboard self-upgrade button and a `cflow upgrade` CLI — gated behind an opt-in update check that keeps the zero-egress default intact.

**Architecture:** A new dependency-free `controlflow_sdk/upgrade/` package detects the install method (git-editable / pipx / pip / unknown), checks the latest version (opt-in), builds the right upgrade command, and — for the web path — spawns a detached stdlib-only helper that waits for the app to exit, upgrades, and writes a status file. The CLI runs the same command inline. Web routes live in a new `plane/routes/updates.py`; the toggle persists in the existing `project.system` JSON.

**Tech Stack:** Python ≥3.11 stdlib, FastAPI + Jinja2 + HTMX (control plane), argparse (CLI), sqlite3 (store), pytest + FastAPI `TestClient`.

## Global Constraints

- Python floor **≥3.11**; ruff target **py311**, line-length **100**; `python -m ruff check .` and `python -m mypy controlflow_sdk` must stay green.
- **No new runtime dependency.** Do not import or add `packaging`; version comparison is a tiny in-repo helper (learning 0003 — keep the core dep-free).
- The new `upgrade/` package is imported **only** by `plane/` and `cli/` — never by the Pyodide-safe core (`model/`, `runner/`, `rules/`).
- **Never touch `contract/bundle.schema.json`** or any bundle producer — this feature is orthogonal to the bundle (cardinal rule, learning 0001).
- Settings persist in the `project.system` JSON dict via `repo.upsert_project(...)` / `repo.get_project(...)` — **no migration** (the AI provider uses the same column).
- Tests must be **offline and pristine** (no real network/subprocess/process-kill): inject fakes. Fast lane is `python -m pytest -q` (ignores `tests/e2e`).
- **Zero-egress default is sacred:** when the toggle is OFF, no route may make a network call.
- Follow the local conventions surfaced by the explorer: `register(app, templates, get_conn)` route modules; read-only GET uses `Depends(get_conn)`, writing handlers open `connect(request.app.state.project_root)` in try/finally (learning 0002); `templates.TemplateResponse(request, name, ctx)` (Starlette ≥1.3 signature).

---

## EXECUTION RULES

- **Never ask the user for permission to continue between tasks.** Execute the full plan start to finish without interruption.
- On an unresolvable error after 2–3 attempts: note it in your progress summary and skip to the next task.
- **Push after every `git commit`:**
  ```bash
  git push -u origin HEAD
  ```
  (This repo has no project-specific post-push status command.)
- Branch: work happens on the current worktree branch `worktree-control-plane-upgrade`. Do not switch branches.

---

## File Structure

**New — `controlflow_sdk/upgrade/` (dep-free core of the feature):**
- `__init__.py` — package docstring only; consumers import submodules directly.
- `version.py` — `is_newer(candidate, current) -> bool` (tuple compare, no deps).
- `detect.py` — `InstallMethod` enum, pure `classify_install(...)`, `detect_install()`, `source_dir()`.
- `check.py` — `current_version()`, `latest_version(fetch=None)`, `UpdateInfo`, `check_for_update(method, *, fetch=None, git_run=None)`.
- `command.py` — `build_upgrade_command(method, *, python=None, source_dir=None) -> list[list[str]]`.
- `spawn.py` — `write_status`/`read_status`, `_HELPER_SOURCE`, `spawn_detached_upgrade(...)`, `schedule_shutdown(...)`.

**Modified:**
- `controlflow_sdk/store/repo.py` — add `get_check_updates_on_launch` / `set_check_updates_on_launch`.
- `controlflow_sdk/cli/__init__.py` — add `upgrade` subparser + dispatch.
- `controlflow_sdk/cli/upgrade_cmd.py` — **new** `upgrade_cmd(args)`.
- `controlflow_sdk/plane/app.py` — import + register `updates` routes.
- `controlflow_sdk/plane/routes/updates.py` — **new** route module (settings page, toggle, check, badge, upgrade).
- `controlflow_sdk/plane/routes/dashboard.py` — surface the post-upgrade status notice.
- Templates: **new** `settings_updates.html`, `partials/update_result.html`, `partials/update_badge.html`, `upgrading.html`, `upgrade_unavailable.html`; **modify** `settings.html` (Updates card), `dashboard.html` (badge include + notice).
- Docs: `README.md`, `docs/INSTALL.md`, `PRODUCT-MAP.md`, `CHANGELOG.md`.

---

## Task 1: `upgrade/version.py` — dependency-free version compare

**Files:**
- Create: `controlflow_sdk/upgrade/__init__.py`
- Create: `controlflow_sdk/upgrade/version.py`
- Test: `tests/upgrade/__init__.py`, `tests/upgrade/test_version.py`

**Interfaces:**
- Produces: `is_newer(candidate: str, current: str) -> bool` — True iff `candidate` is a strictly newer dotted-numeric release than `current`; tolerant of `v` prefixes and malformed segments (never raises).

- [ ] **Step 1: Write the failing test**

Create `tests/upgrade/__init__.py` (empty file). Create `tests/upgrade/test_version.py`:

```python
from controlflow_sdk.upgrade.version import is_newer


def test_newer_patch_and_minor():
    assert is_newer("0.2.0", "0.1.0") is True
    assert is_newer("0.1.1", "0.1.0") is True
    assert is_newer("1.0.0", "0.9.9") is True


def test_equal_is_not_newer():
    assert is_newer("0.1.0", "0.1.0") is False


def test_older_is_not_newer():
    assert is_newer("0.1.0", "0.2.0") is False


def test_short_vs_long_and_v_prefix():
    assert is_newer("0.1.0", "0.1") is False      # 0.1.0 == 0.1
    assert is_newer("v0.2.0", "0.1.0") is True     # leading v ignored


def test_malformed_never_raises():
    assert is_newer("abc", "0.1.0") is False       # non-numeric -> 0
    assert is_newer("0.2.0rc1", "0.1.0") is True   # trailing junk on a segment ignored
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/upgrade/test_version.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'controlflow_sdk.upgrade'`.

- [ ] **Step 3: Write minimal implementation**

Create `controlflow_sdk/upgrade/__init__.py`:

```python
"""Install-aware upgrade + opt-in update-awareness for the control plane.

Dependency-free (stdlib + package metadata only). Imported by ``plane/`` and
``cli/`` — never by the Pyodide-safe core.
"""
```

Create `controlflow_sdk/upgrade/version.py`:

```python
"""Tiny dependency-free version comparison.

Avoids adding ``packaging`` as a dependency (the core stays dep-free — see
docs/learnings/0003). Compares dotted numeric releases like ``1.2.3``; any
non-numeric tail on a segment is ignored so a malformed string never raises.
"""

from __future__ import annotations


def _parts(value: str) -> tuple[int, ...]:
    out: list[int] = []
    for segment in value.strip().lstrip("vV").split("."):
        digits = ""
        for char in segment:
            if char.isdigit():
                digits += char
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(candidate: str, current: str) -> bool:
    """Return True if *candidate* is a strictly newer release than *current*."""
    a, b = _parts(candidate), _parts(current)
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return a > b
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/upgrade/test_version.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit and push**

```bash
git add controlflow_sdk/upgrade/__init__.py controlflow_sdk/upgrade/version.py tests/upgrade/
git commit -m "feat(upgrade): dependency-free version comparison"
git push -u origin HEAD
```

---

## Task 2: `upgrade/detect.py` — install-method detection

**Files:**
- Create: `controlflow_sdk/upgrade/detect.py`
- Test: `tests/upgrade/test_detect.py`

**Interfaces:**
- Produces:
  - `class InstallMethod(enum.Enum)` with members `GIT_EDITABLE="git-editable"`, `PIPX="pipx"`, `PIP="pip"`, `UNKNOWN="unknown"`.
  - `classify_install(direct_url: dict | None, sys_prefix: str, source_has_git: bool) -> InstallMethod` — pure decision.
  - `detect_install() -> InstallMethod` — gathers real environment facts and calls `classify_install`.
  - `source_dir() -> Path | None` — the editable source tree (from `direct_url.json`), else None.

- [ ] **Step 1: Write the failing test**

Create `tests/upgrade/test_detect.py`:

```python
from controlflow_sdk.upgrade.detect import InstallMethod, classify_install


def test_editable_with_git_is_git_editable():
    du = {"url": "file:///home/u/repo", "dir_info": {"editable": True}}
    assert classify_install(du, "/home/u/repo/.venv", True) is InstallMethod.GIT_EDITABLE


def test_editable_without_git_is_unknown():
    du = {"url": "file:///home/u/repo", "dir_info": {"editable": True}}
    assert classify_install(du, "/home/u/repo/.venv", False) is InstallMethod.UNKNOWN


def test_pipx_prefix_is_pipx():
    prefix = "/home/u/.local/pipx/venvs/controlflow-sdk"
    assert classify_install(None, prefix, False) is InstallMethod.PIPX


def test_windows_pipx_prefix_is_pipx():
    prefix = r"C:\Users\u\pipx\venvs\controlflow-sdk"
    assert classify_install(None, prefix, False) is InstallMethod.PIPX


def test_plain_venv_is_pip():
    assert classify_install(None, "/home/u/project/.venv", False) is InstallMethod.PIP


def test_no_direct_url_is_pip():
    assert classify_install({}, "/usr", False) is InstallMethod.PIP
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/upgrade/test_detect.py -q`
Expected: FAIL with `ImportError` / `ModuleNotFoundError` for `detect`.

- [ ] **Step 3: Write minimal implementation**

Create `controlflow_sdk/upgrade/detect.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/upgrade/test_detect.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit and push**

```bash
git add controlflow_sdk/upgrade/detect.py tests/upgrade/test_detect.py
git commit -m "feat(upgrade): detect install method (git-editable/pipx/pip/unknown)"
git push -u origin HEAD
```

---

## Task 3: `upgrade/check.py` — current/latest version + update info

**Files:**
- Create: `controlflow_sdk/upgrade/check.py`
- Test: `tests/upgrade/test_check.py`

**Interfaces:**
- Consumes: `InstallMethod`, `source_dir` (Task 2); `is_newer` (Task 1).
- Produces:
  - `current_version() -> str` — installed distribution version, falling back to `__version__`.
  - `latest_version(fetch: Callable[[], str | None] | None = None) -> str | None` — injectable; default shells out to `pip index versions`.
  - `@dataclass(frozen=True) class UpdateInfo` with fields `method: InstallMethod`, `current: str`, `latest: str | None`, `available: bool`, `message: str`.
  - `check_for_update(method: InstallMethod, *, fetch=None, git_run=None) -> UpdateInfo` — method-aware; `fetch` and `git_run` are injectable so tests never hit the network or git.

- [ ] **Step 1: Write the failing test**

Create `tests/upgrade/test_check.py`:

```python
from types import SimpleNamespace

from controlflow_sdk.upgrade.check import (
    UpdateInfo,
    check_for_update,
    current_version,
    latest_version,
)
from controlflow_sdk.upgrade.detect import InstallMethod


def test_current_version_is_a_string():
    assert isinstance(current_version(), str)
    assert current_version() != ""


def test_latest_version_uses_injected_fetcher():
    assert latest_version(fetch=lambda: "9.9.9") == "9.9.9"
    assert latest_version(fetch=lambda: None) is None


def test_pip_update_available(monkeypatch):
    monkeypatch.setattr(
        "controlflow_sdk.upgrade.check.current_version", lambda: "0.1.0"
    )
    info = check_for_update(InstallMethod.PIP, fetch=lambda: "0.2.0")
    assert isinstance(info, UpdateInfo)
    assert info.available is True
    assert info.latest == "0.2.0"
    assert "0.2.0" in info.message


def test_pip_up_to_date(monkeypatch):
    monkeypatch.setattr(
        "controlflow_sdk.upgrade.check.current_version", lambda: "0.2.0"
    )
    info = check_for_update(InstallMethod.PIP, fetch=lambda: "0.2.0")
    assert info.available is False


def test_pip_unreachable_index_degrades(monkeypatch):
    monkeypatch.setattr(
        "controlflow_sdk.upgrade.check.current_version", lambda: "0.1.0"
    )
    info = check_for_update(InstallMethod.PIP, fetch=lambda: None)
    assert info.available is False
    assert "couldn't" in info.message.lower()


def test_unknown_method_is_not_available(monkeypatch):
    monkeypatch.setattr(
        "controlflow_sdk.upgrade.check.current_version", lambda: "0.1.0"
    )
    info = check_for_update(InstallMethod.UNKNOWN)
    assert info.available is False


def test_git_behind_uses_injected_runner(monkeypatch):
    monkeypatch.setattr(
        "controlflow_sdk.upgrade.check.current_version", lambda: "0.1.0"
    )
    monkeypatch.setattr(
        "controlflow_sdk.upgrade.check.source_dir", lambda: __import__("pathlib").Path(".")
    )

    def fake_git(args):
        if args[:2] == ["git", "rev-list"]:
            return SimpleNamespace(stdout="3\n", returncode=0)
        if args[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(stdout="abc1234\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    info = check_for_update(InstallMethod.GIT_EDITABLE, git_run=fake_git)
    assert info.available is True
    assert "3" in info.message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/upgrade/test_check.py -q`
Expected: FAIL — `check` module not found.

- [ ] **Step 3: Write minimal implementation**

Create `controlflow_sdk/upgrade/check.py`:

```python
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

from controlflow_sdk.upgrade.detect import InstallMethod, source_dir
from controlflow_sdk.upgrade.version import is_newer

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
        return version("controlflow-sdk")
    except PackageNotFoundError:
        from controlflow_sdk import __version__

        return __version__


def _pip_index_latest() -> str | None:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "index", "versions", "controlflow-sdk"],
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


def _git_behind(source: Path, git_run: GitRunner) -> tuple[int, str | None]:
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
        count, sha = _git_behind(src, git_run or _default_git_run(src))
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/upgrade/test_check.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit and push**

```bash
git add controlflow_sdk/upgrade/check.py tests/upgrade/test_check.py
git commit -m "feat(upgrade): version check + method-aware UpdateInfo"
git push -u origin HEAD
```

---

## Task 4: `upgrade/command.py` — build the upgrade command

**Files:**
- Create: `controlflow_sdk/upgrade/command.py`
- Test: `tests/upgrade/test_command.py`

**Interfaces:**
- Consumes: `InstallMethod` (Task 2).
- Produces: `build_upgrade_command(method, *, python: str | None = None, source_dir: str | None = None) -> list[list[str]]` — one or more argv lists, run in order. Git returns two commands (`git pull` then editable reinstall). Raises `ValueError` for `UNKNOWN` or a git call with no `source_dir`.

- [ ] **Step 1: Write the failing test**

Create `tests/upgrade/test_command.py`:

```python
import pytest

from controlflow_sdk.upgrade.command import build_upgrade_command
from controlflow_sdk.upgrade.detect import InstallMethod


def test_pip_command():
    cmds = build_upgrade_command(InstallMethod.PIP, python="/py")
    assert cmds == [["/py", "-m", "pip", "install", "-U", "controlflow-sdk"]]


def test_pipx_command():
    cmds = build_upgrade_command(InstallMethod.PIPX)
    assert cmds == [["pipx", "upgrade", "controlflow-sdk"]]


def test_git_command_is_two_steps():
    cmds = build_upgrade_command(
        InstallMethod.GIT_EDITABLE, python="/py", source_dir="/repo"
    )
    assert cmds == [
        ["git", "-C", "/repo", "pull", "--ff-only"],
        ["/py", "-m", "pip", "install", "-e", "/repo"],
    ]


def test_git_without_source_dir_raises():
    with pytest.raises(ValueError):
        build_upgrade_command(InstallMethod.GIT_EDITABLE, source_dir=None)


def test_unknown_raises():
    with pytest.raises(ValueError):
        build_upgrade_command(InstallMethod.UNKNOWN)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/upgrade/test_command.py -q`
Expected: FAIL — `command` module not found.

- [ ] **Step 3: Write minimal implementation**

Create `controlflow_sdk/upgrade/command.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/upgrade/test_command.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit and push**

```bash
git add controlflow_sdk/upgrade/command.py tests/upgrade/test_command.py
git commit -m "feat(upgrade): build install-aware upgrade command"
git push -u origin HEAD
```

---

## Task 5: `upgrade/spawn.py` — detached helper, status, shutdown

**Files:**
- Create: `controlflow_sdk/upgrade/spawn.py`
- Test: `tests/upgrade/test_spawn.py`

**Interfaces:**
- Produces:
  - `STATUS_FILE = ".controlplane-upgrade.status"`, `LOG_FILE = ".controlplane-upgrade.log"`.
  - `write_status(project_root, payload: dict) -> None`.
  - `read_status(project_root) -> dict | None` — reads and **deletes** the status file (one-shot notice).
  - `_HELPER_SOURCE: str` — a self-contained, stdlib-only helper script (imports nothing from `controlflow_sdk`).
  - `spawn_detached_upgrade(project_root, commands: list[list[str]], *, current: str, popen=None) -> Path` — writes the helper to a temp file, spawns it detached, returns the helper path. `popen` is injectable for tests.
  - `schedule_shutdown(delay: float = 0.7, *, timer=None) -> None` — schedules a self-shutdown; `timer` is injectable for tests.

- [ ] **Step 1: Write the failing test**

Create `tests/upgrade/test_spawn.py`:

```python
import json

from controlflow_sdk.upgrade import spawn


def test_status_roundtrip_then_clears(tmp_path):
    spawn.write_status(tmp_path, {"ok": True, "from": "0.1.0"})
    assert spawn.read_status(tmp_path) == {"ok": True, "from": "0.1.0"}
    # read is one-shot — the file is gone, so a second read is None
    assert spawn.read_status(tmp_path) is None


def test_read_status_missing_is_none(tmp_path):
    assert spawn.read_status(tmp_path) is None


def test_helper_source_is_self_contained():
    # The detached helper must not import controlflow_sdk (the package may be
    # replaced under it) and must be valid Python.
    assert "import controlflow_sdk" not in spawn._HELPER_SOURCE
    compile(spawn._HELPER_SOURCE, "<helper>", "exec")


def test_spawn_writes_helper_and_invokes_popen(tmp_path):
    calls = {}

    def fake_popen(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return object()

    commands = [["pipx", "upgrade", "controlflow-sdk"]]
    helper = spawn.spawn_detached_upgrade(
        tmp_path, commands, current="0.1.0", popen=fake_popen
    )
    assert helper.exists()
    # argv[0] is the interpreter; argv[1] is the helper; argv[2] is JSON config.
    assert calls["argv"][1] == str(helper)
    cfg = json.loads(calls["argv"][2])
    assert cfg["commands"] == commands
    assert cfg["from"] == "0.1.0"
    assert cfg["status"].endswith(spawn.STATUS_FILE)


def test_schedule_shutdown_uses_injected_timer():
    fired = {}

    class FakeTimer:
        def __init__(self, delay, fn):
            fired["delay"] = delay
            fired["fn"] = fn

        def start(self):
            fired["started"] = True

    spawn.schedule_shutdown(0.3, timer=FakeTimer)
    assert fired["delay"] == 0.3
    assert fired["started"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/upgrade/test_spawn.py -q`
Expected: FAIL — `spawn` module not found.

- [ ] **Step 3: Write minimal implementation**

Create `controlflow_sdk/upgrade/spawn.py`:

```python
"""Spawn a detached helper that upgrades after the app exits, plus status I/O.

The helper waits for the control-plane process to fully exit before running the
upgrade, so no file being replaced is held open (the Windows-safe ordering). It
is written to a temp file as a self-contained, stdlib-only script that imports
nothing from controlflow_sdk — so the package can be freely replaced under it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

STATUS_FILE = ".controlplane-upgrade.status"
LOG_FILE = ".controlplane-upgrade.log"

# Self-contained: stdlib only, no controlflow_sdk imports. A 60s deadline is the
# backstop in case parent-PID detection is imperfect on a given platform.
_HELPER_SOURCE = r'''
import errno, json, os, subprocess, sys, time


def pid_alive(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    except Exception:
        return False
    return True


def main():
    cfg = json.loads(sys.argv[1])
    parent = cfg["parent_pid"]
    deadline = time.time() + 60
    while pid_alive(parent) and time.time() < deadline:
        time.sleep(0.25)
    ok = True
    with open(cfg["log"], "w") as log:
        for cmd in cfg["commands"]:
            log.write("$ " + " ".join(cmd) + "\n")
            log.flush()
            try:
                result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
            except Exception as exc:
                log.write("ERROR: %r\n" % (exc,))
                ok = False
                break
            if result.returncode != 0:
                ok = False
                break
    with open(cfg["status"], "w") as status:
        json.dump({"ok": ok, "from": cfg["from"]}, status)


main()
'''


def write_status(project_root: str | os.PathLike[str], payload: dict) -> None:
    (Path(project_root) / STATUS_FILE).write_text(json.dumps(payload))


def read_status(project_root: str | os.PathLike[str]) -> dict | None:
    path = Path(project_root) / STATUS_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    finally:
        path.unlink(missing_ok=True)


def spawn_detached_upgrade(
    project_root: str | os.PathLike[str],
    commands: list[list[str]],
    *,
    current: str,
    popen: Callable[..., Any] | None = None,
) -> Path:
    root = Path(project_root)
    helper = Path(tempfile.gettempdir()) / f"cflow_upgrade_{os.getpid()}.py"
    helper.write_text(_HELPER_SOURCE)
    cfg = json.dumps(
        {
            "parent_pid": os.getpid(),
            "log": str(root / LOG_FILE),
            "status": str(root / STATUS_FILE),
            "commands": commands,
            "from": current,
        }
    )
    argv = [sys.executable, str(helper), cfg]
    kwargs: dict[str, Any] = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:  # detach from the console so it survives the app exiting (Windows)
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_GROUP
    (popen or subprocess.Popen)(argv, **kwargs)
    return helper


def schedule_shutdown(
    delay: float = 0.7, *, timer: Callable[..., Any] | None = None
) -> None:
    import signal

    def _stop() -> None:
        try:
            os.kill(os.getpid(), signal.SIGINT)
        except Exception:
            os._exit(0)

    (timer or threading.Timer)(delay, _stop).start()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/upgrade/test_spawn.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit and push**

```bash
git add controlflow_sdk/upgrade/spawn.py tests/upgrade/test_spawn.py
git commit -m "feat(upgrade): detached upgrade helper, status I/O, self-shutdown"
git push -u origin HEAD
```

---

## Task 6: store — persist the opt-in toggle

**Files:**
- Modify: `controlflow_sdk/store/repo.py` (add two functions near `get_project`/`upsert_project`)
- Test: `tests/store/test_repo_update_setting.py`

**Interfaces:**
- Consumes: existing `get_project(conn)`, `upsert_project(conn, *, name, framework, system, created_at)`.
- Produces:
  - `get_check_updates_on_launch(conn: sqlite3.Connection) -> bool` — reads `system["check_updates_on_launch"]`, default `False`.
  - `set_check_updates_on_launch(conn: sqlite3.Connection, value: bool) -> None` — persists it into `system`, preserving name/framework/created_at and the rest of `system` (e.g. the AI selection).

- [ ] **Step 1: Write the failing test**

Create `tests/store/test_repo_update_setting.py`:

```python
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate


def _db(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    return conn


def test_default_is_false(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_project(conn, name="Acme")
    assert repo.get_check_updates_on_launch(conn) is False


def test_set_then_get_true(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_project(conn, name="Acme")
    repo.set_check_updates_on_launch(conn, True)
    assert repo.get_check_updates_on_launch(conn) is True


def test_toggle_preserves_other_system_keys(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_project(conn, name="Acme", system={"ai": {"provider": "openai"}})
    repo.set_check_updates_on_launch(conn, True)
    project = repo.get_project(conn)
    assert project["system"]["ai"] == {"provider": "openai"}
    assert project["system"]["check_updates_on_launch"] is True
    assert project["name"] == "Acme"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/store/test_repo_update_setting.py -q`
Expected: FAIL — `AttributeError: module 'controlflow_sdk.store.repo' has no attribute 'get_check_updates_on_launch'`.

- [ ] **Step 3: Write minimal implementation**

In `controlflow_sdk/store/repo.py`, add these two functions immediately after `get_project` (keep the existing imports; `get_project`/`upsert_project` already exist):

```python
def get_check_updates_on_launch(conn: sqlite3.Connection) -> bool:
    """Whether the control plane checks for a newer version on launch (default False)."""
    project = get_project(conn) or {}
    system = project.get("system") or {}
    return bool(system.get("check_updates_on_launch", False))


def set_check_updates_on_launch(conn: sqlite3.Connection, value: bool) -> None:
    """Persist the opt-in update-check toggle, preserving the rest of the project record."""
    project = get_project(conn) or {}
    system = dict(project.get("system") or {})
    system["check_updates_on_launch"] = bool(value)
    upsert_project(
        conn,
        name=project.get("name", "") or "",
        framework=project.get("framework"),
        system=system,
        created_at=project.get("created_at", "") or "",
    )
```

> If `sqlite3` is not already imported at the top of `repo.py`, the existing functions already type `conn: sqlite3.Connection`, so the import is present — do not add a duplicate.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/store/test_repo_update_setting.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit and push**

```bash
git add controlflow_sdk/store/repo.py tests/store/test_repo_update_setting.py
git commit -m "feat(store): persist opt-in check-updates-on-launch toggle"
git push -u origin HEAD
```

---

## Task 7: CLI — `cflow upgrade [--check] [--yes]`

**Files:**
- Create: `controlflow_sdk/cli/upgrade_cmd.py`
- Modify: `controlflow_sdk/cli/__init__.py` (add subparser in `_build_parser`, add dispatch in `main`)
- Test: `tests/cli/test_upgrade_cmd.py`

**Interfaces:**
- Consumes: `detect_install`, `source_dir`, `InstallMethod` (Task 2); `check_for_update` (Task 3); `build_upgrade_command` (Task 4).
- Produces: `upgrade_cmd(args: argparse.Namespace) -> int` — `--check` reports and exits 0; otherwise detects, confirms (unless `--yes`), runs the command(s) inline, returns 0 on success / non-zero on failure.

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_upgrade_cmd.py`:

```python
from controlflow_sdk.cli import main
from controlflow_sdk.upgrade.check import UpdateInfo
from controlflow_sdk.upgrade.detect import InstallMethod


def test_upgrade_check_reports_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(
        "controlflow_sdk.cli.upgrade_cmd.detect_install", lambda: InstallMethod.PIP
    )
    monkeypatch.setattr(
        "controlflow_sdk.cli.upgrade_cmd.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", "0.2.0", True, "Version 0.2.0 is available."),
    )
    rc = main(["upgrade", "--check"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "0.1.0" in out
    assert "0.2.0" in out


def test_upgrade_check_when_up_to_date(monkeypatch, capsys):
    monkeypatch.setattr(
        "controlflow_sdk.cli.upgrade_cmd.detect_install", lambda: InstallMethod.PIP
    )
    monkeypatch.setattr(
        "controlflow_sdk.cli.upgrade_cmd.check_for_update",
        lambda method: UpdateInfo(method, "0.2.0", "0.2.0", False, "You're on the latest version."),
    )
    rc = main(["upgrade", "--check"])
    assert rc == 0
    assert "latest" in capsys.readouterr().out.lower()


def test_upgrade_yes_runs_command(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(
        "controlflow_sdk.cli.upgrade_cmd.detect_install", lambda: InstallMethod.PIP
    )
    monkeypatch.setattr(
        "controlflow_sdk.cli.upgrade_cmd.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", "0.2.0", True, "Version 0.2.0 is available."),
    )
    monkeypatch.setattr(
        "controlflow_sdk.cli.upgrade_cmd.build_upgrade_command",
        lambda method, source_dir=None: [["pip", "install", "-U", "controlflow-sdk"]],
    )

    class FakeResult:
        returncode = 0

    monkeypatch.setattr(
        "controlflow_sdk.cli.upgrade_cmd.subprocess.run",
        lambda cmd: ran.append(cmd) or FakeResult(),
    )
    rc = main(["upgrade", "--yes"])
    assert rc == 0
    assert ran == [["pip", "install", "-U", "controlflow-sdk"]]


def test_upgrade_unknown_method_is_handled(monkeypatch, capsys):
    monkeypatch.setattr(
        "controlflow_sdk.cli.upgrade_cmd.detect_install", lambda: InstallMethod.UNKNOWN
    )
    monkeypatch.setattr(
        "controlflow_sdk.cli.upgrade_cmd.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", None, False, "Automatic upgrade isn't available."),
    )
    rc = main(["upgrade", "--yes"])
    assert rc != 0
    assert "isn't available" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/cli/test_upgrade_cmd.py -q`
Expected: FAIL — `argument <command>: invalid choice: 'upgrade'` (subparser not registered).

- [ ] **Step 3: Write minimal implementation**

Create `controlflow_sdk/cli/upgrade_cmd.py`:

```python
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
    if not info.available:
        return 0
    if method is InstallMethod.UNKNOWN:
        print("Automatic upgrade isn't available for this install — see docs/INSTALL.md.")
        return 1
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
```

In `controlflow_sdk/cli/__init__.py`, add the subparser inside `_build_parser()` just before `return parser` (after the `build` subparser block):

```python
    # -- upgrade -------------------------------------------------------------
    upgrade_p = sub.add_parser(
        "upgrade",
        help="Check for and install controlflow-sdk updates (install-aware).",
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
```

And add dispatch in `main()` (alongside the other `if args.command == ...` blocks, before the `parser.print_help()` fallthrough):

```python
    if args.command == "upgrade":
        from controlflow_sdk.cli.upgrade_cmd import upgrade_cmd

        return upgrade_cmd(args)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/cli/test_upgrade_cmd.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit and push**

```bash
git add controlflow_sdk/cli/upgrade_cmd.py controlflow_sdk/cli/__init__.py tests/cli/test_upgrade_cmd.py
git commit -m "feat(cli): cflow upgrade [--check] [--yes]"
git push -u origin HEAD
```

---

## Task 8: plane — Settings ▸ Updates page (toggle + check now)

**Files:**
- Create: `controlflow_sdk/plane/routes/updates.py`
- Modify: `controlflow_sdk/plane/app.py` (import + register `updates`)
- Create: `controlflow_sdk/plane/templates/settings_updates.html`
- Create: `controlflow_sdk/plane/templates/partials/update_result.html`
- Modify: `controlflow_sdk/plane/templates/settings.html` (add an "Updates" card)
- Test: `tests/plane/test_settings_updates.py`

**Interfaces:**
- Consumes: `repo.get_check_updates_on_launch` / `set_check_updates_on_launch` (Task 6); `detect_install`, `InstallMethod` (Task 2); `current_version`, `check_for_update`, `UpdateInfo` (Task 3).
- Produces a `register(app, templates, get_conn)` exporting:
  - `GET /settings/updates` → renders `settings_updates.html`.
  - `POST /settings/updates/toggle` → persists the checkbox, 303 → `/settings/updates`.
  - `POST /settings/updates/check` → returns the `partials/update_result.html` partial (network check; explicit user action).

- [ ] **Step 1: Write the failing test**

Create `tests/plane/test_settings_updates.py`:

```python
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.upgrade.check import UpdateInfo
from controlflow_sdk.upgrade.detect import InstallMethod


def test_settings_hub_links_to_updates(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert 'href="/settings/updates"' in resp.text


def test_updates_page_renders_with_toggle_off_by_default(client):
    resp = client.get("/settings/updates")
    assert resp.status_code == 200
    assert "Check for updates" in resp.text
    # Unchecked by default.
    assert "check_on_launch" in resp.text


def test_toggle_persists_true(client):
    resp = client.post(
        "/settings/updates/toggle",
        data={"check_on_launch": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    conn = connect(client.app.state.project_root)
    assert repo.get_check_updates_on_launch(conn) is True
    conn.close()


def test_toggle_unchecked_persists_false(client):
    conn = connect(client.app.state.project_root)
    repo.set_check_updates_on_launch(conn, True)
    conn.close()
    # An unchecked checkbox submits no field at all.
    resp = client.post("/settings/updates/toggle", data={}, follow_redirects=False)
    assert resp.status_code == 303
    conn = connect(client.app.state.project_root)
    assert repo.get_check_updates_on_launch(conn) is False
    conn.close()


def test_check_now_returns_result_partial(client, monkeypatch):
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.detect_install",
        lambda: InstallMethod.PIP,
    )
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", "0.2.0", True, "Version 0.2.0 is available."),
    )
    resp = client.post("/settings/updates/check")
    assert resp.status_code == 200
    assert "0.2.0" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/plane/test_settings_updates.py -q`
Expected: FAIL — `/settings/updates` 404 (route not registered).

- [ ] **Step 3: Write minimal implementation**

Create `controlflow_sdk/plane/routes/updates.py`:

```python
"""Settings ▸ Updates: the opt-in update check + (Task 9) the upgrade trigger.

Egress discipline: the launch/badge check only runs when the toggle is ON; the
"Check now" button is an explicit user action and may run regardless. No route
makes a network call while the toggle is OFF (zero-egress default — STRATEGY.md).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.upgrade.check import check_for_update, current_version
from controlflow_sdk.upgrade.detect import InstallMethod, detect_install


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    @app.get("/settings/updates", response_class=HTMLResponse)
    def updates_home(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        method = detect_install()
        return templates.TemplateResponse(
            request,
            "settings_updates.html",
            {
                "project": repo.get_project(conn) or {"name": ""},
                "active": "updates",
                "check_on_launch": repo.get_check_updates_on_launch(conn),
                "current": current_version(),
                "method": method.value,
                "can_self_upgrade": method is not InstallMethod.UNKNOWN,
            },
        )

    @app.post("/settings/updates/toggle")
    async def toggle_updates(request: Request) -> RedirectResponse:
        form = await request.form()
        value = form.get("check_on_launch") is not None
        conn = connect(request.app.state.project_root)  # per-handler conn (0002)
        try:
            repo.set_check_updates_on_launch(conn, value)
        finally:
            conn.close()
        return RedirectResponse(url="/settings/updates", status_code=303)

    @app.post("/settings/updates/check", response_class=HTMLResponse)
    def check_now(request: Request) -> Any:
        method = detect_install()
        info = check_for_update(method)
        request.app.state.update_check = info  # cache for the dashboard badge
        return templates.TemplateResponse(
            request, "partials/update_result.html", {"info": info}
        )
```

In `controlflow_sdk/plane/app.py`, add `updates` to the route imports and register it after `settings` (so the `/settings/updates` routes sit alongside `/settings`):

```python
    from controlflow_sdk.plane.routes import (
        ai,
        controls,
        dashboard,
        export,
        pipeline,
        runs,
        settings,
        setup,
        sources,
        updates,
    )
```

```python
    settings.register(app, templates, get_conn)
    updates.register(app, templates, get_conn)
```

Create `controlflow_sdk/plane/templates/settings_updates.html`:

```html
{% extends "base.html" %}
{% block title %}{{ project.name }} — Updates{% endblock %}
{% block body %}
<a class="crumb" href="/settings">← Settings</a>
<div class="page-head">
  <h1>Updates</h1>
</div>

<div class="card">
  <h2>Version</h2>
  <p class="lead">
    Installed version <code class="mono">{{ current }}</code>
    <span class="muted">({{ method }} install)</span>.
  </p>

  <form method="post" action="/settings/updates/toggle">
    <div class="field">
      <label style="display:flex; align-items:center; gap:8px;">
        <input type="checkbox" name="check_on_launch" {% if check_on_launch %}checked{% endif %}>
        <span class="src-name">Check for updates when the app starts</span>
      </label>
      <span class="hint">
        Off by default — keeps the control plane's zero network egress. When on, the
        app asks your configured package index for the latest version on launch.
      </span>
    </div>
    <div class="page-actions">
      <button class="btn btn-primary" type="submit">Save</button>
    </div>
  </form>
</div>

<div class="card">
  <h2>Check now</h2>
  <p class="lead">A one-off check — runs whether or not the launch check is on.</p>
  <div class="page-actions">
    <button class="btn" hx-post="/settings/updates/check"
            hx-target="#check-result" hx-swap="innerHTML">Check for updates</button>
  </div>
  <div id="check-result"></div>
</div>
{% endblock %}
```

Create `controlflow_sdk/plane/templates/partials/update_result.html`:

```html
<div class="card" style="margin-top:14px;">
  <p class="lead">{{ info.message }}</p>
  {% if info.available and info.method.value != 'unknown' %}
  <form method="post" action="/upgrade"
        hx-post="/upgrade" hx-target="body" hx-swap="innerHTML"
        hx-confirm="This will close the app and upgrade controlflow-sdk. Continue?">
    <button class="btn btn-primary" type="submit">Update now</button>
    <a class="btn btn-ghost" target="_blank" rel="noopener"
       href="https://github.com/dom-schweyer-tech/controlflow-sdk/releases">What's new</a>
  </form>
  {% endif %}
</div>
```

> `/upgrade` is added in Task 9; the button targets it. Until then the partial still renders (the form just posts to a not-yet-registered route), which is fine for this task's tests (they assert on the message text).

In `controlflow_sdk/plane/templates/settings.html`, add an Updates card immediately after the AI-assisted authoring card (before the final `{% endblock %}`):

```html
<div class="card">
  <h2>Updates</h2>
  <p class="lead">
    See the installed version and upgrade in place. An opt-in launch check (off by
    default) can surface a banner when a newer version is available.
  </p>
  <div class="page-actions">
    <a class="btn" href="/settings/updates">Manage updates</a>
  </div>
</div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/plane/test_settings_updates.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit and push**

```bash
git add controlflow_sdk/plane/routes/updates.py controlflow_sdk/plane/app.py \
        controlflow_sdk/plane/templates/settings_updates.html \
        controlflow_sdk/plane/templates/partials/update_result.html \
        controlflow_sdk/plane/templates/settings.html \
        tests/plane/test_settings_updates.py
git commit -m "feat(plane): Settings ▸ Updates page with opt-in check + check-now"
git push -u origin HEAD
```

---

## Task 9: plane — dashboard badge + one-click upgrade

**Files:**
- Modify: `controlflow_sdk/plane/routes/updates.py` (add `/updates/badge` GET + `/upgrade` POST)
- Modify: `controlflow_sdk/plane/routes/dashboard.py` (surface the post-upgrade status notice)
- Create: `controlflow_sdk/plane/templates/partials/update_badge.html`
- Create: `controlflow_sdk/plane/templates/upgrading.html`
- Create: `controlflow_sdk/plane/templates/upgrade_unavailable.html`
- Modify: `controlflow_sdk/plane/templates/dashboard.html` (badge include + notice)
- Test: `tests/plane/test_dashboard_upgrade.py`

**Interfaces:**
- Consumes: everything from Task 8 plus `source_dir` (Task 2), `build_upgrade_command` (Task 4), `spawn_detached_upgrade` + `schedule_shutdown` + `read_status` (Task 5).
- Produces (added to the same `updates.register`):
  - `GET /updates/badge` → empty body when the toggle is OFF or no update; the `partials/update_badge.html` partial when ON and a newer version is cached/available.
  - `POST /upgrade` → for `UNKNOWN`, renders `upgrade_unavailable.html`; otherwise spawns the detached upgrade, schedules shutdown, renders `upgrading.html`.
- `dashboard.py` now passes `upgrade_notice = read_status(project_root)` into `dashboard.html`.

- [ ] **Step 1: Write the failing test**

Create `tests/plane/test_dashboard_upgrade.py`:

```python
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.upgrade.check import UpdateInfo
from controlflow_sdk.upgrade.detect import InstallMethod


def _enable_check(client):
    conn = connect(client.app.state.project_root)
    repo.set_check_updates_on_launch(conn, True)
    conn.close()


def test_badge_empty_when_toggle_off(client, monkeypatch):
    # Even if a check WOULD find an update, OFF means no badge and no network.
    called = {"n": 0}

    def boom(method):
        called["n"] += 1
        return UpdateInfo(method, "0.1.0", "0.2.0", True, "x")

    monkeypatch.setattr("controlflow_sdk.plane.routes.updates.check_for_update", boom)
    resp = client.get("/updates/badge")
    assert resp.status_code == 200
    assert resp.text.strip() == ""
    assert called["n"] == 0  # no check ran while OFF


def test_badge_shows_when_on_and_newer(client, monkeypatch):
    _enable_check(client)
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.detect_install",
        lambda: InstallMethod.PIP,
    )
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", "0.2.0", True, "Version 0.2.0 is available."),
    )
    resp = client.get("/updates/badge")
    assert resp.status_code == 200
    assert "0.2.0" in resp.text
    assert "/upgrade" in resp.text


def test_upgrade_spawns_and_renders_upgrading(client, monkeypatch):
    spawned = {}
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.detect_install",
        lambda: InstallMethod.PIP,
    )
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.build_upgrade_command",
        lambda method, source_dir=None: [["pip", "install", "-U", "controlflow-sdk"]],
    )
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.spawn_detached_upgrade",
        lambda root, commands, current: spawned.update(commands=commands) or None,
    )
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.schedule_shutdown",
        lambda: spawned.update(shutdown=True),
    )
    resp = client.post("/upgrade")
    assert resp.status_code == 200
    assert "Upgrading" in resp.text
    assert spawned["commands"] == [["pip", "install", "-U", "controlflow-sdk"]]
    assert spawned["shutdown"] is True


def test_upgrade_unknown_renders_instructions(client, monkeypatch):
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.detect_install",
        lambda: InstallMethod.UNKNOWN,
    )
    resp = client.post("/upgrade")
    assert resp.status_code == 200
    assert "pip install" in resp.text.lower() or "pipx" in resp.text.lower()


def test_dashboard_shows_post_upgrade_notice(client):
    from controlflow_sdk.upgrade.spawn import write_status

    write_status(client.app.state.project_root, {"ok": True, "from": "0.1.0"})
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Upgraded" in resp.text
    # One-shot: the notice clears after being shown once.
    assert "Upgraded" not in client.get("/").text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/plane/test_dashboard_upgrade.py -q`
Expected: FAIL — `/updates/badge` 404.

- [ ] **Step 3: Write minimal implementation**

In `controlflow_sdk/plane/routes/updates.py`, extend the imports at the top:

```python
from controlflow_sdk.upgrade.check import check_for_update, current_version
from controlflow_sdk.upgrade.command import build_upgrade_command
from controlflow_sdk.upgrade.detect import InstallMethod, detect_install, source_dir
from controlflow_sdk.upgrade.spawn import schedule_shutdown, spawn_detached_upgrade
```

Then add these two routes inside `register(...)` (after `check_now`):

```python
    @app.get("/updates/badge", response_class=HTMLResponse)
    def update_badge(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        # OFF → zero egress, no badge.
        if not repo.get_check_updates_on_launch(conn):
            return HTMLResponse("")
        info = getattr(request.app.state, "update_check", None)
        if info is None:
            info = check_for_update(detect_install())
            request.app.state.update_check = info
        if not info.available:
            return HTMLResponse("")
        return templates.TemplateResponse(
            request, "partials/update_badge.html", {"info": info}
        )

    @app.post("/upgrade", response_class=HTMLResponse)
    def do_upgrade(request: Request) -> Any:
        method = detect_install()
        current = current_version()
        if method is InstallMethod.UNKNOWN:
            return templates.TemplateResponse(
                request, "upgrade_unavailable.html", {"current": current}
            )
        src = source_dir() if method is InstallMethod.GIT_EDITABLE else None
        commands = build_upgrade_command(method, source_dir=str(src) if src else None)
        spawn_detached_upgrade(request.app.state.project_root, commands, current=current)
        schedule_shutdown()
        return templates.TemplateResponse(request, "upgrading.html", {"current": current})
```

In `controlflow_sdk/plane/routes/dashboard.py`, import `read_status` and pass the notice into the template. Update the dashboard handler's `TemplateResponse` context (the handler currently returns `{"project": project, "rows": rows}`):

```python
from controlflow_sdk.upgrade.spawn import read_status
```

```python
    notice = read_status(request.app.state.project_root)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"project": project, "rows": rows, "upgrade_notice": notice},
    )
```

Create `controlflow_sdk/plane/templates/partials/update_badge.html`:

```html
<div class="card" style="border-color:var(--accent); margin-bottom:18px;">
  <div class="card-head" style="border:none; padding:0; margin:0;">
    <h2 style="margin:0;">⬆ Update available: {{ info.latest }}</h2>
    <div class="spacer"></div>
    <a class="btn btn-ghost btn-sm" target="_blank" rel="noopener"
       href="https://github.com/dom-schweyer-tech/controlflow-sdk/releases">What's new</a>
    <button class="btn btn-primary btn-sm"
            hx-post="/upgrade" hx-target="body" hx-swap="innerHTML"
            hx-confirm="This will close the app and upgrade controlflow-sdk. Continue?">
      Update now
    </button>
  </div>
  <p class="lead" style="margin-bottom:0;">{{ info.message }}</p>
</div>
```

Create `controlflow_sdk/plane/templates/upgrading.html`:

```html
{% extends "base.html" %}
{% block title %}Upgrading…{% endblock %}
{% block body %}
<div class="page-head">
  <h1>Upgrading…</h1>
</div>
<div class="card">
  <p class="lead">
    The upgrade is running in the background and this app is shutting down.
    When it finishes, start it again:
  </p>
  <pre class="mono"><code>controlplane --project &lt;your engagement dir&gt;</code></pre>
  <p class="hint">
    Progress is logged to <code class="mono">.controlplane-upgrade.log</code> in the
    engagement folder. You can close this tab.
  </p>
</div>
{% endblock %}
```

Create `controlflow_sdk/plane/templates/upgrade_unavailable.html`:

```html
{% extends "base.html" %}
{% block title %}Upgrade{% endblock %}
{% block body %}
<a class="crumb" href="/settings/updates">← Updates</a>
<div class="page-head">
  <h1>Manual upgrade</h1>
</div>
<div class="card">
  <p class="lead">
    Automatic upgrade isn't available for this install (installed version
    <code class="mono">{{ current }}</code>). Upgrade from a terminal:
  </p>
  <pre class="mono"><code>pipx upgrade controlflow-sdk        # if installed with pipx
pip install -U controlflow-sdk     # if installed with pip</code></pre>
  <p class="hint">See <code class="mono">docs/INSTALL.md</code> for offline / air-gapped upgrades.</p>
</div>
{% endblock %}
```

In `controlflow_sdk/plane/templates/dashboard.html`, add the notice + badge at the very top of the `{% block body %}` (immediately after the `{% block body %}` line):

```html
{% if upgrade_notice %}
<div class="card {{ 'pass' if upgrade_notice.ok else 'fail' }}" style="margin-bottom:18px;">
  <p class="lead" style="margin:0;">
    {% if upgrade_notice.ok %}
      ✓ Upgraded from version {{ upgrade_notice.from }}. You're now on the latest installed code.
    {% else %}
      ⚠ Upgrade failed — see <code class="mono">.controlplane-upgrade.log</code> in the engagement folder.
    {% endif %}
  </p>
</div>
{% endif %}
<div hx-get="/updates/badge" hx-trigger="load" hx-swap="outerHTML"></div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/plane/test_dashboard_upgrade.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Run the full plane + upgrade suites to catch regressions**

Run: `python -m pytest tests/plane tests/upgrade tests/cli tests/store -q`
Expected: PASS (no regressions in existing plane tests).

- [ ] **Step 6: Commit and push**

```bash
git add controlflow_sdk/plane/routes/updates.py controlflow_sdk/plane/routes/dashboard.py \
        controlflow_sdk/plane/templates/partials/update_badge.html \
        controlflow_sdk/plane/templates/upgrading.html \
        controlflow_sdk/plane/templates/upgrade_unavailable.html \
        controlflow_sdk/plane/templates/dashboard.html \
        tests/plane/test_dashboard_upgrade.py
git commit -m "feat(plane): dashboard update badge + one-click self-upgrade"
git push -u origin HEAD
```

---

## Task 10: docs, product map, changelog

**Files:**
- Modify: `README.md` (lines ~9 and ~214–215 — egress reword)
- Modify: `docs/INSTALL.md` (line ~147–148 egress reword + a new "Upgrading" section)
- Modify: `PRODUCT-MAP.md` (add a row)
- Modify: `CHANGELOG.md` (add an `[Unreleased]` entry)
- Test: none (docs only) — but verify no test asserts the old egress phrasing.

**Interfaces:** none (documentation).

- [ ] **Step 1: Verify nothing asserts the exact old egress phrasing**

Run: `grep -rn "never makes outbound\|zero network egress" tests/ || echo "no test depends on the phrase"`
Expected: `no test depends on the phrase` (if any test matches, update it to the new phrasing in Step 2).

- [ ] **Step 2: Reword the egress lines (honest carve-out for the opt-in check)**

In `README.md`, change the About paragraph (line ~9) from:

```
on `127.0.0.1`, zero network egress — where you author sources and controls through a browser UI, run
```

to:

```
on `127.0.0.1`, zero network egress by default — where you author sources and controls through a browser UI, run
```

In `README.md`, change the design-principles bullet (lines ~214–215) from:

```
- **Localhost only, zero network egress.** `controlplane` listens on `127.0.0.1:8765` and never
  makes outbound connections. Client data never leaves the machine.
```

to:

```
- **Localhost only, zero network egress by default.** `controlplane` listens on `127.0.0.1:8765` and
  makes no outbound connections — the one exception is an **opt-in** update check (Settings ▸ Updates,
  off by default) that, only when you enable it, asks your configured package index for the latest
  version number. Client data never leaves the machine.
```

In `docs/INSTALL.md`, change lines ~147–148 from:

```
The control plane is **localhost-only with zero network egress** — it listens on `127.0.0.1:8765`
and never makes outbound connections, so client data never leaves the machine (see the
```

to:

```
The control plane is **localhost-only with zero network egress by default** — it listens on
`127.0.0.1:8765` and makes no outbound connections except an **opt-in** update check (off by default;
see [Upgrading](#upgrading)), so client data never leaves the machine (see the
```

- [ ] **Step 3: Add an "Upgrading" section to `docs/INSTALL.md`**

Insert this section immediately before the existing `## Launching` heading:

```markdown
## Upgrading

The control plane is **install-aware** — it upgrades itself the right way for how you installed it.

**From the app.** Open **Settings ▸ Updates**. Turn on *"Check for updates when the app starts"*
(off by default — leaving it off keeps the zero-egress default) to get a banner when a newer version
is available, or click **Check for updates** any time. Click **Update now** to upgrade: the app runs
the upgrade in a detached helper and shuts down — re-run `controlplane` when it finishes (progress is
logged to `.controlplane-upgrade.log` in the engagement folder).

**From the terminal.** The same routine is available headless:

```bash
cflow upgrade --check     # report installed vs latest, change nothing
cflow upgrade             # detect the install method and upgrade (asks to confirm)
cflow upgrade --yes       # upgrade without the prompt
```

`cflow upgrade` picks the command for your install: a git checkout does
`git pull --ff-only` + an editable reinstall; a `pipx` install does `pipx upgrade controlflow-sdk`;
a `pip` install does `pip install -U controlflow-sdk` (honouring your configured index). Air-gapped /
pinned-wheel installs can't self-upgrade — re-run the [pinned-wheel](#option-3--pinned-wheel-air-gapped--no-index-reachable)
steps with the new wheel.
```

- [ ] **Step 4: Add a `PRODUCT-MAP.md` row**

Add this row to the surface table (after the **Settings** row):

```markdown
| Control plane — Updates / upgrade | view + action | **Settings ▸ Updates** shows the installed version + detected install method and an **opt-in** "check for updates on launch" toggle (default OFF — preserves zero egress) plus a manual **Check now**. When a newer version exists, a dashboard banner offers **one-click self-upgrade**: the app detects the install method (git checkout → `git pull` + reinstall · pipx → `pipx upgrade` · pip → `pip install -U`), spawns a detached helper, and shuts down for a manual re-run. The same routine is the `cflow upgrade [--check] [--yes]` CLI. No bundle impact. |
```

- [ ] **Step 5: Add a `CHANGELOG.md` `[Unreleased]` entry**

Insert immediately after the `---` on line 10 (before `## [0.1.0] — 2026-06-16`):

```markdown
## [Unreleased]

### Added

- **Upgrade & update-awareness.** The control plane detects how it was installed and can upgrade
  itself in one click — git checkout → `git pull --ff-only` + editable reinstall · pipx →
  `pipx upgrade` · pip → `pip install -U`. The same routine is available headless as
  `cflow upgrade [--check] [--yes]`. An **opt-in** "check for updates on launch" toggle
  (Settings ▸ Updates, **off by default**) preserves the control plane's zero-egress default.

---
```

- [ ] **Step 6: Commit and push**

```bash
git add README.md docs/INSTALL.md PRODUCT-MAP.md CHANGELOG.md
git commit -m "docs: upgrading guide + honest egress reword + product-map/changelog"
git push -u origin HEAD
```

---

## Task 11: full gate + manual-verification note

**Files:**
- Modify: `docs/superpowers/specs/2026-06-22-control-plane-upgrade-design.md` (append a manual-verification checklist)
- No code changes.

**Interfaces:** none.

- [ ] **Step 1: Run the full fast suite**

Run: `python -m pytest -q`
Expected: PASS — all prior tests plus the new `tests/upgrade`, `tests/cli/test_upgrade_cmd.py`, `tests/store/test_repo_update_setting.py`, `tests/plane/test_settings_updates.py`, `tests/plane/test_dashboard_upgrade.py`. Output pristine (no warnings).

- [ ] **Step 2: Lint + type gates**

Run: `python -m ruff check .`
Expected: PASS (no findings).

Run: `python -m mypy controlflow_sdk`
Expected: PASS (no new errors).

Fix any findings inline, then re-run until both are green.

- [ ] **Step 3: Append a manual-verification checklist to the spec**

The detached spawn + shutdown + relaunch is not exercised by automated tests (no test mutates the runtime environment). Append this section to the design doc so it's verified by hand once:

```markdown
## Manual verification (one-time, by hand)

Self-upgrade mutates the environment, so it is verified manually, not in CI:

1. **pip path** — in a throwaway venv, `pip install -e '.[plane]'` is detected as `git-editable`;
   confirm `cflow upgrade --check` reports the version + method. (For a true `pip` test, install a
   built wheel into a plain venv and confirm `pip install -U` is the chosen command.)
2. **Web button** — launch `controlplane`, enable the launch check, force the badge (or use
   "Check now" with a fake newer version), click **Update now**: the app shows the "Upgrading…"
   page and exits; `.controlplane-upgrade.log` records the command; re-running `controlplane`
   shows the one-shot "Upgraded from …" notice, which clears on the next load.
3. **Air-gapped** — with no index reachable, "Check now" degrades to "couldn't check" and the
   dashboard never blocks; `UNKNOWN` installs show manual instructions, never a dead button.
```

- [ ] **Step 4: Commit and push**

```bash
git add docs/superpowers/specs/2026-06-22-control-plane-upgrade-design.md
git commit -m "docs(spec): manual-verification checklist for self-upgrade"
git push -u origin HEAD
```

- [ ] **Step 5: Open the PR**

```bash
gh pr create --fill --base main --head worktree-control-plane-upgrade
```

Then post the body summarizing: install-aware upgrade button + `cflow upgrade` CLI, opt-in egress (default OFF), no bundle impact, issue #11. Confirm CI is green.

---

## Self-Review

**Spec coverage** (each spec section → task):
- Install detection (§Architecture `detect.py`) → **Task 2**.
- Update check + opt-in + zero-egress (§3) → **Task 3** (check), **Task 6** (toggle persistence), **Task 8** (settings page/toggle/check), **Task 9** (badge gating: OFF ⇒ empty, no network), **Task 10** (egress reword).
- Self-upgrade dance (§4: detached stdlib helper, wait-for-PID, status, manual re-run) → **Task 5** + **Task 9** (`/upgrade` + dashboard notice).
- Install-aware command incl. git (§Architecture `command.py`) → **Task 4**.
- `cflow upgrade [--check] [--yes]` (§CLI) → **Task 7**.
- Web surface routes + templates (§Web surface) → **Tasks 8 & 9**.
- Version compare without `packaging` (§6) → **Task 1**.
- Safety/security (§Safety): explicit action + confirm (`hx-confirm` in Tasks 8/9), respect configured index (`pip index versions` in Task 3, delegate to pip/pipx in Task 4), short timeouts + graceful degrade (Task 3) → covered.
- Testing strategy (§Testing): injected fakes, no real network/subprocess, decision-branch tests with spawn/shutdown mocked, manual cap → Tasks 1–9 + **Task 11**.
- Docs/map/changelog (§Docs) → **Task 10**; manual-verification note → **Task 11**.
- Cardinal rule (no bundle change) → no task touches `contract/` or `bundle/`; stated in Global Constraints.

**Placeholder scan:** no TBD/TODO/"add error handling"/"similar to Task N" — every code step shows full code; every test step shows full assertions.

**Type consistency:** `InstallMethod` members and `.value` strings are consistent across Tasks 2/3/4/7/8/9; `build_upgrade_command` returns `list[list[str]]` everywhere it's consumed (Tasks 5, 7, 9); `UpdateInfo` field names (`method/current/latest/available/message`) match across Tasks 3/7/8/9; `check_for_update(method, *, fetch, git_run)`, `spawn_detached_upgrade(project_root, commands, *, current, popen)`, and `schedule_shutdown(delay, *, timer)` signatures match their call sites and monkeypatch targets. The toggle field name `check_on_launch` (form) and `check_updates_on_launch` (storage key) are used consistently per layer.
