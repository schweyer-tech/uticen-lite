"""Settings ▸ Updates: the opt-in update check + (Task 9) the upgrade trigger.

Egress discipline: the launch/badge check only runs when the toggle is ON; the
"Check now" button is an explicit user action and may run regardless. No route
makes a network call while the toggle is OFF (zero-egress default — STRATEGY.md).
"""

from __future__ import annotations

import shlex
import sqlite3
from collections.abc import Callable, Generator
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.upgrade.check import check_for_update, current_version
from controlflow_sdk.upgrade.command import build_upgrade_command
from controlflow_sdk.upgrade.detect import InstallMethod, detect_install, source_dir
from controlflow_sdk.upgrade.spawn import schedule_shutdown, spawn_detached_upgrade


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
        # shlex.quote so the copyable re-run command is paste-and-run even when the
        # engagement path contains spaces.
        project_dir = shlex.quote(str(request.app.state.project_root))
        return templates.TemplateResponse(
            request, "upgrading.html", {"current": current, "project_dir": project_dir}
        )
