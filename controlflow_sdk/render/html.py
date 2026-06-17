"""Render a Workpaper to a self-contained HTML document.

The output is **structurally equivalent and visually close** to ControlFlow's
in-app workpaper view (same section model/order, sticky results bar, jump-nav
sidebar, shared design tokens) — not a pixel-exact mirror of the React app.

Renderer guarantees (all preserved):

- Single self-contained document: starts with ``<!doctype html>``; **inline
  ``<style>`` only** — no ``<link rel="stylesheet">``, no external assets.
- **No ``<script>`` anywhere.** Collapse uses ``<details>/<summary>`` and the
  jump-nav uses anchor links; scroll-spy active-state highlighting is omitted
  (it would need JS).
- **All author/data-derived text** passes through ``html.escape`` (``_e``).
- Pure stdlib; Pyodide-safe (no template engine, pandas, or pydantic).
"""

from __future__ import annotations

import html as _html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from controlflow_sdk.model.run import RunRecord, SourceProvenance
    from controlflow_sdk.model.violation import Violation
    from controlflow_sdk.model.workpaper import Workpaper

# ---------------------------------------------------------------------------
# Canonical section model (app-ordered). The sidebar lists these 8 entries.
# Sign-off (app section 7) is omitted entirely — a static placeholder would be
# misleading in an export; the footer carries a one-line disclaimer instead.
# ---------------------------------------------------------------------------

# (anchor id, sidebar index badge, label, source tag)
_SECTIONS: list[tuple[str, str, str, str]] = [
    ("results", "★", "Results", "read-only · run_record"),
    ("objective-scope", "0", "Objective & scope", "objective"),
    ("control", "1", "Control", "control · framework_refs"),
    ("data-sources", "2", "Data sources", "provenance"),
    ("procedures", "3", "Procedures", "procedures · run_record"),
    ("evaluation", "4", "Evaluation", "derived"),
    ("exceptions", "5", "Exceptions", "violations"),
    ("conclusion", "6", "Conclusion", "derived"),
]


# ---------------------------------------------------------------------------
# Inline stylesheet — STYLE.md design tokens baked as :root CSS variables.
# Self-contained: no @import, no Google-Fonts fetch; rely on the fallback stack.
# ---------------------------------------------------------------------------

