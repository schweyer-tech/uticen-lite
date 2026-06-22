# Tabbed Edit Source + per-file as-of & version history — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the Edit Source page into Definition / Data / History tabs, make the data "as-of" date a property of each uploaded file, and give file version history a first-class home with a read-only paged row preview.

**Architecture:** Add a store table `source_files` (one row per uploaded file version; `is_current=1` is the live file). `sources.extract_date` stays as a denormalized mirror of the current file's as-of so the bundle/workpaper path is untouched. The plane renders three tab sub-routes; uploads (create + refresh) capture a required as-of date and write `source_files` rows.

**Tech Stack:** Python ≥3.11, FastAPI + Jinja2 + HTMX, `sqlite3`, stdlib `csv`. Tests: pytest. Gates: ruff (py311, line-length 100) + mypy.

## Global Constraints

- Python floor ≥3.11; ruff target `py311`, line-length 100; mypy must stay clean (`controlflow_sdk`).
- Keep the suite green and output pristine (no stray warnings). Run `python -m pytest -q`, `python -m ruff check .`, `python -m mypy controlflow_sdk` before every commit.
- **Cardinal rule:** do NOT touch `contract/bundle.schema.json`, `bundle/`, or `SourceBinding.to_data_source()`. All new state is store/UI-only. `tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py` must stay green unchanged.
- Plane handlers: `async def` / writing handlers open their OWN `connect(root)` (try/finally, return inside try); sync `GET` may use `Depends(get_conn)`. `TemplateResponse(request, "name.html", {ctx without request})`. Redirects are 303. (Learning 0002.)
- Brittle-by-design, localhost, single-user. CSV parsed with `utf-8-sig`.

---

## EXECUTION RULES

- Execute the full plan start to finish without pausing to ask permission between tasks.
- After each task: run all three gates, then **commit locally**. **Do NOT `git push`** — the user explicitly gates pushing to PR #19. (This intentionally overrides the global "push after every commit" rule for this branch; the work accumulates as local commits on `worktree-onboarding-issue-11` until the user says to push.)
- On an unresolvable error after 2–3 attempts: note it and move to the next task.
- Each commit message ends with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_013JpkfZUEVoutkGJAYHqrKJ`

---

## File Structure

- `controlflow_sdk/store/migrations.py` — +step 3 (`source_files` table + backfill), `SCHEMA_VERSION=3`.
- `controlflow_sdk/store/repo.py` — `source_files` CRUD.
- `controlflow_sdk/store/import_service.py` — write a current `source_files` row per imported source.
- `controlflow_sdk/plane/routes/sources.py` — as-of plumbed through create/refresh/confirm; new `/data`, `/history`, `/data/asof` routes + preview paging; History from DB; `save_source` preserves `extract_date`; remove `_list_versions`.
- `controlflow_sdk/plane/templates/_source_tabs.html` (new) — tab nav.
- `controlflow_sdk/plane/templates/source_edit.html` — Definition tab (trim data-file card + as-of).
- `controlflow_sdk/plane/templates/source_data.html` (new), `source_history.html` (new).
- `controlflow_sdk/plane/templates/source_new.html`, `source_refresh.html` — required as-of field / carry-through.
- `controlflow_sdk/plane/static/app.css` — `.tabs`, preview pager.
- Tests: `tests/store/test_migrations.py`, `tests/store/test_source_files.py` (new), `tests/store/test_import_service.py`, `tests/plane/test_sources.py`.

---

## Task 1: Migration step 3 — `source_files` table + backfill

**Files:**
- Modify: `controlflow_sdk/store/migrations.py`
- Test: `tests/store/test_migrations.py`

**Interfaces:**
- Produces: table `source_files(id, source_id, stored_path, original_name, as_of_date, row_count, uploaded_at, is_current)`; `SCHEMA_VERSION == 3`.

- [ ] **Step 1: Write the failing tests** — append to `tests/store/test_migrations.py`:

```python
def test_source_files_table_and_backfill(tmp_path: Path):
    from controlflow_sdk.store.migrations import _STEPS

    conn = connect(tmp_path)
    conn.executescript(_STEPS[0])      # v1 schema
    conn.executescript(_STEPS[1])      # v2: title column
    conn.execute("PRAGMA user_version = 2")
    conn.execute(
        "INSERT INTO sources (id, format, path, key_config, extract_date, created_at) "
        "VALUES ('s', 'csv', 'data/s.csv', '{}', '2026-03-31', '2026-01-01')"
    )
    conn.commit()

    migrate(conn)  # forward step 3 adds the table + backfills a current row
    assert _user_version(conn) == SCHEMA_VERSION == 3
    cols = {r[1] for r in conn.execute("PRAGMA table_info(source_files)").fetchall()}
    assert {"source_id", "stored_path", "original_name", "as_of_date",
            "row_count", "uploaded_at", "is_current"} <= cols
    row = conn.execute(
        "SELECT source_id, stored_path, original_name, as_of_date, is_current "
        "FROM source_files WHERE source_id = 's'"
    ).fetchone()
    assert tuple(row) == ("s", "data/s.csv", "s.csv", "2026-03-31", 1)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/store/test_migrations.py::test_source_files_table_and_backfill -v`
Expected: FAIL (no such table `source_files`).

- [ ] **Step 3: Implement** — in `migrations.py` set `SCHEMA_VERSION = 3` and append this string to `_STEPS` (after the step-2 `ALTER TABLE ... ADD COLUMN title` string):

```python
    # --- step 3 -> user_version 3 -------------------------------------------
    # Per-file data lineage: one row per uploaded file version. is_current=1 is the
    # live file (its stored_path == sources.path); archived versions point under
    # data/.versions/<id>/. as_of_date is the file's data-as-of. Store/UI only — the
    # bundle path reads sources.extract_date (kept in sync with the current row).
    # Backfill one current row per existing source so single-file sources show history.
    """
    CREATE TABLE IF NOT EXISTS source_files (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id     TEXT NOT NULL,
        stored_path   TEXT NOT NULL,
        original_name TEXT NOT NULL,
        as_of_date    TEXT,
        row_count     INTEGER,
        uploaded_at   TEXT NOT NULL DEFAULT '',
        is_current    INTEGER NOT NULL DEFAULT 0
    );
    INSERT INTO source_files
        (source_id, stored_path, original_name, as_of_date, uploaded_at, is_current)
    SELECT id, path, replace(path, 'data/', ''), extract_date, created_at, 1
    FROM sources;
    """,
