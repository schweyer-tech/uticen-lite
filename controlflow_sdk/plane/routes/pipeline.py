"""Server-rendered visual control-pipeline editor (issue #25, Stage 3 — §5/§9/§11).

A control's pipeline is shown as a **server-rendered stacked list of node cards**
in topological order (HTMX add / remove, like the existing "+ Add condition"),
plus a generated read-only **SVG flowchart**, **live row-counts** at every joint,
and a read-only **generated-Python view** (the glass-box). The author binds
sources by adding Import nodes; every card gets a column dropdown from the bound
source. A one-way **"Convert to Python test"** door compiles the graph into the
existing escape-hatch editor.

Per learning 0007 the editor is a server-rendered sub-route tab on the control
editor (``/controls/{id}/pipeline``), and the specific sub-routes are registered
BEFORE the ``/controls/{control_id}`` catch-all so they cannot be shadowed.
Per learning 0002 the read-only GETs take a ``Depends(get_conn)`` connection and
the mutating POSTs open their own per-handler connection.

The graph is **store-only** (the ``pipeline`` column); it COMPILES to the
existing ``rule_spec`` / ``test_code`` at run/build time, so the bundle contract
never learns the word "node" (cardinal rule, learning 0001).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Generator
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from controlflow_sdk.pipeline.compile import compile_pipeline
from controlflow_sdk.pipeline.model import Pipeline, PipelineError, parse_pipeline
from controlflow_sdk.plane.logic_view import derive_builder_graph, is_raw_python
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect

# Operators surfaced in the Filter/Test card column-condition rows. Mirrors
# rules/spec.OPERATORS; the column-vs-column / arithmetic operators are the #1
# Phase-2 grammar follow-up and are intentionally NOT here.
OP_CHOICES: list[tuple[str, str]] = [
    ("eq", "eq (=)"), ("ne", "ne (≠)"), ("gt", "gt (>)"), ("ge", "ge (≥)"),
    ("lt", "lt (<)"), ("le", "le (≤)"), ("is_empty", "is_empty"),
    ("not_empty", "not_empty"), ("in", "in (pipe-separated)"),
    ("not_in", "not_in (pipe-separated)"), ("regex", "regex"),
    ("is_duplicate", "is_duplicate"),
    ("exists_in", "exists in another source"),
    ("not_exists_in", "not in another source"),
]
JOIN_MODE_CHOICES: list[tuple[str, str]] = [
    ("inner", "inner — keep left rows with a match"),
    ("left", "left — keep all left rows, enrich with matches"),
    ("exists", "exists — keep left rows present in the right stream"),
    ("not_exists", "not_exists — keep left rows absent from the right stream"),
]

# Cap the sample used for live row-counts (the offline feedback loop must stay
# fast and offline). 0 is the tell either way (a node dropping to 0).
_ROWCOUNT_SAMPLE = 2000

_EMPTY_GRAPH: dict[str, Any] = {"nodes": []}


# ---------------------------------------------------------------------------
# View-model helpers
# ---------------------------------------------------------------------------

def _graph_of(control: dict | None) -> dict[str, Any]:
    """The stored graph for a control, or an empty graph for a fresh pipeline."""
    if control and control.get("pipeline"):
        return control["pipeline"]
    return dict(_EMPTY_GRAPH)


def _source_columns(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Map every source id → its column dicts (for the per-card dropdowns)."""
    return {s["id"]: s["columns"] for s in repo.list_sources(conn)}


def _stream_columns(
    pipeline: Pipeline, source_columns: dict[str, list[dict]]
) -> dict[str, list[dict]]:
    """Columns visible on each node's OUTPUT stream, keyed by node id.

    Import surfaces its source's columns; Filter/Custom Python pass their input's
    columns through unchanged; a Join unions its two inputs' columns (deduped on
    ``original_name``). This is what feeds the column dropdown on the card that
    consumes a stream — so a Test after a Join can pick a column from either side.
    """
    out: dict[str, list[dict]] = {}
    for node in pipeline.topological():
        if node.type == "import":
            out[node.id] = source_columns.get(node.source_id or "", [])
        elif node.type == "join":
            seen: set[str] = set()
            merged: list[dict] = []
            for src in node.inputs:
                for c in out.get(src, []):
                    if c["original_name"] not in seen:
                        seen.add(c["original_name"])
                        merged.append(c)
            out[node.id] = merged
        elif node.inputs:
            out[node.id] = out.get(node.inputs[0], [])
        else:
            out[node.id] = []
    return out


