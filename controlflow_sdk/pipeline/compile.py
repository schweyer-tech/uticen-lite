"""Compile a control pipeline graph into an existing execution artifact.

A :class:`~controlflow_sdk.pipeline.model.Pipeline` compiles to one of the two
artifacts the runner already understands, so run/build/bundle reuse the current
paths unchanged and the bundle contract never learns the word "node":

* **Pure & single-source** (one Import → Filters → Test, flat all/any, no Join /
  Custom Python) → a **rule_spec** dict (the simple case stays no-code in the
  bundle, preserving the authored-without-Python metric).
* **Otherwise** → a generated ``test(pop, sources)`` Python **string** built by
  walking the DAG topologically. Each Import pulls ``sources[code_id].df``;
  Filter/Join/Test emit pandas snippets over per-node frames; the terminal Test
  emits the violations list in the SAME shape as
  :func:`controlflow_sdk.rules.evaluate.evaluate_rule`.

**Custom Python nodes compile to module-level functions** ``def _node_<id>(rows)``
emitted at module top. Because ``sources`` is a *parameter of ``test()``*, a
module-level function structurally cannot see it — that is the real teeth behind
"custom nodes never see a source" (spec §7/§8), not just a lint.

This module is Pyodide-safe: it emits *source text* and never imports pandas
itself (pandas only runs inside the generated code, under the runner).
"""

from __future__ import annotations

from dataclasses import dataclass

from controlflow_sdk.pipeline.model import Node, Pipeline
from controlflow_sdk.rules.render_rule import _mask_expr
from controlflow_sdk.rules.spec import Condition, parse_rule_spec, referenced_columns


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


def compile_pipeline(pipeline: Pipeline) -> CompileResult:
    """Compile *pipeline* to a rule_spec dict or a ``test(pop, sources)`` string."""
    spec = _try_pure_rule_spec(pipeline)
    if spec is not None:
        return CompileResult(test_kind="rule", rule_spec=spec)
    return CompileResult(test_kind="python", test_code=_emit_python(pipeline))


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
    """
    if len(pipeline.import_source_ids()) != 1:
        return None
    # Pure means every node is Import/Filter/Test — no Join/Custom Python.
    if any(n.type not in ("import", "filter", "test") for n in pipeline.nodes):
        return None

    terminal = pipeline.terminal
    test_logic = terminal.config.get("logic", "all")

    # Walk the single linear chain from the terminal back to the Import.
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

    # A Filter narrows conjunctively; flattening it into the Test spec is only
    # sound under all-AND. Under any-OR, bail to the staged Python path.
    if filters and test_logic != "all":
        return None

    conditions: list[dict] = []
    for flt in reversed(filters):  # Import-order narrowing first
        conditions.extend(flt.config.get("conditions", []))
    conditions.extend(terminal.config.get("conditions", []))
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
    return parse_rule_spec({"logic": "all", "conditions": raw}).conditions


def _emit_python(pipeline: Pipeline) -> str:
    """Emit a deterministic, dependency-free ``test(pop, sources)`` module.

    Module-level ``_node_<id>`` functions for Custom Python nodes come first,
    then ``test(pop, sources)`` which walks the DAG topologically: each node
    assigns its output frame, and the terminal Test returns the violations list.
    """
    order = pipeline.topological()

    # 1. Module-level Custom Python functions (starved: only `rows` in scope).
    helpers: list[str] = []
    for node in order:
        if node.type == "custom_python":
            helpers.append(_emit_custom_helper(node))

    # 2. The orchestrating test(pop, sources).
    body: list[str] = ["def test(pop, sources):"]
    body.append("    " + _SAFEDICT.replace("\n", "\n    "))
    terminal = pipeline.terminal
    import_ids = pipeline.import_source_ids()
    primary_source = import_ids[0] if import_ids else None
    for node in order:
        if node.id == terminal.id:
            continue
        body.extend("    " + ln for ln in _emit_node_lines(node, primary_source))
    body.extend("    " + ln for ln in _emit_terminal(terminal, pipeline))

    parts = helpers + ["\n".join(body)]
    return "\n\n\n".join(parts) + "\n"


_SAFEDICT = (
    "class _SafeDict(dict):\n"
    "    def __missing__(self, key):\n"
    '        return "{" + key + "}"\n'
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


def _emit_terminal(node: Node, pipeline: Pipeline) -> list[str]:
    """Emit the terminal node → the violations list.

    Two cases: a Custom Python ``test``-flavor terminal returns its node fn's
    result directly (its module-level helper already returns violations); a
    rule-style Test emits the evaluate_rule-shaped loop over its input frame.
    """
    if node.type == "custom_python" and node.config.get("flavor") == "test":
        lines = _narrative_comment(node)
        lines.append(f"return _node_{node.id}({_frame(node.inputs[0])})")
        return lines

    src = _frame(node.inputs[0])
    conds = _conditions(node.config.get("conditions", []))
    lines = _narrative_comment(node)
    if not conds:
        lines.append("return []")
        return lines
    combine = " & " if node.config.get("logic", "all") == "all" else " | "
    mask = combine.join(_mask_expr(c, frame=src) for c in conds)
    ref_cols = referenced_columns(parse_rule_spec(
        {"logic": "all", "conditions": node.config.get("conditions", [])}))
    template = node.config.get("description_template", "")
    severity = node.config.get("severity", "medium")
    item_key = node.config.get("item_key_column")
    lines.extend([
        f"_df = {src}",
        f"_mask = {mask}",
        f"_key_col = {item_key!r}",
        "if not _key_col:",
        "    _key_col = pop.key_columns[0] if pop.key_columns else None",
        f"_ref_cols = {ref_cols!r}",
        f"_template = {template!r}",
        f"_severity = {severity!r}",
        "_out = []",
        "for _idx, _row in _df[_mask].iterrows():",
        "    _r = _row.to_dict()",
        "    _item_key = str(_r[_key_col]) if _key_col else str(_idx)",
        "    _description = _template.format_map(_SafeDict(_r)) if _template else ''",
        "    _details = {_c: _r[_c] for _c in _ref_cols if _c in _r}",
        "    _out.append({",
        '        "item_key": _item_key,',
        '        "description": _description,',
        '        "severity": _severity,',
        '        "details": _details,',
        "    })",
        "return _out",
    ])
    return lines
