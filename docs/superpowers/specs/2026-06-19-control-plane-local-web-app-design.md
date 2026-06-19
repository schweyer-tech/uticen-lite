# Control Plane — Local Web App for the ControlFlow SDK — Design

> Status: **Approved design (2026-06-19)** — pending implementation plan.
> Repo: `controlflow-sdk`. Reframes how the SDK is *operated*: a `pip install` + one command
> launches a tiny localhost web app ("control plane") backed by a per-engagement SQLite database,
> replacing hand-edited YAML/Python files as the way you author, run, and export full-population
> control tests. The existing execution/render/bundle core is reused unchanged.

## 1. Summary & motivation

The SDK today is a developer CLI: you `cflow init`, hand-edit `cflow.yaml` / `sources.yaml` /
`controls/<id>/control.yaml` + `test.py` in a text editor, then `cflow run` / `cflow build` in a
terminal. **Authoring is file editing in a terminal.** That works for a developer but is a
non-starter for the corporate GRC analyst the SDK is meant to reach during a consulting engagement —
editing YAML and writing Python in a shell is too much friction to "fly" inside a typical
enterprise.

This design adds a **control plane**: a very light, self-hosted, single-user web app that ships with
the same `pip install`. You run one command, a browser opens, and you author control metadata in
**forms**, define data sources by **selecting files**, build test logic with a **no-code rule
builder** (Python escape hatch for the hard cases), **run** controls over their full population, and
**export** the same import bundle the file-CLI produces today. Everything is backed by a **SQLite
database inside a consistent project folder** — no YAML to hand-edit.

**Strategic fit.** The control plane is the *local authoring surface* of the consulting wedge
(STRATEGY.md "Test-authoring automation" + "Data connectivity: manual upload first-class"): the
owner/operator authors and runs full-population tests on-site, then exports a bundle that imports
into the ControlFlow SaaS where the **CCM loop lives** (exception triage, disposition, self-heal,
sign-off, continuous monitoring). The control plane deliberately **stops at author → run → view →
export**. It is **not** a competing local mini-platform — that "suite trap" is an explicit non-goal
(§11).

## 2. Locked decisions (from the 2026-06-19 brainstorm)

1. **SQLite is the source of truth.** A control "lives" in `controlplane.db`, not in YAML/Python
   files. This mirrors how the ControlFlow app itself stores controls (rows, not files).
2. **No-code rule builder + Python escape hatch.** Metadata is forms; the test is either a
   structured rule-spec (no code) or raw `def test(pop)` Python. Both compile to the **same**
   execution contract.
3. **Scope boundary: author → run → view → export.** No local exception disposition/sign-off.
4. **Server-rendered, no build step.** FastAPI + Jinja2 + HTMX + `sqlite3` (stdlib) + a *vendored*
   offline code editor. No Node, no bundler, no CDN. Pure `pip install`.
5. **Brittle-by-design.** The control plane *trusts the folder-structure convention* and does only
   light validation. The hardened, validated, multi-user experience is what paid ControlFlow is for.
   This is a feature, not debt — it keeps the local tool tiny.

## 3. Architecture

Layered; only the top three layers are new. The existing core is reused essentially unchanged.

```
┌─ Web layer (NEW) ─────────────────────────────┐  controlflow_sdk/plane/
│  FastAPI routes + Jinja templates + HTMX       │   __main__.py · app.py · routes/ ·
│  dashboard · source manager · control editor · │   templates/ · static/ (vendored CodeMirror)
│  run view · export                             │
├─ Store (NEW) ─────────────────────────────────┤  controlflow_sdk/store/
│  sqlite3 (stdlib) + tiny versioned migrator    │   db.py · migrations.py · repo.py
│  project · sources · columns · controls ·      │
│  control_sources · runs · violations           │
├─ Rule engine (NEW) ───────────────────────────┤  controlflow_sdk/rules/
│  rule_spec (JSON) → list[violation dict]       │   spec.py · evaluate.py
│  (vectorized pandas, single-source)            │
├─ Existing core (REUSED) ──────────────────────┤  controlflow_sdk/{runner,render,
│  runner/execute · render/{html,markdown} ·     │   bundle,model,adapters}
│  bundle/{assemble,archive} · model/* ·         │
│  adapters/files (CSV/Parquet/Excel)            │
└────────────────────────────────────────────────┘
```

