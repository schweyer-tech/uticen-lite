# Unified Logic authoring — Builder / Flowchart / Python tabs

> Status: approved design (2026-06-21). First cut. Implementation plan to follow via writing-plans.

## Problem

The control editor's no-code logic is "all over the place":

- **Two competing no-code surfaces** on every control — a conditions **rule-builder** in the
  Definition tab's "Test logic" section, *and* a **node builder** on a separate Pipeline tab.
- **Python appears twice** — the escape-hatch editor in Definition's "Test logic", and a
  "Generated Python" block stacked at the bottom of the Pipeline page.
- The Pipeline page **stacks** Steps → Flowchart → Generated Python vertically instead of letting
  the author toggle between them.

## Goals

- One coherent place to author a control's test logic, with three toggleable views.
- Python lives in exactly one place, never in the Definition tab.
- Definition tab carries metadata only.

## Non-goals (respect `STRATEGY.md`)

- No change to the bundle contract (`contract/bundle.schema.json`) or the compiled artifacts —
  cardinal rule [0001]. The store schema is unchanged.
- Not the CCM loop; still single-user/localhost; author → run → view → export only.
- Cross-source rules rendered as true Import+Join+Test graphs is a **follow-up**, not this cut.

## Design

### Tab structure (server-rendered sub-routes — learning [0007])

Top-level control tabs become **Definition · Logic · History** ("Logic" replaces "Pipeline").
`Logic` has three sub-tabs, each its own `GET` sub-route so it survives reload and matches the
existing tab pattern:

| Route | View |
| --- | --- |
| `/controls/{id}/logic` | 302 → `/logic/builder` |
| `/controls/{id}/logic/builder` | **Builder** — node graph editor |
| `/controls/{id}/logic/flowchart` | **Flowchart** — read-only SVG diagram of the saved graph |
| `/controls/{id}/logic/python` | **Python** — generated (read) + escape hatch (edit) |

Register all `logic` sub-routes **before** the `/controls/{id}` catch-all so it can't shadow them
(learning [0007]). `/controls/{id}/pipeline` 301-redirects to `/controls/{id}/logic/builder`
(preserve bookmarks/tests). A `_logic_tabs.html` include renders the sub-tab nav with an `active`
key, mirroring `_control_tabs.html`.

### Definition tab → metadata only

Remove the entire "Test logic" section (the No-code/Python radio, `rule_conditions`/`rule_builder`,
the CodeMirror Python editor). Keep: Control ID, Title, Objective, Narrative, Framework refs,
Failure thresholds, and the **data-source checkboxes** (sources stay here; Import nodes pick from
the bound set). No Python anywhere on this tab.

### Logic ▸ Builder — nodes for everything

The current Steps node editor. A **new** control seeds an `Import → Test` scaffold so the Builder is
never empty. Existing controls map in by stored representation:

- **pipeline** (has a graph) → render the graph as-is.
- **simple rule_spec** (single-source, no graph) → derive an editable `Import → Test` graph
  (conditions ride the Test node). On save it persists a real graph that **recompiles to the same
  rule_spec** (bundle byte-identical).
- **cross-source rule_spec** (`exists_in`/`not_exists_in`, no graph) → **first-cut:** keep the
  cross-source as a **Test-node condition** (1:1 with the rule grammar, behavior-identical), not a
  synthesized Join node. (Follow-up: render as a true Import+Join+Test graph.)
- **raw-Python** (`test_code`, no graph) → arbitrary Python can't be reversed into nodes; the
  Builder shows a notice: "This control is authored directly in Python — edit it on the Python tab,
  or start a node graph to replace it."

Distinguishing them uses existing store columns: `pipeline` present → graph control; else
`rule_spec` → rule; else `test_code` → raw Python; else → new/empty (scaffold).

### Logic ▸ Flowchart

The multi-lane SVG diagram (the U2 renderer) as a read view of the **saved** graph. For a
raw-Python control with no graph, show the same "authored in Python" notice.

### Logic ▸ Python

- **graph controls** → read-only **generated Python** + the existing **Convert to Python test →**
  one-way offramp.
- **raw-Python controls** → the editable **CodeMirror escape hatch**, relocated here from
  Definition. This is the *only* home for hand-written Python.

### Save / sync model

The three sub-tabs render **saved** state (server-rendered — learning [0007]). Author edits in
Builder → **Save** → Flowchart/Python reflect it. The Builder keeps its existing inline live
row-count preview. No client-side cross-tab state.

### Compile / store / bundle — unchanged

The graph → `rule_spec` (pure single-source) or `test(pop, sources)` `test_code` compile step
(learning [0010]) and the bundle shape are untouched. The three tabs are views/editors over the
same compiled artifacts. Verified by the existing contract gates
(`tests/test_contract_export.py`, `tests/schema/test_bundle_schema.py`).

### Minor: launch banner

The `controlplane` startup output prints both launch forms: `controlplane` and
`python -m uticen_lite.plane`.

## Affected surfaces (indicative)

- Routes: `plane/routes/pipeline.py` (→ logic sub-routes: builder/flowchart/python + convert),
  `plane/routes/controls.py` (Definition GET/POST drop test-logic; relocate the
  `_conditions`/`_condition_row` partial endpoints if the Test node reuses the conditions UI),
  `plane/app.py` (startup banner).
- Templates: `control_edit.html` (strip test-logic), `control_pipeline.html` → split into
  `logic_builder.html` / `logic_flowchart.html` / `logic_python.html`, new `_logic_tabs.html`,
  `_control_tabs.html` (Pipeline → Logic), reuse `rule_conditions.html` on the Test node, relocate
  the CodeMirror editor into `logic_python.html`.
- Tests: a sub-route render test each; the rule/python/pipeline → Builder mapping; Definition no
  longer renders Python; **rewrite `tests/e2e/test_smoke.py`** to author via Logic ▸ Builder
  instead of Definition, then re-run `pytest tests/e2e -m browser` (learning [0012]).

## Risks

- **e2e rewrite** — the smoke authors a rule in Definition today; it must move to Logic ▸ Builder.
  Treated as load-bearing (learning [0012]).
- **Route shadowing** — `/logic/*` must register before `/{id}` (learning [0007]).
- **rule_spec → graph derivation** — kept trivial for the single-source case; cross-source kept as
  a Test-node condition this cut to avoid a risky Join-synthesis.

## Out of scope / follow-ups

- Cross-source rules rendered as real Import+Join+Test graphs.
- Moving the data-source binding out of Definition into the Import nodes (single source of truth).
- Live cross-tab sync (edit in Builder reflected in Flowchart/Python without a save).
