# Spec — Multi-format sources: Excel & Parquet uploads + one-time URL snapshot importer

> Status: ready to implement. Relates to issue **#9** (no-code authoring usability) — widens the
> on-ramp so an analyst can bring the data they actually have (an Excel export, a Parquet extract,
> or a REST endpoint) without first hand-converting it to CSV. Branch: `worktree-multi-format-sources`.

## 1. Why

The control plane is pitched as the local authoring surface where an analyst brings a messy
real-world dataset and turns it into a full-population test (`STRATEGY.md` — the wedge). But today
the **only** data an analyst can bring through the web app is **CSV**. The upload, refresh, and
preview paths in `plane/routes/sources.py` are hardcoded to the stdlib `csv` module
(`_header_of`, `_row_count`, the `source_data` preview reader, the refresh column-diff). An analyst
whose system of record exports `.xlsx` (the overwhelmingly common case in finance/GRC) or whose data
sits behind an internal REST endpoint has to convert by hand before they can even start — friction
exactly where the strategy says to remove it.

The SDK **engine already supports all three file formats** — `adapters/files.py` ships working
`CsvSource`, `ParquetSource`, and `XlsxSource` (with sheet selection). `STRATEGY.md` lists
"CSV / Parquet / Excel" as **in-scope** file-first data. The gap is purely that the web layer never
wired the non-CSV adapters through. This spec closes that gap and adds a **one-time URL
snapshot importer** that fetches a REST/HTTP endpoint once, freezes the response to a local file, and
hands it to the same file-first ingestion.

## 2. North-star / strategy fit

- **In scope, already declared.** Excel & Parquet are named in-scope formats; this is wiring an
  existing engine capability through the primary authoring surface.
- **REST stays a non-goal — reinterpreted, not violated.** `STRATEGY.md` says *"Not live connectors.
  S3 / Snowflake / REST feeds are the SaaS's job; the SDK is file-first."* The importer is **not a
  live connector**: it fetches **once**, on an explicit user action, and **snapshots the response to
  a local file** that becomes the source of truth. There is no polling, no scheduled refresh, no
  background read. The snapshot file — not the endpoint — is what every run reads. This honors the
  user's intent ("bring data from a REST API") while keeping the SDK file-first.
- **Authoring ladder.** Lowers friction on the bottom rung (getting data in) so more of an
  engagement's effort goes to authoring the test, not massaging file formats.

## 3. Cardinal rule (bundle contract) — untouched

Nothing here touches `contract/bundle.schema.json`. Format, sheet, the source URL, and any
credentials are **local run/authoring state**, never serialized into the bundle (the bundle carries
definitions + run provenance, never raw data and never connection config). The existing
contract gate (`tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py`) must stay
green and unchanged — that is the proof the bundle shape did not move. Per learnings
[0006](../../learnings/0006-evolve-source-state-without-touching-the-bundle.md) /
[0010](../../learnings/0010-new-authoring-representation-compiles-to-the-existing-artifact.md), new
source state lives in store-only columns/tables; `sources.extract_date` remains the only
bundle-facing source field and keeps mirroring the current file's as-of date.

## 4. Architecture — two seams, one convergence point

Both halves converge on the **existing** create-source / file-version flow in
`plane/routes/sources.py`. Two new, independently-testable seams are introduced.

### Seam 1 — `extract_table` (format-aware funnel)

New module `controlflow_sdk/plane/ingest.py`:

```python
def extract_table(raw: bytes, fmt: str, *, sheet: str | int | None = None) -> ExtractedTable
# ExtractedTable = (header: list[str], rows: list[list[str]], sheet_names: list[str])
```

The single replacement for the four CSV-hardcoded helpers. It returns the header, the data rows as
lists of **strings** (matching what `coercion_report` and the preview already consume), and — for
xlsx — the available sheet names.

- **CSV** → stdlib (`csv` module), exactly as today. **No new dependency** for the CSV path, so a
  minimal `[plane]` install keeps working.
