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

import json
import logging
import sqlite3
from collections.abc import Callable, Generator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from uticen_lite.model.population import Population
from uticen_lite.pipeline.compile import compile_pipeline
from uticen_lite.pipeline.materialize import new_step_cache as _new_step_cache
from uticen_lite.pipeline.model import Pipeline, PipelineError, parse_pipeline
from uticen_lite.plane.logic_view import derive_builder_graph, is_raw_python
from uticen_lite.store import repo
from uticen_lite.store.db import connect

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
_LOGIC_TRACE_TEMPLATE = "logic_trace.html"
_PIPE_CARDS_PARTIAL = "partials/_pipe_cards.html"
_STEP_PAGE = 100

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
logger = logging.getLogger(__name__)


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


def _merge_join_columns(node: Any, out: dict[str, list[dict]]) -> list[dict]:
    """Union a Join node's two input streams' columns, deduped on ``original_name``."""
    seen: set[str] = set()
    merged: list[dict] = []
    for src in node.inputs:
        for c in out.get(src, []):
            if c["original_name"] not in seen:
                seen.add(c["original_name"])
                merged.append(c)
    return merged


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
            out[node.id] = _merge_join_columns(node, out)
        elif node.inputs:
            out[node.id] = out.get(node.inputs[0], [])
        else:
            out[node.id] = []
    return out


def _input_columns_for(
    node: Any, stream_columns: dict[str, list[dict]]
) -> list[dict]:
    """Columns a node's card should offer (its first input's output stream)."""
    if node.type == "import":
        return []
    if not node.inputs:
        return []
    return stream_columns.get(node.inputs[0], [])


def _diagram_order(pipeline: Pipeline) -> list[Any]:
    """DFS post-order from each terminal, then any unreached node topologically.

    A node's inputs sit contiguously above it (each branch stays together); used for
    lane assignment and as the ungrouped fallback order.
    """
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
    return order


def _diagram_proc_colors(
    pipeline: Pipeline,
) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, str], dict[str, str], dict[str, str]]:
    """Per-procedure color maps + legend for the diagram boxes.

    Best-effort — an incomplete graph yields no colors, never a 500 (learning 0013).
    Returns ``(proc_color_by_node, legend, color_by_pid, code_by_pid, name_by_pid)``.
    """
    proc_color_by_node: dict[str, str] = {}
    legend: list[dict[str, Any]] = []
    color_by_pid: dict[str, str] = {}
    code_by_pid: dict[str, str] = {}
    name_by_pid: dict[str, str] = {}
    try:
        from uticen_lite.pipeline.procedures import (
            derived_membership,
            effective_procedures,
        )

        eff = effective_procedures(pipeline)
        color_by_pid = {p.id: procedure_color(i) for i, p in enumerate(eff)}
        pos_by_pid = {p.id: i for i, p in enumerate(eff)}
        code_by_pid = {p.id: (p.code or f"P{i + 1}") for i, p in enumerate(eff)}
        name_by_pid = {p.id: p.name for p in eff}
        legend = [
            {"code": code_by_pid[p.id], "name": p.name, "color": color_by_pid[p.id]}
            for p in eff
        ]
        for nid, pids in derived_membership(pipeline).items():
            if pids:
                first = min(pids, key=lambda p: pos_by_pid.get(p, 1 << 30))
                proc_color_by_node[nid] = color_by_pid[first]
    except Exception:  # noqa: BLE001 — incomplete graph → no colors; never 500 (0013)
        proc_color_by_node, legend = {}, []
        color_by_pid, code_by_pid, name_by_pid = {}, {}, {}
    return proc_color_by_node, legend, color_by_pid, code_by_pid, name_by_pid


