"""Render a Workpaper to Markdown.

Pure string-building; no template engine or external dependencies.
Pyodide-safe (stdlib only, no pandas/pydantic).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from controlflow_sdk.model.workpaper import Workpaper


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


def render_markdown(wp: Workpaper) -> str:
    """Return a Markdown string representing the full audit workpaper.

    Sections (in order):
    1. Title + metadata (objective, framework refs, generated_at)
    2. Per-procedure: narrative, fenced test-code block, results table,
       violations table, provenance block.
    """
    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append(f"# {wp.title}")
    lines.append("")
    lines.append(f"**Control ID:** {wp.control_id}")
    lines.append(f"**Generated:** {wp.generated_at}")
    lines.append("")

    # ── Objective ─────────────────────────────────────────────────────────────
    lines.append("## Objective")
    lines.append("")
    lines.append(wp.objective)
    lines.append("")

    # ── Narrative ─────────────────────────────────────────────────────────────
    lines.append("## Narrative")
    lines.append("")
    lines.append(wp.narrative)
    lines.append("")

    # ── Framework References ───────────────────────────────────────────────────
    lines.append("## Framework References")
    lines.append("")
    nist_refs: list[str] = wp.framework_refs.get("nist", [])
    if nist_refs:
        lines.append(f"**NIST 800-53:** {', '.join(nist_refs)}")
    extra: dict[str, list[str]] = wp.framework_refs.get("extra", {})
    for framework, refs in extra.items():
        if refs:
            lines.append(f"**{framework}:** {', '.join(refs)}")
    lines.append("")

    # ── Procedures ────────────────────────────────────────────────────────────
    for i, proc in enumerate(wp.procedures, start=1):
        run = proc.result
        lines.append(f"## Procedure {i}: {proc.title}")
        lines.append("")

        # Narrative
        lines.append("### Narrative")
        lines.append("")
        lines.append(proc.narrative)
        lines.append("")

        # Test code
        lines.append("### Test")
        lines.append("")
        lines.append("```python")
        lines.append(proc.test_code)
        lines.append("```")
        lines.append("")

        # Results table
        lines.append("### Results")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| Population | {_md_cell(run.population_size)} |")
        lines.append(f"| Passed | {_md_cell(run.passed)} |")
        lines.append(f"| Failed | {_md_cell(run.failed)} |")
        lines.append(f"| Pass Rate | {_md_cell(run.pass_rate)}% |")
        lines.append("")

        # Violations table
        if run.violations:
            lines.append("### Violations")
            lines.append("")
            lines.append("| Item Key | Severity | Description |")
            lines.append("| --- | --- | --- |")
            for v in run.violations:
                key = _md_cell(v.item_key)
                sev = _md_cell(v.severity)
                desc = _md_cell(v.description)
                lines.append(f"| {key} | {sev} | {desc} |")
            lines.append("")

        # Provenance block
        if run.provenance:
            lines.append("### Data Provenance")
            lines.append("")
            for prov in run.provenance:
                lines.append(f"- **Source:** `{prov.path}`")
                lines.append(f"  - SHA-256: `{prov.sha256}`")
                lines.append(f"  - Row count: {prov.row_count}")
            lines.append("")

    return "\n".join(lines)