- **xlsx / parquet** → lazy-import a new `controlflow_sdk/adapters/inspect.py` that owns the
  pandas-touching reads (`read_dataframe(raw, fmt, sheet) -> pd.DataFrame`, `sheet_names(raw) ->
  list[str]`). **pandas stays strictly in `adapters/`** per the `STRATEGY.md` constraint
  (Pyodide-safe core). The DataFrame is read as all-strings where possible and converted to
  header+rows by `ingest.py`.
- **`[adapters]` absent** → raise a typed `AdaptersUnavailable` that the routes catch and render as a
  friendly "Excel/Parquet support needs `pip install controlflow-sdk[adapters]`" message on the
  upload page — never a 500.

### Seam 2 — `fetch_snapshot` (one-time URL fetch)

New module `controlflow_sdk/plane/fetch.py`:

```python
def fetch_snapshot(
    url: str, *, headers: dict[str, str] | None = None,
    record_path: str | None = None, opener: Opener | None = None,
) -> FetchedSnapshot
# FetchedSnapshot = (raw: bytes, fmt: str, suggested_name: str,
#                    source_url: str, fetched_at: str)
```

- One **GET** via stdlib `urllib.request` — **no new runtime dependency**. The `opener` is
  **injectable** (defaults to the real urllib opener) so tests never touch the network, mirroring the
  injectable-spawn discipline of learning
  [0016](../../learnings/0016-self-replacing-process-upgrades-via-a-detached-helper.md).
- **Format inference** from `Content-Type` then URL extension:
  - `application/json` (or `.json`) → flatten to **CSV bytes** (stdlib `json` + `csv`). Records are a
    top-level JSON array, or the array located by a dotted `record_path` (e.g. `data.items`). Each
    record must be a flat object; non-scalar values are JSON-encoded into their cell. `fmt` becomes
    `csv`. (Deep/nested normalization via `pd.json_normalize` is a deliberate **non-goal for v1** —
    keeps the common REST case dependency-free; revisit if real data needs it.)
  - `text/csv` / `.csv` → snapshot bytes as-is, `fmt=csv`.
  - `.xlsx` / `.parquet` (by content-type or extension) → snapshot bytes as-is; reading them for
    inference still requires `[adapters]` (same friendly-error path as Seam 1).
- **Errors** (non-200, unreachable host, decode failure, empty/!-array JSON, missing `record_path`)
  surface as a typed `FetchError` rendered on the form — never a 500.

### Convergence

```
Upload file ─┐
             ├─→ raw bytes + fmt ─→ extract_table ─→ header/rows/count ─→ [existing create / refresh→confirm flow]
Fetch URL  ──┘                                                                   ↑ writes data/<file>, records file version
```

Every path — CSV/xlsx/parquet upload, and every URL fetch (first or re-fetch) — **writes a local
file under `data/`** and records a file version in the existing `source_files` lineage. The endpoint
is read once per explicit action and never again.

## 5. Half A — Excel & Parquet uploads

1. **Route `extract_table` everywhere CSV is hardcoded** in `plane/routes/sources.py`:
   `create_source` (header inference + row count), `refresh_source` + `confirm_refresh` (column diff +
   count), and `source_data` (the paged preview + `coercion_report` input).
2. **Infer format from the uploaded filename extension** (`.csv` / `.xlsx` / `.parquet`), not a
   trusted `format` form field. Persist it to `sources.format` (already `csv|parquet|xlsx`). Legacy
   `.xls` is **out of scope** (needs `xlrd`) and rejected with a clear message.