def _diagram(
    pipeline: Pipeline,
    counts: dict[str, int],
    collapsed: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """A view-model for the multi-lane server-rendered SVG flowchart (top-down).

    One box per node with a ``row`` (vertical position, top→bottom) and a ``lane``
    (horizontal column). Parallel branches that feed a Join sit in SEPARATE lanes
    that visibly converge at the Join box, so a join / multi-root pipeline no
    longer reads as a single linear chain. Each box also carries its type, a short
    label, and the surviving row-count (``None`` when unknown). Edges connect
    ``(from_row, from_lane) → (to_row, to_lane)`` so the template can draw a
    straight in-lane spine and a converging connector across lanes.

    Nodes are grouped into **swimlane bands** (``bands``): a shared "Inputs" band
    on top feeding one band per effective procedure, ordered by procedure position
    (Task 1's :func:`group_nodes_by_band`). A procedure whose id is in *collapsed*
    (and has ≥1 private node) contributes a single **summary box** instead of its
    private boxes; edges into a collapsed private node redirect to the summary and
    edges leaving one are dropped (the summary is a sink). Band grouping is
    best-effort: any failure falls back to the ungrouped layout with ``bands: []``
    so the flowchart still renders (never 500 — learning 0013).

    Layout is **presentation-only** — it never reorders execution. Compile/run
    still use :meth:`Pipeline.topological`; this view-model only positions boxes.
    """
    # Vertical order: DFS post-order from each terminal so a node's inputs sit
    # contiguously above it (each branch stays together). Then any node not
    # reachable from any terminal, in topological order, for determinism. Used for
    # lane assignment and as the ungrouped fallback order.
    order = _diagram_order(pipeline)
    index = {n.id: i for i, n in enumerate(order)}

    terminal_ids = {t.id for t in pipeline.terminals}
    lanes = _assign_lanes(pipeline, order)
    lane_count = (max(lanes.values()) + 1) if lanes else 1

    # Per-procedure colors: each box takes its FIRST owning procedure's color and
    # the diagram carries a small legend. Best-effort — an incomplete graph yields
    # no colors, never a 500 (learning 0013). The pid→code/name/color maps also
    # drive the band headers + collapsed summary labels below.
    proc_color_by_node, legend, color_by_pid, code_by_pid, name_by_pid = (
        _diagram_proc_colors(pipeline)
    )

    def _real_box(n: Any, row: int, lane: int) -> dict[str, Any]:
        return {
            "id": n.id,
            "type": n.type,
            "label": n.title or _node_label(n),
            "narrative": n.narrative or "",
            "count": counts.get(n.id),
            "row": row,
            "lane": lane,
            "terminal": n.id in terminal_ids,
            "proc_color": proc_color_by_node.get(n.id),
        }

    # Ungrouped fallback view-model (DFS post-order) — returned verbatim if band
    # grouping fails so the flowchart never 500s (learning 0013).
    fallback: dict[str, Any] = {
        "boxes": [_real_box(n, i, lanes[n.id]) for i, n in enumerate(order)],
        "edges": [
            {
                "from_row": index[src], "to_row": index[n.id],
                "from_lane": lanes[src], "to_lane": lanes[n.id],
            }
            for n in order
            for src in n.inputs
        ],
        "rows": len(order), "lanes": lane_count, "procedures": legend, "bands": [],
    }

    try:
        return _band_diagram(
            pipeline, order, lanes, collapsed, legend, _real_box,
            color_by_pid, code_by_pid, name_by_pid,
        )
    except Exception:  # noqa: BLE001 — band grouping best-effort; never 500 (0013)
        return fallback


def _band_render_plan(
    shared_ids: list[str],
    proc_bands: list[dict[str, Any]],
    node_ids_by_pid: dict[str, list[str]],
    lanes: dict[str, int],
    collapsed: frozenset[str],
) -> tuple[list[str], dict[str, str], dict[str, str], dict[str, int]]:
    """Render order + per-render-id band key + collapsed-private → summary id map.

    A collapsed procedure (id in *collapsed* with ≥1 private node) contributes one
    synthetic summary id (taking the band's **min lane**) in place of its private nodes.
    """
    render_order: list[str] = list(shared_ids)
    band_of: dict[str, str] = dict.fromkeys(shared_ids, "__inputs__")
    collapsed_private: dict[str, str] = {}
    summary_lane: dict[str, int] = {}
    for band in proc_bands:
        pid = band["id"]
        node_ids = node_ids_by_pid[pid]
        if pid in collapsed and node_ids:
            sid = "__sum__" + pid
            render_order.append(sid)
            band_of[sid] = pid
            summary_lane[sid] = min(
                (lanes[nid] for nid in node_ids if nid in lanes), default=0
            )
            collapsed_private.update(dict.fromkeys(node_ids, sid))
        else:
            render_order.extend(node_ids)
            band_of.update(dict.fromkeys(node_ids, pid))
    return render_order, band_of, collapsed_private, summary_lane


def _band_boxes(
    render_order: list[str],
    row_by_render: dict[str, int],
    summary_lane: dict[str, int],
    band_of: dict[str, str],
    node_ids_by_pid: dict[str, list[str]],
    lane_of: Callable[[str], int],
    real_box: Callable[[Any, int, int], dict[str, Any]],
    pipeline: Pipeline,
    code_by_pid: dict[str, str],
    name_by_pid: dict[str, str],
    color_by_pid: dict[str, str],
) -> list[dict[str, Any]]:
    """Real-node boxes, plus one summary box per collapsed band."""
    boxes: list[dict[str, Any]] = []
    for rid in render_order:
        row = row_by_render[rid]
        if rid in summary_lane:
            pid = band_of[rid]
            n_steps = len(node_ids_by_pid[pid])
            boxes.append({
                "id": rid, "summary": True, "band": pid, "type": "procedure",
                "label": f"{code_by_pid.get(pid, pid)} · {name_by_pid.get(pid, '')}"
                         f" — {n_steps} step{'s' if n_steps != 1 else ''}",
                "count": None, "row": row, "lane": lane_of(rid),
                "terminal": False, "proc_color": color_by_pid.get(pid),
            })
        else:
            boxes.append(real_box(pipeline.node(rid), row, lane_of(rid)))
    return boxes


def _band_edges(
    order: list[Any],
    collapsed_private: dict[str, str],
    row_by_render: dict[str, int],
    lane_of: Callable[[str], int],
) -> list[dict[str, Any]]:
    """Edges, redirecting collapsed-private targets to their summary and de-duplicating."""
    seen_edges: set[tuple[str, str]] = set()
    edges: list[dict[str, Any]] = []
    for n in order:
        for src in n.inputs:
            if src in collapsed_private:
                continue
            tgt = collapsed_private.get(n.id, n.id)
            if src == tgt or src not in row_by_render or tgt not in row_by_render:
                continue
            if (src, tgt) in seen_edges:
                continue
            seen_edges.add((src, tgt))
            edges.append({
                "from_row": row_by_render[src], "to_row": row_by_render[tgt],
                "from_lane": lane_of(src), "to_lane": lane_of(tgt),
            })
    return edges


def _band_list(
    proc_bands: list[dict[str, Any]],
    rows_by_band: dict[str, list[int]],
    collapsed: frozenset[str],
    code_by_pid: dict[str, str],
    name_by_pid: dict[str, str],
    color_by_pid: dict[str, str],
) -> list[dict[str, Any]]:
    """Inputs band first, then each non-empty procedure band with its inclusive row range."""
    def _toggle(pid: str) -> str:
        s = set(collapsed)
        s.discard(pid) if pid in s else s.add(pid)
        return ",".join(sorted(s))

    bands: list[dict[str, Any]] = []
    if rows_by_band.get("__inputs__"):
        rs = rows_by_band["__inputs__"]
        bands.append({
            "key": "__inputs__", "label": "Inputs & shared steps", "color": None,
            "collapsed": False, "row_start": min(rs), "row_end": max(rs),
            "toggle_collapsed": "",
        })
    for band in proc_bands:
        pid = band["id"]
        prows = rows_by_band.get(pid)
        if not prows:
            continue
        bands.append({
            "key": pid,
            "label": f"{code_by_pid.get(pid, pid)} · {name_by_pid.get(pid, '')}",
            "color": color_by_pid.get(pid), "collapsed": pid in collapsed,
            "row_start": min(prows), "row_end": max(prows), "toggle_collapsed": _toggle(pid),
        })
    return bands


def _band_diagram(
    pipeline: Pipeline,
    order: list[Any],
    lanes: dict[str, int],
    collapsed: frozenset[str],
    legend: list[dict[str, Any]],
    real_box: Callable[[Any, int, int], dict[str, Any]],
    color_by_pid: dict[str, str],
    code_by_pid: dict[str, str],
    name_by_pid: dict[str, str],
) -> dict[str, Any]:
    """Band-grouped (swimlane) view-model with the collapse transform applied.

    Render order is ``shared`` then each procedure band (Task 1's grouping), so each
    band occupies a CONTIGUOUS ``row`` range. A collapsed procedure (id in *collapsed*
    with ≥1 private node) contributes one synthetic summary id in place of its private
    nodes; the summary takes the **min lane** of the band's real nodes (it stands where
    the band stood). Raises on any malformed input — the caller falls back (never 500).
    """
    from uticen_lite.pipeline.procedures import group_nodes_by_band

    grouped = group_nodes_by_band(pipeline)
    shared_ids: list[str] = list(grouped["shared"])
    proc_bands: list[dict[str, Any]] = list(grouped["procedures"])
    node_ids_by_pid = {b["id"]: list(b["node_ids"]) for b in proc_bands}

    render_order, band_of, collapsed_private, summary_lane = _band_render_plan(
        shared_ids, proc_bands, node_ids_by_pid, lanes, collapsed
    )
    row_by_render = {rid: i for i, rid in enumerate(render_order)}

    def _lane_of(rid: str) -> int:
        return summary_lane[rid] if rid in summary_lane else lanes.get(rid, 0)

    boxes = _band_boxes(
        render_order, row_by_render, summary_lane, band_of, node_ids_by_pid,
        _lane_of, real_box, pipeline, code_by_pid, name_by_pid, color_by_pid,
    )
    edges = _band_edges(order, collapsed_private, row_by_render, _lane_of)

    rows_by_band: dict[str, list[int]] = {}
    for rid in render_order:
        rows_by_band.setdefault(band_of[rid], []).append(row_by_render[rid])

    bands = _band_list(
        proc_bands, rows_by_band, collapsed, code_by_pid, name_by_pid, color_by_pid
    )
    lane_count = (max((b["lane"] for b in boxes), default=0) + 1) if boxes else 1
    return {
        "boxes": boxes, "edges": edges, "rows": len(render_order),
        "lanes": lane_count, "procedures": legend, "bands": bands,
    }


def _walk_lane(
    pipeline: Pipeline, lane: dict[str, int], next_lane: list[int],
    node_id: str, my_lane: int,
) -> None:
    """Place *node_id* in *my_lane*; the first input continues the spine, extras fan right."""
    if node_id in lane:
        return
    lane[node_id] = my_lane
    if my_lane >= next_lane[0]:
        next_lane[0] = my_lane + 1
    node = pipeline.node(node_id)
    for i, src in enumerate(node.inputs):
        if i == 0:
            _walk_lane(pipeline, lane, next_lane, src, my_lane)  # spine
        else:
            branch_lane = next_lane[0]
            next_lane[0] += 1
            _walk_lane(pipeline, lane, next_lane, src, branch_lane)  # fan out right


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
    for t in pipeline.terminals:
        if t.id not in lane:
            _walk_lane(pipeline, lane, next_lane, t.id, next_lane[0])
    for n in order:  # detached nodes (not reachable from any terminal) get their own lane
        if n.id not in lane:
            branch_lane = next_lane[0]
            next_lane[0] += 1
            _walk_lane(pipeline, lane, next_lane, n.id, branch_lane)
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

def _load_source_populations(
    conn: sqlite3.Connection, root: Any, source_ids: list[str]
) -> dict[str, Population]:
    """Load the full :class:`Population` for each bound source (by source_id).

    Like :func:`_load_full_frames` but keeps the Population (its ``.df`` AND key
    columns), which the record trace needs to resolve the key column and to
    evaluate ``exists_in`` conditions. Returns only sources whose file exists; a
    broken/adapters-missing file is skipped.
    """
    from uticen_lite.adapters.files import source_for
    from uticen_lite.store.loader import _binding

    out: dict[str, Population] = {}
    for sid in source_ids:
        src = repo.get_source(conn, sid)
        current = repo.get_current_file(conn, sid)
        if not src or not current:
            continue
        fpath = root / current["stored_path"]
        if not fpath.is_file():
            continue
        try:
            out[sid] = source_for(_binding(src), root).load()
        except Exception:  # noqa: BLE001 — a broken/adapters-missing file yields no population
            continue
    return out


def _load_full_frames(
    conn: sqlite3.Connection, root: Any, source_ids: list[str]
) -> dict[str, Any]:
    """Load the FULL coerced DataFrame for each bound source (by source_id).

    Thin wrapper over :func:`_load_source_populations` (keeps the live badges /
    inspector unchanged) — the trace needs the Population, the badges only the df.
    """
    return {
        sid: pop.df
        for sid, pop in _load_source_populations(conn, root, source_ids).items()
    }


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
    from uticen_lite.pipeline.materialize import materialize_steps

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
    from uticen_lite.rules.render_rule import _render_python
    from uticen_lite.rules.spec import parse_rule_spec

    spec = compiled.rule_spec or {"logic": "all", "conditions": []}
    return _render_python(parse_rule_spec(spec))


# ---------------------------------------------------------------------------
# AI-apply helpers (F3: auto-apply drafted rule_spec into the Test node)
# ---------------------------------------------------------------------------

def _ensure_test_node(
    nodes: list[dict[str, Any]], source_ids: list[str]
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(nodes, terminal-test-index)``, deriving an Import→Test scaffold if absent.

    Prefers the *last* test node in *nodes*; if none, derives the canonical scaffold
    from the bound sources and uses its *first* test node; appends one as a last resort.
    """
    import copy

    from uticen_lite.plane.logic_view import derive_builder_graph

    # Find the terminal Test node (last test-type node, or append one).
    test_idx: int | None = None
    for i, n in enumerate(nodes):
        if n.get("type") == "test":
            test_idx = i  # keep the last one
    if test_idx is not None:
        return nodes, test_idx

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
            return nodes, i

    # Pathological: still no test node — append one.
    nodes.append({
        "id": "tst", "type": "test", "inputs": [], "narrative": "",
        "config": {"logic": "all", "conditions": []},
    })
    return nodes, len(nodes) - 1


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

    nodes: list[dict[str, Any]] = list(copy.deepcopy(graph.get("nodes") or []))
    nodes, test_idx = _ensure_test_node(nodes, source_ids)

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


def _ai_apply_error(message: str) -> HTMLResponse:
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
    """Procedure view-model for the Builder section headers, per-Test selector and chips.

    Returns three keys used by ``_pipe_cards.html`` section headers / ``_pipe_node.html``:

    - ``procedures`` — the *effective* procedures (author-defined plus one-per-orphan
      terminal), each with its position color; this is the section-header + selector list.
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
        from uticen_lite.pipeline.procedures import (
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
                # A SOLE auto-derived procedure keeps an EMPTY code for render/bundle
                # byte-identity (learning 0036). The builder hides the empty membership
                # pill at the view layer instead of forcing a label here (audit C2).
                "code": p.code or (f"P{i + 1}" if len(eff) > 1 else ""),
                "name": p.name,
                "assertion": p.assertion,
                "narrative": p.narrative,
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
    except Exception:  # noqa: BLE001 — incomplete graph → no panel data; never 500 (0013)
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
        from uticen_lite.pipeline.procedures import group_nodes_by_band

        grouped = group_nodes_by_band(cards_pipeline)
        proc_by_id = {p["id"]: p for p in proc_ctx.get("procedures", [])}
        _proc_defaults: dict[str, Any] = {
            "code": "", "name": "", "assertion": "", "narrative": "",
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
    except Exception:  # noqa: BLE001 — incomplete graph → one Inputs band; never 500 (0013)
        return fallback


def _card_vms(
    parsed: Pipeline | None,
    graph: dict[str, Any],
    stream_columns: dict[str, list[dict]],
    counts: dict[str, int],
    node_errors: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Ordered node view-models: parsed cards when available, else raw stored dicts."""
    if parsed is not None:
        return [
            _card_vm(n, parsed, stream_columns, counts, node_errors)
            for n in parsed.topological()
        ]
    return [_raw_card_vm(n, node_errors) for n in graph.get("nodes", [])]


def _stored_pipeline_view(
    conn: sqlite3.Connection,
    root: Any,
    graph: dict[str, Any],
    source_columns: dict[str, list[dict]],
) -> tuple[
    Pipeline | None, str | None, dict[str, Any] | None, str,
    dict[str, list[dict]], dict[str, int],
]:
    """Parse the stored graph + build its diagram / generated-Python / counts (best-effort).

    Returns ``(parsed, parse_error, diagram, generated, stream_columns, counts)``.
    """
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
        except Exception as exc:  # noqa: BLE001 — show parse errors; never 500
            parse_error = parse_error or f"could not generate Python: {exc}"
    return parsed, parse_error, diagram, generated, stream_columns, counts


def _builder_cards(
    conn: sqlite3.Connection,
    root: Any,
    builder_graph: dict[str, Any],
    source_columns: dict[str, list[dict]],
    node_errors: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], Pipeline | None]:
    """Ordered cards rendered from the *derived* builder graph (+ its parsed pipeline)."""
    builder_parsed: Pipeline | None = None
    if builder_graph.get("nodes"):
        try:
            builder_parsed = parse_pipeline(builder_graph)
        except PipelineError:
            builder_parsed = None
    builder_stream_cols: dict[str, list[dict]] = {}
    builder_counts: dict[str, int] = {}
    if builder_parsed is not None:
        builder_counts = _row_counts(conn, root, builder_parsed)
        builder_stream_cols = _stream_columns(builder_parsed, source_columns)
    ordered_nodes = _card_vms(
        builder_parsed, builder_graph, builder_stream_cols, builder_counts, node_errors
    )
    return ordered_nodes, builder_parsed


def _editor_context(
    conn: sqlite3.Connection, root: Any, control_id: str,
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
    builder_graph: dict[str, Any] | None = (
        derive_builder_graph(control, list(control.get("source_ids") or []))
        if control is not None
        else None
    )

    # The stored graph drives the diagram / generated Python; the Builder cards may
    # use a derived graph.
    parsed, parse_error, diagram, generated, stream_columns, counts = _stored_pipeline_view(
        conn, root, graph, source_columns
    )

    # For the Builder, render nodes from the *derived* graph so rule-spec / empty
    # controls show a meaningful scaffold.  Fall back to the stored graph otherwise.
    if for_builder and builder_graph is not None and not raw_python:
        ordered_nodes, cards_pipeline = _builder_cards(
            conn, root, builder_graph, source_columns, node_errors or {}
        )
        # The form must submit the DERIVED graph (the one the cards were rendered
        # from), not the stored graph — the stored graph may be empty/absent for a
        # rule_spec or fresh control, so submitting it would silently discard edits.
        builder_graph_json = json.dumps(builder_graph)
    else:
        ordered_nodes = _card_vms(parsed, graph, stream_columns, counts, node_errors or {})
        builder_graph_json = json.dumps(graph)
        cards_pipeline = parsed

    from uticen_lite.plane.routes.ai import _ai_configured

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
        "columns": _input_columns_for(node, stream_columns),
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
# Route implementations (kept module-level so ``register`` stays flat)
# ---------------------------------------------------------------------------

def _pipe_cards_fragment(
    templates: Jinja2Templates,
    request: Request,
    conn: sqlite3.Connection,
    root: Any,
    control_id: str,
    graph: dict[str, Any],
    node_errors: dict[str, list[str]],
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    """Re-render the ``#pipe-cards`` partial for *graph* (shared by autosave + AI-apply)."""
    source_columns = _source_columns(conn)
    sources = repo.list_sources(conn)
    try:
        parsed: Pipeline | None = parse_pipeline(graph)
    except PipelineError:
        parsed = None
    stream_cols: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    if parsed is not None:
        counts = _row_counts(conn, root, parsed)
        stream_cols = _stream_columns(parsed, source_columns)
    nodes = _card_vms(parsed, graph, stream_cols, counts, node_errors)
    proc_ctx = _procedure_context(parsed)
    return templates.TemplateResponse(
        request,
        _PIPE_CARDS_PARTIAL,
        {
            "control_id": control_id,
            "nodes": nodes,
            "sources": sources,
            "op_choices": OP_CHOICES,
            "join_mode_choices": JOIN_MODE_CHOICES,
            # Keep the per-Test selector + chips after the swap (0013).
            **proc_ctx,
            "bands": _card_bands(parsed, nodes, proc_ctx),
        },
        status_code=status_code,
        headers=headers,
    )


def _step_page_ctx(frame: Any, page: int) -> dict[str, Any]:
    """Paged view-model (header + windowed rows) for one materialised step frame."""
    total = len(frame)
    page = max(1, page)
    page_count = max(1, (total + _STEP_PAGE - 1) // _STEP_PAGE)
    page = min(page, page_count)
    start = (page - 1) * _STEP_PAGE
    window = frame.iloc[start:start + _STEP_PAGE]
    return {
        "frame_available": True,
        "header": [str(c) for c in frame.columns],
        "rows": [[("" if pd_isna(v) else str(v)) for v in row]
                 for row in window.itertuples(index=False, name=None)],
        "total": total, "page": page, "page_count": page_count,
        "start1": start + 1, "end1": start + len(window),
    }


def _step_data_ctx(
    conn: sqlite3.Connection, root: Any, control: dict | None, node_id: str, page: int,
) -> dict[str, Any]:
    """Best-effort inspector view-model for one step: paged rows or a friendly reason.

    Never raises into the request — any failure degrades to a friendly page (0013).
    """
    ctx: dict[str, Any] = {"frame_available": False, "reason": "This step is not computable yet."}
    try:
        pipeline = _pipeline_for_view(control)
        if pipeline is None:
            return ctx
        try:
            node = pipeline.node(node_id)
            ctx["step_label"] = _node_label(node)
        except KeyError:
            node = None
        frame = _materialize_full(conn, root, pipeline).get(node_id)
        if frame is not None:
            ctx.update(_step_page_ctx(frame, page))
        elif not pipeline.import_source_ids() or node is not None:
            ctx["reason"] = "Bind a data source (and complete this step) to inspect it."
    except Exception:  # noqa: BLE001 — never 500 the inspector (learning 0013)
        ctx["frame_available"] = False
        ctx["reason"] = "This step can't be inspected right now."
    return ctx


def _render_step_data(
    templates: Jinja2Templates,
    request: Request,
    conn: sqlite3.Connection,
    control_id: str,
    node_id: str,
    page: int,
) -> HTMLResponse:
    root = request.app.state.project_root
    control = repo.get_control(conn, control_id)
    ctx: dict[str, Any] = {
        "project": repo.get_project(conn) or {"name": ""},
        "control": control,
        "control_id": control_id, "node_id": node_id,
    }
    # The inspector is best-effort over a derived/in-progress graph: any unexpected
    # failure (parse, materialize, paging) degrades to a friendly page (0013).
    ctx.update(_step_data_ctx(conn, root, control, node_id, page))
    return templates.TemplateResponse(request, "step_data.html", ctx)


def _render_step_export(
    request: Request, conn: sqlite3.Connection, control_id: str, node_id: str,
) -> Response:
    from uticen_lite.adapters import xlsx_export
    from uticen_lite.plane.ingest import AdaptersUnavailable

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


def _render_steps_export(
    request: Request, conn: sqlite3.Connection, control_id: str,
) -> Response:
    from uticen_lite.adapters import xlsx_export
    from uticen_lite.plane.ingest import AdaptersUnavailable

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


def _render_logic_flowchart(
    templates: Jinja2Templates,
    request: Request,
    conn: sqlite3.Connection,
    control_id: str,
    collapsed: str,
) -> HTMLResponse:
    root = request.app.state.project_root
    ctx = _editor_context(conn, root, control_id)
    ctx["active"] = "logic"
    ctx["logic_tab"] = "flowchart"
    # Re-render the diagram with the requested procedure bands collapsed. The
    # base context already built an (uncollapsed) diagram; only recompute when
    # a non-empty collapse set is requested and there is a pipeline to view.
    collapsed_ids = frozenset(c for c in collapsed.split(",") if c)
    if collapsed_ids and ctx.get("diagram") is not None:
        parsed = _pipeline_for_view(repo.get_control(conn, control_id))
        if parsed is not None:
            ctx["diagram"] = _diagram(parsed, _row_counts(conn, root, parsed), collapsed_ids)
    ctx["collapsed"] = ",".join(sorted(collapsed_ids))
    # HTMX fragment request → return just the swappable flowchart card.
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/_pipe_diagram_card.html", ctx)
    return templates.TemplateResponse(request, "logic_flowchart.html", ctx)


def _trace_frames(
    conn: sqlite3.Connection, root: Any, pipeline: Pipeline, sources: dict[str, Any],
) -> dict[str, Any]:
    """Seed frames with raw import-node DataFrames so ``trace_record`` can always
    locate the record by key, even when ``_materialize_full`` fails (e.g. a
    type-mismatch condition degrades to per-condition detail from the trace)."""
    frames: dict[str, Any] = {
        node.id: sources[node.source_id].df
        for node in pipeline.topological()
        if node.type == "import" and node.source_id in sources
    }
    frames.update(_materialize_full(conn, root, pipeline))
    return frames


def _render_logic_trace(
    templates: Jinja2Templates,
    request: Request,
    conn: sqlite3.Connection,
    control_id: str,
    key: str,
) -> HTMLResponse:
    from uticen_lite.pipeline.trace import trace_record

    root = request.app.state.project_root
    control = repo.get_control(conn, control_id)
    ctx: dict[str, Any] = {
        "project": repo.get_project(conn) or {"name": ""},
        "control": control,
        "control_id": control_id,
        "active": "logic",
        "logic_tab": "trace",
        "key": key,
        "examples": [],
        "trace": None,
        "message": "",
    }
    # The Trace tab must never 500: any failure degrades to a friendly page
    # (learnings 0013/0033).
    try:
        if control is None:
            ctx["message"] = "Control not found."
            return templates.TemplateResponse(request, _LOGIC_TRACE_TEMPLATE, ctx)
        if is_raw_python(control):
            ctx["message"] = (
                "Tracing needs the rule builder — this control is authored in Python."
            )
            return templates.TemplateResponse(request, _LOGIC_TRACE_TEMPLATE, ctx)
        pipeline = _pipeline_for_view(control)
        if pipeline is None:
            ctx["message"] = (
                "This control isn't ready to trace yet — add logic in the Builder first."
            )
            return templates.TemplateResponse(request, _LOGIC_TRACE_TEMPLATE, ctx)
        sources = _load_source_populations(conn, root, pipeline.import_source_ids())
        import_ids = pipeline.import_source_ids()
        primary = sources.get(import_ids[0]) if import_ids else None
        if primary is not None and primary.key_columns:
            kc = primary.key_columns[0]
            ctx["examples"] = (
                primary.df[kc].astype(str).drop_duplicates().head(5).tolist()
            )
        if not sources:
            ctx["message"] = "Bind a data source to trace a record."
            return templates.TemplateResponse(request, _LOGIC_TRACE_TEMPLATE, ctx)
        if key:
            ctx["trace"] = trace_record(
                pipeline, _trace_frames(conn, root, pipeline, sources), key, sources
            )
    except Exception:  # noqa: BLE001 — the Trace tab must never 500
        logger.exception("Unexpected error tracing record in control %r", control_id)
        ctx["trace"] = None
        ctx["message"] = "This control can't be traced right now."
    return templates.TemplateResponse(request, _LOGIC_TRACE_TEMPLATE, ctx)


async def _save_python_impl(request: Request, control_id: str) -> RedirectResponse:
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


async def _save_pipeline_impl(
    templates: Jinja2Templates, request: Request, control_id: str,
) -> HTMLResponse | RedirectResponse:
    from uticen_lite.plane.routes.controls import _save_pipeline_graph

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
                # For autosave errors: return the submitted graph as a cards
                # fragment (422) so the browser stays in place and the newly
                # inserted node remains visible with the error shown inline.
                return _pipe_cards_fragment(
                    templates, request, conn, root, control_id, graph, node_errors,
                    status_code=422,
                )
            # Explicit Save: re-render the full page so the author sees the
            # save-errors banner and inline node errors.
            ctx = _editor_context(
                conn, root, control_id,
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
            # Return the re-rendered pipe-cards fragment so HTMX can swap the
            # cards in place — keeps the author in the builder without a redirect.
            return _pipe_cards_fragment(templates, request, conn, root, control_id, graph, {})
        return RedirectResponse(f"/controls/{control_id}/logic/builder", status_code=303)
    finally:
        conn.close()


async def _convert_to_python_impl(request: Request, control_id: str) -> RedirectResponse:
    """One-way door (§9): compile the pipeline → ``test(pop, sources)`` and
    switch the control to ``test_kind='python'``, dropping the author into the
    existing CodeMirror escape hatch pre-filled with the stitched code."""
    root = request.app.state.project_root
    conn = connect(root)
    try:
        control = repo.get_control(conn, control_id)
        if control is None or not control.get("pipeline"):
            return RedirectResponse(f"/controls/{control_id}/logic/python", status_code=303)
        try:
            parsed = parse_pipeline(control["pipeline"])
        except PipelineError:
            return RedirectResponse(f"/controls/{control_id}/logic/python", status_code=303)
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


def _resolve_ai_context(
    conn: sqlite3.Connection, root: Any, control_id: str
) -> HTMLResponse | tuple[dict[str, Any], dict[str, Any], str, list[str]]:
    """Validate AI config + bound sources for ai-apply.

    Returns an error fragment (``HTMLResponse``) to short-circuit, or
    ``(cfg, sample, objective, source_ids)`` when ready to draft.
    """
    from uticen_lite.plane.routes.ai import _ai_config, _build_sample

    cfg = _ai_config(conn)
    if cfg is None:
        return _ai_apply_error("AI is not configured. Pick a provider in Settings.")

    from uticen_lite.ai.providers import provider_key_present

    if not provider_key_present(cfg["provider"]):
        return _ai_apply_error(
            "AI is not enabled — the selected provider's API key is not "
            "set in this environment.",
        )

    control = repo.get_control(conn, control_id)
    source_ids = list((control or {}).get("source_ids") or [])
    if not source_ids:
        return _ai_apply_error("Bind a data source to this control first.")

    sample = _build_sample(conn, root, source_ids[0])
    if sample is None:
        return _ai_apply_error("Bind a data file to the source first.")

    objective = str((control or {}).get("objective") or "")
    return cfg, sample, objective, source_ids


async def _ai_apply_impl(
    templates: Jinja2Templates, request: Request, control_id: str,
) -> HTMLResponse:
    """Draft a rule_spec via the AI backend and merge it into the terminal
    Test node of the builder graph.  Returns the re-rendered ``#pipe-cards``
    inner HTML so HTMX can swap the cards in place — the author reviews and
    edits before clicking "Save pipeline".  No DB write is performed.

    On error the response body is an OOB fragment that drops the error
    banner into ``#ai-draft-panel`` while leaving ``#pipe-cards`` unchanged
    (HTMX ``hx-swap-oob`` in the response swaps the error target).
    """
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

        # ── AI config + source guards ────────────────────────────────────
        resolved = _resolve_ai_context(conn, root, control_id)
        if isinstance(resolved, HTMLResponse):
            return resolved
        cfg, sample, objective, source_ids = resolved

        # ── draft ────────────────────────────────────────────────────────
        from uticen_lite.ai.draft import DraftError, draft_and_validate
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
            return _ai_apply_error(f"The drafted rule was malformed: {exc}")
        except Exception as exc:  # noqa: BLE001
            msg = (
                str(exc) if isinstance(exc, DraftError)
                else "The AI provider could not produce a usable rule. "
                     "Try again or build the rule by hand."
            )
            return _ai_apply_error(msg)

        # ── merge draft into the terminal Test node + render the cards ────
        merged_graph = _merge_draft_into_graph(graph, draft, source_ids)
        return _pipe_cards_fragment(
            templates, request, conn, root, control_id, merged_graph, {},
            # The JS picks up the merged graph from this HX-Trigger event.
            headers={"HX-Trigger": json.dumps(
                {"aiDraftApplied": json.dumps(merged_graph)}
            )},
        )
    finally:
        conn.close()


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

    @app.get("/controls/{control_id}/logic/step/{node_id}/data", response_class=HTMLResponse)
    def step_data(
        control_id: str,
        node_id: str,
        request: Request,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
        page: int = 1,
    ) -> HTMLResponse:
        return _render_step_data(templates, request, conn, control_id, node_id, page)

    # --- Step export routes --------------------------------------------------

    @app.get("/controls/{control_id}/logic/step/{node_id}/export.xlsx", response_model=None)
    def step_export(
        control_id: str,
        node_id: str,
        request: Request,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    ) -> Response:
        return _render_step_export(request, conn, control_id, node_id)

    @app.get("/controls/{control_id}/logic/export-steps.xlsx", response_model=None)
    def steps_export(
        control_id: str,
        request: Request,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    ) -> Response:
        return _render_steps_export(request, conn, control_id)

    # --- Logic sub-route GETs ------------------------------------------------

    @app.get("/controls/{control_id}/logic/builder", response_class=HTMLResponse)
    def logic_builder(
        control_id: str,
        request: Request,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    ) -> HTMLResponse:
        root = request.app.state.project_root
        ctx = _editor_context(conn, root, control_id, for_builder=True)
        ctx["active"] = "logic"
        ctx["logic_tab"] = "builder"
        return templates.TemplateResponse(request, "logic_builder.html", ctx)

    @app.get("/controls/{control_id}/logic/ai", response_class=HTMLResponse)
    def logic_ai(
        control_id: str,
        request: Request,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    ) -> HTMLResponse:
        root = request.app.state.project_root
        ctx = _editor_context(conn, root, control_id)
        ctx["active"] = "logic"
        ctx["logic_tab"] = "ai"
        return templates.TemplateResponse(request, "logic_ai.html", ctx)

    @app.get("/controls/{control_id}/logic/flowchart", response_class=HTMLResponse)
    def logic_flowchart(
        control_id: str,
        request: Request,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
        collapsed: str = "",
    ) -> HTMLResponse:
        return _render_logic_flowchart(templates, request, conn, control_id, collapsed)

    @app.get("/controls/{control_id}/logic/trace", response_class=HTMLResponse)
    def logic_trace(
        control_id: str,
        request: Request,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
        key: str = "",
    ) -> HTMLResponse:
        return _render_logic_trace(templates, request, conn, control_id, key)

    @app.get("/controls/{control_id}/logic/python", response_class=HTMLResponse)
    def logic_python(
        control_id: str,
        request: Request,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    ) -> HTMLResponse:
        root = request.app.state.project_root
        ctx = _editor_context(conn, root, control_id)
        ctx["active"] = "logic"
        ctx["logic_tab"] = "python"
        return templates.TemplateResponse(request, "logic_python.html", ctx)

    # --- Logic POSTs ---------------------------------------------------------

    @app.post("/controls/{control_id}/logic/python")
    async def save_python(control_id: str, request: Request) -> RedirectResponse:
        return await _save_python_impl(request, control_id)

    @app.post("/controls/{control_id}/logic/builder", response_model=None)
    async def save_pipeline(
        control_id: str, request: Request
    ) -> HTMLResponse | RedirectResponse:
        return await _save_pipeline_impl(templates, request, control_id)

    @app.post("/controls/{control_id}/logic/convert")
    async def convert_to_python(control_id: str, request: Request) -> RedirectResponse:
        return await _convert_to_python_impl(request, control_id)

    @app.post("/controls/{control_id}/logic/ai-apply", response_class=HTMLResponse)
    async def ai_apply(control_id: str, request: Request) -> HTMLResponse:
        return await _ai_apply_impl(templates, request, control_id)

    # --- Legacy /pipeline GET redirect (301 permanent) -----------------------

    @app.get("/controls/{control_id}/pipeline")
    def pipeline_redirect(control_id: str) -> RedirectResponse:
        return RedirectResponse(
            f"/controls/{control_id}/logic/builder", status_code=301
        )