```

- [ ] **Step 4: Run to verify it passes** — and the whole migrations file:

Run: `python -m pytest tests/store/test_migrations.py -v`
Expected: PASS (existing tests still green; `<=` table-set assertion unaffected).

- [ ] **Step 5: Gates + commit (local only, no push)**

```bash
python -m pytest -q && python -m ruff check . && python -m mypy controlflow_sdk
git add controlflow_sdk/store/migrations.py tests/store/test_migrations.py
git commit -m "feat(store): source_files table + v2→v3 backfill for per-file as-of"
```

---

## Task 2: `repo.py` source_files API

**Files:**
- Modify: `controlflow_sdk/store/repo.py`
- Test: `tests/store/test_source_files.py` (new)

**Interfaces:**
- Produces:
  - `set_initial_file(conn, *, source_id, stored_path, original_name, as_of_date, row_count, uploaded_at="") -> None` — replace any rows for the source with a single current row (import/create-from-scratch semantics).
  - `record_current_file(conn, *, source_id, stored_path, original_name, as_of_date, row_count, uploaded_at="") -> None` — demote prior current, insert a new current row (refresh semantics).
  - `archive_current_file(conn, source_id, new_stored_path) -> None` — set the current row `is_current=0` and repoint its `stored_path`.
  - `get_current_file(conn, source_id) -> dict | None`
  - `list_source_files(conn, source_id) -> list[dict]` — newest first.
  - `set_current_file_asof(conn, source_id, as_of_date) -> None` — update current row + sync `sources.extract_date`.

- [ ] **Step 1: Write the failing tests** — create `tests/store/test_source_files.py`:

```python
from pathlib import Path

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate


def _store(tmp_path: Path):
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_source(conn, id="s", format="csv", path="data/s.csv",
                       key_config={"mode": "auto"})
    return conn


def test_set_initial_then_record_archives_prior(tmp_path: Path):
    conn = _store(tmp_path)
    repo.set_initial_file(conn, source_id="s", stored_path="data/s.csv",
                          original_name="s.csv", as_of_date="2026-01-01",
                          row_count=10, uploaded_at="t0")
    cur = repo.get_current_file(conn, "s")
    assert cur["original_name"] == "s.csv" and cur["as_of_date"] == "2026-01-01"

    repo.archive_current_file(conn, "s", "data/.versions/s/t0__s.csv")
    repo.record_current_file(conn, source_id="s", stored_path="data/s.csv",
                             original_name="s2.csv", as_of_date="2026-02-01",
                             row_count=12, uploaded_at="t1")

    cur = repo.get_current_file(conn, "s")
    assert cur["as_of_date"] == "2026-02-01" and cur["row_count"] == 12
    files = repo.list_source_files(conn, "s")
    assert len(files) == 2
    assert files[0]["is_current"] == 1  # newest-first, current on top
    archived = next(f for f in files if not f["is_current"])
    assert archived["stored_path"] == "data/.versions/s/t0__s.csv"
    conn.close()


