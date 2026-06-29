"""TDD tests for the render module (Task 6).

Step 1: write failing tests.
Step 3: implement renderers so these pass.
"""

from __future__ import annotations

import pytest

from uticen_lite.model.run import RunRecord, SourceProvenance
from uticen_lite.model.violation import Severity, Violation
from uticen_lite.model.workpaper import Procedure, Workpaper
from uticen_lite.render import render_html, render_markdown

# ── shared fixture ────────────────────────────────────────────────────────────

XSS_DESCRIPTION = "Bad data with <b>bold</b> & 'quotes'"


@pytest.fixture()
def workpaper() -> Workpaper:
    """Build a Workpaper directly (no filesystem I/O needed)."""
    prov = SourceProvenance(
        source_id="src-1",
        path="/data/invoices.csv",
        sha256="abc123def456abc123def456abc123def456abc123def456abc123def456abc1",
        row_count=100,
    )
    run = RunRecord(
        control_id="ctrl-001",
        executed_at="2026-06-16T00:00:00Z",
        population_size=100,
        violations=[
            Violation(
                item_key="INV-001",
                description="Amount exceeds limit",
                severity=Severity.HIGH,
            ),
            Violation(
                item_key="INV-002",
                description=XSS_DESCRIPTION,
                severity=Severity.CRITICAL,
            ),
        ],
        provenance=[prov],
    )
    procedure = Procedure(
        title="Invoice Threshold Test",
        narrative="All invoices over $1,000 require dual approval.",
        test_code="result = df[df['amount'] > 1000]",
        result=run,
    )
    return Workpaper(
        control_id="ctrl-001",
        title="Invoice Amount Control",
        objective="Ensure no invoices exceed approved limits.",
        narrative="Control owner: Finance team.",
        framework_refs={"nist": ["AC-2", "AU-6"], "extra": {}},
        procedures=[procedure],
        generated_at="2026-06-16T00:00:00Z",
    )


# ── render_markdown tests ─────────────────────────────────────────────────────


