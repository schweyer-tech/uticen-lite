"""Compile a control pipeline graph into an existing execution artifact.

A :class:`~uticen_lite.pipeline.model.Pipeline` compiles to one of the two
artifacts the runner already understands, so run/build/bundle reuse the current
paths unchanged and the bundle contract never learns the word "node":

* **Pure & single-source** (one Import → Filters → Test, flat all/any, no Join /
  Custom Python) → a **rule_spec** dict (the simple case stays no-code in the
  bundle, preserving the authored-without-Python metric).
* **Otherwise** → a generated ``test(pop, sources)`` Python **string** built by
  walking the DAG topologically. Each Import pulls ``sources[code_id].df``;
  Filter/Join/Test emit pandas snippets over per-node frames; the terminal Test
  emits the violations list in the SAME shape as
  :func:`uticen_lite.rules.evaluate.evaluate_rule`.

**Custom Python nodes compile to module-level functions** ``def _node_<id>(rows)``
emitted at module top. Because ``sources`` is a *parameter of ``test()``*, a
module-level function structurally cannot see it — that is the real teeth behind
"custom nodes never see a source" (spec §7/§8), not just a lint.

This module is Pyodide-safe: it emits *source text* and never imports pandas
itself (pandas only runs inside the generated code, under the runner).
"""

from __future__ import annotations

from dataclasses import dataclass

from uticen_lite.pipeline.model import Node, Pipeline
from uticen_lite.rules.render_rule import _mask_expr
from uticen_lite.rules.spec import Condition, parse_rule_spec


@dataclass(frozen=True)
class CompileResult:
    """The artifact a pipeline compiles to.

    Exactly one of ``rule_spec`` / ``test_code`` is set, indicated by
    ``test_kind`` (``"rule"`` or ``"python"``). These land in the control's
    ``rule_spec`` / ``test_code`` columns; the graph stays in the ``pipeline``
    column for re-editing.
    """

    test_kind: str
    rule_spec: dict | None = None
    test_code: str | None = None


@dataclass(frozen=True)
class CompiledProcedure:
    """One compiled artifact for an effective procedure in a pipeline.

    For a multi-check procedure the result is compiled from the union sub-pipeline
    of all its Test nodes. For a single-check procedure it may be a rule_spec or
    a python string (the same as the pre-procedure per-terminal path).
    """

    procedure_id: str
    title: str
    narrative: str
    result: CompileResult
    code: str = ""
    assertion: str = ""


def compile_pipeline(pipeline: Pipeline) -> CompileResult:
    """Compile *pipeline* to a rule_spec dict or a ``test(pop, sources)`` string.

    For a single-terminal pipeline the output is byte-identical to the pre-multi
    behaviour (rule_spec or a simple test() string). For ≥2 terminals the result
    is always ``test_kind="python"`` with a union ``test()`` that computes the
    shared trunk once then concatenates each terminal's violations.
    """
    spec = _try_pure_rule_spec(pipeline)
    if spec is not None:
        return CompileResult(test_kind="rule", rule_spec=spec)
    return CompileResult(test_kind="python", test_code=_emit_python(pipeline))


def _subpipeline_for_terminals(pipeline: Pipeline, terminals: list[Node]) -> Pipeline:
    """Sub-pipeline = the union of the ancestor closures of *terminals*.

    Declared node order is preserved for determinism. Procedure defs are dropped
    from the slice (the slice compiles to violations only)."""
    keep: set[str] = set()

    def visit(nid: str) -> None:
        if nid in keep:
            return
        keep.add(nid)
        for src in pipeline.node(nid).inputs:
            visit(src)

    for t in terminals:
        visit(t.id)
    return Pipeline(nodes=[n for n in pipeline.nodes if n.id in keep])


def _subpipeline_for(pipeline: Pipeline, terminal: Node) -> Pipeline:
    """Back-compat single-terminal slice (now a thin wrapper)."""
    return _subpipeline_for_terminals(pipeline, [terminal])


