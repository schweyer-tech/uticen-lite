# Spec — Visual flowchart authoring (low-code control pipeline)

> Status: ready to pick up. Owner: TBD. Relates to issues **#9** (no-code usability) and **#10**
> (AI-assisted authoring) — this is the surface that unifies both. Branch:
> `claude/lowcode-visual-flowchart-d0cycs`.

## 1. Why

The control plane is pitched as no-code authoring for a non-developer GRC analyst, but today the
no-code rule builder is a flat list of `Condition(column, op, value)` rows with an AND/OR toggle
(`rules/spec.py`, `plane/templates/partials/rule_builder.html`). That is a developer's data
structure leaking through the UI, and it is **single-source only** — so the realistic controls (the
whole Northwind set: three-way match, terminated-access, vendor SoD) drop straight to the Python
escape hatch (`def test(pop, sources)`), which a non-dev cannot write.

A GRC analyst doesn't think in condition rows; they think in an **audit program**: *take all
payments → keep the ones that matter → look up the matching invoice/PO → assert the rule → the rows
that fail are my exceptions.* That is inherently a **flow**. This spec introduces a **visual control
pipeline**: a left-to-right diagram of typed nodes that an analyst builds, that reads like the test
narrative, and that **compiles to the existing execution artifacts** (`rule_spec` or a single
`test(pop, sources)`) so the bundle contract never changes.

**The core must work with no AI and no network** (per `STRATEGY.md` — the authoring ladder's
no-code rung must stand on its own). AI ("lay out the nodes from a description", issue #10) is an
optional topping layered on top later; templates are the AI-free on-ramp.

## 2. North-star fit

- Moves authoring up the ladder (`manual Python → no-code → AI-assisted → AI-authored`) and raises
  the metric that matters: **share of tests authored without hand-written Python**.
- Stays inside the non-goals: a **linear pipeline with a small fixed vocabulary**, NOT an arbitrary
  node-graph / general data tool. The freedom of a free-form graph is the burden; resist it.
- **Cardinal rule intact** (learning 0001): the graph is authoring-side state in
  `controlplane.db`; it **compiles** to `rule_spec`/`test_code` at run/build time, so
  `contract/bundle.schema.json` never learns the word "node". No schema bump.

## 3. Core design principles (do not violate)

1. **The graph is the source of truth, not Python text.** Both the visual canvas and any Python view
   are *renderings/compilations of the graph*. We never parse arbitrary hand-written Python back into
   nodes (that is decompilation, and the round-trip trap). The toggle to Python is two-way only *up
   to the grammar boundary*; crossing it is the deliberate one-way "Convert to Python test"
   graduation (§9).
2. **Data enters only through Import nodes.** Every source the test touches is a visible node bound
   to a **documented** source. Provenance is structural and computable from the graph; the
   workpaper's Data Sources section derives from the Import nodes. A source can't be referenced
   unless it's been through the source editor (title, description, key columns, mapping) — so
   metadata documentation is a *precondition of use*, not a chore.
3. **Custom Python nodes never see a source.** They are `rows → rows` (transform/filter) or
   `rows → violations` (terminal test). They are starved: only `rows` in scope, no `sources`, no file
   access. Enforced by lexical scoping at compile (§7) + an AST allowlist at save + a **hard export
   gate** (§8). All cross-source work goes through the visual Import/Join nodes so it's always
   visible on the diagram.
4. **Live row-counts at every joint** are the offline feedback loop. With no AI to ask "is this
   right?", the count surviving each node is how a non-dev sees a mistake (a node dropping to 0 is the
   tell). This directly fixes issue #9's "silently-empty result".
5. **Every node carries a `narrative`** ("why this step exists"). It is authoring + workpaper
   metadata (becomes a documented test-step rationale and, when compiled to Python, a comment). It is
   NOT threaded into the bundle schema (cardinal rule).

## 4. Node vocabulary

A control's pipeline is a small DAG (mostly linear; the only fan-in is Join). Each node:
`{ id, type, narrative, config, inputs: [node_id, ...] }`. Import nodes have no `inputs` (they have a
`source_id`). Exactly one **terminal Test** node.

