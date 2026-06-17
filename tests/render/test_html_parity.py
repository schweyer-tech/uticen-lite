"""SDK-only structural regression test for app-parity HTML rendering.

Every assertion runs against the string returned by ``render_html(wp)`` — there
is **no live-app fetch, no network, no coupling to the app deployment**. The
tests are deterministic, offline, and Pyodide-safe.

See ``docs/superpowers/specs/2026-06-17-workpaper-app-parity-design.md`` §6.
"""

from __future__ import annotations

import re

import pytest

from controlflow_sdk.model.run import RunRecord, SourceProvenance
from controlflow_sdk.model.violation import Severity, Violation
from controlflow_sdk.model.workpaper import Procedure, Workpaper
from controlflow_sdk.render import render_html

# Canonical 8-section order (Sign-off omitted entirely).
EXPECTED_SECTION_IDS = [
    "results",
    "objective-scope",
    "control",
    "data-sources",
    "procedures",
    "evaluation",
    "exceptions",
    "conclusion",
]

XSS_DESCRIPTION = "Bad data with <b>bold</b> & 'quotes'"


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_workpaper(violations: list[Violation]) -> Workpaper:
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
        violations=violations,
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


@pytest.fixture()
def failing_workpaper() -> Workpaper:
    """A control that failed — 2 violations (one carries an XSS payload)."""
    return _make_workpaper(
        [
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
        ]
    )


@pytest.fixture()
def passing_workpaper() -> Workpaper:
    """A control that operated effectively — 0 violations."""
    return _make_workpaper([])


# ── helpers ───────────────────────────────────────────────────────────────────


def _section_ids(html: str) -> list[str]:
    return re.findall(r'<section id="([^"]+)"', html)


def _exceptions_block(html: str) -> str:
    """The substring from the Exceptions <section> to the next <section> (or end)."""
    start = html.index('<section id="exceptions"')
    rest = html[start + 1 :]
    nxt = rest.find("<section id=")
    return rest if nxt == -1 else rest[:nxt]


# ── 1. sections present & in order ────────────────────────────────────────────


class TestRenderHtmlAppParity:
    def test_sections_present_and_in_order(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert _section_ids(html) == EXPECTED_SECTION_IDS

    def test_signoff_section_omitted(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert 'id="sign-off"' not in html

    # ── 2. sidebar anchors ────────────────────────────────────────────────────

    def test_sidebar_has_one_anchor_per_section(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        for anchor in EXPECTED_SECTION_IDS:
            assert f'href="#{anchor}"' in html

    def test_sidebar_anchors_precede_content(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        first_section = html.index("<section id=")
        for anchor in EXPECTED_SECTION_IDS:
            assert html.index(f'href="#{anchor}"') < first_section

    # ── 3. <details> collapse markers ─────────────────────────────────────────

    def test_details_count_matches_collapsible_elements(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        # sources (deduped) + procedures (code block) + violations
        sources = {
            p.source_id for proc in failing_workpaper.procedures for p in proc.result.provenance
        }
        n_proc = len(failing_workpaper.procedures)
        n_viol = sum(len(p.result.violations) for p in failing_workpaper.procedures)
        expected = len(sources) + n_proc + n_viol
        assert html.count("<details>") == expected

    def test_all_details_default_closed(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert "<details>" in html
        assert "<details open" not in html

    # ── 4. key design tokens baked in ─────────────────────────────────────────

    def test_canonical_tokens_present(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        for token in ("#0b0d17", "#12141f", "#3b82f6", "#10b981", "#ef4444"):
            assert token in html
        assert "--accent-primary" in html

    def test_font_stacks_present(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert "JetBrains Mono" in html
        assert "Inter" in html

    # ── 5. passing vs failing render ──────────────────────────────────────────

    def test_failing_render(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert "Operated with deficiencies" in html
        assert ">FAIL" in html
        assert "INV-001" in html
        assert "INV-002" in html
        assert "2 fail" in html

    def test_passing_render(self, passing_workpaper: Workpaper) -> None:
        html = render_html(passing_workpaper)
        assert "Operated effectively" in html
        assert "100.0%" in html
        assert "No exceptions" in html
        assert "0 fail" in html
        # No violation rows table inside the Exceptions section.
        assert "<table" not in _exceptions_block(html)

    def test_passing_verdict_matches_conclusion(self, passing_workpaper: Workpaper) -> None:
        html = render_html(passing_workpaper)
        # Verdict appears in the sticky pill AND the conclusion line.
        assert html.count("Operated effectively") >= 2

    # ── 6. guarantees still hold (regression guard) ───────────────────────────

    def test_self_contained_no_script_no_link(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert html.startswith("<!doctype html>")
        assert "<script" not in html.lower()
        assert 'rel="stylesheet"' not in html
        assert "<link" not in html

    def test_xss_escaped(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert "<b>" not in html
        assert "&lt;b&gt;" in html

    # ── 7. full-population statement per procedure ────────────────────────────

    def test_full_population_statement_per_procedure(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert html.count("No sampling was applied") == len(failing_workpaper.procedures)
        assert "100 record(s)" in html