**Engagement = a folder = one SQLite DB.** One folder per client engagement (matches the consulting
model). The control plane trusts this layout:

```
acme-engagement/
  controlplane.db      ← SOURCE OF TRUTH (controls, rules, sources, runs, violations)
  data/                ← the raw extracts you select / upload (CSV/Parquet/Excel)
    payments.csv
  target/              ← rendered workpapers + the export bundle (regenerable)
    workpapers/
    bundle.zip
```

**Process model.** `controlplane` (run inside the folder, or `controlplane --project acme-engagement`)
ensures the folder + DB exist (runs the migrator), starts `uvicorn` bound to `127.0.0.1:8765`, and
opens the browser. Single process, single user, **zero network egress** — client data never leaves
the machine (the air-gapped guarantee is structural, not a setting).

**Key refactor that enables reuse:** today `runner/execute.run_control` is coupled to the YAML
`project/loader`. Split *loading* (which produces an in-memory `Control` + `list[Population]`) from
*executing*. Provide two loaders behind one shape: the existing YAML loader (now used only by
`cflow import`) and a new **store loader** that reads `controlplane.db`. `run_control` itself —
violation validation, `RunRecord` assembly, provenance hashing, the 1-arg/2-arg signature dispatch —
is unchanged.

## 4. Data model (SQLite)

Plain `sqlite3`, no ORM. A `schema_version` table + an idempotent, forward-only migrator
(`store/migrations.py`) that applies numbered DDL steps. The schema mirrors the bundle/app model so
export (§7) is a straight projection.

| Table | Columns (types) | Notes |
|---|---|---|
| `schema_version` | `version INTEGER` | single row; migrator gate |
| `project` | `name, framework, system, created_at` | single row; the engagement metadata |
| `sources` | `id PK, format, path, key_config (JSON), created_at` | `format ∈ {csv,parquet,excel}`; `path` relative, under `data/`; `key_config = {mode:'single'\|'composite', columns:[…]}` |
| `columns` | `source_id FK, original_name, display_name, data_type, is_key INT, include INT, ordinal` | PK `(source_id, original_name)`; `data_type ∈ {text,number,date,boolean}` |
| `controls` | `id PK, title, objective, narrative, framework_refs (JSON), failure_threshold_pct REAL NULL, failure_threshold_count INT NULL, test_kind, rule_spec (JSON) NULL, test_code TEXT NULL, created_at, updated_at` | `test_kind ∈ {rule,python}`; exactly one of `rule_spec` / `test_code` is set; `framework_refs = {"nist":["AC-2",…]}` |
| `control_sources` | `control_id FK, source_id FK, ordinal` | PK `(control_id, source_id)`; `ordinal 0` = primary (`pop`) |
| `runs` | `run_id PK, control_id FK, executed_at, total, passed, failed, pass_rate REAL, source_hashes (JSON), created_at` | `run_id` = the existing deterministic 16-char id; `source_hashes = {source_id:{sha256,row_count,path}}` |
| `violations` | `id PK AUTOINC, run_id FK, item_key, description, severity, details (JSON)` | one row per violation; identical shape to the Python path's output |

## 5. Rule engine — the unifying execution contract

Both authoring paths converge on **one** contract: a callable/evaluator that returns
`list[dict]` where each dict is `{item_key, description, severity?, details?}` — exactly what
`runner/execute` already consumes. Authoring choice never changes anything downstream.

```
rule_spec (JSON)  ─┐
                   ├─▶  list[violation dict]  ─▶  runner ─▶ workpaper ─▶ bundle
def test(pop) ─────┘        (one contract)         (existing, unchanged)
```

### 5.1 `rule_spec` shape (`test_kind = "rule"`)

```json
{
  "logic": "all",
  "conditions": [
    { "column": "can_create",  "op": "eq", "value": true },
    { "column": "can_approve", "op": "eq", "value": true }
  ],
  "severity": "high",
  "description_template": "User {user_id} has both create and approve permissions",
  "item_key_column": "user_id"
}
```

