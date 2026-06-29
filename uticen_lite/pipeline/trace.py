"""Walk one record through a control pipeline → a render-only trace view-model.

Given the already-materialised per-node frames (``_materialize_full``) plus the
bound source populations, follow a single record (by item key) through every
non-terminal node — present / dropped / indeterminate — then show per-condition
pass/fail at each terminal Test. The same object is a debugging tool (where did
my record drop out?) and the audit-narrative sentence for a flagged record
(issue #29).

Runner-side, like :mod:`uticen_lite.pipeline.materialize`: it imports pandas via
``_condition_mask`` and never imports from ``uticen_lite.plane`` (no web→core
inversion). Per-condition verdicts reuse the canonical ``_condition_mask`` so the
trace agrees with the real run byte-for-byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from uticen_lite.model.population import Population
from uticen_lite.pipeline.model import Node, Pipeline
from uticen_lite.rules.evaluate import _condition_mask
from uticen_lite.rules.spec import RuleSpec, RuleSpecError, parse_rule_spec


@dataclass
class ConditionRow:
    column: str
    op: str
    value: Any
    actual: Any
    passed: bool | None  # None → couldn't evaluate (bad column/op/source)
    note: str = ""


@dataclass
class NodeStep:
    id: str
    label: str
    type: str
    status: str  # "present" | "dropped" | "absent" | "indeterminate"
    reason: str = ""


@dataclass
class TestStep:
    id: str
    label: str
    reached: bool | None  # did the record arrive at this Test's input?
    flagged: bool | None  # is the record in this Test's violations?
    logic: str
    conditions: list[ConditionRow] = field(default_factory=list)
    note: str = ""


@dataclass
class TraceResult:
    key: str
    key_column: str | None
    source_id: str | None
    found: bool
    shared_count: int
    steps: list[NodeStep] = field(default_factory=list)
    tests: list[TestStep] = field(default_factory=list)
    message: str = ""  # set when the trace can't run (degraded / not found)


_DROP_REASON = {
    "import": "Not present in the imported source.",
    "filter": "A Filter condition excluded this record.",
    "join": "The Join found no match for this record.",
    "custom_python": "A custom Python step removed this record.",
}


def _node_label(node: Node) -> str:
    """A short human label for a node (mirrors the builder's labels).

    Duplicated here deliberately: this module is runner-side and must not import
    from ``uticen_lite.plane.routes.pipeline`` (web→core inversion). The node
    vocabulary is stable.
    """
    if node.title:
        return node.title
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
        return "Test"
    return node.type


def _display(value: Any) -> Any:
    """NaN/NaT-safe display value."""
    import pandas as pd
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return value


def _present(frame: Any, key_column: str | None, key: str) -> bool | None:
    """True/False if *key* is/ isn't in *frame*; None if it can't be determined."""
    if frame is None or key_column is None or key_column not in getattr(frame, "columns", []):
        return None
    return bool((frame[key_column].astype(str) == str(key)).any())


def _xsrc_note(cond: Any, passed: bool) -> str:
    if cond.op == "exists_in":
        return f"{'found' if passed else 'not found'} in {cond.other_source}"
    if cond.op == "not_exists_in":
        return f"{'not found' if passed else 'found'} in {cond.other_source}"
    return ""


def _condition_rows(
    input_frame: Any, key_column: str, key: str,
    spec: RuleSpec, sources: dict[str, Population],
) -> list[ConditionRow]:
    """Per-condition pass/fail for the matched row in *input_frame*.

    Masks are evaluated over the WHOLE input frame (``is_duplicate``/``exists_in``
    are population-relative), then read positionally at the first matching row —
    dup-index safe.
    """
    flags = (input_frame[key_column].astype(str) == str(key)).to_numpy()
    rows: list[ConditionRow] = []
    if not flags.any():
        return rows
    pos = int(flags.argmax())
    for cond in spec.conditions:
        actual = (
            _display(input_frame.iloc[pos][cond.column])
            if cond.column in input_frame.columns else ""
        )
        try:
            mask = _condition_mask(input_frame, cond, sources)
            passed = bool(mask.iloc[pos])
            note = _xsrc_note(cond, passed)
        except Exception as exc:  # noqa: BLE001 — bad column/op/source must not crash the trace
            passed, note = None, f"couldn't evaluate: {exc}"  # type: ignore[assignment]
        rows.append(ConditionRow(cond.column, cond.op, cond.value, actual, passed, note))
    return rows


def trace_record(
    pipeline: Pipeline,
    frames: dict[str, Any],
    key: str,
    sources: dict[str, Population],
) -> TraceResult:
    """Build the trace view-model for *key* over the materialised *frames*."""
    import_nodes = [n for n in pipeline.nodes if n.type == "import"]
    first_import = import_nodes[0] if import_nodes else None
    source_id = first_import.source_id if first_import else None
    primary = sources.get(source_id) if source_id else None
    key_column = primary.key_columns[0] if primary and primary.key_columns else None

    result = TraceResult(
        key=key, key_column=key_column, source_id=source_id,
        found=False, shared_count=0,
    )

    if key_column is None:
        result.message = (
            "This control's source has no key column, so a record can't be "
            "traced by key."
        )
        return result

    import_frame = frames.get(first_import.id) if first_import else None
    if import_frame is None:
        result.message = "Bind a data source to trace a record."
        return result

    flags = (import_frame[key_column].astype(str) == str(key)).to_numpy()
    result.shared_count = int(flags.sum())
    result.found = bool(flags.any())
    if not result.found:
        result.message = f"No record with {key_column} = {key!r} in this source."
        return result

    terminal_ids = {t.id for t in pipeline.terminals}

    # ── non-terminal walk: present / dropped / indeterminate ──────────────
    dropped = False
    for node in pipeline.topological():
        if node.id in terminal_ids:
            continue
        frame = frames.get(node.id)
        present = _present(frame, key_column, key)
        if present is None:
            status = "indeterminate"
            reason = (
                "Not computed yet." if frame is None
                else "The key column isn't carried past this step."
            )
        elif present:
            status, reason = "present", ""
        elif not dropped:
            status, reason = "dropped", _DROP_REASON.get(node.type, "Excluded here.")
            dropped = True
        else:
            status, reason = "absent", "Removed at an earlier step."
        result.steps.append(NodeStep(node.id, _node_label(node), node.type, status, reason))

    # ── per-terminal Test detail ──────────────────────────────────────────
    for t in pipeline.terminals:
        input_frame = frames.get(t.inputs[0]) if t.inputs else None
        reached = _present(input_frame, key_column, key)
        flagged = _present(frames.get(t.id), key_column, key)
        logic = str(t.config.get("logic", "all"))
        step = TestStep(
            id=t.id, label=_node_label(t), reached=reached, flagged=flagged, logic=logic,
        )
        if t.type != "test":
            step.note = "This Test is authored in custom Python — no per-condition detail."
            result.tests.append(step)
            continue
        if reached is None:
            step.note = (
                "Couldn't locate this record at the Test input "
                "(the key column isn't carried here)."
            )
            result.tests.append(step)
            continue
        if not reached:
            step.note = "This record didn't reach the Test."
            result.tests.append(step)
            continue
        try:
            spec = parse_rule_spec(t.config)
        except RuleSpecError as exc:
            step.note = f"Couldn't read this Test's conditions: {exc}"
            result.tests.append(step)
            continue
        step.conditions = _condition_rows(input_frame, key_column, key, spec, sources)
        if step.flagged is None and step.conditions:
            passes = [c.passed for c in step.conditions if c.passed is not None]
            if passes:
                step.flagged = all(passes) if logic == "all" else any(passes)
        result.tests.append(step)

    return result
