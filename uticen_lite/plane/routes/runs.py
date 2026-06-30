from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Generator
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from uticen_lite.model.control import Threshold
from uticen_lite.model.workpaper import Determination
from uticen_lite.project.loader import ProjectError
from uticen_lite.runner.execute import RunnerError
from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.run_service import run_control_in_store

logger = logging.getLogger(__name__)


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    def _run_error(
        request: Request, conn: sqlite3.Connection, control_id: str, message: str
    ) -> HTMLResponse:
        """Render the friendly 'not ready / couldn't run' page (HTTP 422)."""
        try:
            project = repo.get_project(conn) or {"name": ""}
        except Exception:  # pragma: no cover - the project read must never itself 500
            project = {"name": ""}
        return templates.TemplateResponse(
            request,
            "run_error.html",
            {"project": project, "control_id": control_id, "message": message},
            status_code=422,
        )

    @app.post("/controls/{control_id}/run")
    def run(control_id: str, request: Request) -> Response:
        root = request.app.state.project_root
        executed_at = datetime.now(UTC).isoformat()
        conn = connect(root)
        try:
            rec = run_control_in_store(conn, root, control_id, executed_at)
        except (RunnerError, ProjectError, KeyError, IndexError) as exc:
            # A half-authored control (no bound source, no logic) or corrupted state
            # must degrade to a friendly "not ready" page — never a 500 (2026-06-27).
            return _run_error(request, conn, control_id, str(exc))
        except Exception as exc:
            # Backstop: the Run button must NEVER return 500. Log the full traceback
            # server-side so real bugs stay visible, then degrade to the same page.
            logger.exception("Unexpected error running control %r", control_id)
            return _run_error(request, conn, control_id, str(exc))
        finally:
            conn.close()
        return RedirectResponse(f"/controls/{control_id}/runs/{rec.run_id}", status_code=303)

    @app.get("/controls/{control_id}/runs/{run_id}", response_class=HTMLResponse)
    def run_view(
        control_id: str,
        run_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),  # noqa: FAST002
    ) -> HTMLResponse:
        # A 'never raises' GET wraps its whole body, including the pre-render loads,
        # so a missing run record or a workpaper-read failure degrades to the
        # friendly page instead of a 500 (learning 0013).
        root = request.app.state.project_root
        try:
            run = repo.get_run(conn, run_id)
            if run is None:
                raise KeyError(f"no run {run_id!r}")
            wp_path = root / "target" / "workpapers" / f"{control_id}.html"
            workpaper_html = wp_path.read_text(encoding="utf-8") if wp_path.exists() else ""
            # Derive the verdict from the SAME threshold the embedded workpaper uses,
            # never from `failed == 0` — otherwise a run within tolerance is branded a
            # deficiency here while the workpaper calls it effective (audit finding A1).
            control = repo.get_control(conn, control_id)
            threshold = Threshold(
                failure_threshold_pct=(control or {}).get("failure_threshold_pct"),
                failure_threshold_count=(control or {}).get("failure_threshold_count"),
            )
            determination = Determination(threshold, run["failed"], run["total"])
            threshold_text, _ = determination.conclusion_text()
            return templates.TemplateResponse(
                request,
                "run_view.html",
                {
                    "project": repo.get_project(conn) or {"name": ""},
                    "control_id": control_id,
                    "run": run,
                    "passed": determination.passed,
                    "verdict": determination.verdict,
                    "threshold_text": threshold_text,
                    "workpaper_html": workpaper_html,
                },
            )
        except Exception as exc:
            logger.exception("Unexpected error viewing run %r for control %r", run_id, control_id)
            return _run_error(request, conn, control_id, str(exc))
