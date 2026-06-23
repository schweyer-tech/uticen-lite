# Multi-format Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the control plane ingest Excel (`.xlsx`) and Parquet uploads (not just CSV) and add a one-time, user-initiated URL fetch that snapshots a REST/HTTP response to a local file-first source.

**Architecture:** Two new seams converge on the existing create/refresh/version flow in `plane/routes/sources.py`: (1) `plane/ingest.py::extract_table` — a format-aware header/rows funnel (CSV via stdlib; xlsx/parquet via a lazily-imported `adapters/inspect.py` so pandas stays confined to `adapters/`); (2) `plane/fetch.py::fetch_snapshot` — a single stdlib `urllib` GET with an injectable opener that snapshots the response (JSON→CSV) to bytes. Excel sheet selection is threaded store→`SourceBinding.config` so runs read the chosen sheet. Fetch provenance + credentials persist in a store-only `source_fetch` table with a loud at-rest warning. The bundle contract is untouched.

**Tech Stack:** Python ≥3.11, FastAPI + Jinja2 + HTMX (`[plane]`), pandas + openpyxl + pyarrow (`[adapters]`), SQLite, stdlib `urllib`/`csv`/`json`, pytest.

## Global Constraints

- Python floor **≥3.11**; ruff target `py311`, line-length **100**.
- **pandas only in `adapters/`** — pure-Python/Pyodide-safe core. `plane/ingest.py` may touch pandas *only* via a lazy import of `adapters/inspect.py`.
- **No new runtime dependency.** CSV ingest + the URL fetch use stdlib only. xlsx/parquet use the existing `[adapters]` extra (`openpyxl`, `pyarrow`); pandas is already a core dep.
- **Bundle contract frozen.** Do not edit `contract/bundle.schema.json` or `schema_version`. `tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py` must stay green and unchanged.
- **Store evolves via store-only state** (migration `user_version` bump only), never `schema_version`.
- **Secrets never enter the bundle.** `source_fetch` (url/headers/record_path) is store/UI-only.
- **Egress is always user-initiated** (button click); no background/polled/auto fetch anywhere.
- Gates must end green and pristine: `python -m pytest -q` (no stray warnings), `python -m ruff check .`, `python -m mypy controlflow_sdk`.

---

## EXECUTION RULES

- **Never ask the user for permission to continue between tasks.** Execute the full plan start to finish without interruption.
- On an unresolvable error after 2–3 attempts: note it in the task and skip to the next task.
- **Push after every commit:**
  ```bash
  git push -u origin HEAD
  ```
  (Feature branch only — no PR is open during the build, so auto-merge is not armed. Per learning 0018, the PR is opened later, after learnings are committed.)
- Run from the worktree root: `/Users/dom/repos/controlflow-sdk/.claude/worktrees/multi-format-sources`.
- TDD throughout: failing test → run it red → minimal implementation → run it green → commit + push.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `controlflow_sdk/adapters/inspect.py` | **New.** The only pandas in this feature: `read_dataframe(raw, fmt, sheet)`, `sheet_names(raw)`. |
| `controlflow_sdk/plane/ingest.py` | **New.** `extract_table` + `ExtractedTable` + `AdaptersUnavailable`. CSV stdlib; xlsx/parquet via lazy `adapters.inspect`. |
| `controlflow_sdk/plane/fetch.py` | **New.** `fetch_snapshot` + `FetchedSnapshot` + `FetchError`; injectable `opener`; JSON→CSV (stdlib). |
| `controlflow_sdk/store/migrations.py` | **Step 6:** `sources.sheet` column + `source_fetch` table. |
| `controlflow_sdk/store/repo.py` | `upsert_source(..., sheet=None)`; `upsert_source_fetch` / `get_source_fetch`. |
| `controlflow_sdk/store/loader.py`, `store/import_service.py` | Thread `sheet` into `SourceBinding.config`. |
| `controlflow_sdk/plane/routes/sources.py` | Route uploads through `extract_table`; format-from-extension; URL create + re-fetch; friendly errors. |
| `controlflow_sdk/plane/templates/source_new.html` (+ `source_data.html`, `source_history.html`) | Upload/URL modes, sheet dropdown, re-fetch button, secrets warning, non-CSV preview. |
| `PRODUCT-MAP.md` | Update Source-manager / Source-editor rows. |

---

### Task 1: `adapters/inspect.py` — pandas reader + sheet names

**Files:**
- Create: `controlflow_sdk/adapters/inspect.py`
- Test: `tests/adapters/test_inspect.py`

**Interfaces:**
- Produces: `read_dataframe(raw: bytes, fmt: str, *, sheet: str | int | None = None) -> pd.DataFrame`; `sheet_names(raw: bytes) -> list[str]`. `fmt` ∈ `{"csv","xlsx","parquet"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/adapters/test_inspect.py
from __future__ import annotations

import io

import pandas as pd

from controlflow_sdk.adapters import inspect


def _xlsx_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
    return buf.getvalue()


def test_read_dataframe_xlsx_default_and_named_sheet():
    raw = _xlsx_bytes({
        "First": pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}),
        "Second": pd.DataFrame({"a": [9], "b": ["z"]}),
    })
    assert inspect.sheet_names(raw) == ["First", "Second"]
    # default -> first sheet
    df0 = inspect.read_dataframe(raw, "xlsx")
    assert list(df0.columns) == ["a", "b"] and len(df0) == 2
    # named -> second sheet
    df2 = inspect.read_dataframe(raw, "xlsx", sheet="Second")
    assert len(df2) == 1 and df2.iloc[0]["b"] == "z"


def test_read_dataframe_parquet_roundtrip():
    raw_buf = io.BytesIO()
    pd.DataFrame({"id": ["A", "B"], "n": [1, 2]}).to_parquet(raw_buf, index=False)
    df = inspect.read_dataframe(raw_buf.getvalue(), "parquet")
    assert list(df.columns) == ["id", "n"] and len(df) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/adapters/test_inspect.py -q`
Expected: FAIL — `ModuleNotFoundError: controlflow_sdk.adapters.inspect`.

- [ ] **Step 3: Write minimal implementation**

```python
# controlflow_sdk/adapters/inspect.py
"""Pandas-backed reads for source inspection (header/rows/sheets).

CPython-only — imports pandas (core dep) and, at read time, the optional
``[adapters]`` engines (openpyxl for xlsx, pyarrow for parquet). Kept under
``adapters/`` so the pure-Python core stays pandas-free (STRATEGY.md).
"""

from __future__ import annotations

import io

import pandas as pd


def read_dataframe(raw: bytes, fmt: str, *, sheet: str | int | None = None) -> pd.DataFrame:
    """Read *raw* bytes of *fmt* into a DataFrame (strings where possible)."""
    if fmt == "csv":
        return pd.read_csv(io.BytesIO(raw), dtype=str)
    if fmt == "xlsx":
        return pd.read_excel(
            io.BytesIO(raw), sheet_name=(0 if sheet is None else sheet),
            engine="openpyxl", dtype=str,
        )
    if fmt == "parquet":
        return pd.read_parquet(io.BytesIO(raw))
    raise ValueError(f"Unsupported format {fmt!r}")


def sheet_names(raw: bytes) -> list[str]:
    """Return the worksheet names of an xlsx workbook, in order."""
    return list(pd.ExcelFile(io.BytesIO(raw), engine="openpyxl").sheet_names)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/adapters/test_inspect.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add controlflow_sdk/adapters/inspect.py tests/adapters/test_inspect.py
git commit -m "feat(adapters): inspect.read_dataframe/sheet_names for xlsx/parquet/csv bytes"
git push -u origin HEAD
```