def test_set_current_file_asof_syncs_extract_date(tmp_path: Path):
    conn = _store(tmp_path)
    repo.set_initial_file(conn, source_id="s", stored_path="data/s.csv",
                          original_name="s.csv", as_of_date="2026-01-01",
                          row_count=1, uploaded_at="t0")
    repo.set_current_file_asof(conn, "s", "2026-09-09")
    assert repo.get_current_file(conn, "s")["as_of_date"] == "2026-09-09"
    assert repo.get_source(conn, "s")["extract_date"] == "2026-09-09"
    conn.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/store/test_source_files.py -v`
Expected: FAIL (`AttributeError: module ... has no attribute 'set_initial_file'`).

- [ ] **Step 3: Implement** — add to `repo.py` after `list_sources` (the `# ---- sources + columns` section):

```python
# ---- source files (per-file data lineage) ----------------------------------
def _insert_current_file(
    conn: sqlite3.Connection, *, source_id: str, stored_path: str,
    original_name: str, as_of_date: str | None, row_count: int | None,
    uploaded_at: str,
) -> None:
    conn.execute(
        """INSERT INTO source_files
             (source_id, stored_path, original_name, as_of_date, row_count,
              uploaded_at, is_current)
           VALUES (?, ?, ?, ?, ?, ?, 1)""",
        (source_id, stored_path, original_name, as_of_date, row_count, uploaded_at),
    )


def set_initial_file(
    conn: sqlite3.Connection, *, source_id: str, stored_path: str,
    original_name: str, as_of_date: str | None, row_count: int | None,
    uploaded_at: str = "",
) -> None:
    """Replace all file rows for a source with one current row (import/create)."""
    conn.execute("DELETE FROM source_files WHERE source_id = ?", (source_id,))
    _insert_current_file(conn, source_id=source_id, stored_path=stored_path,
                         original_name=original_name, as_of_date=as_of_date,
                         row_count=row_count, uploaded_at=uploaded_at)
    conn.commit()


def record_current_file(
    conn: sqlite3.Connection, *, source_id: str, stored_path: str,
    original_name: str, as_of_date: str | None, row_count: int | None,
    uploaded_at: str = "",
) -> None:
    """Demote any current row, then add a new current row (refresh)."""
    conn.execute(
        "UPDATE source_files SET is_current = 0 WHERE source_id = ? AND is_current = 1",
        (source_id,),
    )
    _insert_current_file(conn, source_id=source_id, stored_path=stored_path,
                         original_name=original_name, as_of_date=as_of_date,
                         row_count=row_count, uploaded_at=uploaded_at)
    conn.commit()


def archive_current_file(
    conn: sqlite3.Connection, source_id: str, new_stored_path: str
) -> None:
    conn.execute(
        "UPDATE source_files SET is_current = 0, stored_path = ? "
        "WHERE source_id = ? AND is_current = 1",
        (new_stored_path, source_id),
    )
    conn.commit()


def get_current_file(conn: sqlite3.Connection, source_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM source_files WHERE source_id = ? AND is_current = 1",
        (source_id,),
    ).fetchone()
    return dict(row) if row else None


def list_source_files(conn: sqlite3.Connection, source_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM source_files WHERE source_id = ? "
        "ORDER BY is_current DESC, uploaded_at DESC, id DESC",
        (source_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def set_current_file_asof(
    conn: sqlite3.Connection, source_id: str, as_of_date: str | None
) -> None:
    conn.execute(
        "UPDATE source_files SET as_of_date = ? WHERE source_id = ? AND is_current = 1",
        (as_of_date, source_id),
    )
    conn.execute("UPDATE sources SET extract_date = ? WHERE id = ?",
                 (as_of_date, source_id))
    conn.commit()
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/store/test_source_files.py -v`
Expected: PASS.

- [ ] **Step 5: Gates + commit (local only, no push)**

```bash
python -m pytest -q && python -m ruff check . && python -m mypy controlflow_sdk
git add controlflow_sdk/store/repo.py tests/store/test_source_files.py
git commit -m "feat(store): source_files repo API (set/record/archive/list/asof)"
```

---

## Task 3: Plumb source_files + as-of through import, create, refresh-confirm; Definition preserves extract_date

**Files:**
- Modify: `controlflow_sdk/store/import_service.py`, `controlflow_sdk/plane/routes/sources.py`
- Test: `tests/store/test_import_service.py`, `tests/plane/test_sources.py`

**Interfaces:**
- Consumes: Task 2 repo API.
- Produces: every create/refresh/import results in a current `source_files` row; `sources.extract_date` mirrors the current file's as-of.

