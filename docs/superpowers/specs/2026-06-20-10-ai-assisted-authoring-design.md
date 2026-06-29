# Spec: AI-assisted authoring — draft a rule_spec from objective + data sample (issue #10)
**Issue:** #10 · **Date:** 2026-06-20 · **Status:** approved-design

## Problem (1–3 sentences)
Authoring a no-code rule control today means hand-building conditions in the rule builder against a free-text column name. We want an opt-in "Draft with AI" affordance on the control editor that, from the control's objective plus the bound source's schema and a small data sample, proposes a `rule_spec` the author reviews and edits in the existing builder. The draft must pass our own `parse_rule_spec` + run-on-sample gate before it can be accepted, so a bad draft can never be saved blind, and no provider is ever called unless the author explicitly selected one and its key/env is present (offline by default).

## Locked decisions
(Settled — implemented exactly as written.)
- Provider-agnostic `Provider` seam with three backends — Anthropic (default), OpenAI, local (Ollama). Protocol method `draft_rule_spec(objective, source_schema, data_sample) -> dict`. Each backend returns a raw dict; OUR `parse_rule_spec` + run-on-sample is the validation gate.
- Output is `rule_spec` ONLY (no Python-test generation). Python fallback stays manually authored.
- Secrets come from ENV VARS (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OLLAMA_HOST`). An in-app settings panel only PICKS the active provider + model; never store keys in the browser/SQLite. No provider call unless a provider+model is selected AND its key/env is present. Offline by default.
- Anthropic backend uses the official `anthropic` SDK (not raw HTTP). Default model `claude-opus-4-8`. Structured outputs via `output_config={"format":{"type":"json_schema","schema": RULE_SPEC_JSON_SCHEMA}}` with `additionalProperties:false`; adaptive thinking allowed. OpenAI backend uses its JSON/structured mode; Ollama uses prompt + JSON parse. Shared contract is "returns a dict" — always re-validated.
- Optional `[ai]` extra in pyproject for `anthropic` (+ `openai`). Pyodide-safe CORE must NOT gain a hard dep — AI code lives in a new `uticen_lite/ai/` module behind the extra, imported lazily so the app runs without it.
- Bundle/contract UNCHANGED — AI only produces a `rule_spec` that flows through the existing save path.
- Honor cardinal rule 0001, learnings 0002 (per-handler conn on POST), 0003 (optional extra/lazy import), 0005/0007 (UI tokens, server-rendered).

## Design

### Architecture overview
Three layers, all behind the `[ai]` extra and lazily imported:

1. **`uticen_lite/ai/` (new module, provider seam + validation gate).** Pure-Python, no FastAPI. Holds the `Provider` protocol, the three backends, the shared `RULE_SPEC_JSON_SCHEMA`, prompt assembly, and the provider-agnostic `draft_and_validate(...)` orchestrator that calls a backend, runs `parse_rule_spec`, and runs the spec on a small in-memory sample. Lazy `import` of `anthropic` / `openai` happens inside each backend's `draft_rule_spec`, never at module import.
2. **`uticen_lite/plane/routes/ai.py` (new route module).** One POST endpoint `/controls/ai/draft` that gathers objective + bound-source schema + data sample, calls the orchestrator, and returns an HTMX partial that the control editor swaps into the rule-builder pane. Plus a `/settings/ai` GET+POST pair for the provider/model picker. Per-handler connection on writes (0002).
3. **Control-editor UI.** A "Draft with AI" button in the Test-logic card (rule pane only) that `hx-post`s the current objective + selected sources, targeting the rule-builder container; the server returns a re-rendered `partials/rule_builder.html` prefilled from the validated draft (or an inline error banner). A small settings panel at `/settings/ai`.

### Data flow (happy path)
```
control editor (rule pane)
  └─ "Draft with AI" → hx-post /controls/ai/draft
        form: objective, source_ids[] (and control_id if editing)
  → routes/ai.draft_rule (per-handler conn):
       1. read active provider+model from store (settings); if none → 200 partial "AI not configured"
       2. resolve key/env presence for that provider; if absent → 200 partial "AI not enabled"
       3. primary source = first bound source; get_source(conn, sid) → source_schema
       4. load a capped data sample for that source via the runner sampler
       5. ai.draft_and_validate(objective, source_schema, data_sample, provider, model)
            a. backend.draft_rule_spec(...) → raw dict
            b. parse_rule_spec(raw)            → RuleSpec  (raises RuleSpecError on bad shape)
            c. evaluate_rule(spec, sample_pop) → list[dict] (proves it runs; raises → caught)
       6. render partials/rule_builder.html with control-shaped ctx carrying the draft
  → HTMX swaps the rule-builder pane; author reviews/edits, then submits the normal form
```
No new persistence: the draft is rendered straight into the existing form fields; the author still clicks "Save changes"/"Create control", which goes through the unchanged `create_control`/`update_control` → `_save_from_form` → `_rule_spec_from_form` → `repo.upsert_control` path.

### Files to create

**`uticen_lite/ai/__init__.py`**
Public surface, all import-safe without the extra:
```python
from .draft import RULE_SPEC_JSON_SCHEMA, DraftError, draft_and_validate
from .providers import Provider, available_providers, provider_key_present
```

**`uticen_lite/ai/providers.py`**
```python
PROVIDERS: dict[str, dict] = {
    "anthropic": {"label": "Anthropic", "default_model": "claude-opus-4-8",
                  "models": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
                  "env": "ANTHROPIC_API_KEY"},
    "openai":    {"label": "OpenAI", "default_model": "gpt-4o",
                  "models": ["gpt-4o", "gpt-4o-mini"], "env": "OPENAI_API_KEY"},
    "ollama":    {"label": "Local (Ollama)", "default_model": "llama3.1",
                  "models": ["llama3.1", "mistral"], "env": "OLLAMA_HOST"},
}

class Provider(Protocol):
    def draft_rule_spec(self, objective: str, source_schema: dict,
                        data_sample: dict, *, model: str) -> dict: ...

def provider_key_present(provider: str) -> bool:
    """True if the env var for *provider* is set (Ollama defaults to localhost)."""
    # ollama: True if OLLAMA_HOST set OR fall back to http://localhost:11434
    ...

def available_providers() -> list[dict]:
    """[{id,label,models,default_model,enabled}] — enabled = key/env present."""
    ...

def get_provider(provider: str) -> Provider:
    """Lazy-construct a backend; imports the SDK inside the constructor."""
    ...
```
- `_AnthropicProvider.draft_rule_spec`: `import anthropic` inside the method; `client = anthropic.Anthropic()` (reads `ANTHROPIC_API_KEY`); `resp = client.messages.create(model=model, max_tokens=2000, thinking={"type":"adaptive"}, system=_SYSTEM_PROMPT, messages=[{"role":"user","content": _user_prompt(...)}], output_config={"format":{"type":"json_schema","schema": RULE_SPEC_JSON_SCHEMA}})`; extract first text block, `json.loads`. (Per claude-api skill: `output_config.format` is the canonical structured-output param; `claude-opus-4-8` is the exact model id; adaptive thinking is the supported mode.)
- `_OpenAIProvider`: `import openai` inside the method; chat completion with `response_format={"type":"json_schema","json_schema":{"name":"rule_spec","schema": RULE_SPEC_JSON_SCHEMA,"strict": True}}`; parse `choices[0].message.content`.
- `_OllamaProvider`: `import urllib.request`/stdlib only (no extra dep); POST to `${OLLAMA_HOST or http://localhost:11434}/api/generate` with `format:"json"`, the prompt embeds the schema as text; parse `response` field as JSON. Keeps Ollama dependency-free.

**`uticen_lite/ai/draft.py`**
```python
RULE_SPEC_JSON_SCHEMA = {
  "type": "object", "additionalProperties": False,
  "required": ["logic", "conditions"],
  "properties": {
    "logic": {"type": "string", "enum": ["all", "any"]},
    "severity": {"type": "string", "enum": ["low","medium","high","critical"]},
    "description_template": {"type": "string"},
    "item_key_column": {"type": ["string", "null"]},
    "conditions": {"type": "array", "items": {
       "type": "object", "additionalProperties": False,
       "required": ["column", "op"],
       "properties": {
         "column": {"type": "string"},
         "op": {"type": "string", "enum": sorted(OPERATORS)},   # from rules.spec
         "value": {"type": ["string","number","boolean","array","null"]},
       }}},
  },
}

class DraftError(Exception): ...

def draft_and_validate(*, objective, source_schema, data_sample,
                       provider, model) -> dict:
    raw = get_provider(provider).draft_rule_spec(
        objective, source_schema, data_sample, model=model)
    spec = parse_rule_spec(raw)                 # → RuleSpecError on bad shape
    _run_on_sample(spec, source_schema, data_sample)   # proves it executes
    return raw   # validated dict, fed to the rule builder
```
- `_run_on_sample` builds a tiny in-memory `Population` from the sample rows + column metadata and calls `evaluate_rule(spec, pop)`. Construct the `Population` directly from a `pandas.DataFrame` of the sample (the sample is already capped and stringified). If `evaluate_rule` raises (e.g. unknown column, regex error), wrap as `DraftError("the drafted rule did not run on your data: ...")`. **Validation is provider-agnostic — it is the same gate for all three backends.**
- `referenced_columns(spec)` cross-checked against the schema's `original_name`s; any column not present → `DraftError` with a clear message (the model hallucinated a column).
- Prompt assembly (`_SYSTEM_PROMPT`, `_user_prompt`): system prompt states the operator vocabulary (the `OPERATORS` set with one-line meanings, mirroring `render_rule._BINARY/_SET/_UNARY`), that it must reference only the listed columns by their exact `original_name`, and that output must match the schema. User prompt embeds: the objective; a compact column table (`original_name`, `display_name`, `data_type`); and up to ~20 sample rows (truncated from the passed sample). Keep token use modest.

**`uticen_lite/plane/routes/ai.py`**
```python
def register(app, templates, get_conn):
    @app.get("/settings/ai", response_class=HTMLResponse)
    def ai_settings(request, conn=Depends(get_conn)):
        # render settings_ai.html with available_providers() + saved selection
    @app.post("/settings/ai")
    async def save_ai_settings(request):          # per-handler conn (0002)
        # persist provider+model into project.system["ai"] (store-only; see below)
    @app.post("/controls/ai/draft", response_class=HTMLResponse)
    async def draft_rule(request):                # per-handler conn (0002)
        form = await request.form()
        objective = form.get("objective","")
        source_ids = form.getlist("source_ids")
        # 1. read saved provider/model; 2. guard provider_key_present;
        # 3. primary sid = source_ids[0]; get_source; build schema+sample;
        # 4. lazy: from uticen_lite import ai (ImportError → "install [ai]") ;
        # 5. draft_and_validate; on DraftError/RuleSpecError → error partial (200);
        # 6. success → render partials/rule_builder.html with a synthetic
        #    `control` ctx whose .rule_spec == validated draft + .test_kind=="rule".
```
- **Settings storage:** reuse the existing `project.system` JSON column (loaded/saved by `repo.upsert_project`/`get_project`). Store `system["ai"] = {"provider": ..., "model": ...}`. This is store-only display/config state — it is NEVER threaded into `to_data_source()` or the bundle (consistent with 0006's "store-only authoring state" rule). No migration needed; `system` already exists.
- **Data sample acquisition (reuse, don't fork):** add a thin helper to `runner/execute.py` OR call the existing path. Preferred: in `routes/ai.py`, load the project via `load_project_from_store(conn)`, find the `SourceBinding` for the primary sid, and call `source_for(binding, root).load()` to get a `Population`; build the sample dict `{columns:[original_name...], rows:[[...]], schema:[{original_name,display_name,data_type}...]}` capping at e.g. 20 rows. This mirrors `collect_data_samples` but returns the schema+rows shape the AI layer needs. If a source has no data file yet, return an error partial ("bind a data file first").

**`uticen_lite/plane/templates/settings_ai.html`** (extends `base.html`)
- A `card` listing the radio of available providers (disabled radios for providers whose env is absent, with a hint "set `ANTHROPIC_API_KEY` to enable"), a model `<select>` per provider, and a Save button. Offline-by-default copy: "No data leaves your machine unless you select a provider and its key is present." Route all colors through `var(--token)` and support `[data-theme=light]` (0005).

**`uticen_lite/plane/templates/partials/ai_draft_error.html`** (fragment)
- A small inline banner (`.notice` / token-driven) showing the `DraftError`/guard message; swapped into the rule-builder container so the author sees why no draft appeared. No raw provider stack traces.

### Files to modify

**`pyproject.toml`**
- Add `ai = ["anthropic>=0.50", "openai>=1.40"]` to `[project.optional-dependencies]`.
- Add the same to `dev` so tests can import the seam (but tests must still pass with the SDKs absent via monkeypatched fake backends — see Testing).
- No `force-include` / packaging change (the AI module is package-internal, shipped by `packages=["uticen_lite"]`).

**`uticen_lite/plane/app.py`**
- Register the new route module: `from uticen_lite.plane.routes import ai` and `ai.register(app, templates, get_conn)` after `controls.register(...)`. The route module itself never imports the `[ai]` SDKs at import time, so the app starts without the extra.

**`uticen_lite/plane/templates/control_edit.html`**
- In the `data-pane="rule"` block, wrap the `{% include "partials/rule_builder.html" %}` in a container `<div id="rule-builder-pane">` and add a "Draft with AI" button above it:
  ```html
  <button class="btn btn-sm btn-ghost" type="button"
          hx-post="/controls/ai/draft"
          hx-include="#f-obj, input[name=source_ids]"
          hx-target="#rule-builder-pane" hx-swap="innerHTML">
    Draft with AI
  </button>
  ```
  Gate its visibility/copy on whether AI is configured (pass an `ai_enabled` flag into the editor context from `new_control`/`edit_control`); when disabled, render it as a link to `/settings/ai`. The button posts the live objective + checked sources; the response is a re-render of `partials/rule_builder.html` into the pane.

**`uticen_lite/plane/routes/controls.py`**
- In `new_control` and `edit_control`, add `"ai_enabled": _ai_configured(conn)` to the template context, where `_ai_configured` reads `project.system.get("ai")` and checks `provider_key_present`. This is a read-only GET, so it may use the `Depends(get_conn)` connection (0002).

**`uticen_lite/plane/templates/base.html`**
- Add a `Settings` nav link (or a small gear in `app-header-tools`) pointing at `/settings/ai`, shown only when `project.name` is set, matching existing nav-link styling.

### Key signatures recap
- `ai.draft_and_validate(*, objective: str, source_schema: dict, data_sample: dict, provider: str, model: str) -> dict`
- `ai.providers.provider_key_present(provider: str) -> bool`
- `ai.providers.available_providers() -> list[dict]`
- `Provider.draft_rule_spec(objective, source_schema, data_sample, *, model) -> dict`
- Route: `POST /controls/ai/draft` → HTMLResponse (rule-builder partial or error fragment)
- Route: `GET/POST /settings/ai`

## Bundle / contract impact
**UNCHANGED.** The AI layer produces a `rule_spec` dict only; it is rendered into the existing rule-builder form and saved through the unchanged `_save_from_form` → `repo.upsert_control` path. No new manifest fields, no producer change in `bundle/assemble.py`/`archive.py`. Provider/model selection is stored only in the store-only `project.system` JSON and is never carried into `to_data_source()` or the bundle (consistent with learning 0006). Data samples are loaded transiently in-handler, sent to the chosen provider only when the author opts in, and never persisted or placed in the bundle — the no-raw-rows trust boundary (0001) is preserved on the export side; sending a sample to a provider is the author's explicit, opt-in choice. `contract/bundle.schema.json` and the contract tests are untouched.

## Testing
TDD targets (write tests first; suite must stay green and pristine, ruff + mypy clean).

**Unit — `tests/ai/` (new dir, `__init__.py`):**
- `test_draft.py`:
  - `draft_and_validate` with a **fake provider** (a `Provider` returning a fixed dict) over a 2+ record in-memory sample (per 0004, use ≥2 rows): asserts the validated dict round-trips through `parse_rule_spec` and that a spec which flags ≥1 sample row runs.
  - Bad-shape draft (unknown `op`) → `RuleSpecError` surfaces (not silently saved).
  - Hallucinated column (`column` not in schema) → `DraftError` with a column-not-found message.
  - A spec that raises in `evaluate_rule` (e.g. `regex` with a bad pattern) → `DraftError`.
  - `RULE_SPEC_JSON_SCHEMA` is valid JSON-Schema, has `additionalProperties: false` at both levels, and its `op` enum equals `sorted(OPERATORS)` (guards drift from `rules/spec.py`).
- `test_providers.py`:
  - `provider_key_present` true/false driven by `monkeypatch.setenv`/`delenv` for each provider; Ollama defaults to enabled (localhost) and respects `OLLAMA_HOST`.
  - `available_providers()` marks each provider enabled iff its env is present.
  - **No-extra safety:** importing `uticen_lite.ai` and calling `available_providers()`/`provider_key_present` works with `anthropic`/`openai` NOT installed (the SDK import is inside `draft_rule_spec`, exercised only via the fake backend). Simulate absence by asserting the public functions never import the SDKs (e.g. monkeypatch `builtins.__import__` to fail on `anthropic` and confirm `available_providers()` still returns).

**Route/integration — `tests/plane/test_ai.py` (new), using the existing `client`/`engagement` fixtures (`tests/plane/conftest.py`):**
- `POST /controls/ai/draft` with **no provider configured** → 200 with an "AI not configured" partial (offline default; no exception).
- With provider configured but **env absent** (monkeypatch `delenv`) → 200 "AI not enabled" partial.
- With provider configured + env present + provider monkeypatched to a fake backend that returns a valid spec → 200 partial containing rule-builder markup prefilled with the drafted conditions (assert `name="cond_column"` value matches a real source column). Reuse the `_make_source` helper pattern from `test_controls.py`.
- Fake backend returns a bad dict → 200 error partial; control is NOT saved (no new control row).
- `GET /settings/ai` renders providers; `POST /settings/ai` persists selection into `project.system["ai"]` (assert via `repo.get_project`).
- Control editor `GET /controls/new` shows the "Draft with AI" affordance only when configured (assert link-to-settings when not).

**Existing files touched by tests:** `tests/plane/test_controls.py` (add an `ai_enabled` context assertion if convenient), `tests/plane/conftest.py` (fixtures reused as-is). All new provider calls in tests go through a monkeypatched fake `Provider`, so the suite never makes a network call and passes without the `[ai]` SDKs installed.

**Packaging:** `tests/plane/test_packaging.py` already builds the wheel; add an assertion that `uticen_lite/ai/` modules ship in the wheel (they are package-internal, no force-include needed) and that the wheel imports with the `[ai]` extra absent.

## Non-goals / out of scope
- Python-test (`test_code`) generation — explicitly excluded; the Python escape hatch stays manually authored.
- Storing or transmitting API keys anywhere (browser, SQLite, bundle) — keys live only in env vars read by the SDK at call time.
- Multi-source cross-joins for drafting — the draft uses the **primary (first-bound) source** only, matching the single-source `rule_spec`/`evaluate_rule` substrate. No cross-source primitive is introduced.
- Streaming the draft, chat-style iteration, or "explain this rule" — single request → single proposed spec.
- Auto-saving the AI draft — the author always reviews and explicitly submits the normal form.
- Any change to the bundle schema, `assemble_bundle`, or the export path.

## Risks & mitigations
- **Core gains a hard dep (Pyodide break).** Mitigation: `anthropic`/`openai` imported only inside backend methods; the route module and `uticen_lite.ai` package import cleanly without the extra; a test asserts public functions don't import the SDKs (learning 0003).
- **Model hallucinates a column or a malformed spec.** Mitigation: every draft passes `parse_rule_spec` + `referenced_columns` schema check + `evaluate_rule` on the sample before reaching the form; failures render a friendly error partial, never a saved control. This gate is identical across all three providers.
- **Cross-thread sqlite on POST handlers (0002).** Mitigation: `/controls/ai/draft` and `/settings/ai` POST open a per-handler `connect(root)` in a try/finally; only sync GETs use `Depends(get_conn)`.
- **Accidental network call when "offline by default" is expected.** Mitigation: no provider is constructed and no `draft_rule_spec` is called unless (a) a provider+model is saved AND (b) `provider_key_present` is true; both guards run before any backend import. Tests cover the not-configured and env-absent paths returning 200 partials with zero provider interaction.
- **Structured-output API drift / wrong model id.** Mitigation: use the exact `output_config={"format":{"type":"json_schema","schema": ...}}` shape and `claude-opus-4-8` per the claude-api skill; `RULE_SPEC_JSON_SCHEMA` uses only supported constructs (`enum`, `additionalProperties:false`, basic types — no numeric/length constraints) so it stays within structured-output limits.
- **Op-enum drift between schema and engine.** Mitigation: derive the schema's `op` enum from `rules.spec.OPERATORS` and assert equality in a unit test, so adding an operator can't silently desync the AI schema.

## Resolved open questions (2026-06-20)
- **Picker default models:** Anthropic `claude-opus-4-8` (locked); OpenAI default `gpt-4o`; Ollama default `llama3.1`. All are editable defaults in the picker — surface them as the prefilled value, user-overridable.
- **Settings storage:** reuse `project.system['ai']` JSON (no new migration), consistent with the store-only authoring-state pattern (learning 0006). No dedicated table.
- **Prompt sizing:** start at ~20 sample rows and `max_tokens` ~2000 (a rule_spec is small); these are tunable starting values, not load-bearing.