- `logic`: `"all"` (AND) or `"any"` (OR) across `conditions`.
- `op` (v1 set): `eq, ne, gt, ge, lt, le, is_empty, not_empty, in, not_in, regex, is_duplicate`.
  - `in`/`not_in`: `value` is a list. `regex`: `value` is a pattern (matched as string).
  - `is_empty`/`not_empty`/`is_duplicate`: `value` is ignored. `is_duplicate` flags rows whose
    `column` value appears more than once in the population (`df[col].duplicated(keep=False)`).
- `severity ∈ {low, medium, high, critical}` (default `medium`).
- `description_template`: Python `str.format`-style over the violating row's columns (keyed by
  `original_name`); unknown placeholders are left literal (safe formatter — never raises).
- `item_key_column`: optional; defaults to the source's single key column from `key_config`.
- **Single-source only.** A `rule` control binds exactly one source. Cross-source = Python (§5.3).

### 5.2 Evaluator (`rules/evaluate.py`)

Vectorized over `pop.df`:
1. Build a boolean `Series` per condition.
2. Reduce with `&` (logic=all) or `|` (logic=any) → the violation mask.
3. For each masked row: `item_key = str(row[item_key_column])`; `description =
   safe_format(template, row)`; `severity` from spec; `details = {col: row[col] for col in
   referenced condition columns}`.
4. Return `list[dict]`. No I/O, fully unit-testable, deterministic.

### 5.3 Python escape hatch (`test_kind = "python"`)

The stored `test_code` is the same `def test(pop)` / `def test(pop, sources)` the SDK already
supports (the multi-source 2-arg form ships as of SDK PR #1). It is executed by the existing
runner in a namespace via `exec`. **Cross-source joins live only here** — the rule builder stays
single-source by design. Execution is **not** Pyodide-sandboxed (local trust; brittle-by-design,
§2.5) — it is the operator's own code, on their own machine, against their own data.

## 6. The web app (5 screens, FastAPI + HTMX)

All server-rendered; HTMX swaps partials (add a condition row, run-and-show). Templates in
`plane/templates/`. Vendored CodeMirror under `plane/static/` (offline, no CDN).

1. **Engagement dashboard** (`GET /`) — controls table: title, framework refs, bound sources,
   last-run status + pass-rate, a per-row **Run** button; "New control" / "New source" actions.
2. **Source & file manager** (`GET /sources`, `GET /sources/{id}`) — upload a file into `data/`
   (or pick an existing one); the app reads the header row and renders a **column-mapping form**
   (per column → display name, `data_type`, `is_key`, `include`) → writes `sources` + `columns`.
   Replaces hand-edited `sources.yaml`.
3. **Control editor** (`GET /controls/new`, `GET /controls/{id}`) — two halves:
   - *Metadata (forms):* title, objective, narrative, framework refs, failure threshold (% or
     count), **source binding** (checkboxes; ordinal 0 = primary).
   - *Test logic (tabbed):* **Rule builder** (default) — `WHEN [column][op][value]`, AND/OR
     groups, `THEN flag as [severity]` + description template; **Python** — CodeMirror box.
4. **Run view** (`GET /controls/{id}/runs/{run_id}`) — pass/fail totals, the violations table, and
   the rendered workpaper **embedded inline** via the SDK's existing `render_html` (the exact
   document the bundle ships).
5. **Export** (`GET /export`) — one button → `target/bundle.zip` via `bundle/assemble` + download.

**Routes that mutate:** `POST /sources`, `POST /sources/{id}`, `POST /controls`,
`POST /controls/{id}`, `POST /controls/{id}/run`, `POST /export`. HTMX posts are same-origin; no
CSRF token needed for a localhost single-user app.

## 7. Run, export & security

**Run.** `POST /controls/{id}/run` loads the control + bound populations from the store, executes
full-population (pandas, in-process, serialized) via the existing runner, writes a `runs` row +
`violations` rows, renders the workpaper to `target/workpapers/`, and redirects to the run view.
Determinism: an optional `executed_at` (the headless `--at` equivalent) keeps demo/test output
stable.

**Export — reuse, never fork.** The bundle is produced by the existing `bundle/assemble` +
`bundle/archive` and **must validate against `contract/bundle.schema.json`**, which stays the single
source of truth for the import shape. The control plane is simply a **new producer** of the same
bundle the file-CLI emits, so ControlFlow imports it unchanged (Settings → Imports). A test asserts
conformance against the contract schema (§10).