class TestRenderMarkdown:
    def test_contains_control_title(self, workpaper: Workpaper) -> None:
        md = render_markdown(workpaper)
        assert "Invoice Amount Control" in md

    def test_contains_pass_rate(self, workpaper: Workpaper) -> None:
        md = render_markdown(workpaper)
        # pass_rate = (100 - 2) / 100 * 100 = 98.0
        assert "98.0" in md

    def test_contains_each_violation_item_key(self, workpaper: Workpaper) -> None:
        md = render_markdown(workpaper)
        assert "INV-001" in md
        assert "INV-002" in md

    def test_contains_source_sha256(self, workpaper: Workpaper) -> None:
        md = render_markdown(workpaper)
        prov = workpaper.procedures[0].result.provenance[0]
        assert prov.sha256 in md

    def test_contains_objective(self, workpaper: Workpaper) -> None:
        md = render_markdown(workpaper)
        assert "Ensure no invoices exceed approved limits." in md

    def test_contains_framework_refs(self, workpaper: Workpaper) -> None:
        md = render_markdown(workpaper)
        assert "AC-2" in md
        assert "AU-6" in md

    def test_full_population_stated_once_in_header(self, workpaper: Workpaper) -> None:
        """Full-population coverage is stated once (header), not per procedure."""
        md = render_markdown(workpaper)
        assert "Full-population test" not in md
        assert "no sampling applied" not in md
        # The old per-procedure restatement is gone.
        assert "No sampling was applied" not in md

    def test_threshold_conclusion(self, workpaper: Workpaper) -> None:
        """Conclusion states the threshold determination (implicit-0 fixture)."""
        md = render_markdown(workpaper)
        assert "## Conclusion" in md
        assert "zero exceptions tolerated" in md
        assert "did not operate effectively" in md

    def test_section_headers_title_cased(self, workpaper: Workpaper) -> None:
        md = render_markdown(workpaper)
        assert "## Objective & Scope" in md
        assert "## Data Sources" in md
        assert "### Framework References" in md
        # Old lowercase forms are gone.
        assert "## Objective & scope" not in md
        assert "## Data sources" not in md

    def test_data_source_default_completeness_accuracy(self, workpaper: Workpaper) -> None:
        """Each data source shows a Completeness & Accuracy line (default derived)."""
        md = render_markdown(workpaper)
        assert "**Completeness & Accuracy:**" in md
        assert "tested in full" in md
        assert "no sampling" in md

    def test_data_source_authored_description_and_ca(self, workpaper: Workpaper) -> None:
        from uticen_lite.model.workpaper import DataSample

        # The fixture's procedure already records provenance for source_id "src-1";
        # attach a render-only sample carrying authored prose for that same source.
        sample = DataSample(
            source_id="src-1",
            path="/data/invoices.csv",
            columns=["Invoice", "Amount"],
            rows=[["INV-001", "1500"]],
            total_rows=1,
            description="Vendor invoice register for the period.",
            completeness_accuracy="Reconciled to the AP subledger control account.",
        )
        workpaper.data_samples = [sample]
        md = render_markdown(workpaper)
        assert "**Description:** Vendor invoice register for the period." in md
        assert "**Completeness & Accuracy:** Reconciled to the AP subledger control account." in md

    def test_evaluation_section_removed(self, workpaper: Workpaper) -> None:
        md = render_markdown(workpaper)
        assert "## Evaluation" not in md

    def test_results_table_has_no_failed_row(self, workpaper: Workpaper) -> None:
        md = render_markdown(workpaper)
        results = md[md.index("## Results") : md.index("## Objective")]
        assert "| Records Tested |" in results
        assert "| Passed |" in results
        assert "| Exceptions |" in results
        assert "| Failed |" not in results

    def test_contains_test_code_fenced_block(self, workpaper: Workpaper) -> None:
        md = render_markdown(workpaper)
        assert "```" in md
        assert "result = df[df['amount'] > 1000]" in md

    def test_contains_passed_and_failed_counts(self, workpaper: Workpaper) -> None:
        md = render_markdown(workpaper)
        assert "98" in md  # passed
        assert "2" in md  # failed

    def test_contains_provenance_path(self, workpaper: Workpaper) -> None:
        md = render_markdown(workpaper)
        assert "/data/invoices.csv" in md

    def test_contains_row_count(self, workpaper: Workpaper) -> None:
        md = render_markdown(workpaper)
        assert "100" in md

    def test_pipe_and_newline_in_violation_escaped(self) -> None:
        """Pipe in item_key and newline in description must not break the table row."""
        from uticen_lite.model.run import RunRecord
        from uticen_lite.model.violation import Severity, Violation
        from uticen_lite.model.workpaper import Procedure, Workpaper

        run = RunRecord(
            control_id="ctrl-x",
            executed_at="2026-06-16T00:00:00Z",
            population_size=10,
            violations=[
                Violation(
                    item_key="INV|002",
                    description="First line\nSecond line",
                    severity=Severity.HIGH,
                ),
            ],
            provenance=[],
        )
        procedure = Procedure(
            title="Pipe Test",
            narrative="Testing escape.",
            test_code="pass",
            result=run,
        )
        wp = Workpaper(
            control_id="ctrl-x",
            title="Pipe Test WP",
            objective="Test pipe escaping.",
            narrative="N/A",
            framework_refs={"nist": [], "extra": {}},
            procedures=[procedure],
            generated_at="2026-06-16T00:00:00Z",
        )

        md = render_markdown(wp)

        # The violation renders in two tables now: the 3-column per-procedure
        # violations table and the 4-column Exceptions summary table. Both must
        # escape the pipe; assert on the per-procedure row (3 columns → 4
        # delimiter pipes, so no leading "E-" ref cell).
        rows = [line for line in md.splitlines() if "INV" in line]
        assert len(rows) == 2, "Violation must render in the procedure + exceptions tables"
        proc_rows = [r for r in rows if not r.lstrip("| ").startswith("E-")]
        assert len(proc_rows) == 1, "Exactly one per-procedure violation row expected"
        row = proc_rows[0]

        # Pipe in item_key must be escaped as \|
        assert "INV\\|002" in row

        # The row must not contain a bare (unescaped) pipe inside a cell value.
        # We verify this by checking that every "|" in the row is either a cell
        # delimiter or part of the escape sequence "\|" — i.e. no lone "|" that
        # isn't preceded by "\".  A simpler structural check: after replacing
        # all escaped pipes with a placeholder, the remaining "|" count must
        # equal the number of column delimiters for a 3-column table (4 pipes:
        # "| col1 | col2 | col3 |").
        row_no_escaped = row.replace("\\|", "ESCAPED_PIPE")
        assert row_no_escaped.count("|") == 4, (
            f"Expected 4 delimiter pipes after removing escaped pipes, "
            f"got {row_no_escaped.count('|')}: {row!r}"
        )

        # Newline in description must be collapsed to a space
        assert "\n" not in row
        assert "First line Second line" in row