def _input_columns_for(
    pipeline: Pipeline, node: Any, stream_columns: dict[str, list[dict]]
) -> list[dict]:
    """Columns a node's card should offer (its first input's output stream)."""
    if node.type == "import":
        return []
    if not node.inputs:
        return []
    return stream_columns.get(node.inputs[0], [])


def _diagram(pipeline: Pipeline, counts: dict[str, int]) -> dict[str, Any]:
    """A view-model for the multi-lane server-rendered SVG flowchart (top-down).

    One box per node with a ``row`` (vertical position, top→bottom) and a ``lane``
    (horizontal column). Parallel branches that feed a Join sit in SEPARATE lanes
    that visibly converge at the Join box, so a join / multi-root pipeline no
    longer reads as a single linear chain. Each box also carries its type, a short
    label, and the surviving row-count (``None`` when unknown). Edges connect
    ``(from_row, from_lane) → (to_row, to_lane)`` so the template can draw a
    straight in-lane spine and a converging connector across lanes.

    Layout is **presentation-only** — it never reorders execution. Compile/run
    still use :meth:`Pipeline.topological`; this view-model only positions boxes.
    """
    # Vertical order: DFS post-order from the terminal so a node's inputs sit
    # contiguously above it (each branch stays together). Then any node not
    # reachable from the terminal, in topological order, for determinism.
    order: list[Any] = []
    seen: set[str] = set()

    def _visit(node_id: str) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        node = pipeline.node(node_id)
        for src in node.inputs:
            _visit(src)
        order.append(node)

    _visit(pipeline.terminal.id)
    for n in pipeline.topological():  # include any node not reachable from terminal
        if n.id not in seen:
            seen.add(n.id)
            order.append(n)
    index = {n.id: i for i, n in enumerate(order)}

    lanes = _assign_lanes(pipeline, order)
    lane_count = (max(lanes.values()) + 1) if lanes else 1

    boxes = [
        {
            "id": n.id,
            "type": n.type,
            "label": _node_label(n),
            "count": counts.get(n.id),
            "row": i,
            "lane": lanes[n.id],
            "terminal": n.id == pipeline.terminal.id,
        }
        for i, n in enumerate(order)
    ]
    edges = [
        {
            "from_row": index[src], "to_row": index[n.id],
            "from_lane": lanes[src], "to_lane": lanes[n.id],
        }
        for n in order
        for src in n.inputs
    ]
    return {"boxes": boxes, "edges": edges, "rows": len(order), "lanes": lane_count}


def _assign_lanes(pipeline: Pipeline, order: list[Any]) -> dict[str, int]:
    """Assign each node a horizontal lane so fan-in branches sit side by side.

    Walks down from the terminal: a node keeps its own lane for its FIRST input
    (the spine stays vertical) and pushes each additional input onto a fresh lane
    to the right, recursively. Any node not reached from the terminal (a detached
    root) is parked in its own new lane. The result places a Join's two feeder
    branches in distinct columns that converge at the Join — pure presentation,
    independent of the topological execution order.
    """
    lane: dict[str, int] = {}
    next_lane = [0]

    def _walk(node_id: str, my_lane: int) -> None:
        if node_id in lane:
            return
        lane[node_id] = my_lane
        if my_lane >= next_lane[0]:
            next_lane[0] = my_lane + 1
        node = pipeline.node(node_id)
        for i, src in enumerate(node.inputs):
            if i == 0:
                _walk(src, my_lane)  # first input continues this lane (spine)
            else:
                branch_lane = next_lane[0]
                next_lane[0] += 1
                _walk(src, branch_lane)  # extra inputs fan out to the right

    _walk(pipeline.terminal.id, 0)
    for n in order:  # detached nodes (not reachable from terminal) get their own lane
        if n.id not in lane:
            branch_lane = next_lane[0]
            next_lane[0] += 1
            _walk(n.id, branch_lane)
    return lane