_CSS = """
:root {
  color-scheme: dark;
  /* backgrounds */
  --bg-base:#0b0d17; --bg-surface-1:#12141f; --bg-surface-2:#1a1d2b;
  --bg-surface-3:#222638; --bg-input:#0f1119;
  /* text */
  --text-primary:#e8e9ed; --text-secondary:#8b8fa3; --text-tertiary:#5c6078;
  /* accent */
  --accent-primary:#3b82f6; --accent-muted:rgba(59,130,246,0.125);
  /* status */
  --status-success:#10b981; --status-success-muted:rgba(16,185,129,0.15);
  --status-warning:#f59e0b; --status-warning-muted:rgba(245,158,11,0.15);
  --status-critical:#ef4444; --status-critical-muted:rgba(239,68,68,0.15);
  --status-info:#6366f1; --status-info-muted:rgba(99,102,241,0.15);
  /* borders + radius */
  --border-default:#1e2235; --radius-card:8px; --radius-input:6px; --radius-badge:9999px;
  --font-sans: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --font-mono: "JetBrains Mono", "Fira Code", "SF Mono", monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--font-sans);
  background: var(--bg-base);
  color: var(--text-primary);
  font-size: 14px; line-height: 22px;
  padding: 24px;
}
a { color: inherit; text-decoration: none; }

/* ── document header ─────────────────────────────────────────────────────── */
.wp-header { margin-bottom: 16px; }
.wp-header h1 {
  font-size: 24px; line-height: 32px; font-weight: 600; color: var(--text-primary);
  margin-bottom: 8px;
}
.wp-header .wp-meta { color: var(--text-secondary); font-size: 12px; line-height: 16px; }
.wp-header .wp-meta .mono { color: var(--text-primary); }
.chip {
  display: inline-block; background: var(--bg-surface-2); color: var(--text-primary);
  border: 1px solid var(--border-default); border-radius: var(--radius-badge);
  padding: 1px 8px; font-family: var(--font-mono); font-size: 11px;
}

/* ── layout: sidebar + content ───────────────────────────────────────────── */
.wp-layout { display: flex; align-items: flex-start; gap: 24px; }
.wp-sidebar {
  flex: 0 0 192px; width: 192px;
  position: sticky; top: 0; align-self: flex-start;
  background: var(--bg-surface-1); border-right: 1px solid var(--border-default);
  padding: 12px; border-radius: var(--radius-card);
}
.wp-sidebar .wp-sidebar-title {
  color: var(--text-primary); font-weight: 600; font-size: 13px; line-height: 18px;
  margin-bottom: 2px;
}
.wp-sidebar .wp-sidebar-sub {
  color: var(--text-secondary); font-family: var(--font-mono); font-size: 12px;
  margin-bottom: 12px; word-break: break-all;
}
.wp-sidebar nav a {
  display: flex; align-items: center; gap: 8px;
  padding: 5px 6px; border-radius: var(--radius-input);
  color: var(--text-secondary); font-size: 13px; line-height: 18px;
}
.wp-sidebar nav a:hover { background: var(--bg-surface-2); color: var(--text-primary); }
.wp-sidebar nav a .nav-idx {
  flex: 0 0 auto; width: 18px; text-align: center;
  font-family: var(--font-mono); font-size: 11px; color: var(--text-tertiary);
}
.wp-sidebar nav a .nav-label { flex: 1 1 auto; }
.wp-sidebar nav a .nav-count {
  flex: 0 0 auto; font-family: var(--font-mono); font-size: 11px;
  background: var(--bg-surface-2); color: var(--text-secondary);
  border-radius: var(--radius-badge); padding: 0 7px;
}
.wp-sidebar nav a .nav-count.crit {
  background: var(--status-critical-muted); color: var(--status-critical);
}
.wp-content { flex: 1 1 auto; min-width: 0; }

/* ── sticky results bar ──────────────────────────────────────────────────── */
.wp-resultbar {
  position: sticky; top: 0; z-index: 5;
  display: flex; align-items: center; gap: 12px;
  background: var(--bg-surface-1); border: 1px solid var(--border-default);
  border-radius: var(--radius-card); padding: 10px 14px; margin-bottom: 20px;
}
.wp-resultbar .rb-label {
  font-size: 11px; letter-spacing: 0.08em; color: var(--text-tertiary);
  text-transform: uppercase;
}
.wp-resultbar .rb-metrics { font-family: var(--font-mono); font-size: 13px; }
.wp-resultbar .rb-metrics .pass { color: var(--status-success); }
.wp-resultbar .rb-metrics .fail { color: var(--status-critical); }
.wp-resultbar .rb-metrics .exc { color: var(--status-warning); }
.wp-resultbar .rb-metrics .sep { color: var(--text-tertiary); }
.wp-resultbar .rb-verdict { margin-left: auto; }

/* ── sections ────────────────────────────────────────────────────────────── */
section { margin-bottom: 28px; }
section h2 {
  display: flex; align-items: baseline; gap: 10px;
  font-size: 18px; line-height: 26px; font-weight: 600; color: var(--text-primary);
  border-bottom: 1px solid var(--border-default); padding-bottom: 6px; margin-bottom: 12px;
}
section h2 .sec-tag {
  font-family: var(--font-mono); font-size: 11px; color: var(--text-tertiary);
  font-weight: 400;
}
:target > h2 { color: var(--accent-primary); }
h3 { font-size: 15px; line-height: 22px; color: var(--text-primary); margin: 14px 0 6px; }
p { margin: 6px 0; color: var(--text-secondary); }

/* ── metric tiles (Results) ──────────────────────────────────────────────── */
.tiles { display: flex; flex-wrap: wrap; gap: 12px; margin: 4px 0 12px; }
.tile {
  flex: 1 1 140px; background: var(--bg-surface-1);
  border: 1px solid var(--border-default); border-radius: var(--radius-card);
  padding: 12px 14px;
}
.tile .tile-label {
  font-size: 12px; line-height: 16px; color: var(--text-secondary);
  text-transform: uppercase; letter-spacing: 0.04em;
}
.tile .tile-value {
  font-family: var(--font-mono); font-size: 32px; line-height: 40px; font-weight: 600;
  color: var(--text-primary);
}
.tile.ok .tile-value { color: var(--status-success); }
.tile.bad .tile-value { color: var(--status-critical); }
.tile.warn .tile-value { color: var(--status-warning); }

/* ── badges / pills ──────────────────────────────────────────────────────── */
.badge {
  display: inline-block; border-radius: var(--radius-badge);
  padding: 2px 10px; font-size: 12px; font-weight: 600;
}
.badge.pass { background: var(--status-success-muted); color: var(--status-success); }
.badge.fail { background: var(--status-critical-muted); color: var(--status-critical); }
.pill {
  display: inline-block; border-radius: var(--radius-badge);
  padding: 3px 12px; font-size: 13px; font-weight: 600;
}
.pill.ok { background: var(--status-success-muted); color: var(--status-success); }
.pill.bad { background: var(--status-critical-muted); color: var(--status-critical); }

/* ── ref tags (framework) ────────────────────────────────────────────────── */
.ref-tag {
  display: inline-block; background: var(--accent-muted); color: var(--accent-primary);
  border-radius: var(--radius-input); padding: 1px 8px; font-family: var(--font-mono);
  font-size: 11px; margin: 2px;
}

/* ── tables (12px density) ───────────────────────────────────────────────── */
table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 12px; }
th {
  text-align: left; padding: 6px 12px; background: var(--bg-surface-2);
  color: var(--text-secondary); font-weight: 600;
  border-bottom: 1px solid var(--border-default);
}
td {
  padding: 6px 12px; border-bottom: 1px solid var(--border-default); color: var(--text-primary);
}
tr:last-child td { border-bottom: none; }
.kv td:first-child { color: var(--text-secondary); width: 160px; }

/* ── mono text ───────────────────────────────────────────────────────────── */
.mono { font-family: var(--font-mono); font-size: 11px; color: var(--text-primary); }
.muted { color: var(--text-secondary); }

/* ── severity ────────────────────────────────────────────────────────────── */
.sev-critical { color: var(--status-critical); font-weight: 600; }
.sev-high     { color: var(--status-critical); font-weight: 600; }
.sev-medium   { color: var(--status-warning); font-weight: 600; }
.sev-low      { color: var(--status-success); font-weight: 600; }

/* ── details / summary (script-free collapse) ────────────────────────────── */
details {
  background: var(--bg-surface-1); border: 1px solid var(--border-default);
  border-radius: var(--radius-card); margin: 8px 0;
}
details > summary {
  cursor: pointer; list-style: none; padding: 8px 12px;
  display: flex; align-items: center; gap: 10px;
  color: var(--text-primary); font-size: 13px;
}
details > summary::-webkit-details-marker { display: none; }
details > summary:hover { background: var(--bg-surface-2); }
details > summary .tri { color: var(--text-tertiary); font-size: 11px; }
details > .details-body { padding: 0 12px 12px; }

/* ── provenance chips ────────────────────────────────────────────────────── */
.prov-chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 6px 0; }
.prov-chip {
  display: inline-block; background: var(--bg-surface-2);
  border: 1px solid var(--border-default); border-radius: var(--radius-badge);
  padding: 2px 10px; font-family: var(--font-mono); font-size: 11px;
  color: var(--text-secondary);
}
.prov-path {
  font-family: var(--font-mono); font-size: 11px; color: var(--text-primary);
  word-break: break-all; margin-top: 4px;
}
.rowcount-badge {
  background: var(--accent-muted); color: var(--accent-primary);
  border-radius: var(--radius-badge); padding: 0 8px; font-family: var(--font-mono);
  font-size: 11px;
}

/* ── code blocks ─────────────────────────────────────────────────────────── */
pre {
  background: var(--bg-base); border: 1px solid var(--border-default);
  border-radius: var(--radius-input); padding: 12px;
  font-family: var(--font-mono); font-size: 11px; color: #a5b4fc;
  overflow-x: auto; white-space: pre-wrap; word-break: break-all; margin: 6px 0;
}

/* ── per-procedure integrity line ────────────────────────────────────────── */
.integrity { font-family: var(--font-mono); font-size: 11px; color: var(--status-success); }
.fullpop { font-weight: 600; color: var(--text-primary); margin: 8px 0; }
.empty-state { color: var(--text-secondary); }

/* ── footer ──────────────────────────────────────────────────────────────── */
.wp-footer {
  margin-top: 32px; padding-top: 12px; border-top: 1px solid var(--border-default);
  color: var(--text-tertiary); font-size: 12px;
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _e(text: object) -> str:
    """html.escape any value (converts to str first)."""
    return _html.escape(str(text))


def _severity_class(severity: str) -> str:
    key = severity.lower()
    mapping = {
        "critical": "sev-critical",
        "high": "sev-high",
        "medium": "sev-medium",
        "low": "sev-low",
    }
    return mapping.get(key, "")


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
    """Flat list of every violation across procedures (Exceptions section)."""
    out: list[Violation] = []
    for proc in wp.procedures:
        out.extend(proc.result.violations)
    return out


class _Agg:
    """Aggregate Results across all procedures' run records."""

    def __init__(self, wp: Workpaper) -> None:
        runs: list[RunRecord] = [p.result for p in wp.procedures]
        self.records_tested = sum(r.population_size for r in runs)
        self.total_failed = sum(r.failed for r in runs)
        self.total_passed = self.records_tested - self.total_failed
        self.exceptions = sum(len(r.violations) for r in runs)
        self.failed_procedures = sum(1 for r in runs if r.failed > 0)

    @property
    def pass_rate(self) -> float:
        if self.records_tested == 0:
            return 0.0
        return round(self.total_passed / self.records_tested * 100, 2)

    @property
    def verdict(self) -> str:
        """Single source of truth, shared by the sticky pill and conclusion line."""
        return "Operated effectively" if self.total_failed == 0 else "Operated with deficiencies"


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------


