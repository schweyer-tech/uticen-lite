"""AI-assisted authoring routes (opt-in, behind the ``[ai]`` extra).

Endpoints:

* ``GET/POST /settings/ai`` — pick the active provider + model. Selection is
  stored in the store-only ``project.system["ai"]`` JSON (learning 0006) and is
  never threaded into ``to_data_source()`` or the bundle.
* ``POST /controls/ai/draft`` — draft a ``rule_spec`` from the live objective +
  the primary bound source's schema and a small data sample, then return an
  HTMX partial: the re-rendered rule builder prefilled from the validated draft,
  or a friendly error banner.

Offline by default: no provider is constructed and ``draft_rule_spec`` is never
called unless a provider+model is saved AND its env key is present — both guards
run before any backend import. Writing handlers open a per-handler connection
(learning 0002); the read-only ``GET`` uses ``Depends(get_conn)``.
"""

import sqlite3
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from uticen_lite.store import repo
from uticen_lite.store.db import connect

_AI_SAMPLE_ROWS = 20  # token-modest sample; tunable starting value (spec §resolved)


def _ai_config(conn: sqlite3.Connection) -> dict[str, str] | None:
    """The saved ``{provider, model}`` selection, or ``None`` if unset."""
    project = repo.get_project(conn)
    if not project:
        return None
    ai = (project.get("system") or {}).get("ai")
    if isinstance(ai, dict) and ai.get("provider") and ai.get("model"):
        return {"provider": str(ai["provider"]), "model": str(ai["model"])}
    return None


def _ai_configured(conn: sqlite3.Connection) -> bool:
    """True when a provider+model is saved AND its env key is present."""
    from uticen_lite.ai.providers import provider_key_present

    cfg = _ai_config(conn)
    return bool(cfg and provider_key_present(cfg["provider"]))


def _build_sample(conn: sqlite3.Connection, root: Path, source_id: str) -> dict[str, Any] | None:
    """Load the primary source and return ``{columns, schema, rows}`` for the AI
    layer. Returns ``None`` when the source has no usable data file yet."""
    from uticen_lite.adapters.files import UnsupportedSourceError, source_for
    from uticen_lite.store.loader import load_project_from_store

    project = load_project_from_store(conn)
    binding = project.sources.get(source_id)
    if binding is None:
        return None
    try:
        pop = source_for(binding, root).load()
    except (UnsupportedSourceError, OSError, ValueError):
        # FileNotFoundError is a subclass of OSError, so it is already covered.
        return None

    cols = [c for c in pop.columns if c.include]
    original_names = [c.original_name for c in cols]
    schema = [
        {"original_name": c.original_name, "display_name": c.display_name,
         "data_type": c.data_type}
        for c in cols
    ]
    rows: list[list[str]] = []
    for record in pop.df.head(_AI_SAMPLE_ROWS).to_dict(orient="records"):
        rows.append([_cell(record.get(name)) for name in original_names])
    return {"columns": original_names, "schema": schema, "rows": rows}


def _cell(value: Any) -> str:
    if pd.isna(value):  # None / NaN / NaT all render as an empty cell
        return ""
    return str(value)


def _draft_failure_message(exc: Exception) -> str:
    """Friendly message for a failed AI draft (verbatim for ``DraftError``)."""
    from uticen_lite.ai.draft import DraftError

    if isinstance(exc, DraftError):
        return str(exc)
    return (
        "The AI provider could not produce a usable rule. Try again or "
        "build the rule by hand."
    )


def _draft_response(
    templates: Jinja2Templates,
    request: Request,
    conn: sqlite3.Connection,
    root: Path,
    objective: str,
    source_ids: list[str],
) -> HTMLResponse:
    """Draft a ``rule_spec`` and return the prefilled rule builder (or an error
    banner). Extracted from the handler so ``register`` stays flat (S3776)."""
    cfg = _ai_config(conn)
    if cfg is None:
        return _error(templates, request,
                      "AI is not configured. Pick a provider in Settings.")

    from uticen_lite.ai.providers import provider_key_present

    if not provider_key_present(cfg["provider"]):
        return _error(
            templates, request,
            "AI is not enabled — the selected provider's API key is not set "
            "in this environment.",
        )

    if not source_ids:
        return _error(templates, request, "Bind a data source to this control first.")

    sample = _build_sample(conn, root, source_ids[0])
    if sample is None:
        return _error(templates, request, "Bind a data file to the source first.")

    from uticen_lite.ai.draft import draft_and_validate
    from uticen_lite.rules.spec import RuleSpecError

    try:
        draft = draft_and_validate(
            objective=objective,
            source_schema={"columns": sample["schema"]},
            data_sample=sample,
            provider=cfg["provider"],
            model=cfg["model"],
        )
    except RuleSpecError as exc:
        return _error(templates, request,
                      f"The drafted rule was malformed: {exc}")
    except Exception as exc:  # DraftError + any backend/transport failure
        return _error(templates, request, _draft_failure_message(exc))

    # Render the rule builder prefilled from the validated draft. The
    # synthetic control carries .rule_spec + test_kind=="rule" so the
    # existing partial renders it; author reviews and submits the form.
    from uticen_lite.plane.routes.controls import _primary_columns

    return templates.TemplateResponse(
        request,
        "partials/rule_builder.html",
        {
            "control": {"rule_spec": draft, "test_kind": "rule"},
            "columns": _primary_columns(conn, source_ids),
            "all_sources": repo.list_sources(conn),
        },
    )


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    @app.get("/settings/ai", response_class=HTMLResponse)
    def ai_settings(
        request: Request,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    ) -> HTMLResponse:
        from uticen_lite.ai.providers import available_providers

        cfg = _ai_config(conn)
        return templates.TemplateResponse(
            request,
            "settings_ai.html",
            {
                "project": repo.get_project(conn) or {"name": ""},
                "providers": available_providers(),
                "selected_provider": cfg["provider"] if cfg else "",
                "selected_model": cfg["model"] if cfg else "",
            },
        )

    @app.post("/settings/ai")
    async def save_ai_settings(request: Request) -> RedirectResponse:
        root = request.app.state.project_root
        conn = connect(root)  # per-handler conn (0002)
        try:
            form = await request.form()
            provider = str(form.get("provider", "")).strip()
            model = str(form.get("model", "")).strip()
            project = repo.get_project(conn) or {"name": "", "framework": None, "system": {}}
            system = dict(project.get("system") or {})
            if provider and model:
                system["ai"] = {"provider": provider, "model": model}
            else:
                system.pop("ai", None)
            repo.upsert_project(
                conn,
                name=project.get("name", ""),
                framework=project.get("framework"),
                system=system,
                created_at=project.get("created_at", "") or "",
            )
        finally:
            conn.close()
        return RedirectResponse("/settings/ai", status_code=303)

    @app.post("/controls/ai/draft", response_class=HTMLResponse)
    async def draft_rule(request: Request) -> HTMLResponse:
        root = request.app.state.project_root
        conn = connect(root)  # per-handler conn (0002)
        try:
            form = await request.form()
            objective = str(form.get("objective", ""))
            source_ids = [str(s) for s in form.getlist("source_ids") if s]
            return _draft_response(templates, request, conn, root, objective, source_ids)
        finally:
            conn.close()


def _error(templates: Jinja2Templates, request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "partials/ai_draft_error.html", {"message": message}
    )