3. **Excel sheet selection, end-to-end:**
   - Store **migration step 6**: `ALTER TABLE sources ADD COLUMN sheet TEXT;` (store-only, NULL ⇒
     first sheet; bumps `user_version` to 6, **not** `schema_version`).
   - `repo.upsert_source(..., sheet: str | None = None)` persists it (add `sheet=excluded.sheet` to
     the upsert).
   - `store/loader.py::_binding` and `store/import_service.py` thread `sheet` into the runtime
     `SourceBinding.config` **when present**, so a control's **run** reads the chosen sheet. (Today
     `XlsxSource` honors `config["sheet"]` but the store never passes it — runs silently always read
     sheet 0. This fixes that latent gap.)
   - The upload UI shows a **sheet dropdown only when the uploaded `.xlsx` has >1 sheet**
     (`extract_table` returns `sheet_names`).
4. **`[adapters]` guard:** a non-CSV upload with the extra absent renders the friendly install
   message; no 500.

## 6. Half B — One-time URL snapshot importer

1. **Two upload modes** on the add-source page (`source_new.html`): *Upload file* (today) and *Fetch
   from URL*. The URL form fields: URL, optional request headers / auth token, optional JSON
   `record_path`, optional sheet (for an `.xlsx` endpoint).
2. **Create flow:** `fetch_snapshot` → write bytes to `data/<suggested_name>` → funnel into the same
   create path (`extract_table` → infer columns → `upsert_source` + `set_initial_file`). The fetch
   provenance (URL, headers, record_path, `last_fetched_at`) is saved to a new store-only
   `source_fetch` table (see §7).
3. **Re-fetch:** a **"Re-fetch from URL"** button on the Data tab re-runs `fetch_snapshot` using the
   **stored** url + headers, stages the result as a pending snapshot, and routes through the
   **existing `refresh → column-diff → confirm` flow** — so a changed remote schema is reviewed by the
   user, never silently applied. Every fetch is a saved, versioned file with its source URL recorded
   in History.
4. **No automatic/background fetch anywhere.** A fetch happens only on an explicit button click.

## 7. Secrets at rest — explicit, persisted, warned

Per the user's directive, credentials **are persisted** (so re-fetch is one click, no re-entry), with
the risk surfaced loudly.

- **New store-only table** `source_fetch` (one row per URL-backed source), isolating the
  secret-bearing blob from the clean `sources` table:
  `source_id PK · url TEXT · headers TEXT (JSON, may contain auth) · record_path TEXT ·
  last_fetched_at TEXT`. Created in **migration step 6** alongside the `sheet` column.
- **Prominent warning callout** rendered wherever a token is entered or stored — the *Fetch from URL*
  form, and the source's Data/History views when a `source_fetch` row exists:
  > ⚠ Credentials you enter here are stored **in plaintext** in `controlplane.db`. Anyone who can read
  > that file (or your disk backups) can read them. Only use this on a machine where data at rest is
  > protected.
- **Never in the bundle.** The `source_fetch` table is store/UI-only; no URL, header, or token is
  serialized into the import bundle (trust boundary intact). Egress is **always user-initiated**
  (learning [0017](../../learnings/0017-opt-in-egress-must-default-off-reword-the-claim-and-prove-off-makes-no-call.md)
  carve-out) — no global egress toggle needed, but the new secrets-at-rest risk is captured as a
  learning this cycle.

## 8. Components & files

