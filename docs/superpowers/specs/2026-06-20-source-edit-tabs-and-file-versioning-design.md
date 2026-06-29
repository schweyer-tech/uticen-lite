# Design — Tabbed Edit Source + per-file as-of date & version history

Date: 2026-06-20
Area: control plane (`uticen_lite/plane/`) + store (`uticen_lite/store/`)
Status: approved (brainstorm), pending spec review

## Problem

The Edit Source page mixes a source's *definition* (metadata + column mapping) with its *data file*
in one long form. Two gaps:

1. The data file, its record count, and a preview aren't surfaced — an author can't easily confirm
   they uploaded the right population.
2. "As-of date" is a single `sources.extract_date` for the whole source, but it logically belongs to
   **the specific file that was uploaded**. And the file version history (added in the prior cycle as
   timestamped files on disk) is not obvious — with one file you can't tell there's history at all.

## Goals

- Split Edit Source into **three tabs**: **Definition**, **Data**, **History** (each a real sub-route).
- Make **as-of date a property of each uploaded file**, captured (required) at upload time.
- Give version **history a first-class home** showing every uploaded file with its as-of date.
- Add a **read-only, server-paged row preview** + record count on the Data tab.

## Non-goals / guardrails

- **Not a general data tool** (STRATEGY non-goal). The preview is read-only and paged — **no sort,
  filter, search, or aggregation**. It exists only to eyeball the population.
- **Bundle contract untouched** (cardinal rule / learning 0001). All new state is store/UI-only;
  `SourceBinding.to_data_source()` and `contract/bundle.schema.json` do not change. `sources.extract_date`
  remains the value that threads into the runtime workpaper.
- No new runtime dependency. CSV preview uses the stdlib `csv` module (not pandas).

## Data model

New table, migration **step 3 → `user_version 3`** (`SCHEMA_VERSION = 3`):

```sql
CREATE TABLE source_files (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id     TEXT    NOT NULL,
  stored_path   TEXT    NOT NULL,   -- repo-relative path on disk (data/<name> or data/.versions/<id>/<stamp>__<name>)
  original_name TEXT    NOT NULL,   -- filename as uploaded
  as_of_date    TEXT,               -- per-file data as-of (ISO yyyy-mm-dd); NULL allowed for backfilled rows
  row_count     INTEGER,            -- cached data-row count (excl header); NULL = compute lazily
  uploaded_at   TEXT    NOT NULL DEFAULT '',
  is_current    INTEGER NOT NULL DEFAULT 0
);
```

Semantics:
- **Current file** = the `is_current = 1` row for a source; its `stored_path` equals `sources.path`.
- **Archived versions** = `is_current = 0` rows whose `stored_path` lives under `data/.versions/<id>/`.
- `sources.extract_date` is kept as a **denormalized mirror** of the current file's `as_of_date`, so the
  binding/loader/workpaper path is unchanged. Every create / refresh-confirm / current-file as-of edit
  re-syncs it.

**Backfill (in the same migration step):** one current row per existing source, so existing/demo
single-file sources appear in History.

```sql
INSERT INTO source_files (source_id, stored_path, original_name, as_of_date, uploaded_at, is_current)
SELECT id, path, replace(path, 'data/', ''), extract_date, created_at, 1 FROM sources;
```

(`path` is always `data/<name>` by convention, so `replace(path,'data/','')` yields the filename.
`row_count` is left NULL and computed lazily on display.)

## Store API (`store/repo.py`)

- `list_source_files(conn, source_id) -> list[dict]` — newest first (`is_current` desc, then `uploaded_at` desc, `id` desc).
- `get_current_file(conn, source_id) -> dict | None` — the `is_current = 1` row.
- `record_current_file(conn, *, source_id, stored_path, original_name, as_of_date, row_count, uploaded_at)` —
  clear any existing `is_current` for the source, insert the new row as current.
- `archive_current_file(conn, source_id, new_stored_path)` — set the current row to `is_current = 0`
  and repoint its `stored_path` to the archived path (used right before a refresh writes new bytes).
- `set_current_file_asof(conn, source_id, as_of_date)` — update the current row's `as_of_date` **and**
  sync `sources.extract_date`.

`get_source` / `list_sources` are unchanged (the new table is queried explicitly).

## Routes (`plane/routes/sources.py`) — tabs as sub-routes

