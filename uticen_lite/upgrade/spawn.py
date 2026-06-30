"""Spawn a detached helper that upgrades after the app exits, plus status I/O.

The helper waits for the control-plane process to fully exit before running the
upgrade, so no file being replaced is held open (the Windows-safe ordering). It
is written to a temp file as a self-contained, stdlib-only script that imports
nothing from uticen_lite — so the package can be freely replaced under it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

STATUS_FILE = ".controlplane-upgrade.status"
LOG_FILE = ".controlplane-upgrade.log"

# Self-contained: stdlib only, no uticen_lite imports. A 60s deadline is the
# backstop in case parent-PID detection is imperfect on a given platform.
_HELPER_SOURCE = r"""
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
    restarted = False
    restart = cfg.get("restart_command")
    if ok and restart:
        kwargs = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = 0x00000008 | 0x00000200
        try:
            subprocess.Popen(
                restart,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                **kwargs,
            )
            restarted = True
        except Exception as exc:
            with open(cfg["log"], "a") as log:
                log.write("RESTART ERROR: %r\\n" % (exc,))
    with open(cfg["status"], "w") as status:
        json.dump({"ok": ok, "from": cfg["from"], "restarted": restarted}, status)


main()
"""


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
    restart_command: list[str] | None = None,
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
            "restart_command": restart_command,
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


def schedule_shutdown(delay: float = 0.7, *, timer: Callable[..., Any] | None = None) -> None:
    import signal

    def _stop() -> None:
        try:
            os.kill(os.getpid(), signal.SIGINT)
        except Exception:
            os._exit(0)

    (timer or threading.Timer)(delay, _stop).start()
