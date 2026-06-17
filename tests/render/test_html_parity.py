"""SDK-only structural regression test for app-parity HTML rendering.

Every assertion runs against the string returned by ``render_html(wp)`` — there
is **no live-app fetch, no network, no coupling to the app deployment**. The
tests are deterministic, offline, and Pyodide-safe.

Round-2 revisions (see ``docs/superpowers/specs/2026-06-18-workpaper-revisions-design.md``):
Evaluation section removed; results order Records→Passed→Exceptions (no Failed
tile); a single full-population statement in the header; an interactive data
table (one inline init script, 500-row cap note); and a Conclusion that states
the threshold determination.
"""

from __future__ import annotations

import re

import pytest

from controlflow_sdk.model.control import Threshold
from controlflow_sdk.model.run import RunRecord, SourceProvenance
from controlflow_sdk.model.violation import Severity, Violation
from controlflow_sdk.model.workpaper import DataSample, Procedure, Workpaper
from controlflow_sdk.render import render_html

# Canonical 7-section order (Sign-off and Evaluation both omitted).
EXPECTED_SECTION_IDS = [
    "results",
    "objective-scope",
    "control",
    "data-sources",
    "procedures",
    "exceptions",
    "conclusion",
]

XSS_DESCRIPTION = "Bad data with <b>bold</b> & 'quotes'"


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_workpaper(
    violations: list[Violation],
    *,
    threshold: Threshold | None = None,
    data_samples: list[DataSample] | None = None,
) -> Workpaper:
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
        threshold=threshold or Threshold(),
        data_samples=data_samples or [],
    )


@pytest.fixture()
def failing_workpaper() -> Workpaper:
    """A control that failed — 2 violations, implicit-0 threshold (any → deficiency)."""
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
def pass_under_threshold_workpaper() -> Workpaper:
    """2 exceptions out of 100 records (2%), threshold 5% → PASSES via threshold."""
    return _make_workpaper(
        [
            Violation(item_key="INV-001", description="Minor rounding", severity=Severity.LOW),
            Violation(item_key="INV-002", description="Minor rounding", severity=Severity.LOW),
        ],
        threshold=Threshold(failure_threshold_pct=5.0),
    )


@pytest.fixture()
def passing_workpaper() -> Workpaper:
    """A control that operated effectively — 0 violations."""
    return _make_workpaper([])


@pytest.fixture()
def workpaper_with_small_sample() -> Workpaper:
    """A control whose source carries an un-capped data sample (3 of 3 rows)."""
    sample = DataSample(
        source_id="src-1",
        path="/data/invoices.csv",
        columns=["Invoice", "Amount"],
        rows=[["INV-001", "1500"], ["INV-002", "900"], ["INV-003", "2200"]],
        total_rows=3,
    )
    return _make_workpaper([], data_samples=[sample])


@pytest.fixture()
def workpaper_with_capped_sample() -> Workpaper:
    """A control whose source has more rows than the 500-row embed cap."""
    rows = [[f"INV-{i:04d}", str(i * 10)] for i in range(500)]
    sample = DataSample(
        source_id="src-1",
        path="/data/invoices.csv",
        columns=["Invoice", "Amount"],
        rows=rows,
        total_rows=1234,
    )
    return _make_workpaper([], data_samples=[sample])


# ── helpers ───────────────────────────────────────────────────────────────────


def _section_ids(html: str) -> list[str]:
    return re.findall(r'<section id="([^"]+)"', html)


def _exceptions_block(html: str) -> str:
    """The substring from the Exceptions <section> to the next <section> (or end)."""
    start = html.index('<section id="exceptions"')
    rest = html[start + 1 :]
    nxt = rest.find("<section id=")
    return rest if nxt == -1 else rest[:nxt]


def _conclusion_block(html: str) -> str:
    start = html.index('<section id="conclusion"')
    rest = html[start:]
    return rest[: rest.index("</section>")]


# ── 1. sections present & in order ────────────────────────────────────────────


