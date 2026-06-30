"""Pure (pandas-free) helpers turning a :class:`Pipeline`'s procedure defs +
Test-node assignments into the effective procedure list, the tests each owns,
and per-node derived membership. No store, no render, no pandas — Pyodide-safe."""

from __future__ import annotations

from typing import Any

from uticen_lite.pipeline.model import Node, Pipeline, ProcedureDef


def _ancestors(pipeline: Pipeline, node_id: str) -> set[str]:
    """All ancestor node ids of *node_id* (inclusive)."""
    keep: set[str] = set()

    def visit(nid: str) -> None:
        if nid in keep:
            return
        keep.add(nid)
        for src in pipeline.node(nid).inputs:
            visit(src)

    visit(node_id)
    return keep


def _assigned_procedure_id(test: Node) -> str | None:
    pid = test.config.get("procedure_id")
    return str(pid) if pid else None


def effective_procedures(pipeline: Pipeline) -> list[ProcedureDef]:
    """The procedures actually used for compile/run/render.

    - When the pipeline defines procedures: those (sorted by ``position``), plus an
      appended auto procedure for every terminal whose ``procedure_id`` is unset or
      dangling (graceful degradation — never drop a test).
    - When none are defined: one auto procedure per terminal (today's behavior),
      coded ``P1..Pn`` in terminal order.  A SOLE auto procedure gets ``code=""``
      for byte-identity with the bundle's single-procedure path (which always
      hardcodes ``code=""``).
    """
    terminals = pipeline.terminals
    defined = sorted(pipeline.procedures, key=lambda p: p.position)
    defined_ids = {p.id for p in defined}

    orphans: list[Node] = []
    for t in terminals:
        pid = _assigned_procedure_id(t)
        if not (pid and pid in defined_ids):
            orphans.append(t)

    out: list[ProcedureDef] = []
    if defined:
        out.extend(defined)
        start = len(defined)
    else:
        start = 0

    # A lone auto procedure (no defined procedures, single terminal) gets code=""
    # so the local workpaper heading matches the bundle's single-procedure shape.
    lone_auto = not defined and len(orphans) == 1
    for i, t in enumerate(orphans):
        out.append(_auto_procedure(t, start + i, lone=lone_auto))
    return out


def _auto_procedure(terminal: Node, position: int, *, lone: bool = False) -> ProcedureDef:
    return ProcedureDef(
        id=terminal.id,
        code="" if lone else f"P{position + 1}",
        name=terminal.config.get("title") or terminal.title or f"Test {terminal.id}",
        assertion="",
        narrative=terminal.narrative,
        failure_threshold_pct=terminal.config.get("failure_threshold_pct"),
        failure_threshold_count=terminal.config.get("failure_threshold_count"),
        position=position,
    )


def tests_for_procedure(pipeline: Pipeline, procedure_id: str) -> list[Node]:
    """Terminals owned by *procedure_id*, in declared order.

    For an auto procedure (id == a terminal id and not a defined procedure), the
    owner is exactly that terminal (so unassigned/legacy terminals each map to self).
    """
    defined_ids = {p.id for p in pipeline.procedures}
    if procedure_id in defined_ids:
        return [t for t in pipeline.terminals if _assigned_procedure_id(t) == procedure_id]
    # Auto procedure: the terminal whose id is the procedure id.
    return [t for t in pipeline.terminals if t.id == procedure_id]


def derived_membership(pipeline: Pipeline) -> dict[str, set[str]]:
    """``{node_id: {procedure_id, …}}`` — a support node belongs to the union of
    procedures of the terminals in its downstream closure. Computed by walking each
    effective procedure's terminals' ancestor closures."""
    out: dict[str, set[str]] = {n.id: set() for n in pipeline.nodes}
    for proc in effective_procedures(pipeline):
        for t in tests_for_procedure(pipeline, proc.id):
            for nid in _ancestors(pipeline, t.id):
                out[nid].add(proc.id)
    return out


def group_nodes_by_band(pipeline: Pipeline) -> dict[str, Any]:
    """Partition node ids into a shared "Inputs" band + one band per effective procedure.

    - ``import`` nodes always sit in the shared band (the data the author brings in).
    - A non-import node belonging to exactly ONE procedure (derived membership ``{P}``)
      sits in ``P``'s band — the nodes private to that procedure's branch.
    - Everything else (membership 0 — orphan/unassigned — or ≥2 — shared upstream
      steps) sits in the shared band.

    Bands preserve topological order within each band, and ``procedures`` is ordered by
    effective-procedure position. The flattened order ``shared + each procedure's nodes``
    is always a valid topological order (a node private to one procedure can never depend
    on a node private to another — that node would be shared). Pure / pandas-free.
    """
    eff = effective_procedures(pipeline)
    by_proc: dict[str, list[str]] = {p.id: [] for p in eff}
    membership = derived_membership(pipeline)
    shared: list[str] = []
    for node in pipeline.topological():
        pids = membership.get(node.id, set())
        if node.type != "import" and len(pids) == 1:
            (only,) = tuple(pids)
            (by_proc[only] if only in by_proc else shared).append(node.id)
        else:
            shared.append(node.id)
    return {
        "shared": shared,
        "procedures": [{"id": p.id, "node_ids": by_proc[p.id]} for p in eff],
    }
