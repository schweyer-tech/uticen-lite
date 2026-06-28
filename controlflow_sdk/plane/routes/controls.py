from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Generator
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect


def _fmt_executed(iso: str) -> str:
    """Render a run's ISO-8601 ``executed_at`` for humans (UTC)."""
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        return iso or "—"


def _fmt_axis(iso: str) -> str:
    """Render a compact ``executed_at`` for a trend chart's x-axis endpoint."""
    try:
        return datetime.fromisoformat(iso).strftime("%b %d")
    except (ValueError, TypeError):
        return ""


def _history_view(runs: list[dict]) -> dict[str, Any]:
    """Build the trend view-model from newest-first run dicts (learning 0004).

    ``repo.list_runs_for`` returns newest-first, but a trend must read
    oldest->newest left-to-right, so chart a reversed copy. ``runs`` itself is
    left untouched (the table renders it newest-first). "latest" reads index 0
    (the newest input) — never the last chronological point.
    """
    chrono = list(reversed(runs))  # oldest->newest for left-to-right charting
    points = [
        {
            "pass_rate": r["pass_rate"],
            "failed": r["failed"],
            "total": r["total"],
            "executed_at": r["executed_at"],
            "x_label": _fmt_axis(r.get("executed_at", "")),
            "label": (
                f"{_fmt_executed(r.get('executed_at', ''))} — "
                f"{r['pass_rate']}% pass, {r['failed']} failed"
            ),
        }
        for r in chrono
    ]
    return {
        "points": points,
        "max_failed": max((p["failed"] for p in points), default=0),
        "latest_pass_rate": runs[0]["pass_rate"] if runs else None,
    }


def _typed(value: str) -> Any:
    v = value.strip()
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except ValueError:
        return v


def _primary_columns(conn: sqlite3.Connection, source_ids: list[str]) -> list[dict]:
    """Columns of the rule's primary population (the first bound source).

    The first bound source is the primary population (mirrors
    ``runner/execute.py`` which uses ``populations[0]``). Returns ``[]`` when no
    source is bound or the source is missing, so the template falls back to a
    free-text column input.
    """
    if not source_ids:
        return []
    src = repo.get_source(conn, source_ids[0])
    return src["columns"] if src else []


def _padded(items: list, n: int) -> list:
    """Right-pad a getlist to length ``n`` with empty strings (parallel fields)."""
    return list(items) + [""] * (n - len(items))


def _resolve_column(selected: str, freetext: str) -> str:
    """Resolve the posted column: the dropdown value, or the free-text sibling
    when the user picked the ``__other__`` (type-a-name) escape hatch."""
    if selected == "__other__" and freetext.strip():
        return freetext.strip()
    return selected.strip()


def _rule_spec_from_form(form: Any) -> dict[str, Any]:
    columns = form.getlist("cond_column")
    n = len(columns)
    ops = _padded(form.getlist("cond_op"), n)
    values = _padded(form.getlist("cond_value"), n)
    freetexts = _padded(form.getlist("cond_column_freetext"), n)
    other_sources = _padded(form.getlist("cond_other_source"), n)
    this_keys = _padded(form.getlist("cond_this_key"), n)
    other_keys = _padded(form.getlist("cond_other_key"), n)
    conditions: list[dict[str, Any]] = []
    for i, (col, op, raw) in enumerate(zip(columns, ops, values)):
        if op in ("exists_in", "not_exists_in"):
            this_key = this_keys[i].strip()
            if not this_key:
                continue
            conditions.append({
                "op": op,
                "column": this_key,
                "other_source": other_sources[i].strip(),
                "this_key": this_key,
                "other_key": other_keys[i].strip(),
            })
            continue
        resolved = _resolve_column(col, freetexts[i])
        if not resolved:
            continue
        cond: dict[str, Any] = {"column": resolved, "op": op}
        if op in ("is_empty", "not_empty", "is_duplicate"):
            pass
        elif op in ("in", "not_in"):
            cond["value"] = [_typed(p) for p in raw.split("|") if p.strip()]
        else:
            cond["value"] = _typed(raw)
        conditions.append(cond)
    return {
        "logic": form.get("rule_logic", "all"),
        "conditions": conditions,
        "severity": form.get("rule_severity", "medium"),
        "description_template": form.get("rule_description", ""),
        "item_key_column": form.get("rule_item_key") or None,
    }