| File | Change |
| --- | --- |
| `controlflow_sdk/plane/ingest.py` | **New.** `extract_table` + `ExtractedTable` + `AdaptersUnavailable`. CSV stdlib; xlsx/parquet via lazy `adapters.inspect`. |
| `controlflow_sdk/adapters/inspect.py` | **New.** `read_dataframe(raw, fmt, sheet)`, `sheet_names(raw)` — the only pandas in this feature. |
| `controlflow_sdk/plane/fetch.py` | **New.** `fetch_snapshot` + `FetchedSnapshot` + `FetchError`; injectable `opener`; JSON→CSV (stdlib). |
| `controlflow_sdk/plane/routes/sources.py` | Replace the 4 CSV-hardcoded helpers with `extract_table`; add URL-create + re-fetch routes; infer format from extension; render `[adapters]`/fetch errors; sheet dropdown wiring. |
| `controlflow_sdk/store/migrations.py` | **Step 6:** `ALTER TABLE sources ADD COLUMN sheet TEXT` + `CREATE TABLE source_fetch (...)`. |
| `controlflow_sdk/store/repo.py` | `upsert_source(..., sheet=None)`; `source_fetch` CRUD helpers (`upsert_source_fetch`, `get_source_fetch`). Fetch provenance lives per-source in `source_fetch` (single URL per source), not per file-version. |
| `controlflow_sdk/store/loader.py`, `store/import_service.py` | Thread `sheet` into `SourceBinding.config` when present. |
| `controlflow_sdk/plane/templates/source_new.html` (+ `source_data.html`, `source_history.html`, `source_refresh.html`) | Upload/URL mode toggle, sheet dropdown, re-fetch button, secrets warning callout, non-CSV preview. |
| `PRODUCT-MAP.md` | Update the Source-manager / Source-editor rows to name Excel/Parquet + URL snapshot. |

## 9. Testing

- **`extract_table`** — unit per format (csv/xlsx/parquet), multi-sheet xlsx (sheet names + selecting
  a non-default sheet), and the `[adapters]`-absent friendly error (monkeypatch the lazy import to
  raise `ImportError`).
- **`fetch_snapshot`** — injected fake opener (no network): JSON-array→CSV, dotted `record_path`, raw
  CSV passthrough, header/auth forwarding into the request, and each `FetchError` case (non-200,
  unreachable, bad JSON, missing record_path).
- **`adapters/inspect`** — round-trip a small DataFrame to xlsx/parquet bytes and back; sheet names.
- **Route tests** (FastAPI `TestClient`): create a source from an `.xlsx` and a `.parquet`
  end-to-end → Data preview renders → **run a control over it and assert the correct full-population
  result** (proves the sheet/format thread reaches the engine); URL-create; re-fetch routes through
  the diff-confirm flow; the secrets warning renders when creds are stored.
- **Engine thread-through** — a store→`_binding`→`source_for` test proving a persisted non-default
  `sheet` is actually read at run time (guards the latent sheet-0 bug).
- **e2e browser smoke** (learning
  [0012](../../learnings/0012-rerun-e2e-browser-smoke-on-htmx-swap-changes.md)) — the add-source form
  restructures in place (mode toggle + conditional sheet dropdown); extend `tests/e2e` to cover the
  assembled DOM rather than trusting isolated partials.
- **Gates** — `pytest -q` (pristine, no warnings), `ruff check`, `mypy controlflow_sdk` all green;
  the **contract gate stays green and unchanged**.

## 10. Non-goals (v1)

- No live/polled connectors, no scheduled refresh, no background reads (the non-goal we are
  respecting).
- No deep/nested JSON normalization (`pd.json_normalize`) — top-level array or a dotted `record_path`
  of flat records only.
- No legacy `.xls` (needs `xlrd`).
- No new bundle fields; `schema_version` frozen.
- No credential encryption at rest (explicitly out — persisted plaintext with a loud warning, per the
  user's directive).

## 11. Risks & mitigations

- **Secrets at rest in `controlplane.db`.** Mitigation: loud, persistent UI warning; isolated
  `source_fetch` table; never in the bundle; captured as a learning.
- **`[plane]`-only installs hitting xlsx/parquet.** Mitigation: lazy import + typed
  `AdaptersUnavailable` + friendly install message; CSV path stays stdlib-only.
- **Latent sheet-0 bug** (store never passed `sheet`). Mitigation: thread `sheet` through
  loader/import and assert it with an engine thread-through test.
- **SSRF-ish fetches** (user points the app at an internal URL). Accepted: single-user localhost,
  brittle-by-design, fully user-initiated — same trust model as the rest of the control plane. Noted,
  not gated.
</content>
</invoke>
