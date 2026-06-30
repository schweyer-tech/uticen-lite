"""Render a Workpaper to a self-contained HTML document.

The output is **structurally equivalent and visually close** to Uticen's
in-app workpaper view (same section model/order, sticky results bar, jump-nav
sidebar, shared design tokens) — not a pixel-exact mirror of the React app.

Renderer guarantees (all preserved):

- Single self-contained document: starts with ``<!doctype html>``; **inline
  ``<style>`` only** — no ``<link rel="stylesheet">``, no external assets / CDN.
- **Exactly one inline ``<script>``** — a vanilla-JS data-table widget (no
  jQuery, no network, no ``eval``); every interpolated value is HTML-escaped.
  Collapse uses ``<details>/<summary>`` and the jump-nav uses anchor links.
- **All author/data-derived text** passes through ``html.escape`` (``_e``).
- Pure stdlib; Pyodide-safe (no template engine, pandas, or pydantic).
"""

from __future__ import annotations

import html as _html
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from uticen_lite.render.dates import format_display_date

if TYPE_CHECKING:
    from uticen_lite.model.run import SourceProvenance
    from uticen_lite.model.violation import Violation
    from uticen_lite.model.workpaper import DataSample, Determination, Workpaper

# Default "Generated" date display format and timezone (mm/dd/yyyy in EST).
_DEFAULT_DATE_FORMAT = "%m/%d/%Y"
_DEFAULT_TZ = "America/New_York"

# Default page length for the interactive data table (configurable constant).
_TABLE_PAGE_LENGTH = 10

# ---------------------------------------------------------------------------
# Canonical section model (app-ordered). The sidebar lists these 7 entries.
# Sign-off (app section 7) and Evaluation (round-2 revision) are omitted; the
# footer carries a one-line disclaimer instead of a sign-off placeholder.
# ---------------------------------------------------------------------------

