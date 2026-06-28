"""Visual control-pipeline graph model (issue #25, Stage 1).

A control's pipeline is a small DAG (mostly linear; the only fan-in is a Join).
Each node is ``{id, type, narrative, config, inputs: [node_id, ...]}``. Import
nodes have a ``source_id`` and no inputs; there are one or more terminal Tests.

This module is **Pyodide-safe**: it is pure ``dataclasses`` + stdlib. It never
imports pandas — the pandas work lives in the generated code that runs under the
runner (see :mod:`controlflow_sdk.pipeline.compile`). Parse/validate mirrors the
posture of :func:`controlflow_sdk.rules.spec.parse_rule_spec`: build typed
objects from a plain dict and reject malformed graphs eagerly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Phase-1 node vocabulary (from the §10 Northwind grammar-coverage audit).
# Deferred to Phase 2 (NOT built here): general ``aggregate`` (group_by) and a
# derived-column ``transform``.
NODE_TYPES = frozenset({"import", "filter", "join", "test", "custom_python"})

JOIN_MODES = frozenset({"exists", "not_exists", "inner", "left"})
CUSTOM_FLAVORS = frozenset({"transform", "test"})


class PipelineError(ValueError):
    """A pipeline graph is malformed."""


def _is_terminal(node: Node) -> bool:
    """True if *node* can be the violations-producing sink of a pipeline.

    A terminal is a ``test`` node (conditions → violations) or a
    ``custom_python`` node with ``flavor == "test"`` (``rows → violations``).
    """
    return node.type == "test" or (
        node.type == "custom_python" and node.config.get("flavor") == "test"
    )


@dataclass(frozen=True)
class Node:
    """One typed node in a control pipeline.

    ``config`` carries type-specific settings (e.g. a Filter's ``conditions`` /
    ``logic``, a Join's keys/mode, a Custom Python node's ``code``/``flavor``).
    ``source_id`` is only set on Import nodes (which have no ``inputs``).
    """

    id: str
    type: str
    narrative: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    inputs: list[str] = field(default_factory=list)
    source_id: str | None = None


@dataclass(frozen=True)
class Pipeline:
    """An ordered DAG of :class:`Node` with one or more terminal Test nodes."""

    nodes: list[Node]

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)  # pragma: no cover (validated upstream)

    @property
    def terminals(self) -> list[Node]:
        """All terminal nodes — sinks that feed nothing and are terminal-capable.

        Each is either a ``test`` node or a ``custom_python`` test-flavor node
        (``rows → violations``). Order matches the declared node order.
        Validated non-empty at parse time.
        """
        consumed = {src for n in self.nodes for src in n.inputs}
        return [n for n in self.nodes if n.id not in consumed and _is_terminal(n)]

    @property
    def terminal(self) -> Node:
        """The first terminal node (back-compat alias for ``terminals[0]``)."""
        return self.terminals[0]

    def import_source_ids(self) -> list[str]:
        """Source ids bound by Import nodes, in node order, de-duplicated.

        This is what ``set_control_sources`` derives a control's source binding
        from (the analyst binds sources *by adding Import nodes*).
        """
        out: list[str] = []
        for n in self.nodes:
            if n.type == "import" and n.source_id and n.source_id not in out:
                out.append(n.source_id)
        return out

    def topological(self) -> list[Node]:
        """Return nodes in a dependency-respecting (Kahn) order.

        Inputs always precede the nodes that consume them. The graph is acyclic
        by construction (cycles are rejected at parse time).
        """
        indegree = {n.id: len(n.inputs) for n in self.nodes}
        by_id = {n.id: n for n in self.nodes}
        consumers: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for n in self.nodes:
            for src in n.inputs:
                consumers[src].append(n.id)
        # Seed with zero-indegree nodes in declared order for determinism.
        ready = [n.id for n in self.nodes if indegree[n.id] == 0]
        order: list[Node] = []
        while ready:
            nid = ready.pop(0)
            order.append(by_id[nid])
            for c in consumers[nid]:
                indegree[c] -= 1
                if indegree[c] == 0:
                    ready.append(c)
        if len(order) != len(self.nodes):  # pragma: no cover (cycle rejected upstream)
            raise PipelineError("pipeline contains a cycle")
        return order

    def validate_sources(self, known: set[str]) -> None:
        """Raise if any Import node binds a source id not in *known*."""
        for sid in self.import_source_ids():
            if sid not in known:
                raise PipelineError(f"unknown source {sid!r} referenced by an Import node")


def parse_pipeline(raw: dict) -> Pipeline:
    """Build and validate a :class:`Pipeline` from a plain dict.

    Rejects: unknown node types, duplicate ids, Import nodes without a
    ``source_id`` (or with inputs), dangling inputs, a Join without two inputs,
    Custom Python nodes with an unknown ``flavor``, a missing/duplicate terminal
    Test, and cycles. Source ids are validated lazily via
    :meth:`Pipeline.validate_sources` (the known set lives in the store).
    """
    raw_nodes = raw.get("nodes", [])
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise PipelineError("a pipeline needs a non-empty 'nodes' list")

    nodes: list[Node] = []
    seen_ids: set[str] = set()
    for rn in raw_nodes:
        node = _parse_node(rn)
        if node.id in seen_ids:
            raise PipelineError(f"duplicate node id {node.id!r}")
        seen_ids.add(node.id)
        nodes.append(node)

    _validate_inputs(nodes, seen_ids)
    _reject_cycles(nodes)  # before terminal analysis: a cycle confounds sink detection
    _validate_terminal(nodes)
    return Pipeline(nodes=nodes)


def _parse_node(rn: dict) -> Node:
    node_id = rn.get("id")
    if not node_id or not isinstance(node_id, str):
        raise PipelineError("each node needs a non-empty string id")
    node_type = rn.get("type")
    if node_type not in NODE_TYPES:
        raise PipelineError(f"unknown node type {node_type!r} on node {node_id!r}")

    inputs = rn.get("inputs", []) or []
    if not isinstance(inputs, list):
        raise PipelineError(f"node {node_id!r} inputs must be a list")
    config = rn.get("config", {}) or {}
    if not isinstance(config, dict):
        raise PipelineError(f"node {node_id!r} config must be an object")
    narrative = str(rn.get("narrative", "") or "")

    if node_type == "import":
        source_id = rn.get("source_id")
        if not source_id:
            raise PipelineError(f"import node {node_id!r} requires a source_id")
        if inputs:
            raise PipelineError(f"import node {node_id!r} must have no inputs")
        return Node(id=node_id, type="import", narrative=narrative,
                    config=config, inputs=[], source_id=str(source_id))

    if node_type == "join":
        if len(inputs) != 2:
            raise PipelineError(f"Join node {node_id!r} requires exactly two inputs")
        mode = config.get("mode")
        if mode not in JOIN_MODES:
            raise PipelineError(f"Join node {node_id!r} has unknown mode {mode!r}")
        for key in ("left_key", "right_key"):
            if not config.get(key):
                raise PipelineError(f"Join node {node_id!r} requires {key}")
    elif node_type == "custom_python":
        flavor = config.get("flavor")
        if flavor not in CUSTOM_FLAVORS:
            raise PipelineError(
                f"custom_python node {node_id!r} has unknown flavor {flavor!r}"
            )
        if not config.get("code"):
            raise PipelineError(f"custom_python node {node_id!r} requires code")

    if node_type != "import" and not inputs:
        raise PipelineError(f"node {node_id!r} of type {node_type!r} requires inputs")

    return Node(id=node_id, type=node_type, narrative=narrative,
                config=config, inputs=list(inputs))


def _validate_inputs(nodes: list[Node], known_ids: set[str]) -> None:
    for n in nodes:
        for src in n.inputs:
            if src not in known_ids:
                raise PipelineError(f"node {n.id!r} has unknown input {src!r}")
            if src == n.id:
                raise PipelineError(f"node {n.id!r} cannot take itself as input")


def _validate_terminal(nodes: list[Node]) -> None:
    """Every sink must be a terminal-capable node; at least one must exist.

    A sink is a node that feeds nothing. Every sink must be a ``test`` node or a
    ``custom_python`` test-flavor node; non-terminal dangling nodes are rejected.
    """
    consumed = {src for n in nodes for src in n.inputs}
    sinks = [n for n in nodes if n.id not in consumed]
    non_terminal = [s for s in sinks if not _is_terminal(s)]
    if non_terminal:
        # Prefix with ``node '<id>': `` so the editor pins this on the offending
        # card (red), not just a top banner (_node_errors_from; 2026-06-27 review).
        raise PipelineError(
            f"node {non_terminal[0].id!r}: must end in a Test (or a custom_python "
            "test-flavor) node — it feeds nothing and is not a Test"
        )
    if not any(_is_terminal(s) for s in sinks):
        raise PipelineError("a pipeline needs at least one terminal Test node")


def _reject_cycles(nodes: list[Node]) -> None:
    by_id = {n.id: n for n in nodes}
    state: dict[str, int] = {}  # 0=unvisited, 1=on-stack, 2=done

    def visit(nid: str) -> None:
        s = state.get(nid, 0)
        if s == 1:
            raise PipelineError("pipeline contains a cycle")
        if s == 2:
            return
        state[nid] = 1
        for src in by_id[nid].inputs:
            visit(src)
        state[nid] = 2

    for n in nodes:
        visit(n.id)