- [ ] **Step 1: Write the failing tests**

Append to `tests/store/test_import_service.py` (inside `test_import_project_returns_counts_and_rows`, after the existing title assertion):

```python
    # Importing a source records a current file row carrying the file's as-of date.
    cur = repo.get_current_file(conn, "invoices")
    assert cur is not None and cur["is_current"] == 1
    assert cur["as_of_date"] == "2026-03-31"
```

Append to `tests/plane/test_sources.py`:

```python
def test_create_records_current_file_with_asof(client):
    client.post("/sources", data={"source_id": "inv", "format": "csv",
                                   "as_of_date": "2026-05-01"},
                files={"file": ("inv.csv", io.BytesIO(b"a\n1\n"), "text/csv")},
                follow_redirects=False)
    from controlflow_sdk.store import repo
    from controlflow_sdk.store.db import connect
    conn = connect(client.app.state.project_root)
    cur = repo.get_current_file(conn, "inv")
    assert cur["as_of_date"] == "2026-05-01" and cur["original_name"] == "inv.csv"
    assert repo.get_source(conn, "inv")["extract_date"] == "2026-05-01"
    conn.close()


def test_refresh_confirm_records_new_version_with_asof(client):
    _upload(client, "tx", b"user_id,amount\nU1,5\n")  # helper defined earlier in file
    # set an initial as-of via the create path is skipped here; refresh supplies its own
    client.post("/sources/tx/refresh",
                data={"as_of_date": "2026-06-30"},
                files={"file": ("tx.csv", io.BytesIO(b"user_id,amount\nU1,5\nU2,9\n"),
                                "text/csv")},
                follow_redirects=False)
    client.post("/sources/tx/refresh/confirm",
                data={"pending": "tx.csv", "as_of_date": "2026-06-30"},
                follow_redirects=False)
    from controlflow_sdk.store import repo
    from controlflow_sdk.store.db import connect
    conn = connect(client.app.state.project_root)
    files = repo.list_source_files(conn, "tx")
    assert len(files) == 2  # initial + refreshed
    assert files[0]["is_current"] == 1 and files[0]["as_of_date"] == "2026-06-30"
    assert repo.get_source(conn, "tx")["extract_date"] == "2026-06-30"
    conn.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/store/test_import_service.py tests/plane/test_sources.py -q`
Expected: FAIL (no current file recorded; as-of not persisted).

- [ ] **Step 3: Implement**

(a) `import_service.py` — after the `repo.set_columns(...)` call inside the `for sid, binding in ...` loop, add:

```python
        _path = binding.config.get("path", "")
        repo.set_initial_file(
            conn, source_id=sid, stored_path=_path,
            original_name=Path(_path).name, as_of_date=binding.extract_date,
            row_count=None, uploaded_at="",
        )
```

(b) `routes/sources.py` — add a row-count helper near the other module helpers:

```python
def _row_count(raw: bytes) -> int:
    n = sum(1 for _ in csvmod.reader(io.StringIO(raw.decode("utf-8-sig"))))
    return max(0, n - 1)  # exclude header
```

(c) `create_source` — accept as-of and record the file. Change the signature to add
`as_of_date: str = Form("")` and, inside the `try` after `repo.set_columns(...)`:

```python
            repo.set_initial_file(
                conn, source_id=source_id, stored_path=f"data/{dest.name}",
                original_name=dest.name, as_of_date=as_of_date.strip() or None,
                row_count=_row_count(raw), uploaded_at=_stamp(),
            )
            if as_of_date.strip():
                conn.execute("UPDATE sources SET extract_date = ? WHERE id = ?",
                             (as_of_date.strip(), source_id))
                conn.commit()
```

(d) `refresh_source` — accept `as_of_date: str = Form("")` and pass it to the template
context as `"as_of_date": as_of_date` (so `source_refresh.html` can carry it hidden).

(e) `confirm_refresh` — add `as_of_date: str = Form("")` to the signature. After the existing
`current_path.write_bytes(new_bytes)` / `pending_path.unlink()` and the column reconcile, replace the
file-archival bookkeeping so it also updates `source_files`:

```python
            # record the new current file version (old one archived above)
            repo.archive_current_file(conn, source_id, str(archive_rel))
            repo.record_current_file(
                conn, source_id=source_id, stored_path=existing["path"],
                original_name=Path(pending).name,
                as_of_date=as_of_date.strip() or None,
                row_count=_row_count(new_bytes), uploaded_at=stamp,
            )
```