# ── render_html tests ─────────────────────────────────────────────────────────


class TestRenderHtml:
    def test_starts_with_doctype(self, workpaper: Workpaper) -> None:
        html = render_html(workpaper)
        assert html.startswith("<!doctype html>")

    def test_single_inline_script(self, workpaper: Workpaper) -> None:
        # The data-table widget is the ONE permitted inline script; no others.
        html = render_html(workpaper)
        assert html.lower().count("<script") == 1
        assert "jquery" not in html.lower()

    def test_escapes_xss_description(self, workpaper: Workpaper) -> None:
        html = render_html(workpaper)
        # The raw <b> must not appear; escaped form must appear
        assert "<b>" not in html
        assert "&lt;b&gt;" in html

    def test_contains_control_title(self, workpaper: Workpaper) -> None:
        html = render_html(workpaper)
        assert "Invoice Amount Control" in html

    def test_contains_pass_rate(self, workpaper: Workpaper) -> None:
        html = render_html(workpaper)
        assert "98.0" in html

    def test_contains_each_violation_item_key(self, workpaper: Workpaper) -> None:
        html = render_html(workpaper)
        assert "INV-001" in html
        assert "INV-002" in html

    def test_contains_source_sha256(self, workpaper: Workpaper) -> None:
        html = render_html(workpaper)
        prov = workpaper.procedures[0].result.provenance[0]
        assert prov.sha256 in html

    def test_has_inline_style(self, workpaper: Workpaper) -> None:
        html = render_html(workpaper)
        assert "<style>" in html

    def test_no_external_stylesheet_link(self, workpaper: Workpaper) -> None:
        html = render_html(workpaper)
        assert 'rel="stylesheet"' not in html
        assert "<link" not in html

    def test_escapes_ampersand_in_description(self, workpaper: Workpaper) -> None:
        html = render_html(workpaper)
        # XSS_DESCRIPTION contains & which must be escaped
        assert "&amp;" in html

    def test_contains_framework_refs(self, workpaper: Workpaper) -> None:
        html = render_html(workpaper)
        assert "AC-2" in html
        assert "AU-6" in html

    def test_full_population_stated_once_in_header(self, workpaper: Workpaper) -> None:
        """Full-population coverage is stated once (header caption), not per proc."""
        html = render_html(workpaper)
        assert html.count("no sampling applied") == 0
        assert "No sampling was applied" not in html


# ── N>1 procedure rendering tests ─────────────────────────────────────────────


def _make_two_procedure_workpaper() -> Workpaper:
    """A 2-procedure workpaper built via the real ``Workpaper.assemble_procedures`` factory.

    P1 passes (0 violations, zero-tolerance threshold); P2 fails (1 violation,
    zero-tolerance threshold).  Uses the genuine factory path — the same one
    Task 4's run service drives — so the renderer gets real per-procedure
    determinations.
    """
    from uticen_lite.model.control import ControlDef, FrameworkRefs, Threshold
    from uticen_lite.model.workpaper import ProcedureSpec, Workpaper

    control = ControlDef(
        id="ctrl-mp",
        title="Multi-Procedure Control",
        objective="Ensure items comply with both checks.",
        narrative="Finance team.",
        framework_refs=FrameworkRefs(nist=[], extra={}),
        risk=None,
        sources=[],
    )

    prov = SourceProvenance(
        source_id="src-1",
        path="/data/invoices.csv",
        sha256="abc123def456abc123def456abc123def456abc123def456abc123def456abc1",
        row_count=50,
    )
    run_pass = RunRecord(
        control_id="ctrl-mp",
        executed_at="2026-06-22T00:00:00Z",
        population_size=50,
        violations=[],
        provenance=[prov],
    )
    run_fail = RunRecord(
        control_id="ctrl-mp",
        executed_at="2026-06-22T00:00:00Z",
        population_size=50,
        violations=[
            Violation(
                item_key="INV-999",
                description="Over limit",
                severity=Severity.HIGH,
            ),
        ],
        provenance=[prov],
    )
    spec_pass = ProcedureSpec(
        title="Approval Check",
        narrative="All items require approval.",
        test_code="result = df[df['approved'] == False]",
        threshold=Threshold(),
    )
    spec_fail = ProcedureSpec(
        title="Amount Limit Check",
        narrative="No item may exceed the limit.",
        test_code="result = df[df['amount'] > 5000]",
        threshold=Threshold(),
    )
    return Workpaper.assemble_procedures(
        control,
        [(spec_pass, run_pass), (spec_fail, run_fail)],
        generated_at="2026-06-22T00:00:00Z",
        data_samples=None,
    )