---

### Task 2: `plane/ingest.py` — `extract_table` format funnel

**Files:**
- Create: `controlflow_sdk/plane/ingest.py`
- Test: `tests/plane/test_ingest.py`

**Interfaces:**
- Consumes: `adapters.inspect.read_dataframe`, `adapters.inspect.sheet_names` (Task 1).
- Produces: `ExtractedTable(header: list[str], rows: list[list[str]], sheet_names: list[str])`; `extract_table(raw: bytes, fmt: str, *, sheet=None) -> ExtractedTable`; `AdaptersUnavailable(RuntimeError)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/plane/test_ingest.py
from __future__ import annotations

import io

import pandas as pd
import pytest

from controlflow_sdk.plane import ingest


def test_extract_table_csv_stdlib():
    raw = b"id,amount\nA,5\nB,6\n"
    t = ingest.extract_table(raw, "csv")
    assert t.header == ["id", "amount"]
    assert t.rows == [["A", "5"], ["B", "6"]]
    assert t.sheet_names == []


def test_extract_table_xlsx_rows_and_sheets():
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame({"id": ["A"], "amount": [5]}).to_excel(xw, sheet_name="S1", index=False)
        pd.DataFrame({"id": ["Z"], "amount": [9]}).to_excel(xw, sheet_name="S2", index=False)
    t = ingest.extract_table(buf.getvalue(), "xlsx", sheet="S2")
    assert t.header == ["id", "amount"]
    assert t.rows == [["Z", "9"]]
    assert t.sheet_names == ["S1", "S2"]


def test_extract_table_missing_adapters_is_friendly(monkeypatch):
    def boom(*a, **k):
        raise ImportError("Missing optional dependency 'openpyxl'")
    monkeypatch.setattr("controlflow_sdk.adapters.inspect.sheet_names", boom)
    monkeypatch.setattr("controlflow_sdk.adapters.inspect.read_dataframe", boom)
    with pytest.raises(ingest.AdaptersUnavailable) as exc:
        ingest.extract_table(b"\x00\x01", "xlsx")
    assert "controlflow-sdk[adapters]" in str(exc.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/plane/test_ingest.py -q`
Expected: FAIL — `ModuleNotFoundError: controlflow_sdk.plane.ingest`.

- [ ] **Step 3: Write minimal implementation**

```python
# controlflow_sdk/plane/ingest.py
"""Format-aware table extraction for the control plane upload/preview paths.

The single funnel that replaces CSV-hardcoded header/row parsing. CSV stays
stdlib (no [adapters] needed); xlsx/parquet lazily delegate to
``adapters.inspect`` so pandas stays confined to ``adapters/`` (STRATEGY.md).
"""

from __future__ import annotations

import csv as csvmod
import io
from dataclasses import dataclass, field


class AdaptersUnavailable(RuntimeError):
    """xlsx/parquet ingest needs the optional ``[adapters]`` extra, which is absent."""


@dataclass(frozen=True)
class ExtractedTable:
    header: list[str]
    rows: list[list[str]]
    sheet_names: list[str] = field(default_factory=list)


def extract_table(raw: bytes, fmt: str, *, sheet: str | int | None = None) -> ExtractedTable:
    """Return header + string rows (+ xlsx sheet names) for *raw* bytes of *fmt*."""
    if fmt == "csv":
        return _csv_table(raw)
    if fmt in ("xlsx", "parquet"):
        return _adapters_table(raw, fmt, sheet)
    raise ValueError(f"Unsupported format {fmt!r}")


def _csv_table(raw: bytes) -> ExtractedTable:
    all_rows = list(csvmod.reader(io.StringIO(raw.decode("utf-8-sig"))))
    if not all_rows:
        return ExtractedTable(header=[], rows=[])
    return ExtractedTable(header=all_rows[0], rows=all_rows[1:])


def _adapters_table(raw: bytes, fmt: str, sheet: str | int | None) -> ExtractedTable:
    # pandas is a core dep so the import succeeds; the engine (openpyxl/pyarrow)
    # is the optional piece and raises ImportError at READ time when absent.
    from controlflow_sdk.adapters import inspect as _inspect

    try:
        names = _inspect.sheet_names(raw) if fmt == "xlsx" else []
        df = _inspect.read_dataframe(raw, fmt, sheet=sheet)
    except ImportError as e:  # openpyxl / pyarrow missing
        raise AdaptersUnavailable(
            "Excel/Parquet support needs the optional dependencies: "
            "pip install 'controlflow-sdk[adapters]'"
        ) from e

    header = [str(c) for c in df.columns]
    filled = df.where(df.notna(), "")
    rows = [[str(v) for v in rec] for rec in filled.itertuples(index=False, name=None)]
    return ExtractedTable(header=header, rows=rows, sheet_names=names)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/plane/test_ingest.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add controlflow_sdk/plane/ingest.py tests/plane/test_ingest.py
git commit -m "feat(plane): extract_table format funnel (csv stdlib; xlsx/parquet via adapters)"
git push -u origin HEAD
```

---

### Task 3: Store migration step 6 — `sheet` column + `source_fetch` table

**Files:**
- Modify: `controlflow_sdk/store/migrations.py` (append a 6th entry to `_STEPS`)
- Test: `tests/store/test_migration_step6.py`

**Interfaces:**
- Produces: `sources.sheet` column (TEXT, NULL ⇒ first sheet); `source_fetch(source_id PK, url, headers, record_path, last_fetched_at)` table; `user_version == 6`.

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_migration_step6.py
from __future__ import annotations

from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_step6_adds_sheet_and_source_fetch(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 6
    assert "sheet" in _cols(conn, "sources")
    assert _cols(conn, "source_fetch") == {
        "source_id", "url", "headers", "record_path", "last_fetched_at"
    }
    # idempotent
    migrate(conn)
    assert "sheet" in _cols(conn, "sources")
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/store/test_migration_step6.py -q`
Expected: FAIL — `source_fetch` table absent / `sheet` not in columns.

- [ ] **Step 3: Write minimal implementation**

Append to `_STEPS` in `controlflow_sdk/store/migrations.py`, immediately after the step-5 string and before the closing `]`:

```python
    # --- step 6 -> user_version 6 -------------------------------------------
    # Multi-format sources. (a) sources.sheet: which xlsx worksheet a source
    # reads (NULL = first sheet); threaded into SourceBinding.config at run
    # time so a control's run reads the chosen sheet. (b) source_fetch:
    # store-only provenance for URL-snapshot sources — the URL, request
    # headers (which MAY carry an auth token, persisted plaintext with a loud
    # UI warning), and an optional JSON record_path, so "Re-fetch from URL" is
    # one click. NEITHER is ever serialized into the bundle (learning 0001):
    # this bumps the STORE user_version only, not schema_version.
    """
    ALTER TABLE sources ADD COLUMN sheet TEXT;
    CREATE TABLE IF NOT EXISTS source_fetch (
        source_id       TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
        url             TEXT NOT NULL,
        headers         TEXT NOT NULL DEFAULT '{}',  -- JSON; may contain auth tokens
        record_path     TEXT,
        last_fetched_at TEXT
    );
    """,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/store/test_migration_step6.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add controlflow_sdk/store/migrations.py tests/store/test_migration_step6.py