def _node_label(node: Any) -> str:
    """A short human label for a node box (diagram + card heading)."""
    if node.type == "import":
        return f"Import · {node.source_id}"
    if node.type == "filter":
        n = len(node.config.get("conditions", []))
        return f"Filter · {n} condition{'s' if n != 1 else ''}"
    if node.type == "join":
        return f"Join · {node.config.get('mode', '?')}"
    if node.type == "custom_python":
        return f"Custom Python · {node.config.get('flavor', '?')}"
    if node.type == "test":
        return "Test (terminal)"
    return node.type


# ---------------------------------------------------------------------------
# Sample frames → live row-counts
# ---------------------------------------------------------------------------

def _load_sample_frames(
    conn: sqlite3.Connection, root: Any, source_ids: list[str]
) -> dict[str, Any]:
    """Load a capped pandas sample for each bound source (by source_id).

    Reuses the same adapter the runner uses (``source_for(...).load()``) so the
    counts reflect exactly the coercion the real run applies — then caps to
    :data:`_ROWCOUNT_SAMPLE` rows. Returns only the sources whose current file
    exists; a missing frame makes :func:`compute_row_counts` return ``{}``.
    """
    from controlflow_sdk.adapters.files import source_for
    from controlflow_sdk.store.loader import _binding

    frames: dict[str, Any] = {}
    for sid in source_ids:
        src = repo.get_source(conn, sid)
        current = repo.get_current_file(conn, sid)
        if not src or not current:
            continue
        fpath = root / current["stored_path"]
        if not fpath.is_file():
            continue
        try:
            pop = source_for(_binding(src), root).load()
        except Exception:  # noqa: BLE001 — a broken file just yields no counts
            continue
        frames[sid] = pop.df.head(_ROWCOUNT_SAMPLE)
    return frames


def _row_counts(
    conn: sqlite3.Connection, root: Any, pipeline: Pipeline
) -> dict[str, int]:
    """Best-effort row-counts for a pipeline over capped source samples.

    Returns ``{}`` when a source is unbound/missing or the probe fails (the
    template then renders "—"); never raises into the request.

    Catches both ``RowCountError`` (runtime failures in the probe) and
    ``RuleSpecError`` (an incomplete/malformed Test-node condition — e.g. a
    condition with ``column=""`` added via "+ Add condition" before the author
    fills it in).  Row counts are a non-critical preview; an in-progress graph
    must NOT crash the editor.
    """
    from controlflow_sdk.pipeline.rowcounts import RowCountError, compute_row_counts
    from controlflow_sdk.rules.spec import RuleSpecError

    frames = _load_sample_frames(conn, root, pipeline.import_source_ids())
    try:
        return compute_row_counts(pipeline, frames)
    except (RowCountError, RuleSpecError):
        return {}


# ---------------------------------------------------------------------------
# Generated-Python glass-box
# ---------------------------------------------------------------------------

def _generated_python(pipeline: Pipeline) -> str:
    """A runnable ``test(pop, sources)`` source for the glass-box + the offramp.

    The general (cross-source / custom) case is the compiler's stitched
    ``test()``. The pure single-source case compiles to a rule_spec (so it stays
    no-code in the bundle), but the glass-box / "Convert to Python test" door must
    still show *runnable* Python — so render the equivalent ``test()`` from the
    rule_spec via the shared rule renderer (identical row selection, losslessly).
    """
    compiled = compile_pipeline(pipeline)
    if compiled.test_code is not None:
        return compiled.test_code
    from controlflow_sdk.rules.render_rule import _render_python
    from controlflow_sdk.rules.spec import parse_rule_spec

    spec = compiled.rule_spec or {"logic": "all", "conditions": []}
    return _render_python(parse_rule_spec(spec))


# ---------------------------------------------------------------------------
# Render the editor
# ---------------------------------------------------------------------------

