"""TDD tests for the render module (Task 6).

Step 1: write failing tests.
Step 3: implement renderers so these pass.
"""

from __future__ import annotations

import pytest

from controlflow_sdk.model.run import RunRecord, SourceProvenance
from controlflow_sdk.model.violation import Severity, Violation
from controlflow_sdk.model.workpaper import Procedure, Workpaper
from controlflow_sdk.render import render_html, render_markdown

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

    def test_full_population_no_sampling_statement(self, workpaper: Workpaper) -> None:
        """Each procedure's results section must assert full-population coverage."""
        md = render_markdown(workpaper)
        assert "No sampling was applied" in md
        # The population size (100) must appear in the statement
        assert "100 record(s)" in md

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
        from controlflow_sdk.model.run import RunRecord
        from controlflow_sdk.model.violation import Severity, Violation
        from controlflow_sdk.model.workpaper import Procedure, Workpaper

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

    def test_no_script_tags(self, workpaper: Workpaper) -> None:
        html = render_html(workpaper)
        assert "<script" not in html.lower()

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

    def test_full_population_no_sampling_statement(self, workpaper: Workpaper) -> None:
        """Each procedure's results section must assert full-population coverage."""
        html = render_html(workpaper)
        assert "No sampling was applied" in html
        assert "100 record(s)" in html