git commit -m "feat(store): migration step 6 — sources.sheet + source_fetch table"
git push -u origin HEAD
```

---

### Task 4: `repo.py` — persist `sheet` + `source_fetch` CRUD

**Files:**
- Modify: `controlflow_sdk/store/repo.py` (`upsert_source`; add `upsert_source_fetch`, `get_source_fetch`)
- Test: `tests/store/test_source_fetch_repo.py`

**Interfaces:**
- Consumes: migration step 6 (Task 3).
- Produces: `upsert_source(..., sheet: str | None = None)`; `upsert_source_fetch(conn, *, source_id, url, headers=None, record_path=None, last_fetched_at=None)`; `get_source_fetch(conn, source_id) -> dict | None` (with `headers` decoded to a dict).

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_source_fetch_repo.py
from __future__ import annotations

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate


def _db(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    return conn


def test_upsert_source_persists_sheet(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_source(conn, id="gl", format="xlsx", path="data/gl.xlsx",
                       key_config={"mode": "auto"}, sheet="Q1")
    assert repo.get_source(conn, "gl")["sheet"] == "Q1"
    conn.close()


def test_source_fetch_roundtrip_and_upsert(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_source(conn, id="api", format="csv", path="data/api.csv",
                       key_config={"mode": "auto"})
    repo.upsert_source_fetch(conn, source_id="api", url="https://x/y.json",
                             headers={"Authorization": "Bearer t"},
                             record_path="data.items", last_fetched_at="20260622T0000Z")
    got = repo.get_source_fetch(conn, "api")
    assert got["url"] == "https://x/y.json"
    assert got["headers"] == {"Authorization": "Bearer t"}
    assert got["record_path"] == "data.items"
    # upsert overwrites
    repo.upsert_source_fetch(conn, source_id="api", url="https://x/z.json")
    got2 = repo.get_source_fetch(conn, "api")
    assert got2["url"] == "https://x/z.json" and got2["headers"] == {}
    assert repo.get_source_fetch(conn, "missing") is None
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/store/test_source_fetch_repo.py -q`
Expected: FAIL — `upsert_source() got an unexpected keyword argument 'sheet'`.

- [ ] **Step 3: Write minimal implementation**

In `controlflow_sdk/store/repo.py`, change `upsert_source` to accept and persist `sheet`. Replace the function with:

```python
def upsert_source(
    conn: sqlite3.Connection, *, id: str, format: str, path: str,
    key_config: dict, title: str | None = None, description: str | None = None,
    completeness_accuracy: str | None = None, extract_date: str | None = None,
    created_at: str = "", sheet: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO sources
             (id, format, path, key_config, title, description,
              completeness_accuracy, extract_date, created_at, sheet)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             format=excluded.format, path=excluded.path,
             key_config=excluded.key_config, title=excluded.title,
             description=excluded.description,
             completeness_accuracy=excluded.completeness_accuracy,
             extract_date=excluded.extract_date, sheet=excluded.sheet""",
        (id, format, path, json.dumps(key_config), title, description,
         completeness_accuracy, extract_date, created_at, sheet),
    )
    conn.commit()
```

Then add, after `set_current_file_asof` (end of the source-files section):

```python
# ---- source fetch (URL-snapshot provenance; store/UI-only) ------------------
def upsert_source_fetch(
    conn: sqlite3.Connection, *, source_id: str, url: str,
    headers: dict | None = None, record_path: str | None = None,
    last_fetched_at: str | None = None,
) -> None:
    """Persist (or overwrite) the URL/headers/record_path for a fetched source.

    SECURITY: ``headers`` may contain auth tokens and is stored PLAINTEXT in
    controlplane.db. The UI warns the user. This row never enters the bundle.
    """
    conn.execute(
        """INSERT INTO source_fetch
             (source_id, url, headers, record_path, last_fetched_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(source_id) DO UPDATE SET
             url=excluded.url, headers=excluded.headers,
             record_path=excluded.record_path, last_fetched_at=excluded.last_fetched_at""",
        (source_id, url, json.dumps(headers or {}), record_path, last_fetched_at),
    )
    conn.commit()


def get_source_fetch(conn: sqlite3.Connection, source_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM source_fetch WHERE source_id = ?", (source_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["headers"] = _loads(d.get("headers"), {})
    return d
```