**Security.** Binds `127.0.0.1` only (`--host`/`--port` override). No auth (localhost single-user
trust). Zero network egress. User-Python runs unsandboxed by design (§5.3).

## 8. CLI fate & migration (brittle-by-design)

SQLite is now the source of truth, so the file-based authoring CLI is superseded:

- **Retire** `cflow init` / `cflow new` (file scaffolding) — replaced by the web app's forms and by
  `controlplane` auto-creating the folder + DB.
- **Keep** `cflow run [dir]` and `cflow build [dir]` as the **headless/automation** path, now
  operating over `controlplane.db` (for the owner's CI/batch re-runs).
- **Add** `cflow import <yaml-project-dir>` — a one-time YAML-project → `controlplane.db` import,
  reusing the existing `project/loader` as its reader.
- `cflow validate` becomes a light **DB integrity** check (bindings resolve, exactly one test body
  per control). Low priority.

**The Northwind example** stays committed as a YAML project under `examples/northwind-trading/` and
becomes the canonical `cflow import` demo: `cflow import examples/northwind-trading` (or a first-run
prompt) seeds a populated control plane instantly. Its end-to-end CI test now exercises the **import
→ run → build** path so the example stays green.

## 9. Packaging & distribution

- `pyproject.toml`:
  - `[project.optional-dependencies] plane = ["fastapi", "uvicorn", "jinja2", "python-multipart"]`
  - `[project.scripts] controlplane = "controlflow_sdk.plane.__main__:main"` (keep `cflow`).
- `controlplane` flags: `--project PATH` (default cwd), `--host` (default `127.0.0.1`), `--port`
  (default `8765`), `--no-browser`.
- CodeMirror vendored as a static asset in the wheel (offline). No Node, no build step — `pip
  install 'controlflow-sdk[plane]'` is the entire install.

## 10. Build sequence — five independently-testable phases

| Phase | Deliverable | Tested by |
|---|---|---|
| 1 | `store/` — SQLite schema, versioned migrator, repo CRUD, model mapping; **decouple `run_control` loading from execution** + a store loader | pytest: CRUD round-trips, migration up, schema-version gate, store-loader produces `Control`+`Population` |
| 2 | `rules/` — `rule_spec` validation + evaluator | pytest: each operator, AND/OR, safe template, `item_key` default, `is_duplicate`, empty result |
| 3 | Headless `cflow run`/`build` over the store + `cflow import` (YAML→DB) | pytest: run determinism (fixed `executed_at`); **bundle validates vs `contract/bundle.schema.json`**; import round-trip on Northwind |
| 4 | `plane/` — FastAPI app, 5 screens, HTMX, vendored CodeMirror | pytest `TestClient`: create source → create control (rule + python) → run → view → export |
| 5 | Packaging (`[plane]` extra + `controlplane` entry), Northwind imported demo, README rewrite | manual smoke: `pip install '.[plane]'` → `controlplane` → author/run/export in browser |

`ruff` + `mypy` stay green throughout. The store and rule engine are pure and trivially
unit-tested; web routes via `TestClient`.

## 11. Non-goals (explicit — strategy guardrails)

- **No local exception lifecycle** — triage, disposition, self-heal, sign-off live in ControlFlow.
  The control plane stops at author → run → view → export.
- **Not a competing platform.** It produces bundles for ControlFlow; it does not reimplement the
  SaaS. (STRATEGY.md "suite trap" litmus.)
- **No multi-tenant, no auth, no RLS** — single-user, localhost.
- **No network connectors in v1** — files only (CSV/Parquet/Excel). Live feeds are the SaaS's job.
- **No cross-source in the rule builder** — Python escape hatch only.
- **No Pyodide sandbox** — local trust (brittle-by-design).

## 12. Future (out of scope for v1)

- **AI-assisted authoring** — draft a `rule_spec` or Python test from the control objective + a data
  sample. This is the strategy's north-star ("manual → AI-assisted → AI-authored"); layered on later
  as its own step over the same execution contract.
- **Multi-source rule builder** (declarative joins).
- **Live connectors** (S3/Snowflake/REST) — already in the SaaS.
- **Scheduling** of local re-runs.
```
