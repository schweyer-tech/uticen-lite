"""Render a Workpaper to Markdown.

Section model mirrors the canonical app-ordered taxonomy used by the HTML
renderer (Results, Objective & scope, Control, Data sources, Procedures,
Exceptions, Conclusion). Markdown has no collapse, jump-nav, or interactive
data table, but the section names and order hold for parity.

Pure string-building; no template engine or external dependencies.
Pyodide-safe (stdlib only, no pandas/pydantic).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from controlflow_sdk.model.run import SourceProvenance
    from controlflow_sdk.model.violation import Violation
    from controlflow_sdk.model.workpaper import DataSample, Workpaper

# Rows shown in the Markdown static preview table per data source.
_MD_PREVIEW_ROWS = 10


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


def render_markdown(wp: Workpaper) -> str:
    """Return a Markdown string representing the full audit workpaper.

    Sections (canonical order): Results, Objective & scope, Control (with
    framework references), Data sources, Procedures, Evaluation, Exceptions,
    Conclusion.
    """
    lines: list[str] = []
    records_tested = wp.records_tested
    exceptions = wp.exception_count
    total_passed = records_tested - exceptions
    pass_rate = round(total_passed / records_tested * 100, 2) if records_tested else 0.0
    determination = wp.determination
    verdict = determination.verdict

    sources = _dedup_provenance(wp)
    samples_by_id: dict[str, DataSample] = {s.source_id: s for s in wp.data_samples}
    violations = _all_violations(wp)
    nist_refs: list[str] = wp.framework_refs.get("nist", [])
    extra: dict[str, list[str]] = wp.framework_refs.get("extra", {})

    # ── Header ────────────────────────────────────────────────────────────────
    # Full-population methodology stated ONCE here, not per section/procedure.
    lines.append(f"# {wp.title}")
    lines.append("")
    lines.append(f"**Control ID:** {wp.control_id}")
    lines.append(f"**Generated:** {wp.generated_at}")
    lines.append("")
    lines.append("_Full-population test — every record evaluated; no sampling applied._")
    lines.append("")

    # ── Results (Records tested · Passed · Exceptions; no Failed) ──────────────
    lines.append("## Results")
    lines.append("")
    lines.append(
        f"`{records_tested} records · {total_passed} pass · {exceptions} exc` — **{verdict}**"
    )
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Records tested | {records_tested} |")
    lines.append(f"| Passed | {total_passed} |")
    lines.append(f"| Exceptions | {exceptions} |")
    lines.append(f"| Pass rate | {pass_rate}% |")
    lines.append("")

    # ── Objective & scope ─────────────────────────────────────────────────────
    lines.append("## Objective & scope")
    lines.append("")
    lines.append(wp.objective)
    lines.append("")

    # ── Control (framework refs fold in here) ─────────────────────────────────
    lines.append("## Control")
    lines.append("")
    lines.append(f"**Control ID:** {wp.control_id}")
    lines.append(f"**Title:** {wp.title}")
    lines.append("")
    lines.append(wp.narrative)
    lines.append("")
    lines.append("### Framework references")
    lines.append("")
    if nist_refs:
        lines.append(f"**NIST 800-53:** {', '.join(nist_refs)}")
    for framework, refs in extra.items():
        if refs:
            lines.append(f"**{framework}:** {', '.join(refs)}")
    if not nist_refs and not any(extra.values()):
        lines.append("None")
    lines.append("")

    # ── Data sources ──────────────────────────────────────────────────────────
    lines.append("## Data sources")
    lines.append("")
    if sources:
        for prov in sources:
            lines.append(f"- **{prov.path}** — {prov.row_count} rows")
            lines.append(f"  - SHA-256: `{prov.sha256}`")
            lines.append(f"  - Source: `{prov.source_id}`")
            lines.append("")
            sample = samples_by_id.get(prov.source_id)
            if sample is not None and sample.columns:
                _append_preview_table(lines, sample)
    else:
        lines.append("No data sources recorded.")
    lines.append("")

    # ── Procedures ────────────────────────────────────────────────────────────
    lines.append("## Procedures")
    lines.append("")
    for i, proc in enumerate(wp.procedures, start=1):
        run = proc.result
        status = "PASS" if run.failed == 0 else "FAIL"
        lines.append(f"### P{i}: {proc.title} — {status}")
        lines.append("")
        lines.append(f"`run {str(run.run_id)[:8]}… · {run.executed_at}`")
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
        if run.violations:
            lines.append("| Item Key | Severity | Description |")
            lines.append("| --- | --- | --- |")
            for v in run.violations:
                key = _md_cell(v.item_key)
                sev = _md_cell(v.severity)
                desc = _md_cell(v.description)
                lines.append(f"| {key} | {sev} | {desc} |")
            lines.append("")

    # ── Exceptions ────────────────────────────────────────────────────────────
    lines.append("## Exceptions")
    lines.append("")
    if violations:
        lines.append("| E-ref | Item Key | Severity | Description |")
        lines.append("| --- | --- | --- | --- |")
        for i, v in enumerate(violations, start=1):
            key = _md_cell(v.item_key)
            sev = _md_cell(v.severity)
            desc = _md_cell(v.description)
            lines.append(f"| E-{i} | {key} | {sev} | {desc} |")
    else:
        lines.append("No exceptions — control operated without deviations.")
    lines.append("")

    # ── Conclusion (threshold determination) ──────────────────────────────────
    lines.append("## Conclusion")
    lines.append("")
    threshold_text, result_text = determination.conclusion_text()
    lines.append(threshold_text)
    lines.append("")
    lines.append(f"**{result_text}**")
    lines.append("")

    return "\n".join(lines)


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