def render_html(wp: Workpaper) -> str:
    """Return a self-contained HTML document for *wp*.

    Guarantee: no ``<script`` tags; all author/data text is html-escaped.
    """
    parts: list[str] = []

    def emit(s: str = "") -> None:
        parts.append(s)

    agg = _Agg(wp)
    sources = _dedup_provenance(wp)
    violations = _all_violations(wp)
    nist_refs: list[str] = wp.framework_refs.get("nist", [])
    extra: dict[str, list[str]] = wp.framework_refs.get("extra", {})

    # ── head ──────────────────────────────────────────────────────────────────
    emit("<!doctype html>")
    emit('<html lang="en">')
    emit("<head>")
    emit('<meta charset="utf-8">')
    emit('<meta name="viewport" content="width=device-width, initial-scale=1">')
    emit(f"<title>{_e(wp.title)} — Audit Workpaper</title>")
    emit(f"<style>{_CSS}</style>")
    emit("</head>")
    emit("<body>")

    # ── document header ───────────────────────────────────────────────────────
    emit('<header class="wp-header">')
    emit(f"<h1>{_e(wp.title)}</h1>")
    emit(
        '<p class="wp-meta">Control '
        f'<span class="chip">{_e(wp.control_id)}</span> '
        f'&middot; Generated <span class="mono">{_e(wp.generated_at)}</span></p>'
    )
    emit("</header>")

    # ── layout: sidebar + content ─────────────────────────────────────────────
    emit('<div class="wp-layout">')
    _emit_sidebar(emit, wp, sources, violations, agg)
    emit('<main class="wp-content">')

    _emit_resultbar(emit, agg)
    _emit_results(emit, agg)
    _emit_objective_scope(emit, wp)
    _emit_control(emit, wp, nist_refs, extra)
    _emit_data_sources(emit, sources)
    _emit_procedures(emit, wp)
    _emit_evaluation(emit, agg)
    _emit_exceptions(emit, violations)
    _emit_conclusion(emit, agg)

    emit("</main>")
    emit("</div>")  # /wp-layout

    # ── footer disclaimer ─────────────────────────────────────────────────────
    emit(
        '<footer class="wp-footer">Generated by controlflow-sdk &middot; '
        f"{_e(wp.generated_at)} &middot; not a finalized workpaper</footer>"
    )

    emit("</body>")
    emit("</html>")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sidebar (script-free anchor jump-nav)