(`_loads` and `json` are already imported in `repo.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/store/test_source_fetch_repo.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add controlflow_sdk/store/repo.py tests/store/test_source_fetch_repo.py
git commit -m "feat(store): upsert_source(sheet=...) + source_fetch CRUD"
git push -u origin HEAD
```

---

### Task 5: Thread `sheet` into `SourceBinding.config` (engine thread-through)

**Files:**
- Modify: `controlflow_sdk/store/loader.py` (`_binding`)
- Modify: `controlflow_sdk/store/import_service.py` (the `upsert_source(...)` call)
- Test: `tests/store/test_sheet_threadthrough.py`

**Interfaces:**
- Consumes: `repo.upsert_source(sheet=...)` (Task 4); `adapters.files.source_for` (existing).
- Produces: `loader._binding` includes `config["sheet"]` iff the stored `sheet` is truthy; `import_service` passes `sheet=binding.config.get("sheet")`.

- [ ] **Step 1: Write the failing test** (proves a non-default sheet is actually read at run time — guards the latent sheet-0 bug)

```python
# tests/store/test_sheet_threadthrough.py
from __future__ import annotations

import io

import pandas as pd

from controlflow_sdk.adapters.files import source_for
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.loader import _binding
from controlflow_sdk.store.migrations import migrate


def test_stored_sheet_is_read_at_runtime(tmp_path):
    (tmp_path / "data").mkdir()
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame({"id": ["A1"], "v": ["first"]}).to_excel(xw, "First", index=False)
        pd.DataFrame({"id": ["B1"], "v": ["second"]}).to_excel(xw, "Second", index=False)
    (tmp_path / "data" / "gl.xlsx").write_bytes(buf.getvalue())

    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_source(conn, id="gl", format="xlsx", path="data/gl.xlsx",
                       key_config={"mode": "auto"}, sheet="Second")
    repo.set_columns(conn, "gl", [
        {"original_name": "id", "display_name": "id", "data_type": "text",
         "is_key": True, "include": True, "ordinal": 0},
        {"original_name": "v", "display_name": "v", "data_type": "text",
         "is_key": False, "include": True, "ordinal": 1},
    ])
    src = repo.get_source(conn, "gl")
    conn.close()

    binding = _binding(src)
    assert binding.config.get("sheet") == "Second"
    pop = source_for(binding, tmp_path).load()
    assert pop.df["v"].tolist() == ["second"]  # NOT the first sheet


def test_binding_omits_sheet_when_none(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_source(conn, id="c", format="csv", path="data/c.csv",
                       key_config={"mode": "auto"})
    repo.set_columns(conn, "c", [
        {"original_name": "id", "display_name": "id", "data_type": "text",
         "is_key": True, "include": True, "ordinal": 0},
    ])
    src = repo.get_source(conn, "c")
    conn.close()
    assert "sheet" not in _binding(src).config
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/store/test_sheet_threadthrough.py -q`
Expected: FAIL — `binding.config.get("sheet")` is `None`, the loaded population is the first sheet.

- [ ] **Step 3: Write minimal implementation**

In `controlflow_sdk/store/loader.py`, replace the `config=` line of `_binding`. Change:

```python
        config={"path": src["path"], "format": src["format"]},
```

to:

```python
        config=_source_config(src),
```

and add this helper above `_binding`:

```python
def _source_config(src: dict) -> dict:
    config = {"path": src["path"], "format": src["format"]}
    if src.get("sheet"):
        config["sheet"] = src["sheet"]
    return config
```

In `controlflow_sdk/store/import_service.py`, add `sheet` to the `upsert_source(...)` call:

```python
            sheet=binding.config.get("sheet"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/store/test_sheet_threadthrough.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add controlflow_sdk/store/loader.py controlflow_sdk/store/import_service.py tests/store/test_sheet_threadthrough.py
git commit -m "feat(store): thread sources.sheet into SourceBinding.config (run reads chosen sheet)"
git push -u origin HEAD
```

---

### Task 6: `plane/fetch.py` — one-time URL snapshot

**Files:**
- Create: `controlflow_sdk/plane/fetch.py`
- Test: `tests/plane/test_fetch.py`

**Interfaces:**
- Produces: `FetchedSnapshot(raw, fmt, suggested_name, source_url, fetched_at)`; `FetchError(Exception)`; `Opener = Callable[[urllib.request.Request], tuple[bytes, str]]`; `fetch_snapshot(url, *, headers=None, record_path=None, opener=None) -> FetchedSnapshot`.

- [ ] **Step 1: Write the failing test**

```python
# tests/plane/test_fetch.py
from __future__ import annotations

import json

import pytest

from controlflow_sdk.plane import fetch


def _opener(body: bytes, ctype: str):
    captured = {}
    def opener(req):
        captured["headers"] = dict(req.header_items())
        captured["url"] = req.full_url
        return body, ctype
    opener.captured = captured
    return opener


def test_json_array_becomes_csv():
    body = json.dumps([{"id": "A", "amt": 5}, {"id": "B", "amt": 6}]).encode()
    snap = fetch.fetch_snapshot("https://x/items.json",
                                opener=_opener(body, "application/json"))
    assert snap.fmt == "csv"
    assert snap.raw.decode().splitlines()[0] == "id,amt"
    assert "A,5" in snap.raw.decode()
    assert snap.source_url == "https://x/items.json"


def test_record_path_navigates_and_headers_forwarded():
    body = json.dumps({"data": {"items": [{"id": "Z"}]}}).encode()
    op = _opener(body, "application/json")
    snap = fetch.fetch_snapshot("https://x/api", headers={"Authorization": "Bearer t"},
                                record_path="data.items", opener=op)
    assert "Z" in snap.raw.decode()
    # urllib title-cases header keys
    assert op.captured["headers"].get("Authorization") == "Bearer t"


def test_csv_passthrough():
    snap = fetch.fetch_snapshot("https://x/data.csv",
                                opener=_opener(b"id,n\nA,1\n", "text/csv"))
    assert snap.fmt == "csv" and snap.raw == b"id,n\nA,1\n"


def test_errors_are_fetcherror():
    with pytest.raises(fetch.FetchError):
        fetch.fetch_snapshot("ftp://nope", opener=_opener(b"", ""))
    with pytest.raises(fetch.FetchError):  # not a JSON array
        fetch.fetch_snapshot("https://x/o.json",
                             opener=_opener(b'{"a":1}', "application/json"))
    with pytest.raises(fetch.FetchError):  # bad record_path
        fetch.fetch_snapshot("https://x/o.json", record_path="missing",
                             opener=_opener(b'{"data":[]}', "application/json"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/plane/test_fetch.py -q`
Expected: FAIL — `ModuleNotFoundError: controlflow_sdk.plane.fetch`.

- [ ] **Step 3: Write minimal implementation**

```python
# controlflow_sdk/plane/fetch.py
"""One-time, user-initiated URL fetch that snapshots a response to bytes.

NOT a live connector (STRATEGY.md non-goal): a single GET on an explicit user
action; the caller writes the result to a local file that becomes the source of
truth. The ``opener`` is injectable so tests never touch the network.
"""

from __future__ import annotations

import csv as csvmod
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

Opener = Callable[[urllib.request.Request], tuple[bytes, str]]


class FetchError(Exception):
    """User-facing failure of a one-time URL fetch."""


@dataclass(frozen=True)
class FetchedSnapshot:
    raw: bytes
    fmt: str            # csv | xlsx | parquet
    suggested_name: str
    source_url: str
    fetched_at: str     # 20260622T101913Z


def _default_opener(req: urllib.request.Request) -> tuple[bytes, str]:
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 user-initiated
            return resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raise FetchError(f"Server returned HTTP {e.code} for {req.full_url}") from e
    except urllib.error.URLError as e:
        raise FetchError(f"Could not reach {req.full_url}: {e.reason}") from e


def _infer_fmt(url: str, content_type: str) -> str:
    ct = content_type.lower()
    low = url.lower().split("?")[0]
    if "json" in ct or low.endswith(".json"):
        return "json"
    if low.endswith(".xlsx") or "spreadsheetml" in ct:
        return "xlsx"
    if low.endswith(".parquet") or "parquet" in ct:
        return "parquet"
    return "csv"


def _name_stem(url: str) -> str:
    stem = PurePosixPath(urlparse(url).path).stem
    return stem or "snapshot"


def _dig(payload: Any, record_path: str | None) -> Any:
    if not record_path:
        return payload
    node = payload
    for part in record_path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise FetchError(f"record_path {record_path!r}: no key {part!r} in the response")
        node = node[part]
    return node


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"))
    return str(v)


def _json_to_csv(raw: bytes, record_path: str | None) -> bytes:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FetchError(f"Response is not valid JSON: {e}") from e
    records = _dig(payload, record_path)
    if not isinstance(records, list):
        where = f"at {record_path!r}" if record_path else "at the top level"
        raise FetchError(f"Expected a JSON array of records {where}")
    if not records:
        raise FetchError("JSON response contained zero records")
    header: list[str] = []
    for rec in records:
        if not isinstance(rec, dict):
            raise FetchError("Each JSON record must be an object")
        for k in rec:
            if k not in header:
                header.append(k)
    buf = io.StringIO()
    w = csvmod.writer(buf)
    w.writerow(header)
    for rec in records:
        w.writerow([_cell(rec.get(k)) for k in header])
    return buf.getvalue().encode("utf-8")


def fetch_snapshot(
    url: str, *, headers: dict[str, str] | None = None,
    record_path: str | None = None, opener: Opener | None = None,
) -> FetchedSnapshot:
    """GET *url* once and snapshot the response (JSON normalised to CSV)."""
    if not url.lower().startswith(("http://", "https://")):
        raise FetchError("URL must start with http:// or https://")
    opener = opener or _default_opener
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    body, content_type = opener(req)
    fmt = _infer_fmt(url, content_type)
    fetched_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = _name_stem(url)
    if fmt == "json":
        return FetchedSnapshot(_json_to_csv(body, record_path), "csv",
                               f"{stem}.csv", url, fetched_at)
    return FetchedSnapshot(body, fmt, f"{stem}.{fmt}", url, fetched_at)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/plane/test_fetch.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add controlflow_sdk/plane/fetch.py tests/plane/test_fetch.py
git commit -m "feat(plane): fetch_snapshot — one-time URL GET, JSON->CSV, injectable opener"
git push -u origin HEAD
```

---

### Task 7: Route uploads through `extract_table` (Excel/Parquet end-to-end)

**Files:**
- Modify: `controlflow_sdk/plane/routes/sources.py`
- Modify: `controlflow_sdk/plane/templates/source_new.html`
- Test: `tests/plane/test_sources_multiformat.py`

**Interfaces:**
- Consumes: `ingest.extract_table`, `ingest.AdaptersUnavailable` (Task 2); `repo.upsert_source(sheet=...)` (Task 4).
- Produces: format inferred from upload extension; `sheet` form field persisted on create; non-CSV preview; friendly `[adapters]`-absent + `.xls`-rejected messages.

- [ ] **Step 1: Write the failing test**

```python
# tests/plane/test_sources_multiformat.py
from __future__ import annotations

import io

import pandas as pd


def _xlsx(df_by_sheet: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in df_by_sheet.items():
            df.to_excel(xw, sheet_name=name, index=False)
    return buf.getvalue()


def test_upload_xlsx_infers_columns_and_format(client):
    raw = _xlsx({"Sheet1": pd.DataFrame({"user_id": ["U1"], "amount": [5]})})
    resp = client.post("/sources",
                       data={"source_id": "gl", "as_of_date": "2026-01-01"},
                       files={"file": ("gl.xlsx", io.BytesIO(raw),
                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                       follow_redirects=False)
    assert resp.status_code in (302, 303)
    edit = client.get("/sources/gl")
    assert "user_id" in edit.text and "amount" in edit.text
    from controlflow_sdk.store import repo
    from controlflow_sdk.store.db import connect
    conn = connect(client.app.state.project_root)
    assert repo.get_source(conn, "gl")["format"] == "xlsx"
    conn.close()


def test_upload_parquet(client):
    buf = io.BytesIO()
    pd.DataFrame({"id": ["A", "B"], "n": [1, 2]}).to_parquet(buf, index=False)
    resp = client.post("/sources",
                       data={"source_id": "p", "as_of_date": "2026-01-01"},
                       files={"file": ("p.parquet", io.BytesIO(buf.getvalue()),
                              "application/octet-stream")},
                       follow_redirects=False)
    assert resp.status_code in (302, 303)
    data = client.get("/sources/p/data")
    assert "A" in data.text and "B" in data.text  # preview renders parquet rows


def test_xlsx_sheet_selection_persisted(client):
    raw = _xlsx({"First": pd.DataFrame({"id": ["A"]}),
                 "Second": pd.DataFrame({"id": ["Z"]})})
    client.post("/sources",
                data={"source_id": "ms", "as_of_date": "2026-01-01", "sheet": "Second"},
                files={"file": ("ms.xlsx", io.BytesIO(raw),
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                follow_redirects=False)
    from controlflow_sdk.store import repo
    from controlflow_sdk.store.db import connect
    conn = connect(client.app.state.project_root)
    assert repo.get_source(conn, "ms")["sheet"] == "Second"
    conn.close()


def test_unsupported_xls_rejected(client):
    resp = client.post("/sources",
                       data={"source_id": "old", "as_of_date": "2026-01-01"},
                       files={"file": ("old.xls", io.BytesIO(b"x"), "application/vnd.ms-excel")},
                       follow_redirects=False)
    assert resp.status_code == 200  # re-renders the form, not a redirect
    assert ".xls" in resp.text or "not supported" in resp.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/plane/test_sources_multiformat.py -q`
Expected: FAIL — xlsx upload errors (CSV decode) / format not persisted / no preview.

- [ ] **Step 3: Write minimal implementation**

In `controlflow_sdk/plane/routes/sources.py`:

(a) Add imports + an extension map near the top (after the existing imports):

```python
from controlflow_sdk.plane.ingest import AdaptersUnavailable, extract_table

_UPLOAD_FORMATS = {".csv": "csv", ".xlsx": "xlsx", ".parquet": "parquet"}


def _fmt_from_name(name: str) -> str | None:
    return _UPLOAD_FORMATS.get(Path(name).suffix.lower())
```

(b) Replace `_header_of` and `_row_count` (which assumed CSV) with format-aware versions, keeping the names so existing callers compile:

```python
def _table_of(raw: bytes, fmt: str, sheet: str | None = None):
    return extract_table(raw, fmt, sheet=sheet)


def _header_of(raw: bytes, fmt: str = "csv", sheet: str | None = None) -> list[str]:
    return _table_of(raw, fmt, sheet).header


def _row_count(raw: bytes, fmt: str = "csv", sheet: str | None = None) -> int:
    return len(_table_of(raw, fmt, sheet).rows)
```

(c) Rewrite `create_source` to infer format, validate it, capture `sheet`, and render a friendly error on failure:

```python
    @app.post("/sources")
    async def create_source(
        request: Request,
        source_id: str = Form(...),
        as_of_date: str = Form(""),
        sheet: str = Form(""),
        file: UploadFile = File(...),
    ) -> Any:
        root = request.app.state.project_root
        filename = file.filename or f"{source_id}.csv"
        fmt = _fmt_from_name(filename)
        raw = await file.read()

        def _err(msg: str) -> Any:
            return templates.TemplateResponse(
                request, "source_new.html",
                {"project": {"name": ""}, "error": msg}, status_code=200,
            )

        if fmt is None:
            return _err(f"Unsupported file type for {filename!r}. "
                        "Upload a .csv, .xlsx, or .parquet file (legacy .xls is not supported).")
        sheet_val = sheet.strip() or None
        try:
            table = extract_table(raw, fmt, sheet=sheet_val)
        except AdaptersUnavailable as e:
            return _err(str(e))

        conn = connect(root)
        try:
            (root / "data").mkdir(parents=True, exist_ok=True)
            dest = root / "data" / Path(filename).name
            dest.write_bytes(raw)
            repo.upsert_source(conn, id=source_id, format=fmt,
                               path=f"data/{dest.name}", key_config={"mode": "auto"},
                               sheet=sheet_val)
            repo.set_columns(conn, source_id, [
                {"original_name": h, "display_name": h, "data_type": "text",
                 "is_key": False, "include": True, "ordinal": i}
                for i, h in enumerate(table.header)
            ])
            repo.set_initial_file(
                conn, source_id=source_id, stored_path=f"data/{dest.name}",
                original_name=dest.name, as_of_date=as_of_date.strip() or None,
                row_count=len(table.rows), uploaded_at=_stamp(),
            )
            if as_of_date.strip():
                conn.execute("UPDATE sources SET extract_date = ? WHERE id = ?",
                             (as_of_date.strip(), source_id))
                conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/sources/{source_id}", status_code=303)
```

(d) Rewrite the `source_data` preview to read the stored file with `extract_table` (not the CSV reader). Replace the `if current:` block body:

```python
        if current:
            fpath = root / current["stored_path"]
            if fpath.is_file():
                fmt = (source or {}).get("format", "csv")
                sheet = (source or {}).get("sheet")
                table = extract_table(fpath.read_bytes(), fmt, sheet=sheet)
                header, data_rows = table.header, table.rows
                total = len(data_rows)
                page = max(1, page)
                start = (page - 1) * PAGE_SIZE
                rows = data_rows[start:start + PAGE_SIZE]
```

(e) In `refresh_source` and `confirm_refresh`, pass the source's format/sheet to `_header_of` / `_row_count`. Replace `_header_of(raw)` with `_header_of(raw, existing["format"], existing.get("sheet"))` and `_row_count(new_bytes)` with `_row_count(new_bytes, existing["format"], existing.get("sheet"))`. (The refresh upload keeps the source's existing format — a source does not change format on refresh.)