| Node | Inputs | Config | Emits | Backed by |
| --- | --- | --- | --- | --- |
| **Import** | — | `source_id` (documented source only) | rows of that source | `set_control_sources` + `repo.get_source` |
| **Filter** | 1 | `conditions[]`, `logic` (all/any) — narrows rows, not a violation | fewer rows | the 12 operators in `rules/spec.py` |
| **Join** | 2 | `left_key`, `right_key`, `mode` (`exists`/`not_exists`/`inner`/`left`), optional `bring_columns[]`, optional `aggregate` (e.g. count/max per key) | enriched/filtered rows | NEW (the #9 cross-source primitive) |
| **Aggregate** | 1 | `group_by[]`, `aggregations[]` (`{col, fn, as}`) | grouped rows | NEW |
| **Custom Python** | 1 | `code`, `flavor` (`transform`→rows / `test`→violations), `narrative` | rows or violations | escape hatch, scoped per-node (§7–8) |
| **Test (terminal)** | 1 | `conditions[]`, `logic`, `severity`, `description_template`, `item_key_column` | violations | `evaluate_rule` / `rule_spec` |

**Phase 1** ships Import, Filter, Join (`exists`/`not_exists`/`inner`/`left` + optional count/max
aggregate-on-join), Test, Custom Python. **Aggregate** (general group-by) and a **Transform**
(derived column) node are Phase 2 unless the Northwind audit (§10) shows they're needed for P1
coverage.

## 5. Two renderings of one graph

- **Pipeline / set view (build + sanity-check):** nodes top-to-bottom in topological order, each
  annotated with **rows surviving** (computed on the loaded sample). `1,204 payments → Filter: 88 →
  Join invoices: 88 → Test: 6 exceptions`.
- **Single-record trace (debug + evidence):** pick one row, walk it through each node, show where it
  gets flagged. This *is* the workpaper exception sentence ("PO-1234 is an exception because its
  approver equals its creator") — the diagram and the defensible evidence are the same object. Cheap
  once the graph + sample exist.

## 6. Storage & wiring (store-only — no bundle impact)

- Add `test_kind = "pipeline"` alongside the existing `"rule"` / `"python"` in `repo.upsert_control`
  and a **store-only** `pipeline` JSON column (graph above). New migration in `store/migrations.py`.
  Follow learning **0006**: do NOT thread any of this into `to_data_source()` / the bundle; it is
  authoring state only.
- Source binding (`set_control_sources`) is **derived from the Import nodes** on save — the analyst
  binds sources *by adding Import nodes*, not via a separate picker. The Import-node dropdown lists
  `repo.list_sources(conn)`.
- `_save_from_form` / `_rule_spec_from_form` in `plane/routes/controls.py` gain a `pipeline` branch.
  Keep per-handler connections for writes (learning **0002**).

## 7. The compile step (graph → execution artifact)

A pipeline **compiles** to one of the existing artifacts; run/build/bundle then reuse the current
paths unchanged. Compile selects the target:

- **Pure & single-source** (one Import → Filters → Test, flat all/any, no Join/Aggregate/Custom) →
  emit a **`rule_spec`** (so the simple case stays "no-code" in the bundle, preserving the metric).
- **Otherwise** → emit a generated **`test(pop, sources)`** Python string by walking the DAG
  topologically: each node emits a pandas snippet over named frames; Import nodes pull
  `sources[code_id].df`; the terminal Test emits the violations list (same shape as
  `evaluate_rule`). Node `narrative` → `# ` comments.

**Custom Python nodes compile to module-level functions, not inline blocks.** Emit
`def _node_<id>(rows): <body>` at module top. Because `sources` is a *parameter of `test()`*, a
module-level function **structurally cannot see it** — that is the real teeth behind "custom nodes
never see a source", not just a lint. The orchestrating `test()` calls `stream = _node_<id>(stream)`.
This same machinery (walking/emitting ASTs) is what the AST deny-scan (§8) rides on.

Run and build both go through compile → existing runner/`assemble_bundle`. The compiled artifact is
what lands in `test_code`/`rule_spec`; the graph stays in the `pipeline` column for re-editing.

## 8. Enforcement stack (custom Python = no file/source access)

Threat model: this is a **guardrail against accidental bypass**, not a sandbox against a malicious
local user (who already has the full escape hatch + a shell). So: light, pure-Python, layered — no
subprocess/seccomp/RestrictedPython/WASM (those fight the offline, brittle-by-design ethos; the trust
boundary that matters — raw population never in the bundle — is enforced at export, not at node run).

1. **Allowlist AST lint at save.** Parse the node's code with `ast`; allow a tiny pure set of imports
   (`re`, `datetime`, `decimal`, a provided helper module) and reject everything else; reject `open`,
   `read_csv`/`read_excel`, `__import__`, `eval`/`exec`/`compile`, `globals`, and dunder attribute
   access. On violation, show an **inline error on the node** that teaches the rule:
   *"Custom nodes can't read files — pull data in with an Import node, or convert this control to a
   full Python test, where source access is allowed."*
2. **Lexical starvation at compile** (§7): custom nodes become module-level `def _node(rows)` — no
   `sources` in scope, only `rows`.
3. **Hard export gate.** Re-run the same AST deny-scan in `validate`/`build`; **refuse to produce a
   bundle** if any custom node trips it — same posture as `tests/test_contract_export.py`. This keeps
   the canvas's provenance claim airtight where it's *consumed*, not only where it's typed. Decision:
   **hard block**, not a warning.

## 9. The Python view & the graduation offramp

- **Custom Python is just another node type** in the diagram (a special-looking one carrying code).
  There is no separate "Python mode"; the canvas always shows the whole test as nodes.
- A **read-only generated-Python view** (the compiled `test()`, foldable per node) is the glass-box:
  generated blocks read-only, custom-node blocks editable inline. This is the two-way toggle *within
  the grammar* (§3.1) and the learning ramp.
- **"Convert to Python test"** is the one-way door: compile the current pipeline to the stitched
  `test(pop, sources)`, set `test_kind="python"`, and drop the author into the existing escape-hatch
  editor (CodeMirror) pre-filled. Nothing is lost; they crossed from the hybrid rung to the top rung
  deliberately and visibly. This is what makes the §8 hard block humane — being blocked is a fork,
  not a dead end.

## 10. First task for the implementer — grammar-coverage audit

Before drawing boxes, audit the **8 Northwind controls** (`examples/northwind-trading/controls/*`)
against the node vocabulary: for each, can it be expressed **fully visually**, **hybrid** (visual +
one Custom node), or only via the **full escape hatch**? That audit tells you whether the bottleneck
is the *canvas* or the *grammar underneath it* (e.g. whether Join's `exists`/`not_exists` +
count-on-join covers terminated-access and three-way-match, or whether general Aggregate must move
into Phase 1). The boxes are the easy part; grammar coverage is the product.

## 11. UI / stack guidance

- Honor the existing stack: FastAPI + HTMX, **server-rendered**, no JS build step, Pyodide-safe core
  (pandas only in `adapters/`). Per learning **0007**, model the editor as server-rendered sub-route
  tabs, not client-side JS tabs.
- **Phase 1 = a server-rendered stacked list of node "cards"** in topological order (HTMX add /
  remove / reorder, like the current `+ Add condition`), plus a **generated read-only diagram**
  (server-rendered SVG/CSS from the graph) for the "flowchart" visual and the row-counts. Represent a
  Join's fan-in as a card naming its two input streams by id — not drawn wires. A richer drag-drop
  canvas is a *later* enhancement, not P1 (avoid pulling in a heavy graph library / build step up
  front).
- Every node card has a **column dropdown** populated from the bound source's columns
  (`repo.get_source`) — never free-text (issue #9). Keep free-text only as a power-user fallback.
- Reuse the design tokens shared with the workpaper renderer (learning **0005**); route colors
  through `var(--token)`.

## 12. Done = (acceptance)

- A non-dev can author a **cross-source** control (e.g. terminated-access) **fully in the browser**,
  no Python, with column dropdowns and live row-counts at each node.
- The pipeline **compiles** and runs full-population with the correct result; the workpaper renders
  (node narratives visible in the procedures), and **export validates** against
  `contract/bundle.schema.json` with **no schema change**.
- A **Custom Python** node works as a `rows → rows` step; a node that reads a file is **blocked at
  the export gate** with the offramp message; **"Convert to Python test"** round-trips a pipeline into
  the escape hatch losslessly.
- Suite stays green/pristine, `ruff` + `mypy` clean (project gates). New learnings captured if any
  durable rule emerges (e.g. the compile/starvation pattern).

## 13. Explicitly out of scope (for this issue)

- AI node-layout (#10) — separate, optional, layered on this substrate later.
- Arbitrary free-form node graphs / a general data tool (non-goal).
- Any change to `contract/bundle.schema.json` or the bundle shape (cardinal rule).
- A heavyweight client-side canvas / JS build step in Phase 1.