# ---------------------------------------------------------------------------


def _emit_sidebar(
    emit,
    wp: Workpaper,
    sources: list[SourceProvenance],
    violations: list[Violation],
    agg: _Agg,
) -> None:
    counts: dict[str, tuple[int, bool]] = {
        "data-sources": (len(sources), False),
        "procedures": (len(wp.procedures), agg.failed_procedures > 0),
        "exceptions": (len(violations), len(violations) > 0),
    }
    emit('<nav class="wp-sidebar">')
    emit(f'<div class="wp-sidebar-title">{_e(wp.title)}</div>')
    emit(f'<div class="wp-sidebar-sub">control {_e(wp.control_id)}</div>')
    emit("<nav>")
    for anchor, idx, label, _tag in _SECTIONS:
        count_html = ""
        if anchor in counts:
            n, crit = counts[anchor]
            if n > 0:
                cls = "nav-count crit" if crit else "nav-count"
                count_html = f'<span class="{cls}">{n}</span>'
        emit(
            f'<a href="#{anchor}">'
            f'<span class="nav-idx">{_e(idx)}</span>'
            f'<span class="nav-label">{_e(label)}</span>'
            f"{count_html}</a>"
        )
    emit("</nav>")
    emit("</nav>")


def _section_open(emit, anchor: str) -> None:
    """Open a <section> with its <h2> heading + source tag."""
    label = next(s[2] for s in _SECTIONS if s[0] == anchor)
    tag = next(s[3] for s in _SECTIONS if s[0] == anchor)
    emit(f'<section id="{anchor}">')
    emit(f'<h2>{_e(label)}<span class="sec-tag">{_e(tag)}</span></h2>')


