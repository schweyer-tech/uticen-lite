# Procedure header owns procedure identity; nodes are pure steps — Design

**Date:** 2026-06-28
**Area:** control plane — Logic Builder (`plane/`), demo (`examples/northwind-trading/`)
**Status:** approved (brainstorm), pending plan

## Problem

After the collapsible-procedure-sections cycle (PR #102) and the multi-procedure
Northwind showcase (PR #103), procedure identity still appears to "live on the node" in
the Logic Builder. For `manual-je-review` (Finance.GL.1) the terminal Test node card
shows what reads as procedure-level information, so the procedure feels merged into its
nodes rather than presented as a header above them.

Concretely, two things cause this:

1. **The terminal Test node card carries vestigial procedure fields.** In
   `uticen_lite/plane/templates/partials/_pipe_node.html` the "Test terminal extras"
   block renders a **"Procedure title"** input (`data-proc-title` → `node.config.title`,
   lines ~121-126) and **"Threshold %/Count"** inputs (`data-threshold-pct` /
   `data-threshold-count` → `node.config.failure_threshold_pct/count`, lines ~141-150).
   The procedure section header above already owns both (code · name · assertion ·
   threshold). For an *explicitly defined* procedure these node fields are dead — the
   header's values win — so editing them does nothing visible.

2. **The procedure header has no narrative field.** The only place a procedure's prose
   can be typed today is a node's own Narrative box, which is why `sod` shows
   "P1 — Segregation of duties…" and reads like procedure identity bolted onto a node.

The procedure **section header** (the `<details>`/`<summary>` in
`partials/_pipe_cards.html`) is already rendered above its nodes and is the right home
for procedure identity. This cycle finishes the consolidation: the header becomes the
complete procedure editor, and the Test node becomes pure step mechanics.

## Decisions (from brainstorming)

- **Narrative:** the procedure header gets its **own** narrative field, **and every node
  keeps its own** per-step narrative. They are two distinct things; node narratives are
  unchanged.
- **Single-procedure controls always show one procedure header band.** This is already
  the rendered behavior (a lone auto-derived procedure produces one band); the design
  keeps it and makes that header the editor for the lone procedure's identity.

## Goal

In the Logic Builder, a procedure is presented as a header that owns all of its identity
(code, name, assertion, **narrative**, threshold), sitting above its steps; each node
card is purely the mechanics of one step. No procedure-level field is editable on a node.

## Non-goals

- No change to the bundle shape or `schema_version` (cardinal rule). Procedure
  code/name/assertion/threshold/verdict remain render+store-only (learning 0015).
- No change to the Flowchart (it already groups procedure swimlane bands) beyond what
  falls out of unchanged band data.
- No new procedure data model — `ProcedureDef` already carries every field.
- No change to per-step node narratives (kept, per the decision above).

## Architecture / components

The change is confined to the Logic Builder's template + serialization split, one
client-side correctness fix, and a demo content touch-up. Data flow is unchanged: cards
and procedure section headers serialize into the store-only pipeline JSON
(`graph.nodes` + `graph.procedures`), which compiles to the existing bundle artifact at
run/build.

### Unit 1 — Procedure header becomes the complete editor

**Files:** `uticen_lite/plane/templates/partials/_pipe_cards.html` (the `proc-head`
`<span>` inside the section `<summary>`); `uticen_lite/plane/templates/logic_builder.html`
(the `newProcedureSection` JS innerHTML template + `serializeProcedures()`);
`uticen_lite/plane/static/app.css` (header layout).

- **Thread `narrative` into the `band.proc` view-model (learning 0038).** Today
  `_procedure_context` (pipeline.py ~698-709) builds the procedures view-model WITHOUT a
  `narrative` key, and `_card_bands`'s `_proc_defaults` (pipeline.py ~753-756) likewise
  omits it. Add `"narrative": p.narrative` to the `_procedure_context` procedures dict and
  `"narrative": ""` to `_proc_defaults` so `band.proc.narrative` is reachable in the
  template at every render site that builds `bands`.
- Add a **Narrative** control to the procedure header (`data-proc-narrative`), rendered
  as a wrapping full-width textarea row beneath the existing code/name/assertion/threshold
  row so the header reads "identity row, then narrative". Value bound to
  `band.proc.narrative`.
- Mirror the field in `newProcedureSection`'s innerHTML so a freshly added procedure
  section has the narrative control too.
- `serializeProcedures()` reads `[data-proc-narrative]` into `graph.procedures[].narrative`.
- `app.css`: let the `proc-head` wrap so the narrative occupies a full-width second row
  beneath the identity inputs. The narrative lives inside the section `<summary>` (the
  header), so it stays visible whether the section is open or collapsed, consistent with
  the existing `.proc-in` inputs. Style the narrative control to match `.proc-in`.

### Unit 2 — Test node card becomes pure step mechanics

**Files:** `uticen_lite/plane/templates/partials/_pipe_node.html` (the
`{% if node.type == 'test' %}` "Test terminal extras" block);
`uticen_lite/plane/templates/logic_builder.html` (`serialize()` Test branch).

- Remove the **"Procedure title"** `pipe-row` (`data-proc-title`).
- Remove the **"Threshold %/Count"** `pipe-row` (`data-threshold-pct` /
  `data-threshold-count`).
- Keep everything else on the Test card: Name (`data-node-title`), Input, Match logic +
  Conditions, **Belongs to ▾** (`data-procedure`), Severity, Description, Item key, and
  the per-step Narrative row.
- In `serialize()` (the `type === 'test'` branch), delete the reads that write
  `node.config.title`, `node.config.failure_threshold_pct`, and
  `node.config.failure_threshold_count`. After this, a re-saved control drops procedure
  metadata off the node; `node.config` for a Test carries only `procedure_id`, `severity`,
  `description_template`, `item_key_column`, `logic`, `conditions`.

### Unit 3 — Sole-procedure code-default correctness fix

**File:** `uticen_lite/plane/templates/logic_builder.html` (`serializeProcedures()`).

Two coupled defaults pre-fill / persist a procedure's code, and both must keep a **sole**
procedure's code empty so the workpaper heading stays the legacy `P1: title` form (a
non-empty `code` switches it to the `P1 &middot; title` middot form — learning **0036**;
the heading reaches `render/html.py:929-932` and `render/markdown.py:224-227`). Because
this cycle makes the header the editing surface even for a single-procedure control,
editing one must NOT promote it to `code="P1"`.

- **Display default** — `_procedure_context` (pipeline.py ~701) currently sets
  `"code": p.code or f"P{i + 1}"`, so a lone procedure's code input pre-fills as `"P1"`.
  Change to `"code": p.code or (f"P{i + 1}" if len(eff) > 1 else "")` so a sole procedure's
  input shows empty (author-defined codes always preserved).
- **Serialize default** — `serializeProcedures()` (logic_builder.html ~310) currently sets
  `code: ... || ('P' + (i + 1))`. Change to
  `code: ... || (heads.length > 1 ? 'P' + (i + 1) : '')` so a sole section serializes
  `code=""` (and a 2+ set serializes `P1..Pn` by position). The display self-heals to
  `P1..Pn` on the next server re-render once 2+ sections exist.
- Result: editing a single-procedure control's header never changes its workpaper
  heading; it stays the byte-identical `P1: title` legacy form, matching the lone
  auto-derived path and the bundle's single-procedure default (assemble.py forces `code=""`
  for N==1 regardless).

