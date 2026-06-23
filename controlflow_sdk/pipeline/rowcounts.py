"""Live row-counts at every joint of a control pipeline (spec §4, §5).

Now delegates to :mod:`controlflow_sdk.pipeline.materialize`.

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

from controlflow_sdk.pipeline.model import Pipeline


class RowCountError(RuntimeError):
    """A pipeline's row-count probe failed to evaluate over the sample frames."""


def compute_row_counts(pipeline: Pipeline, frames: dict[str, Any]) -> dict[str, int]:
    """Return ``{node_id: rows surviving}`` for *pipeline* over *frames*.

    Now a thin ``len()`` over :func:`controlflow_sdk.pipeline.materialize.materialize_steps`
    so the count and the inspectable data are the *same* computation. Returns ``{}`` when a
    source is missing; raises :class:`RowCountError` on an evaluation failure.
    """
    from controlflow_sdk.pipeline.materialize import MaterializeError, materialize_steps
    from controlflow_sdk.rules.spec import RuleSpecError

    try:
        steps = materialize_steps(pipeline, frames)
    except (MaterializeError, RuleSpecError) as exc:
        raise RowCountError(str(exc)) from exc
    return {nid: len(df) for nid, df in steps.items()}