def _editor_context(
    request: Request, conn: sqlite3.Connection, root: Any, control_id: str,
    *, save_errors: list[str] | None = None,
    node_errors: dict[str, list[str]] | None = None,
    for_builder: bool = False,
) -> dict[str, Any]:
    """Build the full template context for the pipeline editor tab.

    When ``for_builder=True`` the node cards are rendered from the *derived*
    graph (``derive_builder_graph``) so that rule-spec and empty controls show
    a meaningful Import→Test scaffold in the Builder, and the ``raw_python``
    flag is set so the Builder can render the "authored directly in Python"
    notice instead of node cards for hand-written Python controls.
    """
    control = repo.get_control(conn, control_id)
    graph = _graph_of(control)
    source_columns = _source_columns(conn)
    sources = repo.list_sources(conn)

    # Derive the graph the Builder should render (may differ from the stored graph).
    raw_python = is_raw_python(control) if control else False
    builder_graph: dict[str, Any] | None = None
    if control is not None:
        builder_graph = derive_builder_graph(control, list(control.get("source_ids") or []))

    # The graph used for rendering the stored pipeline diagram / generated Python
    # is always the *stored* graph.  The builder node cards may use a derived graph.
    parsed: Pipeline | None = None
    parse_error: str | None = None
    diagram: dict[str, Any] | None = None
    generated: str = ""
    stream_columns: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    if graph.get("nodes"):
        try:
            parsed = parse_pipeline(graph)
        except PipelineError as exc:
            parse_error = str(exc)
    if parsed is not None:
        counts = _row_counts(conn, root, parsed)
        diagram = _diagram(parsed, counts)
        stream_columns = _stream_columns(parsed, source_columns)
        try:
            generated = _generated_python(parsed)
        except Exception as exc:  # noqa: BLE001 — show parse errors, never 500
            parse_error = parse_error or f"could not generate Python: {exc}"

    # For the Builder, render nodes from the *derived* graph so rule-spec /
    # empty controls show a meaningful scaffold.  Fall back to the stored graph
    # (and its parsed pipeline) when not building.
    if for_builder and builder_graph is not None and not raw_python:
        builder_parsed: Pipeline | None = None
        builder_stream_cols: dict[str, list[dict]] = {}
        builder_counts: dict[str, int] = {}
        if builder_graph.get("nodes"):
            try:
                builder_parsed = parse_pipeline(builder_graph)
            except PipelineError:
                builder_parsed = None
        if builder_parsed is not None:
            builder_counts = _row_counts(conn, root, builder_parsed)
            builder_stream_cols = _stream_columns(builder_parsed, source_columns)
        ordered_nodes = (
            [_card_vm(n, builder_parsed, builder_stream_cols, builder_counts, node_errors or {})
             for n in builder_parsed.topological()]
            if builder_parsed is not None
            else [_raw_card_vm(n, node_errors or {}) for n in builder_graph.get("nodes", [])]
        )
        # The form must submit the DERIVED graph (the one the cards were rendered
        # from), not the stored graph — the stored graph may be empty/absent for a
        # rule_spec or fresh control, so submitting it would silently discard edits.
        builder_graph_json = json.dumps(builder_graph)
    else:
        # Order the raw node dicts topologically for the cards (falls back to
        # as-stored when the graph can't be parsed yet).
        ordered_nodes = (
            [_card_vm(n, parsed, stream_columns, counts, node_errors or {})
             for n in parsed.topological()]
            if parsed is not None
            else [_raw_card_vm(n, node_errors or {}) for n in graph.get("nodes", [])]
        )
        builder_graph_json = json.dumps(graph)

    from controlflow_sdk.plane.routes.ai import _ai_configured

    return {
        "project": repo.get_project(conn) or {"name": ""},
        "control": control,
        "control_id": control_id,
        "active": "pipeline",
        "graph_json": builder_graph_json,
        "nodes": ordered_nodes,
        "diagram": diagram,
        "generated_python": generated,
        "sources": sources,
        "op_choices": OP_CHOICES,
        "join_mode_choices": JOIN_MODE_CHOICES,
        "parse_error": parse_error,
        "save_errors": save_errors or [],
        "raw_python": raw_python,
        "builder_graph": builder_graph,
        "ai_enabled": _ai_configured(conn),
    }


