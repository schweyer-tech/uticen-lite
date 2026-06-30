"""Materialise the DataFrame at every node of a control pipeline.

This is the engine behind the per-step data inspector, the per-step / whole-pipeline
Excel exports, AND the live row-counts. It generalises :mod:`uticen_lite.pipeline.rowcounts`:
that probe kept only ``len(frame)`` after each node; this keeps the frames themselves.

Like rowcounts it reuses the compiler's *exact* node semantics
(:func:`uticen_lite.pipeline.compile._emit_node_lines` + the module-level custom-python
helpers), so what you inspect is byte-for-byte what the compiled ``test()`` computes. It
``exec``s generated code over real pandas frames, so it lives behind the runner-side boundary
(callers pass already-loaded frames) and never imports pandas at module import time.

The cache primitives (added for incremental recompute) are content-addressed: a node's key
hashes its ancestor-closure plus the version token of every source feeding it, so editing a
step changes that node's key and every descendant's key (their closures contain it) while
upstream keys are unchanged — "recompute from the edited step onward" falls out for free.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from uticen_lite.pipeline.compile import (
    _conditions,
    _emit_node_lines,
    _frame,
)
from uticen_lite.pipeline.model import Node, Pipeline
from uticen_lite.rules.render_rule import _mask_expr

_CACHE_MAX = 64  # bound the in-memory frame cache (single-user, localhost)


class MaterializeError(RuntimeError):
    """A pipeline's step-materialisation failed to evaluate over the given frames."""


def new_step_cache() -> OrderedDict[str, Any]:
    """An LRU-bounded frame cache for :func:`materialize_steps` (one per process)."""
    return OrderedDict()


def _emit_terminal_frame(node: Node) -> list[str]:
    """Lines assigning ``_f_<id>`` for a terminal node — the rows behind its count.

    A rule-style Test yields its violating rows (``input[mask]``); an empty-condition
    Test yields an empty slice (count 0). A custom-python ``test``-flavor terminal yields
    its helper's return value (the violations list), converted to a frame in Python.
    """
    if node.type == "custom_python" and node.config.get("flavor") == "test":
        return [f"{_frame(node.id)} = _node_fns[{node.id!r}]({_frame(node.inputs[0])})"]
    src = _frame(node.inputs[0])
    out = _frame(node.id)
    conds = _conditions(node.config.get("conditions", []))
    if not conds:
        return [f"{out} = {src}.iloc[0:0]"]
    combine = " & " if node.config.get("logic", "all") == "all" else " | "
    mask = combine.join(_mask_expr(c, frame=src) for c in conds)
    return [f"{out} = {src}[{mask}]"]


def _emit_materialize_body(pipeline: Pipeline, recompute: set[str]) -> str:
    """Emit ``def _materialize(frames, _node_fns, _seed): return {node_id: frame_or_list}``.

    Nodes in *recompute* emit their compute lines; all other nodes are seeded from
    ``_seed`` (a cached frame), so only the edited step and its descendants run.
    """
    order = pipeline.topological()
    terminal_ids = {t.id for t in pipeline.terminals}
    lines: list[str] = ["def _materialize(frames, _node_fns, _seed):", "    _out = {}"]
    for node in order:
        f = _frame(node.id)
        if node.id not in recompute:
            lines.append(f"    {f} = _seed[{node.id!r}]")
        elif node.id in terminal_ids:
            lines.extend(f"    {ln}" for ln in _emit_terminal_frame(node))
        elif node.type == "import":
            lines.append(f"    {f} = frames[{node.source_id!r}]")
        elif node.type == "custom_python":
            lines.append(f"    {f} = _node_fns[{node.id!r}]({_frame(node.inputs[0])})")
        else:
            lines.extend(
                f"    {ln}" for ln in _emit_node_lines(node, primary_source=None)
                if not ln.startswith("#")
            )
        lines.append(f"    _out[{node.id!r}] = {f}")
    lines.append("    return _out")
    return "\n".join(lines)


def materialize_steps(
    pipeline: Pipeline,
    frames: dict[str, Any],
    *,
    source_versions: dict[str, str] | None = None,
    cache: OrderedDict[str, Any] | None = None,
    recomputed_out: set[str] | None = None,
) -> dict[str, Any]:
    """Return ``{node_id: DataFrame}`` — the data at every step over *frames*.

    *frames* maps each Import node's ``source_id`` to a loaded pandas DataFrame. Returns
    ``{}`` when any referenced source is absent (mirrors :func:`compute_row_counts`), so the
    editor shows "—" until every Import source has a frame. The terminal node's frame is the
    rows behind its violation count.

    When both *cache* and *source_versions* are supplied, unchanged nodes are reused from the
    cache and only the edited step + its descendants recompute. *recomputed_out*, if given, is
    filled with the ids actually recomputed (for tests).
    """
    needed = set(pipeline.import_source_ids())
    if not needed.issubset(frames.keys()):
        return {}

    use_cache = cache is not None and source_versions is not None
    keys = _step_keys(pipeline, source_versions or {}) if use_cache else {}
    recompute, seed = _plan_recompute(pipeline, keys, cache if use_cache else None)
    if recomputed_out is not None:
        recomputed_out.clear()
        recomputed_out.update(recompute)

    raw = _exec_materialize(pipeline, frames, recompute, seed)
    out = _finalize_materialized(pipeline, raw, recompute)

    if use_cache:
        assert cache is not None  # guarded by use_cache; narrows type for mypy
        _update_cache(cache, keys, recompute, out)
    return out