| Route | Tab | Notes |
|-------|-----|-------|
| `GET /sources/{id}` | **Definition** | Existing editor minus the data-file card and the as-of field: title, description, key columns, column mapping table. |
| `GET /sources/{id}/data` | **Data** | Current file summary (name, record count, editable as-of); paged preview (`?page=N`); upload-new form. |
| `GET /sources/{id}/history` | **History** | Table of `source_files` rows: filename, as-of, uploaded-at, row count, "current" badge. |
| `POST /sources/{id}/data/asof` | — | Inline edit of the **current** file's as-of (updates row + `sources.extract_date`), 303 back to Data. |

Existing flows, extended:
- `POST /sources` (create) and `POST /sources/{id}/refresh` (stage) gain a **required** `as_of_date`
  form field (pre-filled with today in the templates).
- `refresh_source` carries `as_of_date` into the preview; `source_refresh.html` keeps it as a hidden
  field; `confirm_refresh` persists it.
- On **create**: write the file, `record_current_file(...)`, set `sources.extract_date`.
- On **refresh confirm**: archive current bytes to `data/.versions/<id>/<stamp>__<name>`,
  `archive_current_file(...)` repoints the old row, write new bytes to `sources.path`,
  `record_current_file(...)` for the new file, sync `sources.extract_date`, reconcile columns
  (unchanged from current behavior).
- `save_source` (Definition POST) no longer reads/writes `extract_date` — it preserves the existing value.

Route ordering: static sub-paths (`/sources/new`, `/sources/{id}/data`, `/sources/{id}/history`,
`/sources/{id}/refresh...`) are distinct from the `GET /sources/{id}` param route; keep `/sources/new`
registered before the `{id}` route (already the case).

## Preview & counting

- Page size **50**, 1-based `?page=N`. Read the current file from disk, parse with stdlib `csv`
  (`utf-8-sig`), render header + the page slice. Show "records X–Y of TOTAL" + prev/next links.
- `row_count` = data rows excluding header. Cached in `source_files.row_count` at upload; computed and
  displayed lazily when NULL (backfilled rows). Whole-file read per page view is acceptable
  (localhost, brittle-by-design).

## Templates

- `_source_tabs.html` — shared tab-nav partial; takes `source` + `active` ("definition"|"data"|"history").
- `source_edit.html` — **Definition** tab: drop the data-file card and the as-of field; add the tab nav.
- `source_data.html` — **Data** tab: current-file summary + as-of inline edit, paged preview table, upload form.
- `source_history.html` — **History** tab: versions table.
- `source_new.html` / `source_refresh.html` — add the required as-of date field / hidden carry-through.
- `app.css` — `.tabs` nav styling; preview table + pager styles reuse existing `.table-wrap`/`.btn`.

## Cardinal-rule safety

`source_files` and per-file as-of are store/UI-only. `sources.extract_date` still mirrors the current
file's as-of and feeds the workpaper exactly as today. `to_data_source()` and `contract/bundle.schema.json`
are not touched. Gate: existing `tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py`
stay green unchanged.

## Testing

- **Migration** (`tests/store/test_migrations.py`): `SCHEMA_VERSION == 3`; `source_files` table exists;
  v2→v3 backfill inserts one `is_current=1` row per existing source with `as_of_date = extract_date`,
  no data loss.
- **Repo** (`tests/store/test_*`): `record_current_file` flips prior current to archived; `get_current_file`
  / `list_source_files` ordering; `set_current_file_asof` syncs `sources.extract_date`.
- **Routes** (`tests/plane/test_sources.py`): Data tab shows record count + a previewed row + paging
  (page 2 shows later rows); History tab lists versions with as-of; as-of **required** on upload and
  carried through refresh→confirm into the new `source_files` row; current-file as-of edit updates both
  the row and `sources.extract_date`; Definition `save_source` preserves `extract_date`.
- **Import** (`tests/store/test_import_service.py`): importing the demo creates a current `source_files`
  row per source with `as_of_date == "2026-03-31"`.

## Build sequence (for the plan)

1. Migration step 3 + backfill (+ tests).
2. `repo.py` `source_files` API (+ tests).
3. `import_service` / create / refresh-confirm write `source_files` rows; as-of plumbed through; `save_source` preserves extract_date.
4. Tab routes (`/data`, `/history`, `/data/asof`) + preview paging.
5. Templates (tab nav, Definition trim, Data, History, new/refresh as-of) + CSS.
6. Full gates green (439+ tests, ruff, mypy), browser verification of the three tabs + an as-of refresh.