def _conditions_view_from_form(form: Any) -> list[dict[str, Any]]:
    """Rebuild condition view-models from the RAW posted fields (no type coercion).

    Used by the source-checkbox refresh (``GET /controls/_conditions``) to
    re-render every row against the newly-checked source's columns while keeping
    the author's uncommitted state. Unlike ``_rule_spec_from_form`` this preserves
    the verbatim string the user typed (so a ``cond_value`` like ``a|b`` round-
    trips into the same input) and resolves the ``__other__`` free-text column to
    a plain ``column`` so the partial can match it against the new dropdown. An
    empty list yields one blank row (the template's fallback).
    """
    columns = form.getlist("cond_column")
    n = len(columns)
    ops = _padded(form.getlist("cond_op"), n)
    values = _padded(form.getlist("cond_value"), n)
    freetexts = _padded(form.getlist("cond_column_freetext"), n)
    other_sources = _padded(form.getlist("cond_other_source"), n)
    this_keys = _padded(form.getlist("cond_this_key"), n)
    other_keys = _padded(form.getlist("cond_other_key"), n)
    rows: list[dict[str, Any]] = []
    for i, (col, op) in enumerate(zip(columns, ops)):
        if op in ("exists_in", "not_exists_in"):
            rows.append({
                "op": op,
                "column": _resolve_column(col, freetexts[i]) or this_keys[i].strip(),
                "other_source": other_sources[i].strip(),
                "this_key": this_keys[i].strip(),
                "other_key": other_keys[i].strip(),
            })
            continue
        rows.append({
            "op": op or "eq",
            "column": _resolve_column(col, freetexts[i]),
            "value": values[i],
        })
    return rows


def _cross_source_ids(rule_spec: dict[str, Any] | None) -> list[str]:
    """The set of source ids referenced by cross-source conditions (source B)."""
    if not rule_spec:
        return []
    out: list[str] = []
    for c in rule_spec.get("conditions", []):
        other = c.get("other_source")
        if other and other not in out:
            out.append(other)
    return out


def _pipeline_from_form(form: Any) -> dict[str, Any] | None:
    """Parse the posted ``pipeline_json`` into a graph dict (None when absent)."""
    raw = form.get("pipeline_json")
    if not raw:
        return None
    try:
        graph = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return graph if isinstance(graph, dict) else None


def _other_source_ids(pipeline: Any) -> list[str]:
    """Source ids referenced by cross-source conditions (exists_in/not_exists_in).

    Walks every node's ``config.conditions`` list and collects unique
    ``other_source`` values so they can be unioned into the control's bound
    ``source_ids`` alongside the Import-node sources.  Preserves insertion order
    and skips duplicates and empty strings.
    """
    out: list[str] = []
    for node in pipeline.nodes:
        for cond in node.config.get("conditions", []):
            other = cond.get("other_source", "")
            if other and other not in out:
                out.append(other)
    return out