where `archive_rel` is the repo-relative archived path and `stamp` is the timestamp already
computed when archiving (refactor the existing archive block to compute `stamp = _stamp()` and
`archive_rel = Path("data/.versions") / source_id / f"{stamp}__{current_path.name}"`, write the old
bytes to `root / archive_rel`). Then set `sources.extract_date` via the existing `upsert_source(...)`
call by passing `extract_date=as_of_date.strip() or existing.get("extract_date")`.

(f) `save_source` (Definition POST) — stop reading `extract_date` from the form; preserve the
existing value. Change the `extract_date=_field("extract_date")` argument in its `upsert_source(...)`
call to `extract_date=existing.get("extract_date")`.

(g) Remove the now-unused `_list_versions` helper and the `versions=` context key from `edit_source`
(History tab replaces it in Task 6 — for now `edit_source` just drops `versions`).

- [ ] **Step 4: Run to verify they pass** (and the whole suite)

Run: `python -m pytest -q`
Expected: PASS. (Pre-existing refresh tests that don't post `as_of_date` still pass — as-of is optional server-side; the HTML `required` attribute enforces it in the browser, matching how `source_id`/`file` are handled.)

- [ ] **Step 5: Gates + commit (local only, no push)**

```bash
python -m pytest -q && python -m ruff check . && python -m mypy controlflow_sdk
git add controlflow_sdk/store/import_service.py controlflow_sdk/plane/routes/sources.py \
        tests/store/test_import_service.py tests/plane/test_sources.py
git commit -m "feat(plane): record per-file as-of on import/create/refresh; preserve extract_date in Definition save"
```

---

## Task 4: Tab nav partial + Definition tab template

**Files:**
- Create: `controlflow_sdk/plane/templates/_source_tabs.html`
- Modify: `controlflow_sdk/plane/templates/source_edit.html`, `controlflow_sdk/plane/routes/sources.py` (pass `active`), `controlflow_sdk/plane/static/app.css`
- Test: `tests/plane/test_sources.py`

**Interfaces:**
- Consumes: `edit_source` context.
- Produces: Definition tab renders a 3-tab nav; the data-file card + standalone as-of field are gone from it.

- [ ] **Step 1: Write the failing test** — append to `tests/plane/test_sources.py`:

```python
def test_definition_tab_has_nav_and_no_datafile_card(client):
    _upload(client, "d", b"a\n1\n")
    page = client.get("/sources/d").text
    assert 'href="/sources/d/data"' in page and 'href="/sources/d/history"' in page
    assert 'class="tabs"' in page
    # the upload/refresh UI no longer lives on the Definition tab
    assert "/sources/d/refresh" not in page
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/plane/test_sources.py::test_definition_tab_has_nav_and_no_datafile_card -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `_source_tabs.html`:

```html
<nav class="tabs">
  <a href="/sources/{{ source.id }}" class="tab {% if active == 'definition' %}active{% endif %}">Definition</a>
  <a href="/sources/{{ source.id }}/data" class="tab {% if active == 'data' %}active{% endif %}">Data</a>
  <a href="/sources/{{ source.id }}/history" class="tab {% if active == 'history' %}active{% endif %}">History</a>
</nav>
```

In `source_edit.html`: add `{% include "_source_tabs.html" %}` right after the `</div>` that closes
`.page-head`; delete the entire "Data file" `<div class="card">…</div>` block (the refresh form +
Previous versions list); and delete the "Data as-of date" `<div class="field">…</div>` from the
Metadata card. Leave Title, Description, Key columns, and the Columns table intact.

In `routes/sources.py` `edit_source`: add `"active": "definition"` to the context dict.

In `app.css`, after the buttons section, add:

```css
/* ── tabs ─────────────────────────────────────────────────────────────────── */
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border-default); margin: 0 0 20px; }
.tabs .tab {
  padding: 8px 16px; font-size: 13px; font-weight: 600; color: var(--text-secondary);
  border-bottom: 2px solid transparent; text-decoration: none;
}
.tabs .tab:hover { color: var(--text-primary); }
.tabs .tab.active { color: var(--accent-primary); border-bottom-color: var(--accent-primary); }
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/plane/test_sources.py -q`
Expected: PASS (the earlier `test_save_source_metadata_persists` still passes — as-of is now set via the file flows, but `save_source` preserves `extract_date`, and that test posts to create+save; verify it still asserts persisted title/description; the `2026-03-31` assertion on the edit page may need to move — if it fails, update that test to assert the as-of on `/sources/invoices/data` instead).

- [ ] **Step 5: Gates + commit (local only, no push)**

```bash
python -m pytest -q && python -m ruff check . && python -m mypy controlflow_sdk
git add controlflow_sdk/plane/templates/_source_tabs.html \
        controlflow_sdk/plane/templates/source_edit.html \
        controlflow_sdk/plane/routes/sources.py controlflow_sdk/plane/static/app.css \
        tests/plane/test_sources.py