def _card_vm(
    node: Any, pipeline: Pipeline, stream_columns: dict[str, list[dict]],
    counts: dict[str, int], node_errors: dict[str, list[str]],
) -> dict[str, Any]:
    """View-model for one node card (parsed graph)."""
    return {
        "id": node.id,
        "type": node.type,
        "label": _node_label(node),
        "narrative": node.narrative,
        "config": node.config,
        "inputs": node.inputs,
        "source_id": node.source_id,
        "columns": _input_columns_for(pipeline, node, stream_columns),
        "count": counts.get(node.id),
        "terminal": node.id == pipeline.terminal.id,
        "errors": node_errors.get(node.id, []),
    }


def _raw_card_vm(raw: dict, node_errors: dict[str, list[str]]) -> dict[str, Any]:
    """View-model for a node card straight from the stored dict (unparsed)."""
    nid = str(raw.get("id", ""))
    return {
        "id": nid,
        "type": raw.get("type", ""),
        "label": raw.get("type", ""),
        "narrative": raw.get("narrative", ""),
        "config": raw.get("config", {}),
        "inputs": raw.get("inputs", []),
        "source_id": raw.get("source_id"),
        "columns": [],
        "count": None,
        "terminal": False,
        "errors": node_errors.get(nid, []),
    }