(f) Update `source_new.html` to accept any supported file and expose a sheet field. Replace the Format + File fields with:

```html
    <div class="field">
      <label for="s-file">File</label>
      <span class="hint">CSV, Excel (.xlsx), or Parquet. Source data stays local — never written to the bundle.</span>
      <input id="s-file" type="file" name="file" accept=".csv,.xlsx,.parquet" required>
    </div>
    <div class="field">
      <label for="s-sheet">Excel sheet (optional)</label>
      <span class="hint">For multi-sheet .xlsx — leave blank for the first sheet.</span>
      <input id="s-sheet" type="text" name="sheet" placeholder="e.g. Sheet1">
    </div>
```

and add, just below the `<div class="page-head">` block, an error banner:

```html
{% if error %}<div class="callout callout-warn">{{ error }}</div>{% endif %}
```

- [ ] **Step 4: Run the new + existing source tests**

Run: `python -m pytest tests/plane/test_sources_multiformat.py tests/plane/test_sources.py -q`
Expected: PASS (existing CSV tests still pass; the old `format` form field is now ignored — `_fmt_from_name` derives it from the filename).

- [ ] **Step 5: Commit**

```bash
git add controlflow_sdk/plane/routes/sources.py controlflow_sdk/plane/templates/source_new.html tests/plane/test_sources_multiformat.py
git commit -m "feat(plane): accept xlsx/parquet uploads via extract_table + sheet selection"
git push -u origin HEAD
```