class TestMultiProcedureHtml:
    def test_both_procedure_titles_present(self) -> None:
        html = render_html(_make_two_procedure_workpaper())
        assert "Approval Check" in html
        assert "Amount Limit Check" in html

    def test_per_procedure_verdict_pills_present(self) -> None:
        """Each procedure must have its own verdict pill in the Procedures section."""
        html = render_html(_make_two_procedure_workpaper())
        # P1 passes → "Operated effectively"; P2 fails → "Operated with deficiencies"
        procedures_start = html.index('<section id="procedures"')
        procedures_end = html.index('<section id="exceptions"')
        procedures_block = html[procedures_start:procedures_end]
        assert "Operated effectively" in procedures_block
        assert "Operated with deficiencies" in procedures_block

    def test_per_procedure_verdict_pill_count(self) -> None:
        """Exactly 2 verdict pills in the procedures section for a 2-procedure workpaper."""
        html = render_html(_make_two_procedure_workpaper())
        procedures_start = html.index('<section id="procedures"')
        procedures_end = html.index('<section id="exceptions"')
        procedures_block = html[procedures_start:procedures_end]
        # Both verdict strings together = 2 pills
        assert procedures_block.count("Operated effectively") == 1
        assert procedures_block.count("Operated with deficiencies") == 1

    def test_overall_verdict_shows_deficiencies(self) -> None:
        """Overall verdict is deficiencies when any procedure fails."""
        html = render_html(_make_two_procedure_workpaper())
        # The results bar pill reflects the overall control verdict
        bar_start = html.index('<div class="wp-resultbar">')
        bar_end = html.index("</div>", bar_start)
        bar = html[bar_start:bar_end]
        assert "Operated with deficiencies" in bar

    def test_n1_procedures_section_no_pill(self, workpaper: Workpaper) -> None:
        """N==1: procedures section has NO per-procedure verdict pill (byte-identical to today)."""
        html = render_html(workpaper)
        procedures_start = html.index('<section id="procedures"')
        procedures_end = html.index('<section id="exceptions"')
        procedures_block = html[procedures_start:procedures_end]
        # N==1 must NOT add a verdict pill inside the procedures section
        assert "Operated with deficiencies" not in procedures_block
        assert "Operated effectively" not in procedures_block

    def test_n1_backward_compat_overall_verdict(self, workpaper: Workpaper) -> None:
        """N==1: overall verdict in results bar is still 'Operated with deficiencies'."""
        html = render_html(workpaper)
        bar_start = html.index('<div class="wp-resultbar">')
        bar_end = html.index("</div>", bar_start)
        bar = html[bar_start:bar_end]
        assert "Operated with deficiencies" in bar


class TestMultiProcedureMarkdown:
    def test_both_procedure_titles_present(self) -> None:
        md = render_markdown(_make_two_procedure_workpaper())
        assert "Approval Check" in md
        assert "Amount Limit Check" in md

    def test_per_procedure_verdict_in_procedures_section(self) -> None:
        """Each procedure heading shows its own verdict."""
        md = render_markdown(_make_two_procedure_workpaper())
        procedures_start = md.index("## Procedures")
        exceptions_start = md.index("## Exceptions")
        procedures_block = md[procedures_start:exceptions_start]
        assert "Operated effectively" in procedures_block
        assert "Operated with deficiencies" in procedures_block

    def test_overall_verdict_shows_deficiencies(self) -> None:
        """Overall verdict reflects any-fails roll-up."""
        md = render_markdown(_make_two_procedure_workpaper())
        assert "Operated with deficiencies" in md

    def test_n1_procedures_section_no_verdict_line(self, workpaper: Workpaper) -> None:
        """N==1: procedures section has NO per-procedure verdict line (byte-identical to today)."""
        md = render_markdown(workpaper)
        procedures_start = md.index("## Procedures")
        exceptions_start = md.index("## Exceptions")
        procedures_block = md[procedures_start:exceptions_start]
        assert "Procedure verdict:" not in procedures_block