def compile_pipeline_procedures(pipeline: Pipeline) -> list[CompiledProcedure]:
    """Compile each **effective procedure** to a :class:`CompiledProcedure`.

    A procedure may own several Test nodes; its ``result`` is compiled from the
    union sub-pipeline of those tests (the existing multi-terminal ``_out_<id>``
    union emit produces one ``test()`` returning the concatenation of the checks'
    violations). Falls back to one-procedure-per-terminal when none are defined.
    """
    from uticen_lite.pipeline.procedures import (
        effective_procedures,
        tests_for_procedure,
    )

    out: list[CompiledProcedure] = []
    for proc in effective_procedures(pipeline):
        tests = tests_for_procedure(pipeline, proc.id)
        if not tests:
            continue
        sub = _subpipeline_for_terminals(pipeline, tests)
        title = proc.name or (tests[0].config.get("title") or f"Test {tests[0].id}")
        out.append(
            CompiledProcedure(
                procedure_id=proc.id,
                title=str(title),
                narrative=proc.narrative or tests[0].narrative,
                result=compile_pipeline(sub),
                code=proc.code,
                assertion=proc.assertion,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Pure single-source → rule_spec
# ---------------------------------------------------------------------------


def _try_pure_rule_spec(pipeline: Pipeline) -> dict | None:
    """Return a flattened rule_spec dict iff the pipeline is pure & single-source.

    Pure means: exactly one Import, the chain Import → Filter* → Test is linear,
    every node is Import/Filter/Test (no Join/Custom Python). Flattening Filter
    conditions into the Test spec is only sound under **all-AND** logic: a Filter
    is a conjunctive *narrowing* ("keep the rows that matter, then assert the
    rule" — spec §5), never an alternative. Under ``all`` the staged
    ``filter → test`` is exactly ``(filter conds) AND (test conds)``, so the flat
    spec is equivalent. Under ``any`` it is NOT — flattening would OR the filter
    conditions with the test conditions, evaluating both over the *unfiltered*
    population, which flags rows the staged pipeline never sees. So whenever a
    Filter is present we restrict the pure path to ``test_logic == "all"`` and
    otherwise bail to the Python path (the staged-semantics target). A
    filter-free Import → Test can still flatten under any logic (there is nothing
    to narrow first).

    Multi-terminal pipelines always bail to the Python path — the union semantics
    cannot be expressed as a single rule_spec.
    """
    if not _is_pure_single_source(pipeline):
        return None

    terminal = pipeline.terminal
    test_logic = terminal.config.get("logic", "all")

    filters = _walk_linear_filter_chain(pipeline, terminal, test_logic)
    if filters is None:
        return None

    # A Filter narrows conjunctively; flattening it into the Test spec is only
    # sound under all-AND. Under any-OR, bail to the staged Python path.
    if filters and test_logic != "all":
        return None

    return _build_flat_spec(terminal, filters, test_logic)


def _is_pure_single_source(pipeline: Pipeline) -> bool:
    """Pure & single-source: one terminal, one Import, only Import/Filter/Test nodes."""
    if len(pipeline.terminals) != 1:
        return False
    if len(pipeline.import_source_ids()) != 1:
        return False
    # Pure means every node is Import/Filter/Test — no Join/Custom Python.
    if any(n.type not in ("import", "filter", "test") for n in pipeline.nodes):
        return False
    return True


def _walk_linear_filter_chain(
    pipeline: Pipeline, terminal: Node, test_logic: str
) -> list[Node] | None:
    """Walk the single linear chain terminal → Import, collecting Filter nodes.

    Returns the Filter nodes (terminal-order) on success, or ``None`` if the shape
    is not a pure linear chain (fan-in, a cycle, or a Filter whose all/any logic
    differs from the terminal's so it can't flatten safely).
    """
    filters: list[Node] = []
    cursor = terminal
    seen: set[str] = set()
    while cursor.inputs:
        if len(cursor.inputs) != 1:
            return None  # fan-in → not the pure linear shape
        parent = pipeline.node(cursor.inputs[0])
        if parent.id in seen:  # pragma: no cover (cycles rejected at parse)
            return None
        seen.add(parent.id)
        if parent.type == "filter":
            if parent.config.get("logic", "all") != test_logic:
                return None  # mixed all/any can't flatten safely
            filters.append(parent)
        elif parent.type == "import":
            break
        else:  # pragma: no cover (only filter/import remain by here)
            return None
        cursor = parent
    return filters


def _build_flat_spec(terminal: Node, filters: list[Node], test_logic: str) -> dict | None:
    """Flatten the Filter + Test conditions into a single rule_spec dict.

    Filter conditions come first (Import-order narrowing) then the Test's own
    conditions. Returns ``None`` when there are no conditions at all.
    """
    conditions: list[dict] = []
    for flt in reversed(filters):  # Import-order narrowing first
        conditions.extend(_usable_conditions(list(flt.config.get("conditions", []))))
    conditions.extend(_usable_conditions(list(terminal.config.get("conditions", []))))
    if not conditions:
        return None

    return {
        "logic": test_logic,
        "conditions": conditions,
        "severity": terminal.config.get("severity", "medium"),
        "description_template": terminal.config.get("description_template", ""),
        "item_key_column": terminal.config.get("item_key_column"),
    }


# ---------------------------------------------------------------------------
# General DAG → test(pop, sources) string
# ---------------------------------------------------------------------------


def _frame(node_id: str) -> str:
    """The DataFrame variable name for a node's output stream."""
    return f"_f_{node_id}"


def _conditions(raw: list[dict]) -> list[Condition]:
    """Parse a node's raw condition dicts via the shared rule parser.

    Reuses :func:`parse_rule_spec` so Filter/Test conditions validate against the
    SAME operator grammar (incl. cross-source ``exists_in``) as the no-code rule
    builder — one source of truth for the operator set.
    """
    usable = _usable_conditions(raw)
    if not usable:
        return []
    return parse_rule_spec({"logic": "all", "conditions": usable}).conditions


def _usable_conditions(raw: list[dict]) -> list[dict]:
    """Drop placeholder condition rows that are still incomplete in the UI.

    The visual builder keeps a blank row around after "+ Add condition" so the
    author can fill it in, but that placeholder should behave like no condition
    at all until it has the required fields for its operator family.
    """
    out: list[dict] = []
    for cond in raw:
        op = cond.get("op")
        if op in ("exists_in", "not_exists_in"):
            if cond.get("other_source") and cond.get("this_key") and cond.get("other_key"):
                out.append(cond)
            continue
        if cond.get("column"):
            out.append(cond)
    return out


def _emit_python(pipeline: Pipeline) -> str:
    """Emit a deterministic, dependency-free ``test(pop, sources)`` module.

    Module-level ``_node_<id>`` functions for Custom Python nodes come first,
    then ``test(pop, sources)`` which walks the DAG topologically: each node
    assigns its output frame, and the terminal Test returns the violations list.

    For a single-terminal pipeline the output is byte-identical to the pre-multi
    behaviour. For ≥2 terminals, all non-terminal frames are emitted once (the
    shared trunk), then each terminal emits its violations into a uniquely-named
    ``_out_<id>`` variable, and the function returns their concatenation.
    """
    order = pipeline.topological()
    terminals = pipeline.terminals
    terminal_ids = {t.id for t in terminals}

    # 1. Module-level Custom Python functions (starved: only `rows` in scope).
    helpers: list[str] = []
    for node in order:
        if node.type == "custom_python":
            helpers.append(_emit_custom_helper(node))

    # 2. The orchestrating test(pop, sources).
    body: list[str] = ["def test(pop, sources):"]
    body.append("    " + _SAFEDICT.replace("\n", "\n    "))
    import_ids = pipeline.import_source_ids()
    primary_source = import_ids[0] if import_ids else None
    for node in order:
        if node.id in terminal_ids:
            continue
        body.extend("    " + ln for ln in _emit_node_lines(node, primary_source))
    if len(terminals) == 1:
        body.extend("    " + ln for ln in _emit_terminal(terminals[0], pipeline))
    else:
        for t in terminals:
            body.extend("    " + ln for ln in _emit_terminal(t, pipeline, out_var=f"_out_{t.id}"))
        body.append("    return " + " + ".join(f"_out_{t.id}" for t in terminals))

    parts = helpers + ["\n".join(body)]
    return "\n\n\n".join(parts) + "\n"


_SAFEDICT = (
    'class _SafeDict(dict):\n    def __missing__(self, key):\n        return "{" + key + "}"\n'
)


def _narrative_comment(node: Node) -> list[str]:
    if not node.narrative:
        return []
    return [f"# {line}" for line in node.narrative.splitlines()]


def _emit_node_lines(node: Node, primary_source: str | None) -> list[str]:
    """Lines that assign ``_f_<id>`` for a non-terminal node.

    The Import bound to the *primary* source reads ``pop.df`` (the runner's
    primary population); every other Import reads ``sources[code_id].df``. This
    mirrors the runner contract where ``pop`` is ``populations[0]`` and only the
    non-primary bound sources need be looked up in ``sources``.
    """
    lines = _narrative_comment(node)
    if node.type == "import":
        code_id = node.source_id
        if code_id == primary_source:
            lines.append(f"{_frame(node.id)} = pop.df")
        else:
            lines.append(f"{_frame(node.id)} = sources[{code_id!r}].df")
    elif node.type == "filter":
        src = _frame(node.inputs[0])
        lines.extend(_emit_filter(node, src))
    elif node.type == "join":
        lines.extend(_emit_join(node))
    elif node.type == "custom_python":
        src = _frame(node.inputs[0])
        lines.append(f"{_frame(node.id)} = _node_{node.id}({src})")
    return lines


def _emit_filter(node: Node, src: str) -> list[str]:
    conds = _conditions(node.config.get("conditions", []))
    out = _frame(node.id)
    if not conds:
        return [f"{out} = {src}"]
    combine = " & " if node.config.get("logic", "all") == "all" else " | "
    mask = combine.join(_mask_expr(c, frame=src) for c in conds)
    return [f"{out} = {src}[{mask}]"]


def _emit_join(node: Node) -> list[str]:
    """Emit a Join over its two input frames.

    Modes:
      * ``exists`` / ``not_exists`` — filter the left frame by membership of
        ``left_key`` in the right frame's ``right_key`` set (the #9 cross-source
        primitive, generalised to two visible streams).
      * ``inner`` / ``left`` — a pandas merge on the keys, optionally bringing
        only ``bring_columns`` from the right (plus its join key).
    """
    left = _frame(node.inputs[0])
    right = _frame(node.inputs[1])
    out = _frame(node.id)
    cfg = node.config
    lk = cfg["left_key"]
    rk = cfg["right_key"]
    mode = cfg["mode"]

    if mode in ("exists", "not_exists"):
        keyset = f"set({right}[{rk!r}].dropna().astype(str))"
        present = f"{left}[{lk!r}].astype(str).isin({keyset})"
        mask = present if mode == "exists" else f"(~{present})"
        return [f"{out} = {left}[{mask}]"]

    # inner / left merge.
    bring = cfg.get("bring_columns")
    if bring:
        cols = list(dict.fromkeys([rk, *bring]))  # ensure join key present, dedup
        right_expr = f"{right}[{cols!r}]"
    else:
        right_expr = right
    how = "inner" if mode == "inner" else "left"
    return [
        f"{out} = {left}.merge(",
        f"    {right_expr}.drop_duplicates(subset={rk!r}, keep='first'),",
        f"    left_on={lk!r}, right_on={rk!r}, how={how!r}, suffixes=('', '_joined'),",
        ")",
    ]


def _emit_custom_helper(node: Node) -> str:
    """A module-level ``def _node_<id>(rows):`` for a Custom Python node.

    The author body is indented under the function. Because the function is
    defined at module scope, ``sources`` (a parameter of ``test()``) is NOT in
    its enclosing scope — structural starvation (spec §7/§8).

    A ``transform`` node returns the (possibly rebound) ``rows`` frame; a
    ``test`` node's body is expected to ``return`` the violations list itself, so
    no trailing return is appended.
    """
    header = []
    header.extend(_narrative_comment(node))
    header.append(f"def _node_{node.id}(rows):")
    code = node.config.get("code", "")
    code_lines = code.splitlines() or ["pass"]
    indented = ["    " + ln for ln in code_lines]
    if node.config.get("flavor") == "transform":
        indented.append("    return rows")
    return "\n".join(header + indented)


def _emit_terminal(node: Node, pipeline: Pipeline, out_var: str = "_out") -> list[str]:
    """Emit the terminal node → the violations list.

    Two cases: a Custom Python ``test``-flavor terminal returns its node fn's
    result directly (its module-level helper already returns violations); a
    rule-style Test emits the evaluate_rule-shaped loop over its input frame.
    This is a thin router that dispatches to the matching emitter.

    *out_var* names the list variable to populate. In the single-terminal path
    (``out_var="_out"``) the function emits ``return _out`` at the end. In the
    multi-terminal path the caller passes a unique name like ``_out_a`` and emits
    the combined return itself, so no trailing ``return`` is appended here.
    """
    if node.type == "custom_python" and node.config.get("flavor") == "test":
        return _emit_terminal_python(node, out_var)
    return _emit_terminal_rule(node, out_var)


def _emit_terminal_python(node: Node, out_var: str) -> list[str]:
    """Emit a Custom Python ``test``-flavor terminal → the violations list.

    The node's module-level helper already returns violations, so the terminal
    just binds (or returns) the call result.
    """
    lines = _narrative_comment(node)
    if out_var == "_out":
        lines.append(f"return _node_{node.id}({_frame(node.inputs[0])})")
    else:
        lines.append(f"{out_var} = _node_{node.id}({_frame(node.inputs[0])})")
    return lines


def _emit_terminal_rule(node: Node, out_var: str) -> list[str]:
    """Emit a rule-style Test terminal → the evaluate_rule-shaped violations loop."""
    src = _frame(node.inputs[0])
    conds = _conditions(node.config.get("conditions", []))
    lines = _narrative_comment(node)
    if not conds:
        if out_var == "_out":
            lines.append("return []")
        else:
            lines.append(f"{out_var} = []")
        return lines
    combine = " & " if node.config.get("logic", "all") == "all" else " | "
    mask = combine.join(_mask_expr(c, frame=src) for c in conds)
    # Use the already-filtered `conds` list (via `_usable_conditions`) so that
    # blank placeholder rows added by "+ Add condition" before the author fills
    # them in never reach `parse_rule_spec`, which rejects empty columns.
    ref_cols = list(dict.fromkeys(c.column for c in conds))
    template = node.config.get("description_template", "")
    severity = node.config.get("severity", "medium")
    item_key = node.config.get("item_key_column")
    lines.extend(
        [
            f"_df = {src}",
            f"_mask = {mask}",
            f"_key_col = {item_key!r}",
            "if not _key_col:",
            "    _key_col = pop.key_columns[0] if pop.key_columns else None",
            f"_ref_cols = {ref_cols!r}",
            f"_template = {template!r}",
            f"_severity = {severity!r}",
            f"{out_var} = []",
            "for _idx, _row in _df[_mask].iterrows():",
            "    _r = _row.to_dict()",
            "    _item_key = str(_r[_key_col]) if _key_col else str(_idx)",
            "    _description = _template.format_map(_SafeDict(_r)) if _template else ''",
            "    _details = {_c: _r[_c] for _c in _ref_cols if _c in _r}",
            f"    {out_var}.append({{",
            '        "item_key": _item_key,',
            '        "description": _description,',
            '        "severity": _severity,',
            '        "details": _details,',
            "    })",
        ]
    )
    if out_var == "_out":
        lines.append("return _out")
    return lines