# (anchor id, label). Anchor ids stay kebab-case; only the visible labels are
# Title-Cased. The sidebar does not number the sections.
_SECTIONS: list[tuple[str, str]] = [
    ("results", "Results"),
    ("objective-scope", "Objective & Scope"),
    ("control", "Control"),
    ("data-sources", "Data Sources"),
    ("procedures", "Procedures"),
    ("exceptions", "Exceptions"),
    ("conclusion", "Conclusion"),
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
.wp-sidebar nav a {
  display: flex; align-items: center; gap: 8px;
  padding: 5px 6px; border-radius: var(--radius-input);
  color: var(--text-secondary); font-size: 13px; line-height: 18px;
}
.wp-sidebar nav a:hover { background: var(--bg-surface-2); color: var(--text-primary); }
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
.wp-resultbar .rb-control {
  display: inline-flex; align-items: baseline; gap: 8px; min-width: 0;
}
.wp-resultbar .rb-control .rb-cid {
  font-family: var(--font-mono); font-size: 12px; color: var(--text-tertiary);
}
.wp-resultbar .rb-control .rb-cname {
  font-size: 13px; font-weight: 600; color: var(--text-primary);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.wp-resultbar .rb-sep-v {
  width: 1px; align-self: stretch; background: var(--border-default);
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
.src-desc, .src-ca { font-size: 12px; line-height: 18px; margin-top: 8px; }
.src-lbl {
  display: inline-block; font-weight: 600; color: var(--text-primary);
  margin-right: 4px;
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

.empty-state { color: var(--text-secondary); }

/* ── conclusion (threshold determination) ────────────────────────────────── */
.concl-result { font-weight: 600; }
.concl-result.ok { color: var(--status-success); }
.concl-result.bad { color: var(--status-critical); }

/* ── interactive data table (single inline-JS widget) ────────────────────── */
.dt-wrap { margin: 8px 0 4px; }
.dt-controls {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; margin-bottom: 6px;
}
.dt-search {
  background: var(--bg-input); border: 1px solid var(--border-default);
  border-radius: var(--radius-input); color: var(--text-primary);
  font-family: var(--font-sans); font-size: 12px; padding: 5px 10px; min-width: 180px;
}
.dt-search:focus { outline: none; border-color: var(--accent-primary); }
.dt-cap {
  color: var(--text-tertiary); font-size: 11px; font-family: var(--font-mono);
}
table.dt-table th { cursor: pointer; user-select: none; white-space: nowrap; }
table.dt-table th .dt-arrow { color: var(--text-tertiary); font-size: 9px; margin-left: 4px; }
table.dt-table td { font-family: var(--font-mono); font-size: 11px; }
.dt-foot {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; margin-top: 6px;
}
.dt-info { color: var(--text-secondary); font-size: 11px; }
.dt-pager { display: flex; gap: 4px; flex-wrap: wrap; }
.dt-pager button {
  background: var(--bg-surface-2); border: 1px solid var(--border-default);
  border-radius: var(--radius-input); color: var(--text-secondary);
  font-family: var(--font-mono); font-size: 11px; padding: 3px 8px; cursor: pointer;
}
.dt-pager button:hover:not(:disabled) {
  background: var(--bg-surface-3); color: var(--text-primary);
}
.dt-pager button[disabled] { opacity: 0.4; cursor: default; }
.dt-pager button.active {
  background: var(--accent-muted); border-color: var(--accent-primary);
  color: var(--accent-primary);
}

/* ── footer ──────────────────────────────────────────────────────────────── */
.wp-footer {
  margin-top: 32px; padding-top: 12px; border-top: 1px solid var(--border-default);
  color: var(--text-tertiary); font-size: 12px;
}
"""


# ---------------------------------------------------------------------------
# Inline data-table widget (the one permitted <script>).
#
# Vanilla JS, no jQuery / no network / no eval. It reads rows straight from the
# already-rendered (HTML-escaped) DOM, so NO data is interpolated into JS — the
# full table is present even with JS off (graceful degradation). It paginates,
# filters and sorts each table marked with `data-datatable` on DOMContentLoaded.
# Page length is baked from _TABLE_PAGE_LENGTH below.
# ---------------------------------------------------------------------------

_DATATABLE_JS = """
(function () {
  var PAGE = __PAGE_LENGTH__;
  function initOne(wrap) {
    var table = wrap.querySelector("table.dt-table");
    if (!table) return;
    var tbody = table.tBodies[0];
    if (!tbody) return;
    var allRows = Array.prototype.slice.call(tbody.rows);
    var search = wrap.querySelector(".dt-search");
    var info = wrap.querySelector(".dt-info");
    var pager = wrap.querySelector(".dt-pager");
    var headers = Array.prototype.slice.call(table.tHead.rows[0].cells);
    var page = 0;
    var sortCol = -1;
    var sortDir = 1;
    var filtered = allRows;

    function cellText(row, i) {
      var c = row.cells[i];
      return c ? (c.textContent || "").trim() : "";
    }
    function applyFilter() {
      var q = (search && search.value ? search.value : "").toLowerCase();
      if (!q) { filtered = allRows; return; }
      filtered = allRows.filter(function (row) {
        return (row.textContent || "").toLowerCase().indexOf(q) !== -1;
      });
    }
    function applySort() {
      if (sortCol < 0) return;
      filtered = filtered.slice().sort(function (a, b) {
        var x = cellText(a, sortCol), y = cellText(b, sortCol);
        var nx = parseFloat(x), ny = parseFloat(y);
        var bothNum = !isNaN(nx) && !isNaN(ny) && x !== "" && y !== "";
        if (bothNum) return (nx - ny) * sortDir;
        return x.localeCompare(y) * sortDir;
      });
    }
    function render() {
      applyFilter();
      applySort();
      var total = filtered.length;
      var pages = Math.max(1, Math.ceil(total / PAGE));
      if (page >= pages) page = pages - 1;
      if (page < 0) page = 0;
      var start = page * PAGE;
      var end = Math.min(start + PAGE, total);
      allRows.forEach(function (r) { r.style.display = "none"; });
      for (var i = start; i < end; i++) filtered[i].style.display = "";
      if (info) {
        info.textContent = total === 0
          ? "Showing 0 to 0 of 0 entries"
          : "Showing " + (start + 1) + " to " + end + " of " + total + " entries";
      }
      renderPager(pages);
    }
    function makeBtn(label, targetPage, disabled, active) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      if (disabled) b.disabled = true;
      if (active) b.className = "active";
      if (!disabled) b.addEventListener("click", function () { page = targetPage; render(); });
      return b;
    }
    function renderPager(pages) {
      if (!pager) return;
      pager.innerHTML = "";
      pager.appendChild(makeBtn("‹", page - 1, page === 0, false));
      var maxBtns = 7;
      var from = Math.max(0, page - 3);
      var to = Math.min(pages, from + maxBtns);
      from = Math.max(0, to - maxBtns);
      for (var p = from; p < to; p++) {
        pager.appendChild(makeBtn(String(p + 1), p, false, p === page));
      }
      pager.appendChild(makeBtn("›", page + 1, page >= pages - 1, false));
    }
    if (search) {
      search.addEventListener("input", function () { page = 0; render(); });
    }
    headers.forEach(function (th, i) {
      th.addEventListener("click", function () {
        if (sortCol === i) { sortDir = -sortDir; } else { sortCol = i; sortDir = 1; }
        headers.forEach(function (h) {
          var a = h.querySelector(".dt-arrow"); if (a) a.textContent = "";
        });
        var arrow = th.querySelector(".dt-arrow");
        if (arrow) arrow.textContent = sortDir === 1 ? "\\u25B2" : "\\u25BC";
        page = 0; render();
      });
    });
    render();
  }
  function initAll() {
    var wraps = document.querySelectorAll("[data-datatable]");
    Array.prototype.forEach.call(wraps, initOne);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();
"""

_DATATABLE_SCRIPT = (
    "<script>" + _DATATABLE_JS.replace("__PAGE_LENGTH__", str(_TABLE_PAGE_LENGTH)) + "</script>"
)


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
    """Aggregate Results across all procedures' run records.

    The verdict is derived from the workpaper's :class:`Determination` (the
    threshold model) so the Results-bar pill and the Conclusion line can never
    disagree.
    """

    def __init__(self, wp: Workpaper) -> None:
        self.records_tested = wp.records_tested
        self.exceptions = wp.exception_count
        self.total_passed = self.records_tested - self.exceptions
        self.determination: Determination = wp.determination

    @property
    def pass_rate(self) -> float:
        if self.records_tested == 0:
            return 0.0
        return round(self.total_passed / self.records_tested * 100, 2)

    @property
    def verdict(self) -> str:
        """Single source of truth (threshold determination)."""
        return self.determination.verdict

    @property
    def passed(self) -> bool:
        return self.determination.passed


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------


def render_html(
    wp: Workpaper,
    *,
    generated_at: datetime | None = None,
    date_format: str = _DEFAULT_DATE_FORMAT,
    tz: str = _DEFAULT_TZ,
) -> str:
    """Return a self-contained HTML document for *wp*.

    Parameters
    ----------
    wp:
        The assembled workpaper.
    generated_at:
        Actual generation time (defaults to ``datetime.now`` in UTC). This is the
        render-time clock — distinct from the run's execution/as-of date, which
        surfaces per source as the Extract Date.
    date_format:
        ``strftime`` pattern for all displayed dates (default ``mm/dd/yyyy``).
    tz:
        IANA timezone name for date display (default US Eastern). Falls back to
        UTC defensively if the tz database is unavailable.

    Guarantee: exactly one inline ``<script>`` (the data-table widget) and no
    ``<link>`` stylesheet; all author/data text is html-escaped. An ``<a href>``
    credit link in the footer is permitted (the no-link rule is about external
    stylesheets, not anchors).
    """
    parts: list[str] = []

    def emit(s: str = "") -> None:
        parts.append(s)

    gen_dt = generated_at if generated_at is not None else datetime.now(UTC)
    generated_display = format_display_date(gen_dt, date_format=date_format, tz=tz)

    agg = _Agg(wp)
    sources = _dedup_provenance(wp)
    violations = _all_violations(wp)
    nist_refs: list[str] = wp.framework_refs.get("nist", [])
    extra: dict[str, list[str]] = wp.framework_refs.get("extra", {})
    # The run's execution/as-of date — the default Extract Date for any source
    # that does not declare its own.
    run_executed_at = wp.procedures[0].result.executed_at if wp.procedures else ""

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

    # ── document header (anchored as the document top) ────────────────────────
    emit('<header class="wp-header" id="top">')
    emit(f"<h1>{_e(wp.title)}</h1>")
    emit(
        '<p class="wp-meta">Control '
        f'<span class="chip">{_e(wp.control_id)}</span> '
        f'&middot; Generated <span class="mono">{_e(generated_display)}</span></p>'
    )
    emit("</header>")

    # ── layout: sidebar + content ─────────────────────────────────────────────
    emit('<div class="wp-layout">')
    _emit_sidebar(emit, wp, sources, violations)
    emit('<main class="wp-content">')

    _emit_resultbar(emit, agg, wp)
    _emit_results(emit, agg)
    _emit_objective_scope(emit, wp)
    _emit_control(emit, wp, nist_refs, extra)
    _emit_data_sources(emit, sources, wp.data_samples, run_executed_at, date_format, tz)
    _emit_procedures(emit, wp)
    _emit_exceptions(emit, violations)
    _emit_conclusion(emit, agg)

    emit("</main>")
    emit("</div>")  # /wp-layout

    # ── footer ────────────────────────────────────────────────────────────────
    emit(
        '<footer class="wp-footer">Generated by uticen-lite &middot; '
        f"{_e(generated_display)} &middot; "
        'Created by <a href="https://schweyer.tech">schweyer.tech</a></footer>'
    )

    # ── data-table widget (single inline vanilla-JS script) ───────────────────
    emit(_DATATABLE_SCRIPT)

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
) -> None:
    # Count badges. Only the Exceptions badge may be critical (red); more
    # procedures / sources is never "bad", so those stay neutral.
    counts: dict[str, tuple[int, bool]] = {
        "data-sources": (len(sources), False),
        "procedures": (len(wp.procedures), False),
        "exceptions": (len(violations), len(violations) > 0),
    }
    # The sidebar lists only the section links (no title block, no numbering).
    # The first link jumps to the top of the document (#top) instead of the
    # Results section anchor.
    emit('<nav class="wp-sidebar">')
    emit("<nav>")
    for i, (anchor, label) in enumerate(_SECTIONS):
        if i == 0:
            href = "#top"
            nav_label = "↑ Jump to Top"
        else:
            href = f"#{anchor}"
            nav_label = label
        count_html = ""
        if anchor in counts:
            n, crit = counts[anchor]
            if n > 0:
                cls = "nav-count crit" if crit else "nav-count"
                count_html = f'<span class="{cls}">{n}</span>'
        emit(f'<a href="{href}"><span class="nav-label">{_e(nav_label)}</span>{count_html}</a>')
    emit("</nav>")
    emit("</nav>")


def _section_open(emit, anchor: str) -> None:
    """Open a <section> with its <h2> heading (Title-Cased label, no source tag)."""
    label = next(s[1] for s in _SECTIONS if s[0] == anchor)
    emit(f'<section id="{anchor}">')
    emit(f"<h2>{_e(label)}</h2>")


# ---------------------------------------------------------------------------
# Sticky results bar
# ---------------------------------------------------------------------------


def _emit_resultbar(emit, agg: _Agg, wp: Workpaper) -> None:
    # Records-led order, single finding metric (Exceptions). No separate "fail".
    # Leads with the control identity ("<id>: <name>") so the sticky bar always
    # names the control it summarises.
    pill_cls = "ok" if agg.passed else "bad"
    emit('<div class="wp-resultbar">')
    emit(
        '<span class="rb-control">'
        f'<span class="rb-cid mono">{_e(wp.control_id)}:</span>'
        f'<span class="rb-cname">{_e(wp.title)}</span>'
        "</span>"
    )
    emit('<span class="rb-sep-v" aria-hidden="true"></span>')
    emit('<span class="rb-label">Result</span>')
    emit(
        '<span class="rb-metrics">'
        f'<span class="mono">{agg.records_tested} records</span>'
        '<span class="sep"> &middot; </span>'
        f'<span class="pass">{agg.total_passed} pass</span>'
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
    # Tile order: Records tested · Passed · Exceptions. The "Failed" tile is
    # removed in the SDK (Failed == Exceptions for a static single run).
    _section_open(emit, "results")
    emit('<div class="tiles">')
    emit(
        '<div class="tile"><div class="tile-label">Records Tested</div>'
        f'<div class="tile-value">{agg.records_tested}</div></div>'
    )
    emit(
        '<div class="tile ok"><div class="tile-label">Passed</div>'
        f'<div class="tile-value">{agg.total_passed}</div></div>'
    )
    exc_cls = "tile warn" if agg.exceptions > 0 else "tile"
    emit(
        f'<div class="{exc_cls}"><div class="tile-label">Exceptions</div>'
        f'<div class="tile-value">{agg.exceptions}</div></div>'
    )
    emit("</div>")
    emit(f'<p class="muted">Pass Rate <span class="mono">{agg.pass_rate}%</span></p>')
    emit("</section>")


# ---------------------------------------------------------------------------
# 0 Objective & scope
# ---------------------------------------------------------------------------


def _emit_objective_scope(emit, wp: Workpaper) -> None:
    # The full-population methodology is stated once in the header, not here.
    _section_open(emit, "objective-scope")
    emit(f"<p>{_e(wp.objective)}</p>")
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

    emit("<h3>Framework References</h3>")
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


def _emit_data_sources(
    emit,
    sources: list[SourceProvenance],
    data_samples: list[DataSample],
    run_executed_at: str,
    date_format: str,
    tz: str,
) -> None:
    _section_open(emit, "data-sources")
    if not sources:
        emit('<p class="empty-state">No data sources recorded.</p>')
        emit("</section>")
        return
    samples_by_id: dict[str, DataSample] = {s.source_id: s for s in data_samples}
    for prov in sources:
        sample = samples_by_id.get(prov.source_id)
        emit("<details>")
        emit(
            "<summary>"
            '<span class="tri" aria-hidden="true">&#9656;</span>'
            f'<span class="mono">{_e(prov.path)}</span>'
            f'<span class="rowcount-badge">{_e(prov.row_count)} rows</span>'
            "</summary>"
        )
        emit('<div class="details-body">')
        # provenance chip (sha256 + row count + file location)
        emit('<div class="prov-chips">')
        emit(f'<span class="prov-chip">sha256 {_e(str(prov.sha256)[:8])}&hellip;</span>')
        emit(f'<span class="prov-chip">{_e(prov.row_count)} rows</span>')
        emit(f'<span class="prov-chip">source {_e(prov.source_id)}</span>')
        emit("</div>")
        emit(f'<div class="prov-path">{_e(prov.sha256)}</div>')
        emit(f'<div class="prov-path">{_e(prov.path)}</div>')
        # Extract Date — author-supplied as-of date, else the run's as-of date.
        raw_extract = sample.extract_date if sample is not None else None
        extract_display = format_display_date(
            raw_extract or run_executed_at, date_format=date_format, tz=tz
        )
        emit(
            f'<p class="src-ca"><span class="src-lbl">Extract Date</span> {_e(extract_display)}</p>'
        )
        # Description (optional) + Completeness & Accuracy assertion (default derived).
        description = sample.description if sample is not None else None
        if description:
            emit(
                '<p class="src-desc"><span class="src-lbl">Description</span> '
                f"{_e(description)}</p>"
            )
        ca_text = _completeness_accuracy_text(prov, sample)
        emit(
            '<p class="src-ca"><span class="src-lbl">Completeness &amp; Accuracy</span> '
            f"{_e(ca_text)}</p>"
        )
        # interactive data table of the source rows
        if sample is not None and sample.columns:
            _emit_data_table(emit, sample)
        emit("</div>")  # /details-body
        emit("</details>")
    emit("</section>")


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


def _emit_data_table(emit, sample: DataSample) -> None:
    """Render one source's rows as an interactive (vanilla-JS) DataTable.

    Degrades to a plain full table when JS is disabled: the full row set is in
    the DOM; the inline script paginates/filters/sorts it on load. All cell
    values are HTML-escaped.
    """
    shown = len(sample.rows)
    if sample.capped:
        cap_note = f"showing first {shown} of {sample.total_rows} rows"
    else:
        cap_note = f"{sample.total_rows} rows"

    emit('<div class="dt-wrap" data-datatable>')
    emit('<div class="dt-controls">')
    emit('<input class="dt-search" type="text" placeholder="Search…" aria-label="Search table">')
    emit(f'<span class="dt-cap">{_e(cap_note)}</span>')
    emit("</div>")
    emit('<table class="dt-table">')
    emit("<thead><tr>")
    for col in sample.columns:
        emit(f'<th scope="col">{_e(col)}<span class="dt-arrow"></span></th>')
    emit("</tr></thead>")
    emit("<tbody>")
    for row in sample.rows:
        emit("<tr>")
        for cell in row:
            emit(f"<td>{_e(cell)}</td>")
        emit("</tr>")
    emit("</tbody>")
    emit("</table>")
    emit('<div class="dt-foot"><span class="dt-info"></span><span class="dt-pager"></span></div>')
    emit("</div>")  # /dt-wrap


# ---------------------------------------------------------------------------
# 3 Procedures
# ---------------------------------------------------------------------------


def _emit_procedures(emit, wp: Workpaper) -> None:
    _section_open(emit, "procedures")
    multi = len(wp.procedures) > 1
    for i, proc in enumerate(wp.procedures, start=1):
        run = proc.result
        # For N>1: use the threshold-aware determination for the badge so that a
        # procedure with exceptions that still passes its threshold shows PASS.
        # For N==1: keep the historical behaviour (passed iff zero exceptions).
        if multi:
            det = proc.determination
            passed = det.passed
        else:
            passed = run.failed == 0
        badge = (
            '<span class="badge pass">PASS</span>'
            if passed
            else '<span class="badge fail">FAIL</span>'
        )
        # Heading: show code prefix when present (guard keeps N≤1 byte-identical when empty).
        if proc.code:
            emit(f"<h3>{_e(proc.code)} &middot; {_e(proc.title)} {badge}</h3>")
        else:
            emit(f"<h3>P{i}: {_e(proc.title)} {badge}</h3>")
        # Assertion subtitle — suppressed when empty (byte-identical guard).
        if proc.assertion:
            emit(f'<p class="assert">Assertion: {_e(proc.assertion)}</p>')
        emit(f"<p>{_e(proc.narrative)}</p>")

        # collapsible code block (the single header "Generated" date suffices —
        # no per-procedure run-id/date line)
        emit("<details>")
        emit('<summary><span class="tri" aria-hidden="true">&#9656;</span>Code That Ran</summary>')
        emit('<div class="details-body">')
        emit(f"<pre>{_e(proc.test_code)}</pre>")
        emit("</div>")
        emit("</details>")

        # results metric line (full-population is stated once in the header)
        emit(
            '<p class="muted">'
            f'Population <span class="mono">{_e(run.population_size)}</span> &middot; '
            f'Passed <span class="mono">{_e(run.passed)}</span> &middot; '
            f'Failed <span class="mono">{_e(run.failed)}</span> &middot; '
            f'Pass Rate <span class="mono">{_e(run.pass_rate)}%</span></p>'
        )

        # per-procedure verdict pill — only for N>1 (N==1 is byte-identical to today)
        if multi:
            pill_cls = "ok" if det.passed else "bad"
            threshold_text, result_text = det.conclusion_text()
            emit(
                f'<p><span class="pill {pill_cls}">{_e(det.verdict)}</span> '
                f'<span class="muted">{_e(threshold_text)} {_e(result_text)}</span></p>'
            )

        # per-procedure violations table (with "Failed check(s)" column when any v has checks)
        if run.violations:
            has_checks = any(v.details.get("checks") for v in run.violations)
            emit("<table>")
            if has_checks:
                emit(
                    "<thead><tr>"
                    "<th>Item Key</th><th>Severity</th><th>Description</th>"
                    "<th>Failed check(s)</th>"
                    "</tr></thead>"
                )
            else:
                emit(
                    "<thead><tr><th>Item Key</th><th>Severity</th><th>Description</th></tr></thead>"
                )
            emit("<tbody>")
            for v in run.violations:
                sev_cls = _severity_class(str(v.severity))
                checks_raw: list[str] = v.details.get("checks") or []
                checks_cell = f"<td>{_e(', '.join(checks_raw))}</td>" if has_checks else ""
                emit(
                    "<tr>"
                    f'<td class="mono">{_e(v.item_key)}</td>'
                    f'<td class="{_e(sev_cls)}">{_e(v.severity)}</td>'
                    f"<td>{_e(v.description)}</td>"
                    f"{checks_cell}"
                    "</tr>"
                )
            emit("</tbody></table>")
    emit("</section>")


# ---------------------------------------------------------------------------
# 4 Exceptions (collapsible per violation)
# ---------------------------------------------------------------------------


def _emit_exceptions(emit, violations: list[Violation]) -> None:
    _section_open(emit, "exceptions")
    if not violations:
        emit(
            '<p class="empty-state">No exceptions &mdash; control operated without deviations.</p>'
        )
        emit("</section>")
        return

    # per-violation collapsible disposition panel (data only) — no summary table
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
# 5 Conclusion (threshold determination — same source of truth as the pill)
# ---------------------------------------------------------------------------


def _emit_conclusion(emit, agg: _Agg) -> None:
    _section_open(emit, "conclusion")
    threshold_text, result_text = agg.determination.conclusion_text()
    result_cls = "concl-result ok" if agg.passed else "concl-result bad"
    emit(f"<p>{_e(threshold_text)}</p>")
    emit(f'<p class="{result_cls}">{_e(result_text)}</p>')
    emit("</section>")