---

### Task 8: Create a source from a URL snapshot

**Files:**
- Modify: `controlflow_sdk/plane/routes/sources.py` (new `GET`/`POST /sources/from-url`)
- Modify: `controlflow_sdk/plane/templates/source_new.html` (mode toggle + URL form + secrets warning)
- Test: `tests/plane/test_sources_from_url.py`

**Interfaces:**
- Consumes: `fetch.fetch_snapshot` (Task 6); `extract_table` (Task 2); `repo.upsert_source_fetch` (Task 4).
- Produces: `POST /sources/from-url` (form: `source_id`, `url`, `headers` JSON-or-blank, `record_path`, `as_of_date`); writes a `data/<name>` snapshot, creates the source, stores a `source_fetch` row. A test seam: the route resolves the fetcher via `request.app.state` so tests inject a fake.

- [ ] **Step 1: Write the failing test**

```python
# tests/plane/test_sources_from_url.py
from __future__ import annotations

import json

from controlflow_sdk.plane import fetch
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect


def _fake_opener(payload):
    body = json.dumps(payload).encode()
    def opener(req):
        return body, "application/json"
    return opener


def test_create_from_url_snapshots_and_stores_fetch(client):
    # Inject a fake opener so no network is touched.
    client.app.state.fetch_opener = _fake_opener([{"id": "A", "amt": 5},
                                                  {"id": "B", "amt": 6}])
    resp = client.post("/sources/from-url", data={
        "source_id": "api", "url": "https://example.test/items.json",
        "headers": "", "record_path": "", "as_of_date": "2026-01-01",
    }, follow_redirects=False)
    assert resp.status_code in (302, 303)

    edit = client.get("/sources/api")
    assert "id" in edit.text and "amt" in edit.text
    conn = connect(client.app.state.project_root)
    src = repo.get_source(conn, "api")
    fetch_row = repo.get_source_fetch(conn, "api")
    conn.close()
    assert src["format"] == "csv"          # JSON snapshotted to CSV
    assert fetch_row["url"] == "https://example.test/items.json"
    # snapshot file exists on disk
    assert (client.app.state.project_root / src["path"]).is_file()


def test_from_url_form_shows_secrets_warning(client):
    page = client.get("/sources/from-url")
    assert "plaintext" in page.text.lower()
    assert "controlplane.db" in page.text


def test_fetch_error_rerenders_form(client):
    def boom(req):
        raise fetch.FetchError("Could not reach host")
    client.app.state.fetch_opener = boom
    resp = client.post("/sources/from-url", data={
        "source_id": "bad", "url": "https://nope.test/x.json",
        "headers": "", "record_path": "", "as_of_date": "",
    }, follow_redirects=False)
    assert resp.status_code == 200
    assert "Could not reach host" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/plane/test_sources_from_url.py -q`
Expected: FAIL — `/sources/from-url` route does not exist (404/405).

- [ ] **Step 3: Write minimal implementation**

In `controlflow_sdk/plane/routes/sources.py`, add imports and a shared header-parser + the two routes (place the routes inside `register(...)`, near `create_source`):

```python
import json as jsonmod

from controlflow_sdk.plane import fetch as fetchmod
```

```python
    def _do_fetch(request: Request, url: str, headers: dict, record_path: str | None):
        # Tests inject app.state.fetch_opener; production uses the default opener.
        opener = getattr(request.app.state, "fetch_opener", None)
        return fetchmod.fetch_snapshot(url, headers=headers or None,
                                       record_path=record_path or None, opener=opener)

    def _parse_headers(raw: str) -> dict:
        raw = (raw or "").strip()
        if not raw:
            return {}
        try:
            parsed = jsonmod.loads(raw)
        except jsonmod.JSONDecodeError as e:
            raise fetchmod.FetchError(f"Headers must be a JSON object: {e}") from e
        if not isinstance(parsed, dict):
            raise fetchmod.FetchError("Headers must be a JSON object, e.g. "
                                      '{"Authorization": "Bearer ..."}')
        return {str(k): str(v) for k, v in parsed.items()}

    @app.get("/sources/from-url", response_class=HTMLResponse)
    def new_source_from_url(request: Request) -> Any:
        return templates.TemplateResponse(
            request, "source_new.html", {"project": {"name": ""}, "mode": "url"},
        )

    @app.post("/sources/from-url")
    async def create_source_from_url(
        request: Request,
        source_id: str = Form(...),
        url: str = Form(...),
        headers: str = Form(""),
        record_path: str = Form(""),
        as_of_date: str = Form(""),
    ) -> Any:
        root = request.app.state.project_root

        def _err(msg: str) -> Any:
            return templates.TemplateResponse(
                request, "source_new.html",
                {"project": {"name": ""}, "mode": "url", "error": msg,
                 "url": url, "record_path": record_path}, status_code=200,
            )

        try:
            hdrs = _parse_headers(headers)
            snap = _do_fetch(request, url, hdrs, record_path.strip())
            table = extract_table(snap.raw, snap.fmt)
        except (fetchmod.FetchError, AdaptersUnavailable) as e:
            return _err(str(e))

        conn = connect(root)
        try:
            (root / "data").mkdir(parents=True, exist_ok=True)
            dest = root / "data" / snap.suggested_name
            dest.write_bytes(snap.raw)
            repo.upsert_source(conn, id=source_id, format=snap.fmt,
                               path=f"data/{dest.name}", key_config={"mode": "auto"})
            repo.set_columns(conn, source_id, [
                {"original_name": h, "display_name": h, "data_type": "text",
                 "is_key": False, "include": True, "ordinal": i}
                for i, h in enumerate(table.header)
            ])
            repo.set_initial_file(
                conn, source_id=source_id, stored_path=f"data/{dest.name}",
                original_name=dest.name, as_of_date=as_of_date.strip() or None,
                row_count=len(table.rows), uploaded_at=_stamp(),
            )
            repo.upsert_source_fetch(conn, source_id=source_id, url=snap.source_url,
                                     headers=hdrs, record_path=record_path.strip() or None,
                                     last_fetched_at=snap.fetched_at)
            if as_of_date.strip():
                conn.execute("UPDATE sources SET extract_date = ? WHERE id = ?",
                             (as_of_date.strip(), source_id))
                conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/sources/{source_id}", status_code=303)
```

