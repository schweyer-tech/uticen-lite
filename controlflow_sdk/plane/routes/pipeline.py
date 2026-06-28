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
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from controlflow_sdk.pipeline.compile import compile_pipeline
from controlflow_sdk.pipeline.materialize import new_step_cache as _new_step_cache
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

_EMPTY_GRAPH: dict[str, Any] = {"nodes": []}
_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Stable, position-indexed palette for procedures (the Builder panel dot, the
# per-Test selector chips, and the Flowchart box outlines all index into this).
# Kept here (route module) because it is presentation-only; the colors are plain
# hex so the SVG/inline styles can use them directly (the CSS *tokens* still drive
# the surrounding chrome — learning 0005). If you ever duplicate this palette,
# keep both copies in sync.
_PROC_PALETTE = ["#4f7cff", "#18a999", "#d9822b", "#9b5de5", "#e5556e", "#3aa0c2"]


def procedure_color(position: int) -> str:
    """The stable chip/outline color for the procedure at *position* (wraps)."""
    return _PROC_PALETTE[position % len(_PROC_PALETTE)]


def _now_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()

# Process-wide, LRU-bounded cache of materialised step frames (single-user, localhost).
# Keyed inside materialize_steps by each node's ancestor-closure + source versions.
_STEP_CACHE = _new_step_cache()


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
    # Vertical order: DFS post-order from each terminal so a node's inputs sit
    # contiguously above it (each branch stays together). Then any node not
    # reachable from any terminal, in topological order, for determinism.
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

    for t in pipeline.terminals:
        _visit(t.id)
    for n in pipeline.topological():  # include any node not reachable from any terminal
        if n.id not in seen:
            seen.add(n.id)
            order.append(n)
    index = {n.id: i for i, n in enumerate(order)}

    terminal_ids = {t.id for t in pipeline.terminals}
    lanes = _assign_lanes(pipeline, order)
    lane_count = (max(lanes.values()) + 1) if lanes else 1

    # Per-procedure colors: each box takes its FIRST owning procedure's color and
    # the diagram carries a small legend. Best-effort — an incomplete graph yields
    # no colors, never a 500 (learning 0013).
    proc_color_by_node: dict[str, str] = {}
    legend: list[dict[str, Any]] = []
    try:
        from controlflow_sdk.pipeline.procedures import (
            derived_membership,
            effective_procedures,
        )

        eff = effective_procedures(pipeline)
        color_by_pid = {p.id: procedure_color(i) for i, p in enumerate(eff)}
        pos_by_pid = {p.id: i for i, p in enumerate(eff)}
        legend = [
            {"code": p.code or f"P{i + 1}", "name": p.name, "color": color_by_pid[p.id]}
            for i, p in enumerate(eff)
        ]
        for nid, pids in derived_membership(pipeline).items():
            if pids:
                first = min(pids, key=lambda p: pos_by_pid.get(p, 1 << 30))
                proc_color_by_node[nid] = color_by_pid[first]
    except Exception:  # noqa: BLE001 — incomplete graph → no colors, never 500 (0013)
        proc_color_by_node = {}
        legend = []

    boxes = [
        {
            "id": n.id,
            "type": n.type,
            "label": n.title or _node_label(n),
            "narrative": n.narrative or "",
            "count": counts.get(n.id),
            "row": i,
            "lane": lanes[n.id],
            "terminal": n.id in terminal_ids,
            "proc_color": proc_color_by_node.get(n.id),
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
    return {
        "boxes": boxes, "edges": edges, "rows": len(order),
        "lanes": lane_count, "procedures": legend,
    }


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

    for t in pipeline.terminals:
        if t.id not in lane:
            _walk(t.id, next_lane[0])
    for n in order:  # detached nodes (not reachable from any terminal) get their own lane
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
# Full-population frames → source versions → materialised steps → row-counts
# ---------------------------------------------------------------------------

def _load_full_frames(
    conn: sqlite3.Connection, root: Any, source_ids: list[str]
) -> dict[str, Any]:
    """Load the FULL coerced DataFrame for each bound source (by source_id).

    Decision (ii): the live badges and the inspector run over the full population —
    the incremental step cache keeps edits fast. Returns only the sources whose file
    exists; a missing frame makes materialize_steps return ``{}``.
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
            frames[sid] = source_for(_binding(src), root).load().df
        except Exception:  # noqa: BLE001 — a broken/adapters-missing file yields no frame
            continue
    return frames


def _source_versions(
    conn: sqlite3.Connection, root: Any, source_ids: list[str]
) -> dict[str, str]:
    """A cache-busting version token per source (current file path + mtime + size)."""
    out: dict[str, str] = {}
    for sid in source_ids:
        current = repo.get_current_file(conn, sid)
        if not current:
            continue
        stored = current["stored_path"]
        try:
            st = (root / stored).stat()
            out[sid] = f"{stored}:{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            out[sid] = stored
    return out


def _materialize_full(
    conn: sqlite3.Connection, root: Any, pipeline: Pipeline
) -> dict[str, Any]:
    """Best-effort ``{node_id: DataFrame}`` over the full population (cached).

    Returns ``{}`` when a source is unbound/missing or the probe fails — never raises
    into the request (learning 0013). The preview is best-effort and feeds only the
    badges/inspector, never the real run, so ANY failure degrades to ``{}``.
    """
    from controlflow_sdk.pipeline.materialize import materialize_steps

    try:
        sids = pipeline.import_source_ids()
        frames = _load_full_frames(conn, root, sids)
        versions = _source_versions(conn, root, sids)
        return materialize_steps(
            pipeline, frames, source_versions=versions, cache=_STEP_CACHE
        )
    except Exception:  # noqa: BLE001 — preview only; never raise into the request (0013)
        return {}


def _row_counts(
    conn: sqlite3.Connection, root: Any, pipeline: Pipeline
) -> dict[str, int]:
    """Best-effort full-population row-counts (``len`` over the materialised frames)."""
    return {nid: len(df) for nid, df in _materialize_full(conn, root, pipeline).items()}


def pd_isna(v: Any) -> bool:
    """NaN/NaT-safe truthiness for display (avoids importing pandas at module top)."""
    try:
        import pandas as pd
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _pipeline_for_view(control: dict | None) -> Pipeline | None:
    """The parsed pipeline the Builder cards/counts are rendered from.

    Mirrors ``_editor_context(for_builder=True)``: a raw-Python control has none; otherwise
    use the derived builder graph (so rule_spec/empty controls still show their scaffold).
    """
    if control is None or is_raw_python(control):
        return None
    graph = derive_builder_graph(control, list(control.get("source_ids") or [])) \
        or _graph_of(control)
    if not graph.get("nodes"):
        return None
    try:
        return parse_pipeline(graph)
    except PipelineError:
        return None


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
# AI-apply helpers (F3: auto-apply drafted rule_spec into the Test node)
# ---------------------------------------------------------------------------

def _merge_draft_into_graph(
    graph: dict[str, Any],
    draft: dict[str, Any],
    source_ids: list[str],
) -> dict[str, Any]:
    """Merge *draft* rule_spec fields into the terminal Test node of *graph*.

    If the graph has no Test node, fall back to the canonical scaffold
    (Import → Test) derived from the bound sources, then merge.  Returns a
    *new* graph dict; the input is not mutated.
    """
    import copy

    from controlflow_sdk.plane.logic_view import derive_builder_graph

    nodes: list[dict[str, Any]] = list(copy.deepcopy(graph.get("nodes") or []))

    # Find the terminal Test node (last test-type node, or append one).
    test_idx: int | None = None
    for i, n in enumerate(nodes):
        if n.get("type") == "test":
            test_idx = i  # keep the last one

    if test_idx is None:
        # No Test node yet — derive the scaffold from the control's sources and
        # merge into that scaffold's Test node.
        scaffold = derive_builder_graph({"source_ids": source_ids}, source_ids) or {
            "nodes": [
                {"id": "src", "type": "import", "source_id": source_ids[0] if source_ids else None,
                 "narrative": "", "config": {}, "inputs": []},
                {"id": "tst", "type": "test", "inputs": ["src"], "narrative": "",
                 "config": {"logic": "all", "conditions": []}},
            ]
        }
        nodes = list(copy.deepcopy(scaffold.get("nodes") or []))
        for i, n in enumerate(nodes):
            if n.get("type") == "test":
                test_idx = i
                break

    if test_idx is None:
        # Pathological: still no test node — append one.
        nodes.append({
            "id": "tst", "type": "test", "inputs": [], "narrative": "",
            "config": {"logic": "all", "conditions": []},
        })
        test_idx = len(nodes) - 1

    cfg = dict(nodes[test_idx].get("config") or {})
    cfg["conditions"] = list(draft.get("conditions") or [])
    cfg["logic"] = draft.get("logic", cfg.get("logic", "all"))
    if draft.get("severity"):
        cfg["severity"] = draft["severity"]
    if draft.get("item_key_column") is not None:
        cfg["item_key_column"] = draft["item_key_column"]
    if draft.get("description_template") is not None:
        cfg["description_template"] = draft["description_template"]
    nodes[test_idx] = dict(nodes[test_idx])
    nodes[test_idx]["config"] = cfg

    return {"nodes": nodes}


def _ai_apply_error(
    templates: Jinja2Templates, request: Request, message: str
) -> HTMLResponse:
    """Return an OOB error fragment that HTMX swaps into ``#ai-draft-panel``.

    The swap is out-of-band so the ``#pipe-cards`` target is left intact.
    """
    from fastapi.responses import HTMLResponse

    html = (
        f'<div id="ai-draft-panel" hx-swap-oob="innerHTML">'
        f'<div class="ai-notice" role="alert" style="padding:10px 14px;margin-top:10px;'
        f'font-size:13px;color:var(--status-critical);background:var(--status-critical-muted);'
        f'border:1px solid var(--status-critical);border-radius:var(--radius-input);">'
        f'{message}</div></div>'
    )
    return HTMLResponse(html, status_code=200)


# ---------------------------------------------------------------------------
# Render the editor
# ---------------------------------------------------------------------------

def _procedure_context(pipeline: Pipeline | None) -> dict[str, Any]:
    """Procedure view-model for the Builder panel, per-Test selector and chips.

    Returns three keys used by ``_procedures_panel.html`` / ``_pipe_node.html``:

    - ``procedures`` — the *effective* procedures (author-defined plus one-per-orphan
      terminal), each with its position color; this is the panel + the selector list.
    - ``node_procedures`` — ``{node_id: [{id, code, color}]}`` derived membership chips.
    - ``selected_procedure_for`` — ``{test_node_id: effective_owning_procedure_id}``.
      Pre-selecting the **effective** owner (not just ``config['procedure_id']``) is
      what makes a legacy/auto-derived control round-trip on save instead of resetting
      every Test to "unassigned" (the auto procedure's id is reflected, then persisted).

    Best-effort: an incomplete / unparseable graph yields empty data, never a 500
    (learning 0013).
    """
    empty: dict[str, Any] = {
        "procedures": [], "node_procedures": {}, "selected_procedure_for": {}
    }
    if pipeline is None:
        return empty
    try:
        from controlflow_sdk.pipeline.procedures import (
            derived_membership,
            effective_procedures,
            tests_for_procedure,
        )

        eff = effective_procedures(pipeline)
        color_by_pid = {p.id: procedure_color(i) for i, p in enumerate(eff)}
        pos_by_pid = {p.id: i for i, p in enumerate(eff)}
        procedures = [
            {
                "id": p.id,
                "code": p.code or f"P{i + 1}",
                "name": p.name,
                "assertion": p.assertion,
                "failure_threshold_pct": p.failure_threshold_pct,
                "failure_threshold_count": p.failure_threshold_count,
                "color": color_by_pid[p.id],
            }
            for i, p in enumerate(eff)
        ]
        code_by_pid = {p["id"]: p["code"] for p in procedures}
        mem = derived_membership(pipeline)
        node_procedures = {
            nid: [
                {
                    "id": pid,
                    "code": code_by_pid.get(pid, pid),
                    "color": color_by_pid.get(pid, "#888"),
                }
                for pid in sorted(pids, key=lambda p: pos_by_pid.get(p, 1 << 30))
            ]
            for nid, pids in mem.items()
        }
        selected_procedure_for: dict[str, str] = {}
        for p in eff:
            for t in tests_for_procedure(pipeline, p.id):
                selected_procedure_for[t.id] = p.id
        return {
            "procedures": procedures,
            "node_procedures": node_procedures,
            "selected_procedure_for": selected_procedure_for,
        }
    except Exception:  # noqa: BLE001 — incomplete graph → no panel data, never 500 (0013)
        return empty


def _card_bands(
    cards_pipeline: Pipeline | None,
    node_vms: list[dict[str, Any]],
    proc_ctx: dict[str, Any],
) -> dict[str, Any]:
    """Group the ordered node view-models into a shared Inputs band + per-procedure
    bands for the sectioned Builder. Best-effort: an unparsable graph (or any failure)
    puts every card in the Inputs band so the page still renders (learning 0013)."""
    vm_by_id = {vm["id"]: vm for vm in node_vms}
    fallback = {"shared": {"key": "__inputs__", "nodes": node_vms}, "procedures": []}
    if cards_pipeline is None:
        return fallback
    try:
        from controlflow_sdk.pipeline.procedures import group_nodes_by_band

        grouped = group_nodes_by_band(cards_pipeline)
        proc_by_id = {p["id"]: p for p in proc_ctx.get("procedures", [])}
        _proc_defaults: dict[str, Any] = {
            "code": "", "name": "", "assertion": "",
            "failure_threshold_pct": None, "failure_threshold_count": None, "color": "#888",
        }
        procedures = [
            {
                "key": band["id"],
                "proc": proc_by_id.get(band["id"], {"id": band["id"], **_proc_defaults}),
                "nodes": [vm_by_id[nid] for nid in band["node_ids"] if nid in vm_by_id],
            }
            for band in grouped["procedures"]
        ]
        shared_nodes = [vm_by_id[nid] for nid in grouped["shared"] if nid in vm_by_id]
        return {"shared": {"key": "__inputs__", "nodes": shared_nodes}, "procedures": procedures}
    except Exception:  # noqa: BLE001 — incomplete graph → one Inputs band, never 500 (0013)
        return fallback


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
        cards_pipeline = builder_parsed
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
        cards_pipeline = parsed

    from controlflow_sdk.plane.routes.ai import _ai_configured

    proc_ctx = _procedure_context(cards_pipeline)
    return {
        # Procedure panel + per-Test selector + derived chips (best-effort; 0013).
        **proc_ctx,
        "project": repo.get_project(conn) or {"name": ""},
        "control": control,
        "control_id": control_id,
        "active": "pipeline",
        "graph_json": builder_graph_json,
        "nodes": ordered_nodes,
        "bands": _card_bands(cards_pipeline, ordered_nodes, proc_ctx),
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
    terminal_ids = {t.id for t in pipeline.terminals}
    return {
        "id": node.id,
        "type": node.type,
        "label": _node_label(node),
        "title": node.title,
        "narrative": node.narrative,
        "config": node.config,
        "inputs": node.inputs,
        "source_id": node.source_id,
        "columns": _input_columns_for(pipeline, node, stream_columns),
        "count": counts.get(node.id),
        "terminal": node.id in terminal_ids,
        "errors": node_errors.get(node.id, []),
    }


def _raw_card_vm(raw: dict, node_errors: dict[str, list[str]]) -> dict[str, Any]:
    """View-model for a node card straight from the stored dict (unparsed)."""
    nid = str(raw.get("id", ""))
    return {
        "id": nid,
        "type": raw.get("type", ""),
        "label": raw.get("type", ""),
        "title": raw.get("title", ""),
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
    def logic_redirect(control_id: str) -> RedirectResponse:
        return RedirectResponse(f"/controls/{control_id}/logic/builder", status_code=302)

    # --- Step inspector route ------------------------------------------------

    _STEP_PAGE = 100

    @app.get("/controls/{control_id}/logic/step/{node_id}/data", response_class=HTMLResponse)
    def step_data(
        control_id: str,
        node_id: str,
        request: Request,
        page: int = 1,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        root = request.app.state.project_root
        control = repo.get_control(conn, control_id)
        ctx: dict[str, Any] = {
            "project": repo.get_project(conn) or {"name": ""},
            "control": control,
            "control_id": control_id, "node_id": node_id,
            "frame_available": False, "reason": "This step is not computable yet.",
        }
        # The inspector is best-effort over a derived/in-progress graph: any
        # unexpected failure (parse, materialize, paging) degrades to a friendly
        # page — never a 500 (learning 0013; 2026-06-27 review).
        try:
            pipeline = _pipeline_for_view(control)
            if pipeline is not None:
                try:
                    node = pipeline.node(node_id)
                    ctx["step_label"] = _node_label(node)
                except KeyError:
                    node = None
                steps = _materialize_full(conn, root, pipeline)
                frame = steps.get(node_id)
                if frame is not None:
                    total = len(frame)
                    page = max(1, page)
                    page_count = max(1, (total + _STEP_PAGE - 1) // _STEP_PAGE)
                    page = min(page, page_count)
                    start = (page - 1) * _STEP_PAGE
                    window = frame.iloc[start:start + _STEP_PAGE]
                    ctx.update({
                        "frame_available": True,
                        "header": [str(c) for c in frame.columns],
                        "rows": [[("" if pd_isna(v) else str(v)) for v in row]
                                 for row in window.itertuples(index=False, name=None)],
                        "total": total, "page": page, "page_count": page_count,
                        "start1": start + 1, "end1": start + len(window),
                    })
                elif not pipeline.import_source_ids() or node is not None:
                    ctx["reason"] = "Bind a data source (and complete this step) to inspect it."
        except Exception:  # noqa: BLE001 — never 500 the inspector (learning 0013)
            ctx["frame_available"] = False
            ctx["reason"] = "This step can't be inspected right now."
        return templates.TemplateResponse(request, "step_data.html", ctx)

    # --- Step export routes --------------------------------------------------

    @app.get("/controls/{control_id}/logic/step/{node_id}/export.xlsx", response_model=None)
    def step_export(
        control_id: str,
        node_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        from controlflow_sdk.adapters import xlsx_export
        from controlflow_sdk.plane.ingest import AdaptersUnavailable

        root = request.app.state.project_root
        pipeline = _pipeline_for_view(repo.get_control(conn, control_id))
        frame = None
        label = node_id
        if pipeline is not None:
            try:
                label = _node_label(pipeline.node(node_id))
            except KeyError:
                pass
            frame = _materialize_full(conn, root, pipeline).get(node_id)
        if frame is None:
            return PlainTextResponse("This step isn't computable yet.", status_code=409)
        try:
            data = xlsx_export.write_single_step(frame, label)
        except AdaptersUnavailable as exc:
            return PlainTextResponse(str(exc), status_code=503)
        return Response(
            content=data, media_type=_XLSX_MEDIA,
            headers={"content-disposition":
                     f'attachment; filename="{control_id}-{node_id}.xlsx"'},
        )

    @app.get("/controls/{control_id}/logic/export-steps.xlsx", response_model=None)
    def steps_export(
        control_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        from controlflow_sdk.adapters import xlsx_export
        from controlflow_sdk.plane.ingest import AdaptersUnavailable

        root = request.app.state.project_root
        control = repo.get_control(conn, control_id)
        pipeline = _pipeline_for_view(control)
        if pipeline is None:
            return PlainTextResponse("No inspectable pipeline yet.", status_code=409)
        frames = _materialize_full(conn, root, pipeline)
        if not frames:
            return PlainTextResponse("Bind a data source first.", status_code=409)
        steps = [(_node_label(n), frames[n.id])
                 for n in pipeline.topological() if n.id in frames]
        meta = {
            "control": control_id,
            "title": str((control or {}).get("title") or ""),
            "generated_at": _now_iso(),
        }
        try:
            data = xlsx_export.write_step_workbook(steps, meta)
        except AdaptersUnavailable as exc:
            return PlainTextResponse(str(exc), status_code=503)
        return Response(
            content=data, media_type=_XLSX_MEDIA,
            headers={"content-disposition":
                     f'attachment; filename="{control_id}-steps.xlsx"'},
        )

    # --- Logic sub-route GETs ------------------------------------------------

    @app.get("/controls/{control_id}/logic/builder", response_class=HTMLResponse)
    def logic_builder(
        control_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        root = request.app.state.project_root
        ctx = _editor_context(request, conn, root, control_id, for_builder=True)
        ctx["active"] = "logic"
        ctx["logic_tab"] = "builder"
        return templates.TemplateResponse(request, "logic_builder.html", ctx)

    @app.get("/controls/{control_id}/logic/ai", response_class=HTMLResponse)
    def logic_ai(
        control_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        root = request.app.state.project_root
        ctx = _editor_context(request, conn, root, control_id)
        ctx["active"] = "logic"
        ctx["logic_tab"] = "ai"
        return templates.TemplateResponse(request, "logic_ai.html", ctx)

    @app.get("/controls/{control_id}/logic/flowchart", response_class=HTMLResponse)
    def logic_flowchart(
        control_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
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
    ) -> HTMLResponse:
        root = request.app.state.project_root
        ctx = _editor_context(request, conn, root, control_id)
        ctx["active"] = "logic"
        ctx["logic_tab"] = "python"
        return templates.TemplateResponse(request, "logic_python.html", ctx)

    # --- Logic POST (save raw Python test_code) ------------------------------

    @app.post("/controls/{control_id}/logic/python")
    async def save_python(control_id: str, request: Request) -> RedirectResponse:
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

    @app.post("/controls/{control_id}/logic/builder", response_model=None)
    async def save_pipeline(control_id: str, request: Request) -> HTMLResponse | RedirectResponse:
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
            autosave = form.get("autosave") in ("1", "true")
            errors = _save_pipeline_graph(conn, control_id, graph)
            if errors:
                node_errors = _node_errors_from(errors)
                if autosave:
                    # For autosave errors: return the submitted graph as a
                    # cards fragment (422) so the browser stays in place and
                    # the newly inserted node remains visible with the error
                    # shown inline. A full-page 422 would drop the DOM node.
                    source_columns = _source_columns(conn)
                    sources = repo.list_sources(conn)
                    try:
                        err_parsed: Pipeline | None = parse_pipeline(graph)
                    except PipelineError:
                        err_parsed = None
                    err_stream_cols: dict[str, list[dict]] = {}
                    err_counts: dict[str, int] = {}
                    if err_parsed is not None:
                        err_counts = _row_counts(conn, root, err_parsed)
                        err_stream_cols = _stream_columns(err_parsed, source_columns)
                    err_nodes = (
                        [_card_vm(n, err_parsed, err_stream_cols, err_counts, node_errors)
                         for n in err_parsed.topological()]
                        if err_parsed is not None
                        else [_raw_card_vm(n, node_errors) for n in graph.get("nodes", [])]
                    )
                    proc_ctx = _procedure_context(err_parsed)
                    return templates.TemplateResponse(
                        request,
                        "partials/_pipe_cards.html",
                        {
                            "control_id": control_id,
                            "nodes": err_nodes,
                            "sources": sources,
                            "op_choices": OP_CHOICES,
                            "join_mode_choices": JOIN_MODE_CHOICES,
                            # Keep the per-Test selector + chips after the swap (0013).
                            **proc_ctx,
                            "bands": _card_bands(err_parsed, err_nodes, proc_ctx),
                        },
                        status_code=422,
                    )
                # Explicit Save: re-render the full page so the author sees
                # the save-errors banner and inline node errors.
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
            if autosave:
                # Return the re-rendered pipe-cards fragment so HTMX can swap
                # the cards in place — keeps the author in the builder without
                # a full-page redirect.
                source_columns = _source_columns(conn)
                sources = repo.list_sources(conn)
                try:
                    builder_parsed: Pipeline | None = parse_pipeline(graph)
                except PipelineError:
                    builder_parsed = None
                builder_stream_cols: dict[str, list[dict]] = {}
                builder_counts: dict[str, int] = {}
                if builder_parsed is not None:
                    builder_counts = _row_counts(conn, root, builder_parsed)
                    builder_stream_cols = _stream_columns(builder_parsed, source_columns)
                ordered_nodes = (
                    [_card_vm(n, builder_parsed, builder_stream_cols, builder_counts, {})
                     for n in builder_parsed.topological()]
                    if builder_parsed is not None
                    else [_raw_card_vm(n, {}) for n in graph.get("nodes", [])]
                )
                proc_ctx = _procedure_context(builder_parsed)
                return templates.TemplateResponse(
                    request,
                    "partials/_pipe_cards.html",
                    {
                        "control_id": control_id,
                        "nodes": ordered_nodes,
                        "sources": sources,
                        "op_choices": OP_CHOICES,
                        "join_mode_choices": JOIN_MODE_CHOICES,
                        # Keep the per-Test selector + chips after the swap.
                        **proc_ctx,
                        "bands": _card_bands(builder_parsed, ordered_nodes, proc_ctx),
                    },
                )
            return RedirectResponse(f"/controls/{control_id}/logic/builder", status_code=303)
        finally:
            conn.close()

    # --- Logic POST (convert to Python) --------------------------------------

    @app.post("/controls/{control_id}/logic/convert")
    async def convert_to_python(control_id: str, request: Request) -> RedirectResponse:
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

    # --- Logic POST (AI draft → auto-apply into terminal Test node) ----------

    @app.post("/controls/{control_id}/logic/ai-apply", response_class=HTMLResponse)
    async def ai_apply(control_id: str, request: Request) -> HTMLResponse:
        """Draft a rule_spec via the AI backend and merge it into the terminal
        Test node of the builder graph.  Returns the re-rendered ``#pipe-cards``
        inner HTML so HTMX can swap the cards in place — the author reviews and
        edits before clicking "Save pipeline".  No DB write is performed.

        On error the response body is an OOB fragment that drops the error
        banner into ``#ai-draft-panel`` while leaving ``#pipe-cards`` unchanged
        (HTMX ``hx-swap-oob`` in the response swaps the error target).
        """
        from controlflow_sdk.plane.routes.ai import _ai_config, _build_sample

        root = request.app.state.project_root
        conn = connect(root)
        try:
            form = await request.form()

            # ── current graph from the serialised hidden field ──────────────
            raw_json = form.get("pipeline_json")
            try:
                graph: dict[str, Any] = (
                    json.loads(str(raw_json)) if raw_json else dict(_EMPTY_GRAPH)
                )
            except (ValueError, TypeError):
                graph = dict(_EMPTY_GRAPH)

            # ── AI config guards ─────────────────────────────────────────────
            cfg = _ai_config(conn)
            if cfg is None:
                return _ai_apply_error(
                    templates, request,
                    "AI is not configured. Pick a provider in Settings.",
                )

            from controlflow_sdk.ai.providers import provider_key_present

            if not provider_key_present(cfg["provider"]):
                return _ai_apply_error(
                    templates, request,
                    "AI is not enabled — the selected provider's API key is not "
                    "set in this environment.",
                )

            control = repo.get_control(conn, control_id)
            source_ids = list((control or {}).get("source_ids") or [])
            if not source_ids:
                return _ai_apply_error(
                    templates, request,
                    "Bind a data source to this control first.",
                )

            sample = _build_sample(conn, root, source_ids[0])
            if sample is None:
                return _ai_apply_error(
                    templates, request, "Bind a data file to the source first.",
                )

            objective = str((control or {}).get("objective") or "")

            # ── draft ────────────────────────────────────────────────────────
            from controlflow_sdk.ai.draft import DraftError, draft_and_validate
            from controlflow_sdk.rules.spec import RuleSpecError

            try:
                draft = draft_and_validate(
                    objective=objective,
                    source_schema={"columns": sample["schema"]},
                    data_sample=sample,
                    provider=cfg["provider"],
                    model=cfg["model"],
                )
            except RuleSpecError as exc:
                return _ai_apply_error(
                    templates, request,
                    f"The drafted rule was malformed: {exc}",
                )
            except Exception as exc:  # noqa: BLE001
                msg = (
                    str(exc) if isinstance(exc, DraftError)
                    else "The AI provider could not produce a usable rule. "
                         "Try again or build the rule by hand."
                )
                return _ai_apply_error(templates, request, msg)

            # ── merge draft into the terminal Test node ──────────────────────
            merged_graph = _merge_draft_into_graph(
                graph, draft, source_ids
            )

            # ── render the pipe-cards partial ────────────────────────────────
            source_columns = _source_columns(conn)
            sources = repo.list_sources(conn)
            try:
                builder_parsed = parse_pipeline(merged_graph)
            except PipelineError:
                builder_parsed = None

            builder_stream_cols: dict[str, list[dict]] = {}
            builder_counts: dict[str, int] = {}
            if builder_parsed is not None:
                builder_counts = _row_counts(conn, root, builder_parsed)
                builder_stream_cols = _stream_columns(builder_parsed, source_columns)

            ordered_nodes = (
                [_card_vm(n, builder_parsed, builder_stream_cols, builder_counts, {})
                 for n in builder_parsed.topological()]
                if builder_parsed is not None
                else [_raw_card_vm(n, {}) for n in merged_graph.get("nodes", [])]
            )

            proc_ctx = _procedure_context(builder_parsed)
            return templates.TemplateResponse(
                request,
                "partials/_pipe_cards.html",
                {
                    "control_id": control_id,
                    "nodes": ordered_nodes,
                    "sources": sources,
                    "op_choices": OP_CHOICES,
                    "join_mode_choices": JOIN_MODE_CHOICES,
                    # Keep the per-Test selector + chips after the swap.
                    **proc_ctx,
                    "bands": _card_bands(builder_parsed, ordered_nodes, proc_ctx),
                },
                # The JS picks up the merged graph from this HX-Trigger event.
                headers={"HX-Trigger": json.dumps(
                    {"aiDraftApplied": json.dumps(merged_graph)}
                )},
            )
        finally:
            conn.close()

    # --- Legacy /pipeline GET redirect (301 permanent) -----------------------

    @app.get("/controls/{control_id}/pipeline")
    def pipeline_redirect(control_id: str) -> RedirectResponse:
        return RedirectResponse(
            f"/controls/{control_id}/logic/builder", status_code=301
        )
