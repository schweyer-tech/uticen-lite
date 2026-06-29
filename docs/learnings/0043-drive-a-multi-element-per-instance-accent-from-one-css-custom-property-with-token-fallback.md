---
id: 0043
date: 2026-06-29
area: frontend
tags: [plane, css, custom-properties, theming, color-mix, per-procedure-color]
status: active
supersedes: null
superseded_by: null
---

# Drive a dynamic per-instance accent shared by several sub-elements from ONE CSS custom property on the container, each sub-element reading `var(--accent, <static-token>)` — never thread the color inline onto each sub-element

## Context

The Logic Builder procedure header shows a per-procedure colour in **two** places — the
card's left accent **stripe** and the **code-chip** tint (text + border + a muted
background wash). The colour is assigned server-side by position order
(`_PROC_PALETTE` in `routes/pipeline.py`), so the client JS that adds a procedure cannot
know it.

## What worked

- Emit ONE inline custom property on the shared container, server-rendered:
  `<details class="proc-section" style="--proc:{{ band.proc.color }}">`.
- Every sub-element reads it with a **static-token fallback** so it degrades when the
  property is absent:
  - stripe: `border-left: 3px solid var(--proc, var(--border-strong));`
  - chip: `color: var(--proc, var(--text-secondary)); border-color: var(--proc, var(--border-default));`
  - muted wash from the SAME property (no second `--proc-muted`):
    `background: color-mix(in srgb, var(--proc, var(--bg-input)) 14%, var(--bg-input));`
- A client-JS-built section (`newProcedureSection()`) sets **no** `--proc`, so it renders
  neutral until the next server render of `#pipe-cards` fills in the palette colour — a
  graceful default, and a one-line comment at the JS build site stops a future reader from
  "fixing" it with an invented client-side colour.

## The rule

For a **dynamic, per-instance accent colour that more than one sub-element must reflect**:

1. Set a SINGLE CSS custom property on the shared container (inline, server-rendered),
   and have each sub-element read `var(--accent, <static-token>)`. Do **not** repeat the
   colour as an inline `style` on each sub-element — that's the brittle "audit every site"
   trap ([[0014]]); one property cascades to all descendants.
2. Always give the var a **static token fallback** so an instance lacking the property
   renders neutral. In particular a client-JS-created instance that can't know the
   server-assigned value degrades cleanly; document at the JS build site that the colour
   is server-assigned on the next render (don't invent one client-side).
3. Derive any muted/tinted variant from the **same** property via
   `color-mix(in srgb, var(--accent, <bg>) <pct>%, <bg>)` rather than introducing a second
   `--accent-muted` property — one source of truth. `color-mix` is fine for a
   modern-browser, localhost-only surface and degrades to the `<bg>` token where
   unsupported.
4. Keep routing all *static* chrome colours through `var(--token)` as before ([[0005]]);
   this rule only covers the *dynamic per-instance* accent.

## Reference

- `uticen_lite/plane/templates/partials/_pipe_cards.html` — `<details class="proc-section" style="--proc:…">`.
- `uticen_lite/plane/static/app.css` — `.proc-section` stripe + `.proc-head .proc-code-chip` tint/wash off `var(--proc, …)`.
- `uticen_lite/plane/templates/logic_builder.html` — `newProcedureSection()` sets no `--proc` (documented neutral fallback).
- `uticen_lite/plane/routes/pipeline.py` — `_PROC_PALETTE` / position-ordered colour assignment.
- Kin: [[0005]] (route colours through tokens + theming) and [[0032]] (keep the component selector out-specifying the global input block).
