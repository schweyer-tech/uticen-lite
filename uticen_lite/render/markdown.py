"""Render a Workpaper to Markdown.

Section model mirrors the canonical app-ordered taxonomy used by the HTML
renderer (Results, Objective & scope, Control, Data sources, Procedures,
Exceptions, Conclusion). Markdown has no collapse, jump-nav, or interactive
data table, but the section names and order hold for parity.

Pure string-building; no template engine or external dependencies.
Pyodide-safe (stdlib only, no pandas/pydantic).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from uticen_lite.render.dates import format_display_date

if TYPE_CHECKING:
    from uticen_lite.model.run import SourceProvenance
    from uticen_lite.model.violation import Violation
    from uticen_lite.model.workpaper import DataSample, Determination, Workpaper

# Rows shown in the Markdown static preview table per data source.
_MD_PREVIEW_ROWS = 10

# Default "Generated" date display format and timezone (mm/dd/yyyy in EST).
_DEFAULT_DATE_FORMAT = "%m/%d/%Y"
_DEFAULT_TZ = "America/New_York"


def _md_cell(text: object) -> str:
    """Make a value safe to place inside a Markdown table cell."""
    s = str(text)
    return (
        s.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _dedup_provenance(wp: Workpaper) -> list[SourceProvenance]:
    """Provenance across all procedures, deduped by source_id (first wins)."""
    seen: set[str] = set()
    out: list[SourceProvenance] = []
    for proc in wp.procedures:
        for prov in proc.result.provenance:
            if prov.source_id in seen:
                continue
            seen.add(prov.source_id)
            out.append(prov)
    return out


def _all_violations(wp: Workpaper) -> list[Violation]:
    out: list[Violation] = []
    for proc in wp.procedures:
        out.extend(proc.result.violations)
    return out


def render_markdown(
    wp: Workpaper,
    *,
    generated_at: datetime | None = None,
    date_format: str = _DEFAULT_DATE_FORMAT,
    tz: str = _DEFAULT_TZ,
) -> str:
    """Return a Markdown string representing the full audit workpaper.

    Sections (canonical order): Results, Objective & scope, Control (with
    framework references), Data sources, Procedures, Exceptions, Conclusion.

    ``generated_at`` is the actual render-time clock (defaults to now in UTC) —
    distinct from the run's execution/as-of date, which surfaces per source as
    the Extract Date. ``date_format`` / ``tz`` control all displayed dates
    (default ``mm/dd/yyyy`` in US Eastern).
    """
    lines: list[str] = []
    records_tested = wp.records_tested
    exceptions = wp.exception_count
    total_passed = records_tested - exceptions
    pass_rate = round(total_passed / records_tested * 100, 2) if records_tested else 0.0
    determination = wp.determination
    verdict = determination.verdict

    gen_dt = generated_at if generated_at is not None else datetime.now(UTC)
    generated_display = format_display_date(gen_dt, date_format=date_format, tz=tz)

    sources = _dedup_provenance(wp)
    samples_by_id: dict[str, DataSample] = {s.source_id: s for s in wp.data_samples}
    violations = _all_violations(wp)
    nist_refs: list[str] = wp.framework_refs.get("nist", [])
    extra: dict[str, list[str]] = wp.framework_refs.get("extra", {})
    run_executed_at = wp.procedures[0].result.executed_at if wp.procedures else ""

    _render_header(lines, wp, generated_display)
    _render_results(lines, records_tested, total_passed, exceptions, pass_rate, verdict)
    _render_objective(lines, wp)
    _render_control(lines, wp, nist_refs, extra)
    _render_data_sources(
        lines, sources, samples_by_id, run_executed_at, date_format=date_format, tz=tz
    )
    _render_procedures(lines, wp)
    _render_exceptions(lines, violations)
    _render_conclusion(lines, determination)

    return "\n".join(lines)


def _render_header(lines: list[str], wp: Workpaper, generated_display: str) -> None:
    """── Header ──"""
    lines.append(f"# {wp.title}")
    lines.append("")
    lines.append(f"**Control ID:** {wp.control_id}")
    lines.append(f"**Generated:** {generated_display}")
    lines.append("")


def _render_results(
    lines: list[str],
    records_tested: int,
    total_passed: int,
    exceptions: int,
    pass_rate: float,
    verdict: str,
) -> None:
    """── Results (Records Tested · Passed · Exceptions; no Failed) ──"""
    lines.append("## Results")
    lines.append("")
    lines.append(
        f"`{records_tested} records · {total_passed} pass · {exceptions} exc` — **{verdict}**"
    )
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Records Tested | {records_tested} |")
    lines.append(f"| Passed | {total_passed} |")
    lines.append(f"| Exceptions | {exceptions} |")
    lines.append(f"| Pass Rate | {pass_rate}% |")
    lines.append("")


def _render_objective(lines: list[str], wp: Workpaper) -> None:
    """── Objective & Scope ──"""
    lines.append("## Objective & Scope")
    lines.append("")
    lines.append(wp.objective)
    lines.append("")


def _render_control(
    lines: list[str],
    wp: Workpaper,
    nist_refs: list[str],
    extra: dict[str, list[str]],
) -> None:
    """── Control (framework refs fold in here) ──"""
    lines.append("## Control")
    lines.append("")
    lines.append(f"**Control ID:** {wp.control_id}")
    lines.append(f"**Title:** {wp.title}")
    lines.append("")
    lines.append(wp.narrative)
    lines.append("")
    lines.append("### Framework References")
    lines.append("")
    if nist_refs:
        lines.append(f"**NIST 800-53:** {', '.join(nist_refs)}")
    for framework, refs in extra.items():
        if refs:
            lines.append(f"**{framework}:** {', '.join(refs)}")
    if not nist_refs and not any(extra.values()):
        lines.append("None")
    lines.append("")


def _render_data_sources(
    lines: list[str],
    sources: list[SourceProvenance],
    samples_by_id: dict[str, DataSample],
    run_executed_at: str,
    *,
    date_format: str,
    tz: str,
) -> None:
    """── Data Sources ──"""
    lines.append("## Data Sources")
    lines.append("")
    if sources:
        for prov in sources:
            sample = samples_by_id.get(prov.source_id)
            lines.append(f"- **{prov.path}** — {prov.row_count} rows")
            lines.append(f"  - SHA-256: `{prov.sha256}`")
            lines.append(f"  - Source: `{prov.source_id}`")
            raw_extract = sample.extract_date if sample is not None else None
            extract_display = format_display_date(
                raw_extract or run_executed_at, date_format=date_format, tz=tz
            )
            lines.append(f"  - **Extract Date:** {extract_display}")
            description = sample.description if sample is not None else None
            if description:
                lines.append(f"  - **Description:** {description}")
            ca_text = _completeness_accuracy_text(prov, sample)
            lines.append(f"  - **Completeness & Accuracy:** {ca_text}")
            lines.append("")
            if sample is not None and sample.columns:
                _append_preview_table(lines, sample)
    else:
        lines.append("No data sources recorded.")
    lines.append("")


def _render_procedures(lines: list[str], wp: Workpaper) -> None:
    """── Procedures ──"""
    lines.append("## Procedures")
    lines.append("")
    for i, proc in enumerate(wp.procedures, start=1):
        run = proc.result
        status = "PASS" if run.failed == 0 else "FAIL"
        # Heading: show code prefix when present (guard keeps N≤1 byte-identical when empty).
        if proc.code:
            lines.append(f"### {proc.code}: {proc.title} — {status}")
        else:
            lines.append(f"### P{i}: {proc.title} — {status}")
        lines.append("")
        # Assertion subtitle — suppressed when empty (byte-identical guard).
        if proc.assertion:
            lines.append(f"_Assertion: {proc.assertion}_")
            lines.append("")
        lines.append(proc.narrative)
        lines.append("")
        lines.append("```python")
        lines.append(proc.test_code)
        lines.append("```")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| Population | {_md_cell(run.population_size)} |")
        lines.append(f"| Passed | {_md_cell(run.passed)} |")
        lines.append(f"| Failed | {_md_cell(run.failed)} |")
        lines.append(f"| Pass Rate | {_md_cell(run.pass_rate)}% |")
        lines.append("")
        # per-procedure verdict — only for N>1 (N==1 is byte-identical to today)
        if len(wp.procedures) > 1:
            det = proc.determination
            threshold_text, result_text = det.conclusion_text()
            lines.append(f"**Procedure verdict: {det.verdict}** — {threshold_text} {result_text}")
            lines.append("")
        if run.violations:
            has_checks = any(v.details.get("checks") for v in run.violations)
            if has_checks:
                lines.append("| Item Key | Severity | Description | Failed checks |")
                lines.append("| --- | --- | --- | --- |")
            else:
                lines.append("| Item Key | Severity | Description |")
                lines.append("| --- | --- | --- |")
            for v in run.violations:
                key = _md_cell(v.item_key)
                sev = _md_cell(v.severity)
                desc = _md_cell(v.description)
                checks_raw: list[str] = v.details.get("checks") or []
                checks_cell = f" | {_md_cell(', '.join(checks_raw))}" if has_checks else ""
                lines.append(f"| {key} | {sev} | {desc}{checks_cell} |")
            lines.append("")


def _render_exceptions(lines: list[str], violations: list[Violation]) -> None:
    """── Exceptions ──"""
    lines.append("## Exceptions")
    lines.append("")
    if violations:
        lines.append("| E-Ref | Item Key | Severity | Description |")
        lines.append("| --- | --- | --- | --- |")
        for i, v in enumerate(violations, start=1):
            key = _md_cell(v.item_key)
            sev = _md_cell(v.severity)
            desc = _md_cell(v.description)
            lines.append(f"| E-{i} | {key} | {sev} | {desc} |")
    else:
        lines.append("No exceptions — control operated without deviations.")
    lines.append("")


def _render_conclusion(lines: list[str], determination: Determination) -> None:
    """── Conclusion (threshold determination) ──"""
    lines.append("## Conclusion")
    lines.append("")
    threshold_text, result_text = determination.conclusion_text()
    lines.append(threshold_text)
    lines.append("")
    lines.append(f"**{result_text}**")
    lines.append("")


def _completeness_accuracy_text(
    prov: SourceProvenance,
    sample: DataSample | None,
) -> str:
    """Return the Completeness & Accuracy assertion for a source.

    Uses the author-supplied ``completeness_accuracy`` when present; otherwise
    derives a sensible default from the tie-out (row count, file, sha256 prefix).
    """
    if sample is not None and sample.completeness_accuracy:
        return sample.completeness_accuracy
    short_sha = str(prov.sha256)[:8]
    return (
        f"All {prov.row_count} records were loaded from {prov.path} "
        f"(sha256 {short_sha}) and tested in full — row count ties to the "
        f"source extract; no sampling."
    )


def _append_preview_table(lines: list[str], sample: DataSample) -> None:
    """Append a small static preview table (first ~N rows) for a data source."""
    preview = sample.rows[:_MD_PREVIEW_ROWS]
    header = "| " + " | ".join(_md_cell(c) for c in sample.columns) + " |"
    divider = "| " + " | ".join("---" for _ in sample.columns) + " |"
    lines.append(header)
    lines.append(divider)
    for row in preview:
        lines.append("| " + " | ".join(_md_cell(c) for c in row) + " |")
    lines.append("")
    shown = len(preview)
    if sample.total_rows > shown:
        lines.append(f"_showing first {shown} of {sample.total_rows} rows_")
    else:
        lines.append(f"_{sample.total_rows} rows_")
    lines.append("")