git commit -m "feat(plane): Definition tab + shared source tab nav"
```

---

## Task 5: Data tab — route, paged preview, as-of edit

**Files:**
- Create: `controlflow_sdk/plane/templates/source_data.html`
- Modify: `controlflow_sdk/plane/routes/sources.py`, `controlflow_sdk/plane/static/app.css`
- Test: `tests/plane/test_sources.py`

**Interfaces:**
- Consumes: `repo.get_current_file`, `_row_count`.
- Produces: `GET /sources/{id}/data?page=N`, `POST /sources/{id}/data/asof`.

- [ ] **Step 1: Write the failing tests** — append to `tests/plane/test_sources.py`:

```python
def test_data_tab_preview_paging_and_count(client):
    rows = b"id,val\n" + b"".join(f"R{i},{i}\n".encode() for i in range(1, 121))
    client.post("/sources", data={"source_id": "big", "format": "csv",
                                   "as_of_date": "2026-01-01"},
                files={"file": ("big.csv", io.BytesIO(rows), "text/csv")},
                follow_redirects=False)
    p1 = client.get("/sources/big/data").text
    assert "120" in p1                      # record count shown
    assert "R1" in p1 and "R50" in p1       # first page (page size 50)
    assert "R51" not in p1
    p2 = client.get("/sources/big/data?page=2").text
    assert "R51" in p2 and "R100" in p2