# ---------------------------------------------------------------------------
# Sticky results bar
# ---------------------------------------------------------------------------


def _emit_resultbar(emit, agg: _Agg) -> None:
    pill_cls = "ok" if agg.total_failed == 0 else "bad"
    emit('<div class="wp-resultbar">')
    emit('<span class="rb-label">Result</span>')
    emit(
        '<span class="rb-metrics">'
        f'<span class="pass">{agg.total_passed} pass</span>'
        '<span class="sep"> &middot; </span>'
        f'<span class="fail">{agg.total_failed} fail</span>'
        '<span class="sep"> &middot; </span>'
        f'<span class="exc">{agg.exceptions} exc</span>'
        "</span>"
    )
    emit(f'<span class="rb-verdict"><span class="pill {pill_cls}">{_e(agg.verdict)}</span></span>')
    emit("</div>")


# ---------------------------------------------------------------------------
# ★ Results
# ---------------------------------------------------------------------------


def _emit_results(emit, agg: _Agg) -> None:
    _section_open(emit, "results")
    emit('<div class="tiles">')
    emit(
        '<div class="tile ok"><div class="tile-label">Passed</div>'
        f'<div class="tile-value">{agg.total_passed}</div></div>'
    )
    failed_cls = "tile bad" if agg.total_failed > 0 else "tile"
    emit(
        f'<div class="{failed_cls}"><div class="tile-label">Failed</div>'
        f'<div class="tile-value">{agg.total_failed}</div></div>'
    )
    emit(
        '<div class="tile"><div class="tile-label">Records tested</div>'
        f'<div class="tile-value">{agg.records_tested}</div></div>'
    )
    exc_cls = "tile warn" if agg.exceptions > 0 else "tile"
    emit(
        f'<div class="{exc_cls}"><div class="tile-label">Exceptions</div>'
        f'<div class="tile-value">{agg.exceptions}</div></div>'
    )
    emit("</div>")
    emit(f'<p class="muted">Pass rate <span class="mono">{agg.pass_rate}%</span></p>')
    emit("</section>")