def _plan_recompute(
    pipeline: Pipeline,
    keys: dict[str, str],
    cache: OrderedDict[str, Any] | None,
) -> tuple[set[str], dict[str, Any]]:
    """Decide which node ids to recompute and seed the rest from *cache*."""
    if cache is None:
        return {n.id for n in pipeline.nodes}, {}
    recompute = {n.id for n in pipeline.nodes if keys[n.id] not in cache}
    seed = {n.id: cache[keys[n.id]] for n in pipeline.nodes if keys[n.id] in cache}
    for n in pipeline.nodes:           # mark reused entries as recently used
        if keys[n.id] in cache:
            cache.move_to_end(keys[n.id])
    return recompute, seed


def _exec_materialize(
    pipeline: Pipeline,
    frames: dict[str, Any],
    recompute: set[str],
    seed: dict[str, Any],
) -> dict[str, Any]:
    """Compile + exec the per-node body and return the raw ``{node_id: value}`` map."""
    from uticen_lite.pipeline.compile import _emit_custom_helper

    namespace: dict[str, Any] = {}
    helper_parts = [
        _emit_custom_helper(n) for n in pipeline.nodes if n.type == "custom_python"
    ]
    body = _emit_materialize_body(pipeline, recompute)
    src = "\n\n\n".join([*helper_parts, body])
    try:
        # author code, guardrailed by lint
        exec(src, namespace)  # noqa: S102
        node_fns = {
            n.id: namespace[f"_node_{n.id}"]
            for n in pipeline.nodes if n.type == "custom_python"
        }
        return namespace["_materialize"](frames, node_fns, seed)
    # surface as a typed, contained error
    except Exception as exc:  # noqa: BLE001
        raise MaterializeError(str(exc)) from exc


def _finalize_materialized(
    pipeline: Pipeline,
    raw: dict[str, Any],
    recompute: set[str],
) -> dict[str, Any]:
    """Coerce custom-test terminal lists to DataFrames; return ``{node_id: frame}``."""
    import pandas as pd

    out: dict[str, Any] = {}
    terminal_ids = {t.id for t in pipeline.terminals}
    for nid, val in raw.items():
        node = pipeline.node(nid)
        if nid in recompute and nid in terminal_ids and node.type == "custom_python":
            val = pd.DataFrame(val)        # custom-test terminal returns a list
        out[nid] = val
    return out


def _update_cache(
    cache: OrderedDict[str, Any],
    keys: dict[str, str],
    recompute: set[str],
    out: dict[str, Any],
) -> None:
    """Store recomputed frames and evict LRU entries beyond the cap."""
    for nid in recompute:
        cache[keys[nid]] = out[nid]
    while len(cache) > _CACHE_MAX:
        cache.popitem(last=False)


def _ancestor_closure(pipeline: Pipeline, node: Node) -> list[Node]:
    """*node* and all its transitive inputs, in topological order."""
    keep: set[str] = set()

    def visit(nid: str) -> None:
        if nid in keep:
            return
        keep.add(nid)
        for s in pipeline.node(nid).inputs:
            visit(s)

    visit(node.id)
    return [n for n in pipeline.topological() if n.id in keep]


def _canonical_node(node: Node) -> dict[str, Any]:
    """The data-affecting fields of a node (narrative and title are cosmetic — excluded)."""
    return {
        "id": node.id, "type": node.type, "config": node.config,
        "inputs": list(node.inputs), "source_id": node.source_id,
    }


def _step_keys(pipeline: Pipeline, source_versions: dict[str, str]) -> dict[str, str]:
    """Map each node id → a content hash of its ancestor-closure + feeding source versions."""
    import hashlib
    import json

    keys: dict[str, str] = {}
    for node in pipeline.nodes:
        closure = _ancestor_closure(pipeline, node)
        src_ids = sorted({
            n.source_id for n in closure
            if n.type == "import" and n.source_id
        })
        payload = {
            "nodes": [_canonical_node(n) for n in closure],
            "sources": {sid: source_versions.get(sid, "") for sid in src_ids},
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        keys[node.id] = hashlib.sha256(blob.encode()).hexdigest()
    return keys