def test_data_tab_asof_edit_syncs(client):
    client.post("/sources", data={"source_id": "z", "format": "csv",
                                   "as_of_date": "2026-01-01"},
                files={"file": ("z.csv", io.BytesIO(b"a\n1\n"), "text/csv")},
                follow_redirects=False)
    client.post("/sources/z/data/asof", data={"as_of_date": "2026-07-07"},
                follow_redirects=False)
    from controlflow_sdk.store import repo
    from controlflow_sdk.store.db import connect
    conn = connect(client.app.state.project_root)
    assert repo.get_current_file(conn, "z")["as_of_date"] == "2026-07-07"
    assert repo.get_source(conn, "z")["extract_date"] == "2026-07-07"
    conn.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/plane/test_sources.py::test_data_tab_preview_paging_and_count tests/plane/test_sources.py::test_data_tab_asof_edit_syncs -v`
Expected: FAIL (404 / route missing).

- [ ] **Step 3: Implement**

Add a module constant near the top of `routes/sources.py`: `PAGE_SIZE = 50`.

Add the routes (place after `edit_source`, before `save_source`):

```python
    @app.get("/sources/{source_id}/data", response_class=HTMLResponse)
    def source_data(
        source_id: str,
        request: Request,
        page: int = 1,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        root = request.app.state.project_root
        current = repo.get_current_file(conn, source_id)
        header: list[str] = []
        rows: list[list[str]] = []
        total = 0
        if current:
            fpath = root / current["stored_path"]
            if fpath.is_file():
                all_rows = list(
                    csvmod.reader(io.StringIO(fpath.read_text(encoding="utf-8-sig")))
                )
                if all_rows:
                    header, data_rows = all_rows[0], all_rows[1:]
                    total = len(data_rows)
                    page = max(1, page)
                    start = (page - 1) * PAGE_SIZE
                    rows = data_rows[start:start + PAGE_SIZE]
        page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        return templates.TemplateResponse(
            request, "source_data.html",
            {"project": repo.get_project(conn) or {"name": ""},
             "source": repo.get_source(conn, source_id), "current": current,
             "header": header, "rows": rows, "total": total,
             "page": min(page, page_count), "page_count": page_count, "active": "data"},
        )

    @app.post("/sources/{source_id}/data/asof")
    async def update_asof(
        source_id: str,
        request: Request,
        as_of_date: str = Form(""),
    ) -> Any:
        root = request.app.state.project_root
        conn = connect(root)
        try:
            repo.set_current_file_asof(conn, source_id, as_of_date.strip() or None)
            return RedirectResponse(f"/sources/{source_id}/data", status_code=303)
        finally:
            conn.close()
```

Create `source_data.html`:

```html
{% extends "base.html" %}
{% block title %}{{ project.name }} — {{ source.title or source.id }} · Data{% endblock %}
{% block body %}
<a class="crumb" href="/sources">← Sources</a>
<div class="page-head">
  <h1>{{ source.title or "Edit source" }}</h1>
  <p class="muted mono">{{ source.id }}</p>
</div>
{% include "_source_tabs.html" %}

<div class="card">
  <h2>Current file</h2>
  {% if current %}
  <p class="muted">
    <span class="mono">{{ current.original_name }}</span> ·
    {{ total }} record{{ '' if total == 1 else 's' }}
  </p>
  <form method="post" action="/sources/{{ source.id }}/data/asof" class="inline-asof">
    <label for="d-asof">Data as-of date</label>
    <input id="d-asof" type="date" name="as_of_date" value="{{ current.as_of_date or '' }}">
    <button class="btn btn-sm" type="submit">Save date</button>
  </form>
  {% else %}
  <p class="muted">No file uploaded yet.</p>
  {% endif %}
</div>

<div class="card">
  <h2>Refresh data</h2>
  <p class="hint">Upload a newer extract. You'll review any column changes before it replaces the current file — and the previous file is always kept.</p>
  <form method="post" action="/sources/{{ source.id }}/refresh" enctype="multipart/form-data">
    <div class="field">
      <label for="d-file">File</label>
      <input id="d-file" type="file" name="file" accept=".csv" required>
    </div>
    <div class="field">
      <label for="d-newasof">As-of date for this file</label>
      <input id="d-newasof" type="date" name="as_of_date" required>
    </div>
    <button class="btn btn-primary" type="submit">Upload new file…</button>
  </form>
</div>

{% if header %}
<h2>Preview <span class="muted">— records {{ (page - 1) * 50 + 1 }}–{{ (page - 1) * 50 + rows|length }} of {{ total }}</span></h2>
<div class="table-wrap">
  <table>
    <thead><tr>{% for h in header %}<th class="mono">{{ h }}</th>{% endfor %}</tr></thead>
    <tbody>
      {% for r in rows %}<tr>{% for cell in r %}<td>{{ cell }}</td>{% endfor %}</tr>{% endfor %}
    </tbody>
  </table>
</div>
{% if page_count > 1 %}
<div class="pager">
  {% if page > 1 %}<a class="btn btn-sm" href="/sources/{{ source.id }}/data?page={{ page - 1 }}">← Prev</a>{% endif %}
  <span class="muted">Page {{ page }} of {{ page_count }}</span>
  {% if page < page_count %}<a class="btn btn-sm" href="/sources/{{ source.id }}/data?page={{ page + 1 }}">Next →</a>{% endif %}
</div>
{% endif %}
{% endif %}
{% endblock %}
```

In `app.css` add:

```css
.inline-asof { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.inline-asof input { margin-top: 0; }
.pager { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/plane/test_sources.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit (local only, no push)**

```bash
python -m pytest -q && python -m ruff check . && python -m mypy controlflow_sdk
git add controlflow_sdk/plane/templates/source_data.html \
        controlflow_sdk/plane/routes/sources.py controlflow_sdk/plane/static/app.css \
        tests/plane/test_sources.py
git commit -m "feat(plane): Data tab — paged read-only preview, record count, per-file as-of edit"
```

---

## Task 6: History tab + as-of fields on new/refresh templates

**Files:**
- Create: `controlflow_sdk/plane/templates/source_history.html`
- Modify: `controlflow_sdk/plane/routes/sources.py`, `controlflow_sdk/plane/templates/source_new.html`, `controlflow_sdk/plane/templates/source_refresh.html`
- Test: `tests/plane/test_sources.py`

**Interfaces:**
- Consumes: `repo.list_source_files`.
- Produces: `GET /sources/{id}/history`.

- [ ] **Step 1: Write the failing test** — append to `tests/plane/test_sources.py`:

```python
def test_history_tab_lists_versions(client):
    client.post("/sources", data={"source_id": "h", "format": "csv",
                                   "as_of_date": "2026-02-02"},
                files={"file": ("h.csv", io.BytesIO(b"a\n1\n"), "text/csv")},
                follow_redirects=False)
    page = client.get("/sources/h/history").text
    assert "h.csv" in page and "2026-02-02" in page
    assert 'class="tabs"' in page


def test_add_source_page_has_required_asof(client):
    page = client.get("/sources/new").text
    assert 'name="as_of_date"' in page and "required" in page
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/plane/test_sources.py::test_history_tab_lists_versions tests/plane/test_sources.py::test_add_source_page_has_required_asof -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add the route (after `source_data`):

```python
    @app.get("/sources/{source_id}/history", response_class=HTMLResponse)
    def source_history(
        source_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        return templates.TemplateResponse(
            request, "source_history.html",
            {"project": repo.get_project(conn) or {"name": ""},
             "source": repo.get_source(conn, source_id),
             "files": repo.list_source_files(conn, source_id), "active": "history"},
        )
```

Create `source_history.html`:

```html
{% extends "base.html" %}
{% block title %}{{ project.name }} — {{ source.title or source.id }} · History{% endblock %}
{% block body %}
<a class="crumb" href="/sources">← Sources</a>
<div class="page-head">
  <h1>{{ source.title or "Edit source" }}</h1>
  <p class="muted mono">{{ source.id }}</p>
</div>
{% include "_source_tabs.html" %}

<div class="card">
  <h2>File history</h2>
  <p class="hint">Every uploaded version is kept. The current file is what controls test against.</p>
  {% if files %}
  <div class="table-wrap">
    <table>
      <thead><tr><th>File</th><th>As-of date</th><th>Records</th><th>Uploaded</th><th class="shrink"></th></tr></thead>
      <tbody>
        {% for f in files %}
        <tr>
          <td class="mono">{{ f.original_name }}</td>
          <td>{{ f.as_of_date or '—' }}</td>
          <td>{{ f.row_count if f.row_count is not none else '—' }}</td>
          <td class="muted">{{ f.uploaded_at or '—' }}</td>
          <td class="shrink">{% if f.is_current %}<span class="badge">current</span>{% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="empty-state"><h3>No files yet</h3></div>
  {% endif %}
</div>
{% endblock %}
```

In `source_new.html`, add a required as-of field inside the form (after the File field), pre-filled by the browser is not possible server-side, so leave empty + `required`:

```html
    <div class="field">
      <label for="s-asof">Data as-of date</label>
      <span class="hint">The date this extract is current as of.</span>
      <input id="s-asof" type="date" name="as_of_date" required>
    </div>
```

In `source_refresh.html`, carry the as-of through as a hidden field inside BOTH the confirm and
cancel forms (so confirm persists it): add `<input type="hidden" name="as_of_date" value="{{ as_of_date or '' }}">`
next to the existing `pending` hidden input in each form.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/plane/test_sources.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit (local only, no push)**

```bash
python -m pytest -q && python -m ruff check . && python -m mypy controlflow_sdk
git add controlflow_sdk/plane/templates/source_history.html \
        controlflow_sdk/plane/templates/source_new.html \
        controlflow_sdk/plane/templates/source_refresh.html \
        controlflow_sdk/plane/routes/sources.py tests/plane/test_sources.py
git commit -m "feat(plane): History tab + required as-of on add/refresh"
```

---

## Task 7: Full gates + browser verification

**Files:** none (verification only)

- [ ] **Step 1: Full gates**

Run: `python -m pytest -q && python -m ruff check . && python -m mypy controlflow_sdk`
Expected: all green; bundle/contract tests untouched and passing.

- [ ] **Step 2: Browser smoke (fresh demo)**

```bash
DEMO="$CLAUDE_JOB_DIR/tmp/tabs-demo"; rm -rf "$DEMO"; mkdir -p "$DEMO/data"
python -c "from pathlib import Path; from controlflow_sdk.store.db import connect; \
from controlflow_sdk.store.migrations import migrate; \
from controlflow_sdk.store.import_service import load_demo; \
r=Path('$DEMO'); c=connect(r); migrate(c); print(load_demo(c,r)); c.close()"
python -m controlflow_sdk.plane --project "$DEMO" --port 8803 --no-browser
```

Verify with Playwright: `/sources/invoices` (Definition tab + nav, no data-file card),
`/sources/invoices/data` (record count + paged preview + as-of edit), `/sources/invoices/history`
(backfilled current row with as-of `2026-03-31`); run an as-of refresh end-to-end and confirm a second
History row appears and `extract_date` follows the new current file.

- [ ] **Step 3: Final commit if any verification fixups were needed (local only, no push)**

```bash
git add -A && git commit -m "test(plane): browser-verify source tabs + file versioning"
```

---

## Self-Review

- **Spec coverage:** 3 tabs (T4–T6) · per-file as-of required on upload (T3, T5, T6) · history home (T6) · paged preview + count (T5) · `source_files` model + backfill (T1) · repo API (T2) · extract_date mirror / cardinal-rule safety (T1–T3, T7). ✓
- **Placeholder scan:** none — every step has concrete code/commands.
- **Type consistency:** `set_initial_file` / `record_current_file` / `archive_current_file` /
  `get_current_file` / `list_source_files` / `set_current_file_asof` names + signatures match across
  T2 (definition), T3/T5/T6 (callers). `_row_count`, `PAGE_SIZE`, `active`/`current`/`files` context
  keys consistent T3–T6. Migration `_STEPS` index 2 ↔ `SCHEMA_VERSION = 3` (T1).
- **Known fragile test:** `test_save_source_metadata_persists` (prior cycle) asserts the as-of on the
  edit page; T4 Step 4 flags that it may need its as-of assertion moved to `/sources/{id}/data`. The
  executing agent must update it rather than weaken it.