def _save_pipeline_graph(
    conn: sqlite3.Connection, control_id: str, graph: dict[str, Any]
) -> list[str]:
    """Validate, lint, compile and persist a pipeline graph onto an EXISTING control.

    Used by the dedicated pipeline-editor route (the control's metadata already
    exists; only the graph + its compiled artifact + the derived source binding
    change). Returns a list of errors (id-prefixed for lint failures so the
    editor can pin them per-node) — ``[]`` on success. Nothing is persisted when
    there are errors, mirroring the create/update guardrail (§8 layer 1).

    Source binding is derived from TWO sources:

    1. Import nodes (``parsed.import_source_ids()``) — the primary population(s).
    2. ``other_source`` values in any node's ``config.conditions`` — the reference
       sets for ``exists_in`` / ``not_exists_in`` cross-source conditions.

    Both are unioned (Import sources first; extra ``other_source`` values appended
    in the order they appear) so the runner can load them all.  Pre-fix, only (1)
    was collected, causing ``ValueError: exists_in references unknown source`` at
    run time for single-Import pipelines whose Test node used a second source via
    ``not_exists_in`` (T6/T7 regression, issue #25).
    """
    from controlflow_sdk.pipeline.compile import compile_pipeline
    from controlflow_sdk.pipeline.lint import lint_pipeline
    from controlflow_sdk.pipeline.model import PipelineError, parse_pipeline

    control = repo.get_control(conn, control_id)
    if control is None:
        return [f"control {control_id!r} does not exist"]
    try:
        parsed = parse_pipeline(graph)
        parsed.validate_sources({s["id"] for s in repo.list_sources(conn)})
    except PipelineError as exc:
        return [str(exc)]
    lint_errors = lint_pipeline(parsed)
    if lint_errors:
        return lint_errors

    compiled = compile_pipeline(parsed)
    repo.upsert_control(
        conn,
        id=control["id"],
        title=control["title"],
        objective=control["objective"],
        narrative=control["narrative"],
        framework_refs=control["framework_refs"],
        test_kind="pipeline",
        rule_spec=compiled.rule_spec,
        test_code=compiled.test_code,
        pipeline=graph,
        failure_threshold_pct=control["failure_threshold_pct"],
        failure_threshold_count=control["failure_threshold_count"],
        failure_threshold_rationale=control["failure_threshold_rationale"],
    )
    # Union Import-node sources (primary) with other_source values from
    # cross-source conditions — Import sources come first (they are the primary
    # population), then any extra other_source not already present.
    import_ids = parsed.import_source_ids()
    extra_ids = [sid for sid in _other_source_ids(parsed) if sid not in import_ids]
    repo.set_control_sources(conn, control["id"], import_ids + extra_ids)
    return []


def _required_source_ids(existing: dict) -> list[str]:
    """Source ids that the control's logic requires, beyond what the author posted.

    For a PIPELINE control: Import-node sources + any ``other_source`` values
    from cross-source conditions (exists_in/not_exists_in).
    For a RULE-SPEC control: the ``other_source`` values in its conditions.
    Returns ``[]`` on any parse failure so a malformed graph never breaks the
    metadata save.
    """
    try:
        if existing.get("pipeline"):
            from controlflow_sdk.pipeline.model import parse_pipeline
            parsed = parse_pipeline(existing["pipeline"])
            import_ids = parsed.import_source_ids()
            extra = [sid for sid in _other_source_ids(parsed) if sid not in import_ids]
            return import_ids + extra
        if existing.get("rule_spec"):
            return _cross_source_ids(existing["rule_spec"])
    except Exception:  # noqa: BLE001 — malformed graph must not break the metadata save
        pass
    return []


def _node_id_for_import(nodes: list[dict[str, Any]], source_id: str) -> str:
    """Pick a stable, unique import-node id for a newly bound source."""
    used = {str(n.get("id") or "") for n in nodes}
    base = "".join(ch if ch.isalnum() else "_" for ch in f"imp_{source_id}").strip("_") or "imp"
    if base not in used:
        return base
    i = 2
    while f"{base}_{i}" in used:
        i += 1
    return f"{base}_{i}"