In `source_new.html`, add a mode toggle at the top of the card and the URL form. After the `<div class="page-head">`/error banner, wrap the existing upload form so it shows only in file mode, and add the URL form for `mode == "url"`:

```html
<div class="tabbar">
  <a class="tab {{ '' if mode == 'url' else 'active' }}" href="/sources/new">Upload file</a>
  <a class="tab {{ 'active' if mode == 'url' else '' }}" href="/sources/from-url">Fetch from URL</a>
</div>

{% if mode == "url" %}
<div class="card">
  <div class="callout callout-warn">
    ⚠ Credentials you enter here are stored <strong>in plaintext</strong> in
    <span class="mono">controlplane.db</span>. Anyone who can read that file (or your
    backups) can read them. Only use this where data at rest is protected.
  </div>
  <form method="post" action="/sources/from-url">
    <div class="field">
      <label for="u-id">Source ID</label>
      <input id="u-id" type="text" name="source_id" placeholder="e.g. api_users" required>
    </div>
    <div class="field">
      <label for="u-url">URL</label>
      <span class="hint">An https endpoint returning JSON (array of records) or CSV. Fetched once now and snapshotted to a local file.</span>
      <input id="u-url" type="url" name="url" value="{{ url or '' }}" placeholder="https://..." required>
    </div>
    <div class="field">
      <label for="u-headers">Request headers (optional JSON)</label>
      <span class="hint">e.g. <span class="mono">{"Authorization": "Bearer ..."}</span> — stored plaintext (see warning).</span>
      <input id="u-headers" type="text" name="headers" placeholder='{"Authorization": "Bearer ..."}'>
    </div>
    <div class="field">
      <label for="u-rp">JSON record path (optional)</label>
      <span class="hint">Dotted path to the array of records, e.g. <span class="mono">data.items</span>.</span>
      <input id="u-rp" type="text" name="record_path" value="{{ record_path or '' }}" placeholder="data.items">
    </div>
    <div class="field">
      <label for="u-asof">Data as-of date</label>
      <input id="u-asof" type="date" name="as_of_date">
    </div>
    <div class="page-actions">
      <button class="btn btn-primary" type="submit">Fetch &amp; continue</button>
      <a class="btn btn-ghost" href="/sources">Cancel</a>
    </div>
  </form>
</div>
{% else %}
  {# ... existing upload <div class="card"> ... form ... </div> ... #}
{% endif %}
```

