import sqlite3
from collections.abc import Callable, Generator
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from uticen_lite.model.control import Threshold
from uticen_lite.model.workpaper import Determination
from uticen_lite.store import repo
from uticen_lite.upgrade.spawn import read_status


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    @app.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    ) -> HTMLResponse:
        project = repo.get_project(conn) or {"name": ""}
        # First run: no engagement name yet → show the onboarding screen instead of
        # an empty dashboard (issue #11).
        if not project.get("name"):
            return templates.TemplateResponse(request, "setup.html", {"project": project})
        controls = repo.list_controls(conn)
        rows = []
        for c in controls:
            latest = repo.latest_run(conn, c["id"])
            # The last-run badge passes/fails by the control's threshold — the same
            # determination the workpaper uses — never raw `failed == 0` (audit A1).
            passed = None
            if latest is not None:
                threshold = Threshold(
                    failure_threshold_pct=c.get("failure_threshold_pct"),
                    failure_threshold_count=c.get("failure_threshold_count"),
                )
                passed = Determination(threshold, latest["failed"], latest["total"]).passed
            rows.append({"control": c, "latest": latest, "passed": passed})
        notice = read_status(request.app.state.project_root)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"project": project, "rows": rows, "upgrade_notice": notice},
        )
