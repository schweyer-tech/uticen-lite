from __future__ import annotations

from controlflow_sdk.rules.spec import Condition, RuleSpec, referenced_columns

_BINARY = {
    "eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<=",
}
_SET = {"in": "in", "not_in": "not in"}
_UNARY = {"is_empty": "is empty", "not_empty": "is not empty",
          "is_duplicate": "is duplicated"}
_CROSS_SOURCE_OPS = frozenset({"exists_in", "not_exists_in"})


def _condition_text(c: Condition) -> str:
    if c.op in _BINARY:
        return f"{c.column} {_BINARY[c.op]} {c.value}"
    if c.op in _SET:
        return f"{c.column} {_SET[c.op]} {c.value}"
    if c.op == "regex":
        return f"{c.column} matches /{c.value}/"
    if c.op in _UNARY:
        return f"{c.column} {_UNARY[c.op]}"
    return f"{c.column} {c.op} {c.value}"  # pragma: no cover


def rule_to_text(spec: RuleSpec) -> str:
    """Render a rule spec to the procedure ``test_code``.

    Single-source specs render the existing human-readable summary. A spec with
    at least one cross-source (``exists_in`` / ``not_exists_in``) condition
    cannot be expressed as that summary, so it renders to runnable plain Python
    using the multi-source ``test(pop, sources)`` API — the bundle's
    ``test_code`` field stays a plain string (schema unchanged), and the
    generated code is behaviorally identical to :func:`evaluate_rule`.
    """
    if any(c.op in _CROSS_SOURCE_OPS for c in spec.conditions):
        return _render_python(spec)
    joiner = "ALL" if spec.logic == "all" else "ANY"
    lines = [f"Flag a record when {joiner} of the following are true:"]
    lines += [f"  - {_condition_text(c)}" for c in spec.conditions]
    lines.append(f"severity: {spec.severity}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cross-source specs → self-contained plain-Python test(pop, sources)
# ---------------------------------------------------------------------------

def _mask_expr(cond: Condition, frame: str = "df") -> str:
    """A Python source expression (over *frame*/``sources``) for one condition.

    Mirrors :func:`controlflow_sdk.rules.evaluate._condition_mask` exactly so the
    generated code is behaviorally identical to the live no-code evaluation. All
    column/source names and values are injected via ``repr()`` (never bare
    interpolation) so names with quotes can't break the emitted source. *frame*
    is the name of the DataFrame variable the expression reads from (``"df"`` for
    the single-source rule renderer; the pipeline compiler passes a per-node
    frame name so the same machinery emits Filter/Test masks over named frames).
    """
    op = cond.op
    if op in _CROSS_SOURCE_OPS:
        other = (f"set(sources[{cond.other_source!r}].df[{cond.other_key!r}]"
                 f".dropna().astype(str))")
        present = f"{frame}[{cond.this_key!r}].astype(str).isin({other})"
        return present if op == "exists_in" else f"(~{present})"
    col = f"{frame}[{cond.column!r}]"
    val = cond.value
    if op == "eq":
        return f"({col} == {val!r})"
    if op == "ne":
        return f"({col} != {val!r})"
    if op == "gt":
        return f"({col} > {val!r})"
    if op == "ge":
        return f"({col} >= {val!r})"
    if op == "lt":
        return f"({col} < {val!r})"
    if op == "le":
        return f"({col} <= {val!r})"
    if op == "is_empty":
        return f"({col}.isna() | ({col}.astype(str) == ''))"
    if op == "not_empty":
        return f"(~({col}.isna() | ({col}.astype(str) == '')))"
    if op == "in":
        return f"{col}.isin({(val or [])!r})"
    if op == "not_in":
        return f"(~{col}.isin({(val or [])!r}))"
    if op == "regex":
        return f"{col}.astype(str).str.match({str(val)!r}).fillna(False)"
    if op == "is_duplicate":
        return f"{col}.duplicated(keep=False)"
    raise ValueError(f"unhandled operator {op!r}")  # pragma: no cover


def _render_python(spec: RuleSpec) -> str:
    """Emit a deterministic, dependency-free ``test(pop, sources)`` body.

    Combines per-condition masks with the spec's ``logic`` (``&`` for ``all``,
    ``|`` for ``any``) — identical to :func:`evaluate_rule` — and reproduces the
    same ``item_key`` / ``description`` / ``severity`` / ``details`` shape.
    """
    combine = " & " if spec.logic == "all" else " | "
    mask_expr = combine.join(_mask_expr(c) for c in spec.conditions)
    ref_cols = referenced_columns(spec)

    return (
        "def test(pop, sources):\n"
        "    df = pop.df\n"
        "\n"
        "    class _SafeDict(dict):\n"
        "        def __missing__(self, key):\n"
        '            return "{" + key + "}"\n'
        "\n"
        f"    mask = {mask_expr}\n"
        f"    key_col = {spec.item_key_column!r}\n"
        "    if not key_col:\n"
        "        key_col = pop.key_columns[0] if pop.key_columns else None\n"
        f"    ref_cols = {ref_cols!r}\n"
        f"    template = {spec.description_template!r}\n"
        f"    severity = {spec.severity!r}\n"
        "    out = []\n"
        "    for idx, row in df[mask].iterrows():\n"
        "        r = row.to_dict()\n"
        "        item_key = str(r[key_col]) if key_col else str(idx)\n"
        "        description = template.format_map(_SafeDict(r)) if template else ''\n"
        "        details = {c: r[c] for c in ref_cols if c in r}\n"
        "        out.append({\n"
        '            "item_key": item_key,\n'
        '            "description": description,\n'
        '            "severity": severity,\n'
        '            "details": details,\n'
        "        })\n"
        "    return out\n"
    )
