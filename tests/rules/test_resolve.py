"""Unit tests for the shared test_code resolver (issue #12).

Locks the canonical priority order inline → rule → file → "" that both
``bundle.assemble`` and ``store.run_service`` now share, so the two producers
cannot drift.  See ``docs/superpowers/specs/2026-06-20-12-...`` and learning 0001.
"""

from __future__ import annotations

import pathlib

from controlflow_sdk.model.control import ControlDef, FrameworkRefs
from controlflow_sdk.rules.render_rule import rule_to_text
from controlflow_sdk.rules.resolve import resolve_test_code
from controlflow_sdk.rules.spec import parse_rule_spec

_RULE_SPEC = {
    "logic": "all",
    "conditions": [{"column": "can_create", "op": "eq", "value": True}],
    "severity": "high",
}


def _control(**overrides: object) -> ControlDef:
    base: dict[str, object] = {
        "id": "c1",
        "title": "Control One",
        "objective": "o",
        "narrative": "n",
        "framework_refs": FrameworkRefs(nist=[]),
        "risk": None,
        "sources": [],
    }
    base.update(overrides)
    return ControlDef(**base)  # type: ignore[arg-type]


def test_inline_wins_over_rule_and_file(tmp_path: pathlib.Path) -> None:
    """Inline test_code takes priority over both rule_spec and test_path.

    This is the exact case the two old call sites disagreed on, so it pins the
    canonical inline-first priority.
    """
    py = tmp_path / "test.py"
    py.write_text("# from file\n", encoding="utf-8")
    control = _control(test_code="X", rule_spec=_RULE_SPEC, test_path=str(py))
    assert resolve_test_code(control) == "X"


def test_rule_renders_to_text_when_no_inline() -> None:
    """With no inline code, a rule_spec is rendered to human-readable text."""
    control = _control(test_code=None, rule_spec=_RULE_SPEC)
    result = resolve_test_code(control)
    assert result == rule_to_text(parse_rule_spec(_RULE_SPEC))
    assert "Flag a record when ALL" in result


def test_file_read_when_no_inline_no_rule(tmp_path: pathlib.Path) -> None:
    """With neither inline code nor a rule, the test_path file is read verbatim."""
    content = "# Cash cutoff test\ndef test(pop):\n    return []\n"
    py = tmp_path / "test.py"
    py.write_text(content, encoding="utf-8")
    control = _control(test_code=None, rule_spec=None, test_path=str(py))
    assert resolve_test_code(control) == content


def test_empty_string_when_nothing_set() -> None:
    """No inline code, no rule, no path → empty string fallback."""
    control = _control(test_code=None, rule_spec=None, test_path="")
    assert resolve_test_code(control) == ""
