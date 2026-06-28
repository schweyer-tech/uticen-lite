# Design — Collapsible procedure sections on the Logic page

> Status: **approved design, pre-plan.** Date: 2026-06-28. Author surface: `controlplane` web app.
> Follow-on to [2026-06-28-procedures-node-grouping-design.md](2026-06-28-procedures-node-grouping-design.md)
> (procedures shipped as a node-grouping layer). This cycle restructures **how** that grouping is
> presented and authored on the Logic page. **No bundle / contract / store-schema change** — see §1.

## Problem

Procedures shipped as a first-class grouping layer, but the Logic **Builder** still renders a single
**flat vertical stack** of node cards. Procedure membership surfaces only as small colored chips and a
"Procedure ▾" selector buried inside each Test card, and procedure *definitions* live in a separate
top panel. The result: it is **not visually clear which workflow nodes belong to which procedure** —
exactly the authoring-ladder usability gap (#9) this cycle closes.

The author's mental model: bring in the **data sources** at the top; those feed into **procedures**;
each procedure is a labeled, **collapsible** section containing the procedure (code · name · assertion ·
threshold) and the nodes associated with it.

## Goal

Reorganize the Logic page so that:

- shared inputs sit in a band at the top,
- each procedure is a **collapsible section** whose header *is* the procedure editor and whose body
  holds that procedure's own nodes,
- the same grouping (collapsible) is reflected in the read-only Flowchart as procedure **swimlanes**.

Deliver this as a **presentation + authoring-layout** change that reuses the existing procedure model,
compile, run, workpaper, and bundle machinery untouched.

### Non-goals

- No change to the procedure data model, rollup math, workpaper, or **bundle contract**
  (`contract/bundle.schema.json`) — this cycle is `plane/` UI + one pure grouping helper.
- No drag-and-drop authoring (rejected during brainstorming — most JS/risk, weakest keyboard
  accessibility). Tests move between procedures via a "Belongs to ▾" select.
- No cross-control procedure library, no controlled-vocabulary assertions (unchanged non-goals from the
  prior cycle).
- No store-schema migration and **no `schema_version` bump** — nothing bundle-facing changes.

## Decisions captured during brainstorming

| # | Decision | Choice |
| - | -------- | ------ |
| 1 | How sections relate to authoring | **Sections become the editor** — the separate Procedures panel is absorbed into each section's header; insert-a-Test-in-section auto-assigns it; a "Belongs to ▾" select moves a test |
| 2 | Where shared upstream nodes live | **Top "Inputs & shared steps" band** — any node feeding ≥2 procedures (or 0) is shared; only nodes private to one procedure nest in its section |
| 3 | Flowchart scope | **Also restructure the Flowchart** into procedure swimlane bands with collapse |
| 4 | Collapse mechanism — Builder | Native `<details>`/`<summary>`; open state persisted client-side in `localStorage` (default expanded) |
| 5 | Collapse mechanism — Flowchart | **Server re-render** via `?collapsed=<proc_ids>` (the layout engine stays the single source of truth) |
| 6 | Contract/store impact | **None** — view-only reorg + one pure helper; no migration, no `schema_version` bump |

## Scope — what stays frozen

Unchanged: `ProcedureDef` / `Pipeline.procedures`, `procedure_id` on Test nodes, derived membership,
`compile_pipeline_procedures`, the run/rollup math in `store/run_service.py`, the workpaper renderers,
`schema/bundle.schema.json` + `contract/bundle.schema.json`, and the store schema. The only **new
logic** is one pure, pandas-free grouping helper; everything else is `plane/` templates, routes, CSS,
and JS. This keeps the cardinal rule trivially satisfied (learning
[0001](../../learnings/0001-stay-compatible-with-the-controlflow-app.md)): the contract gate
(`tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py`) is untouched because no
bundle producer changes.

## The grouping model (one helper, both views)

A new pure helper in `pipeline/procedures.py`:

```python
def group_nodes_by_band(pipeline: Pipeline) -> NodeBands:
    """Partition pipeline nodes into an ordered shared 'inputs' band + one band per
    effective procedure (ordered by position). Pure / pandas-free; no persistence."""
```

`NodeBands` shape (plain dataclass / dict — JSON-friendly for the template context):

```python
NodeBands = {
    "shared": [node_id, ...],                       # the Inputs & shared steps band
    "procedures": [
        {"proc": <effective ProcedureView>, "nodes": [node_id, ...]},  # ordered by position
        ...
    ],
}
```

Partition rule (membership = the existing `derived_membership(pipeline)`, which maps each node to the
set of procedures whose Test closure contains it):

- **Shared band** — membership size **≥2** (pre-fork imports/filters/joins) **or 0** (orphan /
  unassigned / unparsable). Usually just the data sources.
- **Procedure band `P`** — membership == exactly `{P}` (the nodes private to that procedure), keeping
  topological order, ending in `P`'s Test node(s).
- **Empty defined procedure** — a procedure with no assigned tests still gets a band (empty `nodes`),
  so its section renders with an "add a test" nudge.

### Soundness invariant (stated and tested)

A node private to procedure `P` can never topologically depend on a node private to a **different**
procedure `Q`: if it did, that upstream node would lie in both closures and therefore be **shared**
(membership `{P, Q}`), landing in the shared band — not in `Q`'s private set. Therefore the flattened
order `[shared…, P1 private…, P2 private…]` is **always a valid topological order**, and the existing
DOM-order serializer (`serialize()` in `logic_builder.html`) keeps producing a valid graph with no
ordering changes. A unit test asserts this on a multi-procedure fixture.

## Builder UX — sections are the editor

- **Procedures panel retired.** Its fields move into each **section header**: inline `code · name ·
  assertion · threshold` inputs (carrying the same `data-proc-*` attributes `serialize()` already
  reads), a delete (✕), and the collapse toggle.
- **Collapsible sections** use native `<details>`/`<summary>` — accessible, keyboard-navigable, and
  collapse with no JS. A small script syncs the `open` attribute to `localStorage`
  (key `cflow.logic.collapse.<control_id>.<band_key>`, where `band_key` is a procedure `id` or the
  literal `__inputs__`); default **expanded** when no stored value.
- **Section body** = the procedure's private node cards + insert zones. **Inserting a Test inside a
  section sets its `config.procedure_id`** to that section's procedure id (JS, at insert time).
  Inserting Filter/Join/Custom just places the card (membership derives on the next render).
- **"Belongs to ▾"** select stays on each Test card to *move* it to another procedure. Changing it
  autosaves → re-renders the `#pipe-cards` fragment → the card appears under the new section. This
  reuses the existing HTMX autosave-and-re-render flow (no drag-and-drop).
- **Top "Inputs & shared steps" band** holds shared/orphan cards with its own insert zone (Import /
  shared Filter/Join). It is **also** a collapsible `<details>` using the same affordance and
  `localStorage` mechanism, keyed `band_key = __inputs__`.
- **"＋ Add procedure"** at the bottom appends an empty section (a new `ProcedureDef` with no tests);
  the section shows the "add a test" nudge until a Test is assigned.
- **Legacy / single-procedure controls:** render as the Inputs band + **one** section. The sole
  auto-derived procedure keeps its **empty** code (learning
  [0036](../../learnings/0036-sole-auto-derived-positional-label-is-empty-for-render-bundle-parity.md));
  its header falls back to a neutral label (e.g. the terminal Test's name, else "Procedure") rather
  than rendering a bare "P1". Collapse still works; the underlying graph/wiring is unchanged until the
  author defines procedures.

### `serialize()` changes (logic_builder.html)

- Read `data-proc-*` from the **section headers** instead of `[data-proc-row]` panel rows
  (`serializeProcedures()` re-points to `[data-proc-head]`).
- Walk `[data-node]` across **all bands in DOM order** (unchanged shape — a flat node list; the band
  order is a valid topological order per the invariant above).
- On insert-in-section, set the new node's `procedure_id` before the autosave POST.
- The POST body, route, and stored `pipeline` JSON are **identical** to today — only where fields
  render and which container a card sits in changed.

## Flowchart UX — procedure swimlanes

- `_diagram(pipeline, counts, collapsed=frozenset())` reorganizes boxes into a **shared-inputs band on
  top** (full width) feeding **per-procedure swimlane bands** below, each with a colored header label +
  faint background (reusing the existing per-`position` palette and the lane-assignment engine). The
  band grouping comes from the same `group_nodes_by_band` helper, so the two views never disagree.
- **Collapse** is server-rendered (learning
  [0007](../../learnings/0007-control-plane-editors-are-server-rendered-sub-route-tabs.md)): the
  flowchart route accepts `?collapsed=<comma-separated proc ids>`; a collapsed band is laid out as a
  single **summary box** ("`P2 · Late Posting — 2 steps · ⚠ N`") that the shared inputs still feed,
  instead of its private boxes. Toggling a band's header HTMX-GETs the flowchart fragment with the
  updated `collapsed` set and swaps the SVG; the same `localStorage` keys drive which ids are sent, so
  Builder and Flowchart share collapse state. The layout engine remains the single source of truth —
  no client-side SVG surgery.
- The existing legend stays (code · name · color), now doubling as the band labels.

## Where the code lives

| File | Change |
| --- | --- |
| `pipeline/procedures.py` | **New** `group_nodes_by_band(pipeline)` (pure, pandas-free) + a `NodeBands` type. |
| `plane/routes/pipeline.py` | `_editor_context` builds `bands` for the Builder via the helper; `_diagram` gains a `collapsed` param + summary-box layout; flowchart route parses `?collapsed=`. |
| `plane/templates/partials/_pipe_cards.html` | Restructured into the Inputs band + one `<details>` section per procedure, each with insert zones; absorbs the procedure-definition fields into section headers. |
| `plane/templates/partials/_procedures_panel.html` | **Retired** (absorbed into section headers). |
| `plane/templates/partials/_pipe_node.html` | Test card keeps a relabeled "Belongs to ▾" move-select. Chips stay on **shared** (Inputs-band) cards — they convey which procedures consume that shared node — but are dropped from **private** cards, where the enclosing section already says it. |
| `plane/templates/partials/_pipe_diagram.html` | Swimlane band backgrounds/labels + collapsed summary boxes. |
| `plane/templates/logic_builder.html` | `serialize()`/`serializeProcedures()` re-pointed to section headers; insert-in-section assignment; `<details>` localStorage sync. |
| `plane/static/app.css` | Section / band / summary-box styling; retire `.proc-panel` / `.proc-row` rules. |

## Edge cases — degrade, never 500 (learnings 0013 / 0033)

- **Unparsable / raw cards** (graph won't parse): render in the top Inputs band rather than vanishing;
  grouping treats them as membership-0 (shared).
- **Incomplete graph:** the existing friendly "not ready" / "—" probes are unchanged; `group_nodes_by_band`
  returns whatever bands it can and never raises.
- **Empty defined procedure:** renders an empty section with a nudge; ignored at run until it owns a
  Test (no new raise path).
- **Missing/mismatched item-keys:** unchanged from the prior cycle.

## Testing strategy

- **Unit `group_nodes_by_band`:** shared-vs-private partition on a multi-procedure fixture; the
  **topological-soundness invariant** (flattened band order is a valid topo order); orphan/unassigned →
  Inputs band; empty defined procedure → empty band present; no-procedures fallback → Inputs + one
  section; incomplete/unparsable graph → no raise, raw nodes in Inputs band.
- **e2e browser smoke** (learning
  [0012](../../learnings/0012-rerun-e2e-browser-smoke-on-htmx-swap-changes.md)): re-run and extend the
  Builder smoke for the sectioned DOM, the `<details>` collapse toggle, and **insert-in-section
  auto-assignment** (a Test added in section P2 saves with `procedure_id == P2`); add a flowchart
  collapse round-trip (toggling a band re-renders with the summary box).
- **No contract/bundle tests touched** — assert this explicitly by confirming `bundle/`, `schema/`,
  and `contract/` are not in the diff; run the full suite (`python -m pytest -q`) + `ruff` + `mypy`
  pristine.

## Rough build sequence

1. **Helper:** `group_nodes_by_band` + the soundness/partition unit tests (TDD).
2. **Builder restructure:** `_editor_context` bands; `_pipe_cards.html` into Inputs band + `<details>`
   sections with absorbed headers; retire `_procedures_panel.html`; `serialize()` re-point;
   insert-in-section assignment; `<details>` localStorage sync.
3. **Flowchart swimlanes:** `_diagram` band layout + `collapsed` param + summary boxes;
   `_pipe_diagram.html` band backgrounds/labels; flowchart route `?collapsed=` + HTMX toggle.
4. **CSS:** section/band/summary-box styling; retire old panel rules.
5. **e2e smoke + edge-case tests**; full-suite / ruff / mypy green.

## Open questions

None blocking. Deferred niceties: remembering collapse state server-side (kept client-only by the
brittle-by-design value); animating section expand/collapse.
