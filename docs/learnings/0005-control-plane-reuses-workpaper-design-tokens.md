---
id: 0005
date: 2026-06-19
area: frontend
tags: [plane, web, css, design-tokens, workpaper, theming]
status: active
supersedes: null
superseded_by: null
---

# The control-plane web UI reuses the workpaper renderer's design tokens — keep the two palettes in sync, and drive every color through a token so theming stays a one-place override

## Context

The control plane (`uticen_lite/plane/`) and the workpaper renderer
(`uticen_lite/render/html.py`) are two separate HTML surfaces, but they are the *same product*:
the app is where a consultant authors a control, and the workpaper is the document that same run
produces. They were restyled (PR #16) to share one design system so the authoring surface feels like
the document it exports — the same dark navy palette, Inter + JetBrains Mono, 8px-radius cards,
green/amber/red status colors, pill badges, and metric tiles.

The catch: the two surfaces **do not share a stylesheet.** The workpaper is a single self-contained
HTML file with its CSS inlined in `render/html.py` (it has to be — it travels on its own, offline, no
external assets). The app's CSS lives in `uticen_lite/plane/static/app.css`. So the shared look is
a **convention, not an import** — the token values are duplicated by hand in both files, and nothing
mechanically keeps them aligned. A second consequence: a light/dark theme toggle was cheap to add to
the app *only because every color was already a CSS custom property* — light mode is one
`[data-theme="light"]` block that overrides the token values, nothing else.

## The rule

- **When you change the workpaper palette/typography in `render/html.py`, update
  `plane/static/app.css` to match (and vice-versa).** They are intentionally one design language;
  a drift makes the app and the document it produces look like different products. The app CSS header
  comment already points at `render/html.py` as the source of truth — honor it.
- **Never hardcode a color (or theme-varying value) in a plane template or in `app.css` rules — route
  it through a `var(--token)`.** Theming relies on it: the light theme is purely a token override, and
  the dark CodeMirror syntax colors live in tokens (`--cm-*`) so the same `.cm-*` rules work in both
  themes. A raw hex in a rule silently breaks light mode. When a value legitimately varies by theme
  (header backdrop, row-hover tint, card shadow), add a token to `:root` **and** to the
  `[data-theme="light"]` block — don't inline it.
- **Apply the saved theme before first paint.** The `data-theme` attribute is set from
  `localStorage` by a tiny inline script in `base.html`'s `<head>`, *before* the stylesheet link, to
  avoid a flash of the wrong theme. Keep that script first; don't move theme application into a
  deferred/bottom script.
- **The embedded workpaper stays as-is inside the app.** `run_view.html` embeds the workpaper via an
  `<iframe srcdoc>`; it is a fixed, self-contained artifact (the SDK's contract output) and keeps its
  own dark styling in both app themes. Do not try to re-theme the embedded workpaper from the app.
- **To restyle ONE page's use of a shared structural class, add a modifier class — never mutate the
  base class.** `.page-head` is included by ~20 `plane/` templates; making `.page-head` itself
  `display:flex` to push one page's button to the right silently re-lays-out every other page. Scope
  it: keep the base rule, add `<div class="page-head control-head">` and a `.control-head { display:
  flex; ... }` rule that only that page carries (2026-06-27: the control-editor split header). Same
  guard for `.card`, `.field`, `.btn` — grep the template tree for the class before changing its base
  rule, and put per-page behavior on a modifier.

## Reference

- `uticen_lite/plane/static/app.css` — token definitions (`:root` + `[data-theme="light"]`); the
  header comment names `render/html.py` as the palette source of truth.
- `uticen_lite/render/html.py` (CSS block, ~lines 62–332) — the workpaper's inlined tokens that
  the app mirrors.
- `uticen_lite/plane/templates/base.html` — no-flash theme bootstrap script (head) + toggle.
- `uticen_lite/plane/templates/control_edit.html` — dark CodeMirror override driven by `--cm-*`
  tokens (must load after `codemirror.min.css` to win).
