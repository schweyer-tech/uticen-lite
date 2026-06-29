"""Provider-agnostic draft orchestrator + the validation gate.

A backend proposes a raw ``rule_spec`` dict; this module re-validates it the same
way for **all three** providers before it can reach the rule builder:

1. ``parse_rule_spec`` — shape/operator validation (raises ``RuleSpecError``).
2. column check — every referenced column must exist in the source schema.
3. run-on-sample — ``evaluate_rule`` on a tiny in-memory population proves the
   spec actually executes (a bad regex / unusable column raises → ``DraftError``).

So a bad draft can never be saved blind, and offline-by-default is preserved
because the route guards a provider+key *before* calling :func:`draft_and_validate`.
"""

from __future__ import annotations

from typing import Any

from uticen_lite.ai.providers import get_provider
from uticen_lite.rules.spec import OPERATORS, RuleSpec, parse_rule_spec, referenced_columns

# Output schema for structured outputs. The ``op`` enum is derived from
# rules.spec.OPERATORS so it can never silently desync from the engine
# (guarded by tests/ai/test_draft.py::test_op_enum_equals_operators_no_drift).
# Only constructs supported by structured outputs are used — enum + basic types
# + additionalProperties:false, no numeric/length constraints.
RULE_SPEC_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["logic", "conditions"],
    "properties": {
        "logic": {"type": "string", "enum": ["all", "any"]},
        "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "description_template": {"type": "string"},
        "item_key_column": {"type": ["string", "null"]},
        "conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["column", "op"],
                "properties": {
                    "column": {"type": "string"},
                    "op": {"type": "string", "enum": sorted(OPERATORS)},
                    "value": {
                        "type": ["string", "number", "boolean", "array", "null"]
                    },
                },
            },
        },
    },
}

# Single-source operator vocabulary surfaced to the model (mirrors
# render_rule._BINARY / _SET / _UNARY). Cross-source ops live in OPERATORS for
# schema/engine parity but are out of scope for drafting (single source only).
_OP_GLOSSARY: list[tuple[str, str]] = [
    ("eq", "equals a value"),
    ("ne", "does not equal a value"),
    ("gt", "greater than a number"),
    ("ge", "greater than or equal to a number"),
    ("lt", "less than a number"),
    ("le", "less than or equal to a number"),
    ("is_empty", "the cell is blank/null"),
    ("not_empty", "the cell has a value"),
    ("in", "value is one of a list (set value to a JSON array)"),
    ("not_in", "value is not in a list (set value to a JSON array)"),
    ("regex", "the cell matches a regular expression (set value to the pattern)"),
    ("is_duplicate", "the value appears more than once in the column"),
]

_SAMPLE_ROW_CAP = 20


class DraftError(Exception):
    """A drafted rule_spec parsed but failed the run-on-sample / column gate."""


def draft_and_validate(
    *,
    objective: str,
    source_schema: dict,
    data_sample: dict,
    provider: str,
    model: str,
) -> dict:
    """Draft a rule_spec via *provider* and validate it; return the validated dict.

    Raises
    ------
    RuleSpecError
        The draft is malformed (bad operator / missing column field).
    DraftError
        The draft references an unknown column, or does not execute on the sample.
    """
    raw = get_provider(provider).draft_rule_spec(
        objective, source_schema, data_sample, model=model
    )
    spec = parse_rule_spec(raw)  # → RuleSpecError on bad shape
    _check_columns(spec, source_schema)
    _run_on_sample(spec, source_schema, data_sample)
    return raw


def _schema_columns(source_schema: dict) -> list[dict]:
    """The list of column-metadata dicts from a source schema payload."""
    cols = source_schema.get("columns")
    return list(cols) if isinstance(cols, list) else []


def _check_columns(spec: RuleSpec, source_schema: dict) -> None:
    known = {c.get("original_name") for c in _schema_columns(source_schema)}
    for col in referenced_columns(spec):
        if col not in known:
            raise DraftError(
                f"the drafted rule references a column not in your data: {col!r}"
            )