# ---------------------------------------------------------------------------
# 0 Objective & scope
# ---------------------------------------------------------------------------


def _emit_objective_scope(emit, wp: Workpaper) -> None:
    _section_open(emit, "objective-scope")
    emit(f"<p>{_e(wp.objective)}</p>")
    emit('<p class="fullpop">Full population &mdash; no sampling.</p>')
    emit("</section>")


# ---------------------------------------------------------------------------
# 1 Control (framework refs fold in here)
# ---------------------------------------------------------------------------


def _emit_control(
    emit,
    wp: Workpaper,
    nist_refs: list[str],
    extra: dict[str, list[str]],
) -> None:
    _section_open(emit, "control")
    emit('<table class="kv"><tbody>')
    emit(f'<tr><td>Control ID</td><td class="mono">{_e(wp.control_id)}</td></tr>')
    emit(f"<tr><td>Title</td><td>{_e(wp.title)}</td></tr>")
    emit("</tbody></table>")
    emit(f"<p>{_e(wp.narrative)}</p>")

    emit("<h3>Framework references</h3>")
    if nist_refs or any(extra.values()):
        emit("<p>")
        for ref in nist_refs:
            emit(f'<span class="ref-tag">{_e(ref)}</span>')
        for framework, refs in extra.items():
            for ref in refs:
                emit(f'<span class="ref-tag">{_e(framework)}: {_e(ref)}</span>')
        emit("</p>")
    else:
        emit('<p class="muted">None</p>')
    emit("</section>")


# ---------------------------------------------------------------------------
# 2 Data sources (collapsible per source)
# ---------------------------------------------------------------------------


def _emit_data_sources(emit, sources: list[SourceProvenance]) -> None:
    _section_open(emit, "data-sources")
    if not sources:
        emit('<p class="empty-state">No data sources recorded.</p>')
        emit("</section>")
        return
    for prov in sources:
        emit("<details>")
        emit(
            "<summary>"
            '<span class="tri" aria-hidden="true">&#9656;</span>'
            f'<span class="mono">{_e(prov.path)}</span>'
            f'<span class="rowcount-badge">{_e(prov.row_count)} rows</span>'
            "</summary>"
        )
        emit('<div class="details-body">')
        emit('<div class="prov-chips">')
        emit(f'<span class="prov-chip">sha256 {_e(str(prov.sha256)[:8])}&hellip;</span>')
        emit(f'<span class="prov-chip">{_e(prov.row_count)} rows</span>')
        emit(f'<span class="prov-chip">source {_e(prov.source_id)}</span>')
        emit("</div>")
        emit(f'<div class="prov-path">{_e(prov.sha256)}</div>')
        emit(f'<div class="prov-path">{_e(prov.path)}</div>')
        emit("</div>")  # /details-body
        emit("</details>")
    emit("</section>")


# ---------------------------------------------------------------------------
# 3 Procedures
# ---------------------------------------------------------------------------


