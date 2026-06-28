"""TDD tests for procedure code/assertion + per-check render (Task 4)."""

from __future__ import annotations

from controlflow_sdk.model.run import RunRecord
from controlflow_sdk.model.violation import Violation
from controlflow_sdk.model.workpaper import Procedure, ProcedureSpec, Workpaper


def _proc() -> Procedure:
    run = RunRecord(
        control_id="gl1",
        executed_at="t",
        population_size=650,
        violations=[
            Violation.from_raw({
                "item_key": "A",
                "description": "x",
                "severity": "high",
                "details": {"checks": ["no approval", "preparer=approver"]},
            }),
        ],
    )
    return Procedure(
        code="P1",
        title="Manual JE Review",
        assertion="Segregation of Duties",
        narrative="we tested…",
        test_code="def test(pop): ...",
        result=run,
    )


def test_to_dict_emits_code_and_assertion():
    d = _proc().to_dict()
    assert d["code"] == "P1"
    assert d["assertion"] == "Segregation of Duties"
    assert d["title"] == "Manual JE Review"


def test_html_renders_code_assertion_and_checks():
    from controlflow_sdk.render.html import render_html

    wp = Workpaper(
        control_id="gl1",
        title="t",
        objective="o",
        narrative="n",
        framework_refs={},
        procedures=[_proc(), _proc()],
        generated_at="t",
    )
    html = render_html(wp)
    assert "P1" in html and "Segregation of Duties" in html
    assert "preparer=approver" in html  # which-check annotation surfaced


def test_markdown_renders_code_assertion_and_checks():
    from controlflow_sdk.render.markdown import render_markdown

    wp = Workpaper(
        control_id="gl1",
        title="t",
        objective="o",
        narrative="n",
        framework_refs={},
        procedures=[_proc(), _proc()],
        generated_at="t",
    )
    md = render_markdown(wp)
    assert "P1" in md and "Segregation of Duties" in md
    assert "preparer=approver" in md


def test_procedure_spec_carries_code_assertion():
    """ProcedureSpec must carry code/assertion for the run_service wiring."""
    spec = ProcedureSpec(
        code="P1",
        title="Manual JE Review",
        assertion="Segregation of Duties",
        narrative="n",
        test_code="...",
    )
    assert spec.code == "P1"
    assert spec.assertion == "Segregation of Duties"


def test_assemble_procedures_threads_code_assertion():
    """Workpaper.assemble_procedures must copy code/assertion from ProcedureSpec into Procedure."""
    from unittest.mock import MagicMock

    run = RunRecord(
        control_id="gl1", executed_at="t", population_size=10, violations=[]
    )
    spec = ProcedureSpec(
        code="P1",
        title="Manual JE Review",
        assertion="Segregation of Duties",
        narrative="n",
        test_code="...",
    )

    # Build a minimal mock ControlDef
    ctrl = MagicMock()
    ctrl.id = "gl1"
    ctrl.title = "GL Control"
    ctrl.objective = "o"
    ctrl.narrative = "n"
    ctrl.framework_refs.to_dict.return_value = {}
    ctrl.threshold.to_dict.return_value = {}

    wp = Workpaper.assemble_procedures(ctrl, [(spec, run)], generated_at="t")
    assert wp.procedures[0].code == "P1"
    assert wp.procedures[0].assertion == "Segregation of Duties"


def test_code_assertion_empty_no_extra_html_lines():
    """When code/assertion are empty, N==1 HTML must be byte-identical (no new lines)."""
    from controlflow_sdk.render.html import render_html

    run = RunRecord(
        control_id="c1", executed_at="t", population_size=10, violations=[]
    )
    proc = Procedure(
        code="",
        title="My Test",
        assertion="",
        narrative="n",
        test_code="...",
        result=run,
    )
    wp = Workpaper(
        control_id="c1", title="T", objective="o", narrative="n",
        framework_refs={}, procedures=[proc], generated_at="t",
    )
    html = render_html(wp)
    assert 'class="assert"' not in html


def test_lone_auto_code_empty_heading_is_legacy_form():
    """Single-procedure workpaper with code='' renders the legacy 'P1: title' heading.

    Pins the byte-identity guarantee: a lone auto-derived procedure (code='') must
    produce 'P1: title' in both HTML and Markdown — NOT the 'P1 · title' middot form
    that a non-empty code would generate.
    """
    from controlflow_sdk.render.html import render_html
    from controlflow_sdk.render.markdown import render_markdown

    run = RunRecord(
        control_id="c1", executed_at="t", population_size=10, violations=[]
    )
    proc = Procedure(
        code="",          # lone auto procedure — code always empty
        title="Cash Cutoff",
        assertion="",
        narrative="n",
        test_code="...",
        result=run,
    )
    wp = Workpaper(
        control_id="c1", title="T", objective="o", narrative="n",
        framework_refs={}, procedures=[proc], generated_at="t",
    )

    # HTML: legacy "P1: title" heading, not the "P1 &middot; title" code-prefix form.
    html = render_html(wp)
    assert "P1: Cash Cutoff" in html
    assert "P1 &middot; Cash Cutoff" not in html

    # Markdown: same "P1: title" heading form.
    md = render_markdown(wp)
    assert "### P1: Cash Cutoff" in md