def _reconcile_pipeline_imports(
    pipeline: dict[str, Any] | None,
    selected_source_ids: list[str],
) -> dict[str, Any] | None:
    """Sync Import nodes to the selected Definition sources.

    - Add missing Import nodes for newly selected sources.
    - Remove Import nodes whose source was de-selected.
    - Remove dangling ``inputs`` references to deleted Import node ids.
    """
    if not isinstance(pipeline, dict):
        return pipeline
    nodes = list(pipeline.get("nodes") or [])
    selected_order: list[str] = []
    selected_set: set[str] = set()
    for sid in selected_source_ids:
        if sid not in selected_set:
            selected_set.add(sid)
            selected_order.append(sid)

    removed_ids: set[str] = set()
    import_by_source: dict[str, dict[str, Any]] = {}
    non_import_nodes: list[dict[str, Any]] = []
    for n in nodes:
        if n.get("type") != "import":
            non_import_nodes.append(dict(n))
            continue
        sid = str(n.get("source_id") or "")
        if sid not in selected_set:
            removed_ids.add(str(n.get("id") or ""))
            continue
        if sid in import_by_source:
            removed_ids.add(str(n.get("id") or ""))
            continue
        import_by_source[sid] = dict(n)

    if removed_ids:
        for n in non_import_nodes:
            if isinstance(n.get("inputs"), list):
                n["inputs"] = [i for i in n["inputs"] if i not in removed_ids]

    ordered_imports: list[dict[str, Any]] = []
    id_scope = [*non_import_nodes]
    for sid in selected_order:
        existing = import_by_source.get(sid)
        if existing is None:
            existing = {
                "id": _node_id_for_import(id_scope + ordered_imports, sid),
                "type": "import",
                "source_id": sid,
                "narrative": "",
            }
        ordered_imports.append(existing)

    out = dict(pipeline)
    out["nodes"] = [*ordered_imports, *non_import_nodes]
    return out