(Keep the existing upload form markup intact inside the `{% else %}` branch.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/plane/test_sources_from_url.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add controlflow_sdk/plane/routes/sources.py controlflow_sdk/plane/templates/source_new.html tests/plane/test_sources_from_url.py
git commit -m "feat(plane): create a source from a one-time URL snapshot (+ secrets warning)"
git push -u origin HEAD
```

---

### Task 9: Re-fetch from URL through the diff→confirm flow

**Files:**
- Modify: `controlflow_sdk/plane/routes/sources.py` (new `POST /sources/{id}/refetch`)
- Modify: `controlflow_sdk/plane/templates/source_data.html` (re-fetch button + secrets warning when URL-backed)
- Test: `tests/plane/test_source_refetch.py`

**Interfaces:**
- Consumes: `repo.get_source_fetch` (Task 4); `_do_fetch` + the existing `refresh_source`/`confirm_refresh` staging flow (Tasks 7/8).
- Produces: `POST /sources/{id}/refetch` re-runs the fetch with stored creds, stages the snapshot under `data/.pending/<id>/`, and renders the existing `source_refresh.html` confirm page with the column diff.

- [ ] **Step 1: Write the failing test**

```python
# tests/plane/test_source_refetch.py
from __future__ import annotations

import json


def _opener(records):
    body = json.dumps(records).encode()
    def opener(req):
        return body, "application/json"
    return opener


def _create_url_source(client, records):
    client.app.state.fetch_opener = _opener(records)
    client.post("/sources/from-url", data={
        "source_id": "api", "url": "https://example.test/items.json",
        "headers": "", "record_path": "", "as_of_date": "2026-01-01",
    }, follow_redirects=False)


def test_refetch_stages_and_shows_diff(client):
    _create_url_source(client, [{"id": "A", "amt": 5}])
    # Remote now returns an extra column -> diff should surface it.
    client.app.state.fetch_opener = _opener([{"id": "A", "amt": 5, "note": "x"}])
    resp = client.post("/sources/api/refetch", follow_redirects=False)
    assert resp.status_code == 200
    assert "note" in resp.text                 # added column shown in the diff
    assert "Confirm" in resp.text or "confirm" in resp.text


def test_refetch_without_url_source_redirects(client):
    # CSV source has no source_fetch row.
    import io
    client.post("/sources", data={"source_id": "c", "as_of_date": "2026-01-01"},
                files={"file": ("c.csv", io.BytesIO(b"id\nA\n"), "text/csv")},
                follow_redirects=False)
    resp = client.post("/sources/c/refetch", follow_redirects=False)
    assert resp.status_code in (302, 303)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/plane/test_source_refetch.py -q`
Expected: FAIL — `/sources/api/refetch` does not exist.

- [ ] **Step 3: Write minimal implementation**

Add the route in `register(...)` (after `confirm_refresh`/`cancel_refresh`):

```python
    @app.post("/sources/{source_id}/refetch")
    async def refetch_source(source_id: str, request: Request) -> Any:
        """Re-run the stored URL fetch and route through the refresh-confirm diff."""
        root = request.app.state.project_root
        conn = connect(root)
        try:
            existing = repo.get_source(conn, source_id)
            fetch_row = repo.get_source_fetch(conn, source_id)
            if existing is None or fetch_row is None:
                return RedirectResponse(f"/sources/{source_id}", status_code=303)
            try:
                snap = _do_fetch(request, fetch_row["url"], fetch_row["headers"],
                                 fetch_row.get("record_path"))
                new_headers = extract_table(snap.raw, snap.fmt).header
            except (fetchmod.FetchError, AdaptersUnavailable) as e:
                return templates.TemplateResponse(
                    request, "source_data.html",
                    {"project": repo.get_project(conn) or {"name": ""},
                     "source": existing, "current": repo.get_current_file(conn, source_id),
                     "header": [], "rows": [], "total": 0, "page": 1, "page_count": 1,
                     "page_size": PAGE_SIZE, "coercion": [], "active": "data",
                     "fetch": fetch_row, "error": str(e)}, status_code=200,
                )
            pdir = _pending_dir(root, source_id)
            pdir.mkdir(parents=True, exist_ok=True)
            (pdir / snap.suggested_name).write_bytes(snap.raw)
            _, added, removed = _reconcile_columns(existing["columns"], new_headers)
            # Refresh last_fetched_at provenance now (the snapshot was taken).
            repo.upsert_source_fetch(conn, source_id=source_id, url=fetch_row["url"],
                                     headers=fetch_row["headers"],
                                     record_path=fetch_row.get("record_path"),
                                     last_fetched_at=snap.fetched_at)
            return templates.TemplateResponse(
                request, "source_refresh.html",
                {"project": repo.get_project(conn) or {"name": ""},
                 "source": existing, "pending": snap.suggested_name,
                 "new_headers": new_headers, "added": added, "removed": removed,
                 "as_of_date": ""},
            )
        finally:
            conn.close()
```

(The existing `POST /sources/{source_id}/refresh/confirm` then promotes the staged file — no new confirm route needed.)

In `source_data.html`, surface the re-fetch button + warning when the source is URL-backed. The `source_data` GET must pass the fetch row; update it to add `"fetch": repo.get_source_fetch(conn, source_id)` to its context dict. Then add to the template, near the existing refresh/as-of controls:

```html
{% if fetch %}
<div class="callout callout-warn">
  ⚠ This source was fetched from <span class="mono">{{ fetch.url }}</span>. Stored
  credentials are kept <strong>plaintext</strong> in <span class="mono">controlplane.db</span>.
</div>
<form method="post" action="/sources/{{ source.id }}/refetch">
  <button class="btn" type="submit">Re-fetch from URL</button>
</form>
{% endif %}
{% if error %}<div class="callout callout-warn">{{ error }}</div>{% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/plane/test_source_refetch.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add controlflow_sdk/plane/routes/sources.py controlflow_sdk/plane/templates/source_data.html tests/plane/test_source_refetch.py
git commit -m "feat(plane): Re-fetch from URL routes through the refresh-diff confirm flow"
git push -u origin HEAD
```

---

### Task 10: End-to-end run, e2e smoke, docs, full gates

**Files:**
- Test: `tests/plane/test_xlsx_run_end_to_end.py`
- Modify: `tests/e2e/test_smoke.py` (or the existing e2e smoke module — extend, don't replace)
- Modify: `PRODUCT-MAP.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the failing end-to-end test** (proves an xlsx source on a non-default sheet runs full-population correctly through the engine)

```python
# tests/plane/test_xlsx_run_end_to_end.py
from __future__ import annotations

import io

import pandas as pd

from controlflow_sdk.adapters.files import source_for
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect


def test_xlsx_second_sheet_runs_full_population(client):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame({"user_id": ["DECOY"], "amount": ["999"]}).to_excel(
            xw, "First", index=False)
        pd.DataFrame({"user_id": ["U1", "U2"], "amount": ["10", "20"]}).to_excel(
            xw, "Real", index=False)
    client.post("/sources",
                data={"source_id": "gl", "as_of_date": "2026-01-01", "sheet": "Real"},
                files={"file": ("gl.xlsx", io.BytesIO(buf.getvalue()),
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                follow_redirects=False)

    conn = connect(client.app.state.project_root)
    from controlflow_sdk.store.loader import _binding
    src = repo.get_source(conn, "gl")
    conn.close()
    pop = source_for(_binding(src), client.app.state.project_root).load()
    # Read the chosen sheet (Real), not the decoy first sheet, full population.
    assert sorted(pop.df["user_id"].tolist()) == ["U1", "U2"]
    assert "DECOY" not in pop.df["user_id"].tolist()
```

- [ ] **Step 2: Run it to verify it passes** (the implementation already exists from Tasks 5/7 — this is the integration guard)

Run: `python -m pytest tests/plane/test_xlsx_run_end_to_end.py -q`
Expected: PASS. If it fails, fix the sheet thread-through before continuing.

- [ ] **Step 3: Extend the e2e browser smoke** (learning 0012 — the add-source form restructured in place)

Open the existing e2e smoke (`ls tests/e2e/`). Add a check that the add-source page shows both modes and the URL form's secrets warning. Append a test like:

```python
def test_add_source_has_upload_and_url_modes(page, live_server):
    page.goto(f"{live_server}/sources/new")
    assert page.locator("text=Upload file").count() >= 1
    assert page.locator("text=Fetch from URL").count() >= 1
    page.goto(f"{live_server}/sources/from-url")
    assert "plaintext" in page.content().lower()
```

(Match the existing e2e fixtures' names — `page`/`live_server` or whatever the module already uses. If the e2e module uses a different harness, mirror its existing tests exactly.)

- [ ] **Step 4: Update `PRODUCT-MAP.md`**

Edit the **Source manager** and **Source editor (Data tab)** rows to mention the new capability. In the Source-manager row, change "Add source opens a dedicated upload page that infers columns" to note Excel/Parquet + URL fetch:

```markdown
| Control plane — Source manager | view | List engagement sources (friendly title + code id); **Add source** offers two on-ramps — **upload** a CSV / Excel (`.xlsx`, with sheet selection) / Parquet file, or **fetch from a URL** (a one-time GET that snapshots a JSON/CSV response to a local file; credentials, if supplied, persist in `controlplane.db` with a plaintext-at-rest warning). Columns are inferred either way. Persisted to `controlplane.db`. |
```

Add a sentence to the Source-editor Data-tab row noting **Re-fetch from URL** routes through the same review→confirm diff as a file refresh.

- [ ] **Step 5: Run the full gates**

```bash
python -m pytest -q
python -m ruff check .
python -m mypy controlflow_sdk
```

Expected: all green, no warnings. The contract gate (`tests/test_contract_export.py`, `tests/schema/test_bundle_schema.py`) passes unchanged.

- [ ] **Step 6: Commit**

```bash
git add tests/plane/test_xlsx_run_end_to_end.py tests/e2e/ PRODUCT-MAP.md
git commit -m "test(plane): xlsx end-to-end run + e2e smoke; docs(product-map): multi-format sources"
git push -u origin HEAD
```

---

## Self-Review (completed during planning)

- **Spec coverage:** Seam 1 `extract_table` → Tasks 1–2, 7. Seam 2 `fetch_snapshot` → Task 6, 8. Excel sheet thread-through → Tasks 3–5, 7, 10. `[adapters]` friendly error → Tasks 2, 7. URL create + secrets table/warning → Tasks 3–4, 8. Re-fetch via diff-confirm → Task 9. Format-from-extension + `.xls` reject → Task 7. e2e smoke (0012) → Task 10. Contract-frozen proof → Task 10 gates. PRODUCT-MAP → Task 10. All spec sections map to a task.
- **Placeholders:** none — every code/test step carries real code and exact commands.
- **Type consistency:** `extract_table`/`ExtractedTable`/`AdaptersUnavailable`, `fetch_snapshot`/`FetchedSnapshot`/`FetchError`/`Opener`, `upsert_source(sheet=)`, `upsert_source_fetch`/`get_source_fetch` are used with identical names/signatures across producing and consuming tasks.
</content>