**Single-procedure scope (decision).** The full editable header (incl. narrative) renders
for every procedure, including a lone one — honoring "always show one procedure header".
The export bundle structurally collapses a single procedure into the control's own
title/narrative (assemble.py:121-142), and the local run-path already renders a lone
procedure's *terminal-derived* title/narrative — so a single-procedure local-vs-bundle
title/narrative difference is a **pre-existing property**, not introduced here. This cycle
only guarantees the **heading form** invariant (sole `code=""`); it deliberately does not
re-architect single-procedure title/narrative reconciliation (out of scope; a possible
follow-up). No demo control exercises lone-header editing, so the demo/tests stay green and
byte-identical.

### Unit 4 — Demo content: populate Finance.GL.1 procedure narratives

**Files:** `examples/northwind-trading/controls/manual-je-review/pipeline.yaml` (the
top-level `procedures:` array); `tests/examples/test_northwind.py`.

- Add a `narrative` to each of the two procedures (`p1` Independent Review (SoD),
  `p2` Reviewer Assigned) describing the procedure's purpose, so the new header field is
  demonstrated and the workpaper's procedure narrative renders for the showcase. Keep the
  existing per-step node narratives on `sod` and `review` (they describe the step, not the
  procedure).
- This is additive store/render content (procedure narrative flows into the unbounded
  `workpaper.procedures` array, never changes the bundle *shape*; no `schema_version`
  bump). It does not change any control/source count, so the fan-out is confined to
  `tests/examples/test_northwind.py` (learning 0031): assert each procedure's narrative is
  present.