class TestRenderHtmlAppParity:
    def test_sections_present_and_in_order(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert _section_ids(html) == EXPECTED_SECTION_IDS

    def test_signoff_section_omitted(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert 'id="sign-off"' not in html

    def test_evaluation_section_removed(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert 'id="evaluation"' not in html
        assert 'href="#evaluation"' not in html

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

    # ── 3. results bar + tiles (records→passed→exceptions, no Failed) ─────────

    def test_results_tile_order_no_failed(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        labels = re.findall(r'tile-label">([^<]+)', html)
        assert labels == ["Records tested", "Passed", "Exceptions"]
        assert "Failed" not in labels

    def test_resultbar_has_no_fail_metric(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        bar = html[html.index('<div class="wp-resultbar">') :]
        bar = bar[: bar.index("</div>")]
        # Records-led, single finding metric — no "N fail" segment in the bar.
        assert "records" in bar
        assert "fail" not in bar
        assert "exc" in bar

    # ── 4. <details> collapse markers ─────────────────────────────────────────

    def test_all_details_default_closed(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert "<details>" in html
        assert "<details open" not in html

    # ── 5. key design tokens baked in ─────────────────────────────────────────

    def test_canonical_tokens_present(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        for token in ("#0b0d17", "#12141f", "#3b82f6", "#10b981", "#ef4444"):
            assert token in html
        assert "--accent-primary" in html

    def test_font_stacks_present(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert "JetBrains Mono" in html
        assert "Inter" in html

    # ── 6. single full-population statement (header only) ─────────────────────

    def test_single_full_population_statement(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        # The phrase appears once, in the document header — not per section/proc.
        assert html.count("no sampling applied") == 1
        assert "No sampling was applied" not in html

    # ── 7. interactive data table widget ──────────────────────────────────────

    def test_single_inline_init_script(self, workpaper_with_small_sample: Workpaper) -> None:
        html = render_html(workpaper_with_small_sample)
        # Exactly one <script> (the data-table widget) — the only permitted JS.
        assert html.lower().count("<script") == 1
        assert "data-datatable" in html
        # No external deps / no network.
        assert "jquery" not in html.lower()
        assert "cdn" not in html.lower()
        assert "http://" not in html
        assert "https://" not in html

    def test_data_table_renders_rows(self, workpaper_with_small_sample: Workpaper) -> None:
        html = render_html(workpaper_with_small_sample)
        assert 'class="dt-table"' in html
        assert "INV-001" in html
        assert "Invoice" in html  # column header (display name)
        assert '<input class="dt-search"' in html

    def test_data_table_cap_note_when_capped(self, workpaper_with_capped_sample: Workpaper) -> None:
        html = render_html(workpaper_with_capped_sample)
        assert "showing first 500 of 1234 rows" in html

    def test_data_table_no_cap_note_when_small(
        self, workpaper_with_small_sample: Workpaper
    ) -> None:
        html = render_html(workpaper_with_small_sample)
        assert "showing first" not in html
        assert "3 rows" in html

    def test_data_table_cells_escaped(self) -> None:
        sample = DataSample(
            source_id="src-1",
            path="/data/invoices.csv",
            columns=["Note"],
            rows=[["<script>alert(1)</script>"]],
            total_rows=1,
        )
        wp = _make_workpaper([], data_samples=[sample])
        html = render_html(wp)
        # The cell payload is escaped; the only real <script> is the widget init.
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    # ── 8. threshold conclusion (single source of truth) ──────────────────────

    def test_conclusion_implicit_zero_fail(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        concl = _conclusion_block(html)
        assert "zero exceptions tolerated" in concl
        assert "did not operate effectively" in concl
        # Verdict pill agrees.
        assert "Operated with deficiencies" in html

    def test_conclusion_pass_under_threshold(
        self, pass_under_threshold_workpaper: Workpaper
    ) -> None:
        html = render_html(pass_under_threshold_workpaper)
        concl = _conclusion_block(html)
        assert "at or below 5%" in concl
        assert "(2 / 100 records)" in concl
        assert "within threshold" in concl
        # Despite 2 exceptions, the verdict is effective (threshold absorbed them).
        assert "Operated effectively" in html
        assert "Operated with deficiencies" not in html

    def test_verdict_matches_conclusion_single_source(self, passing_workpaper: Workpaper) -> None:
        html = render_html(passing_workpaper)
        # Verdict appears in the sticky pill AND derives the conclusion outcome.
        assert "Operated effectively" in html
        concl = _conclusion_block(html)
        assert "operated effectively" in concl.lower()

    # ── 9. passing vs failing render ──────────────────────────────────────────

    def test_failing_render(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert "Operated with deficiencies" in html
        assert ">FAIL" in html
        assert "INV-001" in html
        assert "INV-002" in html
        assert "2 exc" in html

    def test_passing_render(self, passing_workpaper: Workpaper) -> None:
        html = render_html(passing_workpaper)
        assert "Operated effectively" in html
        assert "100.0%" in html
        assert "No exceptions" in html
        assert "0 exc" in html
        # No violation rows table inside the Exceptions section.
        assert "<table" not in _exceptions_block(html)

    # ── 10. guarantees still hold (regression guard) ──────────────────────────

    def test_self_contained_no_link_single_script(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert html.startswith("<!doctype html>")
        # Exactly one inline script (the data-table widget); no external assets.
        assert html.lower().count("<script") == 1
        assert 'rel="stylesheet"' not in html
        assert "<link" not in html

    def test_xss_escaped(self, failing_workpaper: Workpaper) -> None:
        html = render_html(failing_workpaper)
        assert "<b>" not in html
        assert "&lt;b&gt;" in html