def _save_from_form(conn: sqlite3.Connection, form: Any, original_id: str | None = None) -> str:
    """Save the Definition form: metadata + sources only.

    For an EXISTING control the logic fields (test_kind, rule_spec, test_code,
    pipeline) are loaded from the store and passed through unchanged so that
    editing metadata never clobbers logic authored on the Logic tab.

    For a NEW control (no existing store record) the control is created with
    empty logic (test_kind="pipeline", no rule_spec/test_code/pipeline); the
    Logic ▸ Builder derives an Import→Test scaffold on first view.

    Reconciliation rules for existing controls:
    - Pipeline controls: reconcile Import nodes to the posted ``source_ids``
      (select => add Import, de-select => remove Import), then union any
      remaining logic-required sources (e.g. cross-source ``other_source``).
    - Non-pipeline controls: preserve existing logic and union logic-required
      sources into posted ``source_ids`` so needed bindings are never dropped.
    """
    cid = str(form.get("id")).strip()
    # The Control ID is an editable Details field. On an existing control a
    # changed id is a rename (moves the row, sources and runs); an empty field
    # falls back to the original so a blank submit never corrupts the record.
    if original_id is not None:
        if not cid:
            cid = original_id
        if cid != original_id:
            repo.rename_control_id(conn, original_id, cid)  # may raise ValueError
    nist = [s.strip() for s in str(form.get("framework_nist", "")).split(",") if s.strip()]
    pct = form.get("failure_threshold_pct")
    cnt = form.get("failure_threshold_count")
    rationale = str(form.get("failure_threshold_rationale", "")).strip() or None
    source_ids = list(form.getlist("source_ids"))

    # Preserve existing logic for updates; use empty logic for new controls.
    existing = repo.get_control(conn, cid)
    if existing is not None:
        test_kind = existing["test_kind"]
        rule_spec = existing["rule_spec"]
        test_code = existing["test_code"]
        pipeline = existing["pipeline"]
        if pipeline:
            pipeline = _reconcile_pipeline_imports(pipeline, source_ids)
            existing = dict(existing)
            existing["pipeline"] = pipeline
        # Union logic-required sources so a needed source is never dropped.
        for sid in _required_source_ids(existing):
            if sid not in source_ids:
                source_ids.append(sid)
    else:
        test_kind = "pipeline"
        rule_spec = None
        test_code = None
        pipeline = None

    repo.upsert_control(
        conn,
        id=cid,
        title=form.get("title", ""),
        objective=form.get("objective", ""),
        narrative=form.get("narrative", ""),
        framework_refs={"nist": nist},
        test_kind=test_kind,
        rule_spec=rule_spec,
        test_code=test_code,
        pipeline=pipeline,
        failure_threshold_pct=float(pct) if pct else None,
        failure_threshold_count=int(cnt) if cnt else None,
        failure_threshold_rationale=rationale,
    )
    repo.set_control_sources(conn, cid, source_ids)
    return cid


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    @app.get("/controls/_condition_row", response_class=HTMLResponse)
    def condition_row(
        request: Request,
        source_id: str = "",
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        cols = _primary_columns(conn, [source_id]) if source_id else []
        return templates.TemplateResponse(
            request, "partials/rule_condition.html",
            {"columns": cols, "all_sources": repo.list_sources(conn)},
        )

    @app.get("/controls/_conditions", response_class=HTMLResponse)
    def conditions_refresh(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        """Re-render the condition rows for the currently-checked sources (U1).

        Fired by ``hx-trigger`` when a data-source checkbox toggles. The request
        carries the uncommitted condition fields + the checked ``source_ids`` as
        query params (HTMX ``hx-include``); we derive the new primary-source
        columns and re-render every row with that source's column dropdown,
        preserving what the author already typed. Read-only — nothing is written
        to the store for this preview (sync GET → ``Depends`` per learning 0002).
        """
        params = request.query_params
        source_ids = [s for s in params.getlist("source_ids") if s]
        return templates.TemplateResponse(
            request,
            "partials/rule_conditions.html",
            {
                "conditions": _conditions_view_from_form(params),
                "columns": _primary_columns(conn, source_ids),
                "all_sources": repo.list_sources(conn),
            },
        )

    @app.get("/controls/new", response_class=HTMLResponse)
    def new_control(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        from controlflow_sdk.plane.routes.ai import _ai_configured

        return templates.TemplateResponse(
            request,
            "control_edit.html",
            {
                "project": repo.get_project(conn) or {"name": ""},
                "control": None,
                "sources": repo.list_sources(conn),
                "columns": [],  # no bound source yet → free-text fallback
                "all_sources": repo.list_sources(conn),
                "ai_enabled": _ai_configured(conn),
            },
        )

    @app.post("/controls/{control_id}/sources", response_class=HTMLResponse)
    def update_control_sources(
        control_id: str,
        request: Request,
        action: str = Form(...),
        source_id: str = Form(...),
    ) -> HTMLResponse:
        """Add/remove a single source binding and re-render the bound-sources
        fragment in place (HTMX swap), so the page never reloads or scrolls to
        the top (2026-06-27 review). Writing handler → per-handler conn (0002)."""
        root = request.app.state.project_root
        conn = connect(root)
        try:
            existing = repo.get_control(conn, control_id)
            if existing is not None:
                current = list(existing["source_ids"])
                if action == "add" and source_id not in current:
                    current.append(source_id)
                elif action == "remove":
                    current = [s for s in current if s != source_id]
                # Never drop a binding the logic requires (pipeline imports /
                # cross-source refs) — mirrors the metadata-save reconciliation.
                for sid in _required_source_ids(existing):
                    if sid not in current:
                        current.append(sid)
                repo.set_control_sources(conn, control_id, current)
            control = repo.get_control(conn, control_id)
            sources = repo.list_sources(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "partials/_bound_sources.html",
            {"control": control, "sources": sources},
        )

    # Register the specific sub-route BEFORE the /{control_id} catch-all so it
    # cannot be shadowed (learning 0007). Read-only sync GET → Depends (0002).
    @app.get("/controls/{control_id}/history", response_class=HTMLResponse)
    def control_history(
        control_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        control = repo.get_control(conn, control_id)
        runs = repo.list_runs_for(conn, control_id)  # newest-first (0004)
        for r in runs:
            r["executed_display"] = _fmt_executed(r.get("executed_at", ""))
        return templates.TemplateResponse(
            request,
            "control_history.html",
            {
                "project": repo.get_project(conn) or {"name": ""},
                "control": control,
                "control_id": control_id,
                "runs": runs,
                "trend": _history_view(runs),
                "active": "history",
            },
        )

    @app.get("/controls/{control_id}", response_class=HTMLResponse)
    def edit_control(
        control_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        from controlflow_sdk.plane.routes.ai import _ai_configured

        control = repo.get_control(conn, control_id)
        return templates.TemplateResponse(
            request,
            "control_edit.html",
            {
                "project": repo.get_project(conn) or {"name": ""},
                "control": control,
                "sources": repo.list_sources(conn),
                "columns": _primary_columns(conn, control["source_ids"]) if control else [],
                "all_sources": repo.list_sources(conn),
                "ai_enabled": _ai_configured(conn),
            },
        )

    def _rerender_with_error(
        request: Request, conn: sqlite3.Connection, control_id: str | None,
        errors: list[str],
    ) -> HTMLResponse:
        """Re-render the edit form with an inline error banner (HTTP 422).

        Used when a pipeline save is REFUSED by the §8 allowlist deny-scan: the
        offending Custom Python node's offramp message reaches the author rather
        than persisting an unsafe node or returning a bare 500.
        """
        from controlflow_sdk.plane.routes.ai import _ai_configured

        control = repo.get_control(conn, control_id) if control_id else None
        return templates.TemplateResponse(
            request,
            "control_edit.html",
            {
                "project": repo.get_project(conn) or {"name": ""},
                "control": control,
                "sources": repo.list_sources(conn),
                "columns": _primary_columns(conn, control["source_ids"]) if control else [],
                "all_sources": repo.list_sources(conn),
                "ai_enabled": _ai_configured(conn),
                "save_errors": errors,
            },
            status_code=422,
        )

    @app.post("/controls", response_model=None)
    async def create_control(request: Request) -> HTMLResponse | RedirectResponse:
        from controlflow_sdk.pipeline.lint import LintError
        from controlflow_sdk.pipeline.model import PipelineError

        root = request.app.state.project_root
        conn = connect(root)
        try:
            form = await request.form()
            try:
                cid = _save_from_form(conn, form)
            except LintError as exc:
                return _rerender_with_error(request, conn, None, exc.errors)
            except PipelineError as exc:
                return _rerender_with_error(request, conn, None, [str(exc)])
            return RedirectResponse(f"/controls/{cid}", status_code=303)
        finally:
            conn.close()

    @app.post("/controls/{control_id}", response_model=None)
    async def update_control(control_id: str, request: Request) -> HTMLResponse | RedirectResponse:
        from controlflow_sdk.pipeline.lint import LintError
        from controlflow_sdk.pipeline.model import PipelineError

        root = request.app.state.project_root
        conn = connect(root)
        try:
            form = await request.form()
            try:
                cid = _save_from_form(conn, form, original_id=control_id)
            except LintError as exc:
                return _rerender_with_error(request, conn, control_id, exc.errors)
            except PipelineError as exc:
                return _rerender_with_error(request, conn, control_id, [str(exc)])
            except ValueError as exc:
                # A bad rename (blank/duplicate id) or unparsable threshold —
                # surface it inline rather than 500.
                return _rerender_with_error(request, conn, control_id, [str(exc)])
            return RedirectResponse(f"/controls/{cid}", status_code=303)
        finally:
            conn.close()

    @app.post("/controls/{control_id}/title", response_model=None)
    async def update_control_title(
        control_id: str, request: Request
    ) -> HTMLResponse | RedirectResponse:
        root = request.app.state.project_root
        conn = connect(root)
        try:
            form = await request.form()
            title = str(form.get("title", "")).strip()
            if not title:
                return _rerender_with_error(
                    request, conn, control_id, ["Control title is required."]
                )
            existing = repo.get_control(conn, control_id)
            if existing is None:
                return _rerender_with_error(
                    request, conn, control_id, [f"Control {control_id!r} does not exist."]
                )
            repo.upsert_control(
                conn,
                id=existing["id"],
                title=title,
                objective=existing["objective"],
                narrative=existing["narrative"],
                framework_refs=existing["framework_refs"],
                test_kind=existing["test_kind"],
                rule_spec=existing["rule_spec"],
                test_code=existing["test_code"],
                pipeline=existing["pipeline"],
                failure_threshold_pct=existing["failure_threshold_pct"],
                failure_threshold_count=existing["failure_threshold_count"],
                failure_threshold_rationale=existing["failure_threshold_rationale"],
            )
            repo.set_control_sources(conn, control_id, existing["source_ids"])
            return RedirectResponse(f"/controls/{control_id}", status_code=303)
        finally:
            conn.close()