## Data flow

1. Page load: `_card_bands` (routes/pipeline.py) → `bands` context; `effective_procedures`
   provides each band's `proc` (explicit or auto-derived; auto-derived still reads
   name/threshold/narrative from the terminal node's config as a fallback). Header renders
   pre-filled from `band.proc`.
2. Edit + save/autosave: `serialize()` reads node cards (now without procedure-title /
   threshold); `serializeProcedures()` reads section headers (now including narrative,
   with the sole-procedure code fix) → `graph.procedures`.
3. Run/build: unchanged — the graph compiles to `rule_spec`/`test()` for the bundle;
   procedure threshold/verdict/narrative are render+store-only.

## Migration / back-compat

No migration step. A control relying on auto-derivation renders its header pre-filled from
`_auto_procedure` (name + threshold from the terminal's `config`, narrative from the
terminal's `narrative`); the **first save migrates** the values into `graph.procedures[]`
and drops the procedure-level values from the node config. Un-re-saved controls keep
working via the unchanged `_auto_procedure` fallback. An orphan/unassigned terminal still
auto-derives its own procedure (its own band + header). Note: for an auto-derived single
procedure, the header narrative pre-fills from the terminal node's narrative, so the same
prose may briefly appear in both places until the author edits one — acceptable, and the
demo sets distinct procedure narratives explicitly.

## Error handling

No new failure modes. Empty header fields serialize as empty/`null` exactly as the
existing code/name/assertion/threshold fields do; the graph still tolerates incomplete
states (learning 0013). The sole-procedure fix narrows an existing latent defect; it does
not add error paths.

## Testing strategy

- **Browser e2e** (`tests/e2e`, `-m browser`) — required because this restructures the
  Test form and the procedure header in place (learning 0012):
  - The Test node card no longer renders a "Procedure title" field or Threshold/Count
    fields.
  - The procedure header renders a narrative field.
  - **Editing the procedure narrative persists**: assert the app's own write (reload →
    the narrative is present), using a write-path assertion, never injecting state to dodge
    a race (learning 0037).
  - A single-procedure control renders exactly one procedure header band.
- **Unit** (`tests/pipeline`, `tests/plane`, `tests/render`):
  - A single explicit procedure with `code=""` round-trips and renders byte-identically
    to the lone auto-derived form — pins the 0036 invariant against the serialize change.
  - A procedure carrying a narrative renders that narrative in the workpaper
    (`render/html.py` / `render/markdown.py`).
- **Demo** (`tests/examples/test_northwind.py`): assert each Finance.GL.1 procedure's
  narrative text is present (in the pipeline/store and the rendered workpaper/bundle as
  applicable). No count assertions change.
- **Gates:** `python -m pytest -q` pristine, `python -m ruff check .`,
  `python -m mypy uticen_lite`. Contract gate
  (`tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py`) stays green —
  proves no bundle-shape drift.

## Global constraints

- Cardinal rule: no change to `contract/bundle.schema.json` or `schema_version`; procedure
  code/name/assertion/threshold/verdict stay render+store-only (learnings 0001, 0015).
- Preserve the single-procedure byte-identity invariant (learning 0036) with a test.
- Pyodide-safe core untouched (no pandas in `pipeline/`).
- Thread any context the partials need through every render site of `_pipe_cards.html` /
  `_pipe_node.html` (learning 0038) — though this change adds no new server-side context
  key (the narrative is already on `band.proc`), confirm the four render sites still pass
  `bands`.
- ruff `py311`, line length 100; Python floor ≥3.11.