def _run_on_sample(spec: RuleSpec, source_schema: dict, data_sample: dict) -> None:
    """Prove the spec executes by running ``evaluate_rule`` on a tiny population.

    Builds a capped in-memory :class:`Population` from the sample rows + column
    metadata. Cells arrive stringified, so each column is coerced by its declared
    ``data_type`` with the same ``coerce_series`` the real adapters use — the gate
    therefore executes the spec exactly as the full-population run will (a numeric
    ``> 100`` works against a ``number`` column). Any failure (e.g. a bad regex)
    is wrapped as :class:`DraftError`. ``RuleSpecError`` propagates unchanged.
    """
    # Imported here (pandas/adapters) so module import stays light; the route only
    # reaches this path once a provider+key is confirmed present.
    import pandas as pd

    from uticen_lite.adapters.files import coerce_series
    from uticen_lite.model.population import ColumnMeta, Population
    from uticen_lite.rules.evaluate import evaluate_rule

    cols = _schema_columns(source_schema)
    original_names = [c["original_name"] for c in cols]
    rows = list(data_sample.get("rows", []))[:_SAMPLE_ROW_CAP]
    raw_df = pd.DataFrame(rows, columns=original_names) if original_names else pd.DataFrame()
    df = pd.DataFrame(
        {c["original_name"]: coerce_series(raw_df[c["original_name"]],
                                           c.get("data_type", "text"))
         for c in cols}
    )

    column_meta = [
        ColumnMeta(
            original_name=c["original_name"],
            display_name=c.get("display_name", c["original_name"]),
            data_type=c.get("data_type", "text"),
            is_key=bool(c.get("is_key")),
        )
        for c in cols
    ]
    pop = Population(df=df, columns=column_meta, source_id="ai-sample")
    try:
        evaluate_rule(spec, pop)
    except Exception as exc:  # noqa: BLE001 — any engine failure is a bad draft
        raise DraftError(f"the drafted rule did not run on your data: {exc}") from exc


# --------------------------------------------------------------------------- #
# Prompt assembly (shared by all three backends)
# --------------------------------------------------------------------------- #
def system_prompt() -> str:
    glossary = "\n".join(f"  - {op}: {meaning}" for op, meaning in _OP_GLOSSARY)
    return (
        "You author full-population control-test rules as a JSON rule_spec.\n"
        "A rule flags rows that match its conditions. Combine conditions with "
        "'logic' = 'all' (AND) or 'any' (OR).\n"
        "Each condition has a 'column' (the EXACT original_name of a listed "
        "column), an 'op', and (for most ops) a 'value'.\n"
        "Available operators:\n"
        f"{glossary}\n"
        "Reference only the columns listed in the user message, by their exact "
        "original_name. Do not invent columns. Output must match the provided "
        "JSON schema exactly."
    )


def user_prompt(objective: str, source_schema: dict, data_sample: dict) -> str:
    cols = _schema_columns(source_schema)
    col_lines = "\n".join(
        f"  - {c['original_name']} (display: {c.get('display_name', c['original_name'])}, "
        f"type: {c.get('data_type', 'text')})"
        for c in cols
    )
    original_names = [c["original_name"] for c in cols]
    rows = list(data_sample.get("rows", []))[:_SAMPLE_ROW_CAP]
    sample_lines = "\n".join(
        "  | " + " | ".join(str(cell) for cell in row) for row in rows
    )
    return (
        f"Control objective:\n{objective.strip()}\n\n"
        f"Columns (use these exact original_name values):\n{col_lines}\n\n"
        f"Header order: {', '.join(original_names)}\n"
        f"Sample rows (first {len(rows)}):\n{sample_lines}\n\n"
        "Draft a rule_spec that flags the rows this control should catch."
    )