def _node_errors_from(save_errors: list[str]) -> dict[str, list[str]]:
    """Split id-prefixed lint errors (``node 'x': ...``) into a per-node map.

    ``lint_pipeline`` prefixes each error with ``node '<id>': `` so the editor
    can pin an inline error on the offending card instead of a top-of-page banner
    (Stage 2 hook). Errors without that prefix stay in the page-level banner.
    """
    per_node: dict[str, list[str]] = {}
    for err in save_errors:
        if err.startswith("node '") and "': " in err:
            nid = err[len("node '"):].split("'", 1)[0]
            msg = err.split("': ", 1)[1]
            per_node.setdefault(nid, []).append(msg)
    return per_node


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    # Register the specific sub-routes BEFORE the /controls/{control_id} catch-all
    # so they cannot be shadowed (learning 0007). NOTE: controls.register() is
    # called AFTER pipeline.register() in app.py for the same reason.

    # --- Logic sub-route redirects -------------------------------------------

    @app.get("/controls/{control_id}/logic")
    def logic_redirect(control_id: str) -> Any:
        return RedirectResponse(f"/controls/{control_id}/logic/builder", status_code=302)

    # --- Logic sub-route GETs ------------------------------------------------

    @app.get("/controls/{control_id}/logic/builder", response_class=HTMLResponse)
    def logic_builder(
        control_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        root = request.app.state.project_root
        ctx = _editor_context(request, conn, root, control_id, for_builder=True)
        ctx["active"] = "logic"
        ctx["logic_tab"] = "builder"
        return templates.TemplateResponse(request, "logic_builder.html", ctx)

    @app.get("/controls/{control_id}/logic/flowchart", response_class=HTMLResponse)
    def logic_flowchart(
        control_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        root = request.app.state.project_root
        ctx = _editor_context(request, conn, root, control_id)
        ctx["active"] = "logic"
        ctx["logic_tab"] = "flowchart"
        return templates.TemplateResponse(request, "logic_flowchart.html", ctx)

    @app.get("/controls/{control_id}/logic/python", response_class=HTMLResponse)
    def logic_python(
        control_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        root = request.app.state.project_root
        ctx = _editor_context(request, conn, root, control_id)
        ctx["active"] = "logic"
        ctx["logic_tab"] = "python"
        return templates.TemplateResponse(request, "logic_python.html", ctx)

    # --- Logic POST (save raw Python test_code) ------------------------------

    @app.post("/controls/{control_id}/logic/python")
    async def save_python(control_id: str, request: Request) -> Any:
        """Save hand-written test_code for a raw-Python control.

        Guard: if the control already has a pipeline or rule_spec it is NOT a
        raw-python control.  A stray/curl POST must not wipe the stored logic —
        redirect back without writing so the round-trip is a no-op for the user.
        """
        root = request.app.state.project_root
        conn = connect(root)
        try:
            control = repo.get_control(conn, control_id)
            if control is None:
                return RedirectResponse(f"/controls/{control_id}/logic/python", status_code=303)
            # Guard: only honour the write when the control has no pipeline or
            # rule_spec.  A stray POST to a GRAPH control (one whose logic was
            # authored in the Builder) must not silently wipe that logic.
            if control.get("pipeline") or control.get("rule_spec"):
                return RedirectResponse(f"/controls/{control_id}/logic/python", status_code=303)
            form = await request.form()
            code = str(form.get("test_code", ""))
            repo.upsert_control(
                conn,
                id=control["id"],
                title=control["title"],
                objective=control["objective"],
                narrative=control["narrative"],
                framework_refs=control["framework_refs"],
                test_kind="python",
                rule_spec=None,
                test_code=code,
                pipeline=None,
                failure_threshold_pct=control["failure_threshold_pct"],
                failure_threshold_count=control["failure_threshold_count"],
            )
            return RedirectResponse(f"/controls/{control_id}/logic/python", status_code=303)
        finally:
            conn.close()

    # --- Logic POST (save builder graph) -------------------------------------

    @app.post("/controls/{control_id}/logic/builder")
    async def save_pipeline(control_id: str, request: Request) -> Any:
        from controlflow_sdk.plane.routes.controls import _save_pipeline_graph

        root = request.app.state.project_root
        conn = connect(root)
        try:
            form = await request.form()
            raw = form.get("pipeline_json")
            try:
                graph = json.loads(str(raw)) if raw else dict(_EMPTY_GRAPH)
            except (ValueError, TypeError):
                graph = dict(_EMPTY_GRAPH)
            errors = _save_pipeline_graph(conn, control_id, graph)
            if errors:
                node_errors = _node_errors_from(errors)
                ctx = _editor_context(
                    request, conn, root, control_id,
                    save_errors=errors, node_errors=node_errors,
                    for_builder=True,
                )
                # The just-rejected graph isn't persisted; render the SUBMITTED
                # graph so the author sees their edits + inline node errors.
                ctx["graph_json"] = json.dumps(graph)
                ctx["active"] = "logic"
                ctx["logic_tab"] = "builder"
                return templates.TemplateResponse(
                    request, "logic_builder.html", ctx, status_code=422
                )
            return RedirectResponse(f"/controls/{control_id}/logic/builder", status_code=303)
        finally:
            conn.close()

    # --- Logic POST (convert to Python) --------------------------------------

    @app.post("/controls/{control_id}/logic/convert")
    async def convert_to_python(control_id: str, request: Request) -> Any:
        """One-way door (§9): compile the pipeline → ``test(pop, sources)`` and
        switch the control to ``test_kind='python'``, dropping the author into the
        existing CodeMirror escape hatch pre-filled with the stitched code."""
        root = request.app.state.project_root
        conn = connect(root)
        try:
            control = repo.get_control(conn, control_id)
            if control is None or not control.get("pipeline"):
                return RedirectResponse(
                    f"/controls/{control_id}/logic/python", status_code=303
                )
            try:
                parsed = parse_pipeline(control["pipeline"])
            except PipelineError:
                return RedirectResponse(
                    f"/controls/{control_id}/logic/python", status_code=303
                )
            # The offramp always graduates to runnable Python — for the pure
            # case this is the rule_spec rendered as an equivalent test().
            code = _generated_python(parsed)
            repo.upsert_control(
                conn,
                id=control["id"],
                title=control["title"],
                objective=control["objective"],
                narrative=control["narrative"],
                framework_refs=control["framework_refs"],
                test_kind="python",
                rule_spec=None,
                test_code=code,
                pipeline=None,
                failure_threshold_pct=control["failure_threshold_pct"],
                failure_threshold_count=control["failure_threshold_count"],
            )
            return RedirectResponse(f"/controls/{control_id}/logic/python", status_code=303)
        finally:
            conn.close()

    # --- Legacy /pipeline GET redirect (301 permanent) -----------------------

    @app.get("/controls/{control_id}/pipeline")
    def pipeline_redirect(control_id: str) -> Any:
        return RedirectResponse(
            f"/controls/{control_id}/logic/builder", status_code=301
        )