def _emit_procedures(emit, wp: Workpaper) -> None:
    _section_open(emit, "procedures")
    for i, proc in enumerate(wp.procedures, start=1):
        run = proc.result
        passed = run.failed == 0
        badge = (
            '<span class="badge pass">PASS</span>'
            if passed
            else '<span class="badge fail">FAIL</span>'
        )
        emit(f"<h3>P{i}: {_e(proc.title)} {badge}</h3>")
        emit(
            f'<div class="integrity">run {_e(str(run.run_id)[:8])}&hellip; '
            f"&middot; {_e(run.executed_at)}</div>"
        )
        emit(f"<p>{_e(proc.narrative)}</p>")

        # collapsible code block
        emit("<details>")
        emit(
            "<summary>"
            '<span class="tri" aria-hidden="true">&#9656;</span>'
            "code that ran"
            f' <span class="mono">run {_e(str(run.run_id)[:8])}&hellip;</span>'
            "</summary>"
        )
        emit('<div class="details-body">')
        emit(f"<pre>{_e(proc.test_code)}</pre>")
        emit("</div>")
        emit("</details>")

        # results metric line
        emit(
            '<p class="muted">'
            f'Population <span class="mono">{_e(run.population_size)}</span> &middot; '
            f'Passed <span class="mono">{_e(run.passed)}</span> &middot; '
            f'Failed <span class="mono">{_e(run.failed)}</span> &middot; '
            f'Pass rate <span class="mono">{_e(run.pass_rate)}%</span></p>'
        )
        emit(
            f'<p class="fullpop">Full population tested: {_e(run.population_size)} record(s). '
            "No sampling was applied.</p>"
        )

        # per-procedure violations table
        if run.violations:
            emit("<table>")
            emit("<thead><tr><th>Item key</th><th>Severity</th><th>Description</th></tr></thead>")
            emit("<tbody>")
            for v in run.violations:
                sev_cls = _severity_class(str(v.severity))
                emit(
                    "<tr>"
                    f'<td class="mono">{_e(v.item_key)}</td>'
                    f'<td class="{_e(sev_cls)}">{_e(v.severity)}</td>'
                    f"<td>{_e(v.description)}</td>"
                    "</tr>"
                )
            emit("</tbody></table>")
    emit("</section>")


# ---------------------------------------------------------------------------
# 4 Evaluation (derived, read-only)
# ---------------------------------------------------------------------------


def _emit_evaluation(emit, agg: _Agg) -> None:
    _section_open(emit, "evaluation")
    emit(
        "<p>Failed procedures: "
        f'<span class="mono">{agg.failed_procedures}</span> &middot; '
        f'Open exceptions: <span class="mono">{agg.exceptions}</span></p>'
    )
    emit("</section>")


# ---------------------------------------------------------------------------
# 5 Exceptions (collapsible per violation)
# ---------------------------------------------------------------------------


def _emit_exceptions(emit, violations: list[Violation]) -> None:
    _section_open(emit, "exceptions")
    if not violations:
        emit(
            '<p class="empty-state">No exceptions &mdash; control operated without deviations.</p>'
        )
        emit("</section>")
        return

    # summary table
    emit("<table>")
    emit("<thead><tr><th>E-ref</th><th>Item key</th><th>Severity</th></tr></thead>")
    emit("<tbody>")
    for i, v in enumerate(violations, start=1):
        sev_cls = _severity_class(str(v.severity))
        emit(
            "<tr>"
            f'<td class="mono">E-{i}</td>'
            f'<td class="mono">{_e(v.item_key)}</td>'
            f'<td class="{_e(sev_cls)}">{_e(v.severity)}</td>'
            "</tr>"
        )
    emit("</tbody></table>")

    # per-violation collapsible disposition panel (data only)
    for i, v in enumerate(violations, start=1):
        sev_cls = _severity_class(str(v.severity))
        emit("<details>")
        emit(
            "<summary>"
            '<span class="tri" aria-hidden="true">&#9656;</span>'
            f'E-{i} &middot; <span class="mono">{_e(v.item_key)}</span>'
            f'<span class="{_e(sev_cls)}">{_e(v.severity)}</span>'
            "</summary>"
        )
        emit('<div class="details-body">')
        emit(f"<p>{_e(v.description)}</p>")
        if v.details:
            emit("<table><tbody>")
            for k, val in v.details.items():
                emit(f'<tr><td>{_e(k)}</td><td class="mono">{_e(val)}</td></tr>')
            emit("</tbody></table>")
        emit("</div>")
        emit("</details>")
    emit("</section>")


# ---------------------------------------------------------------------------
# 6 Conclusion (derived verdict — same source of truth as the sticky pill)
# ---------------------------------------------------------------------------


def _emit_conclusion(emit, agg: _Agg) -> None:
    _section_open(emit, "conclusion")
    if agg.total_failed == 0:
        statement = "Operated effectively. Full population tested with no exceptions."
    else:
        statement = (
            f"Operated with deficiencies. {agg.total_failed} exception(s) "
            f"across {agg.records_tested} record(s) tested."
        )
    emit(f"<p>{_e(statement)}</p>")
    emit("</section>")
