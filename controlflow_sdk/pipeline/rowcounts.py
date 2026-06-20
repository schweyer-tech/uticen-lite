"""Live row-counts at every joint of a control pipeline (spec §4, §5).

With no AI to ask "is this right?", the **count surviving each node** is how a
non-developer sees a mistake — a node dropping to 0 is the tell. This is the
offline feedback loop the spec calls for: ``1,204 → Filter: 88 → Join: 88 →
Test: 6``.

The counter reuses the compiler's *exact* node semantics by emitting the same
per-node pandas lines (:func:`controlflow_sdk.pipeline.compile._emit_node_lines`)
and recording ``len(frame)`` after each assignment, so the counts can never
drift from what the compiled ``test()`` actually computes. The terminal node's
count is the number of violations it would emit.

This module is **not** Pyodide-safe in the same way the model/compile modules are
— it ``exec``s the generated body over real (pandas) frames. It therefore lives
behind the runner-side boundary: callers pass already-loaded frames (e.g. capped
samples). It never imports pandas at module import time.
"""

from __future__ import annotations

from typing import Any

from controlflow_sdk.pipeline.compile import (
    _conditions,
    _emit_node_lines,
    _frame,
)
from controlflow_sdk.pipeline.model import Pipeline
from controlflow_sdk.rules.render_rule import _mask_expr


class RowCountError(RuntimeError):
    """A pipeline's row-count probe failed to evaluate over the sample frames."""


def _emit_counts_body(pipeline: Pipeline) -> str:
    """Emit a ``_probe(frames)`` function that returns ``{node_id: surviving}``.

    ``frames`` maps an Import node's ``source_id`` to its loaded DataFrame. Each
    non-terminal node assigns ``_f_<id>`` exactly as the compiler does, then the
    body records ``len(_f_<id>)``. The terminal node records the violation count:
    the masked length for a rule-style Test, or ``len`` of the custom helper's
    output for a custom-python test-flavor terminal.
    """
    order = pipeline.topological()
    terminal = pipeline.terminal

    lines: list[str] = ["def _probe(frames, _node_fns):", "    _counts = {}"]
    for node in order:
        if node.id == terminal.id:
            continue
        if node.type == "import":
            # Read the bound source frame directly (no pop/sources split here —
            # the caller supplies every Import's frame by source_id).
            lines.append(f"    {_frame(node.id)} = frames[{node.source_id!r}]")
        elif node.type == "custom_python":
            src = _frame(node.inputs[0])
            lines.append(f"    {_frame(node.id)} = _node_fns[{node.id!r}]({src})")
        else:
            for ln in _emit_node_lines(node, primary_source=None):
                if ln.startswith("#"):
                    continue
                lines.append(f"    {ln}")
        lines.append(f"    _counts[{node.id!r}] = len({_frame(node.id)})")

    lines.extend("    " + ln for ln in _emit_terminal_count(terminal, pipeline))
    lines.append("    return _counts")
    return "\n".join(lines)


def _emit_terminal_count(node: Any, pipeline: Pipeline) -> list[str]:
    """Lines recording the terminal node's surviving (violation) count."""
    if node.type == "custom_python" and node.config.get("flavor") == "test":
        src = _frame(node.inputs[0])
        return [
            f"_term = _node_fns[{node.id!r}]({src})",
            f"_counts[{node.id!r}] = len(_term)",
        ]
    src = _frame(node.inputs[0])
    conds = _conditions(node.config.get("conditions", []))
    if not conds:
        return [f"_counts[{node.id!r}] = 0"]
    combine = " & " if node.config.get("logic", "all") == "all" else " | "
    mask = combine.join(_mask_expr(c, frame=src) for c in conds)
    return [f"_counts[{node.id!r}] = int(({mask}).sum())"]


def compute_row_counts(
    pipeline: Pipeline, frames: dict[str, Any]
) -> dict[str, int]:
    """Return ``{node_id: rows surviving}`` for *pipeline* over *frames*.

    ``frames`` maps each Import node's ``source_id`` to a loaded pandas
    DataFrame (e.g. a capped sample). Custom Python nodes are compiled to the
    same module-level ``_node_<id>(rows)`` helpers the runner uses, so the
    counts reflect exactly what the compiled ``test()`` would compute.

    Returns ``{}`` (rather than raising) when the pipeline references a source
    not present in ``frames`` — the editor can then show "—" for every node
    until all Import sources have a loaded sample.
    """
    needed = set(pipeline.import_source_ids())
    if not needed.issubset(frames.keys()):
        return {}

    from controlflow_sdk.pipeline.compile import _emit_custom_helper

    namespace: dict[str, Any] = {}
    # Custom-python helpers first (module-level, starved of `sources`).
    node_fns: dict[str, Any] = {}
    helper_src_parts: list[str] = []
    for node in pipeline.nodes:
        if node.type == "custom_python":
            helper_src_parts.append(_emit_custom_helper(node))
    probe_src = "\n\n\n".join([*helper_src_parts, _emit_counts_body(pipeline)])
    try:
        exec(probe_src, namespace)  # noqa: S102 — author code, guardrailed by lint
        for node in pipeline.nodes:
            if node.type == "custom_python":
                node_fns[node.id] = namespace[f"_node_{node.id}"]
        return namespace["_probe"](frames, node_fns)
    except Exception as exc:  # noqa: BLE001 — surface as a typed, contained error
        raise RowCountError(str(exc)) from exc
